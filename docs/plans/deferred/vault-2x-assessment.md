<!-- docs/plans/deferred/vault-2x-assessment.md -->

# Vault 1.21.2 → 2.x — breaking changes vs our usage (assessment, 2026-10-01)

Research only — **nothing applied**. Running: Vault **1.21.2** (OSS, 3-node Raft, AWS KMS auto-unseal, chart 0.34.1 with `server.image.tag` pinned 1.21.2). Latest: **2.1.1** (2.0.4 is the chart default). Sources: the Vault `CHANGELOG.md` (2.0.0 → 2.1.1 in full), HashiCorp's docs source (`web-unified-docs`: *Important changes*, *Deprecations*, *Upgrade*, *HA upgrade*, *Rollback*), the auth-plugin release notes, and our repo + live cluster. Companion to [chart-bumps-2026-09.md](../active/chart-bumps-2026-09.md) §3.

## Verdict

**One change affects us: `sys/generate-root` (and `sys/rekey`) now require a valid Vault token — the break-glass root-token recovery path.** Everything else either doesn't apply to our footprint or is already satisfied. No config change is *required* for the server to start and serve our workloads; one decision (break-glass) and two verifications (Terraform provider, OIDC login) belong in the rollout.

## Change-by-change

| Change (version) | Affects us? | Evidence |
|---|---|---|
| **`sys/generate-root`, `sys/rekey`, DR `generate-operation-token` require a Vault token** (2.0.0; opt-out `enable_unauthenticated_access = ["generate-root","rekey"]`) | **YES — break-glass only** | Routine rotation is unaffected (`rotate-vault-root-token` = `vault token create -policy=root -orphan -ttl=0`, authenticated by the current root token). The recovery used on 2026-09-03 (`vault operator generate-root` with recovery keys, no valid token) will no longer work unauthenticated. No generate-root runbook exists yet ([open-questions](../../operations/open-questions.md)). See *Decision* below. |
| Non-canonical request paths (`//`, `/./`, `/../`) rejected (2.0.0); **redirected to the cleaned path instead from 2.0.3** | No | ESO store `path: secret` v2, all 19 ExternalSecret keys are clean `k8s/…`; Ansible `vault_kv2_get` paths clean `ansible/…`; no `//` anywhere in repo (grepped) |
| Docker image: `IPC_LOCK` required (2.0.1) then **removed again (2.0.2)** — containers can't `mlock()`, use `disable_mlock = true` | No | Live log `Mlock: supported: true, enabled: false`; chart renders `disable_mlock = true`; process runs uid 100 with no caps. **Don't pin 2.0.1** (needs IPC_LOCK or won't start). |
| Image user/group | No | 2.x Alpine image still `addgroup vault && adduser -S -G vault vault` → uid 100 / gid 1000, same as our `chown 100:1000` init container + `fsGroup 1000` |
| Duplicate HCL attributes now a parse error (2.0.4) | No | Merged server config: one each of `ui`, `disable_mlock`, one `listener`/`storage`/`seal`/`service_registration`; the 4 TF policies have a single `capabilities` per path |
| Wildcards/globs in identity-template policy output denied (2.0.1) | No | No `{{identity…}}` templated policies |
| LIST + more-specific `deny` now honoured (2.0.3) | No | None of our policies use `deny` |
| RSA keys > 8192 bits rejected (2.0.2, CVE-2026-39829) | No | No PKI/SSH/Transit engines; TLS certs come from cert-manager, not Vault |
| `identity/entity/merge` needs `sudo` (2.0.1) | No | Not used in IaC |
| `max_token_header_size` 8 KB (2.0.0) | No | Enterprise-relevant (large external JWTs); our tokens are opaque, OIDC login is a browser flow |
| Auth plugins: `jwt/oidc` v0.25.0 → v0.26.4, `kubernetes` v0.23.1 → v0.24.1 | No (additive) | kid-based JWKS cache, Graph/Okta group fetching, housekeeping; nothing changes OIDC-to-Authentik or ESO's Kubernetes auth config. Still smoke-test both after the roll. |
| UI path `/secrets` → `/secrets-engines` (2.0.0) | No | No deep links to the Vault UI secrets path in repo/docs/Startpage |
| Audit entries gain `supplemental_audit_data` (2.0.0) | No | No audit device in IaC or docs (could not verify the live device list without a privileged token) |
| Raft `retry-join` concurrency cap 20 (2.1.1), Autopilot automated upgrades | No | 3 nodes; automated upgrades are Enterprise |
| UBI image package removals (2.0.4), OCI layout (2.0.0) | No | We use the Alpine image |
| Deprecations (PKI `allow_token_displayname`, LDAP null bind, Snowflake password auth, Agent API proxy, `allowed_parameters` on LIST, Service Broker) | No | None in our footprint |
| 2.1.0 licence text change (Agentic IAM terms) | Awareness | Read before adopting 2.1.x |
| **Community 1.21 line ends at 1.21.4** | Context | Docker Hub has no 1.21.5+ community image; the "1.21.5/1.21.6/1.21.7 backport" rows in HashiCorp's *Important changes* are Enterprise patches. For us these changes first appear in 2.0.x. |

## Our Vault footprint (what we checked against)

KV v2 at `secret/` (31 TF-minted secrets), AppRole (3 roles: ansible-local / ansible-awx / semaphore), Kubernetes auth (ESO), 1 OIDC backend (Authentik, `homelab-admin`), 4 policies (`eso`, `ansible`, `homelab-admin`, `homelab-frigg`), AWS KMS seal, Raft, TLS listener via the cert-manager internal CA fronted by Traefik `BackendTLSPolicy`, injector/CSI disabled. Clients: ESO, Ansible `community.hashi_vault`, Terraform `hashicorp/vault` **4.8.0**, Semaphore, the `vault` CLI (**already v2.1.1 on the control node**, working against the 1.21.2 server), the `vault-homelab-env` / `rotate-vault-root-token` fish tooling.

## Decisions / verifications before rolling

1. **Break-glass (`generate-root`) — owner decision.** The default now needs a valid token *plus* the recovery-key fragments. Options: **(a)** provision a dedicated break-glass policy/AppRole token that is allowed on `sys/generate-root/*` and keep it in 1Password (needs verifying exactly which capability the new check requires — test on the canary pod, don't assume); **(b)** set `enable_unauthenticated_access = ["generate-root"]` in the server config (keeps today's behaviour; weaker against someone spamming bogus key fragments to block a legitimate attempt); **(c)** accept it — the owner's stated position is that a full rebuild is acceptable because data lives outside K3s. Whatever is chosen, write the `generate-root` runbook against 2.x behaviour (open item already tracked).
2. **Terraform provider.** Server hop with provider **4.8.0**, then `terraform plan` in `terraform/vault` must show **no changes**. Provider 5.x officially supports Vault ≥ 1.19 (and adds 2.0-only resources) but is a **separate change**: `vault_kv_secret_v2` stops tracking secret data in state (we have 31 TF-minted ones with `data_json`), `data` is deprecated, auth-backend tune handling changes; needs Terraform ≥ 1.11 (we run 1.16.4).
3. **Raft snapshot first** (`vault operator raft snapshot save`, needs a root-capable token from 1Password — interactive). Vault does not support automatic rollback; the documented rollback is *restore the pre-upgrade snapshot onto the old version*. Keep a copy **off the cluster** (ties to the CRITICAL off-homelab export item in [open-questions](../../operations/open-questions.md)). Per the owner's preference: roll **forward** if it misbehaves; the snapshot only matters if forward is impossible and a rebuild is the alternative.
4. **Smoke tests after the canary pod:** `vault status` (unsealed via KMS, Raft index parity), ESO store `Valid` + 19/19 ExternalSecrets, **OIDC login via Authentik in the UI**, AppRole login (`vault-homelab-env`, Semaphore), `terraform plan` clean, and a deliberate check of the generate-root/rekey auth behaviour (start + cancel an attempt) so the runbook is based on what 2.x actually does.

## Suggested path

`1.21.2 → 2.0.4 → 2.1.1`, one major/minor at a time (HashiCorp's docs give no skip-version rule; each hop is cheap). Set `server.image.tag` explicitly (the StatefulSet is `OnDelete`, so nothing rolls until pods are deleted): **standbys first, active last, one pod at a time** (never fail over from a newer to an older node), confirming 3/3 unsealed + equal Raft index between pods — the procedure used on 2026-09-30 for the 0.34.1 chart roll. Avoid **2.0.1**. Expected impact per pod restart: a few seconds of standby/leader election, no data migration step documented for Raft.
