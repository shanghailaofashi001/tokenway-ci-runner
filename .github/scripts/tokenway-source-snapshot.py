#!/usr/bin/env python3
"""Write or verify the exact source-tree digest used by the release packager."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "coverage", ".cache", ".vite",
    ".tanstack", ".turbo", ".next", ".output", "tmp", "logs",
}
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template"}
EXCLUDED_ENV = {".env", ".env.local", ".env.production", ".env.development"}
DB_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


def excluded(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.relative_to(SOURCE).parts[:-1]):
        return True
    name = path.name
    if name in EXCLUDED_DIRS and path.is_dir():
        return True
    if name in EXCLUDED_ENV:
        return True
    if name.startswith(".env.") and name not in ENV_EXAMPLES:
        return True
    if name == ".DS_Store" or name.endswith(".log"):
        return True
    if path.is_file() and path.suffix.lower() in DB_SUFFIXES:
        return True
    return False


def digest_source() -> str:
    digest = hashlib.sha256()
    for path in sorted(SOURCE.rglob("*")):
        if excluded(path):
            continue
        relative = path.relative_to(SOURCE).as_posix()
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError as exc:
                raise SystemExit(f"Broken symlink in source snapshot: {relative}") from exc
            if not resolved.is_relative_to(SOURCE.resolve()):
                raise SystemExit(f"External symlink in source snapshot: {relative}")
            target = os.readlink(path)
            digest.update(b"L" + bytes([0]) + relative.encode("utf-8") + bytes([0]) + target.encode("utf-8") + bytes([10]))
        elif path.is_file():
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update(b"F" + bytes([0]) + relative.encode("utf-8") + bytes([0]) + bytes.fromhex(file_hash) + bytes([10]))
    return digest.hexdigest()


def main() -> int:
    global SOURCE
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    source_arg = sys.argv[2] if len(sys.argv) > 2 else "source"
    if mode not in {"write", "verify"}:
        raise SystemExit("Usage: tokenway-source-snapshot.py write|verify [source-directory]")
    SOURCE = Path(source_arg).resolve()
    if not SOURCE.is_dir():
        raise SystemExit(f"Source directory does not exist: {SOURCE}")
    digest = digest_source()
    marker = Path(".tokenway-validation/ACCEPTED-SOURCE-TREE.sha256")
    marker.parent.mkdir(parents=True, exist_ok=True)
    if mode == "write":
        marker.write_text(digest + "\n", encoding="utf-8")
        print(f"Frozen TokenWay source tree SHA-256: {digest}")
        return 0
    if not marker.is_file():
        raise SystemExit("Accepted source-tree digest marker is missing.")
    expected = marker.read_text(encoding="utf-8").strip()
    if digest != expected:
        raise SystemExit(f"Source snapshot changed: expected {expected}, found {digest}")
    print(f"PASS: source snapshot unchanged ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
