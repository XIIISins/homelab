<!-- ansible/roles/zabbix-agent/README.md -->

# zabbix-agent

Installs + configures `zabbix-agent2` on every host that should appear on the Zabbix dashboard. Pushes metrics to the Zabbix server LXC (`10.0.11.21`).

Lightweight — adds ~25 MB RSS per host, ~80 packages from the Zabbix repo. Safe to apply broadly.

## What this role does

1. Installs Zabbix's apt repo (same `.deb` as `zabbix-server` role).
2. Installs `zabbix-agent2`.
3. Renders `/etc/zabbix/zabbix_agent2.conf` (Server + ServerActive both pointing at `10.0.10.21`).
4. Starts the agent.

## Variables

See [`defaults/main.yml`](defaults/main.yml). Most-tuned per-host (in host_vars or group_vars):

- `zabbix_agent_hostname` — defaults to `inventory_hostname`. Override if the Zabbix server's host inventory uses a different name.
- `zabbix_agent_host_metadata` — used by Zabbix's server-side auto-registration action to bucket hosts into groups automatically. Set to something machine-parseable like `"role=k3s-worker location=urd"` if you want metadata-driven grouping.
- `zabbix_agent_tls_*` — currently `unencrypted` (homelab-internal traffic). Bump to PSK or cert-based when exposing agents over tailnet.

## Rollout strategy

Add `zabbix-agent` to playbooks that touch hosts you want monitored. Two common patterns:

**Pattern A: monitor everything** — add `- role: zabbix-agent` to every playbook (postgres-host, factorio-host, adguard-host, asgard-k3s, etc.).

**Pattern B: dedicated monitoring-agents playbook** — `ansible/playbooks/zabbix-agents.yml` with `hosts: all` + `roles: [zabbix-agent]`. Run once after cluster bring-up; re-run when agents need config updates.

We use Pattern B — keeps the per-service playbooks tight and lets agent-rollout be a separate operation.

## Operational notes

- **Auto-registration on first contact**: the Zabbix server's "auto-registration" action (set up via UI, after server first deploy) auto-adds new hosts that connect with the configured `HostMetadata`. Once added, the server polls them every minute (or whatever the host's check interval is).
- **Firewall**: agent listens on port 10050. Zabbix server polls in (passive checks); agent connects out to port 10051 (active checks). VLAN 11 is internal so both directions work.
- **No upgrades needed** on agent for server upgrades — zabbix-agent2 is forward-compatible with newer Zabbix servers.

## See also

- [`zabbix-server`](../zabbix-server/)
- Phase 5h in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
