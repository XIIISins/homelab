locals {
  control_planes = {
    gondul = { node = "urd",   vmid = 2001, ip = "10.0.21.11", template_node = "verd",  template_id = 10002 }
    hlokk  = { node = "verd",  vmid = 2002, ip = "10.0.21.12", template_node = "verd",  template_id = 10002 }
    sigrun = { node = "skuld", vmid = 2003, ip = "10.0.21.13", template_node = "skuld", template_id = 10004 }
  }

  workers = {
    einherjar-urd   = { node = "urd",   vmid = 2101, ip = "10.0.21.21", template_node = "verd",  template_id = 10002 }
    einherjar-verd  = { node = "verd",  vmid = 2102, ip = "10.0.21.22", template_node = "verd",  template_id = 10002 }
    einherjar-skuld = { node = "skuld", vmid = 2103, ip = "10.0.21.23", template_node = "skuld", template_id = 10004 }
  }
}

resource "proxmox_virtual_environment_vm" "control_plane" {
  for_each  = local.control_planes

  name      = each.key
  node_name = each.value.node
  vm_id     = each.value.vmid

  clone {
    vm_id     = each.value.template_id
    node_name = each.value.template_node
    full      = true
  }

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
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
  for_each  = local.workers

  name      = each.key
  node_name = each.value.node
  vm_id     = each.value.vmid

  clone {
    vm_id     = each.value.template_id
    node_name = each.value.template_node
    full      = true
  }

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
  }

  disk {
    datastore_id = "local-lvm"
    size         = 30
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