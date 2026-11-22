#!/usr/bin/env python3
"""Conservative, signature-gated repairs for known TokenWay CI workflow defects.

This is not an AI code agent. It only edits one explicitly allowlisted CI workflow
when the failed log contains the exact diagnostic for a previously verified fix.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
import tempfile

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


def repair_chat_sync_files(root: Path, failure_log: str) -> list[str]:
    """Fix the exact known mismatch between Go field alignment and a literal grep."""
    if "PASS: encrypted chat sync DTO" not in failure_log or "ChatHistorySync string" not in failure_log:
        return []

    repairs: list[str] = []
    script_path = root / ".github/scripts/apply_encrypted_chat_history_sync.py"
    workflow_path = root / ".github/workflows/tokenway-finance-r7-validation.yml"

    if script_path.is_file():
        source = script_path.read_text(encoding="utf-8")
        updated = source
        if "import re\n" not in updated:
            updated = updated.replace("from pathlib import Path\n", "from pathlib import Path\nimport re\n", 1)
        updated = updated.replace(
            'if "ChatHistorySync string" not in dto_source:',
            'if not re.search(r"\\bChatHistorySync\\s+string\\b", dto_source):',
        )
        if updated != source:
            script_path.write_text(updated, encoding="utf-8")
            repairs.append("made ChatHistorySync field detection whitespace-alignment aware")

    if workflow_path.is_file():
        source = workflow_path.read_text(encoding="utf-8")
        old = "grep -Fq 'ChatHistorySync string' relaykit/dto/user_settings.go"
        new = "grep -Eq 'ChatHistorySync[[:space:]]+string' relaykit/dto/user_settings.go"
        if old in source:
            workflow_path.write_text(source.replace(old, new), encoding="utf-8")
            repairs.append("made the ChatHistorySync verification accept gofmt-aligned fields")

    return repairs


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

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / ".github/scripts").mkdir(parents=True)
        (root / ".github/workflows").mkdir(parents=True)
        (root / ".github/scripts/apply_encrypted_chat_history_sync.py").write_text(
            'from pathlib import Path\nif "ChatHistorySync string" not in dto_source:\n',
            encoding="utf-8",
        )
        (root / ".github/workflows/tokenway-finance-r7-validation.yml").write_text(
            "grep -Fq 'ChatHistorySync string' relaykit/dto/user_settings.go\n",
            encoding="utf-8",
        )
        chat_repairs = repair_chat_sync_files(
            root,
            "grep -Fq 'ChatHistorySync string'\nPASS: encrypted chat sync DTO applied",
        )
        assert len(chat_repairs) == 2
        script_text = (root / ".github/scripts/apply_encrypted_chat_history_sync.py").read_text()
        workflow_text = (root / ".github/workflows/tokenway-finance-r7-validation.yml").read_text()
        assert 're.search(r"\\bChatHistorySync\\s+string\\b"' in script_text
        assert "grep -Eq 'ChatHistorySync[[:space:]]+string'" in workflow_text

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
    repairs: list[str] = []
    target = args.root / TARGET
    if target.is_file():
        original = target.read_text(encoding="utf-8")
        updated, workflow_repairs = repair_text(original, failure_log)
        if workflow_repairs:
            target.write_text(updated, encoding="utf-8")
            repairs.extend(workflow_repairs)
            repairs.append(f"updated {TARGET.as_posix()}")

    repairs.extend(repair_chat_sync_files(args.root, failure_log))
    if not repairs:
        print("No exact, allowlisted safe repair matched; no changes made")
        return 0

    for repair in repairs:
        print(f"SAFE_REPAIR: {repair}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
