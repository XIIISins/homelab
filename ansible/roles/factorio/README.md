# ansible-role-factorio

Deploys a headless Factorio dedicated server with an operator self-service
control surface. Designed for an LXC where someone other than the homelab
admin manages day-to-day server state (mods, version, restart) via SFTP only.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ Operator (SFTP)                                     │
│  └─ edits /factorio/control/factorio-control.json   │
│  └─ uploads/deletes /factorio/mods/*.zip            │
│  └─ touches /factorio/control/restart-now           │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ LXC                                                 │
│                                                     │
│  factorio-reconcile.timer  (every 30s)              │
│     └─► factorio-reconcile.service                  │
│         └─► /usr/local/bin/factorio-reconcile       │
│              ├─ reads control file                  │
│              ├─ installs/swaps factorio versions    │
│              ├─ writes status file + logs           │
│              └─ restarts factorio.service           │
│                                                     │
│  factorio-restart-now.path  (instant)               │
│     └─► factorio-restart-now.service                │
│         └─► systemctl restart factorio              │
│                                                     │
│  factorio.service  (the game)                       │
│     └─► /usr/local/bin/factorio-launch              │
│         └─► /opt/factorio/bin/x64/factorio          │
└─────────────────────────────────────────────────────┘
```

## File layout on disk

```
/factorio/                 # operator's world via SFTP
├── README.txt
├── mods/                  # mod zips
├── saves/                 # save files (latest is auto-loaded)
├── scenarios/
├── script-output/
├── config/                # server-settings.json, lists
├── logs/                  # factorio.log + reconcile.log (operator-readable)
└── control/
    ├── factorio-control.json    # operator-writable desired state
    ├── factorio-status.json     # operator-readable observed state
    └── restart-now              # touch to restart instantly

/opt/factorio              # symlink → /opt/factorio-<version>
/opt/factorio-2.0.76/      # extracted tarball
/opt/factorio-2.0.75/      # previous version (kept for rollback)

/usr/local/bin/factorio-reconcile    # reconcile script
/usr/local/bin/factorio-launch       # launch wrapper

/etc/systemd/system/factorio.service
/etc/systemd/system/factorio-reconcile.{service,timer}
/etc/systemd/system/factorio-restart-now.{path,service}

/etc/logrotate.d/factorio
/var/cache/factorio-reconcile/       # API response cache
/var/lib/factorio-secrets/           # RCON password — outside operator's SFTP tree
```

## Variables

See `defaults/main.yml`. Key ones:

| Variable                         | Default              | Purpose |
| -------------------------------- | -------------------- | ------- |
| `factorio_initial_version`       | `stable`             | Version written into factorio-control.json on first deploy |
| `factorio_port`                  | `34197`              | Game UDP port |
| `factorio_rcon_port`             | `27015`              | RCON TCP port (LAN-internal) |
| `factorio_rcon_password`         | `""` (auto-generate) | Override with a vault lookup if you need RCON access |
| `factorio_server_name`           | `Factorio Server`    | Surfaced in server browser |
| `factorio_visibility_public`     | `false`              | Whether to list publicly |
| `factorio_force_overwrite_server_settings` | `false`    | Set true once to push Ansible-managed config over operator edits |

## Control file schema

The operator's contract lives at `/factorio/control/factorio-control.json`:

```json
{
  "version": "stable",     // "stable" | "experimental" | "2.0.76"
  "state":   "running",    // "running" | "stopped"
  "restart": false         // one-shot: reconcile sets back to false
}
```

The reconciler reads this every 30s, converges the system to match, and
writes observed state to `/factorio/control/factorio-status.json`. When
`state=stopped`, the `restart` flag and the `restart-now` trigger are both
no-ops — operator-set stopped state wins.

## Usage

```yaml
- hosts: gameserver
  become: true
  roles:
    - role: factorio
      vars:
        factorio_server_name: "niflheim.xiiisins.com — Factorio"
        factorio_admins:
          - your-factorio-username
        factorio_initial_version: "2.0.76"  # or "stable"
```

## What the role does NOT do

- **Install SFTPGo.** Use the separate `sftpgo` role and configure it to
  expose `/factorio/` to the operator's virtual user.
- **Open firewall ports.** UCG-Ultra port forwards for UDP 34197 (game) and
  TCP 22022 (SFTPGo) are configured out-of-band in the UCG UI.
- **Manage saves or mods.** The operator owns those via SFTP.
- **Auto-update mods.** v1 scope deliberately excludes this; operator
  manages mod files manually.

## Notes

- Reconciler runs as root (it needs to write to `/opt/` and call
  `systemctl`). All file writes under `/factorio/` are chown'd back to the
  factorio user.
- First run takes a while (downloads the Factorio tarball, ~200MB).
  Subsequent runs are no-ops unless version drift is detected.
- The reconciler keeps the current install + one previous version on disk
  for fast rollback.
- Mod auto-update is on the roadmap but deferred from v1.
