<!-- docs/outline/procedures/teardown-rebuild.md -->

# Teardown & rebuild

The disaster-recovery path: rebuilding the homelab from the Proxmox layer up using only the repository and the bootstrap secrets. The full runbook (with every command) is mirrored as a PDF in 1Password and cloud storage so it is reachable when the homelab, including Munin and this wiki, is down. It contains pointers, never secrets.

---

## The four legs that do not depend on the homelab

1. **The repository.** The complete declarative spec: Terraform, Ansible and Flux.
2. **The bootstrap secrets in 1Password.** The Vault root token and recovery keys; the AWS KMS unseal credentials and key reference; the sealed-secrets master keypair; the Flux deploy key; the Ansible AppRole and Ansible Vault password; the SSH keys for nodes and break-glass; the Proxmox, AWS-state, Synology and UCG-Ultra logins.
3. **The runbook itself.**
4. **A control node:** any machine with `op`, `terraform`, `ansible`, `kubectl`, `vault`, `flux`, `kubeseal` and `jq`.

## What is assumed and what is rebuilt

Assumed standing: Proxmox on Urd, Verd and Skuld, the cluster formed, templates present. Rebuilt by the runbook: everything else. Things not in IaC (the UCG-Ultra, the KPN box, Synology base configuration) are rebuilt by hand from the architecture docs; everything in IaC is rebuilt by re-applying Terraform, re-running Ansible and letting Flux reconcile.

> **Be honest about validation.** The asgard K3s path was validated end to end in a full teardown and rebuild; single control-plane and worker swaps were validated since. The foundation, LXC and data-restore sections are derived from live IaC and have not been exercised as one cold rebuild. Expect gaps, and fix the IaC when you find one.

## The order

| Section | What | Why here |
|---|---|---|
| 0 | Control node and secrets bootstrap | Everything needs credentials |
| 1 | Foundation by hand: UCG-Ultra and KPN, then Munin (volumes, NFS, iSCSI, the CSI user, Tailscale) | The network and storage must exist first |
| 2 | Terraform state sanity | The S3 backend must be reachable |
| 3 | Asgard K3s core: VMs, K3s via Ansible, Flux bootstrap, **sealed-secrets key restore**, Vault restore, Vault Terraform and AppRoles, infrastructure and storage tiers | The cluster and Vault, in order |
| 4 | AdGuard and Tailscale LXCs | DNS and remote access; they need Vault |
| 5 | Data tier: HAProxy and etcd, then Patroni, then database restore | etcd before Patroni |
| 6 | Application and monitoring LXCs, including PBS | Services that sit on the data tier |
| 7 | Secret-minting Terraform and Flux apps | Authentik, Cloudflare, NetBox, Garage and Semaphore then apps converge |
| 8 | Fleet agents and Semaphore templates | Re-establish monitoring and drift checking |
| 9 | Verification | Prove it |
| 10 | Rollback | Know how to back out |

Day-1 bring-up uses targeted `--limit` plays, never `site.yml`: that is the steady-state tool and refuses a small inventory.

## The dangerous step

**Restore the sealed-secrets master keypair after Flux bootstraps but before the sealed-secrets controller first reconciles.** If the controller starts first it generates a new keypair, and every SealedSecret in Git (Vault's unseal credentials, the CSI credentials) becomes undecryptable. If you raced it, re-seal each from plaintext against the new certificate and commit.

## Other things worth knowing

- Reuse the Flux deploy key rather than creating one, or `flux bootstrap` orphans the old key on GitHub.
- Vault rebuilds auto-unseal with KMS; the key reference and IAM credentials are in 1Password, and the ARN is kept out of Git deliberately.
- Time budget: about two working days, split at the end of the foundation and K3s core.
- Partial rebuilds (one control plane, one stateful worker) are appendices; they are the **K3s node rebuild** page.
- Munin's Tailscale install on DSM 7 has its own appendix; a boot task that sets TUN permissions is easy to lose.

---

## See also

- **K3s node rebuild**, **Vault recovery** and **Backups & restore drills** (this section).
- **Compute & hypervisors** (Components): what is being rebuilt.
- **Identity & secrets** (Components): the bootstrap secret model.
