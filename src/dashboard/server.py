import asyncio
import json
import signal
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.app_config import JOB_SITE
from src.dashboard.data_service import (
    get_app_config,
    get_jobs_payload,
    get_live_state,
    get_messages,
    get_run_detail,
    get_run_events,
    get_run_history,
    get_run_jobs_payload,
    get_run_screenshots,
    get_search_config,
    get_summary,
    update_app_config,
    update_search_config,
)
from src.dashboard.llm_stats import llm_totals
from src.dashboard.runtime import (
    LATEST_SCREENSHOT_FILE,
    ROOT_DIR,
    _signal_process_tree,
    get_process_info,
    is_process_running,
    latest_event_position,
    read_events_since,
    read_events_since_for_run,
    request_pause,
    request_resume,
    request_stop,
    start_bot_process,
    sync_process_state,
    terminate_running_process,
)
from src.dashboard.tailor_service import TAILORED_DIR, JobNotFound, tailor_job
from src.dashboard.tracker_service import get_jobs as get_tracker_jobs
from src.dashboard.tracker_service import (
    status_counts,
    update_job,
)

STATIC_DIR = ROOT_DIR / "src" / "dashboard" / "static"
SITE_NAME = "LinkedIn" if JOB_SITE == "linkedin" else "Indeed"


class SearchConfigPayload(BaseModel):
    config: Dict[str, Any]


class AppConfigPayload(BaseModel):
    config: Dict[str, Any]


class UpdateJobPayload(BaseModel):
    url: str
    status: str | None = None
    notes: str | None = None
    applied_date: str | None = None


class TailorPayload(BaseModel):
    url: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    pid = get_process_info().get("pid")
    terminate_running_process()
    if pid:
        for _ in range(10):
            await asyncio.sleep(0.5)
            if not is_process_running(pid):
                break
        else:
            _signal_process_tree(pid, signal.SIGKILL)


app = FastAPI(title=f"{SITE_NAME} AI Job Applier Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def tracker_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "tracker.html")


@app.get("/ops", response_class=HTMLResponse)
async def ops_index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail_page(run_id: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/messages", response_class=HTMLResponse)
async def messages_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "messages.html").read_text(encoding="utf-8"))


@app.get("/api/meta")
async def meta() -> JSONResponse:
    return JSONResponse({"site_name": SITE_NAME})


@app.get("/api/summary")
async def summary() -> JSONResponse:
    return JSONResponse(get_summary())


@app.get("/api/jobs")
async def jobs(
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> JSONResponse:
    return JSONResponse(get_jobs_payload(status=status, search=search))


@app.get("/api/messages")
async def messages(
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> JSONResponse:
    return JSONResponse({"messages": get_messages(category=category, status=status)})


@app.get("/api/live")
async def live() -> JSONResponse:
    return JSONResponse(get_live_state())


@app.get("/api/runs")
async def runs() -> JSONResponse:
    return JSONResponse({"runs": get_run_history()})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, limit: int = Query(default=200, ge=1, le=5000)) -> JSONResponse:
    return JSONResponse({"run_id": run_id, "events": get_run_events(run_id=run_id, limit=limit)})


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> JSONResponse:
    return JSONResponse({"run_id": run_id, **get_run_detail(run_id)})


@app.get("/api/runs/{run_id}/export")
async def run_export(run_id: str) -> Response:
    payload = {"run_id": run_id, **get_run_detail(run_id)}
    return Response(
        content=json.dumps(payload, indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
    )


@app.get("/api/runs/{run_id}/screenshots")
async def run_screenshots(run_id: str) -> JSONResponse:
    return JSONResponse({"run_id": run_id, "screenshots": get_run_screenshots(run_id)})


@app.get("/api/runs/{run_id}/jobs")
async def run_jobs(
    run_id: str,
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> JSONResponse:
    return JSONResponse(get_run_jobs_payload(run_id=run_id, status=status, search=search))


@app.get("/api/config")
async def config() -> JSONResponse:
    return JSONResponse({"search": get_search_config(), "app": get_app_config()})


@app.put("/api/config/search")
async def save_search_config(payload: SearchConfigPayload) -> JSONResponse:
    try:
        config = update_search_config(payload.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"search": config})


@app.put("/api/config/app")
async def save_app_config(payload: AppConfigPayload) -> JSONResponse:
    try:
        config = update_app_config(payload.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"app": config})


@app.post("/api/control/start")
async def start() -> JSONResponse:
    process_info = start_bot_process()
    return JSONResponse({"process": process_info, "summary": get_summary()})


@app.post("/api/control/pause")
async def pause() -> JSONResponse:
    return JSONResponse({"control": request_pause()})


@app.post("/api/control/resume")
async def resume() -> JSONResponse:
    return JSONResponse({"control": request_resume()})


@app.post("/api/control/stop")
async def stop(force: bool = Query(default=False)) -> JSONResponse:
    if force:
        terminated = terminate_running_process()
        return JSONResponse({"terminated": terminated, "summary": get_summary()})
    return JSONResponse({"control": request_stop()})


@app.get("/api/process")
async def process() -> JSONResponse:
    return JSONResponse({"process": sync_process_state(), "summary": get_summary()})


@app.get("/api/screenshot")
async def screenshot():
    if not LATEST_SCREENSHOT_FILE.exists():
        return JSONResponse({"available": False}, status_code=404)
    return Response(content=LATEST_SCREENSHOT_FILE.read_bytes(), media_type="image/png")


@app.get("/api/screenshot-file")
async def screenshot_file(path: str = Query(...)):
    file_path = (ROOT_DIR / path).resolve()
    try:
        file_path.relative_to(ROOT_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid screenshot path") from exc

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return Response(content=file_path.read_bytes(), media_type="image/png")


@app.get("/api/events/stream")
async def stream_events(
    request: Request, run_id: str | None = Query(default=None)
) -> StreamingResponse:
    async def event_generator():
        initial = get_live_state()
        if run_id:
            initial = {
                **initial,
                "events": get_run_events(run_id=run_id, limit=120),
                "selected_run_id": run_id,
            }
        yield f"event: snapshot\ndata: {JSONResponse(content=initial).body.decode('utf-8')}\n\n"

        position = latest_event_position()
        while True:
            if await request.is_disconnected():
                break
            if run_id:
                events, position = read_events_since_for_run(position, run_id)
            else:
                events, position = read_events_since(position)
            for event in events:
                yield f"event: message\ndata: {JSONResponse(content=event).body.decode('utf-8')}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/tracker/jobs")
def tracker_jobs(
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> JSONResponse:
    jobs = get_tracker_jobs(status=status, search=search)
    return JSONResponse({"jobs": jobs})


@app.patch("/api/tracker/jobs")
def update_tracker_job(payload: UpdateJobPayload) -> JSONResponse:
    try:
        fields = {}
        if payload.status is not None:
            fields["status"] = payload.status
        if payload.notes is not None:
            fields["notes"] = payload.notes
        if payload.applied_date is not None:
            fields["applied_date"] = payload.applied_date

        updated = update_job(payload.url, fields)
        return JSONResponse(updated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/tracker/summary")
def tracker_summary() -> JSONResponse:
    return JSONResponse(status_counts())


@app.get("/api/tracker/llm-stats")
def tracker_llm_stats() -> JSONResponse:
    """Cumulative LLM usage (tailoring is the tracker's only LLM cost source)."""
    return JSONResponse(llm_totals())


@app.post("/api/tracker/jobs/tailor")
async def tailor_tracker_job(payload: TailorPayload) -> JSONResponse:
    """Generate a tailored résumé PDF + cover letter for one job and store paths."""
    try:
        updated = await tailor_job(payload.url)
        return JSONResponse(updated)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tracker/file")
def tracker_file(path: str = Query(...)) -> Response:
    """Serve a tailored artifact (PDF/markdown), scoped to the tailored dir."""
    file_path = (ROOT_DIR / path).resolve()
    try:
        file_path.relative_to(TAILORED_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "application/pdf" if file_path.suffix == ".pdf" else "text/plain"
    return Response(
        content=file_path.read_bytes(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{file_path.name}"'},
    )
