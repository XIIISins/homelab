<!-- docs/procedures/ci.md -->

# CI gate + branch ruleset

*What runs on every PR, how to reproduce it locally, how to change it, and how the `main` ruleset is applied. Decision row: [decisions.md](../operations/decisions.md) "CI gate + branch ruleset". Gotchas: [`known-issues/ci-github-actions.md`](../known-issues/ci-github-actions.md).*

Pushing `main` **is** the K8s deploy (Flux), so `main` only accepts changes that passed CI. CI is static: it never touches a cluster, hypervisor, Vault or Terraform state.

## What runs

Workflow: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). Triggers: `pull_request` → `main`, `push` → `main`, manual `workflow_dispatch` (only works once the file is on `main`).

| Job | Checks | Runs when |
|---|---|---|
| `secrets (gitleaks)` | `gitleaks git --redact` over the **entire repo history** (every commit on every fetched ref, ~2 s), not just the PR's commits. Policy: [`.gitleaks.toml`](../../.gitleaks.toml) | always |
| `yamllint` | YAML parses and is unambiguous ([`.yamllint.yml`](../../.yamllint.yml) — deliberately not a style gate) | a `*.yaml` / `*.yml` / `.yamllint` file changed |
| `actionlint` | workflow syntax + embedded shell | `.github/workflows/**` changed |
| `terraform fmt` / `terraform validate (<module>)` | `fmt -check -recursive`; per module `init -backend=false` + `validate` (no plan, no state) | a `terraform/**` module changed |
| `kubernetes` | `kubectl kustomize` of every `kustomization.yaml` under `k8s/`, then kubeconform `-strict` with the datreeio CRD catalog | `k8s/**` changed |
| `ansible-lint` | profile in [`ansible/.ansible-lint`](../../ansible/.ansible-lint), collections installed from `ansible/requirements.yml` (cached — see [Cache keys](#cache-keys--bump-checklist); Galaxy is only hit when a pin changes) | `ansible/**` changed |
| `docs links` | relative Markdown links + `#anchors` resolve | any `*.md` changed |
| `aiops (schemas + consistency + tests)` | `python3 aiops/tools/lint.py` (schemas, runbook markers in the docs, action registry vs `terraform/semaphore/templates.tf` + playbooks, routing, fixtures) + `unittest` ([`aiops/README.md`](../../aiops/README.md)) | `aiops/`, `ansible/playbooks/aiops-*`, `terraform/semaphore/`, or a docs change adds/removes a `<!-- runbook: RB-… -->` marker or deletes (or renames) a doc file |
| **`CI gate`** | aggregator: fails if any job above failed/cancelled; *skipped* counts as pass | always — **the only required check** |

A Markdown-only PR therefore runs `changes`, `secrets (gitleaks)`, `docs links`, `agent-scope` (a security gate, kept on every PR) and `CI gate`; prose edits to docs never start `yamllint` or `aiops`.

Changes to `.github/**`, `.yamllint.yml`, `.gitleaks.toml`, `ansible/.ansible-lint` or `ansible/requirements.yml` run everything.

**Not covered (by design):** Helm `values` correctness, Flux `${var}` substitution, anything that needs the live cluster (admission webhooks, ownership), `terraform plan` (needs state + creds — `apply` runs from the main checkout only), Ansible *behaviour* (lint ≠ converge; the Semaphore drift-check covers that).

## Reproduce locally

Every step is a script; versions + sha256 are the `env:` block of `ci.yml`.

```bash
.github/scripts/ci-k8s.sh                              # needs kubectl + kubeconform
.github/scripts/ci-terraform.sh terraform/vault        # needs terraform; one or more module dirs
terraform fmt -check -recursive terraform
python3 .github/scripts/ci-doc-links.py
pip install -r aiops/requirements.txt && python3 aiops/tools/lint.py && python3 -m unittest discover -s aiops/tests
yamllint .                                              # pip install -r .github/ci-requirements.txt
gitleaks git --no-banner --redact .                     # >= 8.29.1 — see known-issues
.github/scripts/ci-changes.sh origin/main HEAD          # which jobs would run
```

## Changing CI

- **Bump a tool:** edit URL **and** sha256 together in the `env:` block (`curl -fsSL <url> | sha256sum`); `install-tool.sh` refuses a mismatch. The sha is also the cache key, so the bump busts the tool cache — then run the [bump cache checklist](#cache-keys--bump-checklist). GitHub Actions pins (`uses: …@<sha> # vX.Y.Z`) are bumped by Dependabot ([`.github/dependabot.yml`](../../.github/dependabot.yml)). Terraform/Helm/Ansible pins stay with the `chart-bump` agent.
- **Add a job:** add it to `ci-gate.needs` or it is not gated. If it is path-conditional, add its path rule to `ci-changes.sh`.
- **Never** add a workflow-level `paths:` filter to `ci.yml` — a required check skipped that way never reports and blocks every non-matching PR.
- **Never** make a second check "required"; fold it into `CI gate` so the ruleset stays one line.

## Cache keys + bump checklist

Everything slow is cached with `actions/cache` (SHA-pinned). Each key is derived from the pin that produces the content, so **bumping the pin busts the cache automatically** — the job after a bump is a deliberate cold run. The one exception is the CRD schema cache (a float upstream), which has a manual epoch.

| Cache | Path | Key (busts when…) | Job(s) |
|---|---|---|---|
| Tool downloads (gitleaks, actionlint, terraform, kubectl+kubeconform) | `~/.cache/ci-tools` (`TOOL_CACHE`, files named by sha256, re-verified on every use) | `tool-<name>-<pinned sha256>` — the `*_SHA` in `ci.yml` `env:` changes | secrets, workflows, terraform-*, kubernetes |
| Terraform providers | `~/.cache/terraform-plugins` (`TF_PLUGIN_CACHE_DIR`) | `tfproviders-<TERRAFORM_SHA>-<module>-<hash of module/*.tf>`; `restore-keys` falls back to the module's previous set, `init` fetches only what's missing | terraform-validate |
| kubeconform CRD schemas | `~/.cache/kubeconform` | `kubeconform-schemas-<KUBECONFORM_SCHEMA_EPOCH>-<KUBECONFORM_SHA>-<hash ci-k8s.sh>` | kubernetes |
| pip wheels | `~/.cache/pip` | `pip-ansible-` / `pip-yamllint-<hash of ci-requirements.txt>` | ansible-lint, yamllint |
| Galaxy collections | `ansible/collections` | `galaxy-<os>-<hash of requirements.yml + ci-requirements.txt>`; saved right after the install succeeds (not at job end) so a lint failure doesn't discard it | ansible-lint |

### When you bump a pin — check the cache behaved

Applies to every bump (chart-bump agent, Dependabot action bumps, manual): collection / provider / ansible-core / terraform / tool pins.

1. **Bump the pin in the file the key hashes** (table above). If you bump a Helm chart / operator and need a fresh CRD schema, also bump `KUBECONFORM_SCHEMA_EPOCH` in `ci.yml`.
2. **Push; the first run on the PR is expected to be a cold miss.** In the job log, the cache step says `Cache not found for input keys: …` and the install step runs. That is correct — not a regression.
3. **Confirm it was saved:** the job's post step logs `Cache saved with key: …` (galaxy: the `save galaxy collections` step). No "saved" line = the next run will be cold again; find out why before merging.
4. **Confirm the hit:** push an empty commit (or re-run the job) and check the log says `Cache restored from key: …` and the install step is *skipped* (`install pinned collections` greyed out; tools print `using cached download`). If the key didn't change between the two runs but it still missed, the key is hashing something volatile — fix the key.
5. **Unexpected hit after a bump** (cache restored although you changed a pin) means the pin isn't in the key's inputs — add it. A stale cache is worse than a slow one.

Caches are scoped to the branch that created them but a PR can read `main`'s; after merge the `main` push run re-saves under the same key. Entries unused for 7 days are evicted (10 GB repo cap), so a quiet fortnight means one cold run — also fine.

## Branch + PR flow

Branches: `feat/<descriptive>`, `doc/<descriptive>` (also `fix/`, `chore/`). Conventional-commit subjects. One PR per branch. After the ruleset is on, `main` cannot be pushed to directly (repo admin can bypass — break-glass only).

```bash
git switch -c feat/<name> origin/main
# ...commit...
git push -u origin feat/<name>        # pre-push gitleaks hook scans the entire repo (needs `git config core.hooksPath .githooks` once per clone)
# open the PR; then, once CI is green, either click Merge or:
#   PR page → "Enable auto-merge" (squash or rebase; merge commits are disabled)
```

Auto-merge is for docs and routine bumps. Anything touching `terraform/`, `ansible/` or `k8s/` gets a diff review before merging (CLAUDE.md "Mutating operations").

## Applying the ruleset (the last step — operator, from the main checkout)

**Status: applied 2026-10-01** (ruleset id `24308920`). Surprises on the way: (1) the first `main` push run failed — a Galaxy 502 poisoned the retry loop, fixed in #10; (2) the first plan wanted to null `description` / `has_issues` / `has_projects` on the imported repo (provider plans omitted args as null) — pinned in #11 before applying. Applied with `gh auth token` injected inline (broad `repo` scope) rather than the fine-grained PAT below; the fine-grained PAT has since been minted into 1P (see follow-ups).

Module: [`terraform/github/`](../../terraform/github/). **Order matters** — requiring a check that has never reported blocks every PR, including the one that would fix it.

1. Merge the CI PR to `main` and confirm `CI gate` reported green on the `push` run.
2. Mint a **fine-grained PAT** at github.com → Settings → Developer settings: *only* `XIIISins/homelab`, permissions **Administration: Read and write** + **Metadata: Read**, 90-day expiry. Store it in 1Password as "Terraform - GitHub - token" (human-only: minted by hand, not by TF).
3. Plan and **read the plan** — it must show only the ruleset being created and `allow_auto_merge` / `delete_branch_on_merge` (+ merge-method flags) changing on the imported repo; anything else changing means the `github_repository` block is overriding a setting you want to keep:
   ```bash
   cd terraform/github && terraform init
   GITHUB_TOKEN="$(op read 'op://Homelab 2.0/Terraform - GitHub - token/token')" terraform plan
   GITHUB_TOKEN="$(op read 'op://Homelab 2.0/Terraform - GitHub - token/token')" terraform apply
   ```
4. Verify: open a throwaway PR — it must show `CI gate` as required; try `git push origin main` from a clone — must be rejected (as non-admin / without bypass).

**Rollback / break-glass:** Settings → Rules → Rulesets → `main` → *Enforcement: Disabled* (instant, no Terraform), or `terraform destroy -target=github_repository_ruleset.main`. Repo admins can also merge past a red/missing check via the bypass actor.

## Open follow-ups

- ~~PAT in the shim~~ done 2026-10-01: 1P item UUID `mhazmcb4jfsstiuicjrowljmai` → `GITHUB_TOKEN` in `homelab-env`; `github_token` field in `secret/ansible/frigg/iac-env` → `vault-homelab-env`. Plan/apply is now `source .config/scripts/homelab.sh && vault-homelab-env >/dev/null && terraform plan` (after the one-time Vault seed + `--refresh`). Seed gotcha: the 1P field label is `token`, not `credential`; `op read` of a wrong label returns empty and a hash-compare of two empty strings still "matches" (`e3b0c44298fc1c14`) — `test -n "$GH"` before `vault kv patch`. Rotation: add a GitHub section to `credential-rotation.md` (90-day expiry — due ~2027-01-01).
- Optional later: required `CODEOWNERS`, signed commits, `terraform plan` on PRs once a read-only state role exists.
