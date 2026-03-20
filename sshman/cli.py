from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SSH_DIR = Path.home() / ".ssh"
CONFIG_PATH = SSH_DIR / "config"
CONFIG_D_DIR = SSH_DIR / "config.d"
HOSTS_PATH = CONFIG_D_DIR / "hosts.conf"
TUNNELS_PATH = CONFIG_D_DIR / "tunnels.conf"
BACKUPS_DIR = SSH_DIR / "backups"
DEFAULT_IDENTITY = "~/.ssh/id_ed25519"
MANAGED_HEADER = "# Managed by sshman. Manual edits are allowed.\n"
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class SSHManError(Exception):
    pass


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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if not getattr(args, "command", None):
            parser.print_help()
            return
        args.func(args)
    except SSHManError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sshman",
        description="Offline SSH config and tunnel manager.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize ~/.ssh/config structure.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite managed files.")
    init_parser.set_defaults(func=cmd_init)

    host_parser = subparsers.add_parser("add-host", help="Add a host entry.")
    host_parser.add_argument("--alias", required=True)
    host_parser.add_argument("--host", required=True)
    host_parser.add_argument("--user", required=True)
    host_parser.add_argument("--port", type=int, default=22)
    host_parser.add_argument("--group", default="default")
    host_parser.add_argument("--identity-file")
    host_parser.add_argument("--note")
    host_parser.add_argument("--proxy-jump")
    host_parser.set_defaults(func=cmd_add_host)

    tunnel_parser = subparsers.add_parser("add-tunnel", help="Add a tunnel entry.")
    tunnel_parser.add_argument("--alias", required=True)
    tunnel_parser.add_argument("--via", required=True, help="Existing SSH host alias.")
    tunnel_parser.add_argument("--local-port", required=True, type=int)
    tunnel_parser.add_argument("--target-host", required=True)
    tunnel_parser.add_argument("--target-port", required=True, type=int)
    tunnel_parser.add_argument("--bind-address", default="127.0.0.1")
    tunnel_parser.add_argument("--note")
    tunnel_parser.set_defaults(func=cmd_add_tunnel)

    list_parser = subparsers.add_parser("list", help="List managed entries.")
    list_parser.add_argument(
        "--type",
        choices=("all", "host", "tunnel"),
        default="all",
        help="Filter by entry type.",
    )
    list_parser.add_argument(
        "--simple",
        action="store_true",
        help="Print aliases only for easy piping.",
    )
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show one entry and usage.")
    show_parser.add_argument("alias")
    show_parser.set_defaults(func=cmd_show)

    connect_parser = subparsers.add_parser("connect", help="Connect to a managed host.")
    connect_parser.add_argument("alias", nargs="?")
    connect_parser.set_defaults(func=cmd_connect)

    tunnel_run_parser = subparsers.add_parser("tunnel", help="Start a managed SSH tunnel.")
    tunnel_run_parser.add_argument("alias", nargs="?")
    tunnel_run_parser.set_defaults(func=cmd_tunnel)

    copy_to_parser = subparsers.add_parser("copy-to", help="Copy a file or directory to a managed host.")
    copy_to_parser.add_argument("alias")
    copy_to_parser.add_argument("local_path")
    copy_to_parser.add_argument("remote_path")
    copy_to_parser.add_argument("-r", "--recursive", action="store_true", help="Copy directories recursively.")
    copy_to_parser.set_defaults(func=cmd_copy_to)

    copy_from_parser = subparsers.add_parser("copy-from", help="Copy a file or directory from a managed host.")
    copy_from_parser.add_argument("alias")
    copy_from_parser.add_argument("remote_path")
    copy_from_parser.add_argument("local_path")
    copy_from_parser.add_argument("-r", "--recursive", action="store_true", help="Copy directories recursively.")
    copy_from_parser.set_defaults(func=cmd_copy_from)

    exec_parser = subparsers.add_parser("exec", help="Run one command on a managed host.")
    exec_parser.add_argument("alias")
    exec_parser.add_argument("command")
    exec_parser.set_defaults(func=cmd_exec)

    remove_parser = subparsers.add_parser("remove", help="Remove a managed host or tunnel entry.")
    remove_parser.add_argument("alias")
    remove_parser.set_defaults(func=cmd_remove)

    check_parser = subparsers.add_parser("check", help="Run environment and config checks.")
    check_parser.set_defaults(func=cmd_check)

    backup_parser = subparsers.add_parser("backup", help="Create a timestamped ~/.ssh backup.")
    backup_parser.set_defaults(func=cmd_backup)

    import_parser = subparsers.add_parser("import-csv", help="Import hosts or tunnels from CSV.")
    import_parser.add_argument("--type", required=True, choices=("host", "tunnel"))
    import_parser.add_argument("--file", required=True)
    import_parser.set_defaults(func=cmd_import_csv)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-key",
        help="Deploy your public key to a remote host for passwordless SSH.",
    )
    add_connection_args(bootstrap_parser)
    bootstrap_parser.add_argument(
        "--identity-file",
        default=DEFAULT_IDENTITY,
        help="Private key path to use or create if missing.",
    )
    bootstrap_parser.add_argument(
        "--comment",
        help="SSH key comment when generating a new key.",
    )
    bootstrap_parser.set_defaults(func=cmd_bootstrap_key)

    onboard_parser = subparsers.add_parser(
        "onboard-host",
        help="Bootstrap key auth and then add a managed host entry.",
    )
    add_connection_args(onboard_parser)
    onboard_parser.add_argument("--group", default="default")
    onboard_parser.add_argument("--identity-file", default=DEFAULT_IDENTITY)
    onboard_parser.add_argument("--comment")
    onboard_parser.add_argument("--note")
    onboard_parser.add_argument("--proxy-jump")
    onboard_parser.set_defaults(func=cmd_onboard_host)

    return parser


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--alias", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--port", type=int, default=22)


def cmd_init(args: argparse.Namespace) -> None:
    ensure_ssh_dirs()
    if CONFIG_PATH.exists() and not args.force and "Managed by sshman" not in CONFIG_PATH.read_text():
        raise SSHManError(
            f"{CONFIG_PATH} already exists and is not managed by sshman. Back it up or pass --force."
        )

    write_file(CONFIG_PATH, render_main_config())
    ensure_managed_file(HOSTS_PATH, args.force)
    ensure_managed_file(TUNNELS_PATH, args.force)
    ensure_permissions()
    print(f"Initialized SSH config in {SSH_DIR}")


def cmd_add_host(args: argparse.Namespace) -> None:
    ensure_initialized()
    validate_alias(args.alias)
    validate_port(args.port)
    hosts, tunnels = load_entries()
    ensure_unique_alias(args.alias, hosts, tunnels)

    entry = HostEntry(
        alias=args.alias,
        hostname=args.host,
        user=args.user,
        port=args.port,
        group=args.group,
        identity_file=args.identity_file,
        note=args.note,
        proxy_jump=args.proxy_jump,
    )
    backup_paths()
    append_entry(HOSTS_PATH, render_host_entry(entry))
    print(f"Added host {entry.alias}")
    maybe_validate_alias(entry.alias)


def cmd_add_tunnel(args: argparse.Namespace) -> None:
    ensure_initialized()
    validate_alias(args.alias)
    validate_port(args.local_port)
    validate_port(args.target_port)
    hosts, tunnels = load_entries()
    ensure_unique_alias(args.alias, hosts, tunnels)
    via_host = get_host_by_alias(hosts, args.via)
    if via_host is None:
        raise SSHManError(f"Tunnel via host {args.via!r} does not exist.")
    ensure_unique_local_port(args.local_port, tunnels)

    entry = TunnelEntry(
        alias=args.alias,
        via=args.via,
        local_port=args.local_port,
        target_host=args.target_host,
        target_port=args.target_port,
        bind_address=args.bind_address,
        note=args.note,
    )
    backup_paths()
    append_entry(TUNNELS_PATH, render_tunnel_entry(entry, via_host))
    print(f"Added tunnel {entry.alias}")
    maybe_validate_alias(entry.alias)


def cmd_list(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()

    if args.simple:
        if args.type in ("all", "host"):
            for host in hosts:
                print(host.alias)
        if args.type in ("all", "tunnel"):
            for tunnel in tunnels:
                print(tunnel.alias)
        return

    if args.type in ("all", "host"):
        print("Hosts")
        for host in hosts:
            note = f" [{host.note}]" if host.note else ""
            print(f"  {host.alias:16} {host.user}@{host.hostname}:{host.port}{note}")

    if args.type == "all":
        print()

    if args.type in ("all", "tunnel"):
        print("Tunnels")
        for tunnel in tunnels:
            note = f" [{tunnel.note}]" if tunnel.note else ""
            target = f"{tunnel.target_host}:{tunnel.target_port}"
            print(f"  {tunnel.alias:16} {tunnel.bind_address}:{tunnel.local_port} -> {target} via {tunnel.via}{note}")


def cmd_show(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()
    for host in hosts:
        if host.alias == args.alias:
            print(render_host_entry(host).strip())
            print()
            print(f"Usage: ssh {host.alias}")
            return
    for tunnel in tunnels:
        if tunnel.alias == args.alias:
            via_host = get_host_by_alias(hosts, tunnel.via)
            print(render_tunnel_entry(tunnel, via_host).strip())
            print()
            print(f"Usage: ssh -N {tunnel.alias}")
            return
    raise SSHManError(f"Alias {args.alias!r} not found.")


def cmd_connect(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    if not hosts:
        raise SSHManError("No managed hosts found.")
    alias = args.alias or choose_alias(hosts, "host")
    if get_host_by_alias(hosts, alias) is None:
        raise SSHManError(f"Host alias {alias!r} not found.")
    run_interactive_command(["ssh", alias])


def cmd_tunnel(args: argparse.Namespace) -> None:
    ensure_initialized()
    _, tunnels = load_entries()
    if not tunnels:
        raise SSHManError("No managed tunnels found.")
    alias = args.alias or choose_alias(tunnels, "tunnel")
    if get_tunnel_by_alias(tunnels, alias) is None:
        raise SSHManError(f"Tunnel alias {alias!r} not found.")
    run_interactive_command(["ssh", "-N", alias])


def cmd_copy_to(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    host = get_host_by_alias(hosts, args.alias)
    if host is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    local_path = Path(args.local_path).expanduser()
    if not local_path.exists():
        raise SSHManError(f"Local path not found: {local_path}")
    if local_path.is_dir() and not args.recursive:
        raise SSHManError("Local path is a directory. Re-run with --recursive.")
    run_interactive_command(build_scp_command(host, args.recursive, str(local_path), f"{args.alias}:{args.remote_path}"))


def cmd_copy_from(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    host = get_host_by_alias(hosts, args.alias)
    if host is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    local_target = Path(args.local_path).expanduser()
    local_parent = local_target.parent if local_target.name else local_target
    if not local_parent.exists():
        raise SSHManError(f"Local destination directory does not exist: {local_parent}")
    run_interactive_command(build_scp_command(host, args.recursive, f"{args.alias}:{args.remote_path}", str(local_target)))


def cmd_exec(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    if get_host_by_alias(hosts, args.alias) is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    run_interactive_command(["ssh", args.alias, args.command])


def cmd_remove(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()
    host = get_host_by_alias(hosts, args.alias)
    if host is not None:
        dependent_tunnels = [tunnel.alias for tunnel in tunnels if tunnel.via == args.alias]
        if dependent_tunnels:
            raise SSHManError(
                "Cannot remove host with dependent tunnels: " + ", ".join(dependent_tunnels)
            )
        remove_entry_from_file(HOSTS_PATH, args.alias)
        print(f"Removed host {args.alias}")
        return
    tunnel = get_tunnel_by_alias(tunnels, args.alias)
    if tunnel is not None:
        remove_entry_from_file(TUNNELS_PATH, args.alias)
        print(f"Removed tunnel {args.alias}")
        return
    raise SSHManError(f"Alias {args.alias!r} not found.")


def cmd_check(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()
    issues: list[str] = []
    warnings: list[str] = []

    if not CONFIG_PATH.exists():
        issues.append(f"Missing {CONFIG_PATH}")
    if CONFIG_PATH.exists() and "Include ~/.ssh/config.d/*.conf" not in CONFIG_PATH.read_text():
        issues.append("Main config does not include ~/.ssh/config.d/*.conf")

    aliases = [entry.alias for entry in hosts] + [entry.alias for entry in tunnels]
    duplicate_aliases = sorted({alias for alias in aliases if aliases.count(alias) > 1})
    if duplicate_aliases:
        issues.append(f"Duplicate aliases: {', '.join(duplicate_aliases)}")

    tunnel_ports = [entry.local_port for entry in tunnels]
    duplicate_ports = sorted({str(port) for port in tunnel_ports if tunnel_ports.count(port) > 1})
    if duplicate_ports:
        issues.append(f"Duplicate tunnel local ports: {', '.join(duplicate_ports)}")

    for host in hosts:
        if host.identity_file:
            identity = Path(os.path.expanduser(host.identity_file))
            if not identity.exists():
                issues.append(f"Missing identity file for {host.alias}: {host.identity_file}")

    config_perm = mode_string(CONFIG_PATH)
    if config_perm and config_perm not in {"600", "644"}:
        issues.append(f"Unexpected config permission on {CONFIG_PATH}: {config_perm}")

    ssh_perm = mode_string(SSH_DIR)
    if ssh_perm and ssh_perm != "700":
        issues.append(f"Unexpected SSH dir permission on {SSH_DIR}: {ssh_perm}")

    ssh_check = run_command(["ssh", "-G", "localhost"])
    if ssh_check.returncode != 0:
        issues.append("ssh command is not available or failed to inspect config.")

    agent_keys = run_command(["ssh-add", "-l"])
    if agent_keys.returncode != 0:
        warnings.append("ssh-agent has no loaded identities or is unavailable.")

    print("Check results")
    if issues:
        for issue in issues:
            print(f"  - {issue}")
        for warning in warnings:
            print(f"  - Warning: {warning}")
        raise SSHManError("One or more checks failed.")

    print("  - OK: SSH directory permissions look reasonable")
    print("  - OK: No duplicate aliases or tunnel ports")
    print("  - OK: ssh config structure is valid enough for basic use")
    for warning in warnings:
        print(f"  - Warning: {warning}")


def cmd_backup(args: argparse.Namespace) -> None:
    ensure_ssh_dirs()
    backup_dir = backup_paths()
    print(f"Backup created at {backup_dir}")


def cmd_import_csv(args: argparse.Namespace) -> None:
    ensure_initialized()
    path = Path(args.file).expanduser()
    if not path.exists():
        raise SSHManError(f"CSV file not found: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        print("No rows imported.")
        return

    imported = 0
    for row in rows:
        row = {key: (value or "").strip() for key, value in row.items()}
        if args.type == "host":
            namespace = argparse.Namespace(
                alias=row["alias"],
                host=row["host"],
                user=row["user"],
                port=int(row["port"] or 22),
                group=row.get("group") or "default",
                identity_file=row.get("identity_file") or None,
                note=row.get("note") or None,
                proxy_jump=row.get("proxy_jump") or None,
            )
            cmd_add_host(namespace)
        else:
            namespace = argparse.Namespace(
                alias=row["alias"],
                via=row["via"],
                local_port=int(row["local_port"]),
                target_host=row["target_host"],
                target_port=int(row["target_port"]),
                bind_address=row.get("bind_address") or "127.0.0.1",
                note=row.get("note") or None,
            )
            cmd_add_tunnel(namespace)
        imported += 1
    print(f"Imported {imported} {args.type} entries from {path}")


def cmd_bootstrap_key(args: argparse.Namespace) -> None:
    validate_alias(args.alias)
    validate_port(args.port)
    identity_path = ensure_local_key(args.identity_file, args.comment)
    public_key_path = identity_path.with_suffix(identity_path.suffix + ".pub") if identity_path.suffix else Path(f"{identity_path}.pub")
    if not public_key_path.exists():
        raise SSHManError(f"Public key not found: {public_key_path}")

    print(f"Deploying {public_key_path} to {args.user}@{args.host}:{args.port}")
    deploy_public_key(
        hostname=args.host,
        user=args.user,
        port=args.port,
        public_key_path=public_key_path,
        proxy_jump=None,
    )
    verify_key_login(
        hostname=args.host,
        user=args.user,
        port=args.port,
        identity_file=identity_path,
        proxy_jump=None,
    )
    print("Key bootstrap completed.")


def cmd_onboard_host(args: argparse.Namespace) -> None:
    ensure_initialized()
    validate_alias(args.alias)
    validate_port(args.port)
    identity_path = ensure_local_key(args.identity_file, args.comment)
    public_key_path = identity_path.with_suffix(identity_path.suffix + ".pub") if identity_path.suffix else Path(f"{identity_path}.pub")
    if not public_key_path.exists():
        raise SSHManError(f"Public key not found: {public_key_path}")

    hosts, tunnels = load_entries()
    ensure_unique_alias(args.alias, hosts, tunnels)

    print(f"Bootstrapping key auth for {args.user}@{args.host}:{args.port}")
    deploy_public_key(
        hostname=args.host,
        user=args.user,
        port=args.port,
        public_key_path=public_key_path,
        proxy_jump=args.proxy_jump,
    )
    verify_key_login(
        hostname=args.host,
        user=args.user,
        port=args.port,
        identity_file=identity_path,
        proxy_jump=args.proxy_jump,
    )

    entry = HostEntry(
        alias=args.alias,
        hostname=args.host,
        user=args.user,
        port=args.port,
        group=args.group,
        identity_file=args.identity_file,
        note=args.note,
        proxy_jump=args.proxy_jump,
    )
    backup_paths()
    append_entry(HOSTS_PATH, render_host_entry(entry))
    print(f"Onboarded host {entry.alias}")
    maybe_validate_alias(entry.alias)


def ensure_initialized() -> None:
    if not CONFIG_PATH.exists() or not HOSTS_PATH.exists() or not TUNNELS_PATH.exists():
        raise SSHManError("sshman is not initialized. Run `sshman init` first.")


def ensure_ssh_dirs() -> None:
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG_D_DIR.mkdir(mode=0o700, exist_ok=True)
    BACKUPS_DIR.mkdir(mode=0o700, exist_ok=True)


def ensure_permissions() -> None:
    os.chmod(SSH_DIR, 0o700)
    for path in (CONFIG_PATH, HOSTS_PATH, TUNNELS_PATH):
        if path.exists():
            os.chmod(path, 0o600)


def ensure_managed_file(path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        current = path.read_text()
        if "Managed by sshman" not in current:
            raise SSHManError(f"{path} already exists and is not managed by sshman.")
    write_file(path, MANAGED_HEADER)


def render_main_config() -> str:
    return (
        MANAGED_HEADER
        + "Host *\n"
        + "  ServerAliveInterval 60\n"
        + "  ServerAliveCountMax 3\n"
        + "  TCPKeepAlive yes\n"
        + "  AddKeysToAgent yes\n"
        + "  UseKeychain yes\n"
        + f"  IdentityFile {DEFAULT_IDENTITY}\n"
        + "\n"
        + "Include ~/.ssh/config.d/*.conf\n"
    )


def load_entries() -> tuple[list[HostEntry], list[TunnelEntry]]:
    return parse_hosts(HOSTS_PATH), parse_tunnels(TUNNELS_PATH)


def parse_hosts(path: Path) -> list[HostEntry]:
    return [entry for entry in parse_config_blocks(path) if isinstance(entry, HostEntry)]


def parse_tunnels(path: Path) -> list[TunnelEntry]:
    return [entry for entry in parse_config_blocks(path) if isinstance(entry, TunnelEntry)]


def parse_config_blocks(path: Path) -> list[HostEntry | TunnelEntry]:
    if not path.exists():
        return []
    blocks: list[tuple[list[str], list[str]]] = []
    current: list[str] = []
    comments: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
        if not line:
            if current:
                blocks.append((comments, current))
                current = []
                comments = []
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        current.append(line)
    if current:
        blocks.append((comments, current))

    parsed: list[HostEntry | TunnelEntry] = []
    for comments, block in blocks:
        if not block or not block[0].startswith("Host "):
            continue
        alias = block[0].split(maxsplit=1)[1].strip()
        kv: dict[str, str] = {}
        for line in block[1:]:
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                kv[parts[0]] = parts[1]
        metadata = parse_metadata_comments(comments)
        if "LocalForward" in kv:
            local_left, local_port, target_host, target_port = parse_local_forward(kv["LocalForward"])
            parsed.append(
                TunnelEntry(
                    alias=alias,
                    via=metadata.get("via", ""),
                    local_port=int(local_port),
                    target_host=target_host,
                    target_port=int(target_port),
                    bind_address=local_left,
                    note=metadata.get("note") or None,
                )
            )
        elif "HostName" in kv and "User" in kv:
            parsed.append(
                HostEntry(
                    alias=alias,
                    hostname=kv["HostName"],
                    user=kv["User"],
                    port=int(kv.get("Port", "22")),
                    group=metadata.get("group") or "default",
                    identity_file=kv.get("IdentityFile"),
                    note=metadata.get("note") or None,
                    proxy_jump=kv.get("ProxyJump"),
                )
            )
    return parsed


def parse_local_forward(value: str) -> tuple[str, str, str, str]:
    left, right = value.split(maxsplit=1)
    if ":" in left:
        bind_address, local_port = left.rsplit(":", 1)
    else:
        bind_address, local_port = "127.0.0.1", left
    target_host, target_port = right.rsplit(":", 1)
    return bind_address, local_port, target_host, target_port


def render_host_entry(entry: HostEntry) -> str:
    lines = [format_metadata_comment(kind="host", group=entry.group, note=entry.note)]
    lines.extend(
        [
            f"Host {entry.alias}",
            f"  HostName {entry.hostname}",
            f"  User {entry.user}",
            f"  Port {entry.port}",
        ]
    )
    if entry.identity_file:
        lines.append(f"  IdentityFile {entry.identity_file}")
    if entry.proxy_jump:
        lines.append(f"  ProxyJump {entry.proxy_jump}")
    lines.append("")
    return "\n".join(lines)


def render_tunnel_entry(entry: TunnelEntry, via_host: HostEntry | None) -> str:
    local = f"{entry.bind_address}:{entry.local_port}" if entry.bind_address else str(entry.local_port)
    if via_host is None:
        raise SSHManError(f"Tunnel {entry.alias} references missing host {entry.via!r}.")
    lines = [format_metadata_comment(kind="tunnel", via=entry.via, note=entry.note)]
    lines.extend(
        [
            f"Host {entry.alias}",
            f"  HostName {via_host.hostname}",
            f"  User {via_host.user}",
            f"  Port {via_host.port}",
            "  RequestTTY no",
            "  ExitOnForwardFailure yes",
            f"  LocalForward {local} {entry.target_host}:{entry.target_port}",
        ]
    )
    if via_host.identity_file:
        lines.append(f"  IdentityFile {via_host.identity_file}")
    if via_host.proxy_jump:
        lines.append(f"  ProxyJump {via_host.proxy_jump}")
    lines.append("")
    return "\n".join(lines)


def append_entry(path: Path, block: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        if path.stat().st_size > 0 and not path.read_text().endswith("\n"):
            handle.write("\n")
        handle.write(block)


def remove_entry_from_file(path: Path, alias: str) -> None:
    backup_paths()
    blocks = split_config_blocks(path)
    kept: list[tuple[list[str], list[str]]] = []
    removed = False
    for comments, lines in blocks:
        if lines and lines[0].startswith("Host ") and lines[0].split(maxsplit=1)[1].strip() == alias:
            removed = True
            continue
        kept.append((comments, lines))
    if not removed:
        raise SSHManError(f"Alias {alias!r} not found in {path}.")
    content = MANAGED_HEADER
    for comments, lines in kept:
        if comments:
            content += "\n".join(comments) + "\n"
        if lines:
            content += "\n".join(lines) + "\n"
        content += "\n"
    write_file(path, content.rstrip() + "\n")


def parse_metadata_comments(comments: Iterable[str]) -> dict[str, str]:
    for comment in comments:
        stripped = comment.strip()
        if not stripped.startswith("# sshman:"):
            continue
        payload = stripped.removeprefix("# sshman:").strip()
        metadata: dict[str, str] = {}
        for token in shlex.split(payload):
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            metadata[key] = value
        return metadata
    return {}


def format_metadata_comment(**values: str | None) -> str:
    parts = []
    for key, value in values.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={shlex.quote(str(value))}")
    return "# sshman: " + " ".join(parts)


def get_host_by_alias(hosts: Iterable[HostEntry], alias: str) -> HostEntry | None:
    for host in hosts:
        if host.alias == alias:
            return host
    return None


def get_tunnel_by_alias(tunnels: Iterable[TunnelEntry], alias: str) -> TunnelEntry | None:
    for tunnel in tunnels:
        if tunnel.alias == alias:
            return tunnel
    return None


def build_scp_command(host: HostEntry, recursive: bool, source: str, destination: str) -> list[str]:
    command = ["scp"]
    if recursive:
        command.append("-r")
    if host.port != 22:
        command.extend(["-P", str(host.port)])
    if host.identity_file:
        command.extend(["-i", os.path.expanduser(host.identity_file)])
    if host.proxy_jump:
        command.extend(["-o", f"ProxyJump={host.proxy_jump}"])
    command.extend([source, destination])
    return command


def ensure_unique_alias(alias: str, hosts: Iterable[HostEntry], tunnels: Iterable[TunnelEntry]) -> None:
    aliases = {entry.alias for entry in hosts} | {entry.alias for entry in tunnels}
    if alias in aliases:
        raise SSHManError(f"Alias {alias!r} already exists.")


def ensure_unique_local_port(local_port: int, tunnels: Iterable[TunnelEntry]) -> None:
    used_ports = {entry.local_port for entry in tunnels}
    if local_port in used_ports:
        raise SSHManError(f"Local port {local_port} is already used by another tunnel.")


def validate_alias(alias: str) -> None:
    if not ALIAS_PATTERN.match(alias):
        raise SSHManError("Alias may contain only letters, digits, dot, underscore, and dash.")


def validate_port(port: int) -> None:
    if port < 1 or port > 65535:
        raise SSHManError(f"Invalid port {port}.")


def write_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def backup_paths() -> Path:
    ensure_ssh_dirs()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUPS_DIR / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in (CONFIG_PATH, HOSTS_PATH, TUNNELS_PATH):
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def maybe_validate_alias(alias: str) -> None:
    result = run_command(["ssh", "-G", alias])
    if result.returncode != 0:
        print(f"Warning: `ssh -G {alias}` failed. Inspect your SSH configuration.", file=sys.stderr)


def ensure_local_key(identity_file: str, comment: str | None) -> Path:
    identity_path = Path(os.path.expanduser(identity_file))
    public_key_path = identity_path.with_suffix(identity_path.suffix + ".pub") if identity_path.suffix else Path(f"{identity_path}.pub")
    if identity_path.exists() and public_key_path.exists():
        return identity_path

    ensure_ssh_dirs()
    comment_value = comment or default_key_comment()
    print(f"Generating SSH key at {identity_path}")
    result = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(identity_path), "-C", comment_value],
        check=False,
    )
    if result.returncode != 0:
        raise SSHManError("ssh-keygen failed while creating a new key.")
    return identity_path


def default_key_comment() -> str:
    username = os.environ.get("USER") or "user"
    hostname = socket.gethostname()
    return f"{username}@{hostname}"


def deploy_public_key(
    *,
    hostname: str,
    user: str,
    port: int,
    public_key_path: Path,
    proxy_jump: str | None,
) -> None:
    ssh_copy_id = shutil.which("ssh-copy-id")
    if ssh_copy_id:
        command = [ssh_copy_id, "-i", str(public_key_path), "-p", str(port)]
        if proxy_jump:
            command.extend(["-o", f"ProxyJump={proxy_jump}"])
        command.append(f"{user}@{hostname}")
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise SSHManError("ssh-copy-id failed while deploying the public key.")
        return

    public_key = public_key_path.read_text(encoding="utf-8").strip()
    command = ["ssh", "-p", str(port)]
    if proxy_jump:
        command.extend(["-J", proxy_jump])
    command.append(f"{user}@{hostname}")
    command.append("umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && grep -qxF \"$KEY\" ~/.ssh/authorized_keys || echo \"$KEY\" >> ~/.ssh/authorized_keys")
    env = os.environ.copy()
    env["KEY"] = public_key
    result = subprocess.run(command, env=env, check=False)
    if result.returncode != 0:
        raise SSHManError("ssh fallback failed while deploying the public key.")


def verify_key_login(
    *,
    hostname: str,
    user: str,
    port: int,
    identity_file: Path,
    proxy_jump: str | None,
) -> None:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-p",
        str(port),
        "-i",
        str(identity_file),
    ]
    if proxy_jump:
        command.extend(["-J", proxy_jump])
    command.extend([f"{user}@{hostname}", "exit"])
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SSHManError(
            "Public key deployment finished, but passwordless login verification failed. "
            "Check remote authorized_keys permissions and sshd settings."
        )


def mode_string(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(path.stat().st_mode & 0o777)[2:]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 1, "", "command not found")


def run_interactive_command(command: list[str]) -> None:
    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        raise SSHManError(f"Command not found: {command[0]}") from exc
    if result.returncode != 0:
        raise SSHManError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")


def choose_alias(entries: Iterable[HostEntry | TunnelEntry], entry_type: str) -> str:
    items = list(entries)
    if shutil.which("fzf"):
        completed = subprocess.run(
            ["fzf", "--prompt", f"{entry_type}> "],
            input="\n".join(entry.alias for entry in items),
            capture_output=True,
            text=True,
            check=False,
        )
        alias = completed.stdout.strip()
        if completed.returncode == 0 and alias:
            return alias
        raise SSHManError(f"No {entry_type} selected.")

    print(f"Select a {entry_type}:")
    for index, entry in enumerate(items, start=1):
        if isinstance(entry, HostEntry):
            detail = f"{entry.user}@{entry.hostname}:{entry.port}"
        else:
            detail = f"{entry.bind_address}:{entry.local_port} -> {entry.target_host}:{entry.target_port}"
        print(f"  {index}. {entry.alias} ({detail})")
    choice = input("> ").strip()
    if not choice.isdigit():
        raise SSHManError(f"Invalid {entry_type} selection.")
    index = int(choice)
    if index < 1 or index > len(items):
        raise SSHManError(f"{entry_type.capitalize()} selection out of range.")
    return items[index - 1].alias


def split_config_blocks(path: Path) -> list[tuple[list[str], list[str]]]:
    if not path.exists():
        return []
    blocks: list[tuple[list[str], list[str]]] = []
    current: list[str] = []
    comments: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
        if not line:
            if current:
                blocks.append((comments, current))
                current = []
                comments = []
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        current.append(line)
    if current:
        blocks.append((comments, current))
    return blocks


if __name__ == "__main__":
    main()
