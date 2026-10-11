<!-- docs/outline/diagrams/factorio.md -->

# Factorio diagram

A game server a friend runs through SFTP only. No shell, no web UI: the operator edits JSON control files, and a root timer makes the server match them.

![Factorio: a game server a friend runs through SFTP only](/api/attachments.redirect?id=899cb378-7226-4635-a91f-662a42afc8d2)

---

## How to read it

- **Two doors, both port-forwards.** Players reach the game server over UDP; the operator reaches SFTPGo over TCP. These are the only things exposed to the internet and neither goes through Traefik. The public name is a Cloudflare A record; the LAN name is an AdGuard rewrite.
- **Inside the LXC (dashed box).** The headless Factorio server, SFTPGo (SFTP only, per-folder rights, brute-force defender, optional TOTP) and the `factorio-reconcile` timer.
- **The reconciler.** A root systemd timer every 30 seconds, Python standard library only. It reads the control file, starts, stops or upgrades the server to match, writes status, and downloads and SHA256-verifies new versions from factorio.com.
- **The shared `/factorio/` tree** (group-shared, setgid). `mods/`, `saves/` and `scenarios/` are operator read-write. `control/factorio-control.json` holds the version, the desired state and a restart-now flag. `status/` and `logs/` are written by the reconciler and read-only for the operator. `script-output/` is written by Factorio.
- **RCON password.** New on every start, kept outside the SFTP tree and scrubbed from the process list.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[factorio.excalidraw 46106](/api/attachments.redirect?id=44c9f2f3-db76-4dee-9140-80f1baa0d2ea)

[factorio.svg 54492](/api/attachments.redirect?id=ef1a6483-b91b-4961-85f8-623c7b6b037b)

---

## See also

- **Factorio** (Services): the service page.
- **Edge** (Components): the non-HTTP path through the UCG-Ultra.
