# terraform/semaphore/templates.tf
#
# Four templates per the design doc:
#
#   - refresh-netbox-inventory  cron */4h  ansible-side: ad-hoc command
#   - asgard-drift-check        cron */6h  --check --diff site.yml
#   - asgard-apply              manual     site.yml (full converge)
#   - asgard-fleet-agents       cron daily fleet-agents.yml (defense-
#                                          in-depth vlagent + zabbix-
#                                          agent reconverge across
#                                          every host, complementing
#                                          the per-host-group agent
#                                          roles in site.yml)
#
# The drift-check + apply + fleet-agents templates run *wrapper*
# playbooks (drift-check.yml / apply.yml / fleet-agents.yml) rather
# than the underlying playbooks directly. The wrapper's filename is
# what the hermod_summary callback plugin uses to infer mode for its
# end-of-run POST.

# === refresh-netbox-inventory ===
#
# Wipes the netbox.netbox dynamic-inventory cache + actively rebuilds
# it via `ansible-inventory --list`, with retry-loop tolerance for
# transient NetBox 500s. cache_timeout (24h in netbox.yml) exceeds
# this template's refresh cadence (4h), so consumer playbooks always
# hit the warm cache between refreshes — the refresh template is the
# only NetBox-live path in steady state. See the script for details.
resource "semaphoreui_project_template" "refresh_netbox_inventory" {
  project_id     = semaphoreui_project.asgard.id
  name           = "refresh-netbox-inventory"
  description    = "Rebuild NetBox dynamic-inventory cache (with retry). Drift-check / apply read the cache, not live NetBox."
  app            = "bash"
  playbook       = "ansible/scripts/refresh-netbox-inventory.sh"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Failure → callback fires alert (NetBox down or cache path
  # unwritable). Success → silent (audit trail only).
  suppress_success_alerts = true
}

# === asgard-drift-check ===

resource "semaphoreui_project_template" "asgard_drift_check" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-drift-check"
  description    = "Read-only converge check across the fleet. Reports drift to Hermod."
  app            = "ansible"
  playbook       = "ansible/playbooks/drift-check.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # --check + --diff at the template level so it can't be forgotten.
  # arguments is a list-of-strings; Semaphore wraps each element as
  # a separate argv entry.
  arguments                   = ["--check", "--diff"]
  allow_override_args_in_task = false

  # Vault password supplied via the ansible_vault key — Semaphore
  # writes it to a temp file + passes --vault-password-file.
  # Provider schema (v0.2.2): `password` is a nested object containing
  # `vault_key_id` — NOT a `type = "password"` discriminator field with
  # `vault_key_id` at the top level (that's the WebFetch-documented
  # shape, but the actual schema differs). TF accepts the wrong shape
  # silently + stores vaults=null server-side, so playbook runs hit
  # "Attempting to decrypt but no vault secrets found" on any task
  # that loads an ansible-vault-encrypted var.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Success path can be noisy (clean drift-check every 6h) — only
  # surface failures + the callback-driven drift alerts.
  suppress_success_alerts = true
}

# === asgard-apply ===
#
# Manual-trigger template. No schedule; operator clicks "Run" after
# investigating a drift alert. Failure → Hermod `critical` (via
# callback when HERMOD_MODE=apply).
resource "semaphoreui_project_template" "asgard_apply" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-apply"
  description    = "Full converge across the fleet. Manual trigger after drift investigation."
  app            = "ansible"
  playbook       = "ansible/playbooks/apply.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Provider schema (v0.2.2): `password` is a nested object containing
  # `vault_key_id` — NOT a `type = "password"` discriminator field with
  # `vault_key_id` at the top level (that's the WebFetch-documented
  # shape, but the actual schema differs). TF accepts the wrong shape
  # silently + stores vaults=null server-side, so playbook runs hit
  # "Attempting to decrypt but no vault secrets found" on any task
  # that loads an ansible-vault-encrypted var.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Apply success IS news ("the converge worked, drift cleared") —
  # let Semaphore surface the success indicator + the callback
  # decides whether to actually POST to Hermod.
  suppress_success_alerts = false
}

# === asgard-fleet-agents ===
#
# Daily fleet-wide vlagent + zabbix-agent reconverge. Every per-host-
# group playbook in site.yml already ships these roles, so this is
# belt-and-braces — catches any host the per-group playbooks missed
# (future new playbook that forgets to add agents, etc.).
# Hermod posture matches apply: failure → critical, success → silent.
resource "semaphoreui_project_template" "asgard_fleet_agents" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-fleet-agents"
  description    = "Daily fleet-wide vlagent + zabbix-agent reconverge (defense-in-depth)."
  app            = "ansible"
  playbook       = "ansible/playbooks/fleet-agents.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Same vaults shape as the other ansible templates.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Defense-in-depth job — successful daily runs are background noise,
  # only surface failures (which also fire Hermod `critical` via the
  # callback's apply-mode classification).
  suppress_success_alerts = true
}

# === Schedules ===

resource "semaphoreui_project_schedule" "refresh_netbox_inventory" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.refresh_netbox_inventory.id
  name        = "every-4h"
  cron_format = "0 */4 * * *"
  enabled     = true
}

resource "semaphoreui_project_schedule" "asgard_drift_check" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.asgard_drift_check.id
  name        = "every-6h"
  # Offset 15 min past the hour so it doesn't collide with the
  # inventory-refresh cron at minute 0 (drift-check reads the
  # cache the refresh just wrote — give it 15 min to settle).
  cron_format = "15 */6 * * *"
  enabled     = true
}

# asgard-apply has no schedule — manual-only.

resource "semaphoreui_project_schedule" "asgard_fleet_agents" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.asgard_fleet_agents.id
  name        = "daily"
  # 04:30 UTC daily — quiet hours for the homelab, after the
  # inventory-refresh cron at minute 0 + drift-check at minute 15.
  # Use minute 30 to keep crons visually grouped on the hour.
  cron_format = "30 4 * * *"
  enabled     = true
}
