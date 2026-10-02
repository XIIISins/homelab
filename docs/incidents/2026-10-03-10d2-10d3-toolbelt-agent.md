<!-- docs/incidents/2026-10-03-10d2-10d3-toolbelt-agent.md -->

# 2026-10-02/03 — Phase 10d2/10d3: Zabbix → n8n, the Toolbelt API and the diagnosis agent: findings

*Not an outage: a retrospective on building and bringing live the diagnosis path (Zabbix media type → Toolbelt API on Frigg → n8n agent → `#diagnoses`). Plan: [`10d-diagnosis-chatops.md`](../operations/10d-diagnosis-chatops.md); procedure: [`procedures/aiops-diagnosis.md`](../procedures/aiops-diagnosis.md); open items: [`open-questions.md`](../operations/open-questions.md).*

## What happened

Over one long session the second alert path went from a plan to a working agent: a Zabbix `n8n` media type beside Hermod (independence-tested both ways), the Toolbelt API on Frigg (ingest, dedupe, correlation, breakers, audit log, 19 read-only tools, replay mode, grounded `diagnosis.v1` validation), seven read-only identities (Zabbix, Proxmox, NetBox, Semaphore, Kubernetes, plus the VictoriaLogs/Metrics read routes), the n8n agent workflow with a watchdog, and a replay-based acceptance harness. Four scenarios pass (`canary-agent-down`, `skuld-freeze`, `injection-control`, `already-recovered`), two live passes ran on canaries, Frigg was rebooted and the whole chain recovered with no manual step, and Gná's egress is enforced at the UCG.

## Findings

1. **Every "read-only" claim was proved with a negative test, and four of them caught a real mistake.** `kubectl auth can-i get nodes/proxy` tested the node *named* proxy (needs `--subresource`); the NetBox token can list its *own* token (built in; asserted as `count == 1`, not 403); Semaphore's guest role can list users (id/name/username only) and answers 401, not 403, to a user-create; the Proxmox proof must target a non-existent vmid. Write the denial test with the credential, from the start.
2. **The grounding gate is the control that makes the agent trustworthy, and it fired in anger.** The injection-control run produced a correct verdict that cited *failed* lookups as evidence; validation rejected it and the fallback was posted. Fix: the prompt says only calls that returned data are evidence, and a rejected answer gets one retry with the reasons. Evidence must match a call the Toolbelt *served* for that incident; `NO_RECORDING` and errors can never ground a claim.
3. **n8n 2.x specifics only the live run could show.** Agent 3.x cannot use the HTTP Request Tool (pin 2.3); the Anthropic node's default `thinking: disabled` is a 400 on current models; HTTP Request v4 and the tool node use different header parameter shapes (a wrong one fails silently); a failure inside the agent's sub-nodes ends the whole execution, which is why the Toolbelt expires stuck runs and a watchdog posts the fallback. Execution errors are only readable from `execution_data` in n8n's SQLite.
4. **Replay incidents must be isolated from real alerts in both directions.** The second acceptance attempt was silently swallowed as a `duplicate` of the first. Replay gets a per-run fingerprint and its own correlation group.
5. **The `IPAddressDeny=` cgroup filter also confines `ExecStartPre=+`.** The token loader timed out reaching Vault until it became its own unit. Any helper that needs the network must live outside a unit that denies it.
6. **UCG zone policies:** order matters per zone pair and *editing a policy moves it to the bottom*; a change takes ~1 minute to apply; a destination is one type (domain *or* IP). I first blamed CDN resolver differences (measured: identical answers) and then DNS priming (a cold re-test 15 minutes later disproved it); the real cause was order. Measure before hypothesising; the CEF log line named the blocking policy immediately.
7. **A NetBox `brief` parameter of any value, even `0`, is brief mode** and drops `device`. The placement sync's own sanity floor (`--min-guests`) caught it and kept the previous map; the test fake now mimics NetBox.
8. **Idempotence slips:** a "run once now" step on a oneshot unit reported `changed` on every run until guarded by a `stat`; `-e flag=false` is a string; `import_tasks` tags are inherited by `never`-tagged tasks.
9. **I rebased after a conflict and kept going without checking, which pushed an un-rebased branch.** No harm (merged cleanly with a normal merge commit, no history rewrite), but the lesson stands: after a rebase/merge command, read its result before the next command.
10. **Worked as designed:** the 10c normalizer reuse (same fingerprint on both paths), the checksum-gated n8n import, the optional-credential loader (a missing credential warns, never stops the API), the sanity guard on the placement map, the independence of the two alert paths, and the Frigg reboot (all services back, tmpfs credentials re-created, fleet key reloaded, an agent run passed straight after).

## Not yet done

`terraform apply` in `terraform/adguard` (the `logs-read.` / `metrics-read.` names), 1Password mirrors of the seven new secrets, the live verification of the VL/VM read routes after that apply, and cleanup of the old asgard-K3s n8n. Later phases (10e actions with approval, 10f onward) are untouched.

## Changes

`aiops/` (toolbelt, tools, schemas, n8n generator and prompt, replays, tests), `ansible/roles/{aiops-toolbelt,n8n-agent,zabbix-server}`, `ansible/playbooks/asgard-control.yml`, `k8s/asgard/{infrastructure/aiops-readonly,infrastructure/monitoring-namespace,apps/victoria*}`, `terraform/{vault,proxmox/aiops-access,adguard}`, UCG policies (manual, documented in `architecture/network.md`), and the docs listed in the procedure.
