<!-- docs/operations/drift/2026-10-04-kvasir-hardening.md -->

DRAFT: agent-written, operator to edit

# Drift note: `kvasir` changed=1 in `asgard-drift-check` task 1922 (2026-10-04)

## What the run reported

Source: Semaphore task 1922 (`semaphore.tasks {"task_id": 1922}`), status `success`, started 2026-10-04T09:35:36Z, ended
2026-10-04T09:44:52Z, repo commit `6719102c`. The wrapper [`drift-check.yml`](../../../ansible/playbooks/drift-check.yml)
asserts `--check` mode and imports `site.yml` (per the previous drift note, task 1918), so "changed" means "would change", not "was changed".

- `kvasir`: `ok=50 changed=1 failed=0 unreachable=0`.
- Every other entry in the returned recap: `changed=0 failed=0 unreachable=0`. That includes `mimir` (`ok=50 changed=0`), the AdGuard
  sibling with the same ok/skipped counts as `kvasir`. The recap lists 20 entries (`bifrost` to `mimir`, `localhost` included);
  I did not verify that it is the full host list.
- The one changed task: `TASK [hardening : Configure login banner]`, line `changed: [kvasir]`. The returned `diff` list was **empty**, so the
  content difference is not visible in this evidence.

The task is in the `hardening` role ([`tasks/main.yml`](../../../ansible/roles/hardening/tasks/main.yml), "Configure login banner"). It is an
`ansible.builtin.copy` with fixed inline `content` (a four-line "part of the niflheim homelab cluster" box) to `/etc/issue.net`, mode `0644`.
The sshd template ([`sshd_config.j2`](../../../ansible/roles/hardening/templates/sshd_config.j2)) points `Banner` at that file. No other
file under `ansible/` writes `/etc/issue.net` (`grep`).

## Likely cause (hypothesis)

The commit named by the run, `6719102` (PR #142), is documentation only: `CLAUDE.md` and files under `docs/` (`git.show`). `git.log` for
`ansible/roles/hardening` returns one commit, `ceb7a0c` (2026-09-17, "fix(semaphore): patch its own K3s worker last, avoid killing itself mid-run"),
which is not about the banner. So **no commit I found explains the drift**: the task's content is static and the role has not changed
in a way that touches it.

Since the desired content is fixed in the repo, the likely explanation is that `/etc/issue.net` on `kvasir` differs from it, or has different
ownership or mode (the `copy` task also checks those). **Hypothesis**, not observed: a hand edit or a package/system action on Kvasir.
`mimir` shows no such difference, so it is not a repo-wide change. I made no call that reads the file on Kvasir, so I cannot tell
content, owner or mode apart, nor say when it changed.

Not established: whether this is a one-off or recurring. `semaphore.tasks` was called for task 1922 only.

## Resolution

Not resolved, as far as I can tell. A real (non-check) apply of the `hardening` role to `kvasir` would rewrite `/etc/issue.net` to the repo
content, and the next `asgard-drift-check` should then show `kvasir changed=0`. I did not see a later run or any apply, and this note
changes no code.

## Follow-ups

- Operator: read `/etc/issue.net` on `kvasir` (content, owner, mode) and compare with the task's `content`; that separates a hand edit from an owner/mode difference.
- Operator: check the next `asgard-drift-check` result for `kvasir`. If it still reports the banner task after a real apply, the cause is not the file content.
- Operator: the request mentions incident #40 and its diagnosis in the diagnoses channel. I did not read either (no tool call for it); compare this note with that diagnosis.
- Fix, if the operator wants it, is to apply the `hardening` role to `kvasir` (I did not check which playbook or tag does that); I changed no role, playbook or code.
