import asyncio
import ctypes
import os
import signal
from enum import Enum
from threading import Event
from typing import Any

from config.logger_config import logger

_shutdown_handlers_registered = False
_windows_console_handler = None

# Windows delivers CTRL_CLOSE_EVENT with only a few seconds before it force-kills
# the process, so the console handler must not block cleanup for longer than that.
_WINDOWS_CLEANUP_TIMEOUT_SEC = 4.0


class ShutdownState(Enum):
    """Phases of a graceful shutdown."""

    RUNNING = "running"
    DRAINING = "draining"  # current job finishing, no new jobs started
    CLEANUP = "cleanup"  # browser cleanup in progress
    DONE = "done"


class BrowserClosedError(RuntimeError):
    """Raised when the browser is closed/disconnected mid-run so the run can restart."""


class RuntimeController:
    """Coordinates shutdown and browser-loss reactions across sync/async boundaries."""

    def __init__(self) -> None:
        self.shutdown_requested = Event()
        self.cleanup_complete = Event()
        self.cleanup_complete.set()
        self.shutdown_state = ShutdownState.RUNNING
        self._browser_lost = Event()

    # ------------------------------------------------------------------ run lifecycle
    def reset_for_new_run(self) -> None:
        """Reset per-run state at the start of each run.

        Matters when RESTART_EVERY_DAY loops runs, and when restarting after a
        browser disconnect: the browser-lost flag from the previous attempt must
        not leak into the fresh one.
        """
        self.shutdown_state = ShutdownState.RUNNING
        self._browser_lost.clear()
        self.cleanup_complete.clear()

    def set_shutdown_state(self, state: ShutdownState) -> None:
        self.shutdown_state = state

    def finish_run(self) -> None:
        self.cleanup_complete.set()

    def wait_for_cleanup(self, timeout: float = _WINDOWS_CLEANUP_TIMEOUT_SEC) -> bool:
        return self.cleanup_complete.wait(timeout)

    # ------------------------------------------------------------------ shutdown
    def request_shutdown(self, source: str) -> None:
        if self.shutdown_state == ShutdownState.RUNNING:
            self.set_shutdown_state(ShutdownState.DRAINING)
            logger.warning(f"Shutdown requested via {source}. Finishing current job before exit.")
        elif not self.shutdown_requested.is_set():
            logger.warning(f"Shutdown requested via {source}. Finishing current cleanup.")
        self.shutdown_requested.set()

    def is_shutdown_requested(self) -> bool:
        return self.shutdown_requested.is_set()

    def should_drain(self) -> bool:
        """True once a graceful shutdown has begun (stop before starting new jobs)."""
        return self.shutdown_state == ShutdownState.DRAINING

    # ------------------------------------------------------------------ browser loss
    def mark_browser_lost(self) -> None:
        self._browser_lost.set()

    def is_browser_lost(self) -> bool:
        return self._browser_lost.is_set()

    # ------------------------------------------------------------------ job-loop checkpoint
    def next_job_stop_reason(self) -> str | None:
        """Reason to stop before starting the next job, or None to keep going.

        Factored out of the platform job managers so every manager honours
        shutdown and browser loss identically. Returns the loop's result code:
          - "Error"    the browser was lost; stop so the run can restart cleanly
          - "Shutdown" a graceful shutdown drain has begun; stop before new work
        """
        if self.is_browser_lost():
            logger.warning("Browser disconnected; stopping run")
            return "Error"
        if self.should_drain():
            logger.info("Shutdown requested — stopping before the next job")
            return "Shutdown"
        return None


runtime_controller = RuntimeController()


def _handle_shutdown_signal(signum, _frame) -> None:
    """Convert OS signals into a graceful shutdown request.

    The first signal starts a graceful drain (the current job finishes, then the
    program exits). A second signal escalates to an immediate abort by restoring
    the default handler and raising KeyboardInterrupt, so the user is never stuck
    — e.g. blocked at an ``input()`` prompt where there is no running job to
    drain.
    """
    try:
        signal_name = signal.Signals(signum).name
    except ValueError:
        signal_name = str(signum)

    if runtime_controller.is_shutdown_requested():
        logger.warning(f"Second interrupt ({signal_name}) received — aborting now.")
        # Restore default handling so a further signal hard-kills, and raise so
        # blocking calls (input(), time.sleep) unwind immediately.
        signal.signal(signum, signal.SIG_DFL)
        raise KeyboardInterrupt
    runtime_controller.request_shutdown(f"signal {signal_name}")


def register_shutdown_handlers() -> None:
    """Register SIGINT/SIGTERM and Windows console-close handlers once."""
    global _shutdown_handlers_registered, _windows_console_handler
    if _shutdown_handlers_registered:
        return

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    if os.name == "nt":
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        event_names = {
            0: "CTRL_C_EVENT",
            1: "CTRL_BREAK_EVENT",
            2: "CTRL_CLOSE_EVENT",
            5: "CTRL_LOGOFF_EVENT",
            6: "CTRL_SHUTDOWN_EVENT",
        }

        def console_handler(ctrl_type: int) -> bool:
            runtime_controller.request_shutdown(
                f"console event {event_names.get(ctrl_type, ctrl_type)}"
            )
            runtime_controller.wait_for_cleanup()
            return True

        _windows_console_handler = handler_type(console_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_windows_console_handler, True)

    _shutdown_handlers_registered = True


def attach_browser_close_watchers(browser: Any) -> None:
    """Flag the run as browser-lost if the browser disconnects unexpectedly.

    Only reacts while the run is still meant to be active (RUNNING/DRAINING); the
    intentional ``browser.close()`` during cleanup also fires "disconnected", and
    must not be mistaken for a crash.
    """

    def on_disconnected() -> None:
        if runtime_controller.shutdown_state in (ShutdownState.RUNNING, ShutdownState.DRAINING):
            logger.warning("Browser disconnected unexpectedly; will stop and restart the run.")
            runtime_controller.mark_browser_lost()

    browser.on("disconnected", lambda: on_disconnected())


async def sleep_with_shutdown(seconds: float) -> bool:
    """Sleep up to ``seconds``, waking early if a shutdown is requested.

    Returns True if the full interval elapsed, False if a shutdown request cut it
    short. Uses a monotonic deadline so the total wait does not drift, while
    staying responsive (<=1s) to shutdown requests.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not runtime_controller.is_shutdown_requested():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(1.0, remaining))
    return False
