# terraform/authentik/identity.tf
#
# Authentik identity (users + groups) decoded from YAML data files.
# Source of truth:
#   - users.yaml — one entry per user; each lists their groups
#   - groups.yaml — one entry per group; defines group-level attributes
#
# Group membership is set from the USER side only. The `authentik_group`
# resource has no `users` attribute here — membership is computed as the
# inverse of users[*].groups and applied via the user resource.
#
# This avoids the dual-side-of-relationship plan-flapping problem where
# TF would oscillate between "drop user from group" and "add user to
# group" if both sides tried to declare the relationship.

# -----------------------------------------------------------------------------
# Decode YAML, normalize to maps keyed by name.
# -----------------------------------------------------------------------------
locals {
  users_yaml  = yamldecode(file("${path.module}/users.yaml"))
  groups_yaml = yamldecode(file("${path.module}/groups.yaml"))

  # Flatten user→group references for the cross-reference validation
  # below. Produces a list of {user, group} tuples.
  user_group_refs = flatten([
    for username, user in local.users_yaml : [
      for group in lookup(user, "groups", []) : {
        user  = username
        group = group
      }
    ]
  ])

  # Set of group names that ACTUALLY exist in groups.yaml.
  defined_groups = toset(keys(local.groups_yaml))

  # List of {user, group} references where the group is NOT defined.
  # If this list is non-empty, the precondition in `null_resource` fires.
  unknown_group_refs = [
    for ref in local.user_group_refs : ref
    if !contains(local.defined_groups, ref.group)
  ]
}

# Validation gate. Fires at plan time if any user references a group not
# defined in groups.yaml. The error message lists each bad reference so a
# typo in users.yaml shows up cleanly.
resource "terraform_data" "identity_validation" {
  lifecycle {
    precondition {
      condition = length(local.unknown_group_refs) == 0
      error_message = <<-EOT
        users.yaml references groups not defined in groups.yaml:
        ${join("\n", [for ref in local.unknown_group_refs : "  - user '${ref.user}' references group '${ref.group}'"])}

        Either add the group to groups.yaml or fix the typo in users.yaml.
      EOT
    }
  }
}

# -----------------------------------------------------------------------------
# Groups
# -----------------------------------------------------------------------------
resource "authentik_group" "this" {
  for_each = local.groups_yaml

  name         = each.key
  is_superuser = lookup(each.value, "is_superuser", false)
}

# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------
#
# Existing users (e.g. ghost) are imported once via:
#   terraform import 'authentik_user.this["ghost"]' 8
# After import, TF tracks the user record. The `password` field is NOT
# set here — passwords for human-interaction users live in 1Password,
# per the credential-store split rule.
#
# For NEW users (added by editing users.yaml), a random initial password
# is generated below and written to Vault. The user is expected to
# change it via Authentik UI on first login. No force-reset enforcement
# (Authentik doesn't have a native flag — tracked as pending).

resource "authentik_user" "this" {
  for_each = local.users_yaml

  username = each.key
  name     = each.value.name
  email    = lookup(each.value, "email", null)
  is_active = lookup(each.value, "is_active", true)

  # Each user's groups, resolved to authentik_group resource IDs.
  groups = [
    for group_name in lookup(each.value, "groups", []) :
    authentik_group.this[group_name].id
  ]

  # Initial password generation for new users. Lifecycle ignore_changes
  # on `password` means once the user changes it via the UI, TF won't
  # try to reset it back to the generated value on every apply.
  # For imported users, the password attribute is never set (no
  # random_password resource is referenced) — Authentik keeps whatever
  # password the user already has.
  lifecycle {
    ignore_changes = [password]
  }
}

# Random initial password per user. One per username; created once,
# stable across applies (Terraform persists the random_password output
# in state, so re-apply doesn't regenerate it).
#
# A user removed from users.yaml has their random_password destroyed
# alongside their authentik_user resource, which also removes the Vault
# entry — clean teardown.
locals {
  # Subset of users that should get a Terraform-generated initial password
  # written to Vault. Excludes users with `skip_initial_password: true` in
  # users.yaml — that flag is for pre-existing users whose password lives
  # in 1Password (per the human-interaction credential-store rule) and
  # never belonged in Vault.
  users_needing_initial_password = {
    for username, user in local.users_yaml : username => user
    if !lookup(user, "skip_initial_password", false)
  }
}

# Random initial password per user. One per username; created once,
# stable across applies (Terraform persists the random_password output
# in state, so re-apply doesn't regenerate it).
#
# A user removed from users.yaml has their random_password destroyed
# alongside their authentik_user resource, which also removes the Vault
# entry — clean teardown.
resource "random_password" "initial" {
  for_each = local.users_needing_initial_password

  length  = 24
  special = true
  # Authentik default password policy: 8+ chars. 24 is generous, fits
  # 1Password / Vault display, no UI line-wrapping problems.
}

# Vault write per user. Path:
#   secret/k8s/authentik/users/<username>/initial-password
#
# Operational note: This password is the INITIAL one. If a user logs in
# and changes their password, the Vault value becomes stale (Authentik
# stores its own hash; TF doesn't know about the change). At that point
# the password belongs in 1Password (human-interaction rule), and the
# Vault entry should be deleted. Until force-reset-on-first-login is
# implemented in Authentik (issue #19681 upstream), this is the
# operational seam.
resource "vault_kv_secret_v2" "user_initial_password" {
  for_each = local.users_needing_initial_password

  mount = "secret"
  name  = "k8s/authentik/users/${each.key}/initial-password"
  data_json = jsonencode({
    username         = each.key
    initial_password = random_password.initial[each.key].result
    note             = "Initial password. Change via Authentik UI on first login. Move final to 1Password and delete this Vault entry."
  })
}
