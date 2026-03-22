from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sshman.inventory import InventoryError, load_inventory, write_template
from sshman.models import HostEntry, InventoryHost, InventoryTunnel, TunnelEntry


SSH_DIR = Path.home() / ".ssh"
CONFIG_PATH = SSH_DIR / "config"
CONFIG_D_DIR = SSH_DIR / "config.d"
HOSTS_PATH = CONFIG_D_DIR / "hosts.conf"
TUNNELS_PATH = CONFIG_D_DIR / "tunnels.conf"
BACKUPS_DIR = SSH_DIR / "backups"
APP_CONFIG_DIR = Path.home() / ".config" / "sshman"
DEFAULT_INVENTORY_PATH = APP_CONFIG_DIR / "inventory.yaml"
DEFAULT_IDENTITY = "~/.ssh/id_ed25519"
MANAGED_HEADER = "# Managed by sshman. Manual edits are allowed.\n"
INCLUDE_LINE = "Include ~/.ssh/config.d/*.conf"
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class SSHManError(Exception):
    pass


def main() -> None:
    argv = preprocess_argv(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if not getattr(args, "command", None):
            parser.print_help()
            return
        args.func(args)
    except InventoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except SSHManError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Offline SSH config and tunnel manager.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{ls,t,cp,x,mv,rm,doctor,sync,gen}")

    open_parser = subparsers.add_parser("__open__", help=argparse.SUPPRESS)
    open_parser.add_argument("alias", nargs="?")
    open_parser.set_defaults(func=cmd_open)

    list_parser = subparsers.add_parser("ls", aliases=["list"], help="List managed entries.")
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

    tunnel_run_parser = subparsers.add_parser("t", aliases=["tunnel"], help="Start or inspect tunnels.")
    tunnel_run_parser.add_argument("alias", nargs="?")
    tunnel_run_parser.add_argument("--all", action="store_true", help="Start all tunnels for the selected host.")
    tunnel_run_parser.add_argument(
        "--default",
        action="store_true",
        help="Start only the host's default tunnels.",
    )
    tunnel_run_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current tunnel status instead of starting tunnels.",
    )
    tunnel_run_parser.set_defaults(func=cmd_tunnel)

    copy_parser = subparsers.add_parser("cp", aliases=["copy"], help="Copy files to or from a managed host.")
    copy_parser.add_argument("alias")
    copy_parser.add_argument("source")
    copy_parser.add_argument("destination")
    copy_parser.add_argument("-r", "--recursive", action="store_true", help="Copy directories recursively.")
    copy_parser.set_defaults(func=cmd_copy)

    exec_parser = subparsers.add_parser("x", aliases=["exec"], help="Run one command on a managed host.")
    exec_parser.add_argument("alias")
    exec_parser.add_argument("command")
    exec_parser.set_defaults(func=cmd_exec)

    rename_parser = subparsers.add_parser("mv", aliases=["rename"], help="Rename a managed host or tunnel alias.")
    rename_parser.add_argument("old_alias")
    rename_parser.add_argument("new_alias")
    rename_parser.set_defaults(func=cmd_rename)

    remove_parser = subparsers.add_parser("rm", aliases=["remove"], help="Remove a managed host or tunnel entry.")
    remove_parser.add_argument("alias")
    remove_parser.set_defaults(func=cmd_remove)

    doctor_parser = subparsers.add_parser("doctor", help="Run SSH environment diagnostics.")
    doctor_parser.set_defaults(func=cmd_doctor)

    import_parser = subparsers.add_parser("sync", aliases=["import"], help="Import hosts and tunnels from YAML inventory.")
    import_parser.add_argument("--file", help="Inventory file path. Defaults to ~/.config/sshman/inventory.yaml.")
    import_parser.add_argument(
        "--on-conflict",
        choices=("error", "skip", "update"),
        default="error",
        help="How to handle existing aliases during import.",
    )
    import_parser.add_argument(
        "--use-passwords",
        action="store_true",
        help="Use password fields from inventory for one-time key bootstrap when present.",
    )
    import_parser.set_defaults(func=cmd_import_inventory)

    template_parser = subparsers.add_parser("gen", aliases=["template"], help="Write the YAML inventory template.")
    template_parser.add_argument("--file", help="Template file path. Defaults to ~/.config/sshman/inventory.yaml.")
    template_parser.set_defaults(func=cmd_template)

    hidden_choices = []
    for action in subparsers._choices_actions:
        if action.dest == "__open__":
            hidden_choices.append(action)
    for action in hidden_choices:
        subparsers._choices_actions.remove(action)

    return parser


PUBLIC_COMMANDS = {
    "ls",
    "list",
    "t",
    "tunnel",
    "cp",
    "copy",
    "x",
    "exec",
    "mv",
    "rename",
    "rm",
    "remove",
    "doctor",
    "sync",
    "import",
    "gen",
    "template",
}


def preprocess_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["__open__"]
    first = argv[0]
    if first.startswith("-") or first in PUBLIC_COMMANDS:
        return argv
    return ["__open__", *argv]


def init_config(force: bool = False) -> None:
    ensure_ssh_dirs()
    if not CONFIG_PATH.exists():
        write_file(CONFIG_PATH, render_main_config())
    else:
        ensure_include_line(CONFIG_PATH)
    ensure_managed_file(HOSTS_PATH, force)
    ensure_managed_file(TUNNELS_PATH, force)
    ensure_permissions()


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


def cmd_open(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    if not hosts:
        raise SSHManError("No managed hosts found.")
    alias = args.alias or choose_host_alias(hosts)
    host = get_host_by_alias(hosts, alias)
    if host is None:
        raise SSHManError(f"Host alias {alias!r} not found.")
    start_default_tunnels(alias)
    run_interactive_command(["ssh", alias])


def cmd_tunnel(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()
    if args.status:
        show_tunnel_status(hosts, tunnels)
        return
    if not tunnels:
        raise SSHManError("No managed tunnels found.")

    if not args.alias:
        alias = choose_tunnel_alias(tunnels, hosts)
        start_tunnel(alias)
        return

    host = get_host_by_alias(hosts, args.alias)
    if host is not None:
        host_inventory = get_inventory_host(args.alias)
        if args.all:
            aliases = [tunnel.alias for tunnel in tunnels if tunnel.via == args.alias]
            start_tunnels(aliases)
            return
        if args.default:
            aliases = resolve_default_tunnels(args.alias)
            if not aliases:
                raise SSHManError(f"Host {args.alias!r} has no default tunnels.")
            start_tunnels(aliases)
            return
        aliases = [tunnel.alias for tunnel in tunnels if tunnel.via == args.alias]
        if not aliases:
            raise SSHManError(f"Host {args.alias!r} has no tunnels.")
        if host_inventory and host_inventory.default_tunnels:
            start_tunnels(host_inventory.default_tunnels)
            return
        alias = choose_tunnel_alias([t for t in tunnels if t.via == args.alias], hosts)
        start_tunnel(alias)
        return

    if get_tunnel_by_alias(tunnels, args.alias) is None:
        raise SSHManError(f"Tunnel alias {args.alias!r} not found.")
    start_tunnel(args.alias)


def cmd_copy(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    host = get_host_by_alias(hosts, args.alias)
    if host is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    source = args.source
    destination = args.destination
    source_remote = source.startswith(":")
    destination_remote = destination.startswith(":")
    if source_remote == destination_remote:
        raise SSHManError("Copy requires exactly one remote path prefixed with ':'.")

    if source_remote:
        local_target = Path(destination).expanduser()
        local_parent = local_target.parent if local_target.name else local_target
        if not local_parent.exists():
            raise SSHManError(f"Local destination directory does not exist: {local_parent}")
        source_arg = f"{args.alias}:{source[1:]}"
        destination_arg = str(local_target)
    else:
        local_source = Path(source).expanduser()
        if not local_source.exists():
            raise SSHManError(f"Local path not found: {local_source}")
        if local_source.is_dir() and not args.recursive:
            raise SSHManError("Local path is a directory. Re-run with --recursive.")
        source_arg = str(local_source)
        destination_arg = f"{args.alias}:{destination[1:]}"

    run_interactive_command(build_scp_command(host, args.recursive, source_arg, destination_arg))


def cmd_exec(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, _ = load_entries()
    if get_host_by_alias(hosts, args.alias) is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    run_interactive_command(["ssh", args.alias, args.command])


def cmd_rename(args: argparse.Namespace) -> None:
    ensure_initialized()
    validate_alias(args.new_alias)
    hosts, tunnels = load_entries()
    ensure_unique_alias(args.new_alias, hosts, tunnels)
    host = get_host_by_alias(hosts, args.old_alias)
    if host is not None:
        backup_paths()
        host.alias = args.new_alias
        for tunnel in tunnels:
            if tunnel.via == args.old_alias:
                tunnel.via = args.new_alias
        rewrite_hosts_file(hosts)
        rewrite_tunnels_file(tunnels, hosts)
        print(f"Renamed host {args.old_alias} -> {args.new_alias}")
        return
    tunnel = get_tunnel_by_alias(tunnels, args.old_alias)
    if tunnel is not None:
        backup_paths()
        tunnel.alias = args.new_alias
        rewrite_tunnels_file(tunnels, hosts)
        print(f"Renamed tunnel {args.old_alias} -> {args.new_alias}")
        return
    raise SSHManError(f"Alias {args.old_alias!r} not found.")


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
        backup_paths()
        hosts = [entry for entry in hosts if entry.alias != args.alias]
        rewrite_hosts_file(hosts)
        print(f"Removed host {args.alias}")
        return
    tunnel = get_tunnel_by_alias(tunnels, args.alias)
    if tunnel is not None:
        backup_paths()
        tunnels = [entry for entry in tunnels if entry.alias != args.alias]
        rewrite_tunnels_file(tunnels, hosts)
        print(f"Removed tunnel {args.alias}")
        return
    raise SSHManError(f"Alias {args.alias!r} not found.")


def cmd_import_inventory(args: argparse.Namespace) -> None:
    ensure_initialized(auto_create=True)
    path = resolve_inventory_path(args.file)
    if not path.exists():
        raise SSHManError(f"Inventory file not found: {path}")

    inventory_hosts = load_inventory(path)
    if not inventory_hosts:
        print("No hosts imported.")
        return

    imported_hosts = 0
    imported_tunnels = 0
    skipped = 0
    updated = 0

    for inventory_host in inventory_hosts:
        host_state = apply_host_inventory(inventory_host, args.on_conflict, args.use_passwords)
        imported_hosts += host_state["imported"]
        skipped += host_state["skipped"]
        updated += host_state["updated"]

        for inventory_tunnel in inventory_host.tunnels:
            tunnel_state = apply_tunnel_inventory(
                via_alias=inventory_host.alias,
                inventory_tunnel=inventory_tunnel,
                on_conflict=args.on_conflict,
            )
            imported_tunnels += tunnel_state["imported"]
            skipped += tunnel_state["skipped"]
            updated += tunnel_state["updated"]

    print(f"Imported hosts: {imported_hosts}")
    print(f"Imported tunnels: {imported_tunnels}")
    if updated:
        print(f"Updated existing entries: {updated}")
    if skipped:
        print(f"Skipped existing entries: {skipped}")


def cmd_template(args: argparse.Namespace) -> None:
    destination = resolve_inventory_path(args.file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_template(destination)
    print(f"Wrote template to {destination}")


def apply_host_inventory(inventory_host: InventoryHost, on_conflict: str, use_passwords: bool) -> dict[str, int]:
    hosts, tunnels = load_entries()
    existing_host = get_host_by_alias(hosts, inventory_host.alias)
    if existing_host is not None:
        if on_conflict == "error":
            raise SSHManError(f"Alias {inventory_host.alias!r} already exists.")
        if on_conflict == "skip":
            return {"imported": 0, "skipped": 1, "updated": 0}
        if on_conflict == "update":
            dependent_tunnels = [tunnel.alias for tunnel in tunnels if tunnel.via == inventory_host.alias]
            if dependent_tunnels:
                rewrite_tunnels_file([t for t in tunnels if t.alias not in dependent_tunnels], hosts)
            hosts = [host for host in hosts if host.alias != inventory_host.alias]
            rewrite_hosts_file(hosts)
            existing_host = None
            updated = 1
        else:
            updated = 0
    else:
        updated = 0

    if use_passwords and inventory_host.password:
        identity_file = inventory_host.identity_file or DEFAULT_IDENTITY
        identity_path = ensure_local_key(identity_file, None)
        public_key_path = public_key_for(identity_path)
        deploy_public_key(
            hostname=inventory_host.host,
            user=inventory_host.user,
            port=inventory_host.port,
            public_key_path=public_key_path,
            proxy_jump=inventory_host.proxy_jump,
            password=inventory_host.password,
        )
        verify_key_login(
            hostname=inventory_host.host,
            user=inventory_host.user,
            port=inventory_host.port,
            identity_file=identity_path,
            proxy_jump=inventory_host.proxy_jump,
        )

    cmd_add_host(
        argparse.Namespace(
            alias=inventory_host.alias,
            host=inventory_host.host,
            user=inventory_host.user,
            port=inventory_host.port,
            group=inventory_host.group,
            identity_file=inventory_host.identity_file,
            note=inventory_host.note,
            proxy_jump=inventory_host.proxy_jump,
        )
    )
    return {"imported": 1, "skipped": 0, "updated": updated}


def apply_tunnel_inventory(via_alias: str, inventory_tunnel: InventoryTunnel, on_conflict: str) -> dict[str, int]:
    hosts, tunnels = load_entries()
    existing_tunnel = get_tunnel_by_alias(tunnels, inventory_tunnel.alias)
    if existing_tunnel is not None:
        if on_conflict == "error":
            raise SSHManError(f"Alias {inventory_tunnel.alias!r} already exists.")
        if on_conflict == "skip":
            return {"imported": 0, "skipped": 1, "updated": 0}
        if on_conflict == "update":
            tunnels = [tunnel for tunnel in tunnels if tunnel.alias != inventory_tunnel.alias]
            rewrite_tunnels_file(tunnels, hosts)
            updated = 1
        else:
            updated = 0
    else:
        updated = 0

    cmd_add_tunnel(
        argparse.Namespace(
            alias=inventory_tunnel.alias,
            via=via_alias,
            local_port=inventory_tunnel.local_port,
            target_host=inventory_tunnel.target_host,
            target_port=inventory_tunnel.target_port,
            bind_address=inventory_tunnel.bind_address,
            note=inventory_tunnel.note,
        )
    )
    return {"imported": 1, "skipped": 0, "updated": updated}


def cmd_doctor(args: argparse.Namespace) -> None:
    ensure_initialized()
    hosts, tunnels = load_entries()
    issues: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    check_output = capture_check_output()
    if check_output["failed"]:
        issues.extend(check_output["issues"])
    warnings.extend(check_output["warnings"])
    infos.extend(check_output["infos"])

    private_key = Path(os.path.expanduser(DEFAULT_IDENTITY))
    public_key = Path(f"{private_key}.pub")
    if private_key.exists() and public_key.exists():
        infos.append(f"Default key pair present: {private_key}")
    else:
        warnings.append("Default SSH key pair is incomplete or missing.")

    if CONFIG_PATH.exists():
        infos.append(f"Managed config present: {CONFIG_PATH}")
        if INCLUDE_LINE in CONFIG_PATH.read_text(encoding="utf-8"):
            infos.append("Main ssh config includes sshman managed config.d files.")
    if hosts:
        infos.append(f"Managed hosts: {len(hosts)}")
    if tunnels:
        infos.append(f"Managed tunnels: {len(tunnels)}")
    infos.append(f"Default inventory path: {DEFAULT_INVENTORY_PATH}")

    if not backups_exist():
        warnings.append("No sshman backups found yet. Run `sshman backup` after major changes.")

    if not shutil.which("fzf"):
        issues.append("fzf is not installed. sshm 1.0 requires fzf for interactive selection.")

    print("Doctor results")
    for issue in issues:
        print(f"  - Issue: {issue}")
    for warning in warnings:
        print(f"  - Warning: {warning}")
    for info in infos:
        print(f"  - Info: {info}")
    if issues:
        raise SSHManError("Doctor found blocking issues.")


def ensure_initialized(auto_create: bool = False) -> None:
    if not CONFIG_PATH.exists() or not HOSTS_PATH.exists() or not TUNNELS_PATH.exists():
        if auto_create:
            init_config(force=False)
            return
        raise SSHManError("sshman config is missing. Start with `sshman import`.")


def ensure_ssh_dirs() -> None:
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG_D_DIR.mkdir(mode=0o700, exist_ok=True)
    BACKUPS_DIR.mkdir(mode=0o700, exist_ok=True)
    APP_CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def ensure_permissions() -> None:
    os.chmod(SSH_DIR, 0o700)
    for path in (HOSTS_PATH, TUNNELS_PATH):
        if path.exists():
            os.chmod(path, 0o600)


def ensure_managed_file(path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        current = path.read_text()
        if "Managed by sshman" not in current:
            raise SSHManError(f"{path} already exists and is not managed by sshman.")
    write_file(path, MANAGED_HEADER)


def ensure_include_line(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if INCLUDE_LINE in text:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"\n{INCLUDE_LINE}\n"
    write_file(path, text)


def resolve_inventory_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else DEFAULT_INVENTORY_PATH


def render_main_config() -> str:
    return (
        "Host *\n"
        + "  ServerAliveInterval 60\n"
        + "  ServerAliveCountMax 3\n"
        + "  TCPKeepAlive yes\n"
        + "  AddKeysToAgent yes\n"
        + "  UseKeychain yes\n"
        + f"  IdentityFile {DEFAULT_IDENTITY}\n"
        + "\n"
        + f"{INCLUDE_LINE}\n"
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
    lines.extend(["", ""])
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
    lines.extend(["", ""])
    return "\n".join(lines)


def append_entry(path: Path, block: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        if path.stat().st_size > 0 and not path.read_text().endswith("\n"):
            handle.write("\n")
        handle.write(block)


def rewrite_hosts_file(hosts: list[HostEntry]) -> None:
    content = MANAGED_HEADER
    for host in hosts:
        content += render_host_entry(host)
    write_file(HOSTS_PATH, content)


def rewrite_tunnels_file(tunnels: list[TunnelEntry], hosts: list[HostEntry]) -> None:
    content = MANAGED_HEADER
    for tunnel in tunnels:
        via_host = get_host_by_alias(hosts, tunnel.via)
        if via_host is None:
            raise SSHManError(f"Tunnel {tunnel.alias} references missing host {tunnel.via!r}.")
        content += render_tunnel_entry(tunnel, via_host)
    write_file(TUNNELS_PATH, content)


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


def capture_check_output() -> dict[str, list[str] | bool]:
    hosts, tunnels = load_entries()
    issues: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    if not CONFIG_PATH.exists():
        issues.append(f"Missing {CONFIG_PATH}")
    if CONFIG_PATH.exists() and INCLUDE_LINE not in CONFIG_PATH.read_text(encoding="utf-8"):
        warnings.append("Main config does not include ~/.ssh/config.d/*.conf")

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

    ssh_perm = mode_string(SSH_DIR)
    if ssh_perm and ssh_perm != "700":
        warnings.append(f"Unexpected SSH dir permission on {SSH_DIR}: {ssh_perm}")

    ssh_check = run_command(["ssh", "-G", "localhost"])
    if ssh_check.returncode != 0:
        issues.append("ssh command is not available or failed to inspect config.")
    else:
        infos.append("ssh command is available.")

    if shutil.which("fzf"):
        infos.append("fzf is installed.")

    agent_keys = run_command(["ssh-add", "-l"])
    if agent_keys.returncode != 0:
        warnings.append("ssh-agent has no loaded identities or is unavailable.")
    else:
        infos.append("ssh-agent has loaded identities.")

    return {"failed": bool(issues), "issues": issues, "warnings": warnings, "infos": infos}


def backups_exist() -> bool:
    return BACKUPS_DIR.exists() and any(BACKUPS_DIR.iterdir())


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
    public_key_path = public_key_for(identity_path)
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


def public_key_for(identity_path: Path) -> Path:
    return identity_path.with_suffix(identity_path.suffix + ".pub") if identity_path.suffix else Path(f"{identity_path}.pub")


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
    password: str | None = None,
) -> None:
    ssh_copy_id = shutil.which("ssh-copy-id")
    if ssh_copy_id:
        command = [ssh_copy_id, "-i", str(public_key_path), "-p", str(port)]
        if proxy_jump:
            command.extend(["-o", f"ProxyJump={proxy_jump}"])
        command.append(f"{user}@{hostname}")
        if password:
            command = with_sshpass(command, password)
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
    if password:
        command = with_sshpass(command, password)
    result = subprocess.run(command, env=env, check=False)
    if result.returncode != 0:
        raise SSHManError("ssh fallback failed while deploying the public key.")


def with_sshpass(command: list[str], password: str) -> list[str]:
    sshpass = shutil.which("sshpass")
    if not sshpass:
        raise SSHManError(
            "Inventory requested password-based bootstrap, but sshpass is not installed. "
            "Install sshpass or omit the password field."
        )
    return [sshpass, "-p", password, *command]


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


def choose_host_alias(hosts: Iterable[HostEntry]) -> str:
    items = list(hosts)
    lines = [
        f"{host.alias}\t{host.user}@{host.hostname}:{host.port}\t{host.group}\t{host.note or ''}"
        for host in items
    ]
    return fzf_select(lines, "host> ").split("\t", 1)[0]


def choose_tunnel_alias(tunnels: Iterable[TunnelEntry], hosts: Iterable[HostEntry]) -> str:
    host_map = {host.alias: host for host in hosts}
    items = list(tunnels)
    lines = []
    for tunnel in items:
        via = tunnel.via
        target = f"{tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}"
        via_host = host_map.get(via)
        via_text = via
        if via_host is not None:
            via_text = f"{via} ({via_host.hostname})"
        status = tunnel_status_label(tunnel)
        lines.append(f"{tunnel.alias}\t{via_text}\t{target}\t{status}\t{tunnel.note or ''}")
    return fzf_select(lines, "tunnel> ").split("\t", 1)[0]


def fzf_select(lines: list[str], prompt: str) -> str:
    if not shutil.which("fzf"):
        raise SSHManError("fzf is required for sshm interactive selection. Install it first.")
    completed = subprocess.run(
        ["fzf", "--prompt", prompt, "--delimiter", "\t", "--with-nth", "1,2,3,4,5"],
        input="\n".join(lines),
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode == 0 and value:
        return value
    raise SSHManError("No selection made.")


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


def get_inventory_host(alias: str) -> InventoryHost | None:
    path = resolve_inventory_path(None)
    if not path.exists():
        return None
    for host in load_inventory(path):
        if host.alias == alias:
            return host
    return None


def resolve_default_tunnels(host_alias: str) -> list[str]:
    host = get_inventory_host(host_alias)
    if host is None:
        return []
    return host.default_tunnels


def start_default_tunnels(host_alias: str) -> None:
    aliases = resolve_default_tunnels(host_alias)
    if aliases:
        start_tunnels(aliases)


def start_tunnel(alias: str) -> None:
    run_interactive_command(["ssh", "-fN", alias])


def start_tunnels(aliases: Iterable[str]) -> None:
    for alias in aliases:
        tunnel = get_tunnel_by_alias(load_entries()[1], alias)
        if tunnel is None:
            raise SSHManError(f"Tunnel alias {alias!r} not found.")
        if tunnel_is_running(tunnel):
            continue
        start_tunnel(alias)


def tunnel_is_running(tunnel: TunnelEntry) -> bool:
    import socket as socket_module

    target_host = "127.0.0.1" if tunnel.bind_address in {"", "0.0.0.0", "*"} else tunnel.bind_address
    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex((target_host, tunnel.local_port)) == 0
    finally:
        sock.close()


def tunnel_status_label(tunnel: TunnelEntry) -> str:
    return "running" if tunnel_is_running(tunnel) else "stopped"


def show_tunnel_status(hosts: list[HostEntry], tunnels: list[TunnelEntry]) -> None:
    if not tunnels:
        print("No tunnels")
        return
    print("Tunnel status")
    for tunnel in tunnels:
        target = f"{tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}"
        print(f"  {tunnel.alias:16} {target:40} {tunnel_status_label(tunnel)} via {tunnel.via}")


if __name__ == "__main__":
    main()
