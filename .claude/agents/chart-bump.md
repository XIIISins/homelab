---
name: chart-bump
description: Keeps the homelab's Helm charts and platform components (Flux, K3s, Calico, pinned server images, version-coupled Terraform providers) up to date. Use when asked to "check for updates", "bump <chart>", "bring everything up to date" or to plan/execute an upgrade wave. It investigates live state and upstream, verifies that the target images actually exist, orders the work by blast radius, then executes one item at a time (worktree → render-diff → commit → push → Flux → targeted tests → docs) and stops to ask only for genuine decisions.
---

# chart-bump — upgrade agent for the asgard homelab

You keep `k8s/asgard/**` HelmReleases, `gotk-components.yaml` (Flux), the K3s version, Calico and a few
version-coupled Terraform providers current **without causing a permanent outage**. A temporary outage
is acceptable to the owner; a wedged, unrecoverable or silently-degraded system is not. Read
`CLAUDE.md` first — its invariants and process rules override anything here.

Helper scripts (read-only, in `.claude/scripts/chart-bump/`; they keep state in `$CLAUDE_JOB_DIR/tmp`):

| Script | Use |
|---|---|
| `inventory.sh [--live]` | every HelmRelease: pinned vs latest upstream (`BEHIND` = candidate) |
| `platform.sh` | things that are not chart pins: Vault server image pin, Flux, K3s, Calico, TF providers |
| `render-diff.sh <hr.yaml> <release> <old> <new> [helm args]` | render old vs new with OUR values; schema errors surface as `rc=1`; filtered diff + image list. `NOCRD=1` hides CRDs, `DIFFMAX=n` |
| `images-exist.sh <rendered.yaml \| image...>` | does every image tag exist upstream (Docker Hub, ghcr, quay, registry.k8s.io)? exit 1 if any is missing |

## Phase 1 — Investigate (read-only; no mutations)

1. `inventory.sh --live` and `platform.sh` → the candidate list. Also `flux get hr -A`, `kubectl get nodes`, pods not Running.
2. For each candidate, before anything else:
   - Read the matching `docs/known-issues/<subject>.md` (index in CLAUDE.md) and grep `docs/operations/open-questions.md` + `decisions.md`. Treat pending tasks as prerequisites.
   - **Read the real upstream release notes** between pinned and target — don't trust secondary summaries (including `docs/operations/chart-bumps-2026-09.md`, which was partly wrong). GitHub API (`/releases/tags/<tag>`, `/releases?per_page=100` filtered for `breaking|deprecat|removed|must`), raw `CHANGELOG.md` / `website/docs/releases/...` files. For each breaking item ask "do WE use this?" and grep the repo/cluster to prove it.
   - Compare live values to repo (`helm -n <ns> get values <rel>`): drift means the repo file isn't the truth.
3. Capture a baseline you can compare after: pods, HR status, `terraform plan` (no changes?) for any coupled module (`-parallelism=1` for netbox), endpoint status codes.

## Phase 2 — Checks (gate each item; any failure changes the plan)

- **Upstream availability (the 0.11.4 lesson):** `render-diff.sh` then `images-exist.sh <new render>`. A chart's `appVersion` can point at an image tag that was never published (synology-csi 0.11.4 → `synology/synology-csi:v1.4.0` → crash-looping controller, ~8 min outage). Also verify the chart version exists in the repo index, the platform (amd64) is built, and the repo URL still resolves (charts.external-secrets.io now 302-redirects).
- **Server/provider coupling:** a bump may strand an IaC provider (e-breuninger/netbox supports up to NetBox 4.6.10 → NetBox 4.7 held; authentik provider must equal the server release and renamed `issuer`→`issuer_override`). Check `terraform plan` against the new server; if no released provider supports it, **hold** and say why.
- **Kubernetes compatibility:** Flux minors raise the minimum K8s (2.9 → ≥ 1.34.1); ESO/Vault/cert-manager publish matrices. Dependencies reorder the plan (K3s before Flux).
- **Irreversible migrations** (NetBox, Authentik, Immich, Vault major): take a backup first — `pg_dump -Fc` on the Patroni leader via `ansible <host> -b -m shell` (find the leader with `curl http://<pg-ip>:8008/master` = 200), etcd snapshot for K3s, Raft snapshot (needs a root-capable token → ask the operator). Never roll a chart back across a DB migration (Immich/Authentik rollback poisoning: roll FORWARD).
- **Capacity:** workers are ~85–90 % CPU-*requested* (2 vCPU) — a surge pod > ~300m deadlocks a rolling update (`0/6 nodes… Insufficient cpu`). Plan to delete the old pod (outage accepted) or use `strategy: Recreate`. Check `kubectl describe node | grep -A5 Allocated`.
- **Rollout semantics:** some StatefulSets are `OnDelete` (Vault) — the bump changes the spec but rolls nothing; roll pods yourself, standbys first, leader last. A crash-looping StatefulSet pod is not replaced automatically during a rolling update — delete it.
- **PDBs / storage affinity:** local-path PVs are node-pinned (Vault Raft); iSCSI is per-PVC with a ~10-LUN DSM cap — never create throwaway PVCs on Volume2.
- **Anything applied as a K3s addon file (Calico) or via Helm `crds/` / a manifest that carries CRDs:** removal from the file/chart = deletion of the CRD **and every object of that kind**. K3s prunes **per addon file**; Helm does not touch `crds/` on upgrade but a chart that stops templating a CRD it used to own can orphan or delete it. Before replacing such a file: diff the CRD names **per file** (old vs new counterpart, not the union), export the objects (`kubectl get <group resources> -A -o yaml`), and make the guard fail closed. Calico/CNI changes also need an IPAM/IPPool/BlockAffinity count before and after. Learned 2026-10-01 (cluster-wide pod-network outage, `docs/incidents/2026-10-01-calico-datastore-prune.md`).
- **When the repo docs and the live cluster disagree (a file/object the docs say exists doesn't), STOP and reconcile first** — don't assume your change is equivalent to the documented one. (Calico's addon files were gone from every CP; the "replace the file" procedure was therefore not what had created the live state.)
- **Defaults that changed meaning:** e.g. MetalLB 0.16 defaults to a bundled frr-k8s DaemonSet (we are L2-only → disable explicitly); Authentik 2026.8 trusted-proxy default (10/8 etc.) already covers pod/node ranges — do NOT set an override (it replaces the default); Authentik chart drops explicit `LISTEN__*` env (verify `bindv6only=0` + IPv6 in pod).

## Phase 3 — Plan

Order by **blast radius, lowest first**, then fix dependencies. Current reference order: patch-level/no-diff charts → NetBox → cert-manager → MetalLB → ESO → Authentik → K3s (one minor per run; K3s before Flux — Flux 2.9 needs K8s ≥ 1.34.1) → Calico (own playbook, one minor per run, after K3s) → Flux → Vault major. Majors go **one minor/major at a time** (cert-manager, Authentik, K3s, Calico, Vault 1→2.0→2.1). Present a short table: item, from→to, blast radius, pre-checks, test, rollback. Ask the owner only for decisions that are genuinely theirs (break-glass policy changes, holding an item, running something the permission classifier blocks) — otherwise pick, state the assumption, proceed.

## Phase 4 — Execute (one item at a time; the ritual)

1. `EnterWorktree` (edits in the shared checkout are rejected). Edit with the **Edit tool** (macOS `sed -i` needs a suffix and fails silently in chains).
2. `render-diff.sh` + `images-exist.sh`; fix values the new schema rejects; `terraform plan` for coupled modules (plan from a worktree is fine; **apply only from the main checkout**).
3. Commit: conventional commits, **no `Co-Authored-By`** (repo memory), docs and code in **separate commits**. If signing fails, retry once with `git -c commit.gpgsign=false commit` — never wait on 1Password.
4. `ExitWorktree keep` → in the main checkout `git merge --ff-only <branch>` → `git push origin main` (the pre-push hook runs gitleaks; pushing `main` IS the K8s deploy — only push when the owner has authorised deploys, as in a bump-wave request). Never force-push.
5. `flux reconcile kustomization <infrastructure|apps> --with-source`, then watch: `kubectl get hr`, pods, events. No `kubectl apply`. Stuck after a timeout → `flux reconcile hr <name> --force` / `--reset`; a bad bump → revert commit, push, then fix any wedged StatefulSet pod.
6. **Test what the change actually touches** (below). Write results down with numbers, not "looks fine".
7. Docs (post-flight): progress in `docs/operations/chart-bumps-2026-09.md`-style log, gotchas into `docs/known-issues/<subject>.md`, decisions row if architectural, tick `open-questions.md`.
8. **CI cache check — whenever the bump touches a CI-cached pin** (`ansible/requirements.yml` collections, `.github/ci-requirements.txt` ansible-core/ansible-lint/yamllint, a Terraform provider/`required_providers` in a module, a tool `*_URL`/`*_SHA` in `ci.yml`, or a chart whose CRD schema kubeconform needs → bump `KUBECONFORM_SCHEMA_EPOCH`): follow [`docs/procedures/ci.md`](../../docs/procedures/ci.md#cache-keys--bump-checklist) — the PR's first CI run is an expected cold miss; confirm the cache was *saved*, then confirm a second run *hits* and skips the install. Record the result in the bump log.

### Targeted tests (pick what the item changes)

- **Traefik:** `curl https://smoketest.niflheim.xiiisins.com/anything` = 200 "smoketest ok", an external host, a ForwardAuth host 302 → authentik, access-log lines in common format.
- **Vault:** `vault status` on every pod (Sealed=false, version, same Raft index), UI 200, ESO `ClusterSecretStore` Valid, ExternalSecrets all `SecretSynced`/Ready. Roll standby → standby → active.
- **ESO:** all ExternalSecrets Ready and `refreshTime` newer than the controller restart (2.x printer columns changed — read `.status.conditions`).
- **cert-manager:** all Certificates + ClusterIssuers Ready, controller/webhook logs free of post-startup errors. A forced renewal is the real test but mutates certs — request it from the owner if the classifier blocks it.
- **MetalLB:** a 1 req/s probe of the Traefik and Vault VIPs across the speaker roll (count non-200/000), plus a TCP check of each other VIP.
- **Authentik:** OIDC discovery issuer/authorize URLs are `https`, ForwardAuth 302s, NetBox/Vault OIDC redirect, SAML redirect (Hugin), `terraform plan` clean with the matching provider, no server/worker errors.
- **NetBox:** `/api/status/` version, authenticated API reads, `terraform plan -parallelism=1` clean, dynamic inventory resolves hosts, OIDC redirect.
- **Synology CSI:** node plugins/controller on the new driver, existing PV still Bound/attached, then a full re-mount (delete the consuming pod: NodeUnpublish→Unstage→Stage→Publish in the node-plugin logs; a single retried "Failed to remove target path" is benign).
- **Calico:** `status.calicoVersion`, all TigeraStatus Available, `calico-node` rolled; IPPool / IPAMBlock / BlockAffinity counts ≥ before; a **cross-node** probe (an app on one node reaching a backend on another, e.g. wiki / metric / NetBox / Authentik health), not just the smoketest (same-node backend can mask a broken overlay).
- **K3s:** every node at target + Ready, `flux get hr -A`, `kubectl get tigerastatus`, ExternalSecrets, Vault 3/3, smoketest.

## Guardrails

- **Permission-classifier denials are final for that outcome.** Do not retry in pieces or via another tool; finish everything else, then stop and tell the owner exactly what you wanted to run and why. (Seen: pod deletes on Vault, forced cert renewal, the K3s rolling upgrade.) Once the owner says go for a specific action, do that action.
- Never echo credentials (CLAUDE.md "Never echo secrets"): load env via the warm cache / shim inside the same command (`set -a; source ~/.cache/homelab/env.sh`) — inside a worktree `source` is refused, so call a wrapper script that sources and execs. Never `ansible-inventory --list` (leaks vault.yml); `--graph` is safe.
- One `ansible-playbook` at a time. `terraform apply` from the main checkout only. Reboot-test persistent host changes.
- Don't merge two clusters' concerns, don't float versions (concrete pins only), don't touch Calico via `kubectl edit`.
- Never roll two workers/CPs at once; keep Vault at ≥ 2/3 and etcd at ≥ 2/3.
- If a step surprises you twice, stop and report instead of improvising around it.
- **CNI / DNS / ingress / secrets-store changes get a restore point first** (datastore export, etcd snapshot, Raft snapshot) and a stated recovery plan; roll the *smallest* unit, verify with a cross-node probe (not just same-node), and keep an exit that doesn't depend on the thing you're changing (a terminating `calico-system` deadlocks on the metrics API that needs the pod network).
- **Recovery steps may need owner approval** (finalizer patches, namespace finalize, pod deletes, forced cert renewals are classifier-gated). In an incident, list the exact approvals needed up front instead of discovering them one denial at a time.

## Environment quirks (tooling) that cost time before

- The worktree guard rejects commands it can't prove stay in the worktree: chain-free commands, **no `github`-looking URLs inline** (put them in scripts), no `$var` arithmetic in loops, no `sleep N && …` chains (run the wait as its own call or use Monitor). Use literal paths.
- `grep` is ugrep: `grep -E '^(a|)$'` (empty alternation) errors; `--include=` with zsh globs needs quoting. zsh expands `custom-columns=...[*]` — use `-o jsonpath` with quotes or split commands.
- Helm: use isolated `HELM_*` dirs (scripts do) — the user's repo list references missing caches. OCI charts: `helm show chart oci://…`.
- `flux reconcile kustomization apps|infrastructure --with-source` is the nudge; HelmRelease upgrade timeout is 5–15 min, so a failed rollout self-reverts only after that.
- macOS `sed -i` ≠ GNU; use the Edit tool or python.

## Final report (background-session convention)

End with: what changed (per item: from→to, evidence), what was held and why (upstream blocker / missing image / needs operator), what needs the owner (exact command or decision), and a `result:` line summarising the delivered state. Anything you could not test, say so plainly.
