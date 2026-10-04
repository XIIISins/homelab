<!-- docs/operations/drift/2026-10-04-frigg-ratatoskr-aiops-code.md -->

DRAFT: agent-written, operator to edit

# Drift note: `frigg` and `ratatoskr` changed=2 each in `asgard-drift-check` task 1929 (2026-10-04)

## What the run reported

Source: Semaphore task 1929 (`semaphore.tasks {"task_id": 1929}`), status `success`, started 2026-10-04T12:15:01Z, ended
2026-10-04T12:24:18Z, repo commit `11e3beff` (`git.show` resolves it to `11e3bef`, PR #150). The wrapper
[`drift-check.yml`](../../../ansible/playbooks/drift-check.yml) guards that `--check` is in effect, so nothing was changed on any host;
"changed" means "would change".

- `frigg`: `ok=138 changed=2 failed=0 unreachable=0`.
- `ratatoskr`: the request says `changed=2`. Its recap line is **not** in the returned recap (see below); the two changed tasks listed for it are the evidence.
- Every other entry in the returned recap: `changed=0 failed=0 unreachable=0`. The recap has 20 entries (`bifrost` to `mimir`, `localhost` included) and
  it contains no `ratatoskr` entry at all. I did not verify why; the returned list may be truncated, or the host may be absent. Not established.
- Changed tasks (each from `semaphore.tasks`):
  1. `ratatoskr : Install the bot code from the repo`: `changed: [ratatoskr] => (item=bot/drafts.py)` and `changed: [ratatoskr] => (item=bot/bot.py)`.
     In [`ratatoskr/tasks/main.yml`](../../../ansible/roles/ratatoskr/tasks/main.yml) this is a `copy` loop over `ratatoskr_source_files`
     (`bot/logic.py`, `bot/drafts.py`, `bot/bot.py`); it notifies `Restart ratatoskr`.
  2. `ratatoskr : Flush handlers so changed code/unit is live before the checks`: `changed: [ratatoskr]` (follows from 1).
  3. `aiops-toolbelt : Install the API code and data from the repo`: `changed: [frigg] => (item=toolbelt/change_requests.py)` and
     `changed: [frigg] => (item=author/dispatcher.py)`. A `copy` loop over `aiops_toolbelt_source_files` in
     [`aiops-toolbelt/tasks/main.yml`](../../../ansible/roles/aiops-toolbelt/tasks/main.yml); it notifies `Restart aiops-toolbelt`.
  4. `aiops-toolbelt : Flush handlers so changed code/unit is live before the checks`: `changed: [frigg]` (follows from 3).
- Diffs returned: for `ratatoskr`, a hunk adding an `elif act.kind == "notice":` branch (sends "Request N is approved but **waiting** ..."); for
  `frigg`, one added line `self._blocked_seen: dict[str, float] = {}  # why -> last time claim_blocked was logged ...`. In the output each diff is attached to
  the item line *before* the file it belongs to (the `bot.py` hunk sits on the `bot/drafts.py` item, the `dispatcher.py` hunk on the `toolbelt/change_requests.py` item),
  and the `bot/drafts.py` and `toolbelt/change_requests.py` content differences are not shown. **Hypothesis** for the offset: the diff is printed before its item's `changed:` line
  and attached to the previous one. I did not confirm it.

## Likely cause (hypothesis)

The run's commit, `11e3bef` (PR #150, "a cap that holds back an approved request says so on its card, in chat and in the dispatcher journal"),
changes exactly these four files under `aiops/` (`git.show`): `aiops/author/dispatcher.py` (5 lines changed), `aiops/bot/bot.py` (8), `aiops/bot/drafts.py` (11),
`aiops/toolbelt/change_requests.py` (26), plus tests. The set of changed items matches that file list one for one, and the two visible diff hunks match
the commit's `bot.py` and `dispatcher.py` additions. That is the strongest explanation: **a merged change, not yet applied to Frigg and Ratatoskr** (the
roles copy the code from the repo checkout, so a merge shows up as "would change" until the roles run). A hand edit would not produce a set that mirrors the commit.

Not established: when the roles last ran, or whether anything else differs. No call I made reads the files on either host. The run's commit is not the
branch head: `3081309` (PR #151) is newer and is the current checkout head (`git.log`); it also touches `aiops/author/dispatcher.py`, `aiops/toolbelt/change_requests.py`
and Toolbelt role files, so a later drift run may report differently. I did not read that run, if one exists.

## Resolution

Not resolved, as far as I can tell. Applying the `ratatoskr` and `aiops-toolbelt` roles for real would install the repo code and restart both services; the next
`asgard-drift-check` should then show `changed=0` for both. I saw no later run or apply (`semaphore.tasks` was called for 1929 only), and this note changes no code.

## Follow-ups

- Operator: apply both roles (I did not check which playbook or tags do that; the previous drift note for Frigg names `asgard-control.yml`, tag `aiops-toolbelt`) and
  check the next `asgard-drift-check` for `frigg` and `ratatoskr`. Because #151 changed Toolbelt role files, note that applying the current head is a bigger change than #150 alone.
- Operator: this is the second Toolbelt-code drift on Frigg today (see [`2026-10-04-frigg-aiops-toolbelt.md`](2026-10-04-frigg-aiops-toolbelt.md)). Decide whether merges to `aiops/`
  should reach Frigg and Ratatoskr automatically. (Open question, not a finding.)
- Operator: check why `ratatoskr` is missing from the returned recap while its changed tasks are present.
- Operator: the request says incident #41 was diagnosed in the diagnoses channel. I read neither the incident nor the diagnosis (no tool call for it); compare this note with it.
- This note changes no role, playbook or code.
