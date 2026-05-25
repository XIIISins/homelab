<!-- docs/incidents/2026-05-25-tfstate-s3-migration.md -->

# 2026-05-25 — Terraform state migration to S3 + secret-leak recovery

## Summary

Migrated 8 Terraform modules from local state to S3 backend (`xiiisins-homelab-tfstate`, eu-west-1, native 1.10+ locking via `use_lockfile = true`). `terraform/aws/` (bucket bootstrap) deliberately stays local-state by chicken-egg necessity. Migration was technically clean; one redaction-discipline finding triggered a NetBox admin API token leak + operator-initiated rotation.

## Trigger

User question: "how do we migrate all tfstate's to s3?" — the `terraform/proxmox/asgard-lxcs-root/` split (separate session) had already established the need for a real backend (separate state files, per-module locking). This session executed the migration of the remaining 8 modules.

## Sequence

1. **Pattern established on `terraform/cloudflare/` first** (user explicit: "yeah do cloudflare first"). Backend block added to `versions.tf`, `required_version` bumped to `>= 1.10.0`, `terraform init -migrate-state` → confirm `yes` → `terraform plan` (clean) → stash local state to `~/tmp/stale/tfstate/cloudflare/`.

2. **Batch migration of remaining 7** (user explicit: "yes batch except aws. chicken-egg situation"): adguard, authentik, netbox, proxmox/asgard-k3s, proxmox/asgard-lxcs, tailscale, vault. Each followed the cloudflare pattern. `aws/` skipped.

3. **NetBox plan errored "Invalid token"** — provider needed `NETBOX_API_TOKEN`, never wired into `homelab-env`. Resolved by minting an admin API token via NetBox web UI (chart's `superuser.api_token` value is documentation-only — see CLAUDE.md "NetBox" gotcha), storing in 1P "Asgard - NetBox - Admin API token", and adding to the shim's `__homelab_env_map`.

4. **Secret leak during cache verification.** Verifying that `homelab-env --refresh` populated `NETBOX_API_TOKEN` in both cache files, I ran an inline-redaction awk against `export NETBOX_API_TOKEN=` AND `set -gx NETBOX_API_TOKEN`. Regex matched the bash form (`=value`) and rewrote it to `<REDACTED>`. **Did NOT match the fish form** (`set -gx NETBOX_API_TOKEN 'value'` — space, no `=`), so the fish line printed verbatim to the transcript. Operator-initiated rotation: minted new token, updated 1P, deleted leaked token in NetBox UI. Verified rotation: cache refreshed (length 57 matched 1P len-1-for-newline), terraform plan against rotated token succeeded.

5. **Post-rotation plan transient.** First plan after rotation errored with NetBox 500 `the connection is closed / OperationalError` — NetBox→Postgres pool blip on first request after idle. Second plan: clean, 4 to add / 3 to change / 0 to destroy (pre-existing module drift, unrelated to migration).

## Findings (encoded as CLAUDE.md gotchas)

### Terraform / state

- **`terraform init -migrate-state` is incompatible with `-input=false`.** Migration confirmation is interactive ("Do you want to copy existing state…"); `-input=false` aborts with "Can't ask approval for state migration when interactive input is disabled." Fix: drop `-input=false` for migration init only.
- **Multi-module state migration pattern.** Backend block + version bump + `init -migrate-state` + plan to verify + stash local state files (don't delete).

### Shell / tooling

- **`Bash` tool calls don't inherit operator's interactive shell PATH.** `/opt/homebrew/bin` absent in non-interactive shell; `op`/`terraform`/`vault` not-found. Fix: prefix command with `PATH="/opt/homebrew/bin:$PATH"` or use absolute paths.
- **Pipe-induced subshell hides env exports.** `cmd-that-exports | tail` runs the export in a subshell — exports never reach parent. Verify cache via the file directly, not via inspecting parent env vars.
- **Inline redaction must handle bash AND fish syntax — or skip redaction entirely.** `homelab-env` writes both formats; `KEY=value` regex misses `set -gx KEY 'value'`. **Rule:** use length-only checks (`wc -c`, `${#var}`); never try to inline-redact a secret in stream output.

## Root cause — redaction-regex miss

The redaction regex was written against the bash format I had front-of-mind ("export NAME=value") and not validated against the fish format simultaneously printed. The CLAUDE.md "Never echo secrets" rule covers `$(op read ...)` inline-substitution (which is the correct primary discipline), but didn't cover the secondary "verifying that secrets correctly landed in a cache file" case. The new rule (length-only, never inline-redact in stream) closes that secondary gap.

The BSD-sed `\s` gotcha (CLAUDE.md Shell/tooling — surfaced 2026-05-25 morning during 5b.2) is the **same class** but at a different layer: that one was about regex semantics differing across `sed` implementations; this one is about regex coverage differing across cache-file syntaxes. Both are "you THINK redaction is applying — it isn't." Length-checks dodge both.

## Process — what worked

- **User-initiated rotation, not auto-rotate** — per CLAUDE.md "Recovery if a credential leaked in-session: flag it to the owner and recommend rotation — don't auto-rotate without an explicit ask, that call is theirs." User said "rotated." → verified via length-match + plan → done.
- **Length-only verification post-rotation** — no risk of re-leak. `wc -c` on the 1P token + `${#cache_token}` on the cache value, matched with the trailing-newline adjustment. Took 3 lines of bash, no awk, no sed.
- **Verification script in `$CLAUDE_JOB_DIR/`** instead of inline nested `bash -c '...'` — sidestepped zsh quoting tangles + made the redaction-discipline explicit in the file.

## Status

- 8 modules on S3 ✅ (cloudflare, adguard, authentik, netbox, proxmox/asgard-k3s, proxmox/asgard-lxcs, tailscale, vault)
- aws/ stays local ✅ (chicken-egg)
- NetBox token rotated + 1P updated ✅
- `NETBOX_API_TOKEN` + `NETBOX_SERVER_URL` + `ADGUARD_USERNAME` + `ADGUARD_PASSWORD` + `AWS_DEFAULT_REGION` wired into `homelab-env` (both bash + fish shim files)
- Three new gotchas in CLAUDE.md (Shell/tooling: bash subshell PATH, pipe-subshell, dual-syntax redaction; Terraform/state: migrate-state vs input=false, multi-module pattern)
- Decisions row "Terraform state backend — S3 with native locking, key-per-module" added; "Terraform state storage (initial)" row marked superseded

## Carried forward

- 4 add / 3 change drift in `terraform/netbox/` is pre-existing module work, unrelated to the migration. Not investigated; deferred until operator schedules the next netbox apply.
- `terraform/proxmox/asgard-lxcs-root/` plan still needs `PROXMOX_VE_PASSWORD` — already documented as a follow-up in open-questions row "Split Tailscale resources out of…" (the `set-proxmox-password` shim function exists but is manually-called per the decision row's sub-decision 3).
