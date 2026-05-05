#!/usr/bin/env python3
"""Fetch the latest Postman/Newman CI run status from GitHub Actions API.

Writes data/projects/postman-api/latest.json in the normalised schema used by
other fetch scripts so the dashboard card renders consistently.

Suite rows represent individual Newman request items. Pass/fail counts come
from the --reporter-json-export artifact uploaded by the Postman CI workflow
(artifact name: postman-json).

Usage (CI):
    python scripts/fetch_postman_status.py \
        --output-dir data/projects \
        --token "$GH_TOKEN"

Usage (local — reads GITHUB_TOKEN env var if --token not supplied):
    python scripts/fetch_postman_status.py
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
from datetime import datetime, timezone
from pathlib import Path

REPO = "DaniHogg/Postman-API-Tests"
WORKFLOW = "ci.yml"
BRANCH = "master"

# Request names from the Render Portfolio API Tests collection — used as a
# fallback when no artifact is available.
_FALLBACK_SUITES = [
    "1 - Health Check",
    "2 - List Posts",
    "3 - Create Post",
    "4 - Get Post by Id",
    "5 - Update Post",
    "6 - Delete Post",
    "7 - Get Non-existent Post (404)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Postman/Newman CI status from GitHub API")
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


def parse_newman_json(data: dict) -> tuple[dict, list[dict]]:
    """Parse a Newman --reporter-json-export report into totals + per-request suite rows.

    Newman JSON structure (run.executions is the list of request executions):
      run.executions[n].item.name  — request name
      run.executions[n].assertions — list of {assertion, skipped, error}
    """
    executions = data.get("run", {}).get("executions", [])

    suite_rows: list[dict] = []
    overall = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    for execution in executions:
        name = execution.get("item", {}).get("name", "unknown")
        assertions = execution.get("assertions", []) or []

        passed = 0
        failed = 0
        skipped = 0

        for a in assertions:
            if a.get("skipped"):
                skipped += 1
            elif a.get("error") is not None:
                failed += 1
            else:
                passed += 1

        # If there are no assertion objects, treat the request itself as 1 test.
        # Newman only omits assertions when no pm.test() calls exist.
        if not assertions:
            passed = 1

        if failed > 0:
            row_status = "failed"
        elif passed > 0:
            row_status = "passed"
        else:
            row_status = "skipped"

        overall["passed"] += passed
        overall["failed"] += failed
        overall["skipped"] += skipped

        suite_rows.append({
            "suite_id": name,
            "suite_name": name,
            "status": row_status,
            "totals": {
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "errors": 0,
            },
            "notes": "Assertion counts derived from Newman JSON reporter.",
        })

    return overall, suite_rows


def parse_results_file(results_json: str) -> tuple[dict, list[dict]] | None:
    path = Path(results_json)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_newman_json(data)


def parse_artifact(run_id: str, token: str) -> tuple[dict, list[dict]] | None:
    if not token:
        return None

    artifacts = gh_get(f"/repos/{REPO}/actions/runs/{run_id}/artifacts", token)
    for artifact in artifacts.get("artifacts", []):
        if artifact.get("name") != "postman-json" or artifact.get("expired"):
            continue

        archive_url = artifact.get("archive_download_url")
        if not archive_url:
            continue

        try:
            blob = gh_get_bytes(archive_url, token)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist():
                    if name.endswith(".json"):
                        data = json.loads(zf.read(name).decode("utf-8"))
                        return parse_newman_json(data)
        except Exception as exc:
            print(f"Warning: failed to parse postman-json artifact: {exc}", file=sys.stderr)
            return None

    return None


def fallback_totals_and_suites(overall_status: str) -> tuple[dict, list[dict]]:
    note = "Per-request counts unavailable: no postman-json artifact was found for this run."
    suites = [
        {
            "suite_id": name,
            "suite_name": name,
            "status": overall_status if overall_status in ("passed", "failed") else "unknown",
            "totals": {"passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "notes": note,
        }
        for name in _FALLBACK_SUITES
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
            "id": "postman-api",
            "name": "Postman API Testing",
            "frameworks": ["postman", "newman", "javascript"],
            "repository_url": f"https://github.com/{REPO}",
        },
        "latest": {
            "run_id": run_id,
            "source": {
                "provider": "github-actions",
                "workflow": "Postman API Tests CI",
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
    """Write a placeholder JSON when the GitHub repo has no CI runs yet."""
    out = Path(output_dir) / "postman-api" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$schema_version": "1.0.0",
        "last_refreshed_at": iso_now(),
        "project": {
            "id": "postman-api",
            "name": "Postman API Testing",
            "frameworks": ["postman", "newman", "javascript"],
            "repository_url": f"https://github.com/{REPO}",
        },
        "latest": {
            "run_id": "not-configured",
            "source": {
                "provider": "github-actions",
                "workflow": "Postman API Tests CI",
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
                "Writing placeholder — push the CI workflow to GitHub to enable live results.",
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

    out_path = Path(args.output_dir) / "postman-api" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
