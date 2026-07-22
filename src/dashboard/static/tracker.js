// Tracker.js - Job Funnel Frontend
const STATUSES = ["new", "interested", "applied", "interviewing", "rejected", "offer", "archived"];

// DOM elements
const statusTilesContainer = document.getElementById("status-tiles");
const searchInput = document.getElementById("search-input");
const statusFilter = document.getElementById("status-filter");
const errorMessage = document.getElementById("error-message");
const jobsTableBody = document.getElementById("jobs-tbody");
const tableHeadRow = document.querySelector(".tracker-table thead tr");
const runSweepBtn = document.getElementById("run-sweep-btn");
const stopSweepBtn = document.getElementById("stop-sweep-btn");
const sweepStatusEl = document.getElementById("sweep-status");

let allJobs = []; // master list from the server (never mutated by filtering)
let displayJobs = []; // filtered/sorted view actually rendered
let summary = {};
let currentFilterStatus = null;
let currentSortColumn = null;
let currentSortDirection = "asc";
let sweepPollTimer = null;
let sweepWasRunning = false;

// ──── UTILITY FUNCTIONS ────
/**
 * Escape HTML special characters to prevent XSS
 */
function escapeHtml(text) {
  if (!text) return "";
  const map = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  };
  return text.replace(/[&<>"']/g, (char) => map[char]);
}

/**
 * Format date to YYYY-MM-DD format
 */
function formatDate(dateString) {
  if (!dateString) return "-";
  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;
    return date.toISOString().split("T")[0];
  } catch {
    return dateString;
  }
}

/**
 * Show error message
 */
function showError(message) {
  errorMessage.textContent = message;
  errorMessage.style.display = "block";
  setTimeout(() => {
    errorMessage.style.display = "none";
  }, 5000);
}

/**
 * Determine score badge class and display text
 */
function getScoreBadgeInfo(kw_score, band) {
  if (kw_score === null || kw_score === undefined) {
    return { class: "null", text: "–" };
  }

  if (band === "strong") {
    return { class: "strong", text: String(kw_score) };
  } else if (band === "partial") {
    return { class: "partial", text: String(kw_score) };
  } else {
    return { class: "weak", text: String(kw_score) };
  }
}

// ──── API FUNCTIONS ────
/**
 * Fetch job summary (status counts)
 */
async function fetchSummary() {
  try {
    const response = await fetch("/api/tracker/summary");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    summary = await response.json();
    renderStatusTiles();
    populateStatusFilter();
  } catch (error) {
    console.error("Failed to fetch summary:", error);
    showError("Failed to load summary data");
  }
}

/**
 * Fetch jobs (always unfiltered from server; all filtering is client-side)
 */
async function fetchJobs() {
  try {
    const response = await fetch("/api/tracker/jobs");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    allJobs = data.jobs || [];
    filterAndRenderJobs();
  } catch (error) {
    console.error("Failed to fetch jobs:", error);
    showError("Failed to load jobs");
  }
}

/**
 * Update a job via PATCH
 */
async function updateJob(url, updates) {
  try {
    const response = await fetch("/api/tracker/jobs", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, ...updates }),
    });
    if (!response.ok) {
      if (response.status === 400) {
        const error = await response.json();
        throw new Error(error.detail || "Invalid update");
      } else if (response.status === 404) {
        throw new Error("Job not found");
      }
      throw new Error(`HTTP ${response.status}`);
    }
    const updated = await response.json();
    // Refresh summary and jobs after update
    await fetchSummary();
    await fetchJobs();
  } catch (error) {
    console.error("Failed to update job:", error);
    showError(`Failed to update job: ${error.message}`);
  }
}

// ──── SWEEP CONTROLS ────
/**
 * Fetch sweep process state and reflect it in the controls.
 */
async function fetchSweepStatus() {
  try {
    const response = await fetch("/api/tracker/sweep");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSweepStatus(await response.json());
  } catch (error) {
    console.error("Failed to fetch sweep status:", error);
  }
}

/**
 * Reflect sweep status in buttons + status line; refresh data when a run ends.
 */
function renderSweepStatus(status) {
  const running = !!status.running;
  runSweepBtn.style.display = running ? "none" : "";
  stopSweepBtn.style.display = running ? "" : "none";
  sweepStatusEl.classList.toggle("running", running);

  if (running) {
    sweepStatusEl.textContent = "Sweep running…";
  } else if (status.latest && status.latest.status !== "running") {
    const l = status.latest;
    const parts = [];
    if (l.collected != null) parts.push(`${l.collected} collected`);
    if (l.new_count != null) parts.push(`${l.new_count} new`);
    const detail = parts.length ? ` (${parts.join(", ")})` : "";
    sweepStatusEl.textContent = `Last sweep: ${l.status}${detail}`;
  } else {
    sweepStatusEl.textContent = "";
  }

  // On a running → finished transition, refresh the jobs + status counts.
  if (sweepWasRunning && !running) {
    stopSweepPolling();
    fetchSummary();
    fetchJobs();
  }
  sweepWasRunning = running;
  if (running) startSweepPolling();
}

function startSweepPolling() {
  if (sweepPollTimer) return;
  sweepPollTimer = setInterval(fetchSweepStatus, 3000);
}

function stopSweepPolling() {
  if (sweepPollTimer) {
    clearInterval(sweepPollTimer);
    sweepPollTimer = null;
  }
}

async function runSweep() {
  runSweepBtn.disabled = true;
  try {
    const response = await fetch("/api/tracker/sweep/start", { method: "POST" });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    renderSweepStatus(await response.json());
    startSweepPolling();
  } catch (error) {
    showError(`Could not start sweep: ${error.message}`);
  } finally {
    runSweepBtn.disabled = false;
  }
}

async function stopSweep() {
  stopSweepBtn.disabled = true;
  try {
    await fetch("/api/tracker/sweep/stop", { method: "POST" });
    await fetchSweepStatus();
  } catch (error) {
    showError(`Could not stop sweep: ${error.message}`);
  } finally {
    stopSweepBtn.disabled = false;
  }
}

/**
 * Build the résumé / cover-letter links for a tailored job (empty if none)
 */
function buildTailorLinks(job) {
  const links = [];
  if (job.tailored_resume_path) {
    links.push(
      `<a href="/api/tracker/file?path=${encodeURIComponent(job.tailored_resume_path)}" target="_blank" rel="noopener">Résumé PDF</a>`
    );
  }
  if (job.tailored_cover_path) {
    links.push(
      `<a href="/api/tracker/file?path=${encodeURIComponent(job.tailored_cover_path)}" target="_blank" rel="noopener">Cover letter</a>`
    );
  }
  return links.join("");
}

/**
 * Re-open (and scroll to) a job's expand row after a re-render
 */
function reopenRow(url) {
  const expandRow = document.querySelector(
    `.expand-row[data-url="${CSS.escape(url)}"]`
  );
  if (expandRow) {
    expandRow.classList.add("open");
    expandRow.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/**
 * Generate a tailored résumé + cover letter for a job
 */
async function tailorJob(url, btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Tailoring… (up to a minute)";
  try {
    const response = await fetch("/api/tracker/jobs/tailor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    // Refresh rows (per-job cost + links), then re-open the row.
    await fetchJobs();
    reopenRow(url);
  } catch (error) {
    console.error("Tailoring failed:", error);
    showError(`Tailoring failed: ${error.message}`);
    btn.disabled = false;
    btn.textContent = original;
  }
}

// ──── RENDERING FUNCTIONS ────
/**
 * Render status tiles
 */
function renderStatusTiles() {
  statusTilesContainer.innerHTML = "";

  STATUSES.forEach((status) => {
    const tile = document.createElement("div");
    tile.className = "status-tile";
    if (currentFilterStatus === status) {
      tile.classList.add("active");
    }

    const label = document.createElement("span");
    label.className = "status-tile-label";
    label.textContent = status.replace(/_/g, " ");

    const count = document.createElement("span");
    count.className = "status-tile-count";
    count.textContent = summary[status] || 0;

    tile.appendChild(label);
    tile.appendChild(count);

    tile.addEventListener("click", () => {
      if (currentFilterStatus === status) {
        currentFilterStatus = null;
        statusFilter.value = "";
      } else {
        currentFilterStatus = status;
        statusFilter.value = status;
      }
      renderStatusTiles();
      filterAndRenderJobs();
    });

    statusTilesContainer.appendChild(tile);
  });

  // Add Total tile — clicking it clears the status filter (shows all)
  const totalTile = document.createElement("div");
  totalTile.className = "status-tile";
  if (currentFilterStatus === null) {
    totalTile.classList.add("active");
  }
  const totalLabel = document.createElement("span");
  totalLabel.className = "status-tile-label";
  totalLabel.textContent = "Total";
  const totalCount = document.createElement("span");
  totalCount.className = "status-tile-count";
  totalCount.textContent = summary.total || 0;
  totalTile.appendChild(totalLabel);
  totalTile.appendChild(totalCount);
  totalTile.addEventListener("click", () => {
    currentFilterStatus = null;
    statusFilter.value = "";
    renderStatusTiles();
    filterAndRenderJobs();
  });
  statusTilesContainer.appendChild(totalTile);
}

/**
 * Populate status filter select
 */
function populateStatusFilter() {
  const existingOptions = Array.from(statusFilter.options).slice(1); // Keep "All statuses"

  STATUSES.forEach((status) => {
    if (!existingOptions.find((opt) => opt.value === status)) {
      const option = document.createElement("option");
      option.value = status;
      option.textContent = status.replace(/_/g, " ");
      statusFilter.appendChild(option);
    }
  });
}

/**
 * Filter jobs based on search and status
 */
function filterAndRenderJobs() {
  const searchTerm = searchInput.value.toLowerCase();

  displayJobs = allJobs.filter((job) => {
    const matchesStatus =
      !currentFilterStatus ||
      (job.status === currentFilterStatus ||
        (!job.status && currentFilterStatus === "new"));

    const matchesSearch =
      !searchTerm ||
      (job.job_title || "").toLowerCase().includes(searchTerm) ||
      (job.company_name || "").toLowerCase().includes(searchTerm) ||
      (job.location || "").toLowerCase().includes(searchTerm);

    return matchesStatus && matchesSearch;
  });

  renderJobs();
}

/**
 * Sort jobs based on current sort column and direction
 */
function sortJobs() {
  if (!currentSortColumn) return;

  displayJobs.sort((a, b) => {
    let aVal = a[currentSortColumn];
    let bVal = b[currentSortColumn];

    // Handle null/undefined
    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return currentSortDirection === "asc" ? 1 : -1;
    if (bVal == null) return currentSortDirection === "asc" ? -1 : 1;

    // Handle numeric values (kw_score)
    if (typeof aVal === "number" && typeof bVal === "number") {
      return currentSortDirection === "asc" ? aVal - bVal : bVal - aVal;
    }

    // Handle date strings
    if (currentSortColumn === "first_seen") {
      const aDate = new Date(aVal);
      const bDate = new Date(bVal);
      return currentSortDirection === "asc" ? aDate - bDate : bDate - aDate;
    }

    // Handle string values
    aVal = String(aVal).toLowerCase();
    bVal = String(bVal).toLowerCase();

    if (currentSortDirection === "asc") {
      return aVal.localeCompare(bVal);
    } else {
      return bVal.localeCompare(aVal);
    }
  });
}

/**
 * Render jobs table
 */
function renderJobs() {
  sortJobs();

  if (displayJobs.length === 0) {
    jobsTableBody.innerHTML =
      '<tr><td colspan="7" class="tracker-loading">No jobs found</td></tr>';
    return;
  }

  jobsTableBody.innerHTML = displayJobs
    .map((job) => {
      const scoreBadge = getScoreBadgeInfo(job.kw_score, job.band);
      const jobUrl = escapeHtml(job.url);
      const jobTitle = escapeHtml(job.job_title);
      const company = escapeHtml(job.company_name);
      const location = escapeHtml(job.location);
      const salary = escapeHtml(job.salary_range || "");
      const status = job.status || "new";
      const firstSeen = formatDate(job.first_seen);

      return `
        <tr data-url="${jobUrl}" class="job-row">
          <td><span class="score-badge ${scoreBadge.class}">${scoreBadge.text}</span></td>
          <td><a href="${jobUrl}" target="_blank" rel="noopener" class="job-title-link">${jobTitle}</a></td>
          <td>${company}</td>
          <td>${location}</td>
          <td>${salary || "-"}</td>
          <td>
            <select class="job-status-select" data-url="${jobUrl}" onclick="event.stopPropagation()">
              ${STATUSES.map((s) => `<option value="${s}" ${s === status ? "selected" : ""}>${s}</option>`).join("")}
            </select>
          </td>
          <td>${firstSeen}</td>
        </tr>
        <tr class="expand-row" data-url="${jobUrl}">
          <td colspan="7">
            <div class="expand-row-content">
              <div>
                <div class="expand-section">
                  <label class="expand-section-label">Notes</label>
                  <textarea class="notes-textarea" data-url="${jobUrl}" placeholder="Add notes about this job...">${escapeHtml(job.notes || "")}</textarea>
                </div>
                <div class="expand-section">
                  <label class="expand-section-label">Applied Date</label>
                  <input type="date" class="applied-date-input" data-url="${jobUrl}" value="${job.applied_date || ""}">
                </div>
                <div class="expand-section">
                  <label class="expand-section-label">Matching Keywords</label>
                  <div class="matching-keywords">
                    ${
                      (job.matched && job.matched.length > 0)
                        ? job.matched.map((kw) => `<span class="keyword-badge matched">${escapeHtml(kw)}</span>`).join("")
                        : '<span class="keyword-badge">None</span>'
                    }
                  </div>
                </div>
                <div class="expand-section">
                  <label class="expand-section-label">Missing Keywords</label>
                  <div class="missing-keywords">
                    ${
                      (job.missing && job.missing.length > 0)
                        ? job.missing.map((kw) => `<span class="keyword-badge">${escapeHtml(kw)}</span>`).join("")
                        : '<span class="keyword-badge">None</span>'
                    }
                  </div>
                </div>
                <div class="expand-section tailor-section">
                  <button class="tailor-btn" data-url="${jobUrl}">
                    ${job.tailored_resume_path ? "Re-tailor" : "Tailor résumé + cover"}
                  </button>
                  <span class="tailor-links">${buildTailorLinks(job)}</span>
                  ${job.tailor_cost ? `<span class="tailor-cost">$${Number(job.tailor_cost).toFixed(4)}</span>` : ""}
                </div>
                <div class="job-description-preview">${escapeHtml((job.job_description || "").substring(0, 600))}</div>
              </div>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  attachJobEventListeners();
}

/**
 * Attach event listeners to job rows and controls
 */
function attachJobEventListeners() {
  // Row expand/collapse
  document.querySelectorAll(".job-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest(".job-status-select") || e.target.closest("a")) {
        return;
      }
      const url = row.dataset.url;
      const expandRow = document.querySelector(
        `.expand-row[data-url="${CSS.escape(url)}"]`
      );
      if (expandRow) {
        expandRow.classList.toggle("open");
      }
    });
  });

  // Status select changes
  document
    .querySelectorAll(".job-status-select")
    .forEach((select) => {
      select.addEventListener("change", async (e) => {
        const url = e.target.dataset.url;
        const newStatus = e.target.value;
        await updateJob(url, { status: newStatus });
      });
    });

  // Notes textarea blur
  document.querySelectorAll(".notes-textarea").forEach((textarea) => {
    textarea.addEventListener("blur", async (e) => {
      const url = e.target.dataset.url;
      const notes = e.target.value;
      await updateJob(url, { notes });
    });
  });

  // Applied date input change
  document.querySelectorAll(".applied-date-input").forEach((input) => {
    input.addEventListener("change", async (e) => {
      const url = e.target.dataset.url;
      const appliedDate = e.target.value;
      await updateJob(url, { applied_date: appliedDate });
    });
  });

  // Tailor button
  document.querySelectorAll(".tailor-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await tailorJob(btn.dataset.url, btn);
    });
  });
}

/**
 * Update sort indicator on table headers
 */
function updateSortIndicators() {
  document.querySelectorAll(".tracker-table th").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.column === currentSortColumn) {
      if (currentSortDirection === "asc") {
        th.classList.add("sorted-asc");
      } else {
        th.classList.add("sorted-desc");
      }
    }
  });
}

// ──── EVENT LISTENERS ────
/**
 * Table header click - sorting
 */
tableHeadRow.addEventListener("click", (e) => {
  const th = e.target.closest("th");
  if (!th || !th.classList.contains("sortable")) return;

  const column = th.dataset.column;

  if (currentSortColumn === column) {
    // Toggle direction
    currentSortDirection = currentSortDirection === "asc" ? "desc" : "asc";
  } else {
    // New column, default to ascending
    currentSortColumn = column;
    currentSortDirection = "asc";
  }

  updateSortIndicators();
  renderJobs();
});

/**
 * Search input - client-side filtering
 */
searchInput.addEventListener("input", () => {
  filterAndRenderJobs();
});

/**
 * Status filter select - synced with tiles
 */
statusFilter.addEventListener("change", (e) => {
  currentFilterStatus = e.target.value || null;
  renderStatusTiles();
  filterAndRenderJobs();
});

// Sweep controls
runSweepBtn.addEventListener("click", runSweep);
stopSweepBtn.addEventListener("click", stopSweep);

// ──── INITIALIZATION ────
async function init() {
  // Set initial sort column
  currentSortColumn = "kw_score";
  currentSortDirection = "desc";

  // Load data
  await fetchSummary();
  await fetchJobs();
  await fetchSweepStatus();

  // Update sort indicators
  updateSortIndicators();
}

// Start on page load
document.addEventListener("DOMContentLoaded", init);
