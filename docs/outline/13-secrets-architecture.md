# Secrets — architecture

Nothing sensitive ever touches Git. The repo is public. This is safe because every secret is either encrypted before committing or never committed at all.

## Secrets by layer

| Layer | Tool | Notes |
|-------|------|-------|
| Vault auto-unseal | AWS KMS (eu-west-1) | Always available, ~$1/month, independent of homelab |
| K8s app secrets | External Secrets Operator → Vault | Industry standard, decouples secrets from manifests |
| Vault bootstrap credentials | Sealed Secrets | One-time bootstrap only — see bootstrap sequence |
| Terraform secrets | Ansible Vault + `TF_VAR_` env vars | State never contains secrets |
| Ansible secrets | Ansible Vault | Used throughout — especially must-run tier |
| Must-run LXC secrets | Ansible Vault only | Cannot depend on K8s Vault |
| Git | Nothing sensitive, ever | Repo is public |

## HashiCorp Vault

Self-hosted Vault runs in K3s as a StatefulSet. Storage backend: Integrated Storage (Raft) — no external etcd or Consul dependency. Three Vault pods (one per worker node) form a Raft cluster.

All K3s application secrets live in Vault. External Secrets Operator pulls them into native K8s secrets at pod startup. Applications never talk to Vault directly.

## AWS KMS auto-unseal

Vault's storage is encrypted at rest. On startup, Vault needs the encryption key to unseal. AWS KMS holds this key.

An init container on the Vault pod fetches the AWS KMS credentials from a Sealed Secret, then Vault calls AWS KMS to unseal automatically. No human intervention needed on restart.

**Why AWS KMS?** The unseal mechanism must be independent of what it unseals. AWS KMS is always available regardless of homelab state — exactly how enterprises handle this with cloud KMS.

**Cost:** ~$1/month per KMS key. Replaced an existing mystery charge on the AWS account.

**IAM policy (least privilege):**
```json
{
  "Effect": "Allow",
  "Action": ["kms:Decrypt", "kms:Encrypt", "kms:DescribeKey"],
  "Resource": "arn:aws:kms:eu-west-1:ACCOUNT_ID:key/KEY_ID"
}
```

A compromised credential can only unseal Vault — useless without also having Vault's encrypted storage.

## Cloudflare API tokens

Scoped separately per consumer — if one token leaks, blast radius is contained.

| Consumer | Permissions |
|----------|------------|
| Terraform | Zone read, DNS edit, Tunnel manage |
| cert-manager | DNS edit on `xiiisins.com` zone only |
| cloudflared | Tunnel manage only |

## External Secrets Operator

ESO runs in K3s and syncs secrets from Vault into native K8s `Secret` objects. Applications use standard K8s secrets — they don't know or care that Vault is involved.

Sync is continuous — if a secret rotates in Vault, ESO updates the K8s secret and triggers a pod restart if configured.

## Repo safety

- Ansible Vault encrypts secrets before committing — safe in public repo
- Sealed Secrets are encrypted with the cluster's public key — useless without the cluster's private key
- AWS credentials injected via environment variables at Terraform runtime — never in files
- `terraform.tfvars` is gitignored — `terraform.tfvars.example` committed with placeholders
- Cloudflare tokens stored in Vault post-bootstrap — not in files
