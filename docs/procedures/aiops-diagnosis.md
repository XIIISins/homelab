<!-- docs/procedures/aiops-diagnosis.md -->

# AIOps diagnosis agent (Gná / n8n) — deploy, operate, verify

*Phase 10d. Design and rationale: [`operations/10d-diagnosis-chatops.md`](../operations/10d-diagnosis-chatops.md). Role: [`ansible/roles/n8n-agent`](../../ansible/roles/n8n-agent/README.md). Gotchas: [`known-issues/n8n-aiops.md`](../known-issues/n8n-aiops.md).*

**State of play (10d1, 2026-10-02):** applied and reboot-tested (first play `ok=92 changed=52`; both re-runs `changed=0` with n8n/Caddy not restarted; sandbox exposure 1.5 OK; Caddy matrix verified from Hugin). Still to do: the real-Discord plumbing test and the independence test (needs the 10d2 Zabbix media type). The host, the ingest listener and a **stub** workflow exist: an authenticated POST becomes a `#diagnoses` forum thread that says "no analysis yet" and echoes the alert. The Zabbix media type, the Toolbelt API and the LLM agent come in the next 10d steps. Nothing here can act on the fleet.

## Shape

| Piece | Where |
|---|---|
| Host | Gná, LXC **1121**, Urd, `10.0.11.221`, `gna.niflheim.xiiisins.com` (VLAN 11) |
| n8n | `127.0.0.1:5678`, native systemd, unprivileged `n8n` user, SQLite in `/var/lib/n8n` |
| Ingest | Caddy `:8081` -> `/webhook/*` only, source-IP allow-list (`group_vars/n8n_agent.yml`); everything else 404, other sources 403 |
| Auth | `X-AIOPS-Token` header per source; token = Vault `secret/ansible/aiops/n8n-ingest-token/<source>` |
| Editor | **Not exposed.** SSH tunnel only: `ssh -L 5678:127.0.0.1:5678 ansible@gna`, then `http://127.0.0.1:5678`; login `aiops@niflheim.xiiisins.com` + Vault `secret/ansible/aiops/n8n-owner-password` |
| Workflows | `aiops/n8n/workflows/*.json` in git. **Git is the source of truth**: the role re-imports on change and overwrites editor edits |

## Secrets

| Vault path (`secret/…`) | Field | Minted by |
|---|---|---|
| `ansible/aiops/n8n-encryption-key` | `value` | Terraform (`terraform/vault`) |
| `ansible/aiops/n8n-owner-password` | `value` | Terraform |
| `ansible/aiops/n8n-ingest-token/zabbix` | `value` | Terraform (one per source) |
| `ansible/aiops/discord-diagnosis` | `url` | **Operator** (forum channel webhook) |
| `ansible/aiops/anthropic-api-key` | `key` | **Operator** (used from 10d3; not read by the role yet) |

The operator mirrors every one of these to 1Password (offline-mirror rule). **The n8n encryption key is the one that matters most:** PBS backs up the SQLite DB, not the key; losing the key makes every credential stored in n8n unreadable (re-import is possible — credentials are rebuilt from Vault — but only if Vault is intact).

## Deploy (first time)

Order matters; every `terraform apply` runs from the **main checkout** after the PR is merged (repo rule), `-parallelism=2` for NetBox.

1. `terraform/proxmox/asgard-lxcs` — creates LXC 1121 (`terraform plan` shows only the new container + `random_password.gna_root`).
2. `terraform/vault` — mints the encryption key, owner password and the Zabbix ingest token.
3. `terraform/netbox` (`-parallelism=2`) — `aiops-agent` role, VM `gna`, interface/IP.
4. `terraform/adguard` — `gna.niflheim.xiiisins.com`.
5. Confirm the two operator secrets exist (presence + length only, never print):
   `vault kv get -field=url secret/ansible/aiops/discord-diagnosis | wc -c`.
6. **macOS control node: refresh the NetBox snapshot first** (`refresh-netbox-inventory`), otherwise the new host does not exist for Ansible ([`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md)). Bootstrap, then the full play (hardening locks root SSH out at the end):
   ```
   ansible-playbook playbooks/asgard-gna.yml -e 'ansible_user=root' --tags baseline
   ansible-playbook playbooks/asgard-gna.yml
   ```
7. **Reboot-test** Gná, then re-run the play: it must report no changes and must **not** restart n8n (the import is checksum-gated).

## Verify (read-only unless stated)

```bash
# on Gná
systemctl is-active n8n caddy                         # active active
journalctl -u n8n --since -10min | grep -E 'Activated workflow|Owner was set up'
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:5678/webhook/aiops/zabbix -d '{}'   # 403 (route registered, auth enforced, nothing posted)
# from an allow-listed source (Hugin) and from anywhere else
curl -s -o /dev/null -w '%{http_code}\n' http://gna.niflheim.xiiisins.com:8081/healthz              # 404 from Hugin, 403 from elsewhere
```

**Plumbing test (posts ONE thread into `#diagnoses`)** — from the workstation through the SSH tunnel (the workstation is deliberately not in the Caddy allow-list, and Hugin has no Vault CLI), token read in the shell so it never lands in a transcript:

```bash
ssh -f -N -L 5678:127.0.0.1:5678 ansible@gna      # then, with vault-homelab-env loaded:
curl -s -X POST http://127.0.0.1:5678/webhook/aiops/zabbix \
  -H "X-AIOPS-Token: $(vault kv get -field=value secret/ansible/aiops/n8n-ingest-token/zabbix)" \
  -H 'content-type: application/json' \
  -d '{"source":"zabbix","status":"PROBLEM","severity":"High","host":"canary-1","trigger_name":"plumbing test","event_id":"0"}'
# expect {"message":"Workflow was started"} immediately, and a "[High] canary-1 - plumbing test (PROBLEM)" thread in #diagnoses
```

This exercises n8n (auth, workflow, Discord) but not Caddy; Caddy's matrix is covered by the checks above and, end to end, by the first real Zabbix send in 10d2.

## Independence test (gate before ANY real source is cut over)

The analysis path must never affect the notify path, in either direction.

1. `systemctl stop n8n` on Gná. Trigger a Zabbix test alert: the Discord alert (via Hermod) still arrives, and the Zabbix action log shows **only the n8n operation failed**.
2. `systemctl start n8n`. No backlog flood; the next alert reaches both.
3. Stop Hermod's AppriseAPI instead: n8n still receives and posts to `#diagnoses`.

Record the result in the 10d1 incident/retro note.

## Operate

| Task | How |
|---|---|
| Change a workflow | Edit `aiops/n8n/workflows/*.json` (the editor can be used to *draft*: export, copy into git, never leave it only in the DB) -> PR (CI lints it) -> merge -> `ansible-playbook playbooks/asgard-gna.yml --tags n8n` |
| Detect editor drift | `n8n export:workflow --id=<id>` on Gná vs the file in git; a diff means someone edited in the UI |
| Add an alert source | Add to `terraform/vault` `local.n8n_ingest_sources` **and** role `n8n_ingest_sources`; add a workflow + credential name `aiops-ingest-<source>`; add the source's CIDR to `caddy_sites`; apply Terraform, then the play (CI fails if the three disagree) |
| Rotate an ingest token | `terraform apply -replace='random_password.n8n_ingest_token["zabbix"]'` (main checkout), re-run the play (credential re-imported), update the producer's copy |
| Rotate the Discord webhook | Operator re-mints in Discord, writes Vault, re-run the play with `-e n8n_import_workflows=false --tags n8n:config` (env file changes -> restart) |
| Upgrade n8n / Node | Bump `n8n_version` / `n8n_node_version`+`n8n_node_sha256` in the role defaults, check the n8n `engines.node` range, run the play; back up first (PBS) — n8n migrates its DB on start |
| Disable the agent | `systemctl stop n8n` (or disable the producer's n8n media type). Notifications are unaffected |

## Egress (intended, not yet enforced)

Gná should reach only Discord, the Anthropic API (from 10d3), the Toolbelt API on Frigg and the usual infra (DNS, apt, Vault). That is a UCG firewall rule and is **not** in this change — tracked in [`open-questions.md`](../operations/open-questions.md). Until then the controls are: loopback-only editor, no `executeCommand`/SSH/file nodes (`NODES_EXCLUDE` + CI), workflows reviewed in PRs, no read credentials held by n8n.

## Rollback

Disable the producer's n8n send (10d2+), `systemctl stop n8n`. To remove the host: `terraform destroy -target=proxmox_virtual_environment_container.gna` (main checkout), then the NetBox/AGH/Vault entries. The Discord path never changed.
