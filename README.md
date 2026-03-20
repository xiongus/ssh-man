# sshman

Offline SSH inventory manager for macOS/Linux terminals.

## What it does

- imports hosts and tunnels from one YAML inventory
- generates a starter inventory template
- connects to managed hosts and tunnels
- supports copy, exec, rename, remove, and doctor workflows
- can optionally use password fields once during import for key bootstrap

## What it does not do

- it does not replace `ssh`
- it does not store passwords outside the inventory file
- it does not aim to be a full terminal or deployment platform

## Install

System-wide install:

```bash
cd /Users/xiongus/Documents/initiate
sudo ./scripts/install.sh
```

Remove it later with:

```bash
sudo ./scripts/uninstall.sh
```

## Golden Path

```bash
sshman template --file inventory.yaml
sshman import --file inventory.yaml --on-conflict update
sshman list
sshman connect jump-box
sshman tunnel t-s16-8001
sshman copy jump-box ./app.jar :/root/app.jar
sshman copy jump-box :/var/log/app.log ./app.log
sshman exec jump-box "hostname"
sshman doctor
```

If you omit the alias, `sshman connect` and `sshman tunnel` can prompt you to choose from the saved list.

For shell pipelines or fuzzy pickers:

```bash
sshman list --type host --simple
sshman list --type tunnel --simple
```

For directories, use `--recursive` explicitly:

```bash
sshman copy jump-box ./dist :/opt/app/dist --recursive
sshman copy jump-box :/opt/app/logs ./logs --recursive
```

## Inventory

`sshman` supports one import format only: YAML.

Write a starter inventory:

```bash
sshman template --file inventory.yaml
```

Import it:

```bash
sshman import --file inventory.yaml --on-conflict error
sshman import --file inventory.yaml --on-conflict skip
sshman import --file inventory.yaml --on-conflict update
```

If you choose to keep passwords in the inventory for first-time bootstrap only:

```bash
sshman import --file inventory.yaml --on-conflict update --use-passwords
```

Minimal example:

```yaml
hosts:
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
```

## Notes

- password fields are optional and only used when `--use-passwords` is passed
- if passwords are present and you use `--use-passwords`, `sshpass` must be installed
- editable local development is still possible with `python3 -m pip install -e .`
