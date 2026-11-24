#!/usr/bin/env python3
"""Classify Root package and nested Go module failures into targeted rerun selectors."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def failed_test_names(log_path: Path) -> list[str]:
    if not log_path.is_file():
        return []
    names: set[str] = set()
    with log_path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("Action") != "fail" or not event.get("Test"):
                continue
            # A top-level -run selector reruns all subtests for the failed test.
            names.add(str(event["Test"]).split("/", 1)[0])
    return sorted(names)


def classify(log_dir: Path) -> dict:
    results = []
    for status_path in sorted(log_dir.glob("*.status")):
        line = status_path.read_text(encoding="utf-8").strip()
        match = re.match(r"(PASS|FAIL): (.+?)(?: \(exit \d+\))?$", line)
        if not match:
            results.append({
                "status": "unclassified",
                "status_file": status_path.name,
                "raw_status": line,
            })
            continue

        state, subject = match.groups()
        failed = state == "FAIL"
        if subject.endswith(" full test suite"):
            module = subject.removesuffix(" full test suite")
            selector = {"go_module_scope": module, "targeted_only": True}
            results.append({
                "status": "failed" if failed else "passed",
                "suite": "go-module",
                "module": module,
                "test_set": "full module suite",
                "rerun_selector": selector,
                "status_file": status_path.name,
            })
            continue

        package = subject
        slug = status_path.name.removesuffix(".status")
        names = failed_test_names(log_dir / f"{slug}.jsonl") if failed else []
        selector = {
            "go_module_scope": "root",
            "root_test_packages": package,
            "targeted_only": True,
        }
        if names:
            selector["root_test_pattern"] = "^(" + "|".join(re.escape(name) for name in names) + ")$"
        results.append({
            "status": "failed" if failed else "passed",
            "suite": "root-package",
            "module": "root",
            "package": package,
            "failed_tests": names,
            "test_set": "failed test names" if names else "all tests in package",
            "rerun_selector": selector,
            "status_file": status_path.name,
        })

    failures = [item for item in results if item.get("status") == "failed"]
    return {
        "schema_version": 1,
        "overall": "failed" if failures else ("passed" if results else "unknown"),
        "failed_count": len(failures),
        "results": results,
        "targeted_rerun": [item["rerun_selector"] for item in failures],
    }


def main() -> int:
    log_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tokenway-go-validation-logs")
    if not log_dir.is_dir():
        raise SystemExit(f"Go validation log directory does not exist: {log_dir}")
    report = classify(log_dir)
    report_path = log_dir / "failure-classification.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}: {report['failed_count']} failed suite(s) across {len(report['results'])} result(s).")
    if report["overall"] == "unknown":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
