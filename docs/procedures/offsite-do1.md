<!-- docs/procedures/offsite-do1.md -->

# Procedure — build, validate and cut over the offsite node `do1`

*Phase 10a ([`aiops-roadmap.md`](../operations/aiops-roadmap.md) §10a). Code: `terraform/digitalocean/`, `terraform/tailscale/`, `terraform/cloudflare/ts3.tf`, `terraform/netbox/offsite.tf`, `ansible/playbooks/do1.yml`. Gotchas: [`digitalocean.md`](../known-issues/digitalocean.md).*

The new node is built **beside** the legacy droplet. Nothing below touches the legacy droplet, its firewalls or any live DNS answer until step 9 (cutover). Terraform applies run from the **main checkout** only; one `ansible-playbook` at a time.

## Phase A — build (no live impact)

1. **Mint the scoped DO token** (console only; scopes listed in `terraform/digitalocean/provider.tf`) and seed it: `vault kv patch secret/ansible/frigg/iac-env digitalocean_token="$DO_TOKEN"` (set `DO_TOKEN` interactively; never inline). Then `vault-homelab-env --refresh`; `test -n "$DIGITALOCEAN_TOKEN"`.
2. **`terraform/digitalocean`** — add `terraform.tfvars` (`ssh_public_keys`, see the `.example`), `terraform plan` (expect **7 to add**: project, 2 SSH keys, droplet, reserved IP, assignment, firewall), apply. Note `reserved_ip` and `droplet_ipv4` outputs.
3. **`terraform/tailscale`** — plan (expect: ACL policy in-place update — new `tag:offsite` + 3 grants replacing `* → *`; 1 new `tailscale_tailnet_key.lxc["do1"]`; 1 new Vault secret `ansible/tailscale/authkeys/do1`), apply. The ACL change is tailnet-wide: check from the operator laptop and Frigg that nothing lost reach (`tailscale ping`, Frigg SSH) straight after.
4. **`terraform/cloudflare`** — add `hel_ts3_ip`, `offsite_ip` (= **legacy** droplet IP, so apply is a no-op) and `do1_next_ip` (= new reserved IP) to `terraform.tfvars`; plan (expect **5 to import, 1 to add** (`do1-next`), 0 to change), apply.
5. **`terraform/netbox`** — plan under the netbox TF lock (needs step 2's state), expect 6 adds (site, role, tag, VM, interface, IP), apply.
6. **Operator: PlantNet allowlist** — add the new reserved IP at my.plantnet.org. Keep the legacy IP (dual-IP window).
7. **Ansible** (from a checkout where the HeyLeaf repo exists at `offsite_plantnet_local_repo`; Vault env loaded):
   `ansible-playbook playbooks/do1.yml -e ansible_user=root --tags baseline --check --diff` then for real (`-e ansible_host=<reserved ip>` if `do1-next` DNS has not propagated), then `ansible-playbook playbooks/do1.yml -e do1_reserved_ip=<reserved ip> --check --diff`, then for real. The play asserts the host is named `do1` and that egress comes from the reserved IP.
8. **Restore TS3** (before first real use): stop the stack, replace `/opt/do1/ts3-data/ts3server.sqlitedb` with the integrity-checked dump (`sqlite3 <db> 'PRAGMA integrity_check;'` → `ok`; dump taken via the SQLite backup API because the live DB is WAL-mode), `chown -R 9987:9987 /opt/do1/ts3-data`, start the stack. **The dump named in the roadmap (`~/do1-ts3-dump/` on Frigg) was not found on 2026-10-01 — re-take it (or locate it) first.**

## Phase B — validate (still no cutover)

See the checklist in [`services/teamspeak.md`](../services/teamspeak.md) ("Offsite failover (do1)") and the PlantNet checks: `curl https://do1-next.xiiisins.com/health` (valid LE cert, 200), a real `identify` POST via `do1-next` (PlantNet 200, not 403), `curl -4 https://api.ipify.org` **on do1** equals the reserved IP, firewall probe from an independent vantage (a burst droplet, never the home network), and the reboot test (CLAUDE.md persistence rule): reboot, then confirm default route / DOCKER-USER rules / containers / tailnet tag all return unaided.

## Phase B2 — outside watcher (10b3)

The Gatus watcher (`gatus` role in `do1.yml`) and Frigg's heartbeat have their own gated procedure: [`offsite-watcher.md`](offsite-watcher.md). It needs two operator-seeded Vault secrets before the `gatus` role will run (`--skip-tags gatus` until then).

## Phase C — cutover (operator-gated)

9. Cutover PR: flip `do1_serve_heyleaf: true` in `group_vars/offsite.yml` and re-run the `caddy` tag; set `offsite_ip` in `terraform/cloudflare/terraform.tfvars` to the new reserved IP and apply (moves `do-ts3` + `do1`); **operator** changes `plantnet.heyleaf.app` in its separate Cloudflare zone. Stop the homelab TS3 briefly and connect a TS3 client through the SRV fallback.
10. Soak ~7 days, then cleanup: destroy the legacy droplet, the powered-off `do-tailscale-p01`, both stale firewalls, the unused `startpage` registry and stale SSH keys; delete the `do1-pre-hardening-2026-10-01` snapshot; remove `do1_next_ip` + the `do1-next` record; switch `hosts.yml` `ansible_host` to `do1.xiiisins.com`; **revoke the old broad DO token**.
