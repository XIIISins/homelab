<!-- docs/outline/services-and-purpose/factorio.md -->

# Factorio

A Factorio headless game server, run by a trusted friend who has no shell on the machine. It is the template for operator-managed services in this homelab.

![Factorio: a game server a friend runs through SFTP only](/api/attachments.redirect?id=899cb378-7226-4635-a91f-662a42afc8d2)

---

## The model

The container (LXC 1120 on Urd, `10.0.11.220`) runs the game server and **SFTPGo** side by side. The operator logs into SFTPGo only: no SSH, no web UI. They control the server entirely by editing files over SFTP.

| Path (under `/factorio/`) | Operator can | Purpose |
|---|---|---|
| `mods/`, `saves/`, `scenarios/` | read/write | Game content |
| `control/factorio-control.json` | read/write | Desired state: `version`, `state` (running or stopped), `restart` |
| `control/restart-now` | create | Touching the file triggers an immediate restart |
| `status/factorio-status.json` | read | What the reconciler actually did |
| `logs/`, `script-output/` | read | Logs and game output |

---

## The reconcile loop

A root-owned systemd timer runs a small Python script every 30 seconds. It reads the control file, resolves "stable" or "experimental" to a concrete version through the Factorio API, downloads and checksum-verifies missing versions into versioned directories, swaps the active symlink atomically, makes the service match the requested state, and writes the status file. The operator declares intent; the machine converges. The same declare-then-converge shape is used everywhere else in the homelab.

The RCON password is never in the operator's tree: a fresh one is generated on every service start, and the server is launched with a shim that scrubs it from the process list.

---

## Access

- Game port: UDP 34197, public via a UCG-Ultra port-forward to the LXC, with `factorio.xiiisins.com` resolving straight to the container for LAN clients (a bypass rewrite).
- Admin: SFTP for the operator; Ansible for the platform side.

---

## See also

- **Compute & hypervisors** (Components): why game servers are LXCs.
- **GitOps & automation** (Components): the Ansible role and playbook.
- **Factorio diagram** (Diagrams): the doors, the reconciler and the shared folder on one sheet.
