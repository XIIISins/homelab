<!-- docs/procedures/aiops-author.md -->

# Procedure — the PR author (Phase 10h2)

*Plan and decisions: [`10h-predictive-change.md`](../operations/10h-predictive-change.md) ("10h2"). Code: `aiops/author/` (`scope.py`, `dispatcher.py`, `session.py`, `toolcli.py`), `aiops/author-classes.yml`, `aiops/toolbelt/change_requests.py`, `aiops/bot/drafts.py`, `.claude/agents/aiops-author.md`; roles `aiops-author`, `aiops-toolbelt`, `ratatoskr`; `terraform/vault/author.tf`. Tests: `aiops/tests/test_author_scope.py`, `test_author_dispatcher.py`, `test_change_requests.py`, `test_bot_drafts.py`.*

An operator-approved **change request** becomes **one pull request** that the operator reviews and merges. The author never applies, deploys or merges anything.

## The flow

1. `/aiops draft <kind> <title> <details>` in Discord files a change request (the Toolbelt keeps it `pending`; Gná can also file one, never as the operator, never approve it). A card appears in AIOps-chat.
2. The operator presses **Approve draft**. (Reject / Cancel exist too; a request left alone expires after 24 h.)
3. The **dispatcher** on Frigg claims it. The Toolbelt only hands one out when the kill switch and maintenance are off, fewer than 2 are running, fewer than 3 PRs are open, and the daily budget (6) is not spent.
4. The dispatcher starts `aiops-draft@<id>` (via a marker file and a tiny root launcher): an unprivileged user with a model key and the read-only tools token. It clones the repo, runs headless Claude Code with a fixed tool allow-list, and writes **a patch and a summary, nothing else**.
5. The dispatcher parses the patch strictly (no binary, symlinks, mode changes or deletes), runs the scope rules (`aiops/author-classes.yml`, the same code CI runs), scans the added lines for secrets, applies it to **its own clean clone**, commits as `aiops-author`, pushes `agent/<class>/<id>-<slug>`, opens the PR and reports to the Toolbelt.
6. CI runs on the PR; the `agent-scope` job re-checks the paths from the **base** commit's rules. The card gets the PR link; the dispatcher reports merged/closed when GitHub does.

## Who holds what

| | GitHub token | Model key | Toolbelt | Runs an LLM |
|---|---|---|---|---|
| Dispatcher (`aiops-author`) | yes, only handed to `git push` and the API | passes it into a job's env file for the session's lifetime | author role: claim, report, list | no |
| Session (`aiops-draft@`) | **no** | yes | author-tools role: `/tool/*` only | yes |
| Root launcher / loader | no | no | no | no |

The session can read live state through the Toolbelt's read-only tools and nothing else inside the homelab: its unit denies every private range except the Toolbelt. A prompt-injected session can at worst produce a bad patch; the strict parse, the scope rules, the secret scan, CI and the operator's review all see that patch.

## Classes

`aiops/author-classes.yml`: `docs` is enabled; `capacity` and `drift` exist but are `enabled: false`. A class's `allow` globs, the global `deny` list (the guardrail files: `.github/`, `.claude/`, `CLAUDE.md`, the registry, `aiops/toolbelt|bot|author|n8n|runner`, Vault/Semaphore Terraform, the Ansible role that deploys the author, `scripts/`, decisions/build-sequence/open-questions) and the size limits (12 files, 600 lines, no deletes) live in that file, which an agent PR cannot edit. Enabling a class is a normal reviewed PR.

## Deploy (operator or Claude, from the main checkout)

1. **Vault.** `terraform plan` then `apply` in `terraform/vault` (adds two random bearer tokens in KV and the `aiops-author` policy + AppRole). Expect only additions.
2. **Seed.** `secret/ansible/aiops/author-pat` (field `token`) must exist (see "The GitHub identity"), and `secret/ansible/aiops/anthropic-api-key` (field `key`) already does.
3. **Toolbelt.** `ansible-playbook playbooks/asgard-control.yml --limit frigg --tags aiops-toolbelt` (new flags, loader entries, preflight). It restarts the Toolbelt; the sweeper resumes.
4. **Author.** Mint the secret-zero once and pass it without printing it:
   `vault write -f -field=secret_id auth/approle/role/aiops-author/secret-id` and
   `vault read -field=role_id auth/approle/role/aiops-author/role-id`, then
   `ansible-playbook playbooks/asgard-control.yml --limit frigg --tags aiops-author -e aiops_author_role_id=... -e aiops_author_secret_id=...` (from shell variables, never typed into a transcript). Later runs need no `-e`.
5. **Bot.** Run the `ratatoskr` role (ships `bot/drafts.py` and the new commands).
6. **Reboot-test Frigg** (persistence rule): after the reboot `systemctl is-active aiops-author aiops-draft-launch.path` and the creds loader re-runs on its own.

Acceptance (done once): a probe PR from `agent/docs/<n>-x` touching `CLAUDE.md` fails `agent-scope`; one touching a docs file passes (run 2026-10-04, PRs closed unmerged); a green agent PR stays open until the operator merges it; a draft over its limits or budget is stopped and says so; every draft traces to its request and approval id (`change_request_events`, the dispatcher audit lines, the PR body).

## The GitHub identity (read this before enabling pushes)

The `main` ruleset lets the **Admin** role bypass it. A token for an account with admin on this repo could therefore push to `main` (which Flux deploys) despite the CI gate. The dispatcher **refuses to claim anything** if the token's account has `admin` or `maintain` on the repo (`identity_refused` in its audit log), unless `aiops_author_allow_admin_token: true` is set on purpose. The intended identity is a dedicated GitHub user with *write* (not admin) on the repo and a fine-grained PAT scoped to this repo only (Contents + Pull requests read/write; no Workflows, Administration or Issues), so its branch pushes cannot skip the ruleset. It cannot be created through `gh` or the API: GitHub has no endpoint for minting PATs.

## Operating it

- **Stop everything:** `/aiops kill` (nothing is approved or claimed; running sessions finish and report), or `systemctl stop aiops-author` on Frigg.
- **Look:** `journalctl -u aiops-author -u aiops-draft@<id> -u aiops-draft-launch`; the Toolbelt audit lines `change_request_*`; `/aiops drafts`; `GET /status` carries a `change_requests` block.
- **A request stuck `running`:** the Toolbelt fails it after 40 minutes. A job directory under `/var/lib/aiops-author/jobs/<id>/` keeps `spec.json` and `out/` (the repo and the env file are deleted when the run ends).
- **Dry run:** `-e aiops_author_dry_run=true` runs the whole pipeline up to the push and reports the request failed with "dry run" (the patch is checked; nothing is published).
- **Rotate:** `terraform taint random_password.author_token` (or `author_tools_token`) + apply, then re-run the `aiops-toolbelt` and `aiops-author` roles. The PAT: replace `author-pat` in Vault, restart `aiops-author`; put its expiry in the calendar.
- **Failure modes worth knowing:** a session that exceeds its wall clock (30 min) or budget (`--max-budget-usd`) is stopped and reported; a patch that fails any check is reported `failed` with the reason on the card; a branch pushed but a PR refused leaves the branch (delete it by hand).

## Not built yet

Gná filing requests from chat (the Toolbelt route exists: `POST /change-requests` with the agent token), the forecast card's "Draft fix PR" button (10h1 is still in shadow mode and posts nothing), the 10h3 incident-draft trigger on top of this machinery, the `capacity` and `drift` classes, and burst/canary test runs requested by the author (the doc's per-class substrates). Every one of these is an additive PR.
