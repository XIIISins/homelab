<!-- docs/incidents/2026-09-03-frigg-approle-silent-expiry.md -->

# 2026-09-03 — Frigg's `ansible-frigg` AppRole SecretID silently expired; `rotate-approle` never covered it

## Summary

Owner asked to extend `rotate-approle` to cover `ansible-frigg`. Investigating turned up that it was never wired in at all — `$__homelab_approle_items` only ever listed `ansible-local` (`ansible-awx` stubbed commented-out) — and that gap had already caused a live outage: `ansible-frigg`'s SecretID, minted once at Stage 2 bootstrap (2026-05-31, 90-day `secret_id_ttl`), expired around 2026-08-29 with nothing watching it. `vault list auth/approle/role/ansible-frigg/secret-id` came back empty (Vault's approle backend purges expired SecretIDs on lookup) and Frigg's `homelab-env` had been dead — `Vault AppRole login failed (check SecretID validity)` — for about 5 days before anyone noticed, because nothing on Frigg had needed it in that window.

Fixed same-session: minted a fresh SecretID, created the missing 1P mirror item (`[Asgard] - Ansible - Vault - AppRole (ansible-frigg)`, matching `ansible-local`'s field layout), wired `ansible-frigg` into `$__homelab_approle_items` in both `homelab.sh` and `homelab.fish`, re-seeded Frigg via `-e control_node_frigg_secret_id=...`, and confirmed `homelab-env` works on Frigg again.

## Trigger

Owner: "Fix the rotate-approle function in the homelab scripts so I can also rotate ansible-frigg's stuff."

## Sequence

1. Read `rotate-approle` (`homelab.sh`) — confirmed only `ansible-local` is in the role table; no 1P item UUID exists anywhere in the repo for `ansible-frigg`.
2. Asked the owner whether a 1P mirror item already existed (couldn't check — `op` needs interactive/biometric signin, unavailable in this background session). Owner: none exists; create one. Owner ran `op signin` via `!` so the session's `op` calls could proceed.
3. Checked Vault directly: `vault list auth/approle/role/ansible-frigg/secret-id` → `No value found`. Zero live SecretIDs for the role — not "no 1P copy," actually gone. Cross-checked `ansible-local` + `ansible-awx`: both have live accessors, so this is `ansible-frigg`-specific.
4. Confirmed the outage directly: SSH'd to Frigg, ran `. /usr/local/bin/homelab-env && homelab-env` as `ghost` → `Vault AppRole login failed (check SecretID validity)`.
5. Minted a fresh SecretID (`vault write -f auth/approle/role/ansible-frigg/secret-id`, new expiry 2026-12-02), created the 1P item, wrote the RoleID/SecretID/accessor to a job-tmp file that was never echoed to any tool call or chat output, then deleted it once consumed.
6. Wired `ansible-frigg|${__op_ansible_vault_frigg}` into `$__homelab_approle_items` in `.config/scripts/homelab.sh` AND `.config/fish/conf.d/homelab.fish` (the fish file is the operator's interactive twin, easy to miss). Made `rotate-approle`'s post-revoke "Next:" message role-aware — `ansible-frigg` doesn't read 1P, so its follow-up is `ansible-playbook ... --tags control-node:vault-env -e control_node_frigg_secret_id=<id> -l frigg`, not `homelab-env --refresh`.
7. Re-seeded Frigg via that exact playbook invocation, confirmed `changed=1` on the `Seed Vault AppRole env file` task, then re-verified `homelab-env` on Frigg succeeds (`Loaded Frigg homelab env from Vault ...`) and its Vault token's `display_name` reads `approle`.

## Findings (encoded as gotchas)

1. **`ansible-frigg`'s SecretID has no rotation reminder and no 1P mirror by design** (`terraform/vault/frigg.tf`: "minted manually... never in TF state"). A 90-day TTL with nobody watching it is a silent-expiry trap — `ansible-local` hit almost the identical failure mode once already (see the 2026-05-31 `rotate-approle`/32d-clamp entry in `open-questions.md`), and this is the same class recurring on a role that was never brought into the rotation tooling at all. → [`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md).
2. **Vault's approle backend purges expired SecretIDs from `list` entirely** — an expired-and-gone role shows the same `No value found` as a role that was never minted. Don't read that as "not yet bootstrapped" without checking whether the consumer (Frigg) is actually failing.
3. **Two shim files must move together.** `$__homelab_approle_items` (and any `__op_*` UUID var) lives in both `.config/scripts/homelab.sh` (bash, Claude's path) and `.config/fish/conf.d/homelab.fish` (the operator's interactive path) — a fix landed in only one silently leaves the other stale.
4. **`ansible-frigg` doesn't fit `rotate-approle`'s 1P-centric "paste into 1P, then revoke" flow** — its live credential is a file on Frigg (`/etc/frigg/vault-approle.env`), not something a human reads from 1P and pastes anywhere. The function's post-revoke instructions are now role-conditional: `ansible-local`/future 1P-backed roles get the original `homelab-env --refresh` guidance; `ansible-frigg` gets the `ansible-playbook -e control_node_frigg_secret_id=...` re-seed command instead.
5. **`op signin` inside a background session's Bash tool needs a follow-up call to actually take effect** — the first `op whoami` after the owner's `! op signin` still reported not-signed-in; a second call worked. Don't treat one failed `op whoami` as proof signin didn't land when the owner just ran it.

## What's still open

- No alerting on AppRole SecretID expiry for any role (`ansible-local`, `ansible-frigg`, `ansible-awx`) — this was caught only because the owner happened to ask about rotation. A Zabbix/Semaphore check against `secret-id-accessor/lookup` expiration dates, or just a calendar reminder at T-14d, would close this class permanently. Not built here — flagged as a follow-up, not silently deferred.
