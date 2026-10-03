<!-- docs/procedures/aiops-actions.md -->

# Procedure — AIOps actions: approving, chatting, deploying and verifying (Phase 10e)

*Design: [`operations/10e-approval-actions.md`](../operations/10e-approval-actions.md). Roles: Gná (n8n, the brain), Ratatoskr (the Discord bot, the mouth and the only approver), the Toolbelt on Frigg (the authority). Related: [`aiops-diagnosis.md`](aiops-diagnosis.md) (the read-only half), [`aiops/README.md`](../../aiops/README.md).*

## Using it (day to day)

| Want to | Do |
|---|---|
| Ask the agent something | In `AIOps-chat`: `@Gná why is canary-2 unreachable?` (a thread is started; keep talking in it, mention it each time). Or mention it inside a `#diagnoses` thread to ask about that incident. Anyone in the server can ask; it only reads. |
| Approve or reject a proposed action | Press **Approve** or **Reject** on the card in the incident's thread. Only the operator's Discord account works (the bot and the Toolbelt both check the user id). The card is edited as it runs and verifies, and a result note follows. |
| Stop everything | `/aiops kill [reason]`: nothing may be approved or started, approved-but-unstarted proposals are cancelled, a running task finishes. `/aiops resume` releases it. |
| See what is going on | `/aiops status` (kill switch, budgets, open proposals), `/aiops pending`. |
| Mark a maintenance window | `/aiops maintenance on` / `off` (the flag 10f's autonomy will respect). |

A card expires after 30 minutes undecided and nothing runs. If a card says the post-condition **did not hold**, read its "If it goes wrong" line: a restart has no inverse, so escalate to the unit's runbook.

## Deploy (operator, from the main checkout; every step is CLI)

Order matters; each step gates the next.

1. **Discord + secrets** (done once): the bot application, the channels and `scripts/secrets/seed-discord-bot` (see the setup notes it came with). Check any time: `scripts/secrets/seed-discord-bot --check`.
2. **Terraform** (`-parallelism=2` for NetBox): `terraform/vault` (the `approver-token` and the `chat` ingest token), `terraform/semaphore` (the `aiops` project; the seven templates move into it), `terraform/proxmox/asgard-lxcs` (Ratatoskr, LXC 1122), `terraform/netbox`, `terraform/adguard`.
3. **The executor's identity:** `python3 aiops/tools/mint_semaphore_exec.py --prove-run`. It creates `aiops-exec` as Task Runner on `aiops` only, stores its token, **proves** it can run the aiops templates and cannot touch any other project, and runs `aiops-service-status` once end to end. A `BAD` row exits non-zero: fix it before going on.
4. **UCG** (the UI, once): Ratatoskr's egress, in the same pattern as Gná's ([`network.md`](../architecture/network.md) "Egress allow-list for Ratatoskr"). It needs `discord.com` and `gateway.discord.gg` and the Debian mirrors; the Deploy Window stays paused (the bot installs from apt only).
5. **Ratatoskr:** `ansible-playbook playbooks/asgard-ratatoskr.yml -e ansible_user=root --tags baseline`, then `ansible-playbook playbooks/asgard-ratatoskr.yml`. The role fails early (with the fix in the message) if a Vault secret is missing, and its last task waits for the bot's `ready` line: a failure there on a fresh host means the UCG rule.
6. **Frigg:** `ansible-playbook playbooks/asgard-control.yml --tags aiops-toolbelt` (ships `actions.py`, turns approvals on, loads the approver token and the operator id as required files). Verify: `curl -s -o /dev/null -w '%{http_code}\n' http://10.0.11.30:8090/stats` is `403` (anonymous refused); `journalctl -u aiops-toolbelt -n 5` shows a `start` event with `"actions": true, "executor": true, "approver": true`.
7. **Gná:** `ansible-playbook playbooks/asgard-gna.yml --tags n8n` imports the chat workflow and creates the `aiops-ingest-chat` credential. The role's route check proves `/webhook/aiops/chat` refuses an unauthenticated call. Wrong-source check: `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://10.0.11.221:8081/webhook/aiops/chat` is `404` from Hugin (allowed on the listener, but only for the Zabbix path) and `403` from any host outside the listener's allow-list; only Ratatoskr reaches the chat path.

## Acceptance on the canaries (the part CI cannot prove)

Run once after deployment, and again after any change to the registry, the executor or the bot. Use a canary only ([`canary-pool.md`](canary-pool.md)).

1. **Chat:** in `AIOps-chat`: `@Gná how is canary-1 doing?`. A thread starts and an answer arrives citing a tool. Ask something it cannot do (`@Gná restart the vault pods`): it declines and offers a proposal at most.
2. **Fault → proposal → approval → verified.** Make the temporary High trigger on `canary-2` and stop `zabbix-agent2` there, exactly as in [`aiops-diagnosis.md`](aiops-diagnosis.md) "Live pass on a canary" (the service is the fault, the guest is alive). Within ~5 minutes a diagnosis thread appears **with a `restart-unit` card** (`target_host = canary-2`, `unit = zabbix-agent2.service`; the agent may legitimately propose nothing if its evidence is weak, in which case re-run on a different canary or ask it in the thread: `@Gná propose a restart of zabbix-agent2 on canary-2`). Press **Approve**. Expect, in order: the card says *Approved. Starting.* → *Running.* → *Succeeded and verified.*, a result note follows, and `systemctl is-active zabbix-agent2` on the canary is `active`.
3. **Fails closed** (each must refuse; do them on a second card or a fresh fault):
   - Someone else presses Approve: an ephemeral "Only the operator can approve", nothing runs.
   - Wait 30 minutes on a pending card: it shows *Expired*, nothing ran.
   - `/aiops kill`, then press Approve on a pending card: refused ("the kill switch is engaged"); `/aiops resume`.
   - A *replay* diagnosis (`python3 aiops/tools/replay_run.py canary-agent-down`) may propose actions but no card appears and the proposal cannot be decided.
4. **Audit:** `journalctl -u aiops-toolbelt` shows `proposal_created` → `proposal_approved` (with the deciding user id) → `step` events → `proposal_succeeded`, joined by proposal id; the same lines reach VictoriaLogs through vlagent.
5. **Clean up:** start the agent again, let the trigger recover, delete the temporary trigger.

## Secrets and rotation

| Secret | Path | Rotate |
|---|---|---|
| Discord bot token | `secret/ansible/ratatoskr/discord-bot` | reset in the Developer Portal, `scripts/secrets/seed-discord-bot`, re-run the Ratatoskr role |
| Approver token | `secret/ansible/aiops/approver-token` | `terraform apply -replace=random_password.approver_token` in `terraform/vault`, re-run the Ratatoskr role **and** `asgard-control.yml --tags aiops-toolbelt` |
| Chat webhook token | `secret/ansible/aiops/n8n-ingest-token/chat` | `-replace='random_password.n8n_ingest_token["chat"]'`, re-run the Ratatoskr role **and** the Gná n8n role |
| Executor token | `secret/ansible/aiops/semaphore-exec-token` | delete the secret and the token in Semaphore, re-run `mint_semaphore_exec.py` |
| Operator user id | same Discord secret, field `operator_user_id` (comma list for more than one) | `seed-discord-bot`, then `asgard-control.yml --tags aiops-toolbelt` (the Toolbelt reads it at start) |

Refresh the shim cache everywhere after changing a Vault secret ([`known-issues/vault.md`](../known-issues/vault.md)). Mirror each to 1Password with `scripts/secrets/vault-1p-mirror` (the map has `ratatoskr-discord-bot`; run `unmapped` for the two Terraform-minted ones).

## Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| Bot never says `ready` | UCG egress for `10.0.11.222` (needs `gateway.discord.gg`); `journalctl -u ratatoskr -n 30` on the host |
| Cards do not appear | the Toolbelt's `--actions` is off or the approver token/IP is wrong: `journalctl -u ratatoskr` shows `feed_error` with the HTTP status (403 = token or source address) |
| A proposal is "NOT proposed" in the thread | the registry guard refused it (host not T1, unit not allow-listed, release denied): the thread line says why |
| Approve is refused with "not an operator" | the operator id in Vault does not match the account pressing; `seed-discord-bot --check` |
| Approved but "the executor is not configured" | `semaphore-exec` credential missing on Frigg: run `mint_semaphore_exec.py`, then restart `aiops-toolbelt` |
| A run fails at once | `journalctl -u aiops-toolbelt` `step` events name the task id; open it in Semaphore (project `aiops`) |
| Chat answers "I could not reach my brain" | n8n or the `chat` route is down: `journalctl -u n8n` on Gná; the alert and diagnosis paths are unaffected |

## Add or change an action

Edit `aiops/actions.yml` (typed vars, guard, verify, rollback), add the playbook and the Semaphore template in `terraform/semaphore/templates.tf` (project `aiops`), run `python3 aiops/tools/lint.py` and the tests, regenerate the workflows (`python3 aiops/n8n/build_ingest.py`), apply Terraform, then flip `semaphore.applied: true`. Raising `max_autonomy` to `auto` is a reviewed PR that belongs to 10f1.
