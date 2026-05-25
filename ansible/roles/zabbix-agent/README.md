<!-- ansible/roles/zabbix-agent/README.md -->

# zabbix-agent

Installs + configures `zabbix-agent2` on every host that should appear on the Zabbix dashboard. Pushes metrics to the Zabbix server LXC (`10.0.11.21`).

Lightweight — adds ~25 MB RSS per host, ~80 packages from the Zabbix repo. Safe to apply broadly.

## What this role does

1. OS-routes: `install-debian.yml` (apt) or `install-rhel.yml` (dnf) by `ansible_os_family`. K3s VMs run RHEL 9; everything else is Debian-family.
2. Installs Zabbix's release package + `zabbix-agent2`. On RHEL also sets the `zabbix_can_network` SELinux boolean.
3. `configure.yml` renders `/etc/zabbix/zabbix_agent2.conf` (Server + ServerActive both pointing at `10.0.11.21`) + enables + starts the unit.
4. `register-host.yml` calls Zabbix's API (`delegate_to: localhost`, `community.zabbix.zabbix_host`) to ensure a host record exists with the right Agent interface + host groups + linked templates. Idempotent; declarative.

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

- **Declarative host registration** (default `zabbix_agent_register_host: true`): each playbook run calls Zabbix's API to ensure a host record exists for this agent with the right interface + groups + templates. Idempotent. Disable per-host by setting the var `false` if a host is managed differently. Server-side auto-registration actions are NOT used — declarative wins for an inventory-as-truth setup.
  - **One-time API token bootstrap**: log into Zabbix UI as Admin → top-right user icon → **API tokens** → **Add** → no expiry → **Generate**. Copy the token (shown once) and stash via `vault kv put secret/ansible/zabbix/api-token value=<token>`. The Vault path was minted as a placeholder in 7c.1; `lifecycle.ignore_changes` on `data_json` means this manual put won't be clobbered by future `terraform apply`.
  - Without a real token in Vault, the `register-host` task fails loud (401) — disable it via `--skip-tags zabbix-agent:register` for re-runs while the token is being set up.
- **Host groups / templates** must already exist on the server before the API call. Defaults are `["Linux servers"]` + `["Linux by Zabbix agent"]` (both shipped by Zabbix's stock schema). Override per inventory group via `group_vars/<group>.yml` once you've created custom groups in the UI (e.g. `K3s workers`, `PostgreSQL`, etc.).
- **Firewall**: agent listens on port 10050. Zabbix server polls in (passive checks); agent connects out to port 10051 (active checks). VLAN 11 is internal so both directions work.
- **No upgrades needed** on agent for server upgrades — zabbix-agent2 is forward-compatible with newer Zabbix servers.

## See also

- [`zabbix-server`](../zabbix-server/)
- Phase 5h in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
