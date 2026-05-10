# Secrets — bootstrap sequence

There is a deliberate chicken-and-egg problem on day one: Vault doesn't exist yet, so it can't be used during initial provisioning. This sequence resolves it cleanly without circular dependencies.

## The problem

```
Want to use Vault for secrets
  → Vault needs K3s to run
    → K3s needs secrets to bootstrap
      → Vault doesn't exist yet
```

## The solution

Use Ansible Vault for all secrets during bootstrap. Once Vault is running, migrate secrets into it. After that, Ansible Vault is only used for must-run tier secrets (which cannot depend on K3s Vault) and the Vault unseal credentials.

## Bootstrap order

```
1. Terraform provisions K3s VMs (secrets from Ansible Vault)
2. Ansible configures all nodes (secrets from Ansible Vault)
3. K3s bootstrapped on all three control plane nodes
4. Flux installed, pointed at GitHub repo
5. Sealed Secrets controller deployed via Flux
6. AWS KMS credentials sealed and committed to repo
7. Vault deployed via Flux HelmRelease
   → Init container reads Sealed Secret
   → Fetches AWS KMS credentials
   → Vault auto-unseals via AWS KMS
8. Vault initialised, root token stored in Ansible Vault temporarily
9. External Secrets Operator deployed via Flux
   → Pointed at Vault
10. All remaining secrets migrated into Vault
    → Cloudflare tokens
    → Proxmox API token
    → Database passwords
    → ansible SSH key
    → etc.
11. Everything else deployed via Flux using ESO-managed secrets
12. Root token rotated, admin policy created for ongoing access
```

## Post-bootstrap state

After bootstrap completes:
- Ansible Vault holds: must-run LXC secrets, AWS KMS credentials (backup), ansible SSH key (backup)
- Vault holds: everything else
- Sealed Secrets: only the AWS KMS bootstrap credential
- Git: nothing sensitive

## Must-run tier forever uses Ansible Vault

Must-run LXCs (Factorio, Teamspeak, Pi-hole, etc.) are configured by Ansible/AWX. They cannot depend on K3s Vault being available — they need to be recoverable even when K3s is completely broken. Ansible Vault is the right tool for this layer and stays that way permanently.

## Vault unseal on restart

On any K3s restart or Vault pod restart:
```
Vault pod starts
  → Init container runs
  → Reads Sealed Secret containing AWS KMS credentials
  → Calls AWS KMS Decrypt
  → Vault unseals automatically
  → ESO syncs secrets to K8s
  → Dependent pods start
```

No human intervention needed. AWS KMS availability is the only external dependency.
