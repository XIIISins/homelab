<!-- docs/incidents/2026-05-24-5i3-tf-netbox-retrofit.md -->

# 5i.3 — TF→NetBox standing pattern retrofit (2026-05-24)

**Phase:** 5i.3
**Duration:** Single session (~half a day).
**Outcome:** ✅ Closed. `terraform/netbox/` module manages ~160 records via `e-breuninger/netbox v5.3.0` + `import {}` blocks. Three NetBox-side placement errors corrected as part of the retrofit.

---

## Trigger

5i closed with the TF→NetBox direction decided (Option A: TF writes NetBox via `e-breuninger/netbox`) but implementation deferred to 5i.3. Owner: "next step, tf -> netbox, import current objects from netbox into tf." Plan: 7 per-class checkpoint commits (module skeleton, then site/cluster/manufacturers/roles, VLANs/prefixes, devices, VMs, VIPs, tags) + Stage 8 post-flight.

## What surfaced

### Stage 0 (insertion before Stage 1) — chart deploy gap

Bootstrap blocked: the chart-stashed admin `api_token` from the K8s `netbox-superuser` secret was rejected by NetBox API with `{"detail":"Invalid v1 token"}`. Investigation revealed two stacked issues in chart 8.2.17:

1. **`API_TOKEN_PEPPERS` was never configured.** NetBox 4.4+ added pepper-hashed v2 tokens; without peppers configured at startup, all tokens are unusable. The chart auto-generates the pepper in its `<release>-config` Secret ONLY when no `existingSecret` is set. Our `existingSecret: netbox-app` short-circuited that path, leaving the key empty. **Fix:** Vault mint at `secret/k8s/netbox/api-token-peppers` + add `api_token_peppers` projection to the `netbox-app` ExternalSecret. Commit `b576bd2`.
2. **The chart's `superuser.api_token` value is NEVER written to the DB as a Token row.** `super_user.py` requires BOTH `superuser_api_token` AND `superuser_api_key` files mounted, but the chart only mounts `superuser_api_token` (no `api_key` mount in the deployment template). AND super_user.py exits early if the admin user already exists, so retroactive token-creation via restart is impossible. **Fix:** mint admin token via Django shell post-pepper-fix; stash in 1P "Asgard - NetBox - admin API token".

After both fixes, the v2 admin token authenticates: `Authorization: Bearer nbt_<key>.<token>` (provider auto-detects via the `nbt_` prefix even when sent under the legacy `Token` keyword).

### Stage 1 — auth model pivot

Originally-planned dedicated `terraform` NetBox user + token resources dropped after three e-breuninger/netbox v5.3.0 incompatibilities surfaced:

1. **`netbox_token` POST + PUT pattern is rejected by NetBox 4.4+.** Provider unconditionally PUTs the full resource after POST; NetBox 4.4+ rejects setting the token plaintext on existing tokens with `{"error":"Cannot assign a new plaintext value for an existing token."}`. Token gets created but the apply errors during the post-create update, tainting the resource.
2. **The v2 token secret is unavailable in TF state.** Provider's schema has only a `key` (12-char public) field; the full v2 secret (`nbt_<key>.<token>`) is only in the POST response and there's nowhere to put it. Even if the create-then-update fight is resolved, we couldn't capture the secret for Vault.
3. **`netbox_user` has no `is_superuser` field.** The TF-created user can only have `staff=true` (a NetBox 4.6 no-op anyway since `is_staff` was removed from the User model). Without `is_superuser`, the user has zero permissions and can't manage anything.

Pivot: drop the dedicated user, use the admin token throughout. Provider reads from `NETBOX_API_TOKEN` env var (sourced from 1P via the operator's homelab.sh). Decision row added to [decisions.md](../operations/decisions.md): "TF→NetBox auth model — admin token, no dedicated `terraform` user".

### Stage 5 — placement corrections + VMID custom field

Three VMs were placed on wrong hosts in NetBox vs IaC reality:

- **PBS**: NetBox said verd, IaC says skuld → corrected.
- **idunn**: NetBox said skuld, IaC says verd → corrected.
- **Gjallarbru**: NetBox said verd, IaC says skuld → corrected.

These were data-entry errors in the manual 5i.e UI import. The 5i.3 retrofit's HCL matches IaC truth (`terraform/proxmox/*/lxcs.tf`), and `terraform apply` propagated the corrections via in-place updates.

**VMID custom field blocker:** the hand-created `VMID` custom field was type=integer (semantically correct). Provider v5.3.0's `custom_fields = (Map of String)` stringifies all values; NetBox strictly rejects strings on integer fields. **Upstream issue [#349](https://github.com/e-breuninger/terraform-provider-netbox/issues/349)** documents the gap — open since 2026, not fixed in v5.3.0. **Workaround:** destroy + recreate the field as `type = "text"` with `validation_regex = "^[0-9]*$"` (restores numeric discipline TF-side). Loss of 17 hand-entered values was acceptable because they're declared in `local.vms` in `vms.tf` and re-populated by the same apply. NetBox doesn't allow changing custom_field `type` post-creation (`PUT /extras/custom-fields/{id}/` with `type` changed errors `Changing the type of custom fields is not supported.`), forcing the destroy-recreate dance via `terraform apply -replace=...`.

### Resource-name asymmetry

The e-breuninger/netbox v5.3.0 resource names have a surprising asymmetry:

- Device primary-IP binding: `netbox_device_primary_ip`
- VM primary-IP binding: `netbox_primary_ip` (NOT `netbox_virtual_machine_primary_ip`)

First-pass HCL guessed `netbox_virtual_machine_primary_ip`; provider rejected with "Invalid resource type". Future-debugging shortcut: `curl -s 'https://api.github.com/repos/e-breuninger/terraform-provider-netbox/contents/docs/resources?ref=v5.3.0' | jq -r '.[].name'` to list every resource the provider supports.

### Provider "tags = []" / "custom_fields = {}" clears

The provider sends `tags = []` and `custom_fields = {}` in its PUT body when the field is unset in HCL — silently clearing NetBox-side values that aren't declared. `lifecycle.ignore_changes` does NOT suppress this; the provider still sends the field in its PUT, just from whatever's in state. To preserve, you MUST declare the field in HCL (even just to re-state existing values from the API). Discovered on Stage 6 (VIP `dns_name` values about to be cleared) and Stage 5 (custom_fields VMID).

### Embedded incident — worker OOM cascade

Mid-Stage 0 chart-restart of NetBox triggered an OOM cascade across all 3 K3s workers. Separate retro: [worker-OOM-during-netbox-rolling-restart](2026-05-24-worker-oom-during-netbox-rolling-restart.md). Resolution: bumped worker memory 4G → 8G via sequential `terraform apply -target=...["einherjar-X"]` (bpg/proxmox handles `memory.dedicated` as in-place update + VM reboot, NO recreate).

---

## What we shipped

8 commits across the retrofit:

| Stage | Commit | Description |
|-------|--------|-------------|
| 0 | `b576bd2` | `api_token_peppers` ExternalSecret addition |
| 1 | `b52f96d` | TF module skeleton (versions/provider/main/outputs) |
| (incident) | `d690381` | worker memory 4G → 8G |
| 2 | `dd1774a` | site + cluster + manufacturers + device types + roles (23 imports + 16 changes) |
| 3 | `70701ce` | VLANs + prefixes (20 imports, 0 changes) |
| 4 | `6c1d1e5` | devices + interfaces + primary IPs (5 imports + 15 creates + 5 changes) |
| 5 | `199b94f` | VMs + LXCs + interfaces + IPs (72 imports + 20 creates + 47 changes; 3 placement corrections) |
| 6 | `4ec1762` | VIP records (3 imports + 3 changes) |
| 7 | `cb55a93` | tag definitions (14 imports) |
| 8 | (this) | post-flight: CLAUDE.md gotchas, build-sequence, decisions, open-questions, this retro |

Final `terraform plan` against `terraform/netbox/`: zero drift.

---

## Findings → rules

All landed in [CLAUDE.md](../../CLAUDE.md):

1. **"### NetBox" — `API_TOKEN_PEPPERS` must be supplied via `netbox-app` ExternalSecret** when using `existingSecret`.
2. **"### NetBox" — chart's `superuser.api_token` doesn't get inserted as a DB Token row.** Manual mint via UI or Django shell on first deploy.
3. **"### NetBox" — NetBox 4.6 v2 token auth format** is `Authorization: Bearer nbt_<key>.<token>` (provider auto-detects).
4. **"### NetBox" — NetBox 4.6 User model removed `is_staff`** (Django's standard field); provider's `staff` setting is a no-op.
5. **NEW "### Terraform — netbox provider" section** with 9 entries covering: provider/NetBox version skew warning, `netbox_token` broken against 4.4+, `netbox_user` no is_superuser, `netbox_ip_address` device_interface_id vs interface_id+object_type conflict, `netbox_primary_ip` not `_virtual_machine_primary_ip`, integer custom_fields blocked (issue #349), custom_field type can't be changed post-creation, provider clears unset `tags`/`custom_fields`, NetBox slug auto-strips colons.
6. **Architectural invariant updated** — TF→NetBox standing pattern is live; every new LXC/VM TF resource MUST have a matching `netbox_virtual_machine` + `netbox_interface` + `netbox_ip_address` declaration in `terraform/netbox/vms.tf` locals.

Two new decisions.md rows: auth model pivot, worker memory baseline.

---

## What we did NOT do

Carried forward per [open-questions.md](../operations/open-questions.md):

- **Per-resource tag application** — Stage 7 imported the 14 tag definitions but no tag→resource bindings exist yet. Apply incrementally as resources get touched.
- **AGH trio VMID retrofit** — Saga/Mimir/Kvasir have empty VMIDs (manual install, no IaC). Fill when Phase 5b.2 (AGH IaC) lands.
- **Revert VMID type to integer** — when provider issue #349 lands, swap text → integer and drop `validation_regex`.

---

## Lesson — provider-version vs target-version skew is its own gotcha class

Three of the day's blockers were rooted in `e-breuninger/netbox v5.3.0` being tested up to NetBox 4.4.10 while we're on 4.6.1. Provider warning ("Possibly unsupported Netbox version") is mild but consequential: every "weird" behavior we hit was actually a real upstream gap. **First-line diagnostic for any future provider-side oddity:** check the provider's GitHub for open issues against the NetBox version we're running. The cost of one issue-search beats hours of "is this my mistake or theirs."

The chart-deploy gaps (peppers, super_user.py) are parallel — chart 8.2.17 + NetBox 4.6.1 surfaced behaviors the chart-defaults assume don't happen yet. Same lens: when defaults don't behave, check upstream behavior at the exact version pair before assuming local error.
