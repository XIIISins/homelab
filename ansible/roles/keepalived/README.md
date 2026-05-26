<!-- ansible/roles/keepalived/README.md -->
# `keepalived` role

Generic, data-driven keepalived install + config. Reuse for any number
of VRRP instances and tracking scripts by supplying a different
`keepalived_vrrp_instances` / `keepalived_vrrp_scripts` list per
inventory group.

Also ships an **optional** source-based policy routing facility for
the common case where the VIP's L2 segment is not on the host's
default route. See "Source-based policy routing" below.

## What it does

1. Installs the `keepalived` apt package.
2. Renders `/etc/keepalived/keepalived.conf` from
   `templates/keepalived.conf.j2` — global_defs, vrrp_script blocks,
   vrrp_instance blocks.
3. (Optional) Installs one `<name>-policy-routing.service` per entry in
   `keepalived_source_policy_routing` — a systemd oneshot that
   maintains an `ip rule` + custom routing table + default route.
4. Enables + starts the `keepalived` systemd unit. Notifies a
   `restart keepalived` on config change (VIP failover to another peer
   is sub-second on a healthy cluster).

## Variables

See `defaults/main.yml` for the full annotated schema. Summary:

| Variable | Type | Purpose |
|----------|------|---------|
| `keepalived_global_defs` | dict | Key-value pairs emitted in `global_defs { ... }` |
| `keepalived_enable_script_security` | bool | Emit `enable_script_security` + `script_user` and provision the runner account (default `true`) |
| `keepalived_script_user` / `_group` | string | System account vrrp_scripts drop to (default `keepalived_script`) |
| `keepalived_vrrp_scripts` | list[dict] | `vrrp_script` blocks (track-script definitions) |
| `keepalived_vrrp_instances` | list[dict] | `vrrp_instance` blocks (the actual VRRP) |
| `keepalived_priorities` | dict | Per-host priorities map (caller dereferences) |
| `keepalived_source_policy_routing` | list[dict] | Optional policy-routing entries |

### Script security

`keepalived_enable_script_security: true` (default) makes the role:

1. Create a system account (`keepalived_script:keepalived_script` by
   default) before rendering the config.
2. Emit `enable_script_security` and `script_user <user> <group>` inside
   `global_defs`, so the daemon drops privileges before exec'ing every
   `vrrp_script`.

Without this, keepalived runs track-scripts as root and logs
`SECURITY VIOLATION - scripts are being executed but script_security not
enabled` at startup. The current consumers (`chk_adguard`, `chk_haproxy`)
only call `systemctl is-active`, which reads unit state via D-Bus and
works for any user — no script needs root. Override per-script with
`script_user` inside a `vrrp_script` entry if a future check genuinely
does (rare).

### Concurrency — `throttle: 1`

The role wraps its tasks in a block with `throttle: 1` and applies the
same to both handlers. Effect: every keepalived peer is reconfigured
and (if notified) restarted one host at a time, while the rest of the
parent play keeps its default parallelism.

`serial` is play-level in Ansible — there is no role-level `serial`.
`throttle` at block/task/handler level is the equivalent at smaller
scope. Without this, a config change that notifies every peer would
restart keepalived across the cluster simultaneously, dropping the VIP
for the duration of the restart.

### Election model

All instances should set `state: BACKUP` (the default) and rely on
`priority` to elect the active VIP-holder. Starting one as MASTER and
the rest as BACKUP works in normal operation but allows dual-MASTER
when interfaces come up out of order on simultaneous boot.

### Per-host priority pattern

A single group-level `keepalived_vrrp_instances` list expresses the
priority per host via dereference:

```yaml
# group_vars/<group>.yml
keepalived_priorities:
  hlin:   { VI_PG: 100 }
  eir:    { VI_PG: 90 }
  snotra: { VI_PG: 80 }

keepalived_vrrp_instances:
  - name: VI_PG
    interface: eth1
    virtual_router_id: 51
    priority: "{{ keepalived_priorities[inventory_hostname].VI_PG }}"
    ...
```

### Source-based policy routing

Required whenever the VIP's L2 segment differs from the host's
default route. Without it, reply packets to clients on a third subnet
will exit the default-route interface (eth0) carrying the VIP source
IP — asymmetric routing → stateful firewall drops → service unreachable
from anywhere except direct L2 neighbours of the VIP segment.

Each entry installs a systemd oneshot at `network-online.target` that
mints a custom routing table, sets its default route to the VIP-segment
gateway, and adds an `ip rule from <cidr>` to direct matching source
traffic into the new table. Same approach as
`roles/k3s/tasks/network.yml` (VLAN 20), generalised to N entries.

```yaml
keepalived_source_policy_routing:
  - name: vlan10            # routing-table name
    table_id: 200           # routing-table ID (unique on this host)
    from: 10.0.10.0/24      # source CIDR the rule matches
    gateway: 10.0.10.1      # next-hop for the table's default route
    interface: eth1         # outbound interface
```

## Example: PostgreSQL HAProxy VIP with chk_haproxy track-script

Shipped in `inventory/group_vars/haproxy_etcd.yml`:

```yaml
keepalived_vrrp_scripts:
  - name: chk_haproxy
    script: "/usr/bin/systemctl is-active --quiet haproxy.service"
    interval: 2
    weight: -50          # drops priority by 50 on failure → another peer wins
    fall: 2
    rise: 2

keepalived_priorities:
  hlin:   { VI_PG: 100 }
  eir:    { VI_PG: 90 }
  snotra: { VI_PG: 80 }

keepalived_vrrp_instances:
  - name: VI_PG
    interface: eth1
    virtual_router_id: 51   # unique on this LAN segment (AGH uses 50)
    priority: "{{ keepalived_priorities[inventory_hostname].VI_PG }}"
    auth_pass: "{{ vault_keepalived_pg_vrrp_auth_pass }}"
    virtual_ipaddresses:
      - "10.0.10.210/24 dev eth1"
    track_scripts: [chk_haproxy]

keepalived_source_policy_routing:
  - name: vlan10
    table_id: 200
    from: 10.0.10.0/24
    gateway: 10.0.10.1
    interface: eth1
```
