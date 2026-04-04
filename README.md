# SSHMan 1.0.2

Terminal-first SSH inventory manager for macOS/Linux.

## 1.0.2 Principles

- one source of truth: `~/.config/sshman/inventory.yaml`
- one primary entrypoint: `sshm`
- `fzf` is required, not optional
- no GUI
- no recent-session state machine
- no implicit "start every tunnel"
- inventory drives everything; generated SSH config is only the runtime output

## Install

Install `fzf` first:

```bash
brew install fzf
$(brew --prefix)/opt/fzf/install
```

Install SSHMan system-wide:

```bash
cd /Users/xiongus/Documents/initiate
sudo ./scripts/install.sh
```

This installs both:

- `sshm`
- `sshman`

Remove later with:

```bash
sudo ./scripts/uninstall.sh
```

## Golden Path

Generate the default inventory:

```bash
sshm gen
```

Open the inventory:

```bash
sshm edit
```

Render inventory into SSH config:

```bash
sshm sync
```

If you want inventory passwords to be used for one-time key bootstrap, run:

```bash
sshm sync --use-passwords
```

Daily usage:

```bash
sshm
sshm prod
sshm jump-box
sshm edit jump-box
sshm t
sshm t jump-box --default
sshm t --status
sshm t --status --watch
sshm doctor
```

## Main Commands

- `sshm`
  Open the host selector
- `sshm <query>`
  Pre-fill `fzf` with a query; if one host matches, connect directly
- `sshm <alias>`
  Connect directly to one host and start its default tunnels first
- `sshm ls`
  Show hosts and tunnels from inventory
- `sshm t`
  Open the tunnel selector
- `sshm t <host> --default`
  Start that host's default tunnels
- `sshm t <host> --all`
  Start all tunnels under one host
- `sshm t --status`
  Show colored tunnel runtime status
- `sshm t --status --watch`
  Refresh the status table continuously
- `sshm edit`
  Open the default inventory and prompt to sync if it changed
- `sshm edit <alias>`
  Open the inventory and jump near that host
- `sshm backup list`
  Show saved inventory backups
- `sshm backup restore <timestamp>`
  Restore one inventory backup and sync it
- `sshm completion zsh|bash|fish`
  Print shell completion scripts
- `sshm sync`
  Render inventory into `~/.ssh/config.d`
- `sshm gen`
  Write the default inventory template
- `sshm doctor`
  Validate `fzf`, inventory, backups, and managed SSH state

## Selector UX

`sshm` uses `fzf` as the primary interaction surface.

Host selector keys:

- `Enter`
  Connect the selected host, or open all selected hosts if you multi-select
- `Tab`
  Multi-select hosts
- `Ctrl-T`
  Start the selected host's default tunnels
- `Ctrl-E`
  Edit the selected host in inventory
- `Ctrl-R`
  Rename the selected host
- `Ctrl-D`
  Delete the selected host after confirmation
- `Ctrl-P`
  Probe the selected host and refresh preview reachability
- `Ctrl-/`
  Toggle preview

The preview pane shows:

- alias
- `user@host:port`
- group
- note
- proxy jump
- identity file
- default tunnels
- reachability result from `Ctrl-P`
- tunnel runtime state, PID, and uptime

## Inventory

Default inventory path:

```bash
~/.config/sshman/inventory.yaml
```

Minimal example:

```yaml
hosts:
  - alias: jump-box
    host: 192.168.78.36
    user: root
    port: 22
    group: work
    note: Main jump host
    proxy_jump:
    identity_file: ~/.ssh/id_ed25519
    password:
    default_tunnels:
      - t-db
    tunnels:
      - alias: t-db
        local_port: 13306
        target_host: 10.0.0.10
        target_port: 3306
        bind_address: 127.0.0.1
        note: Main DB
```

Wildcard defaults are supported:

```yaml
default_tunnels:
  - "*"
```

That means "start every tunnel under this host" and only applies when you explicitly write `*`.

Notes:

- `password:` is optional
- passwords are ignored by plain `sshm sync` and only used when you explicitly run `sshm sync --use-passwords`
- `sshm sync --use-passwords` now skips hosts that already have working key-based login, and caches successful bootstrap state under `~/.config/sshman/bootstrap-state.json`
- if you use password-assisted bootstrap, `sshpass` must be installed
- `default_tunnels` must either reference real tunnel aliases or be exactly `["*"]`

## Edit And Sync

By default, `sshm edit` compares the inventory before and after your editor exits.

If it changed, SSHMan asks:

```text
Inventory changed. Sync now? [Y/n/always/never]
```

You can control this with:

- `--no-prompt`
- `SSHMAN_AUTO_SYNC_PROMPT=always`
- `SSHMAN_AUTO_SYNC_PROMPT=never`

## Tunnel Status

`sshm t --status` shows:

- alias
- local bind and target
- status
- PID
- uptime
- via host
- note

Useful flags:

```bash
sshm t --status --running
sshm t --status --dead
sshm t --status --watch
sshm t --status --watch --watch-interval 2
```

## Backups

Every `sshm sync` creates an inventory backup at:

```bash
~/.config/sshman/backup/
```

Backups are timestamped and only the newest 30 are kept by default.

Useful knobs:

- `SSHMAN_BACKUP_KEEP=50`
- `SSHMAN_BACKUP_ENABLED=false`

Commands:

```bash
sshm backup list
sshm backup restore 20260322-120000
```

## Query And Probe Behavior

Useful knobs:

- `SSHMAN_SINGLE_MATCH_CONNECT=0`
  Always open `fzf`, even if one host matches
- `SSHMAN_PROBE_METHOD=tcp|ping|ssh`
- `SSHMAN_PROBE_TIMEOUT=3`
- `SSHMAN_WATCH_INTERVAL=4`

## Shell Completion

Print a completion script:

```bash
sshm completion zsh
sshm completion bash
sshm completion fish
```

Example for `zsh`:

```bash
sshm completion zsh > ~/.zsh/completions/_sshm
```

## SSH Config Behavior

SSHMan does not replace your main:

```bash
~/.ssh/config
```

It only ensures this line exists:

```sshconfig
Include ~/.ssh/config.d/*.conf
```

SSHMan manages:

- `~/.ssh/config.d/hosts.conf`
- `~/.ssh/config.d/tunnels.conf`

## Skill

If you use Codex, the companion skill is:

- `$sshman-maintainer`

It should maintain the inventory file, then run:

```bash
sshm sync
sshm doctor
```
