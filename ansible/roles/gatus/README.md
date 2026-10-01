# gatus

Outside watcher (Phase 10b3): [Gatus](https://github.com/TwiN/gatus) as an unprivileged, hardened systemd service with a pinned static binary, SQLite history, declarative config and a tailnet-only listener. Alerts go to a direct Discord webhook (never Hermod). A dead-man's switch is an external endpoint that Frigg pushes to (`roles/gatus-heartbeat`).

- Required: `gatus_listen_address` (this host's tailnet IP). Secrets (operator-seeded, role fails early if absent): Vault `secret/ansible/do1/gatus-discord` (`webhook_url`) and `secret/ansible/do1/gatus-heartbeat` (`token`).
- Probe lists are host data: `gatus_public_endpoints`, `gatus_tcp_endpoints` (see `group_vars/offsite.yml`).
- Upstream has no release binaries; the pin chain (image manifest -> layer -> binary sha256) is documented in `defaults/main.yml`.
- Procedure, verification and operation: `docs/procedures/offsite-watcher.md`.
