<!-- docs/procedures/offsite-watcher.md -->

# Procedure — the outside watcher (Gatus on `do1`): deploy, verify, operate

*Phase 10b3 ([`aiops-roadmap.md`](../plans/active/aiops-roadmap.md) §10b3, decision D1). Code: `ansible/roles/gatus/`, `ansible/roles/gatus-heartbeat/` (Frigg side), `ansible/playbooks/do1.yml` (`gatus` role), `ansible/playbooks/gatus-heartbeat.yml`, `ansible/playbooks/infra-health-check.yml` (check #9), probe lists in `ansible/inventory/group_vars/offsite.yml`. Gotchas: [`digitalocean.md`](../known-issues/digitalocean.md), [`observability.md`](../known-issues/observability.md), [`tailscale.md`](../known-issues/tailscale.md).*

## What it is

[Gatus](https://github.com/TwiN/gatus) v5.37.0 runs on `do1` as an **unprivileged systemd service** (`gatus` user, hardened unit, `MemoryMax=192M`; measure `MemoryCurrent` after deploy against the ~100 MB budget), SQLite history in `/var/lib/gatus`, config rendered from git. It sits **outside** the homelab so it keeps working when the homelab is down, and alerts through its **own Discord webhook** (never Hermod, which lives in the thing being watched).

| Duty | How |
|------|-----|
| Public endpoints | `xiiisins.com` WebFinger (body check), `home.`, `paste.`, and `do1`'s own `/health`; each also asserts latency and **TLS expiry > 7 d** |
| Tailnet reachability | TCP connect to Frigg `:22` and Hermod `:80` (grants already in `policy.hujson`; TCP, not HTTP, because Hermod's Caddy 403s non-allowlisted sources) |
| Dead-man's switch | external endpoint `homelab_frigg-heartbeat`; Frigg POSTs every 60 s; Gatus records a miss per 300 s of silence; alert after 3 misses = **15 min grace** (must exceed `do1`'s ~2-3 min unattended-upgrade reboot at 04:30; the role asserts this) |
| Metrics | `/metrics` on the same tailnet-only listener (scrape wiring is a follow-up, see below) |

The single listener (UI + `/metrics` + push API) binds **only** `100.102.131.126:8080` (do1's tailnet address). It is not reachable from the internet (DO firewall and bind address), and a `gatus-container-guard` unit drops container-bridge traffic to it (containers reach the host address via `INPUT`, which the `DOCKER-USER` rule does not see).

## Gates (in order)

1. **Merge the PR.** The `infra-health-check` change reaches Semaphore only via `main`.
2. **Seed the secrets (operator, interactive shell; values never in tool calls, chat or git).** Needs a Vault token that can write `secret/ansible/*` (`homelab-admin` is read-only: use the same route you used for `secret/ansible/do1/plantnet`). fish:
   ```fish
   # Discord: create the webhook yourself (alert channel -> Edit Channel ->
   # Integrations -> Webhooks -> New Webhook -> Copy URL). Use a channel that is
   # NOT fed by Hermod, so the "independent path" is visibly independent.
   read -s -P 'Discord webhook URL: ' WEBHOOK
   vault kv put secret/ansible/do1/gatus-discord webhook_url="$WEBHOOK"
   set -e WEBHOOK
   # Heartbeat token: random, >= 32 chars; Gatus (do1) and Frigg read the same one.
   set TOKEN (openssl rand -hex 32)
   vault kv put secret/ansible/do1/gatus-heartbeat token="$TOKEN"
   set -e TOKEN
   # Verify without printing: expect 64 (piped, vault kv get -field adds no newline).
   vault kv get -field=token secret/ansible/do1/gatus-heartbeat | wc -c
   ```
   Then mirror both into 1Password with `scripts/secrets/vault-1p-mirror` ([procedure](secret-mirroring.md); the map already has them). The roles **fail early with these instructions** if either secret is absent or malformed (`do1.yml --skip-tags gatus` runs the rest of the play meanwhile).
3. **Tailscale ACL: no change needed.** Existing grants cover everything: `tag:offsite -> tag:server tcp:22`, `tag:offsite -> 10.0.11.22 tcp:80`, and `tag:server -> *` (Frigg's heartbeat reaches `do1:8080`). Confirm the Hermod grant is applied (`terraform plan` in `terraform/tailscale` shows no diff).
4. **Converge `do1`** (one `ansible-playbook` at a time; Vault env loaded):
   ```
   ansible-playbook playbooks/do1.yml --check --diff
   ansible-playbook playbooks/do1.yml
   ```
   This (a) re-runs `tailscale up --accept-routes` (the homelab `10.0.0.0/16` becomes routable so the Hermod probe works; the ACL still allows only the two grants) and (b) extends the `DOCKER-USER` drop to `10.0.0.0/16`, (c) installs and starts Gatus. Check-mode artifacts: on a fresh run the `gatus` user/units do not exist yet, so owner/unit tasks and the install shell step are skipped or reported spuriously; the real run is the proof.
5. **Start Frigg's heartbeat** (needs step 4 first so the endpoint exists):
   ```
   ansible-playbook playbooks/gatus-heartbeat.yml --check --diff
   ansible-playbook playbooks/gatus-heartbeat.yml
   ```
6. **Reboot test** (persistence rule): reboot `do1`; confirm `gatus` and `gatus-container-guard` come back unaided, the listener is on the tailnet address, and **no false dead-man alert fires** through the 04:30-style reboot (the 15 min grace covers it). Frigg's heartbeat keeps pushing; its failed pushes during the reboot only show in its journal.

## Verify the deployment

On `do1` (`ssh ansible@do1-next.xiiisins.com`):
```
systemctl is-active gatus gatus-container-guard          # active active
ps -o user= -C gatus                                      # gatus (never root)
ss -ltnp | grep ':8080'                                   # ONLY 100.102.131.126:8080
sudo systemd-analyze security gatus | tail -3             # exposure score, record it
systemctl show gatus -p MemoryCurrent -p MemoryMax        # record it (budget ~100 MB), 192M cap
sudo ls -l /etc/gatus /var/lib/gatus                      # gatus.env 0600 gatus:gatus
sudo iptables -S INPUT | grep 8080                        # the container DROP rule
```
From Frigg:
```
curl -s http://100.102.131.126:8080/health                # {"status":"UP"}
sudo systemctl start gatus-heartbeat.service && systemctl status gatus-heartbeat.service --no-pager | head -5
curl -s http://100.102.131.126:8080/api/v1/endpoints/statuses | python3 -m json.tool | grep -E '"key"|"success"' | head -30
```
Every endpoint `success: true`, including `homelab_frigg-heartbeat`. From an independent vantage (not the home network) `nc -zv 129.212.223.26 8080` must **fail**.

## Exit criterion 1 — blocking Frigg's heartbeat alerts through the independent path

1. Note the time (T0). On Frigg, drop the heartbeat at the network level (exercises the real failure path rather than just stopping the timer):
   ```
   sudo iptables -I OUTPUT -d 100.102.131.126 -p tcp --dport 8080 -j DROP
   ```
2. Expect a Discord message in the Gatus channel ("No heartbeat from Frigg ...") **within ~15-25 min** (3 missed 300 s intervals, plus up to one interval of phase; the first real test on 2026-10-01 alerted ~23 min after the block). Record T0 and the alert time.
3. **Prove it was the independent path:** Hermod's Caddy log shows no POST in that window (`ssh hermod sudo tail /var/log/caddy/access.log`), the message arrived in the channel of the seeded webhook, and `grep -ci hermod /etc/gatus/config.yaml` on `do1` is 0.
4. Restore: `sudo iptables -D OUTPUT -d 100.102.131.126 -p tcp --dport 8080 -j DROP`. A "resolved" message follows after 2 good pushes (~2 min). Confirm `iptables -S OUTPUT` on Frigg no longer has the rule.

## Exit criterion 2 — `do1`'s own death is alerted from the homelab side

The Semaphore prober (`infra-health-check`, cron */12 h, **after the PR is merged**) probes `https://do1-next.xiiisins.com/health` (3 tries) and posts an `alert` (Hermod `#infra-alerts`) if it fails.
1. `ssh ansible@do1-next.xiiisins.com 'sudo systemctl stop caddy'` (reversible; takes the proxy and `/health` down without touching the box otherwise).
2. Run the `infra-health-check` template from the Semaphore UI. Expect "do1 (offsite node) UNHEALTHY" in `#infra-alerts`.
3. `sudo systemctl start caddy`; re-run; the finding is gone.

**Known limit:** the prober cadence makes this the slow path (<= 12 h). While `do1` is dead nothing homelab-side pages faster; Frigg's own heartbeat failures are only journal lines (`journalctl -u gatus-heartbeat`, shipped to VictoriaLogs). The fast path is an open follow-up ([`open-questions.md`](../operations/open-questions.md)).

## Operate

- **Rotate the heartbeat token:** write a new value to `secret/ansible/do1/gatus-heartbeat`, run `do1.yml --tags gatus` then `gatus-heartbeat.yml` (a few minutes of mismatch is inside the grace). Rotate the Discord webhook the same way (`--tags gatus`).
- **Bump Gatus:** change the three pins in `roles/gatus/defaults/main.yml` together (recipe in that file: new tag -> linux/amd64 manifest digest -> first layer digest -> sha256 of the extracted binary), `do1.yml --tags gatus --check --diff`, apply. The versioned binary and symlink make rollback a pin revert.
- **Add a probe:** edit `gatus_public_endpoints` / `gatus_tcp_endpoints` in `group_vars/offsite.yml`; apply with `--tags gatus`.
- **Split it out** to its own droplet if it needs Docker, outgrows ~100 MB, needs a public status page, or makes `do1` hard to patch: put the new host in a play with the `gatus` role, set `gatus_listen_address`, point `gatus_heartbeat_watcher_host` at it.
