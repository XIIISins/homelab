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

# ----------------------------------------------------------------------------
# LXCs 1130/1131/1132 — PostgreSQL cluster (Skuld/Urd/Verd)
# ----------------------------------------------------------------------------
# Cluster nodes for the asgard PostgreSQL service. Each LXC runs PostgreSQL 17.
# Streaming replication between nodes and HAProxy VIP frontend
# (10.0.10.210) are configured via Ansible, not Terraform.
#
# Initial deploy (2026-05-XX): only postgres1 (Skuld) is active. Cluster
# expansion to postgres2/3 deferred until post-Authentik — clustering gets
# validated against a real consumer rather than synthetic load.
#
# Each node lives on a different Proxmox host so a single-host failure
# never takes down >1 PG node.
#
# See:
#   - docs/homelab-design.md → "Asgard LXCs" table, "Database (1130-1139)"
#   - ansible/roles/postgres/README.md (TBD)
# ----------------------------------------------------------------------------

locals {
  postgres_nodes = {
    fulla = { node = "skuld", vmid = 1130, ip = "10.0.11.230" }
    # Uncomment when expanding to cluster (post-Authentik):
    # vor = { node = "urd",  vmid = 1131, ip = "10.0.11.231" }
    # idunn = { node = "verd", vmid = 1132, ip = "10.0.11.232" }
  }
}

# Throwaway root passwords — Proxmox API requires one to create the
# container, but each LXC is configured for SSH-key-only auth. Never used;
# persisted in local state, which is gitignored.
resource "random_password" "postgres_root" {
  for_each = local.postgres_nodes

  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "postgres" {
  for_each = local.postgres_nodes

  description = "PostgreSQL cluster node (${each.key})"

  node_name = each.value.node
  vm_id     = each.value.vmid
  tags      = ["asgard", "lxc", "postgres", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  # Sizing is cluster-wide, NOT per-node. Failover symmetry requires
  # identical resources — asymmetric sizing means failover silently
  # degrades. If you need to size a node differently, that's deliberate;
  # promote the field into the locals map at that point.
  cpu {
    cores = 2
  }

  memory {
    dedicated = 4096     # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 16    # GB — local LVM-thin; NFS unsuitable for PG data
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
      password = random_password.postgres_root[each.key].result
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
