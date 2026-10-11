<!-- docs/outline/troubleshooting/applications.md -->

# Applications

Symptoms in the services people actually use. Start with the layer underneath if more than one app is broken (DNS, Postgres, Vault, the cluster); come here when one app misbehaves. Namespaces equal app names (`outline`, `immich`, `netbox`, `teamspeak`, `media`, `microbin`, `startpage`, `overview`, `semaphore`, `authentik`, `garage`).

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
app() { kubectl -n "$1" get pods,svc,pvc 2>&1 | head -30; kubectl -n "$1" get events --sort-by=.lastTimestamp 2>&1 | tail -8; }     # app <namespace>: one-shot overview
```

For any Kubernetes app: `app <ns>`, then `kubectl -n <ns> logs deploy/<name> --tail=60` and `kubectl -n <ns> describe pod <pod> | tail -25`.

---

## 1. Jellyfin (LXC 1123 on Urd, `10.0.11.223`)

```bash
curl -s -m 5 http://10.0.11.223:8096/health ; echo                                       # "Healthy"
PVE() { ssh -o ConnectTimeout=5 "root@$1.niflheim.xiiisins.com" "${@:2}"; }
PVE urd 'pct status 1123; pct exec 1123 -- systemctl is-active jellyfin media.mount jellyfin-qsv-smoke.timer'
PVE urd 'pct exec 1123 -- bash -c "mount | grep \" /media \"; ls -l /dev/dri; id jellyfin"'     # /media must be nfs ro; renderD128 group igpu (gid 2001); jellyfin in igpu
PVE urd 'pct exec 1123 -- journalctl -u jellyfin -n 40 --no-pager'
```

| Symptom | Cause | Fix |
|---|---|---|
| `https://jellyfin.midgard…` returns 502 | The LXC (or the service) is down; Traefik only fronts it | Start the container/service; confirm via `jellyfin-direct.niflheim…:8096` |
| Playback buffers / CPU pegged on transcode | QuickSync not in use (device missing/permissions) | `ls -l /dev/dri` in the container; `jellyfin` must be in group `igpu`; re-run the Ansible play `asgard-jellyfin.yml`; check Zabbix "QuickSync transcode self-test failing" |
| Zabbix: "QSV smoke failing" | GPU device lost, group membership lost, driver issue | `pct exec 1123 -- systemctl start jellyfin-qsv-smoke.service && pct exec 1123 -- journalctl -u jellyfin-qsv-smoke -n 20`; on the host check `dmesg \| grep -i "i915.*hang"`; fix the group via the play |
| HDR/Dolby Vision transcodes die instantly (`Failed to get number of OpenCL platforms`) | No OpenCL runtime on Debian 13 | Install the upstream Intel OpenCL packages via the role; SDR is unaffected |
| `jellyfin.service` will not start: `Dependency failed … media.mount` | The NFS media mount is missing (by design: it refuses to start on an empty library) | Check Munin (`showmount -e 10.0.254.20`), fix the mount, `systemctl start media.mount jellyfin` |
| Service aborts (SIGABRT) right after a **restore** | The restored cache volume is empty and root-owned | The role's tmpfiles rule fixes ownership; re-run the play or `chown -R jellyfin: /var/cache/jellyfin` |
| `/media` is read/write | Someone edited fstab out of band (it must be `ro`) | Re-run `--tags jellyfin:media`; never leave it `rw` |
| Library shows 0 episodes after an import | Sonarr's Jellyfin connection path map is missing (`/data` → `/media`) | Section 2 |
| Slow migration / restore | The 40 GB cache volume copies whole (`pct migrate 1123 <node> --restart` ≈ 9 min each way) | Plan the outage |

Never put `/var/lib/jellyfin` (SQLite) on NFS. The app database is backed up by PBS; the cache mount is excluded.

## 2. Media automation (Sonarr, SABnzbd, Recyclarr; namespace `media`)

```bash
app media
kubectl -n media logs deploy/sonarr --tail=40 ; kubectl -n media logs deploy/sabnzbd --tail=40
kubectl -n media get cronjob recyclarr; kubectl -n media get jobs | tail -3
# API access (Sonarr is behind ForwardAuth; go straight to the service with its key from Vault)
kubectl -n media port-forward svc/sonarr 8989:8989 >/dev/null 2>&1 & sleep 2
curl -s localhost:8989/api/v3/queue -H "X-Api-Key: $(vault kv get -field=api_key secret/k8s/media/sonarr)" | jq '.records[]|{id,title,status,trackedDownloadState}'
kill %1
```

| Symptom | Cause | Fix |
|---|---|---|
| UI redirects to login forever / 404 on `/outpost.goauthentik.io` | The proxy provider is not attached to the embedded outpost | **Identity & edge** §2 |
| SABnzbd crash-loops, jobs `Paused` at 90 to 95% with `__ADMIN__/… missing` | OOM kill at a low memory limit (needs `cache_limit = 256M`, memory limit 2 Gi) | Remove the corrupt jobs from the queue/history; raise limits in Git; the init container re-asserts `cache_limit`/`bandwidth_max` on each start |
| Downloads complete but files have holes/CRC errors | `pp` was unset so SABnzbd did not repair/unpack | Categories must have `pp = 3` (done); replace damaged files: move the file to `/data/_quarantine/`, delete the episode file via the API, search again |
| Imports stall: "No files found are eligible for import" | A single-episode release grabbed as a season pack left phantom queue entries | Delete them with `blocklist=true&skipRedownload=true`, then `POST /command {"name":"EpisodeSearch","episodeIds":[…]}` |
| A wrong-show or wrong-season release was imported | Loose indexer search; auto re-download matched by absolute number | Check the queue after any mass search; remove with blocklist; the profile's minimum custom-format score (100) rejects most junk |
| Removing a queue item did not start a new search | Sonarr does not always re-search | Trigger `EpisodeSearch` yourself; SubsPlease uses absolute numbering, so do not filter by season |
| Jellyfin shows nothing after an import | `mapFrom=/data`, `mapTo=/media` missing in Sonarr's Jellyfin connection; or the connection hostname does not resolve from the pod | Use the container IP `10.0.11.223` for the connection (pods may not resolve `*.niflheim` LXC names reliably) |
| Recyclarr job fails | New config dir env/readonly mount (`RECYCLARR_CONFIG_DIR`, writable `/config`) or you tested before Flux applied the fix | `flux reconcile kustomization apps --with-source`, confirm the CronJob spec, then `kubectl -n media create job --from=cronjob/recyclarr recyclarr-now` |
| Sonarr lost its indexers/download client | They live in its database, not Git; a lost `sonarr-config` volume | Re-add them (keys from Vault `secret/k8s/media/…`); Recyclarr restores profile/naming |
| Usenet connection problems | Connection limits (10 to 20 recommended) / SSL port 563 | Check `secret/k8s/media/usenet` and the SABnzbd server settings |

## 3. Teamspeak (`teamspeak` namespace; voice UDP 9987, files TCP 30033)

```bash
app teamspeak
kubectl -n teamspeak get svc -o wide                                      # LoadBalancer 10.0.20.12 (pinned, shared IP for UDP + TCP)
timeout 3 bash -c '</dev/tcp/10.0.20.12/30033' && echo "30033 open" || echo "30033 closed"
nc -vzu -w3 10.0.20.12 9987                                                # UDP probe (inconclusive on silence)
dig +short SRV _ts3._udp.ts3.xiiisins.com                                  # priority 1 hel-ts3 (home), priority 99 do-ts3 (offsite)
```

| Symptom | Cause | Fix |
|---|---|---|
| Nobody can connect from outside | The UCG port-forwards (UDP 9987 and TCP 30033 → `10.0.20.12`) were lost or the home IP changed | Check the UCG port-forwards (manual, not in IaC) and that `hel-ts3.xiiisins.com` resolves to the current home IP (Cloudflare record, Terraform `terraform/cloudflare/ts3.tf`) |
| Connects but file transfers fail | TCP 30033 not forwarded | Same |
| Server pod restarts/Pending | Postgres VIP, the `data-teamspeak-0` NFS volume, or the MetalLB VIP | `app teamspeak`; **Vault & Postgres** B1; **Storage & iSCSI** §10 |
| Bans hit the wrong address | Source IP not preserved | The Service must be `externalTrafficPolicy: Local`; the VLAN 20 policy routing must be intact (**DNS & networking** §6) |
| Homelab down: do clients fall over? | Yes, to `do-ts3` (priority 99) | Check `curl`/UDP at `do1.xiiisins.com`; the offsite server has its own database, wiping it is acceptable |
| Server queries needed | ServerQuery is not forwarded (10011/10022) | `kubectl -n teamspeak port-forward sts/teamspeak 10011:10011` |

## 4. Factorio (LXC 1120, `10.0.11.220`)

```bash
$SSH ansible@10.0.11.220 'systemctl is-active factorio factorio-reconcile.timer sftpgo; cat /factorio/status/factorio-status.json; sudo tail -20 /factorio/logs/reconcile.log; cat /factorio/control/factorio-control.json'
```

The operator edits `control/factorio-control.json` (`version`, `state`, `restart`) over SFTP; a root timer converges it every 30 s and writes the status file. If the game is not in the state they asked for, **read the status file and reconcile log first**; the reconciler tells you what it did.

| Symptom | Cause | Fix |
|---|---|---|
| Stuck "installing" | Download/checksum failure, or no internet | `reconcile.log`; the Factorio API and `factorio.com` must be reachable from the LXC |
| Server will not start after a manual install | The install directory is not owned by `factorio` (a `.lock` from a prior run) | `chown -R factorio:factorio /opt/factorio-<version>`; the reconciler does this itself |
| SFTP login fails | SFTPGo defender banned the operator (15 failures / 30 min) or the service is down | `systemctl status sftpgo`; wait or clear the ban in SFTPGo |
| `apt update` breaks a play on this host | An old SFTPGo apt mirror entry | SFTPGo is installed from the pinned GitHub `.deb`; remove the stale `sources.list.d/sftpgo.list` |
| Game traffic not reaching it | UCG UDP 34197 forward | Check the UCG; `factorio.xiiisins.com` resolves straight to the LXC on the LAN |

## 5. Immich (`immich` namespace)

```bash
app immich
kubectl -n immich logs deploy/immich-server --tail=60 2>/dev/null || kubectl -n immich get deploy
kubectl -n immich get helmrelease 2>/dev/null; flux get hr -n immich
```

| Symptom | Cause | Fix |
|---|---|---|
| Server crash-loops: `permission denied to create extension "earthdistance"` | Extensions not pre-created | **Vault & Postgres** B2 (`postgres_databases[].extensions`: pgvector, `cube` then `earthdistance`) |
| Upgrade failed and the rollback crash-loops (`previously executed migration … is missing`) | The migrated schema is newer than the rolled-back image | Roll **forward**: `flux reconcile helmrelease immich -n immich --reset` |
| Login with SSO does nothing | OAuth is activated by hand in Immich's admin settings (not declarable) | Admin → Settings → OAuth with the issuer/client from Vault `secret/k8s/immich/oidc`; redirect URIs include `app.immich:///oauth-callback` for mobile |
| Photos missing / library errors | The NFS library volume | **Storage & iSCSI** §10; it is a **secondary copy** by design: no PBS, single NAS |
| Machine-learning pod Pending | Its cache is `local-path` (RWO) | Worker down: **Storage & iSCSI** §9 |

## 6. Outline / the wiki (`outline` namespace) and Garage

```bash
app outline
kubectl -n outline get externalsecret                                      # outline-app and outline-redis-secret
kubectl -n outline logs deploy/outline --tail=60
# Garage (S3). Admin API is ClusterIP only: port-forward, never expose.
kubectl -n garage port-forward svc/garage-admin 3903:3903 >/dev/null 2>&1 & sleep 2
curl -s localhost:3903/v2/GetClusterHealth -H "Authorization: Bearer $(vault kv get -field=admin_token secret/k8s/garage/server)" | jq '.status'     # "healthy"
kill %1
```

| Symptom | Cause | Fix |
|---|---|---|
| Pod `CreateContainerConfigError` | `outline-app` is waiting on a Vault path (S3 or OIDC not provisioned yet): informative, not a bug | `kubectl -n outline describe externalsecret outline-app`; apply `terraform/garage` and `terraform/authentik`, force-sync |
| Boot error `SequelizeConnectionError: self-signed certificate` | Node's `require` = `verify-full` | The DB URL needs `?sslmode=no-verify` |
| `ImagePullBackOff` / 403 | The GHCR image path needs sponsorship | Use Docker Hub `outlinewiki/outline` (pinned) |
| Rollout hangs, two replicas | Migrations run in the entrypoint; `Recreate` strategy is deliberate | Do not switch to `RollingUpdate` |
| Image `runAsNonRoot` error with `nodejs` | Named image user | Numeric `runAsUser: 1001` |
| Uploads fail / attachments 500 | Garage unhealthy or the S3 key rotated | Health above; `secret/k8s/outline/s3`; force-sync and restart |
| Garage pod stuck `0/1` after a fresh deploy | Layout not assigned yet; the layout-init Job needs the headless service to publish not-ready addresses | `kubectl -n garage get jobs`; `kubectl -n garage logs job/layout-init`; first format of the iSCSI metadata LUN can take minutes |
| Garage has no shell | Distroless image | Use the admin API or a debug pod; `kubectl exec … sh` always fails |
| Public wiki 404 | Cloudflared rule/DNS record | **Identity & edge** §4 |
| Source Markdown changed but the wiki did not | Pages come from `docs/outline/` via the sync script and are matched by title | Run the sync (see its README); a page edited only in Outline is not reverted until its source changes |

## 7. MicroBin (`microbin` namespace; `paste.xiiisins.com`)

```bash
app microbin
kubectl -n microbin logs deploy/microbin --tail=40
curl -s -o /dev/null -w '%{http_code}\n' https://paste.xiiisins.com/
```

- **Every POST returns 502 though the pod looks healthy:** the upstream bug (JSON-DB expiry panic poisons the paste mutex). The patched image (`ghcr.io/xiiisins/microbin-homelab`) fixes it; `kubectl -n microbin rollout restart deploy/microbin` clears a poisoned process; if it recurs, confirm the Deployment still runs the patched image.
- First deploy crashloops on `pasta_data/database.json`: the JSON DB path is hard-coded relative to the working directory and ignores `MICROBIN_DATA_DIR`; the volume must mount at `pasta_data`.
- `/list` or `/admin` open to everyone, or the login loops: the partial-auth gating needs the `/list`, `/admin` **and** `/outpost.goauthentik.io` routes through ForwardAuth.

## 8. NetBox (`netbox` namespace)

```bash
app netbox
kubectl -n netbox logs deploy/netbox -c netbox --tail=60
curl -s -o /dev/null -w '%{http_code}\n' https://netbox.niflheim.xiiisins.com/api/status/
```

| Symptom | Cause | Fix |
|---|---|---|
| Pod OOMKilled / 503 for a minute during `terraform plan/apply` | The state refresh hammers the API | Use `-parallelism=1`; the memory floor is 1.5 GiB for the server |
| Terraform: `invalid character 'B'…` / `TextConsumer` / `[500] OperationalError: the connection is closed` | A 503/500 blip mid-refresh | Retry once; use `-parallelism=1` |
| Every API call: `Invalid v1 token` | `API_TOKEN_PEPPERS` missing in the app secret | The `netbox-app` ExternalSecret must carry `api_token_peppers` |
| Scheduled Ansible runs report `skipping: no hosts matched` | Cold inventory cache plus a NetBox 500 | **Monitoring & automation** §4 |
| New OIDC user can log in but do nothing | NetBox permissions are not synced from groups | Log in as the local superuser and elevate the user (Active/Staff/Superuser) |
| Fresh install loops in migrations / OOM at 768 Mi | The migration phase needs more memory | Keep at least the medium preset |
| `VM create fails: could not locate referenced tag "ansible:<group>"` | The tag is missing from `terraform/netbox/tags.tf` | Add the tag in the same change as the VM |
| Perpetual `custom_fields` diff on a VM with no VMID | A DigitalOcean node with no value | Declare the 9900-range VMID |

## 9. Startpage, status page, WebFinger (`startpage`, `overview`, `apex-static`)

```bash
app startpage; kubectl -n startpage logs deploy/startpage -c $(kubectl -n startpage get deploy startpage -o jsonpath='{.spec.template.spec.initContainers[0].name}') --tail=20
app overview; curl -s -o /dev/null -w '%{http_code}\n' https://overview.niflheim.xiiisins.com/healthz
curl -s "https://xiiisins.com/.well-known/webfinger?resource=acct:test@xiiisins.com" | head -c 200; echo       # WebFinger (Tailscale OIDC discovery depends on it)
```

- **Startpage pods fail at start:** the init container clones the private repo over SSH with a read-only deploy key; check its log for auth errors (key rotated/removed in GitHub, or the Vault secret). To publish a change: push to the startpage repo, then `kubectl -n startpage rollout restart deploy/startpage`.
- **Status page numbers blank:** VictoriaMetrics did not answer (the page says so itself and recovers on the next 30 s poll); check `kubectl -n monitoring get pods`. **No Proxmox slide:** the read-only token is absent or invalid; pods read it once at start: after the Secret appears, `kubectl -n overview rollout restart deploy/overview`.
- **WebFinger broken:** lives in `apex-static` (separate from the startpage on purpose); `kubectl -n apex-static get pods`; an ExternalSecret/ConfigMap change needs a pod restart for subPath mounts.

---

## See also

- **Services and purpose**: what each service is.
- **Storage & iSCSI**, **Vault & Postgres**, **Identity & edge** (this section): the layers underneath.
- **Everyday operations** (Procedures): restart and reconcile commands.
