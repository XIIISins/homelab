<!-- docs/outline/procedures/landing-a-change.md -->

# Landing a change

How any change gets into the homelab. Merging to `main` is the Kubernetes deploy, so `main` is protected and nothing is pushed to it directly.

---

## Steps

1. **Pre-flight.** Before proposing new work, search the docs for the thing, check the open-questions list for prerequisites it depends on or would trigger, and read the known-issues file for each system it touches. Treat pending tasks as prerequisites, not backlog.
2. **Branch.** A short, descriptive branch: `feat/…`, `fix/…`, `doc/…` or `chore/…`.
3. **Change and test.** Run what the change needs: `terraform plan` from the main checkout, an Ansible `--check`, or `kubectl kustomize` for manifests. Changes that must survive a reboot are tested with a reboot.
4. **Commit.** Conventional-commit subjects referencing the phase. Docs and code in separate commits where practical.
5. **Pull request.** The `CI gate` check must pass. It runs only the jobs the diff needs: secret scanning (always), YAML lint, manifest rendering and schema validation, Terraform fmt and validate, ansible-lint, doc-link checks and the AIOps suite.
6. **Review.** Docs and routine bumps may auto-merge. `terraform/`, `ansible/` and `k8s/` changes get a diff review first.
7. **Apply.**
   - `k8s/`: Flux reconciles on merge. Nudge with `flux reconcile`, never `kubectl apply`.
   - `terraform/`: apply from the main checkout only.
   - `ansible/`: one playbook at a time, by hand or through Semaphore.
8. **Post-flight docs.** Tick the phase in the build sequence, add decision rows for architectural choices, write an incident note for non-trivial surprises, add new gotchas to the matching known-issues file, and cross-link them.

## Rules that save you

- **Never** `kubectl apply` or `edit` against production, and never replace Calico's addon files by hand.
- The repository is **public**: no secret values, ever. Every push is scanned, and CI scans the full history.
- Dependency bumps go through the `chart-bump` agent, which verifies the target image exists before pinning it.

---

## See also

- **GitOps & automation** (Components): why the layers are split this way.
- **Procedures** (this section): the index.
