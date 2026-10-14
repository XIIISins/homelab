<!-- ansible/roles/net-posture/README.md -->

# net-posture

A periodic check (every 5 minutes) that a host's **network behaviour matches its firewall policy and design**, built for **Gná** (LXC 1121) whose egress is policed by the UCG allow-list ([`architecture/network.md`](../../../docs/architecture/network.md) "Egress allow-list for Gná"). Motivation: after the 2026-10-12 restart Gná silently lost its path to `api.anthropic.com` for hours and every alert became "Analysis unavailable" ([incident](../../../docs/incidents/2026-10-14-gna-egress-after-restart.md)); nothing alerted on it.

## What it does

1. Installs `curl` and `ss`, a `netposture` system user, `/usr/local/bin/net-posture`, and `net-posture.timer` (every `net_posture_interval`, default 5 minutes).
2. `net-posture probe` (in parallel, 5 s timeout each):
   - **allow** list: each destination must answer (any HTTP status counts; the test is TCP + TLS);
   - **deny** list: each must NOT answer (include a raw IP, so a missing DNS answer cannot pass for a block);
   - **window** list: blocked except while an operator has the UCG Deploy Window unpaused;
   - **listeners**: every `ss -tuln` socket off loopback whose port is not in `net_posture_listen_allow_ports`.
3. Writes `/var/lib/net-posture/status` (`key=value`, world-readable). `zabbix-agent2` reads it through `UserParameter=net.posture[*]` (`net-posture read allow.fail|allow.fail.list|deny.leak|window.leak|listen.unexpected|age`).

A timer + state file, not a probing UserParameter: the agent's item timeout (3 s) is shorter than a dropped connection's, and a blocked destination *is* a dropped connection.

The alerts are the Zabbix template [`gna-network-posture.yml`](../../playbooks/files/zabbix/gna-network-posture.yml), which also adds two outside-in TCP checks from Hugin (`:8081` must be open, `:5678` must be closed). Imported by `playbooks/zabbix-host-groups.yml`, linked in `group_vars/n8n_agent.yml`.

## Variables

| Variable | Meaning |
|---|---|
| `net_posture_egress_allow` / `_deny` / `_window` | Destinations (hostnames or IPs) that must answer / must not / must not outside the Deploy Window. All default to `[]` |
| `net_posture_listen_allow_ports` | Non-loopback ports that may listen (TCP or UDP) |
| `net_posture_curl_timeout` | Seconds before a probe gives up (default 5) |
| `net_posture_interval` | How often the probe runs (default `5min`). The Zabbix template's delays and trigger windows assume 5 minutes: change them together |

Keep the lists in step with the UCG policy table: a destination added to a UCG allow rule belongs in `net_posture_egress_allow` in the same PR.

## Tags

`net-posture`, `net-posture:packages`, `net-posture:user`, `net-posture:script`, `net-posture:zabbix`.

## Check by hand

```bash
sudo -u netposture /usr/local/bin/net-posture probe && cat /var/lib/net-posture/status
/usr/local/bin/net-posture read allow.fail.list
```
