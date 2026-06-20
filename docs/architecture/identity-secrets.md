<!-- docs/architecture/identity-secrets.md -->

# Identity and access management

- **Personal user** — Authentik LDAP + SSSD. SSH key only.
- **`ansible` user** — local, AWX service account. Passwordless sudo. SSH key `~/.ssh/ansible_niflheim`.
- **`recovery` / break-glass user** — created by the `baseline` role on all nodes: SSH-key-only, NOPASSWD sudo. SSH key in 1Password.
- **`kubernetes` user** — Synology admin for CSI driver.
- **Proxmox API token** — scoped for Terraform only.

Local admin accounts on all web services (Vault, AWX, Grafana, Netbox, Outline) as Authentik break-glass fallback. Stored in 1Password.

---

# Secrets management

## The architecture: two access patterns, three stores

**The rule:** *Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

| Layer | Tool | Consumer | Contents |
|---|---|---|---|
| Human-operated credentials | **1Password — "Homelab" vault** | You, via UI/mobile/CLI | Web admin passwords (Authentik admin, Grafana admin, etc.), API tokens you paste manually, LXC template root passwords, homelab-hosted DB admin credentials, TLS recovery keys, AppRole RoleID/SecretID for Ansible control nodes |
| Machine-consumed (runtime) | **HashiCorp Vault (asgard K3s)** | K8s workloads via ESO, Ansible via AppRole, automation | Authentik signing key, DB passwords pulled by apps, K8s workload secrets, Ansible-pulled service passwords (e.g. `secret/ansible/sftpgo/admin-password`) |
| Machine-consumed (bootstrap) | **Ansible Vault** (`group_vars/all/vault.yml`) | Ansible, on a fresh node before HashiCorp Vault is reachable | `k3s_token`, `rhel_activation_key`, `rhel_org_id`, `ansible_user_ssh_public_key`, `breakglass_ssh_public_key`. Plus `aws_access_key_id` / `aws_secret_access_key` / `aws_kms_key_id` as a re-seal recovery copy (not pulled by Ansible — used manually with `kubeseal` to regenerate the vault-unseal SealedSecret; see "Bootstrap-of-bootstrap" row for the runtime path). Narrow scope: only what's needed to make a node usable up to the point HashiCorp Vault can take over. |
| Failure-independent | **1Password — other vaults** | You, when homelab is down | Break-glass user SSH key, AWS KMS unseal token, Vault root token, Proxmox/Synology/UCG-Ultra/KPN admin |
| Bootstrap-of-bootstrap | **SealedSecret + Ansible Vault recovery copy** | Cluster controller (sealed-secrets controller decrypts and mounts as K8s Secret; Vault pod reads as env vars) | `vault-unseal` SealedSecret in `k8s/asgard/infrastructure/vault/vault-unseal-secret.yaml` holds the AWS KMS credentials Vault uses for auto-unseal. Runtime path = SealedSecret → K8s Secret → Vault pod env. Recovery path: if the SealedSecret blob is lost (cluster rebuild, sealed-secrets key rotation), the plaintext AWS values in `group_vars/all/vault.yml` are the source to re-run `kubeseal` against. |

## Scope rule

"Things that exist *because the homelab exists*" go in the Homelab vault or Vault. Personal credentials, external service accounts, and infrastructure *under* the homelab (bare-metal Proxmox root, Synology admin, UCG-Ultra admin, KPN router) live in 1Password but **outside** the Homelab vault — they're personal/external. This scope boundary keeps the Homelab vault tightly defined.

## Why two layers and not one

Considered and rejected:
- **Vaultwarden self-hosted** — adds new infrastructure for human-cred storage that 1Password already does well. The "everything self-hosted" instinct is wrong for credentials needed *when the homelab is down*.
- **1Password Connect as the only store** — would tear down working Vault and replace it with a SaaS-dependent K8s integration path. Vault is already running, stable, and matches enterprise patterns for machine secrets.
- **HashiCorp Vault as the only store, with humans using its UI** — Vault's UI is austere; daily human credential use needs better UX (mobile, autofill, search) than Vault is built for.
- **Centralized everything in one tool** — looked into how enterprises do this. Industry pattern (verified across 2025-2026 sources) is *layered*: EPM (1Password/Bitwarden/Keeper) for humans, secrets manager / PAM (Vault/CyberArk/Delinea) for machines. Even enterprises running 1Password's "unified" platform also run Vault alongside it. Layered is the standard, not the workaround.

**Discoverability concern handled by rule, not by tool unification.** "Scattered truth" was the legitimate worry. The fix is a clean rule (above) and clean boundaries, not forcing one tool to do both jobs poorly.

## Why bootstrap-vs-runtime within the machine tier

HashiCorp Vault lives in asgard K3s. Anything K3s itself needs to come up — `k3s_token` to join nodes to the cluster, SSH keys to even reach the nodes via Ansible — cannot live in Vault. That's a circular dependency: Vault depends on K3s, K3s depends on Vault.

The split: Ansible Vault holds the minimum needed to bootstrap a fresh node *up to the point* where HashiCorp Vault becomes reachable. Everything beyond that goes to HashiCorp Vault. This matches the enterprise pattern (Red Hat IPI installer, AWX bootstrap, every K8s-hosted-Vault deployment hits this) — even though "everything in HashiCorp Vault with a fallback cache" is theoretically possible, the bootstrap layer is small, near-permanent, and rarely-touched, so the simpler approach wins.

The line is essentially: *is this secret needed before HashiCorp Vault is reachable from its consumer?* Yes → bootstrap (Ansible Vault). No → runtime (HashiCorp Vault).

## Long-term direction

The current implementation has ESO sync-and-cache for K8s secrets (Vault → ESO → K8s Secret → pod env var). The enterprise pattern is *runtime retrieval* — pods pull from Vault at use time, no caching in K8s Secrets. The migration target is Vault Agent or Vault Secrets Operator. This is a future project on jotunheim cluster first (use the learning environment for that learning), then migrate asgard workloads.

## Vault current state

- 3-node Raft HA, AWS KMS auto-unseal (eu-west-1, single-key AWS account, `vault-unseal` IAM user is decrypt-only on that one key)
  - **KMS key recovery pointer** (what to point a rebuilt Vault's `seal "awskms"` at): region `eu-west-1`. The key-id is `VAULT_AWSKMS_SEAL_KEY_ID` in the `vault-unseal` secret (SealedSecret at `k8s/asgard/infrastructure/vault/vault-unseal-secret.yaml`, runtime path SealedSecret → K8s Secret → Vault pod env) with a recovery copy as `aws_kms_key_id` in `group_vars/all/vault.yml`. The **full key ARN + the decrypt-only IAM access key/secret** live in the 1P bootstrap item (outside the Homelab sub-vault per the failure-independent scope rule). The ARN is deliberately **not** committed — it's an identifier, not a credential, but it embeds the AWS account ID; keeping it in 1P preserves "pointers in git, account-specifics in 1P".
- Storage: 3-node Raft on `local-path` (node-local NVMe, one PV per pod pinned to urd/skuld/verd) — migrated off iSCSI in the 2026-05-30 storage redesign (Class L). HA is app-level (Raft quorum), not storage-level; PBS backs up each worker's `/data`.
- Listener serves TLS via the cert-manager internal CA `homelab-internal-ca` (flipped from `tls_disable=1` 2026-06-20; trust-manager distributes the CA, Traefik re-encrypts via `BackendTLSPolicy`, ESO trusts via `caProvider`). See [`../procedures/vault-tls-migration.md`](../procedures/vault-tls-migration.md)
- KV-v2 engine at `secret/`
- **Kubernetes auth method** at `auth/kubernetes/` (`kubernetes_host=https://kubernetes.default.svc`, Vault's own SA token used as reviewer — Vault SA has `system:auth-delegator` via `vault-server-binding` ClusterRoleBinding)
  - `eso` policy: read on `secret/data/*`
  - `eso` role: binds SA `external-secrets` in ns `external-secrets` → `eso` policy, TTL 1h
- **AppRole auth method** at `auth/approle/` (added 2026-05-16, D1)
  - `ansible` policy: read on `secret/data/ansible/*` — narrower than `eso`, Ansible only reads its own subtree
  - `ansible-local` role: MacBook control node, manual playbook runs. RoleID + SecretID stored in 1P (Homelab vault, item `Ansible - Vault - k3s`) — single source of truth, no env file on disk. Loaded into env via `homelab-env`. 90-day rotation via `rotate-approle ansible-local`. See Control-node fish tooling sub-section.
  - `ansible-awx` role: Originally created for AWX; **now consumed by Semaphore** (Phase 5h.3 replaced AWX with Semaphore but kept this AppRole). RoleID + SecretID stored at Vault KV `secret/k8s/semaphore/vault-approle` (NOT 1P — different storage convention from `ansible-local`), propagated to Semaphore env id 1 via `terraform apply` in `terraform/semaphore/`. Rotate via `rotate-semaphore-approle` (separate function from `rotate-approle`, see Control-node fish tooling). Do NOT use `rotate-approle ansible-awx` — that helper is 1P-canonical and ansible-awx has no 1P entry; running it errors out at "unknown role". Surfaced 2026-05-27 during a leak-driven rotation: operator assumed Semaphore used `ansible-local`, manually wrote `ansible-local`'s new SecretID into Semaphore's Vault KV slot, mismatched role_id+secret_id → Semaphore auth broke until corrected.
  - SecretIDs are NEVER managed by Terraform — generated manually with `vault write -f auth/approle/role/<role>/secret-id`, stored externally. See AppRole bootstrap runbook below.
- ESO ClusterSecretStore `vault` points at `http://vault.vault.svc.cluster.local:8200`, path `secret`, v2, kubernetes auth mount `kubernetes`, role `eso`
- All Vault config (mounts, auth methods, policies, roles) captured in Terraform (`terraform/vault/`). Local state, `VAULT_ADDR`/`VAULT_TOKEN` via env. Provider `hashicorp/vault ~> 4.0`. Scoped Terraform token and remote state deferred.
- **KV contents** (as of 2026-05-16): `secret/ansible/sftpgo/admin-password` (migrated from Ansible Vault as the D1 proof-of-pattern). Authentik bootstrap secrets pending.

## AppRole bootstrap runbook

Setting up a fresh control node (or re-bootstrapping after credential loss). Use this when:
- Setting up Ansible on a new MacBook / control node
- Rotating SecretID at the 90-day cadence
- Recovering from a lost SecretID
- Deploying AWX (same steps, against the `ansible-awx` role)

**Checklist:**

1. ✅ Terraform module `terraform/vault/` applied — AppRole auth method, `ansible` policy, and the relevant role exist.
2. Generate SecretID via `vault write -f auth/approle/role/<role>/secret-id`.
3. Capture RoleID via `terraform output <role>_role_id` (RoleID is not secret).
4. Stash in 1Password Homelab vault as a Login item (one per role; for `ansible-local` the item name is `Ansible - Vault - k3s`) with fields `url`, `method`, `username` (= RoleID), `password` (= SecretID), `secret_id_accessor`, `expires_at` (today + 90 days). Map role name → 1P item name in `$__homelab_approle_items` inside `homelab.fish`.
5. Load via `homelab-env` (control-node fish tooling — see sub-section below). No env file on disk.
6. Install `community.hashi_vault` Galaxy collection + `hvac` Python lib in the Ansible venv.
7. Test the lookup with `playbooks/test-vault-lookup.yml`.

**Detailed steps — MacBook → `ansible-local` role:**

Prerequisites: `pipx`-installed Ansible (Homebrew Ansible bundles its own externally-managed Python; injecting Python deps is awkward), `hvac` injected, fish shell, OBJC fork-safety env var set.

Generate the SecretID. Vault uses your root token here — anything that can mint SecretIDs is by definition privileged:

```fish
set -x VAULT_ADDR http://10.0.20.11:8200
set -x VAULT_TOKEN <root token from 1Password>
vault write -f auth/approle/role/ansible-local/secret-id
```

Output:
```
Key                   Value
---                   -----
secret_id             a8b3f7e2-...
secret_id_accessor    1f2a-...
secret_id_num_uses    0
secret_id_ttl         7776000
```

Capture the matching RoleID (non-secret) from Terraform output:

```fish
cd terraform/vault
terraform output -raw ansible_local_role_id
```

Store in 1Password under `Ansible - Vault - k3s` (Login item, Homelab vault):
- `url`: `http://10.0.20.11:8200`
- `method`: `approle`
- `username` (= RoleID): from `terraform output`
- `password` (= SecretID): from `vault write`
- `secret_id_accessor`: from `vault write` (used to revoke without knowing the SecretID; needed by `rotate-approle`)
- `expires_at`: today + 90 days

The field names `url`/`method`/`username`/`password` are what `homelab-env` reads via `$__homelab_env_map`. The item name is whatever you configured in `$__homelab_approle_items` — for `ansible-local`, that's `Ansible - Vault - k3s`.

The control-node fish tooling at `<repo>/.config/fish/conf.d/homelab.fish` reads those 1P fields and exports them as `VAULT_ADDR`, `ANSIBLE_HASHI_VAULT_AUTH_METHOD`, `ANSIBLE_HASHI_VAULT_ROLE_ID`, `ANSIBLE_HASHI_VAULT_SECRET_ID` when `homelab-env` is invoked. The `ANSIBLE_HASHI_VAULT_*` prefix is the canonical env-var form the `community.hashi_vault` collection reads; `VAULT_ADDR` stays as-is (standard Vault CLI env var).

Symlink once per control node:

```fish
ln -s <repo-path>/.config/fish/conf.d/homelab.fish ~/.config/fish/conf.d/homelab.fish
```

See the **Control-node fish tooling** sub-section below for details and extension points.

Usage: `homelab-env` once per shell session, then run playbooks normally.

Install collection + Python dep:

```fish
ansible-galaxy collection install -r ansible/requirements.yml
pipx inject ansible hvac
```

macOS-specific (one-time, fish universal variable). Prevents Python's macOS fork-safety crashes when Ansible workers fork after loading ObjC-linked libs like `urllib3`:

```fish
set -Ux OBJC_DISABLE_INITIALIZE_FORK_SAFETY YES
```

Verify end-to-end:

```fish
# Load env, mint a root token, seed a test secret
homelab-env
set-vault-token root
vault kv put secret/ansible/test/hello value=world

# Switch to AppRole and run the test play
set-vault-token approle
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/test-vault-lookup.yml
# Expected: "Got value from Vault: world"

# Cleanup
set-vault-token root
vault kv delete secret/ansible/test/hello
set -e VAULT_TOKEN
```

**For AWX (when deployed):** same steps against `ansible-awx` role. SecretID goes into AWX's credential store rather than a local env file. After deploy, restrict via `token_bound_cidrs` in `terraform/vault/main.tf` once the asgard K3s pod CIDR is known.

**Rotation (every 90 days):**

```fish
set-vault-token root           # mints VAULT_TOKEN from 1P
rotate-approle ansible-local   # mints new SecretID, prompts to update 1P, revokes old
set -e VAULT_TOKEN             # don't leave root token in env
homelab-env                    # picks up new SecretID into env
```

`rotate-approle` snapshots the old accessor *before* minting (so the revoke step targets the correct SecretID even after 1P is updated), and revokes the old SecretID only after you confirm 1P is updated. No window where 1P holds a stale value.

*Partial rotation recovery.* If the rotation aborts after 1P is updated but before the revoke step completes (Ctrl+C between the paste and the prompt, or a failed revoke step), Vault holds a SecretID that 1P no longer references. Run `rotate-approle --fix <role>` to find and destroy orphans — it reads the canonical accessor from 1P, lists all Vault accessors for the role, and offers to destroy any that don't match (with metadata shown for verification).

**Vault lookup syntax cheatsheet** for `community.hashi_vault.vault_kv2_get`:

| Accessor | Returns |
|---|---|
| `.raw` | Full Vault API response |
| `.data` | Wrapper (contains both KV data and metadata) |
| `.metadata` | Just version metadata (version number, created_time, etc.) |
| `.secret` | Just the KV key-value pairs — what you usually want |

Use `.secret.<key>` for the actual value:
```yaml
"{{ lookup('community.hashi_vault.vault_kv2_get', 'ansible/sftpgo/admin-password').secret.value }}"
```

## Control-node fish tooling

Single repo-tracked fish file at `<repo>/.config/fish/conf.d/homelab.fish`, symlinked to `~/.config/fish/conf.d/homelab.fish`. Sourced once at shell init; env vars only set when functions are invoked.

`conf.d/` rather than `functions/` because `functions/` uses autoload-by-filename, one function per file; `conf.d/foo.fish` holds many.

Public functions:

| Function | Purpose |
|----------|---------|
| `homelab-env` | Loads homelab env vars from 1P (`VAULT_ADDR`, `ANSIBLE_HASHI_VAULT_*`). Idempotent. |
| `set-vault-token <source>` | Sets `VAULT_TOKEN`. `root` pulls from 1P; `approle` mints via `vault write auth/approle/login` using already-loaded creds (fails loudly if `homelab-env` hasn't run). |
| `vault-root-token` | Pure value-producer; echoes the root token. |
| `rotate-approle <role>` | **1P-canonical flow.** Mints a new SecretID, prints values to paste into 1P, prompts for confirmation, revokes the old SecretID. `--fix` destroys SecretIDs in Vault that aren't in 1P (recovery for partial rotations). `--help` for usage + hazard notes. Use for `ansible-local` (MacBook). |
| `rotate-semaphore-approle` | **Vault-KV-canonical flow.** Mints a new SecretID for `ansible-awx`, writes it to Vault KV `secret/k8s/semaphore/vault-approle`, runs `terraform apply` in `terraform/semaphore/` to push it to Semaphore env id 1, prompts for drift-check validation, then revokes old SecretIDs. No 1P involvement — Vault KV is the source of truth for Semaphore's credentials. |

Machine-accessed items are referenced by **1P item UUID, not title** (see "1Password vault organisation" below). Each UUID is a named `__op_*` variable near the top of the file (self-documenting via a current-title comment); the env map / approle map reference those vars. This decouples automation from item names so the vault can be reorganised freely.

Extension points:
- **New env var loaded by `homelab-env`:** add a `__op_<name>` UUID var, then append a line to `$__homelab_env_map` in the form `"ENV_VAR|$__op_<name>|field"`. Do this in **both** `homelab.fish` and `.config/scripts/homelab.sh` (the `.sh` is repo-only source-of-truth, not currently deployed). No other code change.
- **New `set-vault-token` source:** add a `case <name>` branch to the function's switch block. Wire to a 1P read or another Vault auth method.
- **New AppRole role for rotation:** append a line to `$__homelab_approle_items` in the form `"role-name|$__op_<name>"`. The 1P item must have fields `username` (RoleID), `password` (SecretID), `secret_id_accessor`, `expires_at`.

The 1P vault name (`Homelab 2.0`) is lifted to `$__homelab_op_vault` at the top of the file — single-point edit if it ever changes.

### Vault-backed shim (`vault-homelab-env`) — the non-interactive / Claude path

The 1P-backed `homelab-env` needs `op` to be signed in (biometric/interactive), so it only works when the operator has run it by hand recently — no good for non-interactive runs (Claude, `claude remote-control`, cron). The **Vault-backed** `vault-homelab-env` needs no human: it AppRole-logs-in to Vault with an on-disk secret-zero and pulls the same IaC creds from `secret/ansible/frigg/*`. This is the preferred path for any of **Claude's own** multi-var machine commands.

- **Secret-zero:** `~/.config/ansible/vault-approle.env` (RoleID + SecretID). Re-synced after a `rotate-approle` via `homelab-env` (1P) then `seed-vault-approle`.
- **Cache:** `~/.cache/homelab/vault-env.sh` (POSIX) + `vault-env.fish` (fish), written by `vault-homelab-env`, **3h TTL**, SEPARATE from the 1P `env.{sh,fish}`. Holds the IaC set: `KUBECONFIG`, `VAULT_ADDR`/`VAULT_TOKEN`, `ADGUARD_*`, `CLOUDFLARE_API_TOKEN`, `AUTHENTIK_*`, `NETBOX_*`, `AWS_*`, `TF_VAR_proxmox_api_token`, `SEMAPHOREUI_*`, the `ANSIBLE_HASHI_VAULT_*` AppRole vars + `ANSIBLE_VAULT_PASSWORD_FILE`. The cached `VAULT_TOKEN` is a ~30 min AppRole token — `vault-homelab-env --refresh` re-mints it.
- **Self-healing one-liner** (re-fetches when the 3h cache is stale, fast cache-read when warm). `vault-homelab-env` isn't on PATH — it's defined in the repo shim, so source that first; calling it exports every IaC var into the current shell so the chained command inherits them:
  ```bash
  source "$(git rev-parse --show-toplevel)/.config/scripts/homelab.sh" \
    && vault-homelab-env >/dev/null && terraform apply
  ```
  The refresh path needs `vault` + `jq` on PATH (prefix `PATH="/opt/homebrew/bin:$PATH"`). The known-warm form `. ~/.cache/homelab/vault-env.sh && <cmd>` needs neither but does NOT self-heal a stale cache.
- **Adding a new IaC field:** append to `__vault_homelab_iac_map` in BOTH shim files (`.config/fish/conf.d/homelab.fish` + `.config/scripts/homelab.sh`), and to `control_node_iac_env_fields` in `roles/control-node` if Frigg should get it, then `vault-homelab-env --refresh`.

The 1P-backed `. ~/.cache/homelab/env.sh` / `homelab-env` stays the operator's interactive path at home. On **Frigg** the distinction is moot: its `homelab-env`/`env.sh` is *already* Vault-backed (no `op` on the box), so there `. ~/.cache/homelab/env.sh` IS the Vault path.

## 1Password vault organisation

The **Homelab 2.0** vault uses the title convention **`[Realm] - Consumer - Service - Detail`** (the bracket is a pseudo-folder — 1P has no in-vault folders; brackets also sort to the top). Established 2026-05-30 (Wave S1).

- **Realm**: `[Asgard]` (cluster + its services) · `[Bootstrap]` (must-survive-Vault-down) · `[Infra]` (hardware under the homelab — Proxmox / Synology / UCG / UniFi / personal AWS / fleet-wide SSH key) · `[DO - Offsite]` (DigitalOcean fallback env). `[Jotunheim]` reserved for the future second cluster.
- **Consumer** — the "what's it used for" axis: `Terraform` / `Ansible` (read live by automation — edit carefully) · `Mirror` (cold offline copy of a Vault machine-secret, DR-only) · `Manual` (human / break-glass / web logins).
- Examples: `[Asgard] - Terraform - NetBox - Admin API token`, `[Asgard] - Mirror - Postgres - Admin password`, `[Bootstrap] - Manual - Vault - Root token`, `[Infra] - Manual - Synology - Admin login`.

**Rename safety:** because the shim + TF reference machine items by UUID (above), renaming/retagging is non-breaking — `op://Homelab 2.0/<item-UUID>/<field>` resolves regardless of title. Two CLI caveats: `op item edit --title` cannot rename **SSH-Key items** or **Password items missing a `ps` value** (rename those in the 1P app); everything else renames via CLI.

Every NEW Homelab-vault item follows the convention; if it's machine-read, add its UUID as a `__op_*` var (not the title) in both shim files.

Source-of-truth posture: AppRole RoleID/SecretID and the Vault root token live in 1P only. No copies on disk on the control node. Rotation = update the 1P item; next `homelab-env` picks up the change.

## OpenBao migration (future)

HashiCorp Vault moved to BSL in 2023 and HashiCorp was acquired by IBM in 2025. OpenBao is the Linux Foundation fork at near-parity. Migration is essentially a binary swap (same API, same Terraform provider, same Ansible lookups). Plan: migrate at the next major maintenance window once OpenBao has another ~12 months of production track record, likely 2026 late-year or 2027.
