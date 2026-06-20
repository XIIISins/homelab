# terraform/semaphore/main.tf
#
# One project (`asgard`) holding everything for the homelab fleet.
# A second `jotunheim` project lands when jotunheim K3s exists
# (Phase 7) — same shape, separate Vault paths.

resource "semaphoreui_project" "asgard" {
  name = "asgard"
  # Provider field is `alert`, not `allow_alert`. Surfaces task
  # status in the dashboard; without it the alert column is hidden.
  alert = true
  # Serializes ansible-playbook runs across this project — see
  # CLAUDE.md "ansible-playbook concurrency" gotcha. SSH MaxAuthTries
  # + notify-handler restarts cascade if two playbooks race.
  max_parallel_tasks = 1
}

# === Keys ===
#
# Three keys: GitHub deploy (SSH, repo access), ansible-user SSH
# (login=ansible, host access), ansible-vault password (login_password
# type — only `password` field is used, login is required-but-ignored).

resource "semaphoreui_project_key" "github_deploy" {
  project_id = semaphoreui_project.asgard.id
  name       = "github-deploy"

  ssh = {
    private_key = data.vault_kv_secret_v2.github_deploy_key.data["private"]
    # login required by the resource schema; for git over SSH the
    # user is encoded in the URL (git@github.com:...) so any value
    # here is fine.
    login = "git"
  }
}

resource "semaphoreui_project_key" "ansible_ssh" {
  project_id = semaphoreui_project.asgard.id
  name       = "ansible-ssh"

  ssh = {
    private_key = data.vault_kv_secret_v2.host_ssh_key.data["private"]
    login       = data.vault_kv_secret_v2.host_ssh_key.data["login"]
  }
}

# Ansible-vault password — referenced by templates.vaults[].password.
# Type=login_password (provider's only secret-as-value primitive);
# `login` is unused at runtime but required by schema.
resource "semaphoreui_project_key" "ansible_vault" {
  project_id = semaphoreui_project.asgard.id
  name       = "ansible-vault-password"

  login_password = {
    login    = "ansible-vault"
    password = data.vault_kv_secret_v2.ansible_vault_password.data["value"]
  }
}

# === Repository ===

resource "semaphoreui_project_repository" "homelab" {
  project_id = semaphoreui_project.asgard.id
  name       = "homelab"
  url        = "git@github.com:XIIISins/homelab.git"
  branch     = "main"
  ssh_key_id = semaphoreui_project_key.github_deploy.id
}

# === Inventory ===
#
# `file:` type pointing at the NetBox dynamic inventory committed at
# ansible/inventory/netbox.yml (Phase 5h.3.c). ansible.cfg's
# `inventory = inventory/netbox.yml, inventory/hosts.yml` chains
# the static hosts.yml as DR fallback; Semaphore inherits ansible.cfg
# because working_directory is `ansible/`.
#
# ssh_key_id is the ansible-user key (login=ansible). become_key_id
# omitted — every managed host has passwordless sudo for the ansible
# user (baseline role).
resource "semaphoreui_project_inventory" "netbox" {
  project_id = semaphoreui_project.asgard.id
  name       = "netbox-dynamic"
  ssh_key_id = semaphoreui_project_key.ansible_ssh.id

  file = {
    # Repo-root-relative — Semaphore runs from the repo root. The
    # static-fallback chain that ansible.cfg defines for the operator's
    # MacBook (`inventory = inventory/netbox.yml, inventory/hosts.yml`)
    # is bypassed here: Semaphore passes -i explicitly, which takes
    # precedence over ansible.cfg's inventory setting. NetBox-down +
    # cache-expired is a manual DR path operated from the MacBook
    # (--inventory ansible/inventory/hosts.yml).
    path          = "ansible/inventory/netbox.yml"
    repository_id = semaphoreui_project_repository.homelab.id
  }
}

# === Environment ===
#
# Carries env vars + secrets for every template. The community
# .hashi_vault lookup plugin reads ANSIBLE_HASHI_VAULT_* — must
# match the operator's `~/.config/ansible/vault-approle.env` shape.
# All four are required for AppRole auth.
#
# HERMOD_MODE is set per-template (drift vs apply) so the
# callback plugin can tag its POST correctly.
resource "semaphoreui_project_environment" "default" {
  project_id = semaphoreui_project.asgard.id
  name       = "default"

  environment = {
    # Point Ansible at our ansible.cfg (Semaphore runs from repo root,
    # ansible.cfg lives under ansible/). Inventory + roles + callbacks
    # all resolve relative to this file's location.
    ANSIBLE_CONFIG                  = "ansible/ansible.cfg"
    ANSIBLE_HASHI_VAULT_AUTH_METHOD = "approle"
    # Env var is `_ADDR`, NOT `_URL` — `_URL` is silently ignored by
    # community.hashi_vault (no warning, just "Required option url was
    # not set"). The operator's MacBook works because VAULT_ADDR is also
    # set via homelab-env (the plugin falls back to that); the pod has
    # no VAULT_ADDR, so the plugin-specific var is the only path.
    # HTTPS since the listener TLS flip — Vault serves the homelab internal
    # CA cert, so the lookup must trust it via ANSIBLE_HASHI_VAULT_CA_CERT
    # (the trust-manager vault-ca-bundle ConfigMap, mounted at /etc/vault-ca
    # by the StatefulSet). See docs/procedures/vault-tls-migration.md.
    ANSIBLE_HASHI_VAULT_ADDR    = "https://vault.vault.svc.cluster.local:8200"
    ANSIBLE_HASHI_VAULT_CA_CERT = "/etc/vault-ca/ca.crt"
    ANSIBLE_CALLBACKS_ENABLED   = "hermod_summary"
    # SSH key path for managed-host access. Semaphore strips the pod's
    # baseline env on task spawn — variables set on the StatefulSet
    # container don't reach ansible-playbook. Must be set here in the
    # project_environment to actually propagate. Mounted via the
    # semaphore-ansible-ssh-key Secret + StatefulSet volume.
    ANSIBLE_PRIVATE_KEY_FILE = "/etc/ssh-keys/ansible_niflheim"
  }

  # role_id, secret_id, and the full Hermod URL (config-key embedded)
  # all stored encrypted at rest in Semaphore. role_id isn't strictly
  # sensitive (paired with secret_id it auths against Vault) but it
  # paths through encrypted state for free, so no reason to leak it.
  secrets = [
    {
      name  = "ANSIBLE_HASHI_VAULT_ROLE_ID"
      type  = "env"
      value = data.vault_kv_secret_v2.vault_approle.data["role_id"]
    },
    {
      name  = "ANSIBLE_HASHI_VAULT_SECRET_ID"
      type  = "env"
      value = data.vault_kv_secret_v2.vault_approle.data["secret_id"]
    },
    {
      name = "HERMOD_URL"
      type = "env"
      # niflheim FQDN resolves to Hermod LXC via AGH; in-cluster pods
      # reach it through CoreDNS upstream forward to AGH. config-key is
      # the soft-auth gate per docs/services/notifications.md "Access
      # control — Caddy IP allowlist" (Caddy's IP allowlist is the real
      # gate; the key is belt-and-braces).
      # Port 80 — Caddy fronts AppriseAPI; AppriseAPI's own :8000 is
      # bound to 127.0.0.1 (not externally reachable).
      value = "http://hermod.niflheim.xiiisins.com/notify/${data.vault_kv_secret_v2.hermod_config_key.data["value"]}"
    },
  ]
}
