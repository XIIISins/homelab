# terraform/netbox/devices.tf
#
# Physical devices (5): the three Proxmox hosts (Urd, verd, skuld —
# capitalization matches what got hand-entered in 5i.e and stays that
# way), the NAS (Munin), and the firewall (UCG-Ultra). Each gets one
# interface + one IP address + a primary_ip4 binding.
#
# NIC names matter: Cubi hosts use predictable `enp45s0`; the Beelink
# (Skuld) uses vendor-custom `nic0`. See CLAUDE.md "Proxmox same-node
# hardware refresh changes NIC names" gotcha — this difference is the
# class that caught us when Urd was refreshed from Beelink→Cubi.
#
# The 5 devices were imported via the web UI in Phase 5i.e but their
# interfaces + IPs were not. So Stage 4 mixes: imports for the device
# rows themselves, fresh creates for interfaces/IPs/primary bindings.

locals {
  devices = {
    urd = {
      name           = "urd"
      role           = "proxmox-host"
      device_type    = "msi_cubi"
      interface_name = "enp45s0"
      ip             = "10.0.254.11/24"
      description    = "Proxmox host. Hardware refresh Phase 4a (2026-05-21) — was Beelink N5095, now MSI Cubi i3-1215u/32GB. Proxmox installed manually (no IaC)."
    }
    verd = {
      name           = "verd"
      role           = "proxmox-host"
      device_type    = "msi_cubi"
      interface_name = "enp45s0"
      ip             = "10.0.254.12/24"
      description    = "Proxmox host. Hardware refresh Phase 4c (2026-05-23) — was Beelink, now MSI Cubi i3-1215u/32GB. Proxmox installed manually (no IaC)."
    }
    skuld = {
      name           = "skuld"
      role           = "proxmox-host"
      device_type    = "beelink_mini_s12"
      interface_name = "nic0" # Beelink vendor-custom UEFI NIC name (NOT enp45s0)
      ip             = "10.0.254.13/24"
      description    = "Proxmox host. Beelink Mini S12 Pro (N100/16GB), the outlier vs Urd+Verd. Hosts PBS (1101) + Sigrún CP + several LXCs."
    }
    munin = {
      name           = "Munin"
      role           = "nas"
      device_type    = "synology_ds223j"
      interface_name = "eth0"
      ip             = "10.0.254.20/24"
      description    = "Synology NAS. RAID1 3.5TB. iSCSI provider for Synology CSI in asgard K3s. Tailscale subnet router (5e.3.f)."
    }
    ucg_ultra = {
      name           = "UCG-Ultra"
      role           = "firewall"
      device_type    = "ubiquiti_ucg_ultra"
      interface_name = "eth0"
      ip             = "10.0.254.1/24"
      description    = "Sole firewall policy boundary. KPN Experia (DMZ) → UCG WAN. VLAN aggregator. Posture: Internal→Any allow / External→Internal allow-return / Any→Any deny (last)."
    }
  }

  # Import IDs for devices (created in 5i.e). NIC capitalization on
  # Urd matches NetBox hand-entered casing.
  device_import_ids = {
    urd       = "1"
    verd      = "2"
    skuld     = "3"
    munin     = "6"
    ucg_ultra = "7"
  }
}

resource "netbox_device" "this" {
  for_each = local.devices

  name           = each.value.name
  role_id        = netbox_device_role.this[each.value.role].id
  device_type_id = netbox_device_type.this[each.value.device_type].id
  site_id        = netbox_site.home.id
  status         = "active"
  description    = each.value.description

  # Proxmox hosts (role=proxmox-host) are also virtualization-cluster
  # members — they host the niflheim VMs. NetBox 4.x lets Device link
  # to a Cluster to express that "this device runs VMs in cluster X",
  # which makes per-host VM lists work in the cluster view.
  cluster_id = each.value.role == "proxmox-host" ? netbox_cluster.niflheim.id : null

  # Phase 5h.3 — Ansible inventory group via tag. role → group:
  #   proxmox-host → ansible:proxmox
  #   nas          → ansible:synology
  #   firewall     → (no Ansible — UCG is config-via-UI only)
  tags = compact([
    each.value.role == "proxmox-host" ? "ansible:proxmox" : "",
    each.value.role == "nas" ? "ansible:synology" : "",
  ])

  depends_on = [netbox_tag.this]
}

import {
  for_each = local.device_import_ids
  to       = netbox_device.this[each.key]
  id       = each.value
}

resource "netbox_device_interface" "primary" {
  for_each = local.devices

  device_id = netbox_device.this[each.key].id
  name      = each.value.interface_name
  type      = "1000base-t" # all hosts are 1 GbE
  enabled   = true
}

resource "netbox_ip_address" "device" {
  for_each = local.devices

  ip_address          = each.value.ip
  status              = "active"
  device_interface_id = netbox_device_interface.primary[each.key].id
}

resource "netbox_device_primary_ip" "this" {
  for_each = local.devices

  device_id     = netbox_device.this[each.key].id
  ip_address_id = netbox_ip_address.device[each.key].id
}
