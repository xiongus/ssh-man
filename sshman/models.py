from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HostEntry:
    alias: str
    hostname: str
    user: str
    port: int = 22
    group: str = "default"
    identity_file: str | None = None
    note: str | None = None
    proxy_jump: str | None = None


@dataclass
class TunnelEntry:
    alias: str
    via: str
    local_port: int
    target_host: str
    target_port: int
    bind_address: str = "127.0.0.1"
    note: str | None = None


@dataclass
class InventoryHost:
    alias: str
    host: str
    user: str
    port: int = 22
    group: str = "default"
    identity_file: str | None = None
    note: str | None = None
    proxy_jump: str | None = None
    password: str | None = None
    default_tunnels: list[str] = field(default_factory=list)
    tunnels: list["InventoryTunnel"] = field(default_factory=list)


@dataclass
class InventoryTunnel:
    alias: str
    local_port: int
    target_host: str
    target_port: int
    bind_address: str = "127.0.0.1"
    note: str | None = None
