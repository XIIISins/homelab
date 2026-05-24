# terraform/adguard/main.tf
#
# TF→AdGuard Home writes via gmichels/adguard provider. Initial scope is
# just DNS rewrites (the rewrites.tf locals map); future scope may grow
# to clients / filter lists / user_rules if hand-editing those starts
# producing drift, but the rule for now is "import what we have, then
# stop hand-editing — additions come through Git."
#
# ──────────────────────────────────────────────────────────────────────
# Single-origin write target
# ──────────────────────────────────────────────────────────────────────
#
# We target Saga (10.0.11.201) directly, NOT the keepalived VIP
# (10.0.10.200). adguardhome-sync runs on a cron (`*/1` post 5b.2) and
# fans writes from Saga out to Mimir + Kvasir, so writing to the origin
# is the canonical path. Writing to the VIP races whichever node is
# currently elected and confuses the sync direction. The "VIP is the
# published contract" rule (see terraform/tailscale/dns.tf) applies to
# READERS — writers go to the origin.
#
# ──────────────────────────────────────────────────────────────────────
# Source-of-truth layering — future direction
# ──────────────────────────────────────────────────────────────────────
#
# Every rewrite in rewrites.tf restates a record that ALSO lives in
# NetBox as an IP address with `dns_name` populated. The eventual goal
# is to drive the locals map from a NetBox data source (`data
# "netbox_ip_addresses"` filtered on `dns_name != ""`) so this module
# becomes a thin projection: NetBox = truth, AGH = derived. Until the
# NetBox data source is wired (deferred — not enough scale-of-pain to
# justify yet), the locals map is the spec and adding a new rewrite
# means editing two places (NetBox + here).
#
# See feedback memory `project_source_of_truth_layering.md`.
#
# ──────────────────────────────────────────────────────────────────────
# Bootstrap — import existing hand-created rewrites
# ──────────────────────────────────────────────────────────────────────
#
# 27 rewrites were hand-created in the AGH UI between Phases 4–5i. The
# import blocks in rewrites.tf retrofit those into TF state on the first
# apply. After a clean apply with zero diff, the import blocks become
# no-ops (safe to leave; standard post-flight is to delete them once
# the retrofit is confirmed and the state is steady).
