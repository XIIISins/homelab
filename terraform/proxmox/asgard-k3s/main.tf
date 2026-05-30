locals {
  control_planes = {
    gondul = { node = "urd", vmid = 2001, ip = "10.0.21.11", template_node = "urd", template_id = 10006, cores = 2, memory = 4096 }
    hlokk  = { node = "verd", vmid = 2002, ip = "10.0.21.12", template_node = "verd", template_id = 10002, cores = 2, memory = 4096 }
    sigrun = { node = "skuld", vmid = 2003, ip = "10.0.21.13", template_node = "skuld", template_id = 10004, cores = 2, memory = 4096 }
  }

  # os_disk_ssd: flip the scsi0 OS disk to SSD emulation (rotational=0). Validated
  # on einherjar-urd first as a canary (in-place flag change, no data touched);
  # now true on all workers. The CPs still need it — done in a separate quorum-safe
  # pass (terraform/proxmox/asgard-k3s control_plane disk). NVMe-backed, so accurate.
  workers = {
    einherjar-urd   = { node = "urd", vmid = 2101, ip = "10.0.21.21", ip_vlan20 = "10.0.20.201", template_node = "urd", template_id = 10006, cores = 2, memory = 16384, os_disk_ssd = true }
    einherjar-verd  = { node = "verd", vmid = 2102, ip = "10.0.21.22", ip_vlan20 = "10.0.20.202", template_node = "verd", template_id = 10002, cores = 2, memory = 16384, os_disk_ssd = true }
    einherjar-skuld = { node = "skuld", vmid = 2103, ip = "10.0.21.23", ip_vlan20 = "10.0.20.203", template_node = "skuld", template_id = 10004, cores = 2, memory = 16384, os_disk_ssd = true }
  }
}

resource "proxmox_virtual_environment_vm" "control_plane" {
  for_each = local.control_planes

  name      = each.key
  node_name = each.value.node
  vm_id     = each.value.vmid

  clone {
    vm_id     = each.value.template_id
    node_name = each.value.template_node
    full      = true
  }

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory
  }

  disk {
    datastore_id = "local-lvm"
    size         = 10
    interface    = "scsi0"
    discard      = "on"
  }

  network_device {
    bridge  = "vmbr0"
    vlan_id = 21
  }

  initialization {
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = "10.0.21.1"
      }
    }
    user_account {
      username = "ansible"
      keys     = [var.ssh_public_key]
    }
  }

  agent {
    enabled = true
  }
}

resource "proxmox_virtual_environment_vm" "worker" {
  for_each = local.workers

  name      = each.key
  node_name = each.value.node
  vm_id     = each.value.vmid

  clone {
    vm_id     = each.value.template_id
    node_name = each.value.template_node
    full      = true
  }

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory
  }

  disk {
    datastore_id = "local-lvm"
    size         = 30
    interface    = "scsi0"
    discard      = "on"
    ssd          = each.value.os_disk_ssd # staged per-node (canary on urd)
  }

  # Dedicated node-local data disk for local-path-provisioner — separates
  # persistent stateful data from the OS/ephemeral root disk (detach + reattach
  # to preserve data on host/VM rebuild). Formatted + mounted at /data by the
  # local-path-disk Ansible role. Thin on local-lvm (cap, not reservation).
  # discard + ssd: TRIM passthrough to LVM-thin + non-rotational flag (NVMe).
  disk {
    datastore_id = "local-lvm"
    size         = 50
    interface    = "scsi1"
    discard      = "on"
    ssd          = true
  }

  network_device {
    bridge  = "vmbr0"
    vlan_id = 21
  }

  network_device {
    bridge  = "vmbr0"
    vlan_id = 20
  }

  initialization {
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = "10.0.21.1"
      }
    }
    ip_config {
      ipv4 {
        address = "${each.value.ip_vlan20}/24"
      }
    }
    user_account {
      username = "ansible"
      keys     = [var.ssh_public_key]
    }
  }

  agent {
    enabled = true
  }
}
