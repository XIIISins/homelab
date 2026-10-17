<!-- docs/operations/zabbix-service-coverage.md -->

# Zabbix service-level coverage

*Audited and closed 2026-10-17. Why: the 10f work found that a stopped `AdGuardHome` on a real replica raised no High alert, because the only Zabbix coverage on most hosts was the stock Linux template (host up, disk, memory, CPU), which says nothing about the service the host exists for. This page is the inventory: what each service depends on, whether Zabbix watches it, what was added, what is still open and who acts on a page. Design of Zabbix itself: [`services/zabbix.md`](../services/zabbix.md); gotchas: [`known-issues/zabbix.md`](../known-issues/zabbix.md).*

## How coverage is built

| Layer | Source of truth | What it gives |
|---|---|---|
| Host | stock `Linux by Zabbix agent` on every guest (load muted in LXCs, see known-issues) | agent availability (Average), disk, memory, CPU, interfaces. Guest-level "not running" comes from `Proxmox VE by HTTP` on urd/verd/skuld. |
| Service units and ports | `playbooks/files/zabbix/asgard-services.yml`, one small template per host class, linked in `group_vars/<group>.yml` | `systemd.unit.info[<unit>,ActiveState]` (agent), `net.tcp.service[tcp,,PORT]` (run by the server against the host IP, so it tests the client's path), `net.udp.listen[PORT]` |
| Endpoints (not hosts) | `playbooks/files/zabbix/asgard-service-endpoints.yml` on the virtual host `asgard-services` (no agent; declared by `zabbix_virtual_hosts` in `group_vars/all/zabbix.yml`) | web scenarios (status code + required string) against the K3s-hosted apps, TCP checks on the PostgreSQL VIP, Traefik VIP and Munin ports, ICMP to the UCG. Run by Hugin, outside K3s. |
| Custom per-service | `canary-smoke-test`, `jellyfin`, `patroni-replication`, `gna-network-posture`, `proxmox-igpu` (same directory) | unchanged by this audit |

Everything is declared in git and applied by `playbooks/zabbix-host-groups.yml` (imports the templates and creates the virtual host) followed by the agent role's `register-host` (links the templates). A new template needs no new mechanism: add the file to the loop in the bootstrap play, add its name to the group's `zabbix_agent_link_templates`.

## Severity policy used here

Hermod maps Zabbix **High and Disaster to `critical`** (Hrist, `@everyone`, "look within minutes, even at 2am") and **Average to `alert`** (Mist, "look within hours"); Warning and below stay in the Zabbix UI ([`notifications.md`](../services/notifications.md)). Gna (the diagnosis agent) and therefore autonomous restart receive **High and Disaster only**. So:

- **High** only where a human should be woken: Hermod (the path every other alert leaves through), the Traefik VIP and its end-to-end ingress probe, the PostgreSQL VIP. These also reach Gna.
- **Average** for everything else that is a real service failure but can wait for the morning. These never reach Gna or autonomy, which is the deliberate default: widening autonomy is a separate reviewed decision (candidates are listed at the end, nothing is routed).
- **Warning** where the failure only affects the operator's own tooling (SFTP port, the Factorio reconcile timer).
- The AdGuard and Tailscale unit triggers (10f3, `adguard-unit-state.yml`, `tailscale-unit-state.yml`) are High but named `Unit down: ...`, which `hermod-webhook.js` caps at the `alert` tag: they reach Gna and autonomy without paging. That is the pattern to copy if a unit here is ever promoted for autonomy.
- Trigger names avoid the phrases `aiops/alert-routing.yml` uses for host-down routing ("is not available", "unreachable", "host is down", "no data from"), and one stopped service is one problem: a port check depends on its unit trigger.

## Inventory

"Before" is what Zabbix watched at the start of the audit (live host/template list from the API, 2026-10-17). "Now" adds this audit. Remediation owner is who acts when it fires: **operator** unless noted.

### LXCs and VMs

| Service | Host | Unit / endpoint | Before | Now (check, severity) | Owner / notes |
|---|---|---|---|---|---|
| Zabbix server, UI | Hugin | `zabbix-server` process, nginx, php-fpm | yes: `Zabbix server health`, `Nginx`, `PHP-FPM` templates | + `vlagent.service` (Average) | operator. **Nothing watches Zabbix itself** (see gaps). |
| Hermod (AppriseAPI + Caddy) | Hermod | `caddy.service`, `apprise-api.service`, `:80` | host only | `caddy.service`, `apprise-api.service`, `:80` TCP, all **High**; + vlagent | operator. Zabbix to Gna still carries High when Hermod is down; Gatus on do1 watches `:80` independently. |
| PBS | PBS (LXC 1101) | `proxmox-backup-proxy.service`, `proxmox-backup.service`, `:8007` | host only; failed backup tasks via the S4 prober (12 h, Hermod) | units and `:8007` (Average); + vlagent | operator. Prober still owns "backup task failed". |
| Gna (n8n) | Gna | `n8n.service`; Caddy `:8081`; `:5678` must be closed | `Gna network posture` template (door, listeners, egress) | + `n8n.service` (Average); + vlagent | operator. n8n cannot heal itself, so not an autonomy candidate. |
| Ratatoskr (Discord bot) | Ratatoskr | `ratatoskr.service` (outbound only) | host only | `ratatoskr.service` (Average); + vlagent | operator. It is the only approver of AIOps actions. |
| Jellyfin | Jellyfin | `jellyfin.service`, `/health`, media mount, QSV smoke | yes: `Jellyfin` template | + vlagent; ingress path on the endpoints host (below) | operator. |
| Factorio + SFTPGo | Factorio | `factorio.service`, UDP 34197, `sftpgo.service`, SFTP 22022, `factorio-reconcile.timer` | host only | all five: game unit and UDP listener Average, `sftpgo.service` Average, SFTP port and reconcile timer **Warning**; + vlagent | operator. Friends notice first; Average gives the morning, not the night. |
| PostgreSQL trio | Fulla, Vor, Idunn | `patroni.service`, REST `:8008`, `postgresql@17-main` | `PostgreSQL by Zabbix agent 2` (service down is High), `Patroni replication` (lag, not streaming) | + `patroni.service` and `:8008` (Average); + vlagent | operator. Do not auto-restart: a leader restart is a failover. |
| HAProxy + etcd trio | Hlin, Eir, Snotra | `haproxy.service`, `etcd.service`, `keepalived.service`, `vlan10-policy-routing.service`, VIP `10.0.10.210:5432` | `HAProxy by Zabbix agent`, `Etcd by HTTP` (both Average) | + `keepalived.service`, `vlan10-policy-routing.service` (Average); VIP port on the endpoints host (High); + vlagent | operator. HAProxy and etcd units are left to their stock templates (it would double the problems). |
| AdGuard Home trio | Saga, Mimir, Kvasir | `AdGuardHome.service`, `keepalived.service`, `adguardhome-sync.service` (Saga), VIP `10.0.10.200:53` | host only | `AdGuardHome.service` on Mimir and Kvasir is **High** ("Unit down", the `AdGuard unit state` template from the 10f3 replica work, capped at the Hermod alert tag, so it reaches Gna without paging); Saga, the origin, is deliberately excluded; + vlagent (all three) | operator for Saga; the autonomy policy restarts the replicas. Still open: `keepalived.service`, `adguardhome-sync.service`, a DNS answer probe (gaps). |
| Tailscale LXCs | Bifrost, Heimdall, Gjallarbru | `tailscaled.service` | host only | `tailscaled.service` **High** ("Unit down", `Tailscale unit state`, same cap); + vlagent | operator; autonomy-scoped like the replicas. Frigg's own tailscaled is covered below. |
| Frigg (control node, VM) | Frigg | `aiops-toolbelt.service` + `:8090`, `claude-remote-control.service`, `frigg-ssh-agent.service`, `tailscaled.service`, `gatus-heartbeat.timer` | host only; liveness by the Gatus dead-man heartbeat from do1 | all six (Average); + vlagent | operator. Reboots of the HA VM briefly flip several; a 5-minute window absorbs a restart. |
| K3s control planes | Gondul, Hlokk, Sigrun | `k3s.service`, API `:6443` | host only | `k3s.service` and `:6443` (Average); + vlagent | operator. One CP down is Average on purpose (3 CPs tolerate it). Two down is two Average problems; there is no cross-host "quorum lost" trigger yet (a trigger across hosts cannot live in a template). |
| K3s workers | Einherjar-urd/verd/skuld | `k3s.service` (agent mode) | host only | `k3s.service` (Average); + vlagent | operator. |
| Proxmox hosts | Urd, Verd, Skuld | PVE API, quorum, guests, SMART, iGPU | yes: `Proxmox VE by HTTP`, SMART, iGPU | + vlagent | operator. Cluster not quorum, node offline and API unavailable are already High. |
| Canaries | canary-1..3 | smoke triggers | yes | none | disposable, capped to Hermod `info`; unchanged. |

### Services hosted in K3s (probed from Hugin, host `asgard-services`)

| Service | Endpoint | Before | Now | Severity | Notes |
|---|---|---|---|---|---|
| Traefik / Gateway | `10.0.20.10:443` + `https://smoketest.niflheim.xiiisins.com/` ("smoketest ok") | none | TCP check; web scenario (DNS rewrite, Traefik, certificate, apex-static backend) | **High** | the scenario depends on the TCP trigger so a VIP outage is one problem |
| PostgreSQL VIP | `10.0.10.210:5432` | none (stock HAProxy/PostgreSQL triggers are per node) | TCP check | **High** | proves HAProxy listens, not that a leader backs it; the stock triggers say that |
| Vault | `https://vault.niflheim.xiiisins.com/v1/sys/health` (200 or 429, `"sealed":false`) | none | web scenario | Average | Traefik balances over three pods, so active and standby both pass |
| Authentik | `https://authentik.midgard.xiiisins.com/-/health/ready/` | none | web scenario | Average | Zabbix SAML login depends on it; local `Admin` and the Hugin backdoor still work |
| NetBox | `https://netbox.niflheim.xiiisins.com/login/` | none | web scenario | Average | the Ansible dynamic inventory and Semaphore read it |
| Semaphore | `https://semaphore.niflheim.xiiisins.com/api/ping` | none | web scenario | Average | drift checks and the S4 prober stop with it |
| Outline | `https://wiki.midgard.xiiisins.com/_health` | none | web scenario | Average | |
| Immich | `https://immich.midgard.xiiisins.com/api/server/ping` | none | web scenario | Average | |
| Jellyfin ingress | `https://jellyfin.midgard.xiiisins.com/health` | LXC-local `/health` only | web scenario | Average | separates "Jellyfin sick" from "jellyfin-ingress/Traefik sick" |
| Garage S3 | `https://s3.xiiisins.com/` (403 `AccessDenied` is healthy) | none | web scenario | Average | |
| TeamSpeak | `10.0.20.12:30033` TCP | none | TCP check | Average | voice is UDP 9987 and cannot be probed this way |
| Munin (Synology) | `10.0.254.20` `:3260` iSCSI, `:2049` NFS, `:5001` DSM | none | three TCP checks | Average | iSCSI is Average, not High, because a DSM update reboot outlasts the window |
| UCG-Ultra | `10.0.254.1` ICMP | none | ICMP check | Average | |

### Covered elsewhere, not by Zabbix

| What | By | Note |
|---|---|---|
| Public endpoints (`xiiisins.com` WebFinger, `home.`, `paste.`), Frigg `:22`, Hermod `:80`, Frigg heartbeat | Gatus on `do1` ([`offsite-watcher.md`](../procedures/offsite-watcher.md)) | independent path and Discord webhook; `do1` is deliberately not a Zabbix host |
| Cloudflare token, served-certificate expiry, Patroni `/cluster`, etcd `/health`, PBS failed tasks | S4 prober (`infra-health-check.yml`, every 12 h, Hermod) | cadence is hours, so Zabbix's 5-minute checks add the fast signal for the same dependencies |

## Remaining gaps and why

| Gap | Why it is not closed here | Proposed next step |
|---|---|---|
| Nothing watches Zabbix/Hugin itself | Zabbix cannot page for its own death. The "Zabbix server health" template is self-monitoring. | Add `hugin.xiiisins.com` (the login page) to Gatus on do1 (`gatus_public_endpoints` in `group_vars/offsite.yml`, then `do1.yml`, which is converged by hand). |
| In-cluster health: node NotReady, CrashLoopBackOff, Flux/HelmRelease failing, cert-manager, ESO, MetalLB, Calico, CSI | No alerting layer exists in K3s (VMAlert is Phase 8b, deferred). Only the user-visible edge is probed above. | Either VMAlert, or the official `Kubernetes cluster state by HTTP` template with a read-only ServiceAccount (RBAC in Flux, token in Vault). Decide with Phase 8b. |
| AdGuard: `keepalived.service`, `adguardhome-sync.service` (Saga), a DNS answer probe through the VIP `10.0.10.200` and each node | The `AdGuardHome.service` trigger landed separately (10f3) and is scoped to the autonomy replicas; these three are different failures. | Add them to the `AdGuard unit state` template's neighbours (a DNS probe is a `net.dns` agent check). The ingress smoketest here fails when DNS is down, but cannot say which node. |
| VictoriaMetrics / VictoriaLogs liveness | The public routes are behind Authentik ForwardAuth (a healthy and a dead backend both answer 302) and the read routes are allow-listed to Frigg. | Probe from Frigg (allow-listed) via a UserParameter, or let VMAlert watch itself through an external dead-man check. |
| Sonarr, SABnzbd, Recyclarr, microbin, startpage, overview | ForwardAuth 302 hides the backend; the static ones are low value (the smoketest covers the same Caddy stack). | Skip until media automation is in family use; then a UserParameter on a worker or an in-cluster probe. |
| PostgreSQL dump freshness (`pg-backup`) | Dumps run on the leader only and the leader moves; no status file is exported. | Have the role write a status file and export `vfs.file.time` or a UserParameter on all three nodes; alert when no node has a dump under 36 h. |
| Munin health beyond ports (RAID, volume use, SMART, UPS) | Needs SNMP enabled in DSM (a manual UI step) and a Synology template. | Enable SNMP v3 read-only in DSM, add `Synology DSM by SNMP` to a Munin host, store the credential in Vault. |
| Cloudflared tunnel | The public path is what users use; Gatus on do1 already probes it from outside. | none |
| Stock-template noise | About 55 triggers evaluate as `not supported` (interface speed files on VMs and LXCs, VMware/IPMI/Java/SNMP pollers that are not configured). They cannot fire, but they hide real unsupported items. | Disable those LLD overrides in `zabbix-proxmox-template-tuning.yml` style. Tracked in [`open-questions.md`](open-questions.md). |

## Autonomous-restart candidates (not wired)

Nothing in this change touches `aiops/alert-routing.yml` or `autonomy.hosts`. Only High and Disaster reach Gna, so every Average trigger above needs a deliberate severity change plus a routing row before it could ever restart anything. Candidates, with the reason each is or is not safe, for the next reviewed decision:

| Unit | Verdict |
|---|---|
| `vlagent.service` (all hosts) | safe: stateless, a restart loses at most seconds of logs; the canary soak already proves it |
| `ratatoskr.service`, `sftpgo.service`, `gatus-heartbeat.timer` | likely safe; Ratatoskr's restart drops pending approval buttons |
| `caddy.service` / `apprise-api.service` on Hermod | safe to restart and already High, but Hermod is the alert path: prove it on a canary first |
| `proxmox-backup-proxy.service` | safe outside the backup window; refuse while a task runs |
| `keepalived.service` | restart moves a VIP: only with the peer healthy |
| `patroni.service`, `k3s.service` | no: a restart is a failover or a node drain |
| `n8n.service`, `aiops-toolbelt.service`, `tailscaled.service` (Frigg) | no: the actor cannot repair itself |
| `factorio.service` | no: a restart kicks the players |

## Verification (2026-10-17)

Applied with `zabbix-host-groups.yml --tags zabbix-agent:bootstrap` (templates, virtual host) then `zabbix-agent.yml --tags zabbix-agent:register` and `asgard-zabbix.yml --tags zabbix-agent:register --limit hugin` (linking). All 124 host items created by the new templates were Supported with plausible values (every unit `active`, every TCP check 1, every web scenario passing); the open-problem list before and after was identical; a live stop of `vlagent` on Ratatoskr opened and then cleared the new Average problem (see the PR for timings). A quick re-check: Data collection, Hosts, filter by tag `scope`, or `item.get` on the template names.

## Adding to this

1. Pick the unit (`systemctl list-units`) or endpoint, and the severity from the policy above.
2. Add it to the matching template in `asgard-services.yml` or `asgard-service-endpoints.yml` (new template: also add its name to the group's `zabbix_agent_link_templates`).
3. `ansible-playbook playbooks/zabbix-host-groups.yml --tags zabbix-agent:services-template`, then `zabbix-agent.yml --tags zabbix-agent:register` for new links. Check the new items are Supported before you walk away; update the table above.
