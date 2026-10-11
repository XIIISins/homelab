<!-- docs/outline/services-and-purpose/semaphore.md -->

# Semaphore

The Ansible scheduler. It runs the homelab's playbooks on a timer and by hand, against a NetBox-built inventory, and turns drift into an alert. Internal-only at `semaphore.niflheim.xiiisins.com`.

---

## Why Semaphore

It was chosen over AWX for footprint (about 100 MB against about 3 GB), a single Go binary and a simpler operational story. Because the playbook structure is scheduler-agnostic, swapping it later would need no playbook changes.

## How it runs

A single-replica StatefulSet in asgard K3s with its state in the shared Postgres cluster. Sign-in is Authentik OIDC, with a local break-glass admin. It uses a **custom image** with the repo's pinned Ansible collections baked in: the stock image silently used older bundled collections and ignored the pins. Vault access is an AppRole whose SecretID is injected into the task environment and is never in Terraform state. The project, keys, inventory, templates and schedules are all declared in Terraform, and runs are serialised one at a time. A second project holds the AIOps action executor.

## What it runs

| Template | When | What |
|---|---|---|
| Drift check | Every 6 hours | Full converge in check-and-diff mode. Any difference or failure alerts; a clean run is silent. This is the baseline: the check settles at zero changes. |
| Apply | Manual | Converge the fleet |
| OS updates | Manual | Patch and reboot, one node per quorum group |
| Fleet agents | Daily | Re-converge the log and monitoring agents everywhere |
| Infra health check | Every 12 hours | Certificates, token validity, Patroni and etcd quorum, PBS failures |
| PBS restore test | Weekly | Prove backups restore |
| Inventory refresh | Every 4 hours | Rebuild the NetBox inventory cache |
| Canary variants | Daily | The same loop against disposable test containers, alerts capped at `info` |

Results go to Hermod: drift is an `alert`, a failed apply is `critical`, success is a log line.

Today an operator reads the drift diff and either fixes the IaC or applies it; the direction is automatic convergence of small drift, with the operator told afterwards.

---

## See also

- **GitOps & automation** (Components): playbook structure and the AIOps layer.
- **NetBox** (this section): the inventory source.
- **Hermod** (this section): where its alerts go.
