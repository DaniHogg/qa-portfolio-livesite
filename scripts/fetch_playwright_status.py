#!/usr/bin/env python3
"""Fetch the latest Playwright CI run status from GitHub Actions API.

Writes data/projects/playwright/latest.json in the same normalised schema
used by extract_qa_template_summary.py so the dashboard card renders
consistently.  Does not require Allure artifacts — status, timestamps,
and run URL are sourced entirely from the GitHub REST API.

Usage (CI):
    python scripts/fetch_playwright_status.py \
        --output-dir data/projects \
        --token "$GH_TOKEN"

Usage (local — reads GITHUB_TOKEN env var if --token not supplied):
    python scripts/fetch_playwright_status.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "DaniHogg/playwright-fundamentals"
WORKFLOW = "tests.yml"
BRANCH = "main"

# Spec files derived from test-js/ directory.  Listed here so the dashboard
# can show per-suite rows even when the API only returns overall pass/fail.
SPEC_FILES = [
    "basic",
    "navigation",
    "forms",
    "auth",
    "accessibility",
    "content_interaction",
    "error_handling",
    "advanced",
    "mediawiki_features",
    "performance",
    "search_results",
    "BasicSiteTest",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Playwright CI status from GitHub API")
    parser.add_argument("--output-dir", default="data/projects")
    parser.add_argument("--token", default=os.environ.get("SOURCE_REPO_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--history-limit", type=int, default=5)
    return parser.parse_args()


def gh_get(path: str, token: str) -> dict:
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"GitHub API error {exc.code} for {url}: {exc.read().decode()[:200]}", file=sys.stderr)
        raise


def conclusion_to_status(conclusion: str | None) -> str:
    mapping = {"success": "passed", "failure": "failed", "cancelled": "cancelled"}
    return mapping.get(conclusion or "", "unknown")


def iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_suite_rows(overall_status: str) -> list[dict]:
    """Return a stub row per spec file.  Pass/fail counts are not available
    from the API alone; they show as 0 with a note directing reviewers to
    the linked CI run for full detail."""
    rows = []
    for spec in SPEC_FILES:
        rows.append({
            "suite_id": spec,
            "suite_name": spec.replace("_", " ").title(),
            "status": overall_status if overall_status in ("passed", "failed") else "unknown",
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "notes": "Per-test counts available in the linked Playwright HTML report.",
        })
    return rows


def build_latest_json(run: dict, history_runs: list[dict]) -> dict:
    conclusion = run.get("conclusion")
    status = conclusion_to_status(conclusion)
    started = run.get("run_started_at") or run.get("created_at")
    completed = run.get("updated_at")
    run_id = str(run["id"])
    run_url = run["html_url"]
    branch = run.get("head_branch", BRANCH)
    commit_sha = run.get("head_sha", "")

    # Duration in seconds — approximate from API timestamps
    duration_seconds = 0
    if started and completed:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            duration_seconds = int(
                (datetime.strptime(completed, fmt) - datetime.strptime(started, fmt)).total_seconds()
            )
        except ValueError:
            pass

    history = []
    for h in history_runs:
        history.append({
            "run_id": str(h["id"]),
            "status": conclusion_to_status(h.get("conclusion")),
            "completed_at": h.get("updated_at"),
            "run_url": h["html_url"],
        })

    return {
        "$schema_version": "1.0.0",
        "last_refreshed_at": iso_now(),
        "project": {
            "id": "playwright",
            "name": "Playwright Automation",
            "frameworks": ["playwright", "node"],
            "repository_url": f"https://github.com/{REPO}",
        },
        "latest": {
            "run_id": run_id,
            "source": {
                "provider": "github-actions",
                "workflow": "Playwright Tests",
                "branch": branch,
                "commit_sha": commit_sha,
                "run_url": run_url,
            },
            "status": status,
            "started_at": started,
            "completed_at": completed,
            "duration_seconds": duration_seconds,
            # Per-test totals are not available from the API without downloading
            # Playwright JSON report artifacts.  The run status is authoritative.
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "suites": build_suite_rows(status),
        },
        "history": history,
    }


def main() -> None:
    args = parse_args()

    if not args.token:
        print(
            "Warning: no GitHub token supplied.  Unauthenticated requests are rate-limited to 60/hour.",
            file=sys.stderr,
        )

    runs_data = gh_get(
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
        f"?branch={args.branch}&per_page={args.history_limit + 1}&status=completed",
        args.token,
    )
    runs = runs_data.get("workflow_runs", [])
    if not runs:
        print("No completed workflow runs found.", file=sys.stderr)
        sys.exit(1)

    latest_run = runs[0]
    history_runs = runs[1:]

    output = build_latest_json(latest_run, history_runs)

    out_path = Path(args.output_dir) / "playwright" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Written {out_path}  status={output['latest']['status']}  run={output['latest']['run_id']}")


if __name__ == "__main__":
    main()
