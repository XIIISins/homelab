<!-- docs/procedures/aiops-drift.md -->
# AIOps drift flow (Phase 10h2): a drift-check that would change something reaches Gná

*Plan: [`operations/10h-predictive-change.md`](../operations/10h-predictive-change.md), author pipeline: [`aiops-author.md`](aiops-author.md), diagnosis: [`aiops-diagnosis.md`](aiops-diagnosis.md), actions: [`aiops-actions.md`](aiops-actions.md).*

## The flow

1. **Semaphore runs `asgard-drift-check`** (every 6h, `--check --diff` over `site.yml`). When the run ends, the `hermod_summary` Ansible callback ([`ansible/callback_plugins/hermod_summary.py`](../../ansible/callback_plugins/hermod_summary.py)) tallies per-host `changed=`. If any host WOULD change (production wrapper only; failures stay Hermod's alert; non-prod drift is expected), it POSTs `{template, task_id, changed: {host: n}}` to `AIOPS_DRIFT_URL` (a non-secret URL in Semaphore's project environment, `terraform/semaphore`). A clean run posts nothing.
2. **Gná's webhook `aiops/drift`** has no credential. Caddy admits that one path from the K3s node range `10.0.21.0/24` only (and that range reaches no other path); the lint refuses a credential-free webhook unless that Caddy rule exists.
3. **The Toolbelt's `POST /ingest/drift`** does not believe the message. It re-reads the run from Semaphore with the read-only token, waits for it to finish (up to 3 min), and takes the per-host counts from the run's own PLAY RECAP. A clean run answers `none`; the same task twice, or the same drift (same host) inside 24 h, answers `duplicate`. Otherwise it raises an `aiops.alert/v1` through the existing routing row (`semaphore-drift-detected`, runbook `RB-LXC-BOOT-DRIFT`): one drifted host = that host's own incident, several = the fleet.
4. **The diagnosis chain** runs as for any alert: the agent reads the run with `semaphore.tasks {"task_id": N}`, names the tasks and hosts, looks for a merged-but-unapplied change in `git.log`, and posts a thread in `#diagnoses`.
5. **A drift-note change request** is filed (deterministically, not by the model) and appears as a card in the AIOps chat channel. Approve it and the author drafts `docs/operations/drift/<date>-<host>-<role>.md` as a PR ([`aiops-author.md`](aiops-author.md), "The drift-note class").
6. **The fix, if there is one inside scope:** the agent may propose `replay-role` only for a T1 host and one of `baseline`, `hardening`, `vlagent`, `zabbix-agent`; you approve with a button, the executor runs it through Semaphore and verifies with `replay-role-check`. For anything else (the Toolbelt and author roles on Frigg, hypervisors, K3s nodes) the diagnosis says "apply the role manually" and the note records it.

## What was verified live (2026-10-04) and what was not

Verified: the callback and Semaphore environment variable (applied), the Caddy request matrix (from a K3s node the drift path reaches n8n and every other path is refused; from Frigg it is 403), the Toolbelt ingest against fakes and the live Zabbix path unaffected by the shared workflow change (replay `canary-agent-down` PASS), the drift-note author on a real finding (PR #137).

Not verified: a real drift-check run driving the whole chain, so two things are still unproven: that Semaphore's pod reaches Gná from inside the cluster with a source address in `10.0.21.0/24`, and the `replay-role` proposal on a T1 host. To exercise both, make one harmless change on a T1 replica and run the drift-check:

```bash
# on a T1 host (e.g. kvasir): add a line to the pre-login banner the hardening role manages
echo "drift-test: manual edit" >> /etc/issue.net
# then start asgard-drift-check in Semaphore (read-only); ~9 minutes. Fix = approve the replay-role proposal (hardening).
```

If the pod's source address is not in the Caddy range the callback only logs a warning (`hermod_summary: Gna POST failed`) in the task output and Gná sees nothing; add the real source CIDR to `caddy_sites` in `group_vars/n8n_agent.yml` and to `N8N_NETWORK_AUTH_WEBHOOKS` in `aiops/tools/lint.py`.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Drift in Semaphore's recap, nothing in Discord | the task output for `hermod_summary: Gna POST ...`; Caddy log on Gná (`/var/log/caddy/gna-ingest.log`: 403 = source not allowed) |
| Webhook answers but no incident | Toolbelt journal: `drift_clean` (the run's own recap showed nothing), `drift_duplicate`, or `rejected` with the reason (`unverifiable`: the run did not finish inside the wait) |
| Incident but no note card | the `File drift note` node's last execution in n8n (`ssh -L 5678:127.0.0.1:5678 ansible@gna`); `/aiops drafts` |
| A drift after a deploy you did by hand | expected: re-run the role (both tags `aiops-toolbelt` and `aiops-author` ship Frigg code and the agent definition) |
