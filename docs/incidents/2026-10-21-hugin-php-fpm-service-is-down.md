> **DRAFT: generated from the Toolbelt's records, operator to edit.** The timeline, evidence and actions are mechanical (each line cites its source); the agent's diagnosis is quoted model output; the root cause is a **hypothesis** until you confirm it.

# Incident #110: hugin (2026-10-21)

- **State:** resolved   **Opened:** 2026-10-21 12:06:05Z   **Resolved:** 16:18:05Z   **Duration:** 4h 12m
- **Alerts:** 1 (critical); **tool calls served:** 4; **proposals:** 0

## Timeline (UTC, mechanical)

- 12:06:05Z  Incident #110 opened (group `node:urd`) [incident 110]
- 12:06:05Z  Alert firing on `hugin`: [Zabbix] High: PHP-FPM: Service is down (severity critical, runbook RB-ZBX-TRIAGE) [alert 5629e819]
- 12:07:35Z  Diagnosis agent started [incident 110]
- 12:07:48Z  Diagnosis accepted by the Toolbelt (model claude-sonnet-5-5) [diagnosis]
- 12:07:48Z  Diagnosis posted to the thread [incident 110]
- 16:18:05Z  Alert on `hugin` resolved [alert 5629e819]
- 16:18:05Z  Incident #110 resolved [incident 110]

## Evidence the agent gathered (tool calls actually served)

| time | tool | arguments | outcome |
|---|---|---|---|
| 12:07:38Z | `reach.tcp` | `{"host": "hugin", "port": 22}` | served [tool call 595] |
| 12:07:38Z | `registry.runbook` | `{"id": "RB-ZBX-TRIAGE"}` | served [tool call 596] |
| 12:07:40Z | `reach.tcp` | `{"host": "hugin", "port": 10050}` | served [tool call 597] |
| 12:07:42Z | `reach.tcp` | `{"host": "hugin", "port": 80}` | served [tool call 598] |

## Agent diagnosis (model output, validated against the calls above)

- **Layer:** workload   **Confidence:** medium   **Runbook:** RB-ZBX-TRIAGE
- **Summary:** The hugin guest is alive, but nothing accepts connections on port 80. PHP-FPM has 11 running processes. The PHP-FPM ping check most likely goes through the web server, so the web server (probably nginx) is the likely fault. The hypervisor looks fine, but I did not check it directly.
- **Reasoning:** SSH (22) and the Zabbix agent (10050) are open, so the guest is up and this is not a host or network fault. Port 80 refuses connections, which points to the web frontend. The alert items show php-fpm.ping at Up (1) with 11 processes, which does not match a dead PHP-FPM. The Zabbix API calls (zabbix.problems and zabbix.host) returned 502 errors, so I have no evidence from them. I did not check the hypervisor, the web server unit name or port 443, so the web server is a hypothesis and not a confi…
  - evidence `reach.tcp`: Port 22 is open, so the guest is alive.
  - evidence `reach.tcp`: The Zabbix agent port is open.
  - evidence `reach.tcp`: Connection refused on port 80, so the web server is not listening.
- **Root cause (HYPOTHESIS, not confirmed):** The hugin guest is alive, but nothing accepts connections on port 80. PHP-FPM has 11 running processes. The PHP-FPM ping check most likely goes through the web server, so the web server (probably nginx) is the likely fault. The hypervisor looks fine, but I did not check it directly.
- next check: On hugin, run systemctl status nginx (or apache2) and check that the web server listens on port 80 and that the php-fpm8.4 socket and status page respond.
- next check: Check the web server and PHP-FPM logs for the time the alert fired.
- next check: Check whether the trigger has already cleared, since the alert items show ping Up (1).
- next check: Check pve.node_status for urd to confirm the hypervisor is healthy.

## Actions

No action was proposed.

## Follow-ups for the operator (the post-flight checklist; this draft edits none of them)

- [ ] Confirm or correct the root-cause hypothesis above and write the real narrative.
- [ ] Known-issue: add or update the entry in `docs/known-issues/<subject>.md` (rule, why, symptom/diagnostic, recovery); gotcha text never goes in CLAUDE.md.
- [ ] `docs/incidents/README.md`: add this incident's row; `open-questions.md` / `decisions.md` / `build-sequence.md` if anything changed.
- [ ] Check whether a runbook or the alert routing should change.

## Follow-ups (agent-drafted; the files below were not edited)

- `decisions.md`: no decision row is supported by this draft's evidence.
- `open-questions.md`: add an entry for the unconfirmed root cause (web server on port 80 refused connections while PHP-FPM ping showed Up); the Zabbix API calls returned 502 during diagnosis and were not investigated.
- `build-sequence.md`: nothing indicated by the draft.
- `CLAUDE.md`: nothing; gotcha text belongs in `docs/known-issues/`, and no known-issue entry is added because the root cause is only a hypothesis.
