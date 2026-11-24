#!/usr/bin/env python3
"""Check critical TokenWay frontend/backend route and task-protocol contracts."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else "source").resolve()


def require_file(relative: str) -> Path:
    path = SOURCE / relative
    if not path.is_file():
        raise SystemExit(f"API contract source file is missing: {relative}")
    return path


def normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", ":param", path)
    path = re.sub(r"\{([^}]+)\}", r":\1", path)
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/") or "/"


def join_path(prefix: str, path: str) -> str:
    return normalize_path((prefix.rstrip("/") + "/" + path.lstrip("/")) or "/")


def collect_router_routes() -> set[tuple[str, str]]:
    route_files = (
        "router/api-router.go",
        "router/task-router.go",
        "router/relay-router.go",
    )
    routes: set[tuple[str, str]] = set()
    group_pattern = re.compile(r"(\w+)\s*:=\s*(\w+)\.Group\(\s*\"([^\"]*)\"\s*\)")
    route_pattern = re.compile(r"(\w+)\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\(\s*\"([^\"]*)\"")
    for relative in route_files:
        path = require_file(relative)
        prefixes = {"router": ""}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("//"):
                continue
            group = group_pattern.search(line)
            if group:
                variable, parent, segment = group.groups()
                if parent in prefixes:
                    prefixes[variable] = join_path(prefixes[parent], segment)
            route = route_pattern.search(line)
            if route:
                receiver, method, endpoint = route.groups()
                if receiver in prefixes:
                    routes.add((method, join_path(prefixes[receiver], endpoint)))
    return routes


def collect_frontend_wallet_routes() -> set[tuple[str, str]]:
    path = require_file("web/src/features/wallet/api.ts")
    pattern = re.compile(r"\bapi\.(get|post|put|patch|delete)\s*\(\s*(['\"\x60])([^'\"\x60]+)\2", re.IGNORECASE)
    routes = {
        (method.upper(), normalize_path(endpoint))
        for method, _quote, endpoint in pattern.findall(path.read_text(encoding="utf-8"))
    }
    if not routes:
        raise SystemExit("No frontend wallet API calls were discovered.")
    return routes


def collect_frontend_protocols() -> set[tuple[str, str, str]]:
    path = require_file("web/src/features/task-plugins/lib/host-protocols.ts")
    current_protocol = ""
    endpoints: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        protocol = re.match(r"\s{2}([a-z0-9_]+):\s*\[$", line)
        if protocol:
            current_protocol = protocol.group(1)
            continue
        endpoint = re.search(r"method:\s*'([A-Z]+)'\s*,\s*path:\s*'([^']+)'", line)
        if endpoint and current_protocol:
            method, route = endpoint.groups()
            endpoints.add((current_protocol, method, normalize_path(route)))
    if not endpoints:
        raise SystemExit("No frontend task-plugin host protocol endpoints were discovered.")
    return endpoints


def collect_backend_protocols() -> set[tuple[str, str, str]]:
    path = require_file("pkg/jsplugin/routing.go")
    content = path.read_text(encoding="utf-8")
    constants = dict(re.findall(r"const\s+(Protocol[A-Za-z0-9_]+)\s*=\s*\"([^\"]+)\"", content))
    current_protocol = ""
    endpoints: set[tuple[str, str, str]] = set()
    protocol_literal = re.compile(r"\{Name:\s*\"([^\"]+)\",\s*Operations:\s*\[\]HostProtocolOperation\{")
    protocol_constant = re.compile(r"\{Name:\s*(Protocol[A-Za-z0-9_]+),\s*Operations:\s*\[\]HostProtocolOperation\{")
    operation = re.compile(r"\{Name:\s*\"[^\"]+\",\s*Methods:\s*\[\]string\{([^}]+)\},\s*Path:\s*\"([^\"]+)\"")
    for line in content.splitlines():
        match = protocol_literal.search(line)
        if match:
            current_protocol = match.group(1)
        else:
            match = protocol_constant.search(line)
            if match:
                current_protocol = constants.get(match.group(1), "")
        match = operation.search(line)
        if match and current_protocol:
            methods, route = match.groups()
            for method in re.findall(r"http\.Method([A-Za-z]+)", methods):
                endpoints.add((current_protocol, method.upper(), normalize_path(route)))
        if line.strip() == "}},":
            current_protocol = ""
    if not endpoints:
        raise SystemExit("No backend task-plugin host protocol endpoints were discovered.")
    return endpoints


def main() -> int:
    backend_routes = collect_router_routes()
    frontend_wallet = collect_frontend_wallet_routes()
    missing_wallet = sorted(frontend_wallet - backend_routes)
    required_server_routes = {
        ("POST", "/api/user/epay/notify"),
        ("GET", "/api/user/epay/notify"),
        ("POST", "/v1/tasks/:key"),
        ("GET", "/v1/tasks/:key"),
        ("GET", "/v1/tasks/:key/artifacts"),
        ("GET", "/v1/tasks/:key/artifacts/:artifact_key/content"),
        ("GET", "/v1/responses"),
    }
    missing_server = sorted(required_server_routes - backend_routes)

    frontend_protocols = collect_frontend_protocols()
    backend_protocols = collect_backend_protocols()
    missing_protocols = sorted(frontend_protocols - backend_protocols)

    main_router = require_file("router/main.go").read_text(encoding="utf-8")
    if "SetTaskPluginProtocolRouter(router)" not in main_router:
        raise SystemExit("Backend router does not register the task-plugin host protocol routes.")

    if missing_wallet or missing_server or missing_protocols:
        if missing_wallet:
            print("Frontend wallet calls missing from backend routes:")
            for method, path in missing_wallet:
                print(f"  {method} {path}")
        if missing_server:
            print("Required server routes missing:")
            for method, path in missing_server:
                print(f"  {method} {path}")
        if missing_protocols:
            print("Frontend task-protocol endpoints missing from backend host protocol registry:")
            for protocol, method, path in missing_protocols:
                print(f"  {protocol} {method} {path}")
        return 1

    print(f"PASS: {len(frontend_wallet)} frontend wallet endpoints match backend route registrations.")
    print(f"PASS: {len(frontend_protocols)} task-plugin protocol endpoints match backend host protocol definitions.")
    print(f"PASS: EPay callback, task lifecycle/artifact routes, and Responses WebSocket route are registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
