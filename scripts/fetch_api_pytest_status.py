#!/usr/bin/env python3
"""Fetch the latest API-testing-pytest CI run status from GitHub Actions API.

Writes data/projects/api-testing-pytest/latest.json in the same normalised
schema used by fetch_selenium_status.py and fetch_playwright_status.py so
the dashboard card renders consistently.

Suite counts are derived from the pytest-json-report artifact uploaded by
the API-testing-pytest CI workflow (artifact name: api-pytest-json).

Usage (CI):
    python scripts/fetch_api_pytest_status.py \
        --output-dir data/projects \
        --token "$GH_TOKEN"

Usage (local — reads GITHUB_TOKEN env var if --token not supplied):
    python scripts/fetch_api_pytest_status.py
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = "DaniHogg/API-testing-pytest"
WORKFLOW = "ci.yml"
BRANCH = "main"

_FALLBACK_SUITES = ["test_api"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch API-testing-pytest CI status from GitHub API")
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


def gh_get_bytes(url: str, token: str) -> bytes:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            blob_url = exc.headers.get("Location")
            if not blob_url:
                raise
            with urllib.request.urlopen(blob_url, timeout=60) as resp:
                return resp.read()
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


def suite_display_name(suite_id: str) -> str:
    stem = suite_id
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    return stem.replace("_", " ").title()


def parse_pytest_json_data(data: dict) -> tuple[dict, list[dict]]:
    tests = data.get("tests", [])

    suite_counts: dict[str, Counter] = {}
    for test in tests:
        nodeid = test.get("nodeid", "")
        file_part = nodeid.split("::")[0]
        suite_id = Path(file_part).stem

        if suite_id not in suite_counts:
            suite_counts[suite_id] = Counter()

        outcome = (test.get("outcome") or "").lower()
        if outcome == "passed":
            suite_counts[suite_id]["passed"] += 1
        elif outcome == "failed":
            suite_counts[suite_id]["failed"] += 1
        elif outcome in {"skipped", "xfail", "xpass"}:
            suite_counts[suite_id]["skipped"] += 1
        else:
            suite_counts[suite_id]["errors"] += 1

    overall = Counter()
    suite_rows: list[dict] = []
    for suite_id, counts in sorted(suite_counts.items()):
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
            "notes": "Counts derived from pytest-json-report artifact.",
        })

    total_dict = {
        "passed": overall.get("passed", 0),
        "failed": overall.get("failed", 0),
        "skipped": overall.get("skipped", 0),
        "errors": overall.get("errors", 0),
    }
    return total_dict, suite_rows


def parse_results_file(results_json: str) -> tuple[dict, list[dict]] | None:
    path = Path(results_json)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_pytest_json_data(data)


def parse_artifact(run_id: str, token: str) -> tuple[dict, list[dict]] | None:
    if not token:
        return None

    artifacts = gh_get(f"/repos/{REPO}/actions/runs/{run_id}/artifacts", token)
    for artifact in artifacts.get("artifacts", []):
        if artifact.get("name") != "api-pytest-json" or artifact.get("expired"):
            continue

        archive_url = artifact.get("archive_download_url")
        if not archive_url:
            continue

        try:
            blob = gh_get_bytes(archive_url, token)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist():
                    if name.endswith("report.json"):
                        data = json.loads(zf.read(name).decode("utf-8"))
                        return parse_pytest_json_data(data)
        except Exception as exc:
            print(f"Warning: failed to parse api-pytest-json artifact: {exc}", file=sys.stderr)
            return None

    return None


def fallback_totals_and_suites(overall_status: str) -> tuple[dict, list[dict]]:
    note = "Per-test counts unavailable: no api-pytest-json artifact was provided for this run."
    suites = [
        {
            "suite_id": sid,
            "suite_name": suite_display_name(sid),
            "status": overall_status if overall_status in ("passed", "failed") else "unknown",
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "notes": note,
        }
        for sid in _FALLBACK_SUITES
    ]
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

    duration_seconds = 0
    if started and completed:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            duration_seconds = int(
                (datetime.strptime(completed, fmt) - datetime.strptime(started, fmt)).total_seconds()
            )
        except ValueError:
            pass

    history = [
        {
            "run_id": str(h["id"]),
            "status": conclusion_to_status(h.get("conclusion")),
            "completed_at": h.get("updated_at"),
            "run_url": h["html_url"],
        }
        for h in history_runs
    ]

    return {
        "$schema_version": "1.0.0",
        "last_refreshed_at": iso_now(),
        "project": {
            "id": "api-testing-pytest",
            "name": "API Testing with Pytest",
            "frameworks": ["pytest", "requests", "python"],
            "repository_url": f"https://github.com/{REPO}",
        },
        "latest": {
            "run_id": run_id,
            "source": {
                "provider": "github-actions",
                "workflow": "API Tests CI",
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


def write_not_configured(output_dir: str) -> None:
    """Write a placeholder JSON when the GitHub repo is not yet available."""
    out = Path(output_dir) / "api-testing-pytest" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$schema_version": "1.0.0",
        "last_refreshed_at": iso_now(),
        "project": {
            "id": "api-testing-pytest",
            "name": "API Testing with Pytest",
            "frameworks": ["pytest", "requests", "python"],
            "repository_url": f"https://github.com/{REPO}",
        },
        "latest": {
            "run_id": "not-configured",
            "source": {
                "provider": "github-actions",
                "workflow": "API Tests CI",
                "branch": BRANCH,
                "commit_sha": "",
                "run_url": f"https://github.com/{REPO}/actions",
            },
            "status": "unknown",
            "started_at": None,
            "completed_at": None,
            "duration_seconds": 0,
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "suites": [],
        },
        "history": [],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote placeholder: {out}")


def main() -> None:
    args = parse_args()

    if not args.token:
        print(
            "Warning: no GitHub token supplied. Unauthenticated requests are rate-limited to 60/hour.",
            file=sys.stderr,
        )

    try:
        history_data = gh_get(
            f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
            f"?branch={args.branch}&per_page={args.history_limit + 3}&status=completed",
            args.token,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            print(
                f"Warning: cannot reach {REPO} (HTTP {exc.code}). "
                "Writing placeholder — push the repo to GitHub to enable live results.",
                file=sys.stderr,
            )
            write_not_configured(args.output_dir)
            return
        raise

    runs = history_data.get("workflow_runs", [])
    if not runs:
        print(f"No completed runs found for {REPO}/{WORKFLOW}", file=sys.stderr)
        write_not_configured(args.output_dir)
        return

    latest_run = runs[0]
    history_runs = runs[1: args.history_limit + 1]

    # Prefer a results file passed directly (repository_dispatch path)
    if args.results_json:
        parsed = parse_results_file(args.results_json)
    else:
        parsed = parse_artifact(str(latest_run["id"]), args.token)

    if parsed:
        totals, suites = parsed
    else:
        overall_status = conclusion_to_status(latest_run.get("conclusion"))
        totals, suites = fallback_totals_and_suites(overall_status)

    payload = build_latest_json(latest_run, history_runs, totals, suites)

    out_path = Path(args.output_dir) / "api-testing-pytest" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
