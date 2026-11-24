#!/usr/bin/env python3
"""Regression tests for exact Root and nested-module Go failure routing."""
import json
import pathlib
import subprocess
import sys
import tempfile

classifier = pathlib.Path(__file__).with_name("classify-tokenway-go-failures.py")

with tempfile.TemporaryDirectory(prefix="tokenway-failure-routing-") as temporary:
    root = pathlib.Path(temporary)
    relaykit_package = "github.com/QuantumNous/new-api/relaykit/relayconvert"
    relaykit_build_failure = "github.com/QuantumNous/new-api/relaykit/types"
    root_package = "github.com/QuantumNous/new-api/controller"

    (root / "relaykit.status").write_text(
        "FAIL: relaykit full test suite (exit 1)\n", encoding="utf-8"
    )
    events = [
        {"Action": "fail", "Package": relaykit_package, "Test": "TestDecodeBroken"},
        {"Action": "fail", "Package": relaykit_package},
        {"Action": "fail", "Package": relaykit_build_failure},
    ]
    (root / "relaykit.log").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    root_slug = root_package.replace("/", "-")
    (root / f"{root_slug}.status").write_text(
        f"FAIL: {root_package} (exit 1)\n", encoding="utf-8"
    )
    (root / f"{root_slug}.jsonl").write_text(
        json.dumps({
            "Action": "fail",
            "Package": root_package,
            "Test": "TestRootRegression",
        }) + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(classifier), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads((root / "failure-classification.json").read_text(encoding="utf-8"))
    if report["overall"] != "failed" or report["failed_count"] != 3:
        raise SystemExit(f"unexpected classification summary: {report}")

    results = {(item["module"], item["package"]): item for item in report["results"]}
    first = results[("relaykit", relaykit_package)]
    if first["failed_tests"] != ["TestDecodeBroken"]:
        raise SystemExit(f"nested-module test name was not captured: {first}")
    expected = {
        "go_module_scope": "relaykit",
        "nested_module_packages": relaykit_package,
        "nested_module_test_pattern": "^(?:TestDecodeBroken)$",
    }
    if first["rerun_selector"] != expected:
        raise SystemExit(f"nested-module rerun selector is incorrect: {first['rerun_selector']}")

    build_failure = results[("relaykit", relaykit_build_failure)]
    if build_failure["failed_tests"] or build_failure["rerun_selector"] != {
        "go_module_scope": "relaykit",
        "nested_module_packages": relaykit_build_failure,
    }:
        raise SystemExit(f"package-level build failure routing is incorrect: {build_failure}")

    root_result = results[("root", root_package)]
    if root_result["failed_tests"] != ["TestRootRegression"]:
        raise SystemExit(f"Root test routing regressed: {root_result}")
    if root_result["rerun_selector"].get("root_test_pattern") != "^(?:TestRootRegression)$":
        raise SystemExit(f"Root targeted rerun selector is incorrect: {root_result['rerun_selector']}")

print("PASS: Root and nested Go failures map to exact module, package, failed test and targeted rerun selectors.")
