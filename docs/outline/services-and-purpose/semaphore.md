<!-- docs/outline/services-and-purpose/semaphore.md -->

# Semaphore

The Ansible orchestration layer — a scheduler and UI that runs the homelab's playbooks on a cadence, checks the fleet for drift, and reports problems. Semaphore is to the LXCs and VMs what Flux is to the cluster: the thing that keeps reality matching the spec.

---

## Where it runs

Semaphore runs in **asgard K3s** (`semaphore` namespace), with a Postgres backend on the Patroni VIP (database `semaphore`).

Its dependencies tie together most of the platform:

- **Postgres** for its own state.
- **Vault** for credentials, authenticating with the `ansible-awx` AppRole (the role it inherited when it replaced the originally-planned AWX deployment).
- **NetBox** as its dynamic inventory source.
- **Authentik OIDC** for login, at `semaphore.niflheim.xiiisins.com`.
- **Hermod** for alert delivery.

---

## The templates

Semaphore runs four scheduled or on-demand jobs:

| Template | Cadence | What it does |
|---|---|---|
| `refresh-netbox-inventory` | Every 4 h | Rebuilds the cached NetBox dynamic inventory. |
| `asgard-drift-check` | Every 6 h | Runs the fleet playbooks in `--check --diff` mode; any drift is reported to Hermod with a compact per-host summary. A clean run is silent. |
| `asgard-apply` | Manual | Applies the fleet playbooks for real (operator-triggered). |
| `asgard-fleet-agents` | Daily | Rolls out the monitoring/log agents (Zabbix agent, vlagent) across the fleet. |

The **drift-check baseline is zero changes** across the fleet. A green drift-check means every LXC and VM matches its Ansible spec exactly; any `changed=` line is drift worth a human's attention.

---

## Where it's heading

Apply is operator-driven today — the operator reads the drift diff, then either fixes the spec or runs the apply. The direction is **auto-apply on detected drift**: Semaphore converging the fleet automatically and notifying after the fact, the same posture Flux and Patroni already have.

---

## See also

- **GitOps & automation** (Components) — the full change-deployment flow and how Semaphore fits the Terraform/Ansible/Flux ownership split.
- **Identity & secrets** (Components) — the `ansible-awx` AppRole Semaphore authenticates with.
- **Observability** (Components) — how Semaphore's drift alerts reach Discord through Hermod.
