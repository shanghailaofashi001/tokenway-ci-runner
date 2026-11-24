#!/usr/bin/env python3
"""Create or verify a deterministic digest for the immutable TokenWay source snapshot."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

if len(sys.argv) != 4 or sys.argv[1] not in {"freeze", "verify"}:
    raise SystemExit("usage: tokenway-source-snapshot.py freeze|verify <source-dir> <manifest>")
mode = sys.argv[1]
root = pathlib.Path(sys.argv[2]).resolve()
manifest_path = pathlib.Path(sys.argv[3]).resolve()

def digest_tree():
    digest = hashlib.sha256()
    excluded_dirs = {
        ".git", "node_modules", "dist", "coverage", ".cache", ".vite",
        ".tanstack", ".turbo", ".next", ".output", "tmp", "logs",
    }
    env_examples = {".env.example", ".env.sample", ".env.template"}
    excluded_env = {".env", ".env.local", ".env.production", ".env.development"}
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if any(part in excluded_dirs for part in relative_path.parts[:-1]) or (path.is_dir() and path.name in excluded_dirs):
            continue
        name = path.name
        if name in excluded_env or (name.startswith(".env.") and name not in env_examples):
            continue
        if name == ".DS_Store" or name.endswith(".log"):
            continue
        if path.is_file() and path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            digest.update(b"L" + bytes([0]) + relative.encode("utf-8") + bytes([0]) + target.encode("utf-8") + bytes([10]))
        elif path.is_file():
            file_hash = hashlib.sha256(path.read_bytes()).digest()
            digest.update(b"F" + bytes([0]) + relative.encode("utf-8") + bytes([0]) + file_hash + bytes([10]))
    return digest.hexdigest()

actual = digest_tree()
if mode == "verify":
    expected_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = expected_data["source_tree_sha256"]
    if actual != expected:
        raise SystemExit(f"Source snapshot digest mismatch: expected {expected}, got {actual}")
    print(f"PASS: immutable source snapshot {expected}")
else:
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "source_repository": "QuantumNous/new-api",
        "source_commit": commit,
        "source_tree_sha256": actual,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FROZEN: source commit {commit}, tree SHA-256 {actual}")
