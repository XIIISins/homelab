<!-- docs/operations/drift/2026-10-04-frigg-aiops-toolbelt.md -->

DRAFT: agent-written, operator to edit

# Drift note: `frigg` changed=2 in `asgard-drift-check` task 1918 (2026-10-04)

## What the run reported

Source: Semaphore task 1918 (`semaphore.tasks {"task_id": 1918}`), status `success`, started 2026-10-04T06:15:01Z, ended
2026-10-04T06:24:09Z, repo commit `fd8dd4f` (PR #121). The wrapper [`drift-check.yml`](../../../ansible/playbooks/drift-check.yml)
asserts `--check` mode and imports `site.yml`, so the run changed nothing on any host; "changed" means "would change".

- `frigg`: `ok=121 changed=2 failed=0 unreachable=0`.
- Every other host in the returned recap: `changed=0 failed=0 unreachable=0`. The recap lists 20 entries (`bifrost` to `mimir`, `localhost` included);
  I did not verify that it is the full host list.
- The two changed tasks, both in the `aiops-toolbelt` role ([`tasks/main.yml`](../../../ansible/roles/aiops-toolbelt/tasks/main.yml)):
  1. `Install the API code and data from the repo`: `changed: [frigg] => (item=toolbelt/actions.py)`. This is a `copy` loop, so the file
     on Frigg differs from `aiops/toolbelt/actions.py` at the run's commit. The returned diff was empty, so the content difference is not visible here.
  2. `Flush handlers so changed code/unit is live before the checks`: `changed: [frigg]`. This follows from 1: the copy notifies
     `Restart aiops-toolbelt`.

`toolbelt/actions.py` is the only file in the copy loop that was reported. No unit file, no config and no other code file drifted.

## Likely cause (hypothesis)

The file on Frigg is older than the repo's. `git.log` for `aiops/toolbelt/actions.py` shows its latest change is `e3f0fa6`, PR #115
("retry Semaphore reads so a transient blip does not fail a rebuild at verify", 2026-10-04), touching `aiops/toolbelt/actions.py` and
`aiops/tests/test_semaphore_exec.py` only. `git.log` for `aiops/` lists `e3f0fa6` immediately before `fd8dd4f`, so the run's commit contains it.

That fits a merged change not yet applied to Frigg, but **no tool call I made shows what version Frigg held or when the role last ran**. Alternatives not excluded:
a hand edit on Frigg, or a content difference that has no relation to #115. The empty diff cannot tell them apart.

## Resolution

Not verified. If the hypothesis holds, applying the `aiops-toolbelt` role on Frigg (`asgard-control.yml`, tag `aiops-toolbelt`) installs the
current `actions.py` and restarts the Toolbelt, and the next `asgard-drift-check` shows `frigg changed=0`. I did not see a later
run, so I cannot say whether that already happened.

## Follow-ups

- Operator: check the next `asgard-drift-check` result for `frigg`; if `changed` is still above 0, compare `actions.py` on Frigg with the repo copy.
- Operator: decide whether Toolbelt code merges should reach Frigg automatically, or whether a one-run drift after such a merge is acceptable.
  (Open question, not a finding.)
- This note changes no code and no registry entry.
