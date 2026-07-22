// settings.js - Job Funnel Settings (search + app config editors)
let searchConfig = {};
let appConfig = {};

function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
  return String(text).replace(/[&<>"']/g, (c) => map[c]);
}

function humanize(key) {
  return String(key).replace(/_/g, " ");
}

function showError(message) {
  const el = document.getElementById("error-message");
  el.textContent = message;
  el.style.display = "block";
  setTimeout(() => {
    el.style.display = "none";
  }, 6000);
}

function linesToList(id) {
  return document
    .getElementById(id)
    .value.split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

// ── fetch ──
async function fetchConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderSearch(data.search || {});
    renderApp(data.app || {});
  } catch (error) {
    console.error("Failed to load config:", error);
    showError(`Failed to load settings: ${error.message}`);
  }
}

// ── search config (structured; preserves unshown fields) ──
function renderSearch(cfg) {
  searchConfig = cfg;
  const workModes = ["remote", "hybrid", "onsite"];
  const exp = cfg.experience_level || {};
  const jobTypes = cfg.job_types || {};
  const dateCfg = cfg.date || {};

  const checks = (obj, group) =>
    Object.keys(obj)
      .map(
        (k) =>
          `<label class="check-item"><input type="checkbox" data-group="${group}" data-key="${escapeHtml(
            k
          )}" ${obj[k] ? "checked" : ""}> ${escapeHtml(humanize(k))}</label>`
      )
      .join("");

  document.getElementById("search-form").innerHTML = `
    <div class="field">
      <label class="field-label" for="s-positions">Positions (one per line)</label>
      <textarea id="s-positions">${escapeHtml((cfg.positions || []).join("\n"))}</textarea>
    </div>
    <div class="field">
      <label class="field-label" for="s-locations">Locations (one per line)</label>
      <textarea id="s-locations">${escapeHtml((cfg.locations || []).join("\n"))}</textarea>
    </div>
    <div class="field">
      <span class="field-label">Work mode</span>
      <div class="check-group">
        ${workModes
          .map(
            (m) =>
              `<label class="check-item"><input type="checkbox" data-group="top" data-key="${m}" ${
                cfg[m] ? "checked" : ""
              }> ${m}</label>`
          )
          .join("")}
      </div>
    </div>
    <div class="field">
      <span class="field-label">Experience level</span>
      <div class="check-group">${checks(exp, "experience_level")}</div>
    </div>
    <div class="field">
      <span class="field-label">Job types</span>
      <div class="check-group">${checks(jobTypes, "job_types")}</div>
    </div>
    <div class="field">
      <span class="field-label">Date posted</span>
      <div class="check-group">
        ${Object.keys(dateCfg)
          .map(
            (k) =>
              `<label class="check-item"><input type="radio" name="s-date" data-key="${escapeHtml(
                k
              )}" ${dateCfg[k] ? "checked" : ""}> ${escapeHtml(humanize(k))}</label>`
          )
          .join("")}
      </div>
    </div>
    <div class="field">
      <label class="check-item"><input type="checkbox" id="s-apply-once" ${
        cfg.apply_once_at_company ? "checked" : ""
      }> Apply once at company</label>
    </div>
    <div class="field">
      <label class="field-label" for="s-company-blacklist">Company blacklist (one per line)</label>
      <textarea id="s-company-blacklist">${escapeHtml((cfg.company_blacklist || []).join("\n"))}</textarea>
    </div>
    <div class="field">
      <label class="field-label" for="s-title-blacklist">Title blacklist (one per line)</label>
      <textarea id="s-title-blacklist">${escapeHtml((cfg.title_blacklist || []).join("\n"))}</textarea>
    </div>
    <p class="field-hint">Fields not shown here (e.g. distance, filters) are preserved on save.</p>
  `;
}

async function saveSearch() {
  // Clone the fetched config so any fields we don't render are preserved.
  const cfg = JSON.parse(JSON.stringify(searchConfig || {}));
  cfg.positions = linesToList("s-positions");
  cfg.locations = linesToList("s-locations");
  cfg.company_blacklist = linesToList("s-company-blacklist");
  cfg.title_blacklist = linesToList("s-title-blacklist");
  cfg.apply_once_at_company = document.getElementById("s-apply-once").checked;

  document.querySelectorAll('#search-form input[data-group]').forEach((input) => {
    const group = input.dataset.group;
    const key = input.dataset.key;
    if (group === "top") {
      cfg[key] = input.checked;
    } else {
      cfg[group] = cfg[group] || {};
      cfg[group][key] = input.checked;
    }
  });

  cfg.date = cfg.date || {};
  document.querySelectorAll('#search-form input[name="s-date"]').forEach((input) => {
    cfg.date[input.dataset.key] = input.checked;
  });

  await putConfig("/api/config/search", cfg, "search-status", "save-search");
}

// ── app config (typed inputs from the editable-key set) ──
function renderApp(app) {
  appConfig = app;
  document.getElementById("app-form").innerHTML = Object.keys(app)
    .map((key) => {
      const val = app[key];
      if (typeof val === "boolean") {
        return `<div class="field"><label class="check-item"><input type="checkbox" data-key="${escapeHtml(
          key
        )}" data-type="boolean" ${val ? "checked" : ""}> ${escapeHtml(key)}</label></div>`;
      }
      let input;
      if (typeof val === "number") {
        const t = Number.isInteger(val) ? "int" : "float";
        input = `<input type="number" step="${t === "int" ? "1" : "any"}" data-key="${escapeHtml(
          key
        )}" data-type="${t}" value="${escapeHtml(String(val))}">`;
      } else {
        input = `<input type="text" data-key="${escapeHtml(key)}" data-type="string" value="${escapeHtml(
          val == null ? "" : String(val)
        )}">`;
      }
      return `<div class="field"><label class="field-label">${escapeHtml(key)}</label>${input}</div>`;
    })
    .join("");
}

async function saveApp() {
  const changes = {};
  document.querySelectorAll("#app-form [data-key]").forEach((input) => {
    const key = input.dataset.key;
    const type = input.dataset.type;
    if (type === "boolean") {
      changes[key] = input.checked;
    } else if (type === "int") {
      const n = parseInt(input.value, 10);
      changes[key] = Number.isNaN(n) ? appConfig[key] : n;
    } else if (type === "float") {
      const n = parseFloat(input.value);
      changes[key] = Number.isNaN(n) ? appConfig[key] : n;
    } else {
      changes[key] = input.value;
    }
  });
  await putConfig("/api/config/app", changes, "app-status", "save-app");
}

// ── shared PUT ──
async function putConfig(url, config, statusId, btnId) {
  const status = document.getElementById(statusId);
  const btn = document.getElementById(btnId);
  btn.disabled = true;
  status.className = "settings-status";
  status.textContent = "Saving…";
  try {
    const response = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    status.className = "settings-status ok";
    status.textContent = "Saved ✓";
    setTimeout(() => {
      status.textContent = "";
    }, 3000);
  } catch (error) {
    status.className = "settings-status err";
    status.textContent = `Failed: ${error.message}`;
  } finally {
    btn.disabled = false;
  }
}

// ── init ──
document.getElementById("save-search").addEventListener("click", saveSearch);
document.getElementById("save-app").addEventListener("click", saveApp);
document.addEventListener("DOMContentLoaded", fetchConfig);
