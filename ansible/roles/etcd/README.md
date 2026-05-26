# ansible/roles/etcd/README.md

# etcd

Installs and runs a static-cluster etcd 3-node deployment on the hosts in
`{{ etcd_inventory_group }}` (default: `haproxy_etcd`).

## Usage

Add to a playbook:

```yaml
roles:
  - role: etcd
```

Inventory must define `ansible_host` for each member of the target group;
the role uses that IP for both client and peer URLs.

## Configuration

All settings are in `defaults/main.yml`. Common overrides:

- `etcd_version` — pinned to a concrete release tag (e.g. `v3.6.11`).
  Upgrade is operator-driven: bump this, re-run the play.
- `etcd_cluster_token` — bootstrap-time consistency check.
- `etcd_inventory_group` — change if deploying a second cluster.

## Security posture

Plaintext (no TLS) + no authentication. The cluster is scoped to a private
VLAN with a single consumer (Patroni); the VLAN boundary is the meaningful
trust boundary at homelab scale. See the `Patroni DCS placement` decision
row in `docs/homelab-design.md`.
