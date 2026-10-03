# terraform/proxmox/asgard-pools

Phase 10g. A Proxmox resource pool (`aiops-canary`) and a **pool-scoped** API identity (`aiops-rebuild@pve`, token `runner`) for the
Frigg rebuild runner. The runner re-creates the canary LXCs with Terraform; this module makes "it can only touch canaries" a
fact enforced by Proxmox, not a promise kept by code: the user has write privileges on `/pool/aiops-canary` and nothing on `/`.

- Plan code only until the operator applies it. Apply **from the main checkout only**, with root@pam ticket auth
  (`PROXMOX_VE_PASSWORD`, see `provider.tf`) and your own Vault login (`VAULT_TOKEN`; the shim's AppRole token cannot write KV here).
- Order: this module first, then `terraform/proxmox/asgard-lxcs` (its canaries carry `pool_id = "aiops-canary"`: an in-place update, a
  plan that says *replace* means stop), then `terraform/vault` (the runner's AppRole), then the Ansible role.
- The token secret is written to Vault `secret/ansible/aiops/rebuild/pve-token` (`token_id` + `secret`) and is not an output.
  Mirror it to 1Password with `scripts/secrets/vault-1p-mirror` ([`secret-mirroring.md`](../../../docs/procedures/secret-mirroring.md)).
- The privilege list is a starting set found by reasoning, confirmed empirically (positive and negative test) per
  [`aiops-rebuild.md`](../../../docs/procedures/aiops-rebuild.md). Widen only a privilege, only in a PR, never a path.
- A new canary: add it to `canary_nodes` in `asgard-lxcs` (it inherits the pool), to the runner's `CLASS_TABLE` vmids and to the registry.
