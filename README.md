# SSHMan 1.0.1

Terminal-first SSH inventory manager for macOS/Linux.

## 1.0.1 Principles

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

Daily usage:

```bash
sshm
sshm jump-box
sshm edit jump-box
sshm t
sshm t jump-box --default
sshm t --status
sshm doctor
```

## Main Commands

- `sshm`
  Open the host selector
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
  Show tunnel runtime status with PID when available
- `sshm edit`
  Open the default inventory
- `sshm edit <alias>`
  Open the inventory and jump near that host
- `sshm mv`
  Rename a host or tunnel in inventory, then sync
- `sshm rm`
  Remove a host or tunnel from inventory, then sync
- `sshm sync`
  Render inventory into `~/.ssh/config.d`
- `sshm gen`
  Write the default inventory template
- `sshm doctor`
  Validate `fzf`, inventory, and managed SSH state

## Selector UX

`sshm` uses `fzf` as the primary interaction surface.

Host selector keys:

- `Enter`
  Connect
- `Ctrl-T`
  Start the selected host's default tunnels
- `Ctrl-E`
  Edit the selected host in inventory
- `Ctrl-R`
  Rename the selected host
- `Ctrl-D`
  Delete the selected host after confirmation
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
- tunnel summaries and runtime state

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

Notes:

- `password:` is optional
- passwords are only used when you explicitly run `sshm sync --use-passwords`
- if you use password-assisted bootstrap, `sshpass` must be installed
- `default_tunnels` must reference tunnels defined under the same host

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
