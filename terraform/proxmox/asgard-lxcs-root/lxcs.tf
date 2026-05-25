# terraform/proxmox/asgard-lxcs-root/lxcs.tf

# ----------------------------------------------------------------------------
# LXCs 1113/1114/1115 — Tailscale subnet routers + exit node
# ----------------------------------------------------------------------------
# Bifrost (1113, urd) + Heimdall (1114, verd): subnet-router HA pair
# advertising the 10.0.0.0/16 supernet (auto-approved via tailnet ACL
# autoApprovers — see terraform/tailscale/policy.hujson).
#
# Gjallarbru (1115, skuld): exit node, advertises IPv4+IPv6 default
# routes (also auto-approved).
#
# Each LXC on a different Proxmox host so a single-host failure never
# takes down >1 advertiser of the supernet — the tailnet picks
# another route holder transparently.
#
# Why these LXCs live in their own module:
#   /dev/net/tun passthrough requires `device_passthrough` at create
#   time, which the bpg/proxmox API-token auth path doesn't accept —
#   only root@pam ticket auth can set it. See CLAUDE.md "bpg/proxmox
#   API token can change nesting, NOT other LXC features". Rather than
#   carry an aliased `proxmox.root` provider in the main asgard-lxcs
#   module (and force PROXMOX_VE_PASSWORD on every apply, including
#   API-token-only resources), the tailscale trio lives here under a
#   single root@pam provider. Future LXCs needing root-only features
#   (fuse, keyctl, additional device_passthroughs) join this module.
#
# Tailscale-specific config (apt repo, daemon, authkey from Vault,
# `tailscale up` flags) lives in the Ansible tailscale role.
# Authkeys are minted in terraform/tailscale/ and read by Ansible via
# community.hashi_vault lookup.
#
# See:
#   - docs/homelab-design.md → "Asgard LXCs" table
#   - terraform/tailscale/authkeys.tf
#   - ansible/roles/tailscale/README.md
# ----------------------------------------------------------------------------

locals {
  tailscale_nodes = {
    bifrost    = { node = "urd",   vmid = 1113, ip = "10.0.11.213" }
    heimdall   = { node = "verd",  vmid = 1114, ip = "10.0.11.214" }
    gjallarbru = { node = "skuld", vmid = 1115, ip = "10.0.11.215" }
  }
}

# Throwaway root passwords — Proxmox API requires one to create the
# container, but each LXC is configured for SSH-key-only auth. Never
# used; persisted in remote state, which contains no other secrets
# for this module.
resource "random_password" "tailscale_root" {
  for_each = local.tailscale_nodes

  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "tailscale" {
  for_each = local.tailscale_nodes

  description = "Tailscale node ${each.key}"

  node_name = each.value.node
  vm_id     = each.value.vmid
  tags      = ["asgard", "lxc", "tailscale", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  # Sizing: tailscaled is a tiny Go daemon — a few tens of MB RSS in
  # steady state. 512MB gives headroom for log spikes and apt ops;
  # scale down to 256 later once logs ship off-box.
  cpu {
    cores = 1
  }

  memory {
    dedicated = 512     # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 4     # GB
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = each.key

    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = "10.0.11.1"
      }
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.tailscale_root[each.key].result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true     # systemd 257 on Debian 13 — see gotchas
  }

  # /dev/net/tun passthrough for tailscaled inside an unprivileged
  # LXC. Provider defaults for uid/gid/mode/deny_write are fine.
  device_passthrough {
    path = "/dev/net/tun"
  }

  console {
    enabled = true
    type    = "tty"
  }
}
