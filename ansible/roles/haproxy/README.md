<!-- ansible/roles/haproxy/README.md -->
# `haproxy` role

Generic, data-driven HAProxy install + config. The role itself knows
nothing about specific backends — every listener is described by the
caller via `haproxy_listens`. Reuse the role for any combination of
TCP/HTTP frontends by supplying a different list per inventory group.

## What it does

1. Installs the `haproxy` apt package.
2. Renders `/etc/haproxy/haproxy.cfg` from `templates/haproxy.cfg.j2`,
   using `haproxy -c -f` to validate the rendered file before swapping
   it in.
3. Enables + starts the `haproxy` systemd unit. Notifies a graceful
   `reload haproxy` on config change (existing sessions drain; new
   connections hit the new config). `restart haproxy` exists for
   callers who need a hard reset.

## Variables

See `defaults/main.yml` for the full annotated schema. Summary:

| Variable | Type | Purpose |
|----------|------|---------|
| `haproxy_global_extra` | list[str] | Raw lines appended to `global` |
| `haproxy_defaults_extra` | list[str] | Raw lines appended to `defaults` |
| `haproxy_stats` | dict | Optional stats listener (`bind`, `uri`, `auth`) |
| `haproxy_listens` | list[dict] | Listen blocks — see below |

Each `haproxy_listens` entry:

```yaml
- name: <listener-name>
  bind: ["<ip>:<port>", ...]
  mode: tcp | http
  balance: <algorithm>          # optional
  options: ["<verbatim>", ...]  # rendered as `option <s>` lines
  http_check:                   # optional
    expect: "status 200"
  default_server:               # optional; rendered as `default-server k v k v ...`
    inter: 3s
    fall: 3
    rise: 2
    on-marked-down: shutdown-sessions
  servers:
    - name: <id>
      address: <ip-or-host>
      port: <int>
      check: true               # emits `check` (mutually exclusive with check_port)
      check_port: <int>         # emits `check port <N>` (for separate health-check port)
      extra: "<verbatim>"       # optional, appended to the server line
  extra: ["<verbatim>", ...]    # raw lines inside the listen block (acl, stick-table, ...)
```

## Example: PostgreSQL frontend with Patroni REST-API health-check

Shipped in `inventory/group_vars/haproxy_etcd.yml`:

```yaml
haproxy_listens:
  - name: pg_write
    bind: ["*:5432"]
    mode: tcp
    balance: first
    options:
      - "tcplog"
      - "httpchk GET /master HTTP/1.0"
    http_check:
      expect: "status 200"
    default_server:
      inter: 3s
      fall: 3
      rise: 2
      on-marked-down: shutdown-sessions
    servers:
      - { name: fulla, address: 10.0.11.230, port: 5432, check_port: 8008 }
      - { name: vor,   address: 10.0.11.231, port: 5432, check_port: 8008 }
      - { name: idunn, address: 10.0.11.232, port: 5432, check_port: 8008 }
```

Patroni exposes `/master` on its REST API (port 8008); only the
current leader returns HTTP 200. HAProxy probes each PG node on the
check port, marks the two replicas DOWN, routes writes to the leader.
`balance first` and one-UP-at-a-time make routing fully deterministic.
`on-marked-down: shutdown-sessions` forces existing connections to
close immediately on demotion so clients reconnect through HAProxy
and land on the freshly-elected leader.
