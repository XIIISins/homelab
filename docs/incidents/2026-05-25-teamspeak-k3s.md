<!-- docs/incidents/2026-05-25-teamspeak-k3s.md -->

# 2026-05-25 — Teamspeak K3s deploy (Phase 5g close)

End-to-end deploy of TS3 server in asgard K3s, closing Phase 5g. Pivoted from planned LXC 1121 on Verd → K3s app. Validated external client connectivity via existing SRV ring on `_ts3._udp.ts3.xiiisins.com`. Four findings surfaced during the deploy — all now in [CLAUDE.md "Known gotchas"](../../CLAUDE.md).

## Outcome

- Vault PG password minted via `terraform/vault/main.tf` (`random_password.teamspeak_postgres`), dual-pathed to `ansible/postgres/teamspeak3-password` (postgres-common) + `k8s/teamspeak/postgres-password` (ESO).
- `teamspeak3@teamspeak3` PG role + DB provisioned on Patroni leader (idunn at the time of apply) via `postgres-common-databases`. TS3's `create_postgresql` plugin auto-bootstrapped 28 tables on first connect.
- StatefulSet `teamspeak/teamspeak` (1 replica) on 5Gi `synology-csi-iscsi-retain` PVC, image `teamspeak:3.13.7`, MetalLB VIP `10.0.20.12` shared between voice+filetransfer Services via `allow-shared-ip` annotation, ClusterIP for ServerQuery.
- DNS SRV ring left as legacy (outside TF) — `hel-ts3.xiiisins.com` already pointed at the KPN public IP; no new records added.
- External client claimed ServerAdmin via privilege key + connected over `ts3.xiiisins.com` SRV → `hel-ts3.xiiisins.com` → KPN public IP → UCG port-forward → MetalLB → pod. End-to-end voice path validated.

## Findings

### 1 — Chown init container needs default capabilities (CAP_CHOWN + CAP_FOWNER)

**Trigger.** Pod stuck in `Init:CrashLoopBackOff`. Init container logs:
```
chown: /var/ts3server/lost+found: Operation not permitted
chown: /var/ts3server: Operation not permitted
```

**Cause.** Initial securityContext on the init container had `capabilities.drop: ["ALL"]` for hardening. Even running as UID 0, dropping `CAP_CHOWN` removes the ability to change ownership of files you don't own, and dropping `CAP_FOWNER` blocks `chmod` across files with mismatched ownership. The PVC's fresh ext4 has `lost+found` owned by root, which the chown was trying to retarget to UID 9987.

**Fix.** Removed `capabilities.drop` from the init container, mirroring the Vault chown-init pattern (`runAsUser: 0` + `runAsNonRoot: false` + default caps). Init container is short-lived (<5s); the hardening surface gain wasn't worth the breakage.

**Generalisation.** Any chown/chmod init container that's running as UID 0 still needs `CAP_CHOWN` + `CAP_FOWNER` in the bounding set. Drop ALL strips them. Either omit the drop entirely (Vault pattern) or explicitly `capabilities.add: ["CHOWN", "FOWNER"]`. Now in CLAUDE.md.

### 2 — StatefulSet RollingUpdate won't replace a CrashLoopBackOff pod

**Trigger.** After committing the chown fix and reconciling Flux, `kubectl get sts` showed `UpdateRevision != CurrentRevision`, but the pod was still on the old (broken) template after several minutes.

**Cause.** StatefulSet's `RollingUpdate` strategy waits for the existing pod to be `Ready` before deleting it for replacement. A pod in `Init:CrashLoopBackOff` is never Ready → the controller waits indefinitely. By design (avoids cascade-deletion thrashing) but creates a deadlock for permanent init failures.

**Fix.** `kubectl delete pod teamspeak-0 --grace-period=0 --force` to break the deadlock. The StatefulSet recreated the pod with the new template within seconds.

**Generalisation.** For any first-deploy or fix-deploy where the running pod is CrashLoopBackOff, the rolling controller won't help. Manual pod-delete is the documented escape hatch. Affects StatefulSets specifically — Deployment's RollingUpdate has different semantics (creates new RS, scales up new + down old in parallel, can proceed past unready pods). Now in CLAUDE.md.

### 3 — Ansible `--tags` doesn't propagate to inner tasks of `include_tasks` (dynamic include)

**Trigger.** `ansible-playbook playbooks/postgres-host.yml --tags postgres-common-databases` ran the include statement but no inner tasks executed (`PLAY RECAP` showed `ok=1` with the include only). Even `--tags patroni-service,postgres-common-databases` only ran the includes — not the inner tasks that actually set `patroni_is_leader` or created the DB.

**Cause.** `include_tasks` is *dynamic* include — at runtime, only the include statement gets the tag. Inner tasks are loaded later and don't inherit the parent's tag. Contrast with `import_tasks` (static), which DOES propagate tags at parse time.

**Fix path.** Combined three workarounds:
1. `--skip-tags baseline,postgres,patroni-install,patroni-config,patroni-adoption,hardening,postgres-common-users` — skip the heavy roles entirely (baseline = full package update + reboot, hardening = SSH lockdown, etc.).
2. `-e '{"patroni_is_leader": true}'` (JSON object, not bare `key=value`) — Ansible's strict-mode boolean conditional rejects `key=value` strings; JSON gets a real boolean. Surfaced as `Conditional result (True) was derived from value of type 'str'` error.
3. `-e '{"postgres_databases":[{...teamspeak3 only...}]}'` — override the full list with just our new entry, so the pre-existing `zabbix` entry (no Vault path yet) doesn't fail the loop.

**Generalisation.** Two distinct gotchas, both now in CLAUDE.md:
- (a) For surgical re-runs of a role embedded in a multi-role play, `--tags` doesn't compose well with `include_tasks`. Use `--skip-tags` against the heavy roles + accept that all of the target role's tasks run.
- (b) Ansible strict-mode boolean conditionals need JSON `-e '{"key": true}'`, not `-e key=true`. The latter silently produces a string and trips the type checker.

### 4 — MetalLB IPAddressPool static/dynamic collision

**Trigger.** Initial design picked `loadBalancerIP: 10.0.20.11` for the teamspeak Services. Just before apply, noticed `VAULT_ADDR=http://10.0.20.11:8200` — Vault UI's `serviceType: LoadBalancer` (no static `loadBalancerIP`) had MetalLB-auto-allocated `.11`. Static request would collide.

**Cause.** MetalLB allocates dynamic IPs from the bottom of the pool. `.10` is statically pinned by Traefik; `.11` is dynamically allocated to Vault UI. Any new static neighbor in that band races with dynamic growth.

**Fix.** Moved Teamspeak to `10.0.20.12` (adjacent, statically claimed). MetalLB's static-beats-dynamic precedence protects this allocation. Comment in `service-voice.yaml` documents the rationale.

**Generalisation.** Longer-term fix is to **pin Vault UI to a static IP and carve the pool into static (`.10-.49`) + dynamic (`.50-.99`) bands** via two MetalLB `IPAddressPool` resources. Deferred until more LB Services land — currently just Traefik + Vault + Teamspeak. Now in CLAUDE.md as a deferred gotcha; open-questions entry tracks the carve-out.

## Process notes

- Two of the four findings (chown caps + StatefulSet deadlock) only surface on first-deploy. Re-deploys to the same StatefulSet/PVC don't reproduce. Test discipline: assume init containers will fail at least once on PVC-first-deploy.
- The Ansible-tags class is structural (third time it's surfaced in this repo — `tasks_from:` silently ignored in `roles:` block was the earlier one). Worth standardising a "surgical postgres re-run" wrapper that does the `--skip-tags` + leader-detect + JSON-bool plumbing for the operator.
- Pre-existing `zabbix` Vault path absence (Phase 5h not yet started) blocked the postgres-common-databases full-list run. List-override workaround unblocked Teamspeak but the underlying gap remains — Zabbix PG password should be minted in TF before Phase 5h kicks off.
