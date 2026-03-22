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

from sshman.inventory import (
    InventoryError,
    find_host_line,
    load_inventory,
    save_inventory,
    write_template,
)
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
HOST_PREVIEW_LINE = "{alias}\t{user}@{host}:{port}\t{group}\t{note}"
TUNNEL_PREVIEW_LINE = "{alias}\t{via}\t{mapping}\t{status}\t{note}"
SELECTOR_KEYS = "enter,ctrl-t,ctrl-e,ctrl-r,ctrl-d"


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
        description="UX-first SSH inventory manager.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{ls,t,cp,x,mv,rm,edit,doctor,sync,gen}",
    )

    open_parser = subparsers.add_parser("__open__", help=argparse.SUPPRESS)
    open_parser.add_argument("alias", nargs="?")
    open_parser.set_defaults(func=cmd_open)

    preview_parser = subparsers.add_parser("__preview__", help=argparse.SUPPRESS)
    preview_parser.add_argument("kind", choices=("host", "tunnel"))
    preview_parser.add_argument("alias")
    preview_parser.set_defaults(func=cmd_preview)

    list_parser = subparsers.add_parser("ls", help="List hosts and tunnels from inventory.")
    list_parser.add_argument("--type", choices=("all", "host", "tunnel"), default="all")
    list_parser.add_argument("--simple", action="store_true", help="Print aliases only.")
    list_parser.set_defaults(func=cmd_list)

    tunnel_parser = subparsers.add_parser("t", help="Start tunnels or inspect tunnel status.")
    tunnel_parser.add_argument("alias", nargs="?")
    tunnel_parser.add_argument("--all", action="store_true", help="Start all tunnels for a host.")
    tunnel_parser.add_argument("--default", action="store_true", help="Start only a host's default tunnels.")
    tunnel_parser.add_argument("--status", action="store_true", help="Show tunnel runtime status.")
    tunnel_parser.set_defaults(func=cmd_tunnel)

    copy_parser = subparsers.add_parser("cp", help="Copy files to or from a managed host.")
    copy_parser.add_argument("alias")
    copy_parser.add_argument("source")
    copy_parser.add_argument("destination")
    copy_parser.add_argument("-r", "--recursive", action="store_true", help="Copy directories recursively.")
    copy_parser.set_defaults(func=cmd_copy)

    exec_parser = subparsers.add_parser("x", help="Run one command on a managed host.")
    exec_parser.add_argument("alias")
    exec_parser.add_argument("command")
    exec_parser.set_defaults(func=cmd_exec)

    rename_parser = subparsers.add_parser("mv", help="Rename a host or tunnel alias in inventory.")
    rename_parser.add_argument("old_alias")
    rename_parser.add_argument("new_alias")
    rename_parser.set_defaults(func=cmd_rename)

    remove_parser = subparsers.add_parser("rm", help="Remove a host or tunnel from inventory.")
    remove_parser.add_argument("alias")
    remove_parser.set_defaults(func=cmd_remove)

    edit_parser = subparsers.add_parser("edit", help="Open the inventory in your editor.")
    edit_parser.add_argument("alias", nargs="?")
    edit_parser.set_defaults(func=cmd_edit)

    doctor_parser = subparsers.add_parser("doctor", help="Validate fzf, inventory, and SSH state.")
    doctor_parser.set_defaults(func=cmd_doctor)

    sync_parser = subparsers.add_parser("sync", help="Render inventory into ~/.ssh/config.d.")
    sync_parser.add_argument("--file", help="Inventory file path. Defaults to ~/.config/sshman/inventory.yaml.")
    sync_parser.add_argument(
        "--use-passwords",
        action="store_true",
        help="Use inventory passwords once for SSH key bootstrap when present.",
    )
    sync_parser.set_defaults(func=cmd_sync)

    template_parser = subparsers.add_parser("gen", help="Write the default inventory template.")
    template_parser.add_argument("--file", help="Template file path. Defaults to ~/.config/sshman/inventory.yaml.")
    template_parser.set_defaults(func=cmd_template)

    hidden_choices = []
    for action in subparsers._choices_actions:
        if action.dest in {"__open__", "__preview__"}:
            hidden_choices.append(action)
    for action in hidden_choices:
        subparsers._choices_actions.remove(action)

    return parser


PUBLIC_COMMANDS = {"ls", "t", "cp", "x", "mv", "rm", "edit", "doctor", "sync", "gen"}


def preprocess_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["__open__"]
    first = argv[0]
    if first.startswith("-") or first in PUBLIC_COMMANDS:
        return argv
    return ["__open__", *argv]


def cmd_open(args: argparse.Namespace) -> None:
    ensure_fzf_installed()
    ensure_inventory_exists()
    if args.alias:
        connect_host(args.alias)
        return
    interactive_host_selector()


def cmd_preview(args: argparse.Namespace) -> None:
    ensure_inventory_exists()
    inventory_hosts = load_inventory_state()
    if args.kind == "host":
        host = find_inventory_host(inventory_hosts, args.alias)
        if host is None:
            raise SSHManError(f"Host alias {args.alias!r} not found.")
        print(render_host_preview(host))
        return
    host, tunnel = find_inventory_tunnel(inventory_hosts, args.alias)
    if host is None or tunnel is None:
        raise SSHManError(f"Tunnel alias {args.alias!r} not found.")
    print(render_tunnel_preview(host, tunnel))


def cmd_list(args: argparse.Namespace) -> None:
    ensure_inventory_exists()
    inventory_hosts = load_inventory_state()

    if args.simple:
        if args.type in ("all", "host"):
            for host in inventory_hosts:
                print(host.alias)
        if args.type in ("all", "tunnel"):
            for host in inventory_hosts:
                for tunnel in host.tunnels:
                    print(tunnel.alias)
        return

    if args.type in ("all", "host"):
        print("Hosts")
        for host in inventory_hosts:
            note = f" [{host.note}]" if host.note else ""
            defaults = f" defaults={','.join(host.default_tunnels)}" if host.default_tunnels else ""
            print(f"  {host.alias:20} {host.user}@{host.host}:{host.port} ({host.group}){defaults}{note}")

    if args.type == "all":
        print()

    if args.type in ("all", "tunnel"):
        print("Tunnels")
        for host in inventory_hosts:
            for tunnel in host.tunnels:
                note = f" [{tunnel.note}]" if tunnel.note else ""
                target = f"{tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}"
                state = tunnel_runtime_label(tunnel)
                print(f"  {tunnel.alias:20} {target:45} {state:8} via {host.alias}{note}")


def cmd_tunnel(args: argparse.Namespace) -> None:
    inventory_hosts = load_inventory_state()
    if args.status:
        show_tunnel_status(inventory_hosts)
        return

    ensure_runtime_ready()
    if args.alias:
        host = find_inventory_host(inventory_hosts, args.alias)
        if host is not None:
            if args.all:
                start_inventory_tunnels(host.tunnels)
                return
            if args.default:
                default_tunnels = resolve_default_inventory_tunnels(host)
                if not default_tunnels:
                    raise SSHManError(f"Host {host.alias!r} has no default tunnels.")
                start_inventory_tunnels(default_tunnels)
                return
            default_tunnels = resolve_default_inventory_tunnels(host)
            if default_tunnels:
                start_inventory_tunnels(default_tunnels)
                return
            if not host.tunnels:
                raise SSHManError(f"Host {host.alias!r} has no tunnels.")
            start_tunnel_by_alias(choose_tunnel_alias([host]))
            return

        tunnel_host, tunnel = find_inventory_tunnel(inventory_hosts, args.alias)
        if tunnel_host is None or tunnel is None:
            raise SSHManError(f"Tunnel alias {args.alias!r} not found.")
        start_tunnel_by_alias(tunnel.alias)
        return

    ensure_fzf_installed()
    interactive_tunnel_selector(inventory_hosts)


def cmd_copy(args: argparse.Namespace) -> None:
    inventory_hosts = load_inventory_state()
    host = find_inventory_host(inventory_hosts, args.alias)
    if host is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    ensure_runtime_ready()

    source_remote = args.source.startswith(":")
    destination_remote = args.destination.startswith(":")
    if source_remote == destination_remote:
        raise SSHManError("Copy requires exactly one remote path prefixed with ':'.")

    if source_remote:
        local_target = Path(args.destination).expanduser()
        local_parent = local_target.parent if local_target.name else local_target
        if not local_parent.exists():
            raise SSHManError(f"Local destination directory does not exist: {local_parent}")
        source_arg = f"{args.alias}:{args.source[1:]}"
        destination_arg = str(local_target)
    else:
        local_source = Path(args.source).expanduser()
        if not local_source.exists():
            raise SSHManError(f"Local path not found: {local_source}")
        if local_source.is_dir() and not args.recursive:
            raise SSHManError("Local path is a directory. Re-run with --recursive.")
        source_arg = str(local_source)
        destination_arg = f"{args.alias}:{args.destination[1:]}"

    run_interactive_command(build_scp_command(host, args.recursive, source_arg, destination_arg))


def cmd_exec(args: argparse.Namespace) -> None:
    inventory_hosts = load_inventory_state()
    if find_inventory_host(inventory_hosts, args.alias) is None:
        raise SSHManError(f"Host alias {args.alias!r} not found.")
    ensure_runtime_ready()
    run_interactive_command(["ssh", args.alias, args.command])


def cmd_rename(args: argparse.Namespace) -> None:
    validate_alias(args.new_alias)
    inventory_hosts = load_inventory_state()
    rename_inventory_alias(inventory_hosts, args.old_alias, args.new_alias)
    persist_inventory(inventory_hosts)
    sync_inventory(resolve_inventory_path(None), use_passwords=False)
    print(f"Renamed {args.old_alias} -> {args.new_alias}")


def cmd_remove(args: argparse.Namespace) -> None:
    inventory_hosts = load_inventory_state()
    remove_inventory_alias(inventory_hosts, args.alias)
    persist_inventory(inventory_hosts)
    sync_inventory(resolve_inventory_path(None), use_passwords=False)
    print(f"Removed {args.alias}")


def cmd_edit(args: argparse.Namespace) -> None:
    path = ensure_inventory_exists()
    line = find_host_line(path, args.alias) if args.alias else None
    open_in_editor(path, line=line)


def cmd_doctor(args: argparse.Namespace) -> None:
    issues: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    if not shutil.which("ssh"):
        issues.append("ssh is not installed or not on PATH.")
    else:
        infos.append("ssh is available.")

    if not shutil.which("fzf"):
        issues.append("fzf is required for sshm 1.0.1. Install it first.")
    else:
        infos.append("fzf is available.")

    inventory_path = resolve_inventory_path(None)
    if inventory_path.exists():
        infos.append(f"Inventory file present: {inventory_path}")
        try:
            inventory_hosts = load_inventory_state()
            infos.append(f"Inventory hosts: {len(inventory_hosts)}")
            infos.append(f"Inventory tunnels: {sum(len(host.tunnels) for host in inventory_hosts)}")
        except InventoryError as exc:
            issues.append(f"Inventory is invalid: {exc}")
    else:
        warnings.append(f"Inventory file missing: {inventory_path}. Run `sshm gen`.")

    if CONFIG_PATH.exists():
        if INCLUDE_LINE in CONFIG_PATH.read_text(encoding="utf-8"):
            infos.append("~/.ssh/config includes sshman managed config.d files.")
        else:
            warnings.append("~/.ssh/config does not include ~/.ssh/config.d/*.conf")
    else:
        warnings.append("~/.ssh/config does not exist yet.")

    if HOSTS_PATH.exists():
        infos.append(f"Managed hosts config present: {HOSTS_PATH}")
    else:
        warnings.append("Managed hosts config is missing. Run `sshm sync`.")

    if TUNNELS_PATH.exists():
        infos.append(f"Managed tunnels config present: {TUNNELS_PATH}")
    else:
        warnings.append("Managed tunnels config is missing. Run `sshm sync`.")

    private_key = Path(os.path.expanduser(DEFAULT_IDENTITY))
    public_key = public_key_for(private_key)
    if private_key.exists() and public_key.exists():
        infos.append(f"Default key pair present: {private_key}")
    else:
        warnings.append("Default SSH key pair is incomplete or missing.")

    agent_keys = run_command(["ssh-add", "-l"])
    if agent_keys.returncode == 0:
        infos.append("ssh-agent has loaded identities.")
    else:
        warnings.append("ssh-agent has no loaded identities or is unavailable.")

    if backups_exist():
        infos.append(f"Backups available under {BACKUPS_DIR}")
    else:
        warnings.append("No automatic backups found yet under ~/.ssh/backups.")

    print("Doctor results")
    for issue in issues:
        print(f"  - Issue: {issue}")
    for warning in warnings:
        print(f"  - Warning: {warning}")
    for info in infos:
        print(f"  - Info: {info}")
    if issues:
        raise SSHManError("Doctor found blocking issues.")


def cmd_sync(args: argparse.Namespace) -> None:
    path = ensure_inventory_exists(args.file)
    imported_hosts, imported_tunnels = sync_inventory(path, use_passwords=args.use_passwords)
    print(f"Synced hosts: {imported_hosts}")
    print(f"Synced tunnels: {imported_tunnels}")


def cmd_template(args: argparse.Namespace) -> None:
    destination = resolve_inventory_path(args.file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_template(destination)
    print(f"Wrote template to {destination}")


def ensure_inventory_exists(value: str | None = None) -> Path:
    path = resolve_inventory_path(value)
    if not path.exists():
        raise SSHManError(f"Inventory file not found: {path}. Run `sshm gen` first.")
    return path


def resolve_inventory_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else DEFAULT_INVENTORY_PATH


def load_inventory_state(path: Path | None = None) -> list[InventoryHost]:
    target = path or ensure_inventory_exists()
    return load_inventory(target)


def persist_inventory(hosts: list[InventoryHost], path: Path | None = None) -> None:
    target = path or resolve_inventory_path(None)
    save_inventory(target, hosts)


def sync_inventory(path: Path, use_passwords: bool) -> tuple[int, int]:
    ensure_ssh_dirs()
    init_config(force=False)
    inventory_hosts = load_inventory(path)
    validate_inventory_state(inventory_hosts)

    if use_passwords:
        for host in inventory_hosts:
            if host.password:
                identity_file = host.identity_file or DEFAULT_IDENTITY
                identity_path = ensure_local_key(identity_file, None)
                public_key_path = public_key_for(identity_path)
                deploy_public_key(
                    hostname=host.host,
                    user=host.user,
                    port=host.port,
                    public_key_path=public_key_path,
                    proxy_jump=host.proxy_jump,
                    password=host.password,
                )
                verify_key_login(
                    hostname=host.host,
                    user=host.user,
                    port=host.port,
                    identity_file=identity_path,
                    proxy_jump=host.proxy_jump,
                )

    managed_hosts = [
        HostEntry(
            alias=host.alias,
            hostname=host.host,
            user=host.user,
            port=host.port,
            group=host.group,
            identity_file=host.identity_file,
            note=host.note,
            proxy_jump=host.proxy_jump,
        )
        for host in inventory_hosts
    ]
    managed_tunnels = [
        TunnelEntry(
            alias=tunnel.alias,
            via=host.alias,
            local_port=tunnel.local_port,
            target_host=tunnel.target_host,
            target_port=tunnel.target_port,
            bind_address=tunnel.bind_address,
            note=tunnel.note,
        )
        for host in inventory_hosts
        for tunnel in host.tunnels
    ]

    backup_paths()
    rewrite_hosts_file(managed_hosts)
    rewrite_tunnels_file(managed_tunnels, managed_hosts)
    ensure_permissions()
    return len(managed_hosts), len(managed_tunnels)


def validate_inventory_state(hosts: list[InventoryHost]) -> None:
    aliases: set[str] = set()
    tunnel_aliases: set[str] = set()
    local_ports: set[int] = set()
    host_names = {host.alias for host in hosts}

    for host in hosts:
        validate_alias(host.alias)
        validate_port(host.port)
        if host.alias in aliases:
            raise SSHManError(f"Duplicate host alias in inventory: {host.alias}")
        aliases.add(host.alias)

        if host.proxy_jump and host.proxy_jump not in host_names:
            raise SSHManError(f"Host {host.alias} references missing proxy_jump host {host.proxy_jump!r}.")

        for tunnel in host.tunnels:
            validate_alias(tunnel.alias)
            validate_port(tunnel.local_port)
            validate_port(tunnel.target_port)
            if tunnel.alias in aliases or tunnel.alias in tunnel_aliases:
                raise SSHManError(f"Duplicate alias in inventory: {tunnel.alias}")
            if tunnel.local_port in local_ports:
                raise SSHManError(f"Duplicate tunnel local port in inventory: {tunnel.local_port}")
            tunnel_aliases.add(tunnel.alias)
            local_ports.add(tunnel.local_port)


def ensure_runtime_ready() -> None:
    if not CONFIG_PATH.exists() or not HOSTS_PATH.exists() or not TUNNELS_PATH.exists():
        raise SSHManError("Managed SSH config is missing. Run `sshm sync` first.")


def connect_host(alias: str) -> None:
    inventory_hosts = load_inventory_state()
    host = find_inventory_host(inventory_hosts, alias)
    if host is None:
        raise SSHManError(f"Host alias {alias!r} not found.")
    ensure_runtime_ready()
    start_default_tunnels_for_host(host)
    run_interactive_command(["ssh", alias])


def interactive_host_selector() -> None:
    while True:
        inventory_hosts = load_inventory_state()
        if not inventory_hosts:
            raise SSHManError("No hosts defined in inventory.")
        action, alias = choose_host_action(inventory_hosts)
        if action in {"", "enter"}:
            connect_host(alias)
            return
        if action == "ctrl-t":
            host = require_inventory_host(inventory_hosts, alias)
            default_tunnels = resolve_default_inventory_tunnels(host)
            if not default_tunnels:
                print(f"No default tunnels configured for {alias}.", file=sys.stderr)
                continue
            start_inventory_tunnels(default_tunnels)
            continue
        if action == "ctrl-e":
            cmd_edit(argparse.Namespace(alias=alias))
            continue
        if action == "ctrl-r":
            new_alias = prompt("New alias: ").strip()
            if not new_alias:
                continue
            cmd_rename(argparse.Namespace(old_alias=alias, new_alias=new_alias))
            continue
        if action == "ctrl-d":
            confirm = prompt(f"Delete {alias}? [y/N] ").strip().lower()
            if confirm in {"y", "yes"}:
                cmd_remove(argparse.Namespace(alias=alias))
            continue


def interactive_tunnel_selector(inventory_hosts: list[InventoryHost]) -> None:
    tunnels = flatten_inventory_tunnels(inventory_hosts)
    if not tunnels:
        raise SSHManError("No tunnels defined in inventory.")
    alias = choose_tunnel_alias(inventory_hosts)
    start_tunnel_by_alias(alias)


def choose_host_action(hosts: list[InventoryHost]) -> tuple[str, str]:
    rows = [
        HOST_PREVIEW_LINE.format(
            alias=host.alias,
            user=host.user,
            host=host.host,
            port=host.port,
            group=host.group,
            note=host.note or "",
        )
        for host in hosts
    ]
    key, row = fzf_select(
        rows,
        prompt="host> ",
        preview_command=build_preview_command("host"),
        expect_keys=SELECTOR_KEYS,
    )
    return key, row.split("\t", 1)[0]


def choose_tunnel_alias(hosts: list[InventoryHost]) -> str:
    rows: list[str] = []
    for host in hosts:
        for tunnel in host.tunnels:
            rows.append(
                TUNNEL_PREVIEW_LINE.format(
                    alias=tunnel.alias,
                    via=host.alias,
                    mapping=f"{tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}",
                    status=tunnel_runtime_label(tunnel),
                    note=tunnel.note or "",
                )
            )
    _key, row = fzf_select(
        rows,
        prompt="tunnel> ",
        preview_command=build_preview_command("tunnel"),
        expect_keys="enter",
    )
    return row.split("\t", 1)[0]


def fzf_select(lines: list[str], prompt: str, preview_command: str, expect_keys: str) -> tuple[str, str]:
    ensure_fzf_installed()
    command = [
        "fzf",
        "--prompt",
        prompt,
        "--delimiter",
        "\t",
        "--with-nth",
        "1,2,3,4,5",
        "--expect",
        expect_keys,
        "--preview",
        preview_command,
        "--preview-window",
        "right,60%,wrap",
        "--bind",
        "ctrl-/:toggle-preview",
    ]
    completed = subprocess.run(
        command,
        input="\n".join(lines),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SSHManError("No selection made.")
    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not output_lines:
        raise SSHManError("No selection made.")
    if len(output_lines) == 1:
        return "enter", output_lines[0]
    return output_lines[0], output_lines[1]


def build_preview_command(kind: str) -> str:
    program = f"{shlex.quote(sys.executable)} -m sshman.cli"
    return f"{program} __preview__ {kind} {{1}}"


def render_host_preview(host: InventoryHost) -> str:
    lines = [
        f"alias: {host.alias}",
        f"host: {host.user}@{host.host}:{host.port}",
        f"group: {host.group}",
        f"note: {host.note or '-'}",
        f"proxy_jump: {host.proxy_jump or '-'}",
        f"identity_file: {host.identity_file or DEFAULT_IDENTITY}",
        f"default_tunnels: {', '.join(host.default_tunnels) if host.default_tunnels else '-'}",
        "",
        "tunnels:",
    ]
    if host.tunnels:
        for tunnel in host.tunnels:
            lines.append(
                f"  - {tunnel.alias}: {tunnel.bind_address}:{tunnel.local_port} -> "
                f"{tunnel.target_host}:{tunnel.target_port} [{tunnel_runtime_label(tunnel)}]"
            )
    else:
        lines.append("  - none")
    return "\n".join(lines)


def render_tunnel_preview(host: InventoryHost, tunnel: InventoryTunnel) -> str:
    runtime = tunnel_runtime_info(tunnel)
    lines = [
        f"alias: {tunnel.alias}",
        f"via: {host.alias} ({host.user}@{host.host}:{host.port})",
        f"mapping: {tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}",
        f"note: {tunnel.note or '-'}",
        f"status: {runtime['status']}",
        f"pid: {runtime['pid'] or '-'}",
    ]
    return "\n".join(lines)


def start_default_tunnels_for_host(host: InventoryHost) -> None:
    default_tunnels = resolve_default_inventory_tunnels(host)
    if default_tunnels:
        start_inventory_tunnels(default_tunnels)


def start_inventory_tunnels(tunnels: list[InventoryTunnel]) -> None:
    for tunnel in tunnels:
        if tunnel_is_running(tunnel):
            continue
        start_tunnel_by_alias(tunnel.alias)


def start_tunnel_by_alias(alias: str) -> None:
    ensure_runtime_ready()
    run_interactive_command(["ssh", "-fN", alias])


def flatten_inventory_tunnels(hosts: list[InventoryHost]) -> list[tuple[InventoryHost, InventoryTunnel]]:
    return [(host, tunnel) for host in hosts for tunnel in host.tunnels]


def resolve_default_inventory_tunnels(host: InventoryHost) -> list[InventoryTunnel]:
    tunnel_map = {tunnel.alias: tunnel for tunnel in host.tunnels}
    return [tunnel_map[alias] for alias in host.default_tunnels if alias in tunnel_map]


def show_tunnel_status(hosts: list[InventoryHost]) -> None:
    tunnels = flatten_inventory_tunnels(hosts)
    if not tunnels:
        print("No tunnels")
        return
    print("Tunnel status")
    for host, tunnel in tunnels:
        runtime = tunnel_runtime_info(tunnel)
        mapping = f"{tunnel.bind_address}:{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}"
        pid = runtime["pid"] or "-"
        print(f"  {tunnel.alias:20} {mapping:40} {runtime['status']:8} pid={pid:8} via {host.alias}")


def tunnel_runtime_info(tunnel: InventoryTunnel | TunnelEntry) -> dict[str, str | None]:
    if not tunnel_is_running(tunnel):
        return {"status": "stopped", "pid": None}
    pid = find_listener_pid(tunnel.local_port)
    return {"status": "running", "pid": pid}


def tunnel_runtime_label(tunnel: InventoryTunnel | TunnelEntry) -> str:
    return str(tunnel_runtime_info(tunnel)["status"])


def tunnel_is_running(tunnel: InventoryTunnel | TunnelEntry) -> bool:
    host = "127.0.0.1" if tunnel.bind_address in {"", "0.0.0.0", "*"} else tunnel.bind_address
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex((host, tunnel.local_port)) == 0
    finally:
        sock.close()


def find_listener_pid(port: int) -> str | None:
    completed = run_command(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("p"):
            return line[1:]
    return None


def rename_inventory_alias(hosts: list[InventoryHost], old_alias: str, new_alias: str) -> None:
    if find_inventory_host(hosts, new_alias) is not None or find_inventory_tunnel(hosts, new_alias)[1] is not None:
        raise SSHManError(f"Alias {new_alias!r} already exists.")

    host = find_inventory_host(hosts, old_alias)
    if host is not None:
        host.alias = new_alias
        for other in hosts:
            if other.proxy_jump == old_alias:
                other.proxy_jump = new_alias
        return

    tunnel_host, tunnel = find_inventory_tunnel(hosts, old_alias)
    if tunnel_host is not None and tunnel is not None:
        tunnel.alias = new_alias
        tunnel_host.default_tunnels = [new_alias if alias == old_alias else alias for alias in tunnel_host.default_tunnels]
        return

    raise SSHManError(f"Alias {old_alias!r} not found.")


def remove_inventory_alias(hosts: list[InventoryHost], alias: str) -> None:
    host = find_inventory_host(hosts, alias)
    if host is not None:
        referenced_by = [entry.alias for entry in hosts if entry.proxy_jump == alias]
        if referenced_by:
            raise SSHManError(
                f"Cannot remove host {alias!r}; it is referenced by proxy_jump in: {', '.join(referenced_by)}"
            )
        hosts.remove(host)
        return

    tunnel_host, tunnel = find_inventory_tunnel(hosts, alias)
    if tunnel_host is not None and tunnel is not None:
        tunnel_host.tunnels = [entry for entry in tunnel_host.tunnels if entry.alias != alias]
        tunnel_host.default_tunnels = [entry for entry in tunnel_host.default_tunnels if entry != alias]
        return

    raise SSHManError(f"Alias {alias!r} not found.")


def find_inventory_host(hosts: Iterable[InventoryHost], alias: str) -> InventoryHost | None:
    for host in hosts:
        if host.alias == alias:
            return host
    return None


def require_inventory_host(hosts: Iterable[InventoryHost], alias: str) -> InventoryHost:
    host = find_inventory_host(hosts, alias)
    if host is None:
        raise SSHManError(f"Host alias {alias!r} not found.")
    return host


def find_inventory_tunnel(
    hosts: Iterable[InventoryHost], alias: str
) -> tuple[InventoryHost | None, InventoryTunnel | None]:
    for host in hosts:
        for tunnel in host.tunnels:
            if tunnel.alias == alias:
                return host, tunnel
    return None, None


def init_config(force: bool = False) -> None:
    ensure_ssh_dirs()
    if not CONFIG_PATH.exists():
        write_file(CONFIG_PATH, render_main_config())
    else:
        ensure_include_line(CONFIG_PATH)
    ensure_managed_file(HOSTS_PATH, force)
    ensure_managed_file(TUNNELS_PATH, force)
    ensure_permissions()


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
    if path.exists():
        if not force:
            current = path.read_text(encoding="utf-8")
            if "Managed by sshman" not in current:
                raise SSHManError(f"{path} already exists and is not managed by sshman.")
            return
        write_file(path, MANAGED_HEADER)
        return
    write_file(path, MANAGED_HEADER)


def ensure_include_line(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if INCLUDE_LINE in text:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"\n{INCLUDE_LINE}\n"
    write_file(path, text)


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


def render_tunnel_entry(entry: TunnelEntry, via_host: HostEntry) -> str:
    local = f"{entry.bind_address}:{entry.local_port}" if entry.bind_address else str(entry.local_port)
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


def build_scp_command(host: InventoryHost, recursive: bool, source: str, destination: str) -> list[str]:
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


def backups_exist() -> bool:
    return BACKUPS_DIR.exists() and any(BACKUPS_DIR.iterdir())


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
    command.append(
        "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
        "grep -qxF \"$KEY\" ~/.ssh/authorized_keys || echo \"$KEY\" >> ~/.ssh/authorized_keys"
    )
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


def ensure_fzf_installed() -> None:
    if not shutil.which("fzf"):
        raise SSHManError("fzf is required for sshm interactive selection. Install it first.")


def open_in_editor(path: Path, line: int | None = None) -> None:
    editor = os.environ.get("EDITOR", "vi")
    parts = shlex.split(editor)
    if not parts:
        raise SSHManError("EDITOR is empty.")
    binary = parts[0]
    base = Path(binary).name
    command = parts[:]
    if line:
        if base in {"vi", "vim", "nvim"}:
            command.append(f"+{line}")
        elif base == "nano":
            command.append(f"+{line}")
        elif base in {"code", "cursor", "subl"}:
            command.append(f"{path}:{line}")
            run_interactive_command(command)
            return
        else:
            print(f"Opening inventory. Target host starts near line {line}.")
    command.append(str(path))
    run_interactive_command(command)


def prompt(text: str) -> str:
    return input(text)


if __name__ == "__main__":
    main()
