<!-- docs/procedures/aiops-drift.md -->
# AIOps drift flow (Phase 10h2): a drift-check that would change something reaches Gná

*Plan: [`plans/active/10h-predictive-change.md`](../plans/active/10h-predictive-change.md), author pipeline: [`aiops-author.md`](aiops-author.md), diagnosis: [`aiops-diagnosis.md`](aiops-diagnosis.md), actions: [`aiops-actions.md`](aiops-actions.md).*

## The flow

1. **Semaphore runs `asgard-drift-check`** (every 6h, `--check --diff` over `site.yml`). When the run ends, the `hermod_summary` Ansible callback ([`ansible/callback_plugins/hermod_summary.py`](../../ansible/callback_plugins/hermod_summary.py)) tallies per-host `changed=`. If any host WOULD change (production wrapper only; failures stay Hermod's alert; non-prod drift is expected), it POSTs `{template, task_id, changed: {host: n}}` to `AIOPS_DRIFT_URL` (a non-secret URL in Semaphore's project environment, `terraform/semaphore`). A clean run posts nothing.
2. **Gná's webhook `aiops/drift`** has no credential. Caddy admits that one path from the K3s node range `10.0.21.0/24` only (and that range reaches no other path); the lint refuses a credential-free webhook unless that Caddy rule exists.
3. **The Toolbelt's `POST /ingest/drift`** does not believe the message. It re-reads the run from Semaphore with the read-only token, waits for it to finish (up to 3 min), and takes the per-host counts from the run's own PLAY RECAP. A clean run answers `none`; the same task twice, or the same drift (same host) inside 24 h, answers `duplicate`. Otherwise it raises an `aiops.alert/v1` through the existing routing row (`semaphore-drift-detected`, runbook `RB-LXC-BOOT-DRIFT`): one drifted host = that host's own incident, several = the fleet.
4. **The diagnosis chain** runs as for any alert: the agent reads the run with `semaphore.tasks {"task_id": N}`, names the tasks and hosts, looks for a merged-but-unapplied change in `git.log`, and posts a thread in `#diagnoses`.
5. **A drift-note change request** is filed (deterministically, not by the model) and appears as a card in the AIOps chat channel. Approve it and the author drafts `docs/operations/drift/<date>-<host>-<role>.md` as a PR ([`aiops-author.md`](aiops-author.md), "The drift-note class").
6. **The fix, if there is one inside scope:** the agent may propose `replay-role` only for a T1 host and one of `baseline`, `hardening`, `vlagent`, `zabbix-agent`; you approve with a button, the executor runs it through Semaphore and verifies with `replay-role-check`. For anything else (the Toolbelt and author roles on Frigg, hypervisors, K3s nodes) the diagnosis says "apply the role manually" and the note records it.

## Proven end to end (2026-10-04)

One harmless line was appended to kvasir's `/etc/issue.net` (the hardening role's login banner) and `asgard-drift-check` was started in Semaphore. The whole chain ran on that real drift:

1. The callback's POST reached Gná from `10.0.21.23` (a K3s worker: the pod's traffic is SNATed into the allowed range).
2. The Toolbelt re-read task 1922 from Semaphore and raised incident 40 for kvasir (`drift_ingested`).
3. The agent diagnosed it (`layer: drift`, runbook `RB-LXC-BOOT-DRIFT`, cause labelled a hypothesis) and proposed the read-only `replay-role-check`; the drift-note request (CR 6) was filed deterministically. Approving it produced PR #143, a note that says exactly what it could and could not show.
4. `replay-role-check` (dry run) then `replay-role hardening` on kvasir were approved in Discord and executed through Semaphore: **prior check passed, converge succeeded, verify (`changed=0`) held**. kvasir's `/etc/issue.net` was byte-identical to the original afterwards (md5 compared).

The proposals of that run were created from chat (`@Gná propose ...`) because the first one expired in its 30-minute window; the window is now 4 hours. Note that a proposal made from chat is not linked to the incident (the incident is only in its reason text).

Bugs this proof found, all fixed: the session launcher missing a marker (markers directory instead of a glob, see [`aiops-author.md`](aiops-author.md)), the executor reading `recap_missing` when Semaphore had not yet stored the last log rows ([`known-issues/semaphore.md`](../known-issues/semaphore.md)), and the 30-minute approval window.

If the pod's source address ever leaves `10.0.21.0/24` the callback only logs a warning (`hermod_summary: Gna POST failed`) in the task output and Gná sees nothing; add the real CIDR to `caddy_sites` in `group_vars/n8n_agent.yml` and to `N8N_NETWORK_AUTH_WEBHOOKS` in `aiops/tools/lint.py`.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Drift in Semaphore's recap, nothing in Discord | the task output for `hermod_summary: Gna POST ...`; Caddy log on Gná (`/var/log/caddy/gna-ingest.log`: 403 = source not allowed) |
| Webhook answers but no incident | Toolbelt journal: `drift_clean` (the run's own recap showed nothing), `drift_duplicate`, or `rejected` with the reason (`unverifiable`: the run did not finish inside the wait) |
| Incident but no note card | the `File drift note` node's last execution in n8n (`ssh -L 5678:127.0.0.1:5678 ansible@gna`); `/aiops drafts` |
| A drift after a deploy you did by hand | expected: re-run the role (both tags `aiops-toolbelt` and `aiops-author` ship Frigg code and the agent definition) |
