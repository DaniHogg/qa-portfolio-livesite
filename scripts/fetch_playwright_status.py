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
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = "DaniHogg/playwright-fundamentals"
WORKFLOW = "tests.yml"
BRANCH = "main"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Playwright CI status from GitHub API")
    parser.add_argument("--output-dir", default="data/projects")
    parser.add_argument("--token", default=os.environ.get("SOURCE_REPO_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--results-json", default="")
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


def status_from_totals(totals: dict) -> str:
    if totals.get("failed", 0) > 0 or totals.get("errors", 0) > 0:
        return "failed"
    if totals.get("passed", 0) > 0:
        return "passed"
    if totals.get("skipped", 0) > 0:
        return "skipped"
    return "unknown"


def iter_specs(suite_node: dict):
    for spec in suite_node.get("specs", []):
        yield spec
    for child in suite_node.get("suites", []):
        yield from iter_specs(child)


def count_test_result(test: dict) -> str:
    # A single test can have retries in results[]; the final result is authoritative.
    results = test.get("results", [])
    if results:
        final = (results[-1].get("status") or "").lower()
    else:
        final = (test.get("status") or "").lower()

    if final == "passed":
        return "passed"
    if final in {"skipped"}:
        return "skipped"
    if final in {"failed"}:
        return "failed"
    if final in {"timedout", "interrupted"}:
        return "errors"
    return "errors"


def normalize_suite_id(suite_file: str) -> str:
    stem = Path(suite_file).stem
    if stem.endswith(".spec"):
        return stem[: -len(".spec")]
    return stem


def suite_display_name(suite_id: str) -> str:
    with_spaces = suite_id.replace("_", " ")
    with_spaces = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", with_spaces)
    return with_spaces.title()


def parse_playwright_results(results_json: str) -> tuple[dict, list[dict]] | None:
    path = Path(results_json)
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    top_suites = data.get("suites", [])
    suite_rows: list[dict] = []
    overall = Counter()

    for suite in top_suites:
        suite_file = suite.get("file") or suite.get("title") or "suite"
        suite_id = normalize_suite_id(suite_file)
        counts = Counter()

        for spec in iter_specs(suite):
            for test in spec.get("tests", []):
                counts[count_test_result(test)] += 1

        totals = {
            "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            "errors": counts.get("errors", 0),
        }
        overall.update(totals)
        suite_rows.append({
            "suite_id": suite_id,
            "suite_name": suite_display_name(suite_id),
            "status": status_from_totals(totals),
            "totals": totals,
            "notes": "Counts derived from Playwright JSON report artifact.",
        })

    total_dict = {
        "passed": overall.get("passed", 0),
        "failed": overall.get("failed", 0),
        "skipped": overall.get("skipped", 0),
        "errors": overall.get("errors", 0),
    }
    return total_dict, suite_rows


def fallback_totals_and_suites(overall_status: str) -> tuple[dict, list[dict]]:
    note = "Per-test counts unavailable: no Playwright JSON artifact was provided for this run."
    suites = []
    for suite_id in [
        "accessibility",
        "advanced",
        "auth",
        "basic",
        "BasicSiteTest",
        "content_interaction",
        "error_handling",
        "forms",
        "mediawiki_features",
        "navigation",
        "performance",
        "search_results",
    ]:
        suites.append({
            "suite_id": suite_id,
            "suite_name": suite_display_name(suite_id),
            "status": overall_status if overall_status in ("passed", "failed") else "unknown",
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "notes": note,
        })
    return ({"passed": 0, "failed": 0, "skipped": 0, "errors": 0}, suites)


def build_latest_json(run: dict, history_runs: list[dict], totals: dict, suites: list[dict]) -> dict:
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
            "totals": totals,
            "suites": suites,
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

    history_data = gh_get(
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
        f"?branch={args.branch}&per_page={args.history_limit + 3}&status=completed",
        args.token,
    )
    history_pool = history_data.get("workflow_runs", [])
    if not history_pool:
        print("No completed workflow runs found.", file=sys.stderr)
        sys.exit(1)

    if args.run_id:
        latest_run = gh_get(f"/repos/{REPO}/actions/runs/{args.run_id}", args.token)
    else:
        latest_run = history_pool[0]

    if args.run_url:
        latest_run["html_url"] = args.run_url
    if args.commit_sha:
        latest_run["head_sha"] = args.commit_sha
    if args.branch:
        latest_run["head_branch"] = args.branch

    history_runs = [h for h in history_pool if str(h.get("id")) != str(latest_run.get("id"))][:args.history_limit]

    parsed = parse_playwright_results(args.results_json) if args.results_json else None
    if parsed:
        totals, suites = parsed
    else:
        totals, suites = fallback_totals_and_suites(conclusion_to_status(latest_run.get("conclusion")))

    output = build_latest_json(latest_run, history_runs, totals, suites)

    out_path = Path(args.output_dir) / "playwright" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Written {out_path}  status={output['latest']['status']}  run={output['latest']['run_id']}")


if __name__ == "__main__":
    main()
