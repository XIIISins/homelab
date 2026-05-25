<!-- ansible/roles/caddy-reverse-proxy/README.md -->

# caddy-reverse-proxy

Generic Caddy reverse-proxy role. Renders a Caddyfile composed of N site
blocks, one per entry in `caddy_sites`. Each site is independently:

- bound to a listener (port + optional hostname)
- source-IP allowlisted via Caddy's `remote_ip` matcher
- reverse-proxied to a single upstream
- logged in JSON to its own log file

Built for Phase 5h.2 (Hermod, gating AppriseAPI behind an IP allowlist),
but the role itself has no per-service knowledge — pure Caddy wiring,
reusable for any future LXC needing a thin reverse proxy.

## Usage

```yaml
# group_vars/<hostgroup>.yml
caddy_sites:
  - name: hermod
    listen: ":80"
    upstream: "127.0.0.1:8000"
    allowed_cidrs:
      - "10.0.11.21/32"      # Zabbix server
      - "10.0.21.0/24"       # K3s nodes
      - "10.0.20.0/24"       # MetalLB pool
```

```yaml
# playbooks/<service>.yml
roles:
  - role: caddy-reverse-proxy
    tags: [caddy]
```

## Site fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | yes | — | Friendly identifier. Used in log file path + comments. |
| `listen` | yes | — | Caddy address spec: `:80`, `:443`, `hostname:80`. |
| `upstream` | yes | — | `reverse_proxy` target. `host:port`. |
| `allowed_cidrs` | yes | — | List of CIDRs/IPs permitted to reach the upstream. |
| `log_path` | no | `/var/log/caddy/<name>.log` | JSON access log destination. |
| `forbidden_body` | no | `"forbidden"` | Body returned on 403 to disallowed sources. |
| `extra_directives` | no | `[]` | Raw Caddy directives appended inside the matched-handle block. Used sparingly. |

## What's NOT in scope

- **TLS termination.** Role binds plain `:80` by default. If you need
  HTTPS, set `listen: ":443"` and add an `extra_directives` entry like
  `tls /path/to/cert /path/to/key`. Automatic Let's Encrypt is *possible*
  but not currently wired — most homelab use cases are behind Cloudflared
  or internal-only, so plain HTTP is fine.
- **Anything that isn't reverse proxy + allowlist.** Static file
  serving, advanced routing, etc. — write a dedicated role.
- **Caddy admin API.** Disabled by default (`admin off` in globals) so
  the role's Caddyfile is the single source of truth, not the runtime
  config.

## Idempotency

- `validate` on the template task ensures a syntactically-invalid
  Caddyfile fails the play before it's installed.
- `notify: reload caddy` on the template task — Caddy's systemd reload
  re-reads the Caddyfile without dropping connections.

## See also

- [Hermod (5h.2)](../../../docs/services/notifications.md) — first
  consumer; allowlist + ingress patterns documented in detail.
