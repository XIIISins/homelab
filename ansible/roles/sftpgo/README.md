# ansible-role-sftpgo

Deploys SFTPGo from the official OSU mirror APT repo, configures it via
environment variables (per upstream recommendation), bootstraps an admin
account, and declaratively manages virtual users + permissions via the
REST API.

Generic — same role serves Factorio, Minecraft, anything else that needs
operator-managed file access. Per-LXC, pass `sftpgo_users` and
`sftpgo_extra_groups` to plug into local services.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Operator (SFTP client over the internet)                   │
│   └─ sftp -P 22022 operator@host                            │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  LXC                                                        │
│                                                             │
│  sftpgo.service  (system user: sftpgo)                      │
│   ├─ SFTP daemon on 0.0.0.0:22022                           │
│   ├─ Web admin on 127.0.0.1:8080 (SSH tunnel for mgmt)      │
│   ├─ Web client on 127.0.0.1:8081 (disabled v1)             │
│   ├─ Data provider: sqlite at /var/lib/sftpgo/sftpgo.db     │
│   ├─ Defender enabled (rate-limit + temp-ban)               │
│   └─ TOTP available (per-user opt-in)                       │
│                                                             │
│  System: sftpgo user is member of {extra_groups}            │
│  → Files created by SFTPGo in shared paths get the          │
│    consumer-service group via setgid bits; both can         │
│    read/write via group=0664 + UMask=0002.                  │
└─────────────────────────────────────────────────────────────┘
```

## Variables

See `defaults/main.yml` for the full list. Required for any deployment:

| Variable                  | Notes                                              |
| ------------------------- | -------------------------------------------------- |
| `sftpgo_users`            | List of virtual users to provision via API.        |
| `sftpgo_admin_password`   | Plaintext — typically from a vault lookup. If empty, a 32-char password is generated and persisted to `/etc/sftpgo/admin-password.txt` (root-only). |
| `sftpgo_extra_groups`     | List of OS groups to add the `sftpgo` user to so it can write files in paths owned by another service. e.g. `['factorio']`. |

Common overrides:

| Variable                          | Default              |
| --------------------------------- | -------------------- |
| `sftpgo_sftp_bind_port`           | `22022`              |
| `sftpgo_admin_username`           | `admin`              |
| `sftpgo_web_admin_bind_address`   | `0.0.0.0`            |
| `sftpgo_web_client_enabled`       | `false` (deferred)   |
| `sftpgo_defender_enabled`         | `true`               |

## Virtual user format

Each entry in `sftpgo_users` maps directly to SFTPGo's user model:

```yaml
sftpgo_users:
  - username: factorio-operator
    description: "Buddy who manages the Factorio server."
    # Read at runtime from HashiCorp Vault (the standard rail for runtime secrets):
    password: "{{ lookup('community.hashi_vault.vault_kv2_get', 'ansible/factorio/operator-password').secret.value }}"
    # Optionally also accept SSH key auth (either method works):
    public_keys:
      - "ssh-ed25519 AAAA... operator@laptop"
    home_dir: /factorio
    permissions:
      "/":         ["list", "download"]
      "/mods":     ["list", "download", "upload", "overwrite", "delete", "rename", "create_dirs"]
      "/saves":    ["list", "download", "upload", "overwrite", "delete", "rename"]
      "/config":   ["list", "download", "upload", "overwrite", "rename"]
      "/control":  ["list", "download", "upload", "overwrite"]
      "/logs":     ["list", "download"]
```

You can supply `password`, `public_keys`, or both. SFTPGo accepts whichever
the client offers. Password auth is operator-friendlier; pubkey is more
secure. With both configured, the operator can use either.

Fields supported (passed through to the SFTPGo API verbatim):
- `username` (required)
- `status` (1 = enabled, default 1)
- `password` (plaintext; prefer `public_keys` for SFTP)
- `public_keys` (list)
- `home_dir` (default: `/var/lib/sftpgo/data/<username>`)
- `permissions` (dict: path → list of permission strings)
- `virtual_folders` (list — for mounting paths outside `home_dir`)
- `quota_size`, `quota_files`, `max_sessions`
- `description`

Valid permission strings:
`*`, `list`, `download`, `upload`, `overwrite`, `delete`, `delete_files`,
`delete_dirs`, `rename`, `rename_files`, `rename_dirs`, `create_dirs`,
`create_symlinks`, `chmod`, `chown`, `chtimes`.

## Usage with the factorio role

```yaml
- hosts: factorio_lxc
  become: true
  roles:
    - role: factorio

    - role: sftpgo
      vars:
        sftpgo_extra_groups: [factorio]
        # sftpgo_admin_password defaults to a HashiCorp Vault lookup
        # (secret/ansible/sftpgo/admin-password); override only for a custom bootstrap.
        sftpgo_users:
          - username: operator
            description: "Factorio server operator"
            password: "{{ lookup('community.hashi_vault.vault_kv2_get', 'ansible/factorio/operator-password').secret.value }}"
            home_dir: /factorio
            permissions:
              "/":        ["list", "download"]
              "/mods":    ["list", "download", "upload", "overwrite", "delete", "rename", "create_dirs"]
              "/saves":   ["list", "download", "upload", "overwrite", "delete", "rename"]
              "/config":  ["list", "download", "upload", "overwrite", "rename"]
              "/control": ["list", "download", "upload", "overwrite"]
              "/logs":    ["list", "download"]
```

## What this role does NOT do

- **Open firewall ports.** Configure the UCG port-forward for SFTP (e.g.
  TCP 22022) manually.
- **Provision TLS certificates.** Web UI binds plaintext loopback by default;
  put a reverse proxy in front when you expose the web client.
- **Manage Authentik/OIDC integration.** Deferred until the Authentik milestone.
- **Manage TLS for the SFTP service itself.** SFTP uses SSH, not TLS — the
  host keys are auto-generated by SFTPGo on first start, stored in
  `/var/lib/sftpgo/`.

## Web UI access

By default the admin UI binds to `0.0.0.0:8080` — reach it from your LAN at
`http://<lxc-ip>:8080`. The UI does not have TLS configured by default; for a
homelab where the binding is on an internal VLAN this is acceptable. Override
`sftpgo_web_admin_bind_address` to `127.0.0.1` if you'd prefer SSH-tunnel
access for stricter isolation.

Admin credentials are in `/etc/sftpgo/admin-password.txt` after first deploy
(if you didn't provide one via `sftpgo_admin_password`). Move that to your
1Password Homelab vault and clear the file.

## Migrating SFTPGo auth to Authentik (future)

When Authentik is up:

- **Web UI (admin + client)**: configure SFTPGo's OIDC integration —
  `SFTPGO_HTTPD__BINDINGS__N__OIDC__*` env vars point at Authentik. Users
  authenticate with their Authentik identity, role assignment happens via
  the `sftpgo_role` claim or the implicit-roles config.
- **SFTP protocol**: SFTP has no OIDC support (it's SSH). Switch the data
  provider's auth backend to LDAP pointing at Authentik's LDAP outpost —
  operators authenticate with LDAP credentials, pubkeys can also live in
  LDAP user records.

Neither migration requires re-deploying; just env var changes and a
restart. Virtual user records become a federation cache, not a primary
truth source.
