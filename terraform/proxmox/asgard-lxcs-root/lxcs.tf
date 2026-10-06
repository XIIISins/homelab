# terraform/proxmox/asgard-lxcs-root/lxcs.tf

# ----------------------------------------------------------------------------
# LXCs 1113/1114/1115 — Tailscale subnet routers + exit node
# ----------------------------------------------------------------------------
# Bifrost (1113, urd) + Heimdall (1114, skuld): subnet-router HA pair
# advertising the 10.0.0.0/16 supernet (auto-approved via tailnet ACL
# autoApprovers — see terraform/tailscale/policy.hujson).
#
# Gjallarbru (1115, verd): exit node, advertises IPv4+IPv6 default
# routes (also auto-approved).
#
# Each LXC on a different Proxmox host so a single-host failure never
# takes down >1 advertiser of the supernet — the tailnet picks
# another route holder transparently.
#
# Placement rebalance 2026-07-21: Gjallarbru and Heimdall were swapped
# (Gjallarbru skuld→verd, Heimdall verd→skuld) because Skuld has an
# open hardware fault — repeated instant hard crashes with fatal BERT
# machine-check records, see docs/incidents/2026-07-17-skuld-hard-crashes.md.
# The exit node is a SINGLE advertiser: losing it drops every remote
# client's default route until the node returns. The subnet routers are
# an HA PAIR: losing one is transparent to the tailnet. So the role that
# rides on the unreliable node is a pair member, not the singleton.
# One-TS-node-per-host is preserved. Moved with `pct migrate --restart`
# + `terraform state rm`/`import` (node_name is part of the bpg resource
# ID, so editing it in place would otherwise force a destroy/recreate —
# which would mint a NEW tailnet machine and force every client to
# re-select the exit node).
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
    bifrost    = { node = "urd", vmid = 1113, ip = "10.0.11.213" }
    heimdall   = { node = "skuld", vmid = 1114, ip = "10.0.11.214" }
    gjallarbru = { node = "verd", vmid = 1115, ip = "10.0.11.215" }
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
    dedicated = 512 # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 4 # GB
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
    nesting = true # systemd 257 on Debian 13 — see gotchas
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

# ----------------------------------------------------------------------------
# LXC 1123 - Jellyfin media server with Intel QuickSync (Urd) - Phase 5h
# ----------------------------------------------------------------------------
# Plan: docs/operations/5h-jellyfin.md (steps J0-J6). Transcodes on the Alder Lake iGPU (/dev/dri/renderD128, i915),
# never on the CPU. Lives in THIS module because `device_passthrough` (and `mount = ["nfs"]`) need root@pam ticket
# auth, which the API-token module cannot use (docs/known-issues/lxc-proxmox.md).
#
# Privileged, deliberately: it mounts the Munin media share itself (PBS pattern), so uids map 1:1 to the NAS and any of
# the three identical nodes can run it after a `pct migrate --restart`. An unprivileged container could not mount NFS
# and would need a host bind mount, which pins it to one host's fstab. Mitigations: the media is mounted READ-ONLY,
# Jellyfin runs as the unprivileged `jellyfin` user, ONLY the render node is passed (not card0, not all of /dev/dri),
# and the container is reachable from the LAN and the tailnet only (no tunnel, no port-forward).
#
# Sizing (J0 on 2026-10-06): 4 cores (scans and subtitle extraction; iGPU work does not count), 3 GB RAM + 1 GB swap
# (a cap, not a reservation: ~1 GB steady, ~2 GB during a big scan; Urd has ~7.6 GB available), 16 GB rootfs.
# /var/cache/jellyfin is a separate 40 GB mount point with backup = false: transcode segments and the image cache are
# disposable, and the PBS datastore is at ~81 %. Config, the SQLite database and metadata (rootfs, /var/lib/jellyfin)
# ARE backed up. SQLite never goes on NFS.
#
# gid 993 is the host's `render` group; the container's `render` group is created with the SAME gid by the Ansible
# jellyfin role, so host, Terraform and container agree after any rebuild. The device node name is asserted on every
# host converge (ansible/roles/proxmox-host/tasks/gpu.yml).
#
# Not in a PVE HA group: passthrough plus an in-guest NFS mount means a move is a deliberate
# `pct migrate 1123 <node> --restart` (J5 tests it).
#
# Front door: jellyfin.midgard.xiiisins.com via Traefik (k8s/asgard/apps/jellyfin-ingress) and
# jellyfin-direct.niflheim.xiiisins.com straight to the LXC (terraform/adguard/rewrites.tf), so playback survives a K3s
# outage. Remote access is Tailscale only (decision D-1); Jellyfin must NOT go behind the Cloudflare tunnel.
#
# See: ansible/roles/jellyfin/, ansible/playbooks/asgard-jellyfin.yml
# ----------------------------------------------------------------------------

resource "random_password" "jellyfin_root" {
  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "jellyfin" {
  description = "Jellyfin media server (QuickSync on the Intel iGPU), Phase 5h"

  node_name = "urd"
  vm_id     = 1123
  tags      = ["asgard", "lxc", "jellyfin", "managed-by-terraform"]

  unprivileged  = false
  start_on_boot = true
  started       = true

  cpu {
    cores = 4
  }

  memory {
    dedicated = 3072 # MB
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 16 # GB - OS + /var/lib/jellyfin (config, SQLite, metadata); this part is backed up
  }

  # Transcodes + image cache: local-lvm, NOT backed up, NOT NFS, NOT tmpfs (tmpfs would count against the 3 GB).
  mount_point {
    volume = var.lxc_storage
    size   = "40G"
    path   = "/var/cache/jellyfin"
    backup = false
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = "jellyfin"

    ip_config {
      ipv4 {
        address = "10.0.11.223/24"
        gateway = "10.0.11.1"
      }
    }

    # PVE owns resolv.conf (see Ratatoskr / Gna); baseline_manage_resolv_conf=false in group_vars/media_server.yml.
    dns {
      domain  = "niflheim.xiiisins.com"
      servers = ["10.0.10.200", "10.0.254.1"]
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.jellyfin_root.result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true # systemd 257 on Debian 13 - see gotchas
    mount   = ["nfs"]
  }

  # Only the render node, not card0 and not the whole of /dev/dri. gid = the host's `render` group (993).
  device_passthrough {
    path = "/dev/dri/renderD128"
    gid  = 993
    mode = "0660"
  }

  console {
    enabled = true
    type    = "tty"
  }

  # bpg/proxmox doesn't return template_file_id or user_account from the API on read.
  lifecycle {
    ignore_changes = [
      operating_system[0].template_file_id,
      initialization[0].user_account,
    ]
  }
}
