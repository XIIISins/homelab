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
# Cluster nodes for the asgard PostgreSQL service. Each LXC runs PostgreSQL 17
# under Patroni, with etcd-DCS on the adjacent HAProxy/etcd trio (1133-1135).
# HAProxy VIP frontend (10.0.10.210) is configured via keepalived on that
# trio. Patroni handles auto-failover; manual primary promotion is not the
# design.
#
# Phase 5g.2 expanded the cluster from Fulla-standalone to Patroni-managed
# 3-node HA, driven by operational pressure (Authentik PG-DNS flapping).
# Each node lives on a different Proxmox host so a single-host failure
# never takes down >1 PG node.
#
# See:
#   - docs/homelab-design.md → "Phase 5g.2" build sequence row + decision log
#   - ansible/roles/postgres/README.md
#   - ansible/roles/patroni/README.md (TBD — 5g.2.e)
# ----------------------------------------------------------------------------

locals {
  postgres_nodes = {
    fulla = { node = "skuld", vmid = 1130, ip = "10.0.11.230" }
    vor   = { node = "urd",   vmid = 1131, ip = "10.0.11.231" }
    idunn = { node = "verd",  vmid = 1132, ip = "10.0.11.232" }
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

# ----------------------------------------------------------------------------
# LXCs 1133/1134/1135 — HAProxy + etcd trio (Urd/Verd/Skuld)
# ----------------------------------------------------------------------------
# Fronts the PostgreSQL cluster. Each LXC runs three services:
#   - etcd: distributed config store for Patroni's leader election
#     (~300-500MB working set, fsync-heavy)
#   - HAProxy: TCP frontend with Patroni REST API leader-detection
#     (checks /master endpoint on each PG node, only the current
#     leader returns 200)
#   - keepalived: VIP `10.0.10.210` floats across the trio, providing
#     a stable connection-string target for all PG consumers
#
# Why etcd co-located on the HAProxy trio rather than on PG nodes or
# in a dedicated etcd cluster:
#   - fsync separation: etcd and PG are both fsync-heavy; co-locating
#     them on PG nodes puts them in competition for disk slots (the
#     2026-05-14 K3s-etcd storm is the reference incident)
#   - failure-domain separation: a PG node death doesn't touch DCS
#     quorum and vice versa
#   - zero additional LXC cost vs the original HAProxy-only trio
#     (HAProxy + keepalived are <100MB; etcd fits comfortably in the
#     remaining headroom on a 2GB LXC)
# See homelab-design.md decision row "Patroni DCS placement".
#
# Each node lives on a different Proxmox host so a single-host
# failure never takes down >1 of either service. Sizing identical
# across nodes per the failover-symmetry rule.
#
# See:
#   - docs/homelab-design.md → "Phase 5g.2" build sequence + decision log
#   - ansible/roles/etcd/README.md (TBD — 5g.2.c)
#   - ansible/roles/haproxy/README.md (TBD — 5g.2.g)
# ----------------------------------------------------------------------------

locals {
  # eth0 on VLAN 11 (HL-ASG-SVC) carries etcd peer + Patroni REST API
  # traffic. eth1 on VLAN 10 (HL-ASG-VIP) hosts the keepalived VIP
  # 10.0.10.210 — VRRP requires all peers L2-adjacent on the VIP's
  # segment, so each node needs a real address there for advertisement
  # source + diagnostics. Default gateway stays on eth0; replies from
  # VLAN 10 source IPs route out eth1 via source-based policy routing
  # installed by the keepalived role (same pattern as K3s workers'
  # VLAN 20 fix — see CLAUDE.md "Networking / multi-homed workers").
  haproxy_etcd_nodes = {
    hlin   = { node = "urd",   vmid = 1133, ip = "10.0.11.233", vlan10_ip = "10.0.10.233" }
    eir    = { node = "verd",  vmid = 1134, ip = "10.0.11.234", vlan10_ip = "10.0.10.234" }
    snotra = { node = "skuld", vmid = 1135, ip = "10.0.11.235", vlan10_ip = "10.0.10.235" }
  }
}

# Throwaway root passwords — Proxmox API requires one to create the
# container, but each LXC is configured for SSH-key-only auth. Never
# used; persisted in local state, which is gitignored.
resource "random_password" "haproxy_etcd_root" {
  for_each = local.haproxy_etcd_nodes

  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "haproxy_etcd" {
  for_each = local.haproxy_etcd_nodes

  description = "HAProxy + etcd + keepalived trio member (${each.key})"

  node_name = each.value.node
  vm_id     = each.value.vmid
  tags      = ["asgard", "lxc", "haproxy-etcd", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  # Sizing is cluster-wide, NOT per-node. Failover symmetry requires
  # identical resources — same rule as the PG nodes. 2GB accommodates
  # etcd (~300-500MB working set + Raft log) + HAProxy (~50MB) +
  # keepalived (~20MB) with headroom for logs/metrics.
  cpu {
    cores = 2
  }

  memory {
    dedicated = 2048    # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 16   # GB — etcd snapshots + WAL + OS
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  network_interface {
    name     = "eth1"
    bridge   = var.lxc_network_bridge
    vlan_id  = 10
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = each.key

    # ip_config blocks are matched to network_interface blocks by
    # declaration order. eth0 carries the default gateway; eth1 has
    # an address only (source-based policy routing handles replies).
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = "10.0.11.1"
      }
    }

    ip_config {
      ipv4 {
        address = "${each.value.vlan10_ip}/24"
      }
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.haproxy_etcd_root[each.key].result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true     # systemd 257 on Debian 13 — see gotchas
  }

  console {
    enabled = true
    type    = "tty"
  }
}

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
# /dev/net/tun passthrough is the reason these LXCs need a different
# Proxmox feature footprint than the other asgard LXCs — unprivileged
# containers cannot get TUN access by default. bpg/proxmox v0.70.1+
# correctly applies device_passthrough at create time (PR #1722); the
# pinned 0.106.0 is well above that.
#
# Tailscale-specific config (apt repo, daemon, authkey from Vault,
# `tailscale up` flags) lives in the Ansible tailscale role (5e.3.e.iv).
# Authkeys are minted in terraform/tailscale/ (5e.3.e.ii) and read by
# Ansible via community.hashi_vault lookup.
#
# See:
#   - docs/homelab-design.md → "Asgard LXCs" table
#   - terraform/tailscale/authkeys.tf
#   - ansible/roles/tailscale/README.md (TBD — 5e.3.e.iv)
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
# used; persisted in local state, which is gitignored.
resource "random_password" "tailscale_root" {
  for_each = local.tailscale_nodes

  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "tailscale" {
  provider = proxmox.root
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
