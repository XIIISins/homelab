<!-- docs/outline/procedures.md -->

# Procedures

Step-by-step operational runbooks. Where **Troubleshooting** is reactive ("this is broken, what now"), Procedures are deliberate ("I am going to do this thing on purpose"). Each one is a standalone document; some compose others.

*A procedure is a route to a known end state, written so it can be followed under pressure. The thinking happens once, when the runbook is written — not at 2 a.m. when it's being run.*

---

## How these are written

- **Composable.** A big procedure references smaller ones rather than inlining them. "Rebuild the cluster" leans on "deploy with Terraform," "provision with Ansible," and "recover Vault."
- **Danger points are called out explicitly.** Any irreversible step (a `terraform destroy`, a key that can't be regenerated) is flagged before it appears, not after.
- **The repo is the source of exact values.** These runbooks give the shape, the order, and the load-bearing commands. Per-resource specifics (exact module paths, variable names) live in the code; the runbook tells you which code to run and why.

---

## The runbooks

| Procedure | When you reach for it | Blast radius |
|---|---|---|
| **AppRole rotation** | Routine credential hygiene, or after a SecretID leak | One consumer's Vault auth |
| **Vault recovery** | Vault is sealed, a follower won't rejoin, or a token expired mid-deploy | The secrets layer |
| **K3s node rebuild** | A single control-plane or worker node needs rebuilding | One node; cluster stays up |
| **NetBox initial data import** | First-time (or post-wipe) NetBox population | NetBox data only |
| **Proxmox host patching** | Rolling OS patch + reboot of the PVE hypervisor hosts | Each host's guests, one host at a time |
| **Teardown & rebuild** | The whole asgard cluster needs reconstructing from scratch | Everything in asgard K3s |

Ordered above by increasing blast radius. Reach for the smallest procedure that solves the problem.

---

## See also

- **Troubleshooting** — for the reactive side: a symptom you're trying to diagnose rather than an operation you're choosing to run.
- **Components & interactions** — the architecture these procedures operate on.
- **GitOps & automation** (Components) — the normal change paths these procedures sit outside of (they're the break-glass operations).
