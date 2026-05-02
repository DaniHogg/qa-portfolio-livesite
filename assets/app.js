async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Failed to fetch ${path}: ${res.status}`);
  }
  return res.json();
}

function fmtDate(value) {
  if (!value) return "n/a";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleString();
}

function staleLabel(latest) {
  const staleDays = Number(latest?.stale_after_days || 7);
  const end = new Date(latest?.completed_at || 0).getTime();
  const ageMs = Date.now() - end;
  const stale = end > 0 && ageMs > staleDays * 86400000;
  return stale ? `Stale (${staleDays}+ days old)` : "Fresh";
}

function badge(status) {
  const safe = status || "not-run";
  return `<span class="status ${safe}">${safe}</span>`;
}

async function renderIndex() {
  const host = document.getElementById("project-cards");
  if (!host) return;

  const index = await fetchJson("data/projects/index.json");
  if (!Array.isArray(index.projects) || index.projects.length === 0) {
    host.innerHTML = "<p>No projects available.</p>";
    return;
  }

  const cards = [];
  for (const item of index.projects) {
    const latest = await fetchJson(`data/projects/${item.id}/latest.json`);
    const run = latest.latest || {};
    const stale = staleLabel(run);
    const staleClass = stale.startsWith("Stale") ? "stale" : "";
    cards.push(`
      <article class="card">
        <h3>${latest.project?.name || item.id}</h3>
        <p class="muted">${item.summary || ""}</p>
        <p>${badge(run.status)}</p>
        <p class="muted">Completed: ${fmtDate(run.completed_at)}</p>
        <p class="${staleClass}">${stale}</p>
        <div class="link-row">
          <a href="project.html?project=${encodeURIComponent(item.id)}">View details</a>
          <a href="${run.source?.run_url || '#'}" target="_blank" rel="noreferrer">Workflow run</a>
        </div>
      </article>
    `);
  }

  host.innerHTML = cards.join("\n");
}

async function renderProject() {
  const title = document.getElementById("project-title");
  if (!title) return;

  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("project");
  if (!projectId) {
    title.textContent = "Missing project id";
    return;
  }

  const data = await fetchJson(`data/projects/${projectId}/latest.json`);
  const project = data.project || {};
  const latest = data.latest || {};

  document.getElementById("project-kicker").textContent = project.id || projectId;
  title.textContent = project.name || projectId;
  document.getElementById("project-summary").textContent =
    `${badge(latest.status)} Completed: ${fmtDate(latest.completed_at)} | ${staleLabel(latest)}`;

  const meta = [
    ["Run ID", latest.run_id],
    ["Branch", latest.source?.branch],
    ["Commit", latest.source?.commit_sha],
    ["Duration", `${latest.duration_seconds || 0}s`],
    ["Last refreshed", fmtDate(data.last_refreshed_at)],
    ["Workflow", latest.source?.workflow],
  ];

  document.getElementById("latest-meta").innerHTML = meta
    .map(([k, v]) => `<div class="card"><strong>${k}</strong><br>${v || "n/a"}</div>`)
    .join("\n");

  const suites = Array.isArray(latest.suites) ? latest.suites : [];
  document.getElementById("suite-rows").innerHTML = suites.map((s) => `
    <tr>
      <td>${s.suite_name || s.suite_id}</td>
      <td>${badge(s.status)}</td>
      <td>${s.totals?.passed || 0}</td>
      <td>${s.totals?.failed || 0}</td>
      <td>${s.totals?.skipped || 0}</td>
      <td>${s.notes || ""}</td>
    </tr>
  `).join("\n");

  const history = Array.isArray(data.history) ? data.history : [];
  document.getElementById("history-list").innerHTML = history.map((h) => `
    <li>
      <span>${badge(h.status)} ${fmtDate(h.completed_at)}</span>
      <a href="${h.run_url || '#'}" target="_blank" rel="noreferrer">Run</a>
    </li>
  `).join("\n");

  const coveragePath = `data/projects/${projectId}/coverage-audit.json`;
  const coverageLink = document.getElementById("coverage-link");
  if (coverageLink) {
    coverageLink.href = coveragePath;
  }

  const coverageHost = document.getElementById("coverage-summary");
  if (coverageHost) {
    try {
      const coverage = await fetchJson(coveragePath);
      const summary = coverage.summary || {};
      const cards = [
        ["Covered suites", summary.covered_suites],
        ["Not covered suites", summary.not_covered_suites],
        ["Unknown suites", summary.unknown_suites],
        ["Audit run", coverage.run_id],
      ];
      coverageHost.innerHTML = cards
        .map(([k, v]) => `<div class="card"><strong>${k}</strong><br>${v ?? "n/a"}</div>`)
        .join("\n");
    } catch (err) {
      coverageHost.innerHTML = `<div class="card">Coverage audit unavailable: ${err.message}</div>`;
    }
  }
}

renderIndex().catch((err) => {
  const host = document.getElementById("project-cards");
  if (host) host.innerHTML = `<p>${err.message}</p>`;
});

renderProject().catch((err) => {
  const title = document.getElementById("project-title");
  if (title) title.textContent = err.message;
});
