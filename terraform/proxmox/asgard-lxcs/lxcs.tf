# ----------------------------------------------------------------------------
# LXC 1120 — Factorio dedicated server (Urd)
# ----------------------------------------------------------------------------
# Hosts headless Factorio + SFTPGo. Operator (a friend) connects via SFTP
# on TCP 22022 to manage mods/saves/server state; players connect via
# UDP 34197 (Factorio protocol). Both ports are forwarded on UCG-Ultra.
#
# See:
#   - docs/homelab-design.md → "Must-run LXCs" table
#   - ansible/roles/factorio/README.md
#   - ansible/roles/sftpgo/README.md
# ----------------------------------------------------------------------------

# Throwaway root password — Proxmox API requires one to create the container,
# but the LXC is configured for SSH-key-only auth. Never used; persisted in
# local state, which is gitignored.
resource "random_password" "factorio_root" {
  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "factorio" {
  description = "Factorio dedicated server + SFTPGo for operator self-service"

  node_name = "urd"
  vm_id     = 1120
  tags      = ["asgard", "lxc", "factorio", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  cpu {
    cores = 4
  }

  memory {
    dedicated = 8192     # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 8     # GB
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = "factorio"

    ip_config {
      ipv4 {
        address = "10.0.11.220/24"
        gateway = "10.0.11.1"
      }
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.factorio_root.result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true
  }

  console {
    enabled = true
    type    = "tty"
  }
}
