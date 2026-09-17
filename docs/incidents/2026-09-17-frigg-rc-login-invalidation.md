<!-- docs/incidents/2026-09-17-frigg-rc-login-invalidation.md -->

# 2026-09-17 — `claude-remote-control` crash-looped past `StartLimitBurst`, sat `failed` ~7.5h undetected

## Summary

Third occurrence of the recurring RC-login-invalidation failure mode (previously 2026-08-02, 2026-08-05 — both documented only as a comment in `claude-remote-control.service.j2`, never given their own incident writeup or any monitoring). This time the service crashed with `Error: You must be logged in to use Remote Control.` five times in ~70s starting 07:51:19 CEST, hit `StartLimitIntervalSec=300`/`StartLimitBurst=5`, and the unit went to `failed` — `Restart=always` never gets a chance to fire once systemd gives up on the burst limit. It then sat dead for **~7.5 hours** (07:52:19 → 15:18:33 CEST) with nothing watching it; the owner noticed independently and manually restarted it (re-ran `claude auth login` + `systemctl restart claude-remote-control`). Confirmed via `journalctl -u claude-remote-control` on Frigg — no automated recovery or alert fired in that window.

The 06:15 UTC drift-check run (Semaphore task 1668, mid-outage) correctly reported `changed=1` on `control-node : Start claude-remote-control service` — but that's a `--check`-mode side effect of catching the unit down, not itself a fix or an alert route anyone monitors proactively.

Fixed same-session: added a new check (#7) to `infra-health-check.yml` that reads `claude-remote-control.service`'s state via `service_facts` on Frigg and POSTs a critical finding to Hermod if it isn't `running`, so the next occurrence pages instead of going silent.

## Trigger

Owner, reviewing a routine drift-check recap: "I think I fixed frigg, but claude keeps logging out." — asked to check what drifted.

## Sequence

1. Pulled the full Semaphore task-1668 log via the REST API (`GET /tasks/1668/output`) and grepped for `changed:` entries on frigg/hermod/saga.
2. frigg's only changed task was `control-node : Start claude-remote-control service (only once the full-scope login exists)` — `ansible.builtin.systemd: state: started` is idempotent, so `changed=1` in `--check` mode means the service was NOT active at 12:15 UTC.
3. SSH'd to Frigg directly (`ansible@frigg.niflheim.xiiisins.com`) and pulled `journalctl -u claude-remote-control --since '-12 hours'`. Found the crash-loop: `Error: You must be logged in to use Remote Control.` × 5 between 07:51:19–07:52:19 CEST, then `Failed with result 'exit-code'` and no further `Started` line until 15:18:33 CEST (the owner's manual fix, confirmed — no Semaphore task ran in that window).
4. Confirmed no monitoring covers this: grepped `zabbix-agent` role and `infra-health-check.yml` for `claude-remote-control` — nothing. Same blind-spot class as the 2026-09-03 `ansible-frigg` AppRole silent-expiry incident.
5. Also found, same drift-check run: saga's `adguardhome-sync.yaml` had a stale AGH admin password (Vault rotated, not yet re-applied — benign, pending a real `asgard-apply`) and hermod's Caddy had drifted `2.11.3` (pin) → `2.11.4` (apt/unattended-upgrades installed newer). Both reported to the owner; caddy pin bumped to `2.11.4` per owner's call (verified `2.11.4` is still Cloudsmith's current candidate before accepting).
6. **Secret leak, self-caught mid-session:** displaying saga's rendered-config diff (to show the pending AGH password change) printed both the *old* and *new* AdGuard admin passwords in cleartext into the tool-call transcript. Flagged to the owner immediately per the "never echo secrets" recovery procedure; recommended rotating the AGH admin password since both values are now exposed in-session. Not auto-rotated — owner's call.
7. Added check #7 to `infra-health-check.yml`: a new play (`hosts: control`, `ignore_unreachable: true`) gathers `claude-remote-control.service` state via `ansible.builtin.service_facts` (read-only), consumed via `hostvars` in the existing `localhost` prober play, which already owns the Hermod-POST plumbing.
8. First draft compared `service_facts`' `state` field against `'active'` — wrong. Verified via `ansible-doc` + module source + a live ad-hoc run against Frigg (`ansible frigg -m service_facts`) that this module only ever reports `state` as `"running"`/`"stopped"`; `"failed"`/`"masked"`/`"not-found"` land in the separate `status` field, and only survive there when the unit is *currently* bad (a healthy unit's `status` gets overwritten with its systemd *enablement* string, e.g. `"enabled"` — confusingly not an activation state at all). Fixed the check to compare `state != 'running'`.
9. Syntax-checked the playbook (`ansible-playbook -i inventory/hosts.yml --syntax-check`) and re-verified the live ad-hoc probe against the fixed logic.

## Findings (encoded as gotchas / fixes)

1. **`StartLimitBurst` exhaustion on the RC login-invalidation failure mode is now its third occurrence and its first with real downtime measured (~7.5h)** — the `.service` template's own comment documented 2026-08-02 and 2026-08-05 but neither got an incident writeup or a monitoring fix; this is the first time it did. → [`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md).
2. **Fixed: `infra-health-check.yml` check #7** — `claude-remote-control.service` state on Frigg, read via `service_facts`, critical-POSTed to Hermod when not `running`. Closes the same blind-spot class as check #6 (AppRole expiry).
3. **`ansible.builtin.service_facts`'s `state` field is binary (`running`/`stopped`) only** — a currently-`failed` unit reports `state: stopped`, not `state: failed`. The `status` field carries the real bad-state string (`failed`/`masked`/`not-found`) but ONLY while the unit is actually in one of those states; for anything else `status` gets silently overwritten with the unit's *enablement* string (`enabled`/`disabled`/`static`), which looks like an activation state but isn't. Don't gate a health check on `status == 'active'` — that value never appears. Gate on `state != 'running'`.
4. **A rendered-secrets diff (`--diff`/`-vv` style output in a drift-check log) is exactly as leak-prone as a `cat`'d credentials file** — same class as the 2026-09-03 transcript-secret-leak incident, different vector (task-log diff instead of a cache-write side effect). Displaying "what changed" for a config file that embeds a live password means displaying the password. → recommend rotating the AdGuard admin password (owner to action).

## What's still open

- The AdGuard admin password exposed in this session's transcript has not been rotated as of this writing — flagged to the owner, not auto-actioned.
- Saga's `adguardhome-sync.yaml` still has the stale (pre-rotation) AGH admin password; needs a real (non-`--check`) `asgard-apply` to pick up the current Vault value. Owner deferred ("I'll do that later").
- No broader audit was done for *other* rendered-secret diffs that might surface the same way (any templated file with a Vault-sourced credential, shown via `--diff` in a drift-check log). Not in scope this session; worth a pass if this pattern recurs.
