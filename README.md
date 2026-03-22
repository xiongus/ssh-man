# SSHMan 1.0

Terminal-first SSH inventory manager for macOS/Linux.

## 1.0 Principles

- one source of truth: `~/.config/sshman/inventory.yaml`
- one interactive entrypoint: `sshm`
- one formal compatibility command: `sshman`
- one required selector dependency: `fzf`
- no GUI
- no recent-session state machine
- no implicit "start every tunnel"

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

Edit:

```bash
~/.config/sshman/inventory.yaml
```

Sync inventory into SSH config:

```bash
sshm sync --on-conflict update
```

Daily usage:

```bash
sshm
sshm jump-box
sshm ls
sshm t
sshm t jump-box --default
sshm t --status
sshm cp jump-box ./app.jar :/root/app.jar
sshm cp jump-box :/var/log/app.log ./app.log
sshm x jump-box "hostname"
sshm doctor
```

## Commands

- `sshm`
  Open the host selector with `fzf`
- `sshm <alias>`
  Connect directly to a host and start that host's default tunnels first
- `sshm ls`
  List hosts and tunnels
- `sshm t`
  Pick a tunnel interactively and start it
- `sshm t <host> --default`
  Start the host's default tunnels
- `sshm t <host> --all`
  Start all tunnels under that host
- `sshm t --status`
  Show tunnel running/stopped status
- `sshm cp`
  Copy files to or from a host
- `sshm x`
  Run a remote command
- `sshm mv`
  Rename a host or tunnel alias
- `sshm rm`
  Remove a host or tunnel
- `sshm sync`
  Import inventory into managed SSH config
- `sshm gen`
  Write the default inventory template
- `sshm doctor`
  Validate dependencies and managed SSH state

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
- password fields are only used when `sshm sync --use-passwords` is passed
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
sshm sync --on-conflict update
sshm doctor
```
