# IaC — Ansible & AWX

Ansible configures everything that Terraform provisions. AWX is the control plane that runs Ansible at scale with audit trail, scheduling, and credential management.

## Ansible responsibilities

| Layer | Ansible manages |
|-------|----------------|
| Proxmox nodes | OS hardening, user accounts, SSSD, network bridges, VLAN config |
| LXCs | Service installation, configuration, systemd units |
| K3s VMs | OS hardening, user accounts, SSSD, K3s prerequisites |
| All hosts | SSH config, sudo policy, fail2ban, unattended upgrades |

Ansible does **not** manage K3s workloads — that's Flux. Ansible manages the OS and infrastructure layer only.

## Service account

The `ansible` service account exists as a local user on every managed host. AWX connects as this user. When `ansible` appears in auth logs it was AWX — distinguishable from human changes at a glance.

```
ansible ALL=(ALL) NOPASSWD: ALL
```

Passwordless sudo is acceptable because:
- The `ansible` account has no password (key auth only)
- The private key lives in AWX/Vault, not on disk
- All sudo commands are logged

## AWX

AWX (open-source Ansible Tower) runs in K3s and provides:

- **Audit trail** — every playbook run logged: who triggered it, when, what changed, full output
- **Scheduled reconciliation** — full playbook run every 30 minutes against all hosts
- **Credential management** — ansible SSH key and secrets pulled from Vault, never stored in plaintext
- **Job templates** — complex operations defined once, run reliably
- **Webhook receiver** — external systems can trigger runs on demand

AWX uses the PostgreSQL LXC cluster as its database — no additional stateful workload inside K3s.

## Reconciliation model

```
Git repo (source of truth)
  ↓
AWX runs full playbooks every 30 minutes
  → Idempotent — only changes what has drifted
  → Every run logged with timestamp, trigger, changed tasks, full output
  → Drift detected → fixed automatically → visible in AWX audit log
```

## Running Ansible before AWX exists

During Phases 4 and 5 (before K3s is up), playbooks run directly from the MacBook:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/proxmox.yml \
  --vault-password-file ~/.vault-pass
```

The same playbooks are later imported into AWX — no rework. The `--vault-password-file` approach is replaced by AWX's Vault credential integration post-bootstrap.

## Dynamic inventory

AWX uses a dynamic inventory script that queries the Proxmox API to auto-discover all VMs and LXCs. No manual inventory file maintenance — new resources provisioned by Terraform are automatically visible to AWX within minutes.

## Ansible Vault

Ansible Vault encrypts secrets at rest in the repo. Used for:
- Must-run LXC secrets (permanently)
- Bootstrap secrets (until Vault is up)
- Proxmox API token (until rotated into Vault)

```bash
# Encrypt a secret
ansible-vault encrypt_string 'supersecret' --name 'db_password'

# Run with vault password
ansible-playbook playbook.yml --vault-password-file ~/.vault-pass
```

The vault password itself is stored in 1Password.
