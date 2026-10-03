<!-- docs/procedures/aiops-incident-drafts.md -->

# Procedure — AIOps incident drafts (Phase 10h3)

*Plan: [`operations/10h-predictive-change.md`](../operations/10h-predictive-change.md) "10h3". Code: [`aiops/toolbelt/incident_draft.py`](../../aiops/toolbelt/incident_draft.py). Status 2026-10-03: **the mechanical draft is built and served by the Toolbelt (approver role); the LLM-written narrative and the pull-request delivery are NOT built** (they need the 10h2 machinery and a GitHub identity).*

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

Until the `/aiops draft-incident <id>` bot command lands, call it from a host the approver token is allowed from (Ratatoskr holds the token):

```bash
python3 - <<'PY'   # run on Ratatoskr as root; prints the markdown, nothing secret
import json, urllib.request
tok = open("/etc/ratatoskr/approver-token").read().strip()
r = urllib.request.Request("http://10.0.11.30:8090/incident/<ID>/draft", headers={"Authorization": "Bearer " + tok})
print(json.load(urllib.request.urlopen(r))["markdown"])
PY
```

Copy it to `docs/incidents/<slug>.md`, write the real narrative, add the row to `docs/incidents/README.md`, and make the follow-up edits.

## Known limits (honest)

- The Toolbelt only knows what Zabbix sent it (High and Disaster) plus what the agent was asked. An incident worked interactively in an operator session leaves only git history and logs: for those the draft is a timeline skeleton at best.
- Retention: the SQLite database keeps incidents until it is reset; how far back VictoriaLogs reaches is still an open question for older drafts.
- Not built yet: the grounded narrative section (LLM, every claim citing an evidence id, ungrounded sentences dropped), the `/aiops draft-incident` command, automatic drafting on a trigger, and opening the PR (10h2, needs a GitHub App).
