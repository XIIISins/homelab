<!-- docs/outline/components-and-interactions/identity-and-secrets.md -->

# Identity & secrets

This subpage covers who can do what, and where credentials live. Two systems carry the load: **Authentik** as the identity provider for both humans and SSH, and a **three-store secrets model** that splits credentials by who consumes them and when.

The principle that shapes every choice on this page: secrets land in the store whose access pattern matches their consumer. Humans need a UI with autofill and mobile access — that's 1Password. Machines at runtime need an audited, policy-driven API — that's HashiCorp Vault. Machines that need credentials *before Vault is reachable* live in the narrowest possible third store. The discipline is the rule, not any one tool.

---

## Authentik — identity provider

Authentik runs in the asgard K3s cluster (`authentik` namespace). One instance, serving every web-app login + every SSH login on every node.

- **OIDC for web apps.** Every internal service that fronts a browser UI (Vault UI, NetBox, Outline, Zabbix, vmui, the VictoriaLogs UI, etc.) federates to Authentik. Some use native OIDC; some are wrapped via Traefik's ForwardAuth against an Authentik proxy provider. Each app's gate is a group-membership check declared in `terraform/authentik/`.
- **SAML for the apps that need it.** Zabbix 7.0 uses SAML 2.0 against Authentik rather than ForwardAuth. The choice is failure-domain-driven: Zabbix is the K3s-down emergency observability path, so its auth cannot itself depend on K3s being healthy. SAML against Authentik keeps the dependency direction sane (apex hostname → CF tunnel → Traefik → Zabbix → SAML to Authentik).
- **LDAP for SSH.** Every node runs SSSD pointed at Authentik's LDAP outpost. The operator's personal user (`ghost`) authenticates against LDAP with an SSH key registered in Authentik. No node-local accounts for human use.
- **Local break-glass accounts** exist on every web service for the case where Authentik itself is down. Their credentials live in 1Password, not in Authentik. Same for Linux: each node has a `recovery` user with NOPASSWD sudo and an SSH key in 1Password.

### Identity as data

Users and groups are declared in `terraform/authentik/users.yaml` and `groups.yaml` and applied via Terraform. Per-app group bindings (e.g. `outline-users`, `zabbix-admins`, `vault-admins`) gate access. Adding a user to a new app is a YAML edit + `terraform apply`, not a click in the Authentik UI.

---

## The secrets architecture: three stores, one rule

**The rule:** *Homelab human lookup → Vault UI (Authentik-gated), with 1Password as the offline mirror. Bootstrap and non-homelab human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

| Store | Consumer | What lives here |
|---|---|---|
| **HashiCorp Vault** (asgard K3s) | K8s workloads via ESO, Ansible via AppRole, automation | App-to-DB passwords, Authentik signing key, S3 access keys, every credential a running workload needs |
| **1Password — Homelab vault** | Operator at any time; also the offline mirror | Bootstrap-only credentials, the offline mirror of everything in Vault, AppRole RoleID/SecretID for the control node |
| **Ansible Vault** (`group_vars/all/vault.yml`) | Ansible, on a fresh node before HashiCorp Vault is reachable | `k3s_token`, RHEL activation keys, baseline SSH keys, AWS KMS re-seal recovery copy |
| **1Password — other vaults** | Operator, when homelab is down or for non-homelab systems | Proxmox root, Synology admin, UCG-Ultra admin, KPN router, personal credentials |

### Scope rule

"Things that exist *because the homelab exists*" go in the Homelab vault or Vault. Infrastructure *under* the homelab (Proxmox root, Synology admin, UCG-Ultra, KPN) lives in 1Password but outside the Homelab vault — it's personal/external. This keeps the Homelab vault tightly defined.

### Why three stores and not one

Vault's UI is austere — daily human credential use needs better UX (mobile, autofill, search) than Vault is built for. 1Password as the only store would force a SaaS-dependent K8s integration path on every workload, and Vault already runs cleanly and matches enterprise patterns. Ansible Vault exists for the narrow bootstrap layer because anything K3s itself needs to come up cannot live in a K3s-hosted Vault (circular dependency).

The discoverability concern that one-store would solve is handled instead by the rule + a discipline: every Vault-stored secret is also mirrored offline into the 1Password Homelab vault. If Vault is down, the operator opens 1Password.

**Public-repo posture.** The repository is public. Secrets never live in Git regardless, but two things now rest on discipline rather than repo-privacy: the `ansible-vault`-encrypted `group_vars/all/vault.yml` is publicly downloadable, so its security is entirely the strength of the vault passphrase (keep it strong, rotate if ever weakened); and SealedSecrets are safe by design (encrypted to the cluster's public key). Internal IPs, Vault paths, and 1Password item IDs are now public recon surface — identifiers, not values, an accepted portfolio tradeoff. The never-commit-secrets discipline is load-bearing, not best-effort.

---

## Vault — runtime store

Three-node Raft HA in asgard K3s. AWS KMS auto-unseal (eu-west-1 single-key account, decrypt-only IAM user). **Node-local `local-path` storage** — Raft replicates the store across the three nodes, so HA is at the app layer and the per-node disk needs no storage-layer replication (a wiped node re-syncs from its peers + KMS-auto-unseals). KV-v2 engine mounted at `secret/`.

- **Listener TLS is disabled.** `tls_disable = 1` on the Vault listener is deliberate — Traefik terminates TLS at the niflheim Gateway in front of Vault's UI; cluster traffic to Vault is plaintext over the in-cluster network.
- **Vault config is Terraform-managed.** Auth methods, policies, roles, and KV mounts all live in `terraform/vault/`. SecretIDs are never in state — they're minted manually and stored externally.
- **UI is Authentik-gated** at `vault.niflheim.xiiisins.com` (Traefik-fronted, niflheim Gateway). Phase 6 Stage 1 is live: OIDC SSO + the `vault-admins` group → read-only `homelab-admin` policy binding, with `VAULT_ADDR` cut over to the HTTPS FQDN. The Vault UI is now the primary lookup point for homelab-scoped human secrets; 1Password remains the offline mirror.

### Path convention

`secret/<consumer-domain>/...` where the consumer-domain identifies who reads the secret, not which Terraform module mints it.

- `secret/k8s/<app>/...` — K8s workloads (e.g. `secret/k8s/outline/s3` for Outline's S3 credentials).
- `secret/ansible/<role>/...` — Ansible-on-LXC credentials (e.g. `secret/ansible/postgres/zabbix-password`).
- `secret/k8s/semaphore/vault-approle` — exceptional case; see AppRole flow below.

Human-only secrets that never reach a machine consumer (e.g. Munin's Tailscale authkey) stay in 1Password only — Vault is the runtime path, and there's no runtime here.

### Two auth methods, two consumer patterns

- **Kubernetes auth** at `auth/kubernetes/`. Vault's own service account is the reviewer. The `eso` role binds the `external-secrets` ServiceAccount to a policy that grants read on `secret/data/*`. This is the path every K8s workload uses, via ESO.
- **AppRole auth** at `auth/approle/`. Two roles:
  - **`ansible-local`** — used by the MacBook control node for manual playbook runs. RoleID + SecretID live in the 1Password Homelab vault.
  - **`ansible-awx`** — used by Semaphore for scheduled playbook runs. RoleID + SecretID live at Vault KV `secret/k8s/semaphore/vault-approle` (different storage convention from `ansible-local`).

Both AppRole roles bind to a narrower `ansible` policy that grants read only on `secret/data/ansible/*`. Ansible reads its own subtree; it cannot reach K8s app secrets.

---

## ESO flow — Kubernetes workloads

K8s workloads never talk to Vault directly. The External Secrets Operator (ESO) does, and projects each Vault path into a K8s `Secret` that the consumer pod reads as env vars or mounted files.

1. Operator declares an `ExternalSecret` in the consumer's namespace, referencing a Vault path under `secret/<consumer-domain>/...`.
2. ESO authenticates to Vault as its own ServiceAccount via the Kubernetes auth method.
3. The Vault `eso` policy grants read on the path. ESO fetches the value and materializes a K8s `Secret` with the same name as the `ExternalSecret`.
4. The consumer pod's spec references the K8s `Secret` (via `envFrom: secretRef:` or a volume mount). On pod start, the value lands in the container.

A `ClusterSecretStore` named `vault` points at `http://vault.vault.svc.cluster.local:8200`, KV-v2, mount `secret`, role `eso`. Every namespace's `ExternalSecret` references this `ClusterSecretStore` — no per-namespace store config.

The `refreshInterval` is 1h by default. Rotation requires a force-sync annotation on the `ExternalSecret` plus a `kubectl rollout restart` on the consumer (Kubernetes snapshots Secret-as-env at pod start; the consumer doesn't see the new value until the pod restarts).

---

## AppRole flow — Ansible

The control node (MacBook running fish) and Semaphore (scheduled playbooks in K3s) both authenticate to Vault via AppRole. They use different roles and different storage conventions for the credential pair.

### Control node — `ansible-local`

RoleID + SecretID for `ansible-local` live in the 1Password Homelab vault. The repo-tracked fish file `<repo>/.config/fish/conf.d/homelab.fish` exposes a `homelab-env` function that reads 1Password and exports `VAULT_ADDR` + `ANSIBLE_HASHI_VAULT_*` into the shell. Ansible's `community.hashi_vault` collection reads those env vars and authenticates with Vault on every lookup.

`homelab-env` caches into `~/.cache/homelab/env.sh` and `.fish` with a 24h TTL so multi-command flows don't re-fetch from 1Password on every invocation. The cache holds derived env vars only — no Vault token by default.

For **non-interactive** control-node use — the Frigg watchtower VM, and headless automation where 1Password can't prompt for biometric auth — a parallel **Vault-backed shim** (`vault-homelab-env`) AppRole-logs-in to Vault with an on-disk secret-zero and exports the same IaC env vars (cache `~/.cache/homelab/vault-env.{sh,fish}`, 3h TTL). The 1Password-backed `homelab-env` stays the path for the operator's interactive MacBook; `vault-homelab-env` is the path that needs no human.

`rotate-approle ansible-local` mints a new SecretID, prompts the operator to paste it into 1Password, then revokes the old one. The accessor is snapshotted *before* the mint so the revoke step targets the correct SecretID even after 1P is updated. Rotation cadence: 90 days (the longest TTL Vault permits).

### Semaphore — `ansible-awx`

Semaphore reads its AppRole credential from Vault KV at `secret/k8s/semaphore/vault-approle`, not from 1Password. The credential pair is injected into Semaphore's environment via Terraform (`terraform/semaphore/`).

Rotation uses `rotate-semaphore-approle` — a separate helper from `rotate-approle`. It mints a new SecretID, writes it to Vault KV with a hash-verify on the read-back, runs `terraform apply` to push it into Semaphore, then revokes the old SecretID after a drift-check confirms the new one works.

---

## Failure surfaces worth knowing

- **Authentik down.** Every web UI's primary login path breaks. Break-glass: each web service has a local admin account (Vault UI, NetBox, Outline, Zabbix, etc.) whose password lives in 1Password. SSH continues to work via SSSD's cached credentials for a while; once the cache expires, the per-node `recovery` user is the fallback (key in 1Password).
- **Vault down.** Every K8s workload loses its ability to rotate or fetch new secrets. Workloads already running keep their existing `Secret` values (ESO materializes K8s `Secret` objects, which persist independently of Vault). Pods that restart re-read the K8s `Secret` and stay alive; net effect is rotation freezes. Operator emergency lookups go through the 1Password offline mirror.
- **1Password down.** Bootstrap is blocked (the MacBook control node can't load AppRole credentials), but running workloads are unaffected. Vault still serves K8s + Ansible-on-Semaphore traffic.
- **Sealed-secrets master key lost.** Every `SealedSecret` in Git becomes undecryptable. Recovery is the master keypair backup stored in 1Password — restored *before* the sealed-secrets controller starts on the rebuilt cluster. Loss of both Git and 1Password copies means rebuilding every bootstrap secret by hand.
- **AppRole leak.** A leaked SecretID is rotated immediately via the rotation helper that matches the role (`rotate-approle` for `ansible-local`, `rotate-semaphore-approle` for `ansible-awx`). The corresponding RoleID is non-sensitive; only the SecretID rotates. Vault audit log shows every login by that SecretID, so the blast radius is auditable.

---

## See also

- **Storage & data** (this section) — Vault on the node-local `local-path` tier, Patroni-minted DB credentials at `secret/ansible/postgres/<app>-password`.
- **GitOps & automation** (this section) — Semaphore's Vault KV-stored credentials, drift-check templates that authenticate via the `ansible-awx` AppRole.
- **Edge** (this section) — Authentik fronted via Traefik + Cloudflared, Traefik ForwardAuth middleware against Authentik proxy providers.
- **Services and purpose** — per-service notes on which auth method each app uses (OIDC, SAML, ForwardAuth).
- **Procedures** — AppRole bootstrap and rotation runbooks.
- **Troubleshooting** — Authentik outpost host caching, ESO refresh + force-sync, AppRole mismatch diagnostics.
