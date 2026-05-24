# terraform/netbox/main.tf
#
# TF→NetBox writes via e-breuninger/netbox provider (Phase 5i.3).
# Authenticates as the NetBox admin user via NETBOX_API_TOKEN env var
# (operator-side bootstrap token from 1P "Asgard - NetBox - admin API
# token"). The originally-planned dedicated `terraform` NetBox user
# was dropped: provider v5.3.0 has three v2-token / NetBox-4.6
# incompatibilities (POST+PUT token-update rejected, v2 token secret
# unavailable in state, no is_superuser field) that make TF management
# of the auth identity itself impossible without manual workarounds.
# See CLAUDE.md "### NetBox" gotchas for the full incompatibility list.
#
# Source-of-truth model: TF declarations here are the spec for what
# NetBox should contain; `terraform import` retrofits the existing
# records hand-created via the web UI during Phase 5i.e (per the
# procedure at docs/procedures/netbox-initial-data-import.md). Once
# imported, all future LXC/VM declarations in terraform/proxmox/*
# MUST have matching netbox_* resources here (or in adjacent files
# under this module) — see CLAUDE.md invariant.

# Module skeleton — first concrete resources land in Stage 2
# (cluster_type, cluster, manufacturers, device_types, roles).
