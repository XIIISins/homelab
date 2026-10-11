<!-- docs/outline/troubleshooting/monitoring-and-automation.md -->

# Monitoring & automation

Symptoms with the things that watch and run the homelab: Zabbix (Hugin), Hermod, VictoriaMetrics/Logs and the log shippers, Semaphore and its jobs, the health prober, the control node (Frigg) and the AIOps bot. If this layer is broken you are partly blind: fix it early, and remember Zabbix and Hermod are **outside K3s on purpose**, so they should still tell you the cluster is down.

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
```

---

## 1. Zabbix (Hugin, LXC 1102 on Urd, `10.0.11.21`)

```bash
$SSH ansible@10.0.11.21 'systemctl is-active zabbix-server zabbix-agent2 nginx; sudo tail -20 /var/log/zabbix/zabbix_server.log'
curl -s -o /dev/null -w 'via Traefik: %{http_code}\n' https://hugin.midgard.xiiisins.com/
curl -s -o /dev/null -w 'direct backdoor: %{http_code}\n' http://hugin-direct.niflheim.xiiisins.com/    # bypasses Traefik; local Admin login only
```

| Symptom | Cause | Fix |
|---|---|---|
| UI via Traefik fails, direct works | Traefik/Authentik/SAML problem | Use the backdoor (`hugin-direct`) with the local `Admin`; fix the edge (**Identity & edge**) |
| Both fail | Server or the database (Postgres VIP) | `journalctl -u zabbix-server`; **Vault & Postgres** B1; `sudo systemctl restart zabbix-server` |
| Local `Admin` locked after 5 bad logins | Zabbix lockout | On the Postgres leader: `sudo -iu postgres psql zabbix -c "UPDATE users SET attempt_failed=0, attempt_clock=0, attempt_ip='' WHERE username='Admin';"` |
| A host shows "no data"/red | Agent name mismatch or agent stopped | Host name in Zabbix must equal the agent's `Hostname=` (the inventory name); `ssh … 'systemctl status zabbix-agent2'`; re-run `zabbix-agent.yml --limit <host>` |
| An item "Unknown metric pgsql.…" | The agent2 plugin package is missing | `zabbix-agent2-plugin-postgresql` is installed by the postgres group vars; re-run the host play |
| A fixed unsupported item stays red | Unsupported items retry every 10 minutes | Wait, or API task type 6 "Check now" |
| "Load average too high" on an LXC | Meaningless in containers; muted fleet-wide | Ignore; if it reappears, the template macro override was lost |
| NVMe temperature alerts on a Proxmox host | Thresholds are 75/85 on purpose | Check the real temperature (`smartctl`), airflow |
| "LXC has been restarted (uptime < 10m)" for `CT1199`/`restore-test-*` | Scratch containers from the restore test | Ignore; they clear on their own |
| Replica loss was silent | Stock template has no trigger | The `Patroni replication` template adds two; make sure it is imported (`zabbix-host-groups.yml`) |
| A template added in Git is missing for a new host | The host registered before the template was imported | `ansible-playbook … playbooks/zabbix-host-groups.yml`, then re-run the host play |
| Canary alert never reached the AIOps agent | The agent only receives High and Disaster; the stock "agent not available" is Average | Use the `Canary smoke test` template's triggers (`playbooks/files/zabbix/canary-smoke-test.yml`), which are High |

Escalation severities map to Hermod tags: Disaster/High → `critical`, Average → `alert`; Warning and Information stay in the UI only. Disk thresholds are global macros (`{$VFS.FS.PUSED.MAX.WARN/CRIT}`), tighter for Postgres.

## 2. Hermod (notifications, LXC 1103 on Verd, `10.0.11.22`)

```bash
$SSH ansible@10.0.11.22 'systemctl is-active apprise-api caddy; sudo tail -5 /var/log/caddy/hermod.log'
# A test notification FROM AN ALLOWLISTED SOURCE (Frigg, a Proxmox host, Hugin, the PG trio, K3s nodes). The key is read from Vault in-process.
KEY=$(vault kv get -field=value secret/ansible/hermod/config-key)
curl -s -X POST "http://hermod.niflheim.xiiisins.com/notify/$KEY" -H 'Content-Type: application/json' \
  -d '{"title":"standby test","body":"manual test from the runbook","type":"info","tag":"info"}'; echo; unset KEY
```

| Symptom | Cause | Fix |
|---|---|---|
| `403 forbidden` from Hermod | The source IP is not allowlisted (e.g. your laptop) | Test from an allowlisted host; adding a producer is `caddy_sites[].allowed_cidrs` in `group_vars/apprise.yml` + a re-run |
| POST succeeds but nothing in Discord | No Apprise URL matches the tag (the Discord secret for that tag was never rendered), or the webhook is dead | `vault kv get -field=url secret/ansible/hermod/discord/<tag> >/dev/null && echo exists`; re-run `asgard-hermod.yml --tags hermod-api` (it fails fast on a missing secret) |
| Message lands in **Hel** (`#hermod-untagged`) | The producer sent no `tag` | Fix the producer; routing is by tag, severity only colours |
| `Unsupported URL … Ignored entry url` after editing the YAML | Apprise wants the URL as the dict **key**, options nested under it | Fix the template, not by hand |
| Patroni failover posted nothing | The callback must always exit 0 and match both `master` and `primary` | `cat /etc/patroni/hermod-callback.sh` on a PG node |
| Sonarr events missing | They bypass Hermod and post straight to the media webhook | Check Sonarr's Discord connection, not Hermod |

Note: the config key appears in access logs; the IP allowlist is the real gate. If Hermod itself is down, alerts are not delivered but Zabbix still records them and logs still ship; watch the Zabbix UI and `critical` problems directly.

## 3. VictoriaMetrics, VictoriaLogs and the shippers

```bash
kubectl -n monitoring get pods,pvc
kubectl -n monitoring get sts                                            # the VictoriaMetrics and VictoriaLogs StatefulSets
kubectl -n monitoring logs sts/<name-from-above> --tail=30
# Is data arriving? (vmui at https://metric.niflheim.xiiisins.com; or from Frigg, the read-only route)
curl -s 'https://metrics-read.niflheim.xiiisins.com/api/v1/query' --data-urlencode 'query=count(up)' | jq '.data.result[0].value[1]'    # from Frigg
# A host's log shipper
$SSH ansible@<host> 'systemctl is-active vlagent; sudo journalctl -u vlagent -n 20 --no-pager'
```

| Symptom | Cause | Fix |
|---|---|---|
| `container_*` metrics missing, vmagent logs `403 Forbidden` | vmagent's ClusterRole lacks `nodes/proxy` | The HelmRelease `rbac.extraRules` must contain it (Git) |
| `kube_*` sums read double | kube-state-metrics scraped twice | Every `kube_*` query must pin `job="kube-state-metrics"` |
| "by node" graphs show one line | cAdvisor series carry `instance`, not `node` | Use `sum by(instance)` |
| A host's logs do not appear | vlagent down, wrong endpoint, or no rsyslog on Debian 13 (journald-only) | Check the unit; K3s VMs must point at the ClusterIP `http://10.43.14.105:9428/insert/native` (hosts cannot reach a MetalLB VIP); LXCs use `https://logs.niflheim.xiiisins.com/insert/native` (the **native** endpoint, not `jsonline`) |
| New dashboard JSON not showing | vmui reads `customDashboardsPath` at start only | `kubectl -n monitoring rollout restart sts/<the VictoriaMetrics statefulset>` |
| Disk filling | Retention 1 month (metrics) / 30 days (logs) on 20 GiB volumes | Grow the worker `/data` disk before raising retention; do not move them to NFS |
| `local-path` volume lost with a worker | These stores are single-instance and refillable | Accept the gap; it refills from live ingest |

vmalert is not enabled; Zabbix and the prober raise alerts, not VictoriaMetrics.

## 4. Semaphore (scheduler)

```bash
kubectl -n semaphore get pods                                           # semaphore-0 Running
curl -s "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/last" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[0:5][]|"\(.id) \(.status) \(.template_id)"'
```

| Symptom | Cause | Fix |
|---|---|---|
| A task stays `running` for hours; later jobs `waiting` | The project runs one task at a time. A fleet reboot killed `semaphore-0`'s node mid-play (it has no PVC and lands anywhere) | Read the end of the output (`.../tasks/<id>/output`: it cuts off mid-handler); stop the task: `curl -s -X POST "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/<id>/stop" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN"`; the playbook is idempotent, so re-run it. Check the rebooted node returned (`kubectl get nodes`) |
| Task "success" but `skipping: no hosts matched` everywhere | Cold NetBox inventory cache plus a NetBox 500 produced zero hosts and Ansible exited 0 | Run the `refresh-netbox-inventory` template; check NetBox (`kubectl -n netbox get pods`); the `site.yml` preflight now fails loudly under 20 hosts |
| A group's variables are ignored in Semaphore only | NetBox builds groups from `ansible:<group>` tags; a group that exists only in `hosts.yml` is empty there | Add the tag in `terraform/netbox/tags.tf`/`vms.tf`, apply, refresh the inventory |
| Per-host vars missing in Semaphore | Inline `hosts.yml` host vars are invisible to the NetBox inventory | Put them in `inventory/host_vars/<host>.yml` |
| Run fails on a task using a collection feature | Semaphore ran the older image-bundled collections | The custom image bakes the pins; rebuild/bump it via CI |
| Vault login fails in a task | `ansible-awx` SecretID stale, or the internal CA missing | **AppRole rotation** (C); `ANSIBLE_HASHI_VAULT_CA_CERT` |
| Hermod message flood after a drift check | The drift is real | Read the diff in the task output; fix the IaC or apply |
| A canary host pages | It must not: canary alerts cap at `info` | Canary hosts belong in `site-nonprod.yml`, never `site.yml` |
| Pod OOM or restarts | Memory limit | Check `kubectl -n semaphore describe pod semaphore-0` |

`asgard-apply` mutates the fleet; start it only deliberately. A task started via the API with a `git_branch` runs that branch's code; treat the API token as powerful.

## 5. The health prober (`infra-health-check`, every 12 h)

It posts `critical` for: Cloudflare token invalid, a certificate under 3 days, Patroni/etcd quorum lost, a failed PBS task, the prober erroring, a lapsed Frigg remote-control session, control-node env drift (`frigg/iac-env`). It posts `alert` (not `critical`) when the `do1` `/health` fails, because `do1` is the failover node, not a primary service. Run it now: **Everyday operations** §8. If it itself errors (`RB-S4-PROBER-ERRORED`), read the task output; common causes: an expired credential it uses (Vault token, PBS API token), or a Hermod 403.

| Alert | Do |
|---|---|
| Cloudflare token invalid | **Credential rotation** §3 (check the verify endpoint first) |
| PBS task failed | §7 below |
| Patroni/etcd quorum | **Vault & Postgres** B1 |
| Cert under 14/3 days | **Identity & edge** §3 |
| `frigg/iac-env` field stale | **Credential rotation** §9 |

## 6. Frigg (control node, VM 2900, `10.0.11.30`)

```bash
ssh ansible@frigg.niflheim.xiiisins.com; sudo -iu ghost                      # then:
. /usr/local/bin/homelab-env && homelab-env                                 # load the env (Vault-backed; no `op`)
systemctl is-active frigg-ssh-agent claude-remote-control frigg-reauth-listener aiops-toolbelt
ssh-add -l                                                                  # one fingerprint = the fleet key is loaded in the agent
```

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied (publickey)` to fleet hosts | The memory-only agent is empty/down (the key is never on disk) | `sudo systemctl restart frigg-ssh-agent` (it reloads the key from Vault); `ssh-add -l`. On Frigg `ANSIBLE_PRIVATE_KEY_FILE` points at the **public** key file; with `IdentitiesOnly` the agent supplies the private half |
| `homelab-env`/`vault-homelab-env` fails with login errors | The `ansible-frigg` SecretID expired (90 days) | **AppRole rotation** (B); then re-run the control-node play with the new SecretID |
| Playbook runs a stale version of the repo | Frigg's checkout is not auto-updated | `cd ~/homelab && git -c core.fileMode=false pull --ff-only` before Frigg-driven applies |
| `*.niflheim` lookups fail only on Frigg | systemd-resolved stuck to the UCG fallback | `resolvectl status`; set the AdGuard VIP as eth0's DNS; reboot-test |
| Remote control is `failed` (`You must be logged in to use Remote Control`) | The login session was invalidated; the unit crash-looped past its start limit | The listener auto-remediates: it posts an auth URL to Discord and serves a paste-back page at `http://frigg-auth.niflheim.xiiisins.com:8686/` (internal only). Manual: `claude auth login` as `ghost`, then `sudo systemctl reset-failed claude-remote-control && sudo systemctl restart claude-remote-control` (the `reset-failed` is required within 5 minutes of the crash) |
| Terraform from Frigg `permission denied` on `auth/token/create` | The Frigg policy lacked it; now granted | Use the root token modules from the operator's workstation |
| A shim change has no effect | The three-hour cache | `vault-homelab-env --refresh >/dev/null 2>&1` |
| Drift-check shows `changed` on a Frigg task | A file a runtime process also writes was fully managed | Note it; the fix is a drop-in layer, not a template fight |

## 7. PBS and backup alerts (`RB-PBS-BACKUP-FAILURE`)

```bash
ssh root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager task list --limit 20 | grep -v OK'
ssh root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager task log <upid> | tail -30'
ssh root@urd.niflheim.xiiisins.com 'pvesm status | grep pbs'                # datastore active?
```

Typical: the datastore NFS mount is gone (mount it, restart `proxmox-backup` and `proxmox-backup-proxy`), the datastore is full (check retention/GC), a guest was locked or migrated mid-job, or Munin is down (**Hosts & Proxmox** §8). Prove recovery with a restore test: **Backups & restore drills** §3.

## 8. AIOps (Gná, Ratatoskr, the Toolbelt)

In Discord: `/aiops status` (kill switch, budgets, open proposals), `/aiops pending`, **`/aiops kill [reason]`** stops everything (nothing can be approved or started; resume with `/aiops resume`), `/aiops maintenance on|off`, `/aiops autonomy on|off|reset-breaker`. Only the operator's Discord account can approve; cards expire after 4 hours undecided and nothing runs.

```bash
$SSH ansible@10.0.11.221 'systemctl is-active n8n caddy; sudo journalctl -u n8n -n 30 --no-pager'                    # Gná
$SSH ansible@10.0.11.222 'systemctl is-active ratatoskr; sudo journalctl -u ratatoskr -o cat -n 30 | grep -E "ready|feed_error|privileged_intent_missing"'    # the bot
$SSH ansible@10.0.11.30 'sudo journalctl -u aiops-toolbelt -n 30 --no-pager'                                          # the Toolbelt on Frigg
```

| Symptom | Cause | Fix |
|---|---|---|
| Bot never says `ready` | Egress to `gateway.discord.gg` blocked at the UCG, or a bad token | UCG policy for `10.0.11.222`; `journalctl -u ratatoskr` |
| Approval cards do not appear (`feed_error` 403) | The Toolbelt's actions are off, or the approver token/IP is wrong | `journalctl -u ratatoskr`; role `--tags aiops-toolbelt` |
| A run fails at once | See `step` events in the Toolbelt log; open the task in the Semaphore `aiops` project | Fix the underlying play |
| "I could not reach my brain" | n8n or the chat route is down | `systemctl restart n8n` on Gná; alerts and diagnosis paths are unaffected |
| Diagnosis threads missing | Only High/Disaster reach the agent; the n8n ingest listener is allowlisted | Check the Zabbix media type and `caddy` on Gná |
| Gná cannot reach the internet to deploy | Egress is default-deny; the "Deploy Window" policy is paused on purpose | Unpause only while re-running the n8n/vlagent roles, then pause again; keep the allows above the deny |
| Bot update did not take | The code-deploy template ran old code, or the dispatcher was not restarted | `aiops-code-deploy` runs every 15 minutes; check its last run in Semaphore |
| A canary heals itself and pages nobody | Intended: canary alerts cap at `info` and autonomous healing is canary-only | `scripts/canary/fault stop <canary> <unit>` to exercise it deliberately |

If anything looks wrong about what the bot is doing, **`/aiops kill` first, investigate second.**

## 9. Offsite node and outside watcher (`do1`)

`do1.xiiisins.com` (DigitalOcean) runs the Teamspeak failover and a Gatus watcher that checks the public endpoints, TCP reachability of Frigg and Hermod over the tailnet, and a heartbeat from Frigg (alert after about 15 minutes of silence), posting to a Discord channel **not** fed by Hermod. A Gatus alert that Hermod did not echo means the problem may be Hermod/Discord itself.

```bash
curl -s -m 8 https://do1.xiiisins.com/health ; echo
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@do1.xiiisins.com 'systemctl is-active gatus; sudo journalctl -u gatus -n 20 --no-pager'   # do1 is converged by hand (playbooks/do1.yml) and excluded from the fleet plays on purpose
systemctl list-timers | grep gatus-heartbeat; sudo journalctl -u gatus-heartbeat -n 5 --no-pager                  # on Frigg: the heartbeat is a timer
```

---

## See also

- **Observability** (Components) and **Zabbix**, **Hermod**, **Semaphore**, **AIOps agent** (Services).
- **Everyday operations**, **Credential rotation**, **Backups & restore drills** (Procedures).
- **Hosts & Proxmox** (this section).
