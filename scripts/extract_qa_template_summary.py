#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Totals:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract normalized summary from Allure results")
    parser.add_argument("--project-id", default="qa-automation-template")
    parser.add_argument("--project-name", default="QA Automation Template")
    parser.add_argument("--allure-dir", required=True)
    parser.add_argument("--output-dir", default="data/projects")
    parser.add_argument("--repo-url", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--workflow", default="QA Template CI")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--stale-after-days", type=int, default=7)
    return parser.parse_args()


def iso_from_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_status(status: str) -> str:
    s = (status or "").lower()
    if s in {"passed"}:
        return "passed"
    if s in {"skipped"}:
        return "skipped"
    if s in {"failed", "broken", "unknown"}:
        return "failed"
    return "failed"


def classify_suite(record: dict[str, Any]) -> str:
    label_items = record.get("labels") or []
    tags = {x.get("value", "") for x in label_items if x.get("name") == "tag"}
    full_name = (record.get("fullName") or "").lower()

    if "unit" in tags or ".unit." in full_name:
        return "unit"
    if "winapp" in tags or "winapp" in full_name:
        return "reference-winapp"
    if "mobile" in tags or ".mobile." in full_name:
        return "mobile"

    if "api" in tags:
        if "smoke" in tags:
            return "api-smoke"
        if "regression" in tags:
            return "api-regression"
        return "api"

    if "web" in tags:
        if "smoke" in tags:
            return "web-smoke"
        if "regression" in tags:
            return "web-regression"
        return "web"

    if ".api." in full_name:
        return "api"
    if ".web." in full_name:
        return "web"

    return "other"


def suite_name(suite_id: str) -> str:
    mapping = {
        "unit": "Unit",
        "api": "API",
        "api-smoke": "API Smoke",
        "api-regression": "API Regression",
        "web": "Web",
        "web-smoke": "Web Smoke",
        "web-regression": "Web Regression",
        "mobile": "Mobile",
        "reference-winapp": "Reference WinApp",
        "other": "Other",
    }
    return mapping.get(suite_id, suite_id.replace("-", " ").title())


def expected_suites(project_id: str) -> list[tuple[str, str]]:
    if project_id == "qa-automation-template":
        return [
            ("unit", "Framework-level tests"),
            ("api-smoke", "Basic API health checks"),
            ("api-regression", "Extended API coverage; may be workflow_dispatch-gated"),
            ("web-smoke", "UI smoke coverage; may be workflow_dispatch-gated"),
            ("web-regression", "UI regression coverage; may be workflow_dispatch-gated"),
        ]
    return []


def combine_status(totals: Totals) -> str:
    if totals.failed > 0 or totals.errors > 0:
        return "failed"
    if totals.passed > 0 and totals.skipped == 0:
        return "passed"
    if totals.passed > 0 and totals.skipped > 0:
        return "partial"
    if totals.skipped > 0:
        return "skipped"
    return "not-run"


def aggregate(allure_dir: Path) -> tuple[dict[str, Any], dict[str, Totals], str | None, str | None]:
    totals = Totals()
    suite_totals: dict[str, Totals] = defaultdict(Totals)
    started: list[int] = []
    stopped: list[int] = []

    result_files = sorted(allure_dir.glob("*-result.json"))
    if not result_files:
        raise FileNotFoundError(f"No result files found in {allure_dir}")

    for path in result_files:
        record = read_json(path)
        st = normalize_status(record.get("status", ""))
        suite_id = classify_suite(record)

        if st == "passed":
            totals.passed += 1
            suite_totals[suite_id].passed += 1
        elif st == "skipped":
            totals.skipped += 1
            suite_totals[suite_id].skipped += 1
        else:
            totals.failed += 1
            suite_totals[suite_id].failed += 1

        start_ms = record.get("start")
        stop_ms = record.get("stop")
        if isinstance(start_ms, int):
            started.append(start_ms)
        if isinstance(stop_ms, int):
            stopped.append(stop_ms)

    return totals.to_dict(), suite_totals, iso_from_ms(min(started) if started else None), iso_from_ms(max(stopped) if stopped else None)


def overall_status(suites: list[dict[str, Any]]) -> str:
    values = [s.get("status") for s in suites]
    if any(v == "failed" for v in values):
        return "failed"
    if all(v in {"skipped", "not-run"} for v in values):
        return "skipped"
    if any(v in {"skipped", "not-run", "partial"} for v in values):
        return "partial"
    return "passed"


def load_history(runs_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in sorted(runs_dir.glob("*.json")):
        try:
            rows.append(read_json(file))
        except json.JSONDecodeError:
            continue

    rows.sort(key=lambda x: (x.get("completed_at", ""), x.get("run_id", "")), reverse=True)
    return rows


def ensure_project_index(root: Path, project_id: str, summary: str) -> None:
    index_path = root / "index.json"
    payload: dict[str, Any]
    if index_path.exists():
        payload = read_json(index_path)
    else:
        payload = {"projects": []}

    projects = payload.setdefault("projects", [])
    if not any(p.get("id") == project_id for p in projects):
        projects.append({"id": project_id, "summary": summary})

    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def main() -> int:
    args = parse_args()
    allure_dir = Path(args.allure_dir)
    output_root = Path(args.output_dir)

    if not allure_dir.exists():
        raise FileNotFoundError(f"Allure directory does not exist: {allure_dir}")

    project_dir = output_root / args.project_id
    runs_dir = project_dir / "runs"
    project_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    totals, suite_totals, started_at, completed_at = aggregate(allure_dir)
    completed_at = completed_at or now_iso()
    started_at = started_at or completed_at

    notes_by_suite = {sid: note for sid, note in expected_suites(args.project_id)}
    suite_ids = set(suite_totals.keys()) | set(notes_by_suite.keys())

    suites: list[dict[str, Any]] = []
    for suite_id in sorted(suite_ids):
        stot = suite_totals.get(suite_id, Totals())
        suites.append(
            {
                "suite_id": suite_id,
                "suite_name": suite_name(suite_id),
                "status": combine_status(stot),
                "totals": stot.to_dict(),
                "notes": notes_by_suite.get(suite_id, ""),
            }
        )

    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    duration_seconds = max(int((completed_dt - started_dt).total_seconds()), 0)

    latest = {
        "run_id": args.run_id,
        "source": {
            "provider": "github-actions",
            "workflow": args.workflow,
            "branch": args.branch,
            "commit_sha": args.commit_sha,
            "run_url": args.run_url,
        },
        "status": overall_status(suites),
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
        "totals": totals,
        "suites": suites,
        "stale_after_days": args.stale_after_days,
    }

    run_snapshot = {
        "run_id": latest["run_id"],
        "status": latest["status"],
        "completed_at": latest["completed_at"],
        "run_url": latest["source"]["run_url"],
    }

    run_file = runs_dir / f"{args.run_id}.json"
    with run_file.open("w", encoding="utf-8") as fh:
        json.dump(run_snapshot, fh, indent=2)
        fh.write("\n")

    history = load_history(runs_dir)
    trimmed = history[: args.history_limit]

    for stale_entry in history[args.history_limit :]:
        stale_name = stale_entry.get("run_id")
        if stale_name:
            stale_file = runs_dir / f"{stale_name}.json"
            if stale_file.exists():
                stale_file.unlink()

    document = {
        "$schema_version": "1.0.0",
        "project": {
            "id": args.project_id,
            "name": args.project_name,
            "frameworks": ["pytest", "selenium", "requests", "allure"],
            "repository_url": args.repo_url,
        },
        "latest": latest,
        "history": trimmed,
        "history_limit": args.history_limit,
        "last_refreshed_at": now_iso(),
    }

    latest_path = project_dir / "latest.json"
    with latest_path.open("w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")

    ensure_project_index(output_root, args.project_id, "Flagship pytest framework with API, web, and unit coverage")

    print(f"run_id={args.run_id} status={latest['status']} output={latest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
