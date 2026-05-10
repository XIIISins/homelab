# Identity — user model

Production discipline from day one. Root SSH is disabled on all hosts after initial bootstrap. Every action is traceable to a named human or a named automated system.

## User accounts

### Personal admin user (you)

Created on every Proxmox node, LXC, and K3s VM during initial Ansible provisioning via Authentik LDAP + SSSD.

- SSH key authentication only — password auth disabled
- Root SSH login disabled on all hosts after bootstrap
- Sudo access with full logging — all sudo commands written to auth log
- Personal SSH key pair (Ed25519): private key on MacBook only, backed up to 1Password personal vault

### `ansible` service account

Created as a local user on every managed host. Used exclusively by AWX for all automated Ansible runs.

- Local account — does not go through SSSD/Authentik
- SSH key auth only, passwordless sudo
- Private key stored in AWX credential store, backed by Vault
- Never used for human logins

**Why local and not SSSD?** AWX must be able to connect to hosts even if Authentik is down. The `ansible` account is the automation path; it cannot depend on the identity provider it might be helping to fix.

**Audit trail:** when `ansible` appears in auth logs it was AWX. When your personal username appears it was you. These two identities are always distinguishable.

### `recovery` break-glass user

Exists only on the three Proxmox hypervisor nodes — not on LXCs or K3s VMs.

- Local account, completely independent of SSSD/Authentik
- SSH key auth only — no password
- Sudo access with logging
- Private key stored in 1Password — accessible on phone and Mac
- Username is obviously distinct (e.g. `recovery`) so it stands out in auth logs

**Why only on Proxmox nodes?** From a Proxmox node you can reach any LXC or VM via `pct enter <id>` or `qm terminal <id>` — direct console access that bypasses SSH entirely. You don't need the break-glass user on every host, just on the three nodes that give you console access to everything.

## Proxmox API token (Terraform)

Not a system user. A dedicated Proxmox API token scoped to exactly what Terraform needs:
- VM/LXC create, modify, delete
- Storage access
- Cluster operations

Shows in Proxmox audit logs independently of human logins. Revocable without affecting any user account. Stored in Ansible Vault during bootstrap, rotated into Vault after K3s is up.

## SSH key strategy

```
Personal key pair (Ed25519)
  Private key: MacBook only
  Backup: 1Password personal vault
  Authorised on: all Proxmox nodes, all LXCs, all K3s VMs (via SSSD)

AWX ansible key pair (Ed25519)
  Private key: AWX credential store → backed by Vault
  Never touches disk outside AWX
  Authorised on: all managed hosts (ansible user's authorized_keys)

Recovery key pair (Ed25519)
  Private key: 1Password (accessible on phone)
  Authorised on: three Proxmox nodes only (recovery user)
```

## SSSD and Authentik LDAP

SSSD (System Security Services Daemon) on each host queries Authentik's LDAP provider to resolve your personal user and authenticate SSH logins. `cache_credentials = true` means recently authenticated users can still log in if Authentik is temporarily unreachable.

Authentik provides:
- LDAP provider → SSH login + sudo group membership for infrastructure hosts
- OIDC provider → SSO for all web applications

One user in Authentik. One place to disable access. Revoke in Authentik and within minutes access is gone from every host and every application.
