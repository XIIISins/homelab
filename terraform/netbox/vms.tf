# terraform/netbox/vms.tf
#
# All 20 NetBox VirtualMachine records — the 6 K3s VMs (3 CPs + 3
# workers) AND the 14 LXCs (NetBox doesn't distinguish VM from LXC;
# they're all VirtualMachine, differentiated by `role`).
#
# Sources of truth (cross-reference, don't duplicate):
#   - K3s VMs: terraform/proxmox/asgard-k3s/main.tf locals.{control_planes,workers}
#   - IaC LXCs: terraform/proxmox/asgard-lxcs/lxcs.tf locals.{postgres,haproxy_etcd,tailscale}_nodes + factorio
#   - PBS LXC: no IaC, hand-managed on Skuld
#   - AGH LXCs (Saga/Mimir/Kvasir): no IaC yet (Phase 5b.2 pending)
#
# Three NetBox-side placement corrections land with this commit (the
# manual 5i.e UI import had them wrong vs IaC reality):
#   - PBS: skuld (was: verd)
#   - idunn: verd (was: skuld)
#   - Gjallarbru: skuld (was: verd)
#
# cpu + memory get filled in everywhere from the IaC spec. Worker
# memory is 8192 (post 2026-05-24 incident — see commit d690381).

locals {
  vms = {
    # ── Asgard K3s control planes (RHEL 9, 2vCPU/4GB/10GB) ─────────
    gondul = { vmid = "2001", role = "k3s-control-plane", device = "urd", cpu = 2, memory = 4096, primary_iface = "eth0" }
    hlokk  = { vmid = "2002", role = "k3s-control-plane", device = "verd", cpu = 2, memory = 4096, primary_iface = "eth0" }
    sigrun = { vmid = "2003", role = "k3s-control-plane", device = "skuld", cpu = 2, memory = 4096, primary_iface = "eth0" }

    # ── Asgard K3s workers (RHEL 9, 2vCPU/8GB/15GB, multi-homed) ───
    einherjar-urd   = { vmid = "2101", role = "k3s-worker", device = "urd", cpu = 2, memory = 8192, primary_iface = "eth0" }
    einherjar-verd  = { vmid = "2102", role = "k3s-worker", device = "verd", cpu = 2, memory = 8192, primary_iface = "eth0" }
    einherjar-skuld = { vmid = "2103", role = "k3s-worker", device = "skuld", cpu = 2, memory = 8192, primary_iface = "eth0" }

    # ── PBS (Proxmox Backup Server, privileged LXC, no IaC) ────────
    PBS = { vmid = "1101", role = "backup-server", device = "skuld", cpu = 2, memory = 2048, primary_iface = "eth0" }

    # ── Hugin — Zabbix server (LXC 1102, Phase 7c) ─────────────────
    hugin = { vmid = "1102", role = "monitoring", device = "urd", cpu = 2, memory = 4096, primary_iface = "eth0" }

    # ── AGH trio (Saga/Mimir/Kvasir, Phase 5b.2) ──────────────────
    # VMIDs from network.md: 1110/1111/1112. Sizing matches the
    # adguard_nodes locals in terraform/proxmox/asgard-lxcs/lxcs.tf
    # (1 vCPU / 512MB / 4GB disk). Apply this update AFTER the 5b.2
    # LXC deploy lands so NetBox + reality agree.
    saga   = { vmid = "1110", role = "dns", device = "urd", cpu = 1, memory = 512, primary_iface = "eth0" }
    Mimir  = { vmid = "1111", role = "dns", device = "verd", cpu = 1, memory = 512, primary_iface = "eth0" }
    kvasir = { vmid = "1112", role = "dns", device = "skuld", cpu = 1, memory = 512, primary_iface = "eth0" }

    # ── Tailscale trio (subnet routers Bifrost/Heimdall + exit Gjallarbru) ─
    Bifrost    = { vmid = "1113", role = "tailscale-gateway", device = "urd", cpu = 1, memory = 512, primary_iface = "eth0" }
    Heimdall   = { vmid = "1114", role = "tailscale-gateway", device = "verd", cpu = 1, memory = 512, primary_iface = "eth0" }
    Gjallarbru = { vmid = "1115", role = "tailscale-gateway", device = "skuld", cpu = 1, memory = 512, primary_iface = "eth0" }

    # ── Factorio LXC ───────────────────────────────────────────────
    factorio = { vmid = "1120", role = "game-server", device = "urd", cpu = 4, memory = 8192, primary_iface = "eth0" }

    # ── PostgreSQL trio (Patroni) ──────────────────────────────────
    Fulla = { vmid = "1130", role = "db", device = "skuld", cpu = 2, memory = 4096, primary_iface = "eth0" }
    vor   = { vmid = "1131", role = "db", device = "urd", cpu = 2, memory = 4096, primary_iface = "eth0" }
    idunn = { vmid = "1132", role = "db", device = "verd", cpu = 2, memory = 4096, primary_iface = "eth0" }

    # ── HAProxy+etcd+keepalived trio (Patroni DCS + PG VIP frontend) ─
    hlin   = { vmid = "1133", role = "service-frontend", device = "urd", cpu = 2, memory = 2048, primary_iface = "eth0" }
    eir    = { vmid = "1134", role = "service-frontend", device = "verd", cpu = 2, memory = 2048, primary_iface = "eth0" }
    snotra = { vmid = "1135", role = "service-frontend", device = "skuld", cpu = 2, memory = 2048, primary_iface = "eth0" }
  }

  # Flat interface map keyed by "<vm>.<iface>". Workers + HAProxy/etcd
  # trio are multi-homed (eth1 on the VIP segment); everyone else has
  # just eth0.
  vm_interfaces = {
    # K3s CPs (single-homed VLAN 21)
    "gondul.eth0" = { vm = "gondul", name = "eth0", ip = "10.0.21.11/24" }
    "hlokk.eth0"  = { vm = "hlokk", name = "eth0", ip = "10.0.21.12/24" }
    "sigrun.eth0" = { vm = "sigrun", name = "eth0", ip = "10.0.21.13/24" }

    # K3s workers (multi-homed: eth0 VLAN 21 K3s, eth1 VLAN 20 MetalLB L2)
    "einherjar-urd.eth0"   = { vm = "einherjar-urd", name = "eth0", ip = "10.0.21.21/24" }
    "einherjar-urd.eth1"   = { vm = "einherjar-urd", name = "eth1", ip = "10.0.20.201/24" }
    "einherjar-verd.eth0"  = { vm = "einherjar-verd", name = "eth0", ip = "10.0.21.22/24" }
    "einherjar-verd.eth1"  = { vm = "einherjar-verd", name = "eth1", ip = "10.0.20.202/24" }
    "einherjar-skuld.eth0" = { vm = "einherjar-skuld", name = "eth0", ip = "10.0.21.23/24" }
    "einherjar-skuld.eth1" = { vm = "einherjar-skuld", name = "eth1", ip = "10.0.20.203/24" }

    # PBS + Hugin (Zabbix) + AGH trio + Tailscale trio + Factorio + PG trio (single-homed VLAN 11)
    "PBS.eth0"        = { vm = "PBS", name = "eth0", ip = "10.0.11.20/24" }
    "hugin.eth0"      = { vm = "hugin", name = "eth0", ip = "10.0.11.21/24" }
    "saga.eth0"       = { vm = "saga", name = "eth0", ip = "10.0.11.201/24" }
    "Mimir.eth0"      = { vm = "Mimir", name = "eth0", ip = "10.0.11.202/24" }
    "kvasir.eth0"     = { vm = "kvasir", name = "eth0", ip = "10.0.11.203/24" }
    "Bifrost.eth0"    = { vm = "Bifrost", name = "eth0", ip = "10.0.11.213/24" }
    "Heimdall.eth0"   = { vm = "Heimdall", name = "eth0", ip = "10.0.11.214/24" }
    "Gjallarbru.eth0" = { vm = "Gjallarbru", name = "eth0", ip = "10.0.11.215/24" }
    "factorio.eth0"   = { vm = "factorio", name = "eth0", ip = "10.0.11.220/24" }
    "Fulla.eth0"      = { vm = "Fulla", name = "eth0", ip = "10.0.11.230/24" }
    "vor.eth0"        = { vm = "vor", name = "eth0", ip = "10.0.11.231/24" }
    "idunn.eth0"      = { vm = "idunn", name = "eth0", ip = "10.0.11.232/24" }

    # HAProxy/etcd trio (multi-homed: eth0 VLAN 11 service + peer, eth1 VLAN 10 VIP/VRRP)
    "hlin.eth0"   = { vm = "hlin", name = "eth0", ip = "10.0.11.233/24" }
    "hlin.eth1"   = { vm = "hlin", name = "eth1", ip = "10.0.10.233/24" }
    "eir.eth0"    = { vm = "eir", name = "eth0", ip = "10.0.11.234/24" }
    "eir.eth1"    = { vm = "eir", name = "eth1", ip = "10.0.10.234/24" }
    "snotra.eth0" = { vm = "snotra", name = "eth0", ip = "10.0.11.235/24" }
    "snotra.eth1" = { vm = "snotra", name = "eth1", ip = "10.0.10.235/24" }
  }

  # Import IDs sourced from /api/virtualization/virtual-machines/ +
  # /api/virtualization/interfaces/ + /api/ipam/ip-addresses/ at
  # retrofit time. The IP-ID map is keyed on the same "<vm>.<iface>"
  # composite as vm_interfaces, since one IP-per-interface in our model.
  vm_import_ids = {
    gondul          = "1"
    hlokk           = "2"
    sigrun          = "3"
    einherjar-urd   = "4"
    einherjar-verd  = "5"
    einherjar-skuld = "6"
    PBS             = "7"
    saga            = "8"
    Mimir           = "9"
    kvasir          = "10"
    Bifrost         = "11"
    Heimdall        = "12"
    Gjallarbru      = "13"
    factorio        = "14"
    Fulla           = "15"
    vor             = "16"
    idunn           = "17"
    hlin            = "18"
    eir             = "19"
    snotra          = "20"
  }
  vm_interface_import_ids = {
    "gondul.eth0"          = "1"
    "hlokk.eth0"           = "2"
    "sigrun.eth0"          = "6"
    "einherjar-urd.eth0"   = "4"
    "einherjar-urd.eth1"   = "9"
    "einherjar-verd.eth0"  = "5"
    "einherjar-verd.eth1"  = "10"
    "einherjar-skuld.eth0" = "3"
    "einherjar-skuld.eth1" = "8"
    "PBS.eth0"             = "11"
    "saga.eth0"            = "12"
    "Mimir.eth0"           = "13"
    "kvasir.eth0"          = "14"
    "Bifrost.eth0"         = "17"
    "Heimdall.eth0"        = "15"
    "Gjallarbru.eth0"      = "16"
    "factorio.eth0"        = "18"
    "Fulla.eth0"           = "19"
    "vor.eth0"             = "20"
    "idunn.eth0"           = "21"
    "hlin.eth0"            = "24"
    "hlin.eth1"            = "25"
    "eir.eth0"             = "22"
    "eir.eth1"             = "23"
    "snotra.eth0"          = "26"
    "snotra.eth1"          = "27"
  }
  vm_ip_import_ids = {
    "gondul.eth0"          = "1"
    "hlokk.eth0"           = "2"
    "sigrun.eth0"          = "4"
    "einherjar-urd.eth0"   = "5"
    "einherjar-urd.eth1"   = "9"
    "einherjar-verd.eth0"  = "3"
    "einherjar-verd.eth1"  = "10"
    "einherjar-skuld.eth0" = "6"
    "einherjar-skuld.eth1" = "8"
    "PBS.eth0"             = "11"
    "saga.eth0"            = "12"
    "Mimir.eth0"           = "13"
    "kvasir.eth0"          = "14"
    "Bifrost.eth0"         = "17"
    "Heimdall.eth0"        = "15"
    "Gjallarbru.eth0"      = "16"
    "factorio.eth0"        = "18"
    "Fulla.eth0"           = "19"
    "vor.eth0"             = "20"
    "idunn.eth0"           = "21"
    "hlin.eth0"            = "24"
    "hlin.eth1"            = "25"
    "eir.eth0"             = "22"
    "eir.eth1"             = "23"
    "snotra.eth0"          = "26"
    "snotra.eth1"          = "27"
  }
}

resource "netbox_virtual_machine" "this" {
  for_each = local.vms

  name       = each.key
  cluster_id = netbox_cluster.niflheim.id
  role_id    = netbox_device_role.this[each.value.role].id
  device_id  = netbox_device.this[each.value.device].id
  site_id    = netbox_site.home.id
  status     = "active"
  vcpus      = each.value.cpu
  memory_mb  = each.value.memory

  # VMID cross-reference to the Proxmox vmid. Empty string for the
  # AGH trio (Saga/Mimir/Kvasir — no IaC vmid until Phase 5b.2).
  # Field is type=text (see custom_fields.tf) so the provider's
  # Map(String) serialization works without coercion drama.
  custom_fields = {
    VMID = each.value.vmid
  }

  depends_on = [netbox_custom_field.vmid]
}

import {
  for_each = local.vm_import_ids
  to       = netbox_virtual_machine.this[each.key]
  id       = each.value
}

resource "netbox_interface" "this" {
  for_each = local.vm_interfaces

  virtual_machine_id = netbox_virtual_machine.this[each.value.vm].id
  name               = each.value.name
  enabled            = true
}

import {
  for_each = local.vm_interface_import_ids
  to       = netbox_interface.this[each.key]
  id       = each.value
}

resource "netbox_ip_address" "vm" {
  for_each = local.vm_interfaces

  ip_address                   = each.value.ip
  status                       = "active"
  virtual_machine_interface_id = netbox_interface.this[each.key].id
}

import {
  for_each = local.vm_ip_import_ids
  to       = netbox_ip_address.vm[each.key]
  id       = each.value
}

# Primary IPv4 binding: each VM's primary interface (per the
# primary_iface attribute in local.vms) gets bound as primary_ip4
# on the VM via the dedicated netbox_virtual_machine_primary_ip
# resource. Multi-homed VMs (workers, HAProxy/etcd trio) get
# their eth0 as primary; the eth1 IP is informational only.
resource "netbox_primary_ip" "this" {
  for_each = local.vms

  virtual_machine_id = netbox_virtual_machine.this[each.key].id
  ip_address_id      = netbox_ip_address.vm["${each.key}.${each.value.primary_iface}"].id
}
