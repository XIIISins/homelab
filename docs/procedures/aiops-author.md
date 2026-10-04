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

The session can read live state through the Toolbelt's read-only tools and nothing else inside the homelab: its unit denies every private range except the Toolbelt. Its tool calls carry its change request id (`AIOPS_CR_ID`, sent by `toolcli`), not an incident id: the Toolbelt serves them only while that request is `running`, caps them at 60 per request, and audits each as `tool_call` with `change_request`. (Until 2026-10-04 every live call from a session was refused with `incident_id (integer) is required`; the first drift-note run found it.) A prompt-injected session can at worst produce a bad patch; the strict parse, the scope rules, the secret scan, CI and the operator's review all see that patch.

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

## The `drift-note` class

`/aiops draft` kind **drift-note**: name a drift-check run (the Semaphore task id, e.g. the latest `asgard-drift-check`) in the
details. The session reads the run with `semaphore.tasks {"task_id": N}` (the recap and each changed task with its diff head,
redacted) and `git.log`, and writes ONE note, `docs/operations/drift/YYYY-MM-DD-<host>-<role>.md`: what the run reported, the
likely cause (a **hypothesis** unless a commit or deploy explains it), the resolution, follow-ups. It changes no code. The first
case: the 2026-10-04 06:24Z run showed `frigg changed=2` because `aiops-toolbelt` code on Frigg lagged a merged PR (#115) until
the role was re-run. The code-changing `drift` class stays disabled; Gná filing these on a drift-check result is not built.

## Infrastructure PRs and the canary test (the `drift` class)

`/aiops draft` kind **drift** asks for a change to ONE of the four roles the canary pool runs (`baseline`, `hardening`, `vlagent`,
`zabbix-agent`); nothing else is allowed (`aiops/author-classes.yml`, and `ansible/playbooks/aiops-*.yml` is denied to every class so a
PR can never edit the guard it is tested under). When its PR opens, the **Toolbelt itself** reads the PR from GitHub (read-only) and
either proposes `pr-canary-test` or records why it cannot: only role content for one allow-listed role, one to three commits ahead of
`main`, no deletes or renames, a scannable diff, and none of the constructs a canary test should not run (`delegate_to`,
`local_action`, lookups, `uri`/`get_url`, `include_vars`, ...). The proposal's card appears next to the request's card; approve it and
the executor runs, through Semaphore with the PR branch as a per-task `git_branch`:

1. a dry run of the PR's code on one canary (what would change),
2. the real run on that canary,
3. a second dry run, which must show `changed=0` (the change is idempotent).

The dispatcher writes the outcome into the PR description under **Canary test** (counts, the canary, the commit, the proposal id; the
full run output stays in the private Discord thread because a PR comment on a public repo is public) and keeps it current until the PR
closes. The test is bound to the commit the Toolbelt read: if the branch head moves after you approve, or the PR is no longer testable,
the run is refused (`guard refused at execution time`) and nothing runs. The test is three Semaphore tasks that each check the branch out afresh, so the PR is re-read before every step and a head that moves mid-test stops the next step (`the PR changed under the test`). A small window remains between that read and Semaphore's own clone (seconds); closing it needs a sha checkout Semaphore does not offer. A PR that cannot be tested says `Not tested: <reason>` instead
(burst-cluster tests for `k8s/` and Terraform changes are not built, so those classes stay disabled). The two actions are `internal`:
the diagnosis and chat agents are never told they exist and cannot propose them.

What this does NOT protect against: the canary run executes the PR's tasks with the fleet SSH key and the runner's environment, so the
static scan is a filter, not a sandbox. The operator reading the diff before approving the test, and a canary being disposable
(alerts capped at `info`, rebuildable in 117 s), are the real controls.

## What the PR says it was tested with

Before it pushes, the dispatcher applies the session's patch to its own clean clone and runs the class's `checks` (declared in
`aiops/author-classes.yml`; for `docs`: `.github/scripts/ci-doc-links.py`) with a bare environment. The results are written
into the PR body under **Checks** (`doc links: pass`, `scope rules: pass`, `secret scan: pass`) and reported to the Toolbelt;
a failing check stops the push and fails the request with the check's output tail. These are run by the dispatcher, not claimed
by the model. A check can only be a script already on `main` under `.github/scripts/` (the scope rules forbid a patch from
touching `.github/`). Not built: a burst/canary test run per request for the classes that change infrastructure (`capacity`,
`drift`); they stay disabled until that exists.

## Operating it

- **Stop everything:** `/aiops kill` (nothing is approved or claimed; running sessions finish and report), or `systemctl stop aiops-author` on Frigg.
- **Look:** `journalctl -u aiops-author -u aiops-draft@<id> -u aiops-draft-launch`; the Toolbelt audit lines `change_request_*`; `/aiops drafts`; `GET /status` carries a `change_requests` block.
- **A request stuck `running`:** the Toolbelt fails it after 40 minutes. A job directory under `/var/lib/aiops-author/jobs/<id>/` keeps `spec.json` and `out/` (the repo and the env file are deleted when the run ends).
- **Dry run:** (pass booleans as JSON, `-e '{"aiops_author_dry_run": false}'`: `-e x=false` is the truthy string "false" in the unit template) `-e aiops_author_dry_run=true` runs the whole pipeline up to the push and reports the request failed with "dry run" (the patch is checked; nothing is published).
- **Rotate:** `terraform taint random_password.author_token` (or `author_tools_token`) + apply, then re-run the `aiops-toolbelt` and `aiops-author` roles. The PAT: replace `author-pat` in Vault, restart `aiops-author`; put its expiry in the calendar.
- **Failure modes worth knowing:** a session that exceeds its wall clock (30 min) or budget (`--max-budget-usd`) is stopped and reported; a patch that fails any check is reported `failed` with the reason on the card; a branch pushed but a PR refused leaves the branch (delete it by hand).

## Not built yet

Gná filing requests from chat (the Toolbelt route exists: `POST /change-requests` with the agent token), the forecast card's "Draft fix PR" button (10h1 is still in shadow mode and posts nothing), an automatic trigger for 10h3 incident drafts (`/aiops draft-incident` is manual), Gná filing drift notes is built ([`aiops-drift.md`](aiops-drift.md)) but unproven live, the `capacity` class (and burst-cluster tests for `k8s/`), and burst/canary test runs requested by the author (the doc's per-class substrates). Every one of these is an additive PR.
