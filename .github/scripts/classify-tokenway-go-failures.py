#!/usr/bin/env python3
"""Classify Root package and nested-module Go failures into targeted rerun selectors."""
import json
import pathlib
import re
import sys

log_dir = pathlib.Path(sys.argv[1])
results = []

def failed_test_names(log_path: pathlib.Path) -> list[str]:
    names: set[str] = set()
    if not log_path.is_file():
        return []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            match = re.match(r"--- FAIL: (Test\w+)", line)
            if match:
                names.add(match.group(1))
            continue
        if event.get("Action") == "fail" and event.get("Test"):
            names.add(event["Test"])
    return sorted(names)

for status_path in sorted(log_dir.glob("*.status")):
    line = status_path.read_text(encoding="utf-8").strip()
    if not line:
        continue
    failed = line.startswith("FAIL:")
    subject = re.sub(r"^(?:PASS|FAIL):\s*", "", line)
    subject = re.sub(r"\s+\(exit \d+\)$", "", subject)

    if subject.endswith(" full test suite"):
        module = subject.removesuffix(" full test suite")
        log_path = log_dir / f"{module}.log"
        tests = failed_test_names(log_path) if failed else []
        selector = {"go_module_scope": module}
        results.append({
            "status": "failed" if failed else "passed",
            "suite": "go-module",
            "module": module,
            "package": None,
            "failed_tests": tests,
            "rerun_selector": selector,
            "log": log_path.name,
        })
        continue

    package = subject
    slug = package.replace("/", "-")
    log_path = log_dir / f"{slug}.jsonl"
    tests = failed_test_names(log_path) if failed else []
    selector = {
        "go_module_scope": "root",
        "root_test_packages": package,
    }
    if tests:
        selector["root_test_pattern"] = "^(" + "|".join(re.escape(name) for name in tests) + ")$"
    results.append({
        "status": "failed" if failed else "passed",
        "suite": "root-package",
        "module": "root",
        "package": package,
        "failed_tests": tests,
        "rerun_selector": selector,
        "log": log_path.name,
    })

failures = [item for item in results if item["status"] == "failed"]
report = {
    "schema_version": 1,
    "overall": "failed" if failures else "passed",
    "failed_count": len(failures),
    "results": results,
    "targeted_rerun": [item["rerun_selector"] for item in failures],
}
output = log_dir / "failure-classification.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Go failure classification: {len(failures)} failed suite(s) / {len(results)} total.")
