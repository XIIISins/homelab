<!-- docs/operations/drift/2026-10-05-fleet-hardening.md -->

DRAFT: agent-written, operator to edit

# Drift note: fleet-wide `hardening : Configure login banner` in `asgard-drift-check` task 2048 (2026-10-05)

## What the run reported

Source: Semaphore task 2048 (`semaphore.tasks {"task_id": 2048}`), status `success`, started 2026-10-05T12:15:01Z, ended
2026-10-05T12:24:35Z, repo commit `0db44e8c`. As in the earlier notes, the wrapper runs `site.yml` in `--check` mode (per
[the 2026-10-04 kvasir note](2026-10-04-kvasir-hardening.md)), so "changed" means "would change".

- Task `TASK [hardening : Configure login banner]` reported `changed` on 23 hosts: `einherjar-skuld`, `einherjar-urd`, `einherjar-verd`,
  `gondul`, `hlokk`, `sigrun`, `kvasir`, `mimir`, `saga`, `gjallarbru`, `bifrost`, `heimdall`, `fulla`, `idunn`, `vor`, `eir`, `snotra`,
  `hlin`, `factorio`, `hermod`, `gna`, `hugin`, `frigg` (the changed lines in the call; this matches the request's list).
- `frigg` additionally reported two `aiops-toolbelt` tasks as changed: `Install the unit` and
  `Flush handlers so changed code/unit is live before the checks`. Its recap is `ok=143 changed=3`.
- The returned recap shows `changed=1` for every other listed host (and `frigg` `changed=3`); `localhost` is `changed=0`. The recap in the
  tool answer has 20 entries and does not include `saga`, `sigrun`, `snotra` or `vor`, although each has a changed line; I did not
  establish why. No host shows `failed` or `unreachable` above 0.
- Diffs: only 4 hosts returned a diff (`gjallarbru`, `bifrost`, `eir`, `snotra`). On those, in `/etc/issue.net`, the line
  `* All activity is logged and monitored.` would become `* All activity is logged, monitored and audited.`. The other hosts' `diff` lists were
  **empty**, so their difference is not visible in this evidence.

## Likely cause (hypothesis)

Banner: the `hardening` role now holds the text `All activity is logged, monitored and audited.`
([`tasks/main.yml`](../../../ansible/roles/hardening/tasks/main.yml)). `git.log` for `ansible/roles/hardening` shows `947d24b` (2026-10-04,
"drift: Banner wording (#149)", 1 line changed in `tasks/main.yml`, change request #7) as the latest change to that role. The diffs on four hosts
show the old wording live and the new wording wanted. **Hypothesis**: #149 changed the repo and the hosts have not had a real apply of
`hardening` since, so every host that has the old file reports drift. This fits the four visible diffs; I did not read the file on the other
hosts, so for them (including `kvasir`) it is unconfirmed that the cause is the same.

The run's own commit, `0db44e8` (PR #184), changes `CLAUDE.md`, `aiops/actions.yml`, an aiops test and two docs files; it does not touch
the role (`git.show`). It does not explain the banner drift.

`frigg` toolbelt tasks: `git.log` for `ansible/roles/aiops-toolbelt` lists recent commits including `60ee447` (2026-10-05, #182, adds the soak
injector, and per its message adds `soak.py` to the role's shipped modules and a role default) and `d0d32af` (2026-10-05). **Hypothesis**: one of
these changed the unit or shipped code on the repo side before it was applied to `frigg`. I did not read the unit diff (the task diffs were empty).

## Resolution

Not resolved, as far as I can tell. I saw no later run and no apply. A real apply of `hardening` (fleet) and of `aiops-toolbelt` on `frigg` would
be expected to bring the next drift-check to `changed=0` for these tasks. This note changes no code.

## Follow-ups

- Operator: decide whether to apply the `hardening` role fleet-wide now (it is the intended repo state after #149); I did not check which playbook or tag does this.
- Operator: read `/etc/issue.net` on one host with an empty diff (for example `kvasir`) to confirm it is the old wording and not an owner/mode difference.
- Operator: apply or review the `aiops-toolbelt` role on `frigg` (a toolbelt deploy restarts the services; see the role's handlers). The changed unit was not inspected here.
- Operator: the request says incident #45 was diagnosed in the diagnoses channel. I did not read it (no tool call); compare it with this note.
- Operator: the recap omitting four hosts that have changed lines is worth a look at how the tool summarizes the run.
