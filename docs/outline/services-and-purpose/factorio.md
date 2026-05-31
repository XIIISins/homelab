<!-- docs/outline/services-and-purpose/factorio.md -->

# Factorio

A dedicated Factorio multiplayer server for the friend group. Interesting less for the game and more for the **self-service pattern** it pioneered — a template the homelab reuses for any operator-managed service where someone other than the platform owner needs to make changes.

---

## Where it runs

Factorio runs in **LXC 1120** (`10.0.11.220`) — a normal unprivileged service LXC. The game server itself is self-contained: no database, no Authentik, no shared dependencies. It listens on the standard Factorio UDP game port.

---

## The self-service pattern

The design goal: let a trusted operator manage the server (upload saves, change settings, swap mods) **without ever giving them a shell** on the box. The pattern has three pieces:

1. **SFTPGo** gives the operator SFTP-only access into `/factorio/`. They can drop files and edit JSON control files — but they get no login shell, no `sudo`, no ability to run arbitrary code.
2. **JSON control files** are the interface. The operator expresses desired state (which save is active, server settings, mod list) by editing those files over SFTP.
3. **A root-owned Python reconcile loop** (a systemd timer firing every 30 seconds) reads the control files and converges the actual server state to match — starting, stopping, or reconfiguring the game server as needed.

The operator describes *what they want*; the reconcile loop makes it so. It's the same converge-to-desired-state philosophy as Flux and Patroni, applied at the single-service level, with a hard security boundary (SFTP, no shell) in the middle.

This is the **template for future operator-managed services** — game servers, voice servers, anything where a non-platform-owner needs day-to-day control without root.

---

## See also

- **Compute & hypervisors** (Components) — the LXC model Factorio runs in.
- **GitOps & automation** (Components) — the same converge-to-desired-state idea at the fleet level.
- **Services and purpose** — the catalog this is part of.
