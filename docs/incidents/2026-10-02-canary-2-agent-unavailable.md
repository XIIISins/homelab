> **DRAFT: generated from the Toolbelt's records, operator to edit.** The timeline, evidence and actions are mechanical (each line cites its source); the agent's diagnosis is quoted model output; the root cause is a **hypothesis** until you confirm it.

# Incident #9: canary-2 (2026-10-02)

- **State:** posted   **Opened:** 2026-10-02 20:51:44Z   **Resolved:** not yet   **Duration:** open
- **Alerts:** 1 (info); **tool calls served:** 4; **proposals:** 0

## Timeline (UTC, mechanical)

- 20:51:44Z  Incident #9 opened (group `unplaced`) [incident 9]
- 20:51:44Z  Alert firing on `canary-2`: [Zabbix] High: Linux: Zabbix agent is not available (for 1m) [10d3 live pass, temporary] (severity info, runbook RB-HOST-HARD-FREEZE) [alert 4dee40e1]
- 20:53:14Z  Diagnosis agent started [incident 9]
- 20:53:25Z  Diagnosis accepted by the Toolbelt (model claude-sonnet-5-5) [diagnosis]
- 20:53:25Z  Diagnosis posted to the thread [incident 9]

## Evidence the agent gathered (tool calls actually served)

| time | tool | arguments | outcome |
|---|---|---|---|
| 20:53:17Z | `reach.tcp` | `{"host": "canary-2", "port": 22}` | served [tool call 18] |
| 20:53:17Z | `zabbix.host` | `{"host": "canary-2"}` | served [tool call 19] |
| 20:53:19Z | `reach.tcp` | `{"host": "10.0.11.191", "port": 22}` | served [tool call 20] |
| 20:53:19Z | `pve.guests` | `{}` | served [tool call 21] |

## Agent diagnosis (model output, validated against the calls above)

- **Layer:** host   **Confidence:** medium   **Runbook:** -
- **Summary:** The canary-2 LXC (on urd) is running and reachable on SSH, but nothing is listening on the Zabbix agent port 10050. The agent service is probably stopped or crashed inside the guest. This is not a hard freeze, a hypervisor fault or a network fault.
- **Reasoning:** The runbook name RB-HOST-HARD-FREEZE does not fit. A frozen host would not accept SSH, and Zabbix got 'connection refused' rather than a timeout. Its sibling canary guests on urd, and urd itself, are up. The alert text says 'live pass, temporary', so this may be a deliberate test canary. I could not check the agent service state inside the guest. The netbox lookup returned an error, and the lookup by hostname failed with a DNS error from the tool.
  - evidence `zabbix.host`: Agent interface 10.0.11.191:10050 reports 'Connection refused'; the host is not in maintenance.
  - evidence `reach.tcp`: TCP port 22 is open, so the guest is up and reachable on the network.
  - evidence `pve.guests`: canary-2 (vmid 1191) is running on urd with uptime of about 93,000 s. canary-1 and canary-3 are also running on urd.
- **Root cause (HYPOTHESIS, not confirmed):** The canary-2 LXC (on urd) is running and reachable on SSH, but nothing is listening on the Zabbix agent port 10050. The agent service is probably stopped or crashed inside the guest. This is not a hard freeze, a hypervisor fault or a network fault.
- next check: Check the zabbix-agent service status and logs inside canary-2.
- next check: Confirm whether this canary agent outage is a planned test, as the 'temporary' label suggests.

## Actions

No action was proposed.

## Follow-ups for the operator (the post-flight checklist; this draft edits none of them)

- [ ] Confirm or correct the root-cause hypothesis above and write the real narrative.
- [ ] Known-issue: add or update the entry in `docs/known-issues/<subject>.md` (rule, why, symptom/diagnostic, recovery); gotcha text never goes in CLAUDE.md.
- [ ] `docs/incidents/README.md`: add this incident's row; `open-questions.md` / `decisions.md` / `build-sequence.md` if anything changed.
- [ ] Check whether a runbook or the alert routing should change.

## Follow-ups (added by the drafting agent; files it may not edit)

- `decisions.md`: nothing unless the operator decides the runbook mapping (RB-HOST-HARD-FREEZE on an agent-unavailable trigger) should change.
- `open-questions.md`: whether the canary agent outage was a planned test, and why the agent is not listening (unconfirmed).
- `build-sequence.md`: no change identified from the evidence.
- `CLAUDE.md`: no change; gotcha text belongs in `docs/known-issues/`, and the evidence does not yet support an entry.
