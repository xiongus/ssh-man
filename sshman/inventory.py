from __future__ import annotations

from pathlib import Path

from sshman.models import InventoryHost, InventoryTunnel


TEMPLATE = """hosts:
  - alias: jump-box
    host: 192.168.78.36
    user: root
    port: 22
    group: default
    note: Primary jump host
    proxy_jump:
    identity_file: ~/.ssh/id_ed25519
    password:
    tunnels:
      - alias: t-s16-8001
        local_port: 18001
        target_host: 100.124.241.16
        target_port: 8001
        bind_address: 127.0.0.1
        note: Service tunnel
"""


class InventoryError(Exception):
    pass


def write_template(path: Path | None = None) -> str:
    if path is not None:
        path.write_text(TEMPLATE, encoding="utf-8")
    return TEMPLATE


def load_inventory(path: Path) -> list[InventoryHost]:
    lines = path.read_text(encoding="utf-8").splitlines()
    raw_hosts = parse_simple_yaml(lines)
    hosts: list[InventoryHost] = []
    for item in raw_hosts.get("hosts", []):
        host = InventoryHost(
            alias=required_str(item, "alias"),
            host=required_str(item, "host"),
            user=required_str(item, "user"),
            port=optional_int(item, "port", 22),
            group=optional_str(item, "group", "default"),
            identity_file=nullable_str(item, "identity_file"),
            note=nullable_str(item, "note"),
            proxy_jump=nullable_str(item, "proxy_jump"),
            password=nullable_str(item, "password"),
            tunnels=[],
        )
        tunnels = item.get("tunnels", [])
        if tunnels is None:
            tunnels = []
        if tunnels and not isinstance(tunnels, list):
            raise InventoryError(f"Host {host.alias} tunnels must be a list.")
        for tunnel_item in tunnels:
            host.tunnels.append(
                InventoryTunnel(
                    alias=required_str(tunnel_item, "alias"),
                    local_port=required_int(tunnel_item, "local_port"),
                    target_host=required_str(tunnel_item, "target_host"),
                    target_port=required_int(tunnel_item, "target_port"),
                    bind_address=optional_str(tunnel_item, "bind_address", "127.0.0.1"),
                    note=nullable_str(tunnel_item, "note"),
                )
            )
        hosts.append(host)
    return hosts


def parse_simple_yaml(lines: list[str]) -> dict:
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]

    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise InventoryError(f"Line {lineno}: list item without list parent.")
            payload = stripped[2:]
            item: dict = {}
            parent.append(item)
            if payload:
                key, value = split_key_value(payload, lineno)
                item[key] = parse_scalar(value)
            stack.append((indent, item))
            continue

        key, value = split_key_value(stripped, lineno)
        if isinstance(parent, list):
            if not parent or not isinstance(parent[-1], dict):
                raise InventoryError(f"Line {lineno}: cannot assign key inside list.")
            target = parent[-1]
        elif isinstance(parent, dict):
            target = parent
        else:
            raise InventoryError(f"Line {lineno}: invalid YAML structure.")

        if value == "":
            next_nonempty = next_nonempty_line(lines, lineno)
            if next_nonempty is not None:
                next_indent, next_text = next_nonempty
                if next_indent > indent and next_text.startswith("- "):
                    target[key] = []
                    stack.append((indent, target[key]))
                elif next_indent > indent:
                    target[key] = {}
                    stack.append((indent, target[key]))
                else:
                    target[key] = None
            else:
                target[key] = None
        else:
            target[key] = parse_scalar(value)
    return root


def next_nonempty_line(lines: list[str], current_lineno: int) -> tuple[int, str] | None:
    for raw in lines[current_lineno:]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        return indent, raw.strip()
    return None


def split_key_value(text: str, lineno: int) -> tuple[str, str]:
    if ":" not in text:
        raise InventoryError(f"Line {lineno}: expected key: value.")
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def parse_scalar(value: str):
    if value in {"null", "Null", "NULL", "~", ""}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.isdigit():
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def required_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise InventoryError(f"Missing required string field: {key}")
    return value


def optional_str(data: dict, key: str, default: str) -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise InventoryError(f"Field {key} must be a string.")
    return value


def nullable_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryError(f"Field {key} must be a string or null.")
    return value


def required_int(data: dict, key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise InventoryError(f"Field {key} must be an integer.")
    return value


def optional_int(data: dict, key: str, default: int) -> int:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, int):
        raise InventoryError(f"Field {key} must be an integer.")
    return value
