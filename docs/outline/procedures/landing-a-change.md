<!-- docs/outline/procedures/landing-a-change.md -->

# Landing a change

How every change to the homelab gets in. Merging to `main` **is** the Kubernetes deploy, so `main` is protected: no direct pushes, one required check (`CI gate`), and a diff review for `terraform/`, `ansible/` and `k8s/`.

**Use when:** you are changing anything in the repository, including docs.
**Prerequisites:** Session setup done; a clean working tree.

---

## 1. Pre-flight (before you write anything)

```bash
cd ~/homelab && git fetch origin && git switch main && git pull --ff-only

# What does the repo already say about this?
grep -ril "<thing>" docs | head -20
sed -n 1,200p docs/operations/open-questions.md | grep -i -B1 -A3 "<thing>"   # prerequisites, deferred work
ls docs/known-issues/                                                          # read the file for every system you touch
```

Treat unchecked items in the open-questions list as **prerequisites**, not backlog: if your change depends on one, or would trigger a latent one, do that first and say why.

## 2. Branch

```bash
git switch -c fix/<short-slug>          # feat/ fix/ doc/ chore/  (concise and descriptive; never claude/<random>)
```

## 3. Make the change, test it locally

Run what applies to the files you touched. These are the same checks CI runs.

```bash
# Kubernetes manifests (needs kubectl + kubeconform)
.github/scripts/ci-k8s.sh

# Terraform (one or more module dirs)
terraform fmt -check -recursive terraform
.github/scripts/ci-terraform.sh terraform/<module>
terraform -chdir=terraform/<module> plan          # read-only; apply happens later, from the main checkout

# Ansible
ansible-lint ansible/                              # profile in ansible/.ansible-lint
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/<play>.yml --limit <host> --check --diff

# YAML, links, secrets, AIOps schemas
pip install -r .github/ci-requirements.txt && yamllint .
python3 .github/scripts/ci-doc-links.py
gitleaks git --no-banner --redact .
pip install -r aiops/requirements.txt && python3 aiops/tools/lint.py && python3 -m unittest discover -s aiops/tests

# Which CI jobs will your diff trigger?
.github/scripts/ci-changes.sh origin/main HEAD
```

Anything that must survive a reboot (network config, sysctls, systemd units): apply it to one node, **reboot that node**, and verify before you propose it.

## 4. Commit

Conventional commits, referencing the phase where one exists. **No attribution trailers** (no `Co-Authored-By`, no session links). Docs and code in separate commits where practical.

```bash
git add -p
git commit -m "fix(vault): pin the raft join CA path in the rejoin runbook"
git log --oneline origin/main..HEAD
```

The repository is **public**. Before you push, make sure no secret values, tokens or private keys are in the diff: `git diff origin/main..HEAD | grep -iE "token|secret|password|BEGIN .*PRIVATE" `. Identifiers (hostnames, IPs, Vault paths, 1Password item UUIDs) are fine; values are never fine.

## 5. Push and open the pull request

```bash
git push -u origin fix/<short-slug>
gh pr create --fill                       # then complete the template sections
gh pr checks --watch                      # wait for CI gate
```

Required check: **`CI gate`**. If it fails, read the failing job (`gh run view --log-failed`); fix and push again. Do not skip, disable or quarantine a check to get green.

Auto-merge is fine for docs and routine bumps (`gh pr merge --auto --squash`). For `terraform/`, `ansible/` and `k8s/` changes, wait for a diff review.

## 6. After the merge: apply

| What changed | How it lands | Verify |
|---|---|---|
| `k8s/asgard/…` | Flux reconciles `main` automatically (≈1 min). Nudge: `flux reconcile source git flux-system && flux reconcile kustomization apps --with-source` | `flux get kustomizations; flux get hr -A \| grep -v True` |
| `terraform/<module>/…` | **From the main checkout:** `git switch main && git pull --ff-only && terraform -chdir=terraform/<module> plan` then `apply` | The apply output; then the thing it manages |
| `ansible/…` | `ansible-playbook … --limit <host> --check --diff`, then without `--check`; or let the Semaphore drift-check report it | Drift check returns `changed=0` |
| Docs only | Nothing to apply | |

Terraform notes: the `netbox` module needs `-parallelism=1` (the default overloads NetBox). The `aws` module needs the bootstrap identity: `set-aws-creds bootstrap`. Modules that write secrets to Vault or read `secret/k8s/*` need the root token (`set-vault-token root`, operator only). Never apply a plan that proposes to **destroy** a guest, volume or database you did not intend to; stop and ask.

## 7. Post-flight (documentation is part of done)

- `docs/operations/build-sequence.md`: tick the phase, add emergent sub-phases.
- `docs/operations/decisions.md`: a row for any architectural decision.
- `docs/incidents/`: a dated write-up for anything with several findings or surprises.
- `docs/known-issues/<subject>.md`: new gotchas (rule, why, symptom, recovery) in the matching file.
- `docs/operations/open-questions.md`: new items; closed items move to the archive file.
- Check `python3 .github/scripts/ci-doc-links.py` still passes.

## Dependency bumps

Helm charts, K3s, Calico, Flux and pinned images go through the `chart-bump` agent, which checks upstream, **verifies that the target image exists** before pinning, orders by blast radius and executes one item at a time. Versions are concrete pins; no floating ranges. K3s minors go through **K3s & Calico upgrade**.

---

## Rollback

- **Kubernetes:** `git revert <merge-commit>` as a new PR (or, for an urgent fix, the same with auto-merge), then `flux reconcile`. Flux does not roll back by itself.
- **Terraform:** revert the code, then `plan` and `apply` from the main checkout; for a destroyed resource, restore from backup first.
- **Ansible:** revert and re-run the play for the affected hosts.

---

## See also

- **Everyday operations**: reconcile, restart and run-a-playbook commands.
- **GitOps & automation** (Components): why the layers are split this way.
