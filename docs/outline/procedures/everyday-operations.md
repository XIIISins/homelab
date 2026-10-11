<!-- docs/outline/procedures/everyday-operations.md -->

# Everyday operations

The small, safe moves you will use constantly: look at state, read logs, restart something, force a secret to refresh, run a playbook, start a scheduled job by hand. Everything here assumes you did **Session setup** (see **Procedures**).

---

## 1. Look at state (all read-only)

```bash
# Cluster
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
kubectl get events -A --sort-by=.lastTimestamp | tail -30
kubectl top nodes 2>/dev/null; kubectl top pods -A --sort-by=memory 2>/dev/null | head -15

# GitOps
flux get kustomizations
flux get helmreleases -A
flux get sources git

# Secrets sync
kubectl get externalsecrets -A | grep -v "SecretSynced"       # anything not synced
kubectl get clustersecretstore vault

# Certificates (all should be Ready=True)
kubectl get certificate -A

# What do the volumes look like?
kubectl get pvc -A | grep -v Bound
kubectl get pv | grep -c synology                              # iSCSI LUN count; the NAS ceiling is ~10
```

Proxmox, from a node (`ssh root@urd.niflheim.xiiisins.com`):

```bash
pvecm status | grep -E "Quorate|Nodes"       # expect Quorate: Yes, Nodes: 3
pct list                                     # LXCs on this node (add `qm list` for VMs)
qm list
ha-manager status                            # HA resources (Frigg)
pvesh get /cluster/resources --type vm --output-format json | jq -r '.[]|"\(.vmid) \(.name) \(.node) \(.status)"' | sort
```

---

## 2. Read logs

```bash
# A pod
kubectl -n <ns> logs deploy/<name> --tail=100
kubectl -n <ns> logs deploy/<name> --previous --tail=100     # the crashed container
kubectl -n <ns> describe pod <pod> | tail -30                # events explain Pending / ImagePull / mount errors

# A service on an LXC or VM
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@<host>.niflheim.xiiisins.com \
  'sudo journalctl -u <unit> -n 100 --no-pager'

# The same, with no SSH: through the Proxmox host
ssh root@<pve-host>.niflheim.xiiisins.com 'pct exec <ctid> -- journalctl -u <unit> -n 100 --no-pager'
```

Everything is also searchable in VictoriaLogs at `https://logs.niflheim.xiiisins.com` (sign in through Authentik). From the control node, the read-only query route needs no sign-in:

```bash
# from Frigg only (IP-allowlisted)
curl -s https://logs-read.niflheim.xiiisins.com/select/logsql/query \
  --data-urlencode 'query=_time:15m error' -d limit=50
```

---

## 3. Restart a workload

Restarting is the safest write operation. Check the pod is the problem first (logs), then:

```bash
# Deployment / StatefulSet / DaemonSet
kubectl -n <ns> rollout restart deploy/<name>
kubectl -n <ns> rollout status deploy/<name> --timeout=180s

# Stuck pod (CrashLoopBackOff on an old template, StatefulSet that will not roll)
kubectl -n <ns> delete pod <pod> --grace-period=0 --force     # the controller recreates it
```

Rules:

- **Vault** is `OnDelete`: restart pods **one at a time, leader last**, waiting for each to be unsealed and caught up. See **Vault recovery**.
- **Traefik, Authentik, Cloudflared** have several replicas; a `rollout restart` is safe but watch `rollout status`.
- A subPath-mounted ConfigMap does not update in a running pod: restart it.
- A secret changed and the pod still has the old value: force the sync (below), then restart the consumer.

A service on an LXC:

```bash
ssh ... ansible@<host> 'sudo systemctl restart <unit> && systemctl is-active <unit>'
# or from the Proxmox host, restart the whole container (last resort)
ssh root@<pve-host> 'pct reboot <ctid>'
```

Common unit names: `AdGuardHome`, `adguardhome-sync`, `keepalived`, `haproxy`, `etcd`, `patroni`, `zabbix-server`, `zabbix-agent2`, `nginx`, `apprise-api` (Hermod) + `caddy`, `factorio` + `factorio-reconcile.timer` + `sftpgo`, `jellyfin`, `n8n` (Gná), `ratatoskr`, `aiops-toolbelt` (Frigg), `vlagent`, `tailscaled`.

---

## 4. Force a secret to refresh (External Secrets)

A new or changed value in Vault takes up to an hour to reach its Kubernetes Secret. Do not wait:

```bash
kubectl annotate -n <ns> externalsecret <name> force-sync=$(date +%s) --overwrite
kubectl -n <ns> get externalsecret <name>                   # STATUS SecretSynced
kubectl -n <ns> rollout restart deploy/<consumer>           # pods read env at start
```

If it shows `SecretSyncedError`, the message names the missing Vault path; one missing path blocks the whole Secret. Read the path in Vault, fix the missing key (see **Secret mirroring** / **Credential rotation**), then force-sync again.

---

## 5. Nudge Flux

```bash
flux reconcile source git flux-system                      # fetch main now
flux reconcile kustomization apps --with-source            # or infrastructure, gateway-config, ...
flux reconcile helmrelease <name> -n <ns>                  # one release
flux reconcile helmrelease <name> -n <ns> --reset          # exhausted retries ("Stalled RetriesExceeded")
flux get helmreleases -A | grep -v "True"                  # anything not Ready
```

Flux applies what is in Git. If the change you want is not in Git yet, **land it** (see **Landing a change**), do not `kubectl apply`.

---

## 6. Cordon, drain, uncordon a worker

Only when a procedure tells you to (rebuilds, patching):

```bash
kubectl cordon einherjar-<node>
kubectl drain einherjar-<node> --ignore-daemonsets --delete-emptydir-data --timeout=300s
# ... work ...
kubectl uncordon einherjar-<node>
```

Expect some pods to stay Pending: workers are close to full on requests, and `local-path` or iSCSI pods are pinned to their node.

---

## 7. Run an Ansible playbook

Only from Frigg (or the Mac for roles without Vault lookups), **one at a time**.

```bash
cd ~/homelab/ansible
# 1. What would change? (read-only)
ansible-playbook -i inventory/hosts.yml playbooks/asgard-<group>.yml --limit <host> --check --diff
# 2. Apply to one host
ansible-playbook -i inventory/hosts.yml playbooks/asgard-<group>.yml --limit <host>
# 3. Only a role
ansible-playbook -i inventory/hosts.yml playbooks/asgard-<group>.yml --limit <host> --tags <tag>
```

Playbook names: `asgard-{k3s,adguard,tailscale,postgres,haproxy-etcd,factorio,hermod,gna,ratatoskr,jellyfin,zabbix,proxmox,pbs,control}.yml`. A fresh LXC's first run is as root (`-e ansible_user=root --tags baseline`); afterwards as `ansible`.

`site.yml` is the whole-fleet converge, used by Semaphore. Do not run it by hand unless told to.

---

## 8. Start a Semaphore job by hand

```bash
# List templates (id and name)
curl -s "$SEMAPHOREUI_API_BASE_URL/project/1/templates" \
  -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[]|"\(.id)\t\(.name)"'

# Start one (e.g. the drift check). Read-only templates are safe; `asgard-apply` mutates the fleet.
curl -s -X POST "$SEMAPHOREUI_API_BASE_URL/project/1/tasks" \
  -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"template_id": <id>}' | jq '{id, status}'

# Follow it
curl -s "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/<task-id>" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r .status
curl -s "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/<task-id>/output" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[].output' | tail -40
```

The queue runs **one task at a time**; a task stuck in `running` blocks everything behind it (see *Monitoring & automation* troubleshooting).

---

## 9. Add a DNS name for an internal service

AdGuard rewrites are Terraform. Never use the AdGuard UI.

```bash
# 1. Branch, then add the name to locals.rewrites in terraform/adguard/rewrites.tf, e.g.
#      "myapp.niflheim.xiiisins.com" = "10.0.20.10"     # Traefik VIP for a K8s-fronted app
# 2. If pods call that name, also add a CoreDNS rewrite (k8s/asgard/infrastructure/coredns-custom/)
# 3. PR, review, merge (see Landing a change), then from the MAIN checkout:
terraform -chdir=terraform/adguard plan
terraform -chdir=terraform/adguard apply

# 4. Verify on all three resolvers, then end to end
for n in 10.0.11.201 10.0.11.202 10.0.11.203; do dig +short @$n myapp.niflheim.xiiisins.com; done
curl -s https://smoketest.niflheim.xiiisins.com/anything         # "smoketest ok" if DNS+Traefik+backend work
```

Sync to the replicas takes up to a minute.

---

## 10. Add a Proxmox guest (LXC or VM)

1. Append the guest to the right Terraform module: `terraform/proxmox/asgard-lxcs/lxcs.tf` (normal) or `terraform/proxmox/asgard-lxcs-root/` (needs device passthrough, FUSE or keyctl).
2. In the **same change**, add the NetBox declarations in `terraform/netbox/vms.tf` (VM, interface, IP; new Ansible groups need a tag in `tags.tf`).
3. Add the host to `ansible/inventory/hosts.yml`, write its role and `asgard-<service>.yml`.
4. Apply, from the main checkout: `terraform -chdir=terraform/proxmox/asgard-lxcs apply`, then `terraform -chdir=terraform/netbox apply -parallelism=2`.
5. Bootstrap: `ansible-playbook -i inventory/hosts.yml playbooks/asgard-<service>.yml -e ansible_user=root --tags baseline --limit <host>`, then the full play as `ansible`.
6. Run the Semaphore `refresh-netbox-inventory` template so scheduled jobs see it.

---

## See also

- **Procedures**: session setup and the index.
- **Troubleshooting**: when something is wrong, not just routine.
- **Landing a change**: the path every repository change takes.
