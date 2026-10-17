> **DRAFT: generated from the Toolbelt's records, operator to edit.** The timeline, evidence and actions are mechanical (each line cites its source); the agent's diagnosis is quoted model output; the root cause is a **hypothesis** until you confirm it.

# Incident #93: mimir (2026-10-17)

- **State:** resolved   **Opened:** 2026-10-17 08:58:04Z   **Resolved:** 09:00:04Z   **Duration:** 2m 0s
- **Alerts:** 1 (critical); **tool calls served:** 4; **proposals:** 1

## Timeline (UTC, mechanical)

- 08:58:04Z  Incident #93 opened (group `node:verd`) [incident 93]
- 08:58:04Z  Alert firing on `mimir`: [Zabbix] High: Unit down: AdGuardHome.service not active (severity critical, runbook RB-ZBX-TRIAGE) [alert 764c8203]
- 08:59:34Z  Diagnosis agent started [incident 93]
- 08:59:42Z  Diagnosis accepted by the Toolbelt (model claude-sonnet-5-5) [diagnosis]
- 08:59:42Z  Proposal #110 `restart-unit` on `mimir` created (diagnosis) [proposal #110]
- 08:59:42Z  Proposal #110 approved by the Toolbelt under policy restart-failed-unit [proposal #110]
- 08:59:43Z  Diagnosis posted to the thread [incident 93]
- 08:59:43Z  Thread bound [proposal #110]
- 09:00:04Z  Alert on `mimir` resolved [alert 764c8203]
- 09:00:04Z  Incident #93 resolved [incident 93]
- 09:00:11Z  Proposal #110 ended **succeeded** [proposal #110]

## Evidence the agent gathered (tool calls actually served)

| time | tool | arguments | outcome |
|---|---|---|---|
| 08:59:36Z | `reach.tcp` | `{"host": "mimir", "port": 22}` | served [tool call 496] |
| 08:59:36Z | `registry.actions` | `{}` | served [tool call 497] |
| 08:59:36Z | `pve.node_status` | `{"node": "verd"}` | served [tool call 498] |
| 08:59:38Z | `reach.tcp` | `{"host": "mimir", "port": 53}` | served [tool call 499] |

## Agent diagnosis (model output, validated against the calls above)

- **Layer:** host   **Confidence:** high   **Runbook:** RB-UNIT-STOPPED-T1
- **Summary:** AdGuardHome.service is stopped on mimir. The guest is alive and its hypervisor verd is healthy. This is a service fault on the guest, not a hypervisor or network fault.
- **Reasoning:** SSH on mimir answers and verd is online with normal load. DNS port 53 refuses connections, which matches the Zabbix ActiveState=inactive. So the guest is up and only the service is down. mimir and AdGuardHome.service are on the restart-unit allow-list, which is the stopped-unit class RB-UNIT-STOPPED-T1. I did not check why the unit stopped.
  - evidence `reach.tcp`: SSH port open, so the guest is alive
  - evidence `pve.node_status`: verd is online, cpu 6%, uptime about 4.6 days, so the hypervisor is healthy
  - evidence `reach.tcp`: Port 53 connection refused, so AdGuardHome is not serving
- **Root cause (HYPOTHESIS, not confirmed):** AdGuardHome.service is stopped on mimir. The guest is alive and its hypervisor verd is healthy. This is a service fault on the guest, not a hypervisor or network fault.
- next check: Check the AdGuardHome journal on mimir for why it stopped (config error, crash or manual stop)
- next check: Confirm DNS resolution works after the restart, and that kvasir (the other AdGuard host) is serving in the meantime

## Actions

- Proposal #110 `restart-unit` on `mimir`: **succeeded**, decided by auto (restart-failed-unit).  [proposal #110]

## Follow-ups for the operator (the post-flight checklist; this draft edits none of them)

- [ ] Confirm or correct the root-cause hypothesis above and write the real narrative.
- [ ] Known-issue: add or update the entry in `docs/known-issues/<subject>.md` (rule, why, symptom/diagnostic, recovery); gotcha text never goes in CLAUDE.md.
- [ ] `docs/incidents/README.md`: add this incident's row; `open-questions.md` / `decisions.md` / `build-sequence.md` if anything changed.
- [ ] Check whether a runbook or the alert routing should change.

## Follow-ups (agent-added, not part of the mechanical draft)

- `docs/operations/decisions.md`: nothing evidenced; add a row only if the operator decides to change the `restart-failed-unit` policy (it auto-approved proposal #110 here).
- `docs/operations/open-questions.md`: why AdGuardHome.service stopped on mimir is open (the agent states it did not check; no journal was read).
- `docs/operations/build-sequence.md`: no change evidenced.
- `CLAUDE.md`: no change; gotcha text belongs in `docs/known-issues/dns-adguard.md` once a cause is confirmed.
- No known-issues entry drafted: the evidence supports only a hypothesis, not a rule.
