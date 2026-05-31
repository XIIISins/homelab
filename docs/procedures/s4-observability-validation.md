<!-- docs/procedures/s4-observability-validation.md -->

# Wave S4 — observability deploy + validation runbook

*Companion to [`docs/operations/1.0-stabilization.md`](../operations/1.0-stabilization.md) Wave S4. This runbook is the operator-gated deploy + the synthetic-failure validation that satisfies the wave's done-criterion ("one synthetic failure per item → expected Hermod alert").*

> **✅ Deployed + validated 2026-05-31.** All steps below were executed: Vault scaffolds + CF/PBS tokens minted, `terraform/semaphore` template + every-12h schedule applied, Zabbix disk macros pushed + read back. Validation: clean baseline silent from the Semaphore pod (task 123); synthetic run from the pod (task 124) delivered **2 critical + 3 warning** to Hermod. The `community.crypto` "first-run risk" below is **resolved** — the pod has it. Three deploy-surfaced fixes are committed (`zabbix_globalmacro macro_type: text`; etcd `content | from_json`; prober `become: false`). The only remaining **operator-optional** test is the live disk-trigger-fire (§ validation matrix). The deploy sequence is kept below as the rebuild-from-scratch reference.

## What S4 shipped

The five S4 items are closed by **two mechanisms**, deliberately leaning on proven patterns over the never-before-used `community.zabbix.zabbix_trigger`/`zabbix_item` modules (a dozen module-shape gotchas warned against blind-authoring there):

| S4 item | Mechanism | File |
|---|---|---|
| Cloudflare token validity | active prober (Semaphore playbook) | `ansible/playbooks/infra-health-check.yml` |
| cert-manager cert-expiry | active prober — served-cert probe ×3 zones | same |
| Patroni quorum-loss | active prober — REST `/cluster` (defence-in-depth for the `on_role_change` callback) | same |
| etcd quorum | active prober — `/health` per DCS node | same |
| PBS backup-failure | active prober — `/api2/json/.../tasks?errors=1` (official Zabbix PBS template needs 7.2; we run 7.0 LTS) | same |
| Disk-usage thresholds | Zabbix global macros + per-class override | `group_vars/all/zabbix.yml`, `playbooks/zabbix-host-groups.yml`, `group_vars/postgres.yml` |

The prober runs `hosts: localhost` inside the Semaphore pod every 12h (`45 6,18 * * *`), POSTs findings to Hermod (`critical`→Hrist, `alert`→Mist), and is **silent on a clean run**. A play-level rescue POSTs a `critical` if the prober itself errors, so a broken health check is never silent.

> **⚠️ Deviation from the literal S4 decision (operator sign-off needed).** PBS was chosen to be monitored via the stock "Proxmox Backup Server by HTTP" Zabbix template — but that template requires **Zabbix 7.2+** and we run **7.0 LTS** (deliberate). Rather than upgrade off LTS or take a community-template dependency, PBS backup-failure detection rides the prober (same observability→Hermod path, not the rejected PBS-native-webhook). If you'd prefer the Zabbix-native route, the alternatives are: (a) import a community PBS template via `zabbix_template`, or (b) plan a 7.0→7.2 upgrade. Say the word and I'll swap it.

## Deploy sequence (operator-gated)

**0. Prereq — Semaphore pod has the prober's collections.** The prober uses `community.crypto.get_certificate` + `community.hashi_vault`. Confirm the Semaphore image ships `community.crypto` (the pod-start `pip install` covers *python* deps, not Galaxy collections). If absent, add `ansible-galaxy collection install -r ansible/requirements.yml` to the StatefulSet `command:` wrapper alongside the existing pip install, or bake into the image. **Verify first** — this is the most likely first-run failure.

**1. Galaxy collection pins (S5, harmless to do here too).**
```
ansible-galaxy collection install -r ansible/requirements.yml --force
```
on the control node (and ensure the Semaphore pod resolves the same set).

**2. Terraform applies** (from main checkout, after ff-merge):
```
# provider pin bumps — no resource changes expected, just re-resolve the lock
cd terraform/vault          && terraform init -upgrade && terraform plan   # expect: 2 to add (CF + PBS placeholders), 0 to change beyond provider
cd terraform/proxmox/zabbix-access && terraform init -upgrade && terraform plan  # expect: no changes (pin only)
cd terraform/semaphore      && terraform plan   # expect: 1 template + 1 schedule to add
# apply each after reviewing plan
```
Remember `terraform/vault/` and `zabbix-access/` need a valid `VAULT_TOKEN`; re-mint if the session is old.

**3. One-time secrets the operator mints** (machine-at-runtime → Vault):
```
# Cloudflare token — mirror the 1P value (the verify endpoint validates the token used to call it)
vault kv put secret/ansible/cloudflare/api-token \
  value="$(op read 'op://Homelab 2.0/<cloudflare-token-item>/credential')"

# PBS monitoring token — on the PBS LXC as root:
proxmox-backup-manager user create zabbix@pbs
proxmox-backup-manager user generate-token zabbix@pbs monitoring   # prints the secret ONCE
proxmox-backup-manager acl update / Audit --auth-id 'zabbix@pbs!monitoring'
# then stash the printed secret:
vault kv put secret/ansible/zabbix/pbs-token value='<token-secret>'
```

**4. Push the disk-usage macros** (one-at-a-time ansible). The global macros + postgres override land via the zabbix bootstrap + agent playbooks:
```
ansible-playbook ansible/playbooks/zabbix-host-groups.yml        # global macros
ansible-playbook ansible/playbooks/zabbix-agent.yml --limit postgres   # per-class override
```
(or the relevant Semaphore template). `--tags zabbix-agent:macros` scopes to just the global-macro task if iterating.

**5. First run.** Trigger `infra-health-check` manually in Semaphore. A healthy fleet → **no Hermod message** + a green task with the debug summary `0 critical, 0 warning`.

## Validation matrix (the S4 done-criterion)

Each row produces the expected Hermod alert *non-destructively* where possible. Destructive cluster tests (kill quorum) are staged at the bottom for an operator-present window — per the "non-destructive only, autonomously" decision.

| Item | Non-destructive synthetic failure | Expected alert | Cleanup |
|---|---|---|---|
| **CF token** | `vault kv put secret/ansible/cloudflare/api-token value=bogus` → run prober | `critical` (Hrist): "Cloudflare API token INVALID" | restore real token |
| **cert-expiry** | run prober with `-e cert_warn_days=9999` (every real cert is <9999d out) | `alert` (Mist): cert expires for all 3 zones | none (override is per-run) |
| **PBS** | `vault kv put secret/ansible/zabbix/pbs-token value=bogus` → run prober | `critical` (Hrist): "PBS auth FAILED" | restore real token |
| **disk thresholds** | temporarily set the postgres `{$VFS.FS.PUSED.MAX.CRIT}` macro to `5` (every FS exceeds 5%) in Zabbix → wait for the trigger | Zabbix "disk critically low" (Average) → Hermod via the existing media-type | restore to `85` (or re-run the agent playbook) |
| **Patroni quorum** | *negative test:* healthy cluster → prober reports no Patroni finding | (no alert — confirms no false positive) | — |
| **etcd quorum** | *negative test:* healthy → no finding | (no alert) | — |

**Destructive positive tests (operator-present window only):**
- **Patroni quorum:** `sudo patronictl -c /etc/patroni/patroni.yml pause` then stop PG on 2 of 3 nodes (or `systemctl mask patroni && pkill -9 -f patroni` per the HAProxy/keepalived gotcha). Run the prober → expect `critical` "Patroni cluster degraded: ... running_members=1/3". **Recovery:** unmask + start patroni on the stopped nodes, `patronictl resume`, confirm `patronictl list` shows 3 streaming + a leader.
- **etcd quorum:** stop etcd on 2 of 3 DCS nodes → prober → expect `critical` "etcd quorum LOST: 1/3". **Recovery:** start etcd, confirm `etcdctl endpoint health` green on all three. ⚠️ etcd quorum loss also stalls Patroni elections — keep the window short and do this *after* the Patroni test, not concurrently.

## Notes / known first-run risks

- **`get_certificate` reachability:** the prober probes `netbox.niflheim` / `authentik.midgard` / `wiki` (apex) — all three have CoreDNS `rewrite name exact` entries → Traefik ClusterIP, so the Semaphore pod reaches Traefik directly (not the MetalLB VIP it can't reach). If a future cert zone is added, pick a served FQDN that has a CoreDNS rewrite, or the probe will hit the unreachable VIP.
- **PBS API shape:** `tasks?errors=1&since=<epoch>` + the `PBSAPIToken=<id>:<secret>` auth header are authored to the documented PBS API; if the first run 401s with a valid token, check the token-id format (`user@pbs!tokenname`) and that the `Audit` ACL was granted. The prober treats 401/403 as a `critical` finding (not a silent pass), so a misconfig surfaces loudly.
- **Patroni `/cluster` member states:** the prober counts `running` + `streaming` as healthy and `leader`/`master`/`primary` as the leader role (covers the Patroni 3.x→4.x `master`→`primary` rename). If a future Patroni version renames states again, widen the `selectattr` lists.
