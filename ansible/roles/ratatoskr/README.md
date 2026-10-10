# ratatoskr

The AIOps Discord bot (Phase 10e), LXC 1122 on Urd, `10.0.11.222`. Plan: [`docs/plans/done/10e-approval-actions.md`](../../../docs/plans/done/10e-approval-actions.md). Procedure: [`docs/procedures/aiops-actions.md`](../../../docs/procedures/aiops-actions.md). Code: [`aiops/bot/`](../../../aiops/bot/).

## What it does

- Forwards `@Gná …` messages (in `AIOps-chat` and inside `#diagnoses` threads) to the n8n agent and posts the answer.
- Renders each pending proposal from the Toolbelt's feed as a card with Approve / Reject buttons, and edits it as the action runs and verifies.
- Is the **only** party that can turn a click into a decision: only the operator's Discord user id (in Vault) can press the buttons or use `/aiops kill | resume | maintenance | status | pending`, and the Toolbelt checks the id again.

## What it deliberately is not

It does not think (n8n does), it does not run anything (the Toolbelt's executor does, through Semaphore) and nothing listens on it. It holds three secrets and no others: the Discord bot token, the approver token and the chat-webhook token.

## Layout

| Path | What |
|---|---|
| `/opt/ratatoskr/aiops/bot/{bot,logic}.py` | the code, copied from the repo (root-owned: the service cannot modify itself) |
| `/etc/ratatoskr/config.json` | urls only |
| `/etc/ratatoskr/{discord-bot.json,approver-token,chat-token}` | secrets, `0400` owned by the service user, written from Vault at play time |
| `/var/lib/ratatoskr/state.json` | feed cursor + announced-once set (survives restarts) |

## Run

```
ansible-playbook playbooks/asgard-ratatoskr.yml          # full play as `ansible`
ansible-playbook playbooks/asgard-ratatoskr.yml --tags ratatoskr   # just the bot
```

The bot itself needs only apt (Debian `python3-discord`, 2.5 in trixie), but the `vlagent` role in the same play downloads a release from github.com: add `10.0.11.222` to the **AIOps - Deploy Window** policy's source and unpause it for the first run, then pause it again.
The role fails early, with the fix in the message, if the Vault secrets are missing or malformed, and verifies that the
bot reached the Discord gateway (a `ready` line in its journal). If that check fails on a fresh host, the UCG egress policy
for `10.0.11.222` is the usual cause (needs `discord.com` and `gateway.discord.gg`): see `docs/architecture/network.md`.

## Secrets (Vault)

| Path | Fields | Who writes it |
|---|---|---|
| `secret/ansible/ratatoskr/discord-bot` | `token`, `application_id`, `guild_id`, `operator_user_id`, `diagnoses_channel_id`, `chat_channel_id` | the operator, `scripts/secrets/seed-discord-bot` |
| `secret/ansible/aiops/approver-token` | `value` | Terraform (`terraform/vault`) |
| `secret/ansible/aiops/n8n-ingest-token/chat` | `value` | Terraform (`terraform/vault`) |
