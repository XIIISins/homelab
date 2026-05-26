# ansible/roles/tailscale/README.md

# tailscale role

Installs the Tailscale daemon (Debian only) and joins the tailnet using
a pre-authorized authkey read from HashiCorp Vault.

## Required per-host vars (inline on inventory entries)

- `tailscale_tag` — single tag name without `tag:` prefix
  (e.g. `subnet-router`, `exit-node`). Must match a tag the authkey
  was minted with (see `terraform/tailscale/authkeys.tf`).
- `tailscale_advertise_routes` — optional, comma-separated CIDRs
  advertised by this node (e.g. `10.0.0.0/16`). Subnet-router roles.
- `tailscale_advertise_exit_node` — optional boolean. Exit-node roles.

## Required global vars (`group_vars/all/vars.yml`)

- `vault_url` — HashiCorp Vault URL
- `vault_approle_role_id` — AppRole RoleID
- `vault_approle_secret_id` — AppRole SecretID

## Vault dependency

Reads authkey from `secret/ansible/tailscale/authkeys/<inventory_hostname>`.
Authkey is minted by `terraform/tailscale/` (see `authkeys.tf`).

## Bootstrap

First run (LXC has only root SSH, no `ansible` user yet):

    ansible-playbook -e 'ansible_user=root' --tags baseline playbooks/asgard-tailscale.yml

Subsequent runs (full play as `ansible`):

    ansible-playbook playbooks/asgard-tailscale.yml

## Tailscale auth-key expiry note

Tailscale caps auth-key lifetime at 90 days (API hard-cap; no truly
indefinite option). The Terraform module mints reusable keys with
`recreate_if_invalid = "always"`, so any `terraform apply` after a key
expires regenerates it. Operationally: if you rebuild an LXC 90+
days after initial key mint, run `terraform apply` on the
`terraform/tailscale/` module BEFORE re-running this role — otherwise
the role reads a stale expired key from Vault and `tailscale up`
fails with `auth key expired`.
