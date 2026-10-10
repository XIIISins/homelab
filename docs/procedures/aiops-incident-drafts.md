<!-- docs/procedures/aiops-incident-drafts.md -->

# Procedure — AIOps incident drafts (Phase 10h3)

*Plan: [`plans/active/10h-predictive-change.md`](../plans/active/10h-predictive-change.md) "10h3". Code: [`aiops/toolbelt/incident_draft.py`](../../aiops/toolbelt/incident_draft.py). Status 2026-10-03: **the mechanical draft is built and served by the Toolbelt (approver role); the LLM-written narrative and the pull-request delivery are NOT built** (they need the 10h2 machinery and a GitHub identity).*

## What you get

`GET /incident/<id>/draft` on the Toolbelt (approver token only; the diagnosis agent cannot read it) returns `{incident_id, slug, filename, markdown}`. The markdown is a skeleton of `docs/incidents/<filename>` built **without any model**:

| Section | Source | Rule |
|---|---|---|
| Header, **Timeline (UTC)** | the incident, alerts, diagnosis, proposals and proposal events | every line is time-ordered and cites its source (`[alert ab12cd34]`, `[proposal #7]`, `[tool call 41]`) |
| **Evidence** | the tool calls the Toolbelt actually served (refused calls are listed too) | a table of tool, arguments, outcome |
| **Agent diagnosis** | the stored `diagnosis.v1` | quoted and labelled model output; the root cause is explicitly a **HYPOTHESIS** |
| **Actions** | proposals with who decided (an operator or `auto:<policy>`) and the result | |
| **Follow-ups** | static checklist | the post-flight items the operator updates (known-issue, incidents README, decisions/open-questions); the draft edits none of them |

Everything is passed through the Toolbelt's redactor (bearer tokens, `password=`/`token=` values, webhooks, private keys) before it leaves, because the repo is public. The document opens with a `DRAFT: operator to edit` banner.

## Using it

**Automatic (since 2026-10-04):** when an incident resolves and crossed the bar, the Toolbelt files the write-up request itself and its card appears in the
AIOps chat channel like any draft ("filed by toolbelt"); you press **Approve** (or Reject, and it is never refiled). The bar is the plan's: an action was executed or refused for
it (a proposal ended `succeeded`, `failed`, `verify_failed` or `rejected`), or three or more alerts were correlated into it, or it lasted 30 minutes or more. Replays and incidents
made only of the disposable canary pool's own faults are never written up. At most 3 are filed per UTC day (the author's own caps still apply and a deferred one is retried on the
next sweep, every two minutes), and only incidents resolved in the last 7 days are considered. `aiops_toolbelt_auto_incident_drafts: false` turns it off. Approval stays manual on
purpose: the drafting session costs tokens and ends in a public PR.

By hand: `/aiops draft-incident <incident number>` (the number is in the diagnosis thread). It files a `docs` change request whose body only
names the incident; its card appears in the AIOps chat channel and you press **Approve**. The drafting session then fetches the
mechanical draft with the read-only `incident.draft` tool (available to sessions only, while their request is running), writes it
verbatim to `docs/incidents/<filename>`, adds the row to `docs/incidents/README.md` and a follow-ups list, and the dispatcher opens
the PR for you to review and edit (see [`aiops-author.md`](aiops-author.md)). The session may not edit `decisions.md`,
`open-questions.md`, `build-sequence.md` or `CLAUDE.md`; it lists what they need.

To read the draft without a PR: the approver route `GET /incident/<id>/draft` (Ratatoskr holds the token).

## Known limits (honest)

- The Toolbelt only knows what Zabbix sent it (High and Disaster) plus what the agent was asked. An incident worked interactively in an operator session leaves only git history and logs: for those the draft is a timeline skeleton at best.
- Retention: the SQLite database keeps incidents until it is reset; how far back VictoriaLogs reaches is still an open question for older drafts.
- Not built yet: the grounded narrative section (LLM, every claim citing an evidence id, ungrounded sentences dropped), automatic drafting on a trigger (the command is manual: you pick the incident), and a grounded narrative beyond the mechanical draft.
