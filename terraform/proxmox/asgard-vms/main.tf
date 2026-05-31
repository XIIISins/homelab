# terraform/proxmox/asgard-vms/main.tf
#
# Standalone (non-K3s) Asgard VMs. First tenant: Frigg — the always-on
# control-node "watchtower" (Phase 6 Stage 2). Hosts `claude
# remote-control`, runs the full TF/Ansible/Flux cycle via its own Vault
# AppRole, reachable from anywhere via Tailscale. A distinct module from
# asgard-k3s (cluster VMs) + asgard-lxcs (LXCs).
#
# HA: unlike every other VM/LXC (local-lvm, node-pinned), Frigg's disk
# lives on the shared `munin-nfs` datastore so Proxmox ha-manager can
# cold-restart it on a surviving node (≥2/3 quorum). The "VM disks =
# local-lvm" invariant is PG-latency-specific and doesn't apply to a
# control node. See open-questions.md Phase 6 Stage 2.

locals {
  # Non-K3s asgard VM band = 2900+ (K3s VMs occupy 2001-2103).
  vms = {
    frigg = {
      node        = "verd"        # initial home; ha-manager may relocate on failure
      vmid        = 2900
      ip          = "10.0.11.30"
      gateway     = "10.0.11.1"   # VLAN 11 (HL-ASG-SVC)
      template_id = 10010         # debian-13-cloud (resides on verd)
      cores       = 2
      memory      = 6144          # comfortable for claude + light TF/Ansible builds;
                                  # leaves HA failover headroom on a 32GB node already
                                  # running a 16GB worker + 4GB CP. Bump if builds need it.
      disk_size   = 40
    }
  }
}

resource "proxmox_virtual_environment_vm" "this" {
  for_each = local.vms

  name      = each.key
  node_name = each.value.node
  vm_id     = each.value.vmid

  clone {
    vm_id     = each.value.template_id
    node_name = each.value.node # template 10010 lives on the same node (verd)
    full      = true
  }

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory
  }

  # Disk on the shared NFS datastore (the HA prerequisite). qcow2 (thin +
  # snapshots) on the file-backed NFS store; HDD-backed (DS223J RAID1) so
  # ssd=false. discard=on for thin reclaim.
  disk {
    datastore_id = "munin-nfs"
    size         = each.value.disk_size
    interface    = "scsi0"
    discard      = "on"
    ssd          = false
    file_format  = "qcow2"
  }

  network_device {
    bridge  = "vmbr0"
    vlan_id = 11
  }

  initialization {
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = each.value.gateway
      }
    }
    user_account {
      username = "ansible"
      keys     = [var.ssh_public_key]
    }
  }

  # Debian genericcloud ships no qemu-guest-agent, so enabling the agent
  # here would make the provider block waiting for it at create. Leave it
  # off — the frigg Ansible role installs + enables qemu-guest-agent; flip
  # to true on a later apply once it's running (gives Proxmox graceful
  # shutdown + agent-reported IPs).
  agent {
    enabled = false
  }
}

# HA: keep Frigg started; ha-manager cold-restarts it on a surviving node
# if its current host fails (disk is on shared munin-nfs, reachable from
# all 3 nodes). No hagroup → eligible on any cluster node, which meets the
# "lives as long as Proxmox holds quorum" goal; add a node-preference
# hagroup later if desired.
resource "proxmox_haresource" "this" {
  for_each = local.vms

  resource_id = "vm:${each.value.vmid}"
  state       = "started"
  comment     = "Terraform (asgard-vms) — ${each.key} watchtower"

  depends_on = [proxmox_virtual_environment_vm.this]
}
