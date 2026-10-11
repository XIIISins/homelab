<!-- docs/outline/troubleshooting.md -->

# Troubleshooting

You are on call, something is wrong, and you did not build this. Start here. The method is always the same: **sweep, narrow, read the logs, fix the smallest thing, verify.** Everything below is copy-paste; every diagnostic is read-only unless it says otherwise.

Do **Session setup** (see **Procedures**) first: it loads credentials into your shell without you typing any.

---

## 1. The health sweep (run this first)

One block, read-only, about 30 seconds. It prints `OK` or a problem line per area. Copy the whole thing into a terminal that has the shim loaded.

```bash
SSH="ssh -o IdentitiesOnly=true -o ConnectTimeout=5 -i $ANSIBLE_PRIVATE_KEY_FILE"
hdr() { printf '\n=== %s\n' "$*"; }

hdr "DNS: the VIP and each AdGuard node answer an internal name"
for s in 10.0.10.200 10.0.11.201 10.0.11.202 10.0.11.203; do
  printf '%-14s ' $s; dig +short +time=2 +tries=1 @$s pbs.niflheim.xiiisins.com | grep . || echo "NO ANSWER"
done

hdr "Web edge: DNS + Traefik + backend (expects: smoketest ok)"
curl -s -m 8 https://smoketest.niflheim.xiiisins.com/anything || echo "FAILED"; echo

hdr "Proxmox quorum, per host"
for h in urd verd skuld; do
  printf '%-6s ' $h; $SSH root@$h.niflheim.xiiisins.com 'pvecm status 2>/dev/null | grep -E "Quorate" | tr -s " "' || echo "UNREACHABLE"
done

hdr "Kubernetes nodes (expect 6 Ready)"
kubectl get nodes --no-headers 2>&1 | awk '{print $1, $2, $5}'

hdr "Pods not Running/Completed"
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded --no-headers 2>&1 | head -20

hdr "Flux: anything not Ready"
flux get kustomizations 2>&1 | grep -v " True " ; flux get helmreleases -A 2>&1 | grep -v " True "

hdr "External Secrets: anything not synced"
kubectl get externalsecrets -A --no-headers 2>&1 | grep -v SecretSynced

hdr "Certificates: anything not Ready"
kubectl get certificate -A --no-headers 2>&1 | grep -v " True "

hdr "PVCs not Bound; iSCSI LUN count (ceiling ~10)"
kubectl get pvc -A --no-headers 2>&1 | grep -v Bound; echo "iSCSI PVs: $(kubectl get pv --no-headers 2>/dev/null | grep -c synology)"

hdr "Vault (each pod: sealed? HA mode?)"
for i in 0 1 2; do printf 'vault-%s ' $i; kubectl -n vault exec vault-$i -c vault -- vault status 2>/dev/null | grep -E "Sealed|HA Mode" | tr -s ' ' | tr '\n' ' '; echo; done

hdr "Postgres/Patroni: leader + replicas"
for ip in 10.0.11.230 10.0.11.231 10.0.11.232; do   # any member answers; Fulla (.230) is on Skuld, which freezes
  $SSH ansible@$ip 'sudo patronictl -c /etc/patroni/patroni.yml list' 2>/dev/null | tail -8 | grep . && break
done || echo "NO PATRONI MEMBER ANSWERED"
hdr "Postgres VIP accepts TCP; etcd healthy on the trio"
timeout 3 bash -c '</dev/tcp/10.0.10.210/5432' && echo "VIP 10.0.10.210:5432 open" || echo "VIP DOWN"
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do printf '%s ' $ip; curl -s -m 3 http://$ip:2379/health || echo "etcd DOWN"; echo; done

hdr "Traefik + Cloudflared"
kubectl -n traefik get pods --no-headers | awk '{print $1,$2,$3}'; kubectl -n cloudflared get pods --no-headers | awk '{print $1,$2,$3}'
curl -s -o /dev/null -m 8 -w 'public authentik: HTTP %{http_code}\n' https://authentik.xiiisins.com/

hdr "Backups: PBS reachable, newest job results"
$SSH root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager task list --limit 5 2>&1 | head -8' || echo "PBS UNREACHABLE"

hdr "Semaphore queue (a task stuck 'running' blocks the project)"
curl -s -m 8 "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/last" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[0:3][]|"\(.id) \(.status) \(.tpl_alias // .template_id)"' 2>&1
```

**Reading the result.** Anything that prints `NO ANSWER`, `DOWN`, `UNREACHABLE`, a non-`True` Flux row, or a pod in `CrashLoopBackOff`/`Pending` points you at a page in the index below. If **several unrelated** areas fail at once, suspect the lowest layer first: **network/DNS, then Postgres, then Vault, then the cluster.** If a whole Proxmox host is missing, go straight to **Hosts & Proxmox**.

**Quorum arithmetic to keep in your head:** every trio (Proxmox, etcd on the K3s control planes, the HAProxy/etcd trio, Patroni, Vault) tolerates **one** member down and stops on two. One host down is normal; **two hosts down at once is an emergency: stop and escalate.**

---

## 2. Index by symptom

| You see | Go to |
|---|---|
| A pod stuck in `ContainerCreating` with a mount error, a volume read-only, "Multi-Attach", a PVC that will not bind | **Storage & iSCSI** |
| An internal name returns NXDOMAIN, a VIP does not answer, DNS is stale after a failover, LoadBalancer traffic times out | **DNS & networking** |
| A node will not rejoin, a rollout is stuck, a HelmRelease will not reconcile, a Secret will not sync, a Job will not update | **Kubernetes & Flux** |
| Vault is sealed, a token or AppRole fails, Postgres rejects a client, a write hits a replica, a failover looks wrong | **Vault & Postgres** |
| A login redirects to the wrong host or denies a valid user, a certificate will not renew, the tunnel returns 404/502, SSH reports a host key change | **Identity & edge** |
| A Proxmox host or LXC is down or frozen, a guest will not start, a node is unreachable, Munin or Tailscale trouble | **Hosts & Proxmox** |
| Zabbix, Hermod, VictoriaMetrics/Logs, Semaphore, Frigg or the AIOps bot misbehave; an alert fired | **Monitoring & automation** |
| Jellyfin, Sonarr/SABnzbd, Teamspeak, Factorio, Immich, Outline, MicroBin, NetBox, the wiki or the status page is broken | **Applications** |

## 3. Index by alert

Hermod posts to Discord with a `runbook` hint. Each alert class maps to a section:

| Alert / runbook id | Likely meaning | Go to |
|---|---|---|
| `critical` Postgres/etcd quorum, `RB-PG-HA-DEGRADED` | A Patroni or etcd member down, no leader | **Vault & Postgres**; **Postgres operations** |
| `RB-VAULT-APPROLE-EXPIRY` | An AppRole SecretID is about to expire | **AppRole rotation** |
| `RB-VAULT-RAFT-DRAIN` | A Vault node being drained/replaced | **Vault recovery** |
| `RB-TLS-CERT-EXPIRY`, `RB-CF-TOKEN-INVALID` | A wildcard cert will not renew; Cloudflare token stale | **Identity & edge**; **Credential rotation** |
| `RB-CRED-IAC-ENV-SYNC` | A rotation missed the control node's env secret | **Credential rotation** §9 |
| `RB-ESO-FORCE-SYNC` | A secret value has not reached the pod | **Kubernetes & Flux** |
| `RB-FLUX-HR-STALLED`, `RB-FLUX-HR-NODE-OUTAGE` | HelmRelease stalled or waiting on a down node | **Kubernetes & Flux** |
| `RB-ISCSI-STALE-SESSION` | iSCSI LUN pinned to the wrong node | **Storage & iSCSI** |
| `RB-ETCD-RAFT-SYSLOG-FLOOD` | A control plane's disk filling while a peer is down | **Kubernetes & Flux** |
| `RB-CALICO-DATASTORE-PRUNE` | Cross-node pod traffic broken, IP pools gone | **K3s & Calico upgrade**; escalate |
| `RB-HOST-HARD-FREEZE`, `RB-LXC-BOOT-DRIFT` | A host froze (Skuld); LXC config reset by reboot | **Hosts & Proxmox** |
| `RB-GUEST-DEAD`, `RB-GUEST-BROKEN`, `RB-UNIT-STOPPED-T1` | A guest or unit down (canaries heal themselves) | **Hosts & Proxmox**; **Monitoring & automation** |
| `RB-PBS-BACKUP-FAILURE` | A PBS task failed | **Monitoring & automation**; **Backups & restore drills** |
| `RB-S4-PROBER-ERRORED`, `RB-SEMAPHORE-RUN-FAILED`, `RB-ZBX-TRIAGE` | The health prober, a Semaphore run, or Zabbix itself | **Monitoring & automation** |
| `RB-FRIGG-RC-LOGIN` | The control node's remote-control login lapsed | **Monitoring & automation** |
| `RB-ALERT-UNCLASSIFIED` | A producer sent a message the router does not know | Read the alert; find the source host's page |

Severity meaning: `critical` look within minutes; `alert` within hours; `info` never needs a response (canary and non-production noise).

---

## 4. First-response habits

- **Read before you write.** Logs and `describe` first; most fixes are one restart.
- **Change one thing, re-check, change the next.**
- **Never** `kubectl apply` a fix: fix Git, or restart/scale as the runbook says.
- **Reboot-test** any fix that must survive a restart.
- **Write down** what you ran and saw; an incident note in `docs/incidents/` is expected for anything non-trivial.
- **Escalate** (see the list in **Procedures**) rather than guessing when two quorum members are affected, data would be lost, or the root token is needed.

---

## See also

- **Procedures**: the runbook for the fix once you know the problem.
- **Components & interactions**: how the failing piece connects to its neighbours.
