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
          <a href="${latest.project?.repository_url || '#'}" target="_blank" rel="noreferrer">Repository</a>
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
    .map(([k, v]) => {
      const safeValue = v || "n/a";
      const valueClass = k === "Commit" ? "meta-value meta-value-commit" : "meta-value";
      return `<div class="card"><strong>${k}</strong><br><span class="${valueClass}">${safeValue}</span></div>`;
    })
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

async function renderPortfolio() {
  const host = document.getElementById("portfolio-cards");
  if (!host) return;

  const data = await fetchJson("data/portfolio-projects.json");
  const projects = Array.isArray(data.projects) ? data.projects : [];
  if (projects.length === 0) {
    host.innerHTML = "<p>No portfolio projects available.</p>";
    return;
  }

  // Group projects by category, preserving insertion order
  const groups = new Map();
  for (const project of projects) {
    const cat = project.category || "Other";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(project);
  }

  const sections = [];
  for (const [category, items] of groups) {
    const cards = items.map((project) => {
      const links = Array.isArray(project.links) ? project.links : [];
      const linkHtml = links.length > 0
        ? `<div class="link-row">${links.map((link) => {
            const external = link.external ? ' target="_blank" rel="noreferrer"' : "";
            return `<a href="${link.url || '#'}"${external}>${link.label || "Open"}</a>`;
          }).join("\n")}</div>`
        : "";

      const whatTested = project.what_tested
        ? `<p class="portfolio-field"><span class="field-label">What was tested:</span> ${project.what_tested}</p>`
        : "";
      const whyMatters = project.why_it_matters
        ? `<p class="portfolio-field"><span class="field-label">Why it matters:</span> ${project.why_it_matters}</p>`
        : "";

      return `
        <article class="card portfolio-card">
          <h3>${project.name || "Untitled project"}</h3>
          <p class="portfolio-field"><span class="field-label">How it&apos;s built:</span> ${project.tech_stack || "n/a"}</p>
          ${whatTested}
          ${whyMatters}
          ${linkHtml}
        </article>`;
    }).join("\n");

    sections.push(`
      <section class="portfolio-category">
        <h2 class="category-heading">${category}</h2>
        <div class="portfolio-grid">${cards}
        </div>
      </section>`);
  }

  host.innerHTML = sections.join("\n");
}

renderIndex().catch((err) => {
  const host = document.getElementById("project-cards");
  if (host) host.innerHTML = `<p>${err.message}</p>`;
});

renderProject().catch((err) => {
  const title = document.getElementById("project-title");
  if (title) title.textContent = err.message;
});

renderPortfolio().catch((err) => {
  const host = document.getElementById("portfolio-cards");
  if (host) host.innerHTML = `<p>${err.message}</p>`;
});

async function renderProofStrip() {
  const strip = document.getElementById("proof-strip");
  if (!strip) return;

  const tools = ["Pytest", "Playwright", "Selenium", "Appium", "Postman", "k6"];

  try {
    const index = await fetchJson("data/projects/index.json");
    const projects = Array.isArray(index.projects) ? index.projects : [];

    let totalPassed = 0;
    let totalFailed = 0;
    let activeCount = 0;
    let lastUpdated = null;

    for (const item of projects) {
      try {
        const latest = await fetchJson(`data/projects/${item.id}/latest.json`);
        const run = latest.latest || {};
        if (run.status && run.status !== "not-run") activeCount++;
        totalPassed += run.totals?.passed || 0;
        totalFailed += run.totals?.failed || 0;
        const runDate = run.completed_at ? new Date(run.completed_at) : null;
        if (runDate && (!lastUpdated || runDate > lastUpdated)) lastUpdated = runDate;
      } catch (_) { /* project data unavailable, skip */ }
    }

    const updatedStr = lastUpdated ? lastUpdated.toLocaleDateString() : "n/a";
    const toolChips = tools.map((t) => `<span class="tool-chip">${t}</span>`).join("");

    strip.innerHTML = `
      <span class="strip-item"><span class="strip-ok">&#10003;</span> ${totalPassed} Passing</span>
      <span class="strip-sep">|</span>
      <span class="strip-item"><span class="strip-fail">&#10007;</span> ${totalFailed} Failing</span>
      <span class="strip-sep">|</span>
      <span class="strip-item">${activeCount} Active project${activeCount !== 1 ? "s" : ""}</span>
      <span class="strip-sep">|</span>
      <span class="strip-item strip-muted">Last updated: ${updatedStr}</span>
      <span class="strip-sep">|</span>
      <span class="strip-item strip-tools">${toolChips}</span>
    `;
  } catch (_) {
    const toolChips = tools.map((t) => `<span class="tool-chip">${t}</span>`).join("");
    strip.innerHTML = `<span class="strip-item strip-tools">${toolChips}</span>`;
  }
}

renderProofStrip().catch(() => {});

async function renderProjectContext() {
  const contextEl = document.getElementById("project-context");
  const listEl = document.getElementById("project-context-list");
  if (!contextEl || !listEl) return;

  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("project");
  if (!projectId) return;

  try {
    const portfolio = await fetchJson("data/portfolio-projects.json");
    const match = (portfolio.projects || []).find(
      (p) => p.name?.toLowerCase().replace(/\s+/g, "-") === projectId ||
             projectId.includes(p.name?.split(" ")[0]?.toLowerCase())
    );

    if (match) {
      const items = [
        match.what_tested ? `What was tested: ${match.what_tested}` : null,
        match.why_it_matters ? `Why it matters: ${match.why_it_matters}` : null,
        match.tech_stack ? `Tools: ${match.tech_stack}` : null,
      ].filter(Boolean);

      if (items.length > 0) {
        listEl.innerHTML = items.map((i) => `<li>${i}</li>`).join("");
        contextEl.hidden = false;
      }
    }
  } catch (_) { /* portfolio data unavailable, skip */ }
}

renderProjectContext().catch(() => {});
