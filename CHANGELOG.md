# Changelog

## 1.0.2

- added query-driven entry so `sshm <query>` can prefill the selector or connect directly on a single match
- stabilized host selector enter behavior around single-selection
- fixed `fzf` enter parsing so filtered selections connect the highlighted host reliably
- added `sshm edit` change detection with a sync prompt after editing
- upgraded tunnel status with color, uptime, PID, `--running`, `--dead`, and `--watch`
- added `Ctrl-P` reachability probes in the host selector preview
- added support for `default_tunnels: ["*"]`
- added automatic inventory backups before every `sshm sync`
- improved `--use-passwords` reporting so bootstrap successes and failures are reported per host
- added `sshm backup list` and `sshm backup restore <timestamp>`
- added `sshm completion zsh|bash|fish`
- expanded selector behavior to support multi-select host and tunnel actions

## 1.0.1

- rebuilt the CLI around the finalized 1.0.1 UX-first model
- made `inventory.yaml` the actual management surface for `ls`, selector flows, `mv`, `rm`, and `edit`
- removed legacy long command names from the public CLI and kept only the short 1.0.1 surface
- added `sshm edit` and `sshm edit <alias>`
- upgraded the host selector to use `fzf` preview plus action keys for connect, default tunnels, edit, rename, and delete
- fixed hidden preview routing so the `fzf` preview pane works correctly
- tightened `sshm sync` into a full inventory-to-config render instead of incremental import semantics
- expanded tunnel status to show runtime state and PID when available
- rewrote the README around the 1.0.1 golden path and selector workflow

## 1.0.0

- finalized the 1.0 command model around `sshm`
- added a short-command public surface: `ls`, `t`, `cp`, `x`, `mv`, `rm`, `sync`, `gen`, `doctor`
- made bare `sshm` open the host selector and `sshm <alias>` connect directly
- made `fzf` a required dependency for interactive selection
- added host-level `default_tunnels`
- added tunnel running/stopped status with `sshm t --status`
- installed both `sshm` and `sshman` wrappers
- rewrote the documentation around the 1.0 golden path

## 0.9.0

- changed sshman to preserve an existing `~/.ssh/config` and only add an `Include ~/.ssh/config.d/*.conf` line
- moved the default inventory location to `~/.config/sshman/inventory.yaml`
- updated `template` and `import` to use the default inventory path when `--file` is omitted
- removed the sample repo-local `inventory.yaml` to avoid leaking host definitions into source control

## 0.8.0

- reduced the public CLI surface to a smaller golden-path command set
- replaced `copy-to` and `copy-from` with a single `copy` command
- removed public `init`, `add-host`, `add-tunnel`, `show`, `check`, `backup`, `bootstrap-key`, and `onboard-host` commands
- made inventory import auto-initialize managed SSH config when needed

## 0.7.0

- replaced CSV import with a single YAML inventory import flow
- added `template` to generate the recommended inventory file
- added optional password fields for one-time bootstrap during YAML import
- refactored inventory models into dedicated modules

## 0.6.0

- added `rename` for managed host and tunnel aliases
- added `doctor` for richer SSH environment diagnostics
- added `import-csv --on-conflict` with `error`, `skip`, and `update` modes
- updated host rename behavior to keep dependent tunnels aligned

## 0.5.0

- added `copy-to` and `copy-from` for common `scp` workflows
- added `exec` for one-off remote commands
- added `remove` for deleting managed hosts or tunnels
- made recursive directory copy explicit with `--recursive`
- blocked host removal when tunnels still depend on that host

## 0.4.0

- added `scripts/install.sh` for self-contained system-wide installation
- added `scripts/uninstall.sh` for cleanup
- documented fixed-location installation that does not depend on the source checkout

## 0.3.0

- added `connect` to open a managed SSH host directly
- added `tunnel` to start a managed tunnel directly
- added `list --simple` for piping into tools like `fzf`
- documented the one-time editable install workflow in the README

## 0.2.0

- added `bootstrap-key` to deploy a local public key to a remote host
- added `onboard-host` to bootstrap key auth and write a managed host entry in one flow
- added automatic SSH key creation when the local identity file is missing
- documented onboarding and bootstrap workflows in the README

## 0.1.0

- created the initial `sshman` offline CLI
- added `init`, `add-host`, `add-tunnel`, `list`, `show`, `check`, `backup`, and `import-csv`
- added support for managed `~/.ssh/config` and `config.d` layout
