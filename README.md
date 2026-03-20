# sshman

Offline SSH config and tunnel manager for macOS/Linux terminals.

## What it does

- Initializes a clean `~/.ssh/config` layout with `config.d`
- Adds SSH host entries
- Adds SSH tunnel entries
- Bootstraps SSH public-key auth for first-time server setup
- Lets you connect to a managed host or start a managed tunnel directly
- Backs up managed config files before changes
- Lists and shows managed entries
- Runs basic checks for alias conflicts, ports, permissions, and agent status
- Imports hosts or tunnels from CSV

## What it does not do

- It does not replace `ssh`
- It does not store server passwords
- It is designed around native `ssh`, SSH keys, and macOS Keychain

## Install

System-wide install:

```bash
cd /Users/xiongus/Documents/initiate
sudo ./scripts/install.sh
```

This copies `sshman` into a fixed system location and creates `/usr/local/bin/sshman`, so the source checkout is no longer required after install.

Remove it later with:

```bash
sudo ./scripts/uninstall.sh
```

Editable local development install:

```bash
cd /Users/xiongus/Documents/initiate
python3 -m pip install -e .
```

## Usage

```bash
sshman init
sshman add-host --alias s36 --host 192.168.78.36 --user root --note "Primary jump box"
sshman add-tunnel --alias t-s16-8001 --via s36 --local-port 18001 --target-host 100.124.241.16 --target-port 8001
sshman list
sshman show s36
sshman connect s36
sshman tunnel t-s16-8001
sshman check
```

If you omit the alias, `sshman connect` and `sshman tunnel` can prompt you to choose from the saved list.

For shell pipelines or fuzzy pickers:

```bash
sshman list --type host --simple
sshman list --type tunnel --simple
```

First-time password-based onboarding:

```bash
sshman onboard-host --alias s36 --host 192.168.78.36 --user root --note "Primary jump box"
```

For an existing host you just want to convert to key-based login:

```bash
sshman bootstrap-key --alias s36 --host 192.168.78.36 --user root
```

These commands will:

- create `~/.ssh/id_ed25519` if missing
- prompt you once through `ssh-copy-id` or native `ssh`
- verify that key-based login now works
- keep server passwords out of local config files

You can also run it without installing the console script:

```bash
python3 -m sshman.cli --help
```

The editable install is best for development. If you want a self-contained machine install, use `./scripts/install.sh` instead.

## CSV import

Host CSV headers:

```csv
alias,host,user,port,group,identity_file,note,proxy_jump
```

Tunnel CSV headers:

```csv
alias,via,local_port,target_host,target_port,bind_address,note
```

## Notes

- `onboard-host` and `bootstrap-key` use `ssh-copy-id` if available
- without `ssh-copy-id`, `sshman` falls back to native `ssh`
- passwords are used only for the bootstrap session and are not stored
