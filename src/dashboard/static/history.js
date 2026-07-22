// history.js - Job Funnel History tab
const llmStatsContainer = document.getElementById("llm-stats");
const sweepsTbody = document.getElementById("sweeps-tbody");
const jobSelect = document.getElementById("job-select");
const jobJson = document.getElementById("job-json");

let jobsByUrl = {};

function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
  return String(text).replace(/[&<>"']/g, (c) => map[c]);
}

function formatTs(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (isNaN(d.getTime())) return value;
    return d.toISOString().slice(0, 19).replace("T", " ");
  } catch {
    return value;
  }
}

// ── LLM stats tiles ──
async function fetchLlmStats() {
  try {
    const response = await fetch("/api/tracker/llm-stats");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderLlmStats(await response.json());
  } catch (error) {
    console.error("Failed to fetch LLM stats:", error);
  }
}

function renderLlmStats(stats) {
  const tiles = [
    ["Tailor Calls", stats.calls ?? 0],
    ["Tailor Cost", `$${(stats.total_cost ?? 0).toFixed(4)}`],
    ["Tokens", (stats.total_tokens ?? 0).toLocaleString()],
    ["LLM Time", `${(stats.total_time_seconds ?? 0).toFixed(1)}s`],
  ];
  llmStatsContainer.innerHTML = "";
  tiles.forEach(([label, value]) => {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    const l = document.createElement("span");
    l.className = "stat-tile-label";
    l.textContent = label;
    const v = document.createElement("span");
    v.className = "stat-tile-value";
    v.textContent = value;
    tile.appendChild(l);
    tile.appendChild(v);
    llmStatsContainer.appendChild(tile);
  });
}

// ── Sweeps table ──
async function fetchSweeps() {
  try {
    const response = await fetch("/api/tracker/sweeps");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderSweeps(data.sweeps || []);
  } catch (error) {
    console.error("Failed to fetch sweeps:", error);
    sweepsTbody.innerHTML =
      '<tr><td colspan="7" class="hist-empty">Failed to load sweeps.</td></tr>';
  }
}

function queryCount(queriesJson) {
  try {
    const q = JSON.parse(queriesJson || "[]");
    return Array.isArray(q) ? q.length : 0;
  } catch {
    return 0;
  }
}

function renderSweeps(sweeps) {
  if (sweeps.length === 0) {
    sweepsTbody.innerHTML =
      '<tr><td colspan="7" class="hist-empty">No sweeps yet. Run one from the tracker.</td></tr>';
    return;
  }
  sweepsTbody.innerHTML = sweeps
    .map((s) => {
      const status = s.status || "—";
      const badgeClass = ["done", "running", "failed", "stopped"].includes(status)
        ? status
        : "";
      return `
        <tr>
          <td>${s.id}</td>
          <td>${formatTs(s.started_at)}</td>
          <td>${formatTs(s.finished_at)}</td>
          <td>${queryCount(s.queries_json)}</td>
          <td>${s.collected ?? "—"}</td>
          <td>${s.new_count ?? "—"}</td>
          <td><span class="sweep-badge ${badgeClass}">${escapeHtml(status)}</span></td>
        </tr>`;
    })
    .join("");
}

// ── Raw job inspector ──
async function fetchJobs() {
  try {
    const response = await fetch("/api/tracker/jobs");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const jobs = data.jobs || [];
    jobsByUrl = {};
    jobSelect.innerHTML = '<option value="">Select a job…</option>';
    jobs.forEach((job) => {
      jobsByUrl[job.url] = job;
      const opt = document.createElement("option");
      opt.value = job.url;
      opt.textContent = `${job.job_title || "(untitled)"} — ${job.company_name || ""}`;
      jobSelect.appendChild(opt);
    });
  } catch (error) {
    console.error("Failed to fetch jobs:", error);
  }
}

jobSelect.addEventListener("change", (e) => {
  const job = jobsByUrl[e.target.value];
  if (!job) {
    jobJson.classList.add("hist-empty");
    jobJson.textContent = "Pick a job to view its raw record.";
    return;
  }
  jobJson.classList.remove("hist-empty");
  jobJson.textContent = JSON.stringify(job, null, 2);
});

// ── init ──
async function init() {
  await Promise.all([fetchLlmStats(), fetchSweeps(), fetchJobs()]);
}

document.addEventListener("DOMContentLoaded", init);
