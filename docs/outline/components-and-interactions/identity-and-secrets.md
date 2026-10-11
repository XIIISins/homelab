<!-- docs/outline/components-and-interactions/identity-and-secrets.md -->

# Identity & secrets

Two questions run through the whole homelab: *who is this person?* and *where does this credential come from?* Authentik answers the first. Three stores, each with one job, answer the second.

---

## Identity: Authentik

**Authentik is the only identity provider.** It runs in the asgard cluster (three server replicas and a worker, behind Traefik, with its database on the Postgres VIP). Users and groups are defined as data in Terraform (`users.yaml`, `groups.yaml`); each application is its own Terraform file that creates the provider, the application and a per-app group binding.

| Protocol | Used by |
|---|---|
| **OIDC** | Outline, NetBox, Semaphore, Immich, MicroBin (admin paths), Vault UI, Tailscale |
| **SAML** | Zabbix |
| **Proxy / ForwardAuth** | Tools with no SSO of their own: the VictoriaMetrics UI (`metric`), the VictoriaLogs UI (`logs`), Sonarr, SABnzbd. A Traefik middleware sends the request to the Authentik outpost before the app sees it. |

Access is per-app: a user must be in the application's group, and non-members are denied (with an opaque "Policy binding 'None'" message that looks like a bug and is not). Group membership lives in `users.yaml`, so granting access is a reviewed commit.

**Break-glass.** Every web app also keeps a local admin account, stored in 1Password, so a broken Authentik does not lock the operator out. On hosts, the `ansible` service account (key-only, passwordless sudo) runs automation, and the `recovery` user (key-only, key in 1Password) exists for emergencies. LDAP-backed SSH for personal accounts (Authentik's LDAP outpost plus SSSD on hosts) is designed and the group exists, but is not deployed: operators reach hosts today with keys, Tailscale SSH, and the memory-only agent on the control node.

---

## Secrets: three stores, one rule

> **Humans looking up a homelab secret → the Vault UI** (mirrored offline in 1Password).
> **Bootstrap, and non-homelab credentials → 1Password.**
> **Machines at runtime → HashiCorp Vault.**
> **Machines at bootstrap → Ansible Vault.**

| Store | Who reads it | Holds |
|---|---|---|
| **HashiCorp Vault** (asgard, 3-node Raft, AWS KMS auto-unseal) | Kubernetes workloads through External Secrets Operator; Ansible through AppRole; humans through the Vault UI behind Authentik | Every runtime secret: database passwords, API tokens, app keys, per-consumer S3 credentials |
| **1Password, "Homelab" vault** | The operator | (1) Credentials that must survive Vault being down: the Vault root token, the KMS unseal credentials, the SSH recovery key, the sealed-secrets keypair backup, the control-node AppRole credentials. (2) A scripted offline mirror of every Vault-stored homelab secret. |
| **1Password, other vaults** | The operator | Credentials for things *under* the homelab and personal ones: Proxmox root, Synology admin, the UCG-Ultra, KPN, plus anything non-homelab |
| **Ansible Vault** (`group_vars/all/vault.yml`) | Ansible, on a fresh node | Only what is needed *before* Vault is reachable: the K3s join token, RHEL subscription keys, SSH public keys, and a recovery copy of the KMS reference |
| **Sealed Secrets** | The cluster controller | The one secret Vault needs to unseal itself (the AWS KMS credentials), safe to commit because it is encrypted to the cluster's key |

**The scope rule.** Things that exist *because the homelab exists* go in Vault (and the Homelab 1Password vault as a mirror). Personal credentials, and infrastructure beneath the homelab, live in 1Password outside it. Human-only secrets that no machine ever reads (for example the Munin Tailscale key) stay in 1Password alone.

**The bootstrap rule.** Vault runs in Kubernetes, so anything Kubernetes needs to start cannot live in Vault: that would be a loop. The test is "is this needed before Vault is reachable?" Yes means Ansible Vault; no means Vault.

**Why not one tool.** Humans need autofill, search and mobile access that Vault's UI does not give; machines need leases, policies and an API that a password manager does not give. Enterprises layer the same way. The layering is kept legible by a clean rule rather than by forcing one tool to do both jobs.

The repository is public. Topology, IPs, Vault paths and 1Password item identifiers are accepted as public information; values never are. Ansible Vault files are downloadable, so their security rests on a long passphrase, and Sealed Secrets are public-safe by design.

---

## Vault

| Aspect | Detail |
|---|---|
| Deployment | Three pods, one per worker, Raft on `local-path` volumes. Quorum replication is the availability story, not storage-level replication. |
| Unseal | AWS KMS auto-unseal with a single-key account; the IAM user is scoped to that key only. |
| TLS | The listener serves TLS from the cert-manager internal CA. Traefik re-encrypts to Vault through a `BackendTLSPolicy`; External Secrets trusts the CA via trust-manager's bundle. There is no plaintext endpoint. |
| Layout | KV v2 at `secret/`, organised by **consumer**: `secret/k8s/…` for Kubernetes workloads, `secret/ansible/…` for Ansible on LXCs and VMs. The Terraform module that mints a secret writes it to the consumer's path. |
| Config | Mounts, auth methods, policies and roles are all in Terraform (`terraform/vault/`). **AppRole SecretIDs are never in Terraform state.** |
| UI | `vault.niflheim.xiiisins.com`, signed in through Authentik OIDC. |
| Snapshots | A daily Raft snapshot is uploaded to S3 by a CronJob that authenticates with its ServiceAccount, so there is no credential to expire. |

### Two auth methods

- **Kubernetes auth** for External Secrets Operator: the operator's ServiceAccount maps to a policy that reads `secret/data/*`.
- **AppRole** for Ansible and automation. Policies are narrower (the `ansible` policy reads only `secret/data/ansible/*`). Separate roles exist for the operator's workstation, for Semaphore, and for the control node, so each can be rotated and revoked independently. Roles carry no source-address binding, since the callers' addresses change or are NAT'd.

SecretIDs are minted by hand, stored outside Terraform, and rotated on a 90-day cadence with helper functions that mint the new one and revoke the old one only after the store is updated. There are two helpers because Semaphore's credentials are canonical in Vault, while the workstation's are canonical in 1Password; using the wrong one for a role fails at the first step.

---

## How a secret reaches a pod

```
Terraform (or an operator) writes secret/k8s/<app>/…  in Vault
        │
External Secrets Operator ── Kubernetes auth ──▶ reads the path
        │
Kubernetes Secret  ──▶ pod env var / mounted file
```

Each app ships an `ExternalSecret` that names the Vault path and the Secret to build. Secrets refresh on a poll interval (an hour by default) and can be forced. The trade-off is that the secret exists as a Kubernetes Secret as well as in Vault. The production design keeps this ESO sync model; runtime retrieval (Vault Agent or the Vault Secrets Operator) is explored only as a learning exercise on the ephemeral burst cluster.

The same applies in reverse for secrets that Terraform mints (Garage keys, Authentik client secrets, the Proxmox read-only token for the overview page): the minting module writes straight into the consumer's Vault path, then the 1Password mirror script copies it across.

## How a secret reaches Ansible

On a playbook run, the `community.hashi_vault` lookup logs in with AppRole and reads `secret/ansible/…`. The control node and AI assistant sessions use a **Vault-backed environment shim**: it logs in non-interactively, caches the infrastructure credentials for three hours, and exports them to the commands that need them, so nothing interactive (like a password manager prompt) is required. Credentials are resolved inside the shell process and never typed into a command line or transcript.

---

## The mirror

The 1Password mirror is manual discipline backed by a script (`vault-1p-mirror`) that copies Vault ↔ 1Password from a names-only map and verifies every write. The direction of the rule is: **everything in Vault has a 1Password copy; not everything in 1Password is in Vault.** Items follow a `[Realm] - Consumer - Service - Detail` title convention, and machines reference items by UUID rather than title so retitling is safe.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| Authentik | OIDC/SAML/ForwardAuth logins fail. Local break-glass accounts, direct LXC backdoors and key-based SSH remain. Running sessions on most apps continue until they expire. |
| One Vault pod | Raft keeps quorum (2 of 3). External Secrets and Ansible continue. |
| Vault sealed | Existing Kubernetes Secrets keep working, new syncs and Ansible lookups fail. KMS auto-unseal normally prevents this; if AWS is unreachable, unsealing needs the 1Password-held bootstrap credentials. |
| All Vault pods | Existing workloads keep running on their cached Kubernetes Secrets. Restore comes from a Raft snapshot in S3; the root token lives in 1Password. |
| 1Password | Day-to-day operation is unaffected: machines never read it. The offline mirror and bootstrap credentials are unavailable until it returns. |
| A leaked credential | Rotate it. The procedures are per credential class; automation never rotates on its own. |

---

## See also

- **Edge** (this section): how requests reach Authentik and the ForwardAuth middleware.
- **GitOps & automation** (this section): how Semaphore and Terraform use these credentials.
- **Storage & data** (this section): the S3 backups of Vault.
- **Vault & Postgres** and **Identity & edge** troubleshooting (Troubleshooting).
- **Procedures**: AppRole rotation, credential rotation, Vault recovery.
