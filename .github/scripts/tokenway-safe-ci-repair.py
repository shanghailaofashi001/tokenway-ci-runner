#!/usr/bin/env python3
"""Conservative, signature-gated repairs for known TokenWay CI workflow defects.

This is not an AI code agent. It only edits one explicitly allowlisted CI workflow
when the failed log contains the exact diagnostic for a previously verified fix.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

TARGET = Path(".github/workflows/tokenway-finance-r7-validation.yml")


def _step_blocks(lines: list[str]):
    headers = []
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>[ ]*)-\s+name:\s*(?P<name>.*?)\s*$", line)
        if match:
            headers.append((index, len(match.group("indent")), match.group("name")))
    for pos, (start, indent, name) in enumerate(headers):
        end = len(lines)
        for next_start, next_indent, _ in headers[pos + 1 :]:
            if next_indent == indent:
                end = next_start
                break
        yield start, end, indent, name


def _normalize_step(block: list[str]) -> str:
    text = "".join(block)
    # Treat escaped newline spelling differences as equivalent in shell printf.
    return re.sub(r"\\+n", r"\\n", text).rstrip()


def repair_text(original: str, failure_log: str) -> tuple[str, list[str]]:
    updated = original
    repairs: list[str] = []

    if "SC1007" in failure_log and re.search(r"\bGOFLAGS=\s+(?=GOTOOLCHAIN=)", updated):
        updated, count = re.subn(
            r"\bGOFLAGS=\s+(?=GOTOOLCHAIN=)",
            "GOFLAGS='' ",
            updated,
        )
        if count:
            repairs.append(f"quoted {count} empty GOFLAGS assignment(s)")

    duplicate_signature = re.search(
        r'step ID ["\']offline_graph["\'] duplicates|id:\s*offline_graph.*duplicates',
        failure_log,
        re.IGNORECASE,
    )
    if duplicate_signature:
        lines = updated.splitlines(keepends=True)
        blocks = [
            (start, end, indent, name)
            for start, end, indent, name in _step_blocks(lines)
            if name.strip() == "Verify offline dependency graph"
            and any(re.match(r"^\s*id:\s*offline_graph\s*$", item) for item in lines[start:end])
        ]
        if len(blocks) > 1 and not re.search(r"steps\.offline_graph\b", updated):
            normalized = [_normalize_step(lines[start:end]) for start, end, _, _ in blocks]
            if all(item == normalized[0] for item in normalized[1:]):
                for start, end, _, _ in reversed(blocks[1:]):
                    del lines[start:end]
                updated = "".join(lines)
                repairs.append(f"removed {len(blocks) - 1} byte-equivalent duplicate offline graph steps")

    return updated, repairs


def self_test() -> None:
    fixture = """jobs:
  validate:
    steps:
      - name: Restore
        run: |
          export GOFLAGS= GOTOOLCHAIN=local
      - name: Verify offline dependency graph
        id: offline_graph
        run: echo graph
      - name: Verify offline dependency graph
        id: offline_graph
        run: echo graph
      - name: Finish
        run: echo done
"""
    log = "SC1007: warning: remove space after =; step ID \"offline_graph\" duplicates"
    result, repairs = repair_text(fixture, log)
    assert "GOFLAGS='' " in result
    assert result.count("id: offline_graph") == 1
    assert result.count("Verify offline dependency graph") == 1
    assert len(repairs) == 2
    print("safe repair self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--log", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.log is None:
        parser.error("--log is required unless --self-test is used")

    failure_log = args.log.read_text(encoding="utf-8", errors="replace")
    target = args.root / TARGET
    if not target.is_file():
        print("No allowlisted target workflow found; no changes made")
        return 0

    original = target.read_text(encoding="utf-8")
    updated, repairs = repair_text(original, failure_log)
    if not repairs:
        print("No exact, allowlisted safe repair matched; no changes made")
        return 0

    target.write_text(updated, encoding="utf-8")
    for repair in repairs:
        print(f"SAFE_REPAIR: {repair}")
    print(f"SAFE_REPAIR_FILE: {TARGET.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
