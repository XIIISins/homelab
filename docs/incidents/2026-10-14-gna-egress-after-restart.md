<!-- docs/incidents/2026-10-14-gna-egress-after-restart.md -->

# Gná lost its path to the Anthropic API after the homelab restart (found 2026-10-14)

Found by the operator, who noticed the canary smoke-test faults no longer got their Discord cards. Not user-visible, but the AIOps loop was blind: every alert since the restart was posted as "Analysis unavailable", so no diagnosis, no proposal, no autonomous heal, and the 10f soak recorded misses it did not earn.

## What happened

- The homelab restarted on 2026-10-12 (Ratatoskr and Frigg came back at about 18:36 UTC). The last card, proposal #80 (an auto-approved `restart-unit` on canary-1), was posted at 11:38 UTC, and soak injection 22 healed at 11:39 UTC.
- After that, soak injections 25 and 26 ended `missed` (injection 27 was still open when this was found): the canary alert opened an incident and Gná posted a thread within about 10 seconds, with `Analysis unavailable (the agent failed or its answer did not pass validation)`. The Toolbelt journal showed no `diagnosis_accepted` and no `diagnosis_rejected`, which meant the agent never submitted anything.
- n8n execution 3432 (incident #82) ended in the workflow's `Render fallback` branch because the `Anthropic model` node failed with `NodeApiError: Request timed out.` after about 100 seconds.
- From Gná at 03:40 UTC on 2026-10-14: `api.anthropic.com` and `deb.debian.org` (both allowed by the UCG) timed out, `discord.com` answered, and `github.com` (blocked, the Deploy Window is paused) answered 200. DNS from AdGuard and from the UCG returned identical addresses, and IPv6 has no route so it fails instantly (not the cause). At 03:48 UTC, with no UCG edit (the operator confirmed no zone change), all five matched the policy.
- Nothing alerted on any of it. The Gná host, Caddy, n8n and the Toolbelt were all healthy; the only symptom was a fallback post per alert.

## Cause

The UCG egress rules for Gná did not match Gná's traffic for at least hours after the restart, then did again without a change. The exact trigger is not confirmed. The likely reading is that the gateway's domain-based rules had not settled after its own restart (both Anthropic and Debian are domain rules; the Deploy Window being open at the same time points at the rule set rather than DNS), but nothing on the gateway was inspected. If it recurs, enable `Syslog Logging` on the deny and read `UNIFIfirewallPolicy=` ([`n8n-aiops.md`](../known-issues/n8n-aiops.md)).

## Fix

A posture probe on Gná (every 5 minutes) and a Zabbix template that alerts when it stops matching the design ([`net-posture`](../../ansible/roles/net-posture/README.md), [`gna-network-posture.yml`](../../ansible/playbooks/files/zabbix/gna-network-posture.yml)). Egress: allow-listed destinations must answer, the default-deny destinations (a domain and a raw IP) must not, the Deploy Window destination must not outside a deploy. Ingress: no listener off loopback outside the allowed ports, Caddy `:8081` reachable from Hugin, n8n `:5678` not.

Deployed 2026-10-14 (template import, then the Gná play with `--tags net-posture,zabbix-agent`): 11 items supported, 7 triggers loaded, none firing, role idempotent in check mode.

## What it cost, and what to remember

- Soak injections 25 to 27 (at least) are misses caused by the outage, not by the agent. The 10f read-out must exclude them ([`open-questions.md`](../operations/open-questions.md)).
- The detection gap is the finding, as with the Fulla replica ([`2026-10-06-fulla-stale-replica.md`](2026-10-06-fulla-stale-replica.md)): a dependency that is only used on an alert has no traffic to notice it by. Probe the path itself.
- Writing the template taught two Zabbix lessons ([`zabbix.md`](../known-issues/zabbix.md)): `configuration.importcompare` does not validate trigger expressions, and 7.0 refuses `length()` on an item reference.
