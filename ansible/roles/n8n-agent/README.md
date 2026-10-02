<!-- ansible/roles/n8n-agent/README.md -->

# n8n-agent

n8n as the AIOps diagnosis agent on **Gná** (LXC 1121, Urd, `10.0.11.221`). Plan: [`docs/operations/10d-diagnosis-chatops.md`](../../../docs/operations/10d-diagnosis-chatops.md). Operate: [`docs/procedures/aiops-diagnosis.md`](../../../docs/procedures/aiops-diagnosis.md). Gotchas: [`docs/known-issues/n8n-aiops.md`](../../../docs/known-issues/n8n-aiops.md).

Native systemd, no container: a pinned official Node tarball + a pinned `n8n` npm release under an unprivileged `n8n` user, loopback listener only. [`caddy-reverse-proxy`](../caddy-reverse-proxy/) is the only off-host surface (`:8081`, IP-allowlisted, `/webhook/*` only; see `group_vars/n8n_agent.yml`).

## What it does, in order

1. Reads the secrets from Vault (encryption key, owner password, per-source ingest tokens, the `#diagnoses` webhook) and **fails with the fix** if one is missing.
2. Installs the pinned Node (sha256-verified) and the pinned n8n.
3. Writes the encryption key as a 0400 **file** and the env file (0600); the owner account is created from the env on first start (no UI step).
4. Installs/starts `n8n.service`.
5. Imports `aiops/n8n/workflows/*.json` + the ingest credentials, publishes them and restarts n8n — **only when their checksum changed** (a restart interrupts in-flight agent runs).
6. Smoke test: an unauthenticated POST to each ingest route must be refused with 403 (proves the route is registered and auth is enforced, and posts nothing to Discord).

## Variables worth knowing

| Variable | Meaning |
|---|---|
| `n8n_version`, `n8n_node_version` / `n8n_node_sha256` | The two pins. n8n needs Node ≥ the `engines` range; bump both together |
| `n8n_ingest_sources` | Alert sources allowed to post. Must match `terraform/vault` `local.n8n_ingest_sources` (CI-checked) |
| `n8n_import_workflows` | `false` skips steps 5–6 (e.g. for a bare reinstall) |
| `n8n_nodes_exclude` | Node types refused at runtime (`NODES_EXCLUDE`) |

## Tags

`n8n`, `n8n:preflight`, `n8n:packages`, `n8n:user`, `n8n:layout`, `n8n:install`, `n8n:config`, `n8n:service`, `n8n:workflows`, `n8n:verify`.
