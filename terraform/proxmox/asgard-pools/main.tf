# terraform/proxmox/asgard-pools/main.tf
#
# Phase 10g: a Proxmox-enforced boundary for the rebuild runner (docs/plans/active/10g-rebuild-loop.md, "PVE-side least
# privilege"; docs/procedures/aiops-rebuild.md). The runner re-creates canary LXCs with Terraform. Whatever software
# checks exist around it, its PVE API token must be UNABLE to touch anything else: so the token's user has write
# privileges on ONE resource pool and nothing on `/`.
#
#   pool      aiops-canary       members: the canary LXCs 1190-1192 (terraform/proxmox/asgard-lxcs sets pool_id)
#   user      aiops-rebuild@pve  its ACLs are exactly the four kinds below, no ACL on `/`
#   token     runner             privilege separation OFF, so it inherits the user's ACLs (same shape as aiops-access/);
#                                the secret goes to Vault, never to an output
#
# Privilege set: the minimum bpg/proxmox is expected to need to plan, destroy and re-create an unprivileged LXC in the
# pool. It is a STARTING SET: the exact list is confirmed empirically (positive: a canary `-replace` through the
# runner; negative: a stop/destroy of a guest OUTSIDE the pool, PBS 1101 included, must 403). See "Acceptance" in the
# procedure. If the first live plan/apply reports a missing privilege, add exactly that privilege here, in a PR; never
# widen the path.
#
#   /pool/aiops-canary   AiopsRebuildGuest    propagate=true: applies to member guests and to a guest being created in
#                                             the pool (PVE checks VM.Allocate on the pool for a new VMID).
#   /storage/<guest>     AiopsRebuildStorage  allocate the rootfs volume; Datastore.Audit to read it back.
#   /storage/<template>  AiopsRebuildTemplate read-only: the vztmpl the container is created from.
#   /sdn/zones/<zone>    AiopsRebuildNet      SDN.Use on the zone: attach the container NIC to vmbr0 / its VLAN tag.
#
# NOT granted anywhere: Sys.*, Permissions.Modify, User.Modify, Realm.*, VM.Migrate, VM.Snapshot*, VM.Backup,
# VM.Clone, VM.Console, Datastore.Allocate, Datastore.AllocateTemplate, Pool.Allocate, and every privilege on `/`.
# `features` other than nesting, `mount=` and device passthrough need root@pam in PVE: this token can never set them
# (which is also why the Tailscale LXCs, asgard-lxcs-root, are out of this runner's scope).
#
# Provider auth is root@pam (ticket): creating users, roles, pools and ACLs needs it (same as aiops-access/). Apply
# from the MAIN checkout only. The new pool must exist BEFORE `terraform apply` in terraform/proxmox/asgard-lxcs, which
# then puts the canaries into it (an in-place pool membership update; a plan that says "replace" is the signal to stop).

resource "proxmox_virtual_environment_pool" "aiops_canary" {
  pool_id = var.canary_pool_id
  comment = "AIOps canary LXCs (1190-1192): the only guests the rebuild runner may touch. Managed by terraform/proxmox/asgard-pools."
}

resource "proxmox_virtual_environment_role" "rebuild_guest" {
  role_id = "AiopsRebuildGuest"
  privileges = [
    "Pool.Audit",
    "VM.Allocate",
    "VM.Audit",
    "VM.Config.CPU",
    "VM.Config.Disk",
    "VM.Config.HWType",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.PowerMgmt",
  ]
}

resource "proxmox_virtual_environment_role" "rebuild_storage" {
  role_id    = "AiopsRebuildStorage"
  privileges = ["Datastore.Audit", "Datastore.AllocateSpace"]
}

resource "proxmox_virtual_environment_role" "rebuild_template" {
  role_id    = "AiopsRebuildTemplate"
  privileges = ["Datastore.Audit"]
}

resource "proxmox_virtual_environment_role" "rebuild_net" {
  role_id    = "AiopsRebuildNet"
  privileges = ["SDN.Use"]
}

resource "proxmox_virtual_environment_user" "aiops_rebuild" {
  user_id = "aiops-rebuild@pve"
  comment = "AIOps rebuild runner (pool-scoped writes, no ACL on /). Managed by terraform/proxmox/asgard-pools."
  enabled = true

  # The ACLs are separate proxmox_virtual_environment_acl resources below. After the first apply the provider reads them
  # back onto this resource's `acl` attribute, and a later plan then wants to UPDATE the user by REMOVING every ACL entry
  # (pool, storage, SDN) that is not declared inline: applying that would strip the runner's permissions (found
  # 2026-10-03 while adding the VM.Audit ACLs). The ACL resources are the source of truth, so ignore the inline attribute.
  lifecycle {
    ignore_changes = [acl]
  }
}

resource "proxmox_virtual_environment_acl" "rebuild_pool" {
  path      = "/pool/${proxmox_virtual_environment_pool.aiops_canary.pool_id}"
  role_id   = proxmox_virtual_environment_role.rebuild_guest.role_id
  user_id   = proxmox_virtual_environment_user.aiops_rebuild.user_id
  propagate = true
}

# Per-VMID ACLs on EXACTLY the canary VMIDs (never a wildcard), with the same guest role as the pool. Two live findings
# (2026-10-03): (1) a destroyed canary leaves the pool, so the pool ACL stops covering its VMID and a refresh of
# /vms/<vmid> answers 403 (VM.Audit) instead of "not found"; (2) creating a container with a fresh VMID checks the config
# privileges (VM.Config.Options, ...) on /vms/<vmid> itself, which a pool ACL does not cover until the guest exists.
# Without these the runner can neither plan nor re-create a guest that was deleted behind Terraform's back.
resource "proxmox_virtual_environment_acl" "rebuild_vm" {
  for_each = toset([for v in var.canary_vmids : tostring(v)])

  path      = "/vms/${each.value}"
  role_id   = proxmox_virtual_environment_role.rebuild_guest.role_id
  user_id   = proxmox_virtual_environment_user.aiops_rebuild.user_id
  propagate = false
}

resource "proxmox_virtual_environment_acl" "rebuild_storage" {
  for_each = toset(var.guest_storage_ids)

  path      = "/storage/${each.value}"
  role_id   = proxmox_virtual_environment_role.rebuild_storage.role_id
  user_id   = proxmox_virtual_environment_user.aiops_rebuild.user_id
  propagate = false
}

resource "proxmox_virtual_environment_acl" "rebuild_template" {
  for_each = toset(var.template_storage_ids)

  path      = "/storage/${each.value}"
  role_id   = proxmox_virtual_environment_role.rebuild_template.role_id
  user_id   = proxmox_virtual_environment_user.aiops_rebuild.user_id
  propagate = false
}

resource "proxmox_virtual_environment_acl" "rebuild_net" {
  path      = "/sdn/zones/${var.sdn_zone_id}"
  role_id   = proxmox_virtual_environment_role.rebuild_net.role_id
  user_id   = proxmox_virtual_environment_user.aiops_rebuild.user_id
  propagate = true
}

resource "proxmox_virtual_environment_user_token" "aiops_rebuild" {
  user_id    = proxmox_virtual_environment_user.aiops_rebuild.user_id
  token_name = "runner"
  comment    = "Rebuild runner token (pool-scoped). Managed by terraform/proxmox/asgard-pools."

  # Privilege separation OFF: the token inherits the user's ACL (the pool + the storage/SDN entries above).
  privileges_separation = false
}

# The token secret goes to Vault and ONLY there (no output block exists; state is the encrypted S3 backend).
# Path follows the machine-consumer convention (`ansible/aiops/...`); the runner's own AppRole policy
# (terraform/vault/rebuild-runner.tf) reads exactly `ansible/aiops/rebuild/*`. The loader on Frigg turns it into
# TF_VAR_proxmox_api_token ("USER@REALM!NAME=SECRET", the format asgard-lxcs expects) on tmpfs.
resource "vault_kv_secret_v2" "rebuild_pve_token" {
  mount = "secret"
  name  = "ansible/aiops/rebuild/pve-token"

  data_json = jsonencode({
    token_id = "${proxmox_virtual_environment_user.aiops_rebuild.user_id}!${proxmox_virtual_environment_user_token.aiops_rebuild.token_name}"
    # bpg/proxmox returns the full `USER@REALM!NAME=UUID`; keep only the UUID (same handling as aiops-access/).
    secret = element(reverse(split("=", proxmox_virtual_environment_user_token.aiops_rebuild.value)), 0)
  })
}
