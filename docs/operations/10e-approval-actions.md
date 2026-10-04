<!-- docs/operations/10e-approval-actions.md -->

# Phase 10e — Approval-gated actions and a conversational agent: plan and as-built

*Decided and built 2026-10-03. Status: 🟡 **code complete and tested; not yet deployed** (the operator steps at the end make it live). Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10e. Procedure: [`procedures/aiops-actions.md`](../procedures/aiops-actions.md). Predecessor: [`10d-diagnosis-chatops.md`](10d-diagnosis-chatops.md).*

---

## Goal and boundary

Stage 2 of the AIOps loop: the agent can now **propose** a change from the action registry, the **operator approves** it with one click in Discord, a **registry-checked executor** runs it through Semaphore, and the result is **verified** and posted back. The same bot lets people **talk to the agent** (ask why it concluded something, ask it to look again, ask for a change) so it behaves like a colleague with a voice, not a one-way notifier.

Nothing here lets the agent act on its own. T0/T1 autonomy is 10f. The Discord notification path (Hermod) is untouched and stays independent of everything on this page.

## The three roles (the rule everything else follows)

```
   a person in Discord                       the operator (one Discord user id)
        │  @Gná why...?                              │  presses Approve / Reject
        ▼                                            ▼
 ┌────────────────────┐   chat turn    ┌─────────────────────────┐   decision (approver token)
 │  Gna  (n8n)        │◄──────────────►│  Ratatoskr  (the bot)   │──────────────────────────────┐
 │  the BRAIN         │   via webhook  │  the MOUTH + the only   │                              ▼
 │  reads untrusted   │                │  APPROVER, outbound only│            ┌──────────────────────────────┐
 │  text through an   │                └─────────────────────────┘            │  Toolbelt (Frigg)            │
 │  LLM: no authority │  propose / read-only tools (agent token)              │  the AUTHORITY: validates,   │
 └─────────┬──────────┘───────────────────────────────────────────────────────►│  stores, binds approval to   │
           │                                                                   │  exact params, executes,     │
           └─ no Discord token, no approval, no executor credential           │  verifies, audits, kill sw.  │
                                                                               └──────────────┬───────────────┘
                                                                                              ▼
                                                              Semaphore project `aiops` (Task Runner, 7 templates only)
```

**Why the bot is not on Gná and n8n is not the bot.** Gná reads alert text, logs and chat through an LLM, so prompt injection is its main risk. If the approver credential lived there, a compromised agent could approve its own proposals. So: the brain holds no authority; the bot is a dumb transport that is the *only* thing that turns a click into a decision, and it never lets the brain see or influence that path; the Toolbelt is the only thing that executes.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Where approval happens | A **button on a card** posted by the bot in the proposal's thread, pressed by an allow-listed Discord **user id** (plus `/aiops` slash commands for the kill switch) | A webhook cannot carry buttons or read reactions; a button press carries the clicker's immutable user id, so authority never depends on message text. Typing "yes do it" in chat approves nothing. |
| Who can talk to the agent | Anyone in the server, read-only, in `AIOps-chat` and in `#diagnoses` threads, **mention-only** | The server holds only the operator and trusted friends. `@mention` delivers message content without the privileged Message Content intent, so the bot asks for **no privileged intent**. |
| Who can approve | One operator user id (comma list supported), checked in the bot **and again** at the Toolbelt | Defence in depth: a fooled bot still cannot approve for a stranger. |
| Bot host | Own LXC (**Ratatoskr**, 1122, `10.0.11.222`, Urd), Debian `python3-discord`, outbound only | Separation from the LLM host; no inbound surface; apt only (no PyPI egress). |
| Two Toolbelt roles | *agent* (n8n): ingest, tools, chat, **create** proposals. *approver* (bot): feed, **decide**, flags, status. Separate tokens **and** source addresses | A leaked agent token cannot decide; a leaked approver token is useless from any other host. |
| Executor identity | Semaphore user `aiops-exec`, **Task Runner on a dedicated `aiops` project only**; the seven `aiops-*` templates moved into it | Semaphore roles are per *project*: a Task Runner can run every template in its project, including `asgard-apply`. A separate project makes the "allow-listed-template key" real. |
| What an approval authorises | The **exact params hash** shown on the card; the executor re-validates against the registry at run time | Closes the gap between "what was shown" and "what ran". |
| Failure behaviour | Everything fails closed: timeout, restart mid-run (→ failed), stale approval (→ cancelled), kill switch (→ cancelled), replay proposals (→ never decidable) | An action that is not clearly approved, running and verified is not an action. |

## What was built (slices; PRs on `main`)

| Slice | PR | What |
|---|---|---|
| 10e1 engine | #72 | `aiops/toolbelt/actions.py`: registry-validated proposals, decisions, executor (prior step, verify, rollback note), kill switch, expiry, restart recovery; Semaphore client; output parser |
| 10e1 wiring | #73 | agent/approver roles in `server.py`, proposals from diagnoses (with `params`), chat endpoints (caps, scrubbing, turn-scoped tools), one re-entrant DB lock |
| 10e bot | #74 | `aiops/bot/`: cards, buttons that survive restarts, slash commands, mention chat, feed planner |
| 10e chat | #75 | n8n `chat.json` + prompts; the `chat` ingest source; per-path source rules on Gná's Caddy |
| 10e1 identity | #76 | Semaphore `aiops` project; `mint_semaphore_exec.py` with a reach proof |
| 10e host | #77 | Ratatoskr LXC/role/playbook; the Toolbelt's approver role on Frigg |

## Flows

**Proposal.** Diagnosis (or a chat turn) names a registry action with `params` → the Toolbelt validates it (declared vars only, patterns full-matched, tier/host/unit/release guards, template applied) and stores it **pending** (4-hour TTL since 2026-10-04, was 30 minutes; ≤3 pending per incident, 30 a day) → when the incident thread exists the bot posts a card (action, target, params, what it does, what it verifies, rollback note, the params hash) → the operator presses **Approve** or **Reject** → on approve the Toolbelt checks the user id and the hash and the kill switch, then runs: (`requires_prior` step, e.g. a `replay-role-check` dry run) → the action → the registry `verify` post-condition → result posted to the thread and the card edited. A failed or unverified run says so and shows the rollback note.

**Chat.** `@Gná …` in `AIOps-chat` (a thread per question) or in a diagnosis thread → bot → n8n `chat` workflow → Toolbelt `/chat/turn` (caps, history, incident + diagnosis + proposals as context) → agent (same read-only tools + `propose_action`) → `/chat/reply` (secret-shaped strings and `@everyone` neutralised) → bot posts it. Limits: 10 questions/hour per person, 30 per thread, 100 a day, 15 tool calls per answer.

**Kill switch.** `/aiops kill` (operator only) engages a Toolbelt flag: nothing may be approved or started, approved-but-not-started proposals are cancelled, a running task finishes. `/aiops resume` releases it. `/aiops maintenance on|off` is the flag 10f's autonomy will respect.

## Trust boundary: who holds what

| | Holds | Can | Cannot |
|---|---|---|---|
| **Gná (n8n)** | Toolbelt agent token, Anthropic key, chat ingest credential, Discord diagnosis webhook | read tools, chat, **create** proposals | decide, flag, read the feed, run anything, post a card |
| **Ratatoskr (bot)** | Discord bot token, approver token, chat-webhook token | render cards, relay a click, flags | think, run anything, read tools |
| **Toolbelt (Frigg)** | read-only backend creds, `semaphore-exec` token | validate, store, execute registry templates, audit | anything outside the registry |
| **`aiops-exec` (Semaphore)** | Task Runner on project `aiops` | run the seven `aiops-*` templates | see or run `asgard-*` templates, edit templates, create users |

## Acceptance and exit

Automated (CI): 342 tests, including the full propose → approve → execute → verify → announce lifecycle across a real socket, the role matrix, every refusal path, and the reach proof's leak detection. Live (operator, after deployment): [`procedures/aiops-actions.md`](../procedures/aiops-actions.md) "Acceptance on the canaries": a fault on a canary is diagnosed, a `restart-unit` proposal appears as a card, approving it fixes the canary and the card says *verified*; a stranger's press, an expired card, the kill switch and a replay each fail closed. Exit criterion (roadmap): ≥ N real incidents handled via propose → approve → verified with a complete audit trail; none has been handled yet.

## Risks

- **A Discord outage** removes the approval path, not the alert path: proposals expire, nothing runs.
- **The unit's egress filter** (`IPAddressDeny`) is best effort inside an unprivileged LXC; the UCG policy is the boundary.
- **Chat cost:** bounded by the caps above and the dedicated Anthropic key's spend limit.
- **Prompt injection through chat** can at worst make the agent *propose* something the registry guard allows; a human still has to press the button, and the card shows exactly what will run.
- **One operator** is a single point of failure for approvals; add a second user id to `operator_user_id` (comma list) if wanted.

## Operator steps to go live (CLI only; I can run the ones marked *)

1. `terraform apply` in `terraform/vault`, `terraform/semaphore`, `terraform/proxmox/asgard-lxcs`, `terraform/netbox` (`-parallelism=2`), `terraform/adguard`.
2. `python3 aiops/tools/mint_semaphore_exec.py --prove-run` *.
3. UCG egress for `10.0.11.222` (see [`network.md`](../architecture/network.md)).
4. `playbooks/asgard-ratatoskr.yml`, then `asgard-control.yml --tags aiops-toolbelt`, then `asgard-gna.yml --tags n8n`.
5. The live acceptance on the canaries.

## Next

10f (autonomous T1 healing): guards first (kill switch ✅ here, per-target rate limit, circuit breaker, maintenance flag, check-mode/diff-scope gate), then the first classes, then a ~14-day soak on the canaries.
