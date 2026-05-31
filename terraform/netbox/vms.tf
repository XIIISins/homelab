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
  # role → ansible-inventory-group mapping. Drives the `ansible:<group>`
  # tag that lands on each VM (Phase 5h.3). NetBox `role` and Ansible
  # inventory group are conceptually different — role says "what kind
  # of thing is this", group says "which playbook converges it" — but
  # they map 1:1 in this homelab. If they diverge later (e.g. two PG
  # roles, one consumer group), promote `ansible_group` to a per-VM
  # field instead of computing from role.
  ansible_group_for_role = {
    "k3s-control-plane" = "k3s-cp"
    "k3s-worker"        = "k3s-worker"
    "backup-server"     = "pbs"
    "monitoring"        = "zabbix"
    "notifications"     = "apprise"
    "dns"               = "adguard"
    "tailscale-gateway" = "tailscale"
    "game-server"       = "gameserver"
    "db"                = "postgres"
    "service-frontend"  = "haproxy-etcd"
    "control-node"      = "control"
  }

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
    pbs = { vmid = "1101", role = "backup-server", device = "skuld", cpu = 2, memory = 2048, primary_iface = "eth0" }

    # ── Hugin — Zabbix server (LXC 1102, Phase 7c) ─────────────────
    hugin = { vmid = "1102", role = "monitoring", device = "urd", cpu = 2, memory = 4096, primary_iface = "eth0" }

    # ── Hermod — Notifications hub (LXC 1103, Phase 5h.2) ──────────
    # New `notifications` role (see roles.tf). No import_id below —
    # this VM is being created fresh, not retrofitted.
    hermod = { vmid = "1103", role = "notifications", device = "verd", cpu = 1, memory = 512, primary_iface = "eth0" }

    # ── AGH trio (Saga/Mimir/Kvasir, Phase 5b.2) ──────────────────
    # VMIDs from network.md: 1110/1111/1112. Sizing matches the
    # adguard_nodes locals in terraform/proxmox/asgard-lxcs/lxcs.tf
    # (1 vCPU / 512MB / 4GB disk). Apply this update AFTER the 5b.2
    # LXC deploy lands so NetBox + reality agree.
    saga   = { vmid = "1110", role = "dns", device = "urd", cpu = 1, memory = 512, primary_iface = "eth0" }
    mimir  = { vmid = "1111", role = "dns", device = "verd", cpu = 1, memory = 512, primary_iface = "eth0" }
    kvasir = { vmid = "1112", role = "dns", device = "skuld", cpu = 1, memory = 512, primary_iface = "eth0" }

    # ── Tailscale trio (subnet routers Bifrost/Heimdall + exit Gjallarbru) ─
    bifrost    = { vmid = "1113", role = "tailscale-gateway", device = "urd", cpu = 1, memory = 512, primary_iface = "eth0" }
    heimdall   = { vmid = "1114", role = "tailscale-gateway", device = "verd", cpu = 1, memory = 512, primary_iface = "eth0" }
    gjallarbru = { vmid = "1115", role = "tailscale-gateway", device = "skuld", cpu = 1, memory = 512, primary_iface = "eth0" }

    # ── Factorio LXC ───────────────────────────────────────────────
    factorio = { vmid = "1120", role = "game-server", device = "urd", cpu = 4, memory = 8192, primary_iface = "eth0" }

    # ── PostgreSQL trio (Patroni) ──────────────────────────────────
    fulla = { vmid = "1130", role = "db", device = "skuld", cpu = 2, memory = 4096, primary_iface = "eth0" }
    vor   = { vmid = "1131", role = "db", device = "urd", cpu = 2, memory = 4096, primary_iface = "eth0" }
    idunn = { vmid = "1132", role = "db", device = "verd", cpu = 2, memory = 4096, primary_iface = "eth0" }

    # ── HAProxy+etcd+keepalived trio (Patroni DCS + PG VIP frontend) ─
    hlin   = { vmid = "1133", role = "service-frontend", device = "urd", cpu = 2, memory = 2048, primary_iface = "eth0" }
    eir    = { vmid = "1134", role = "service-frontend", device = "verd", cpu = 2, memory = 2048, primary_iface = "eth0" }
    snotra = { vmid = "1135", role = "service-frontend", device = "skuld", cpu = 2, memory = 2048, primary_iface = "eth0" }

    # ── Frigg — control-node watchtower (VM 2900, Phase 6 Stage 2) ──
    # Non-K3s standalone VM (terraform/proxmox/asgard-vms), HA-on-NFS.
    # No import_id — created fresh on first apply.
    frigg = { vmid = "2900", role = "control-node", device = "verd", cpu = 2, memory = 6144, primary_iface = "eth0" }
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
    "pbs.eth0"        = { vm = "pbs", name = "eth0", ip = "10.0.11.20/24" }
    "hugin.eth0"      = { vm = "hugin", name = "eth0", ip = "10.0.11.21/24" }
    "hermod.eth0"     = { vm = "hermod", name = "eth0", ip = "10.0.11.22/24" }
    "saga.eth0"       = { vm = "saga", name = "eth0", ip = "10.0.11.201/24" }
    "mimir.eth0"      = { vm = "mimir", name = "eth0", ip = "10.0.11.202/24" }
    "kvasir.eth0"     = { vm = "kvasir", name = "eth0", ip = "10.0.11.203/24" }
    "bifrost.eth0"    = { vm = "bifrost", name = "eth0", ip = "10.0.11.213/24" }
    "heimdall.eth0"   = { vm = "heimdall", name = "eth0", ip = "10.0.11.214/24" }
    "gjallarbru.eth0" = { vm = "gjallarbru", name = "eth0", ip = "10.0.11.215/24" }
    "factorio.eth0"   = { vm = "factorio", name = "eth0", ip = "10.0.11.220/24" }
    "fulla.eth0"      = { vm = "fulla", name = "eth0", ip = "10.0.11.230/24" }
    "vor.eth0"        = { vm = "vor", name = "eth0", ip = "10.0.11.231/24" }
    "idunn.eth0"      = { vm = "idunn", name = "eth0", ip = "10.0.11.232/24" }

    # HAProxy/etcd trio (multi-homed: eth0 VLAN 11 service + peer, eth1 VLAN 10 VIP/VRRP)
    "hlin.eth0"   = { vm = "hlin", name = "eth0", ip = "10.0.11.233/24" }
    "hlin.eth1"   = { vm = "hlin", name = "eth1", ip = "10.0.10.233/24" }
    "eir.eth0"    = { vm = "eir", name = "eth0", ip = "10.0.11.234/24" }
    "eir.eth1"    = { vm = "eir", name = "eth1", ip = "10.0.10.234/24" }
    "snotra.eth0" = { vm = "snotra", name = "eth0", ip = "10.0.11.235/24" }
    "snotra.eth1" = { vm = "snotra", name = "eth1", ip = "10.0.10.235/24" }

    # Frigg — control-node watchtower (single-homed VLAN 11)
    "frigg.eth0" = { vm = "frigg", name = "eth0", ip = "10.0.11.30/24" }
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
    pbs             = "7"
    saga            = "8"
    mimir           = "9"
    kvasir          = "10"
    bifrost         = "11"
    heimdall        = "12"
    gjallarbru      = "13"
    factorio        = "14"
    fulla           = "15"
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
    "pbs.eth0"             = "11"
    "saga.eth0"            = "12"
    "mimir.eth0"           = "13"
    "kvasir.eth0"          = "14"
    "bifrost.eth0"         = "17"
    "heimdall.eth0"        = "15"
    "gjallarbru.eth0"      = "16"
    "factorio.eth0"        = "18"
    "fulla.eth0"           = "19"
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
    "pbs.eth0"             = "11"
    "saga.eth0"            = "12"
    "mimir.eth0"           = "13"
    "kvasir.eth0"          = "14"
    "bifrost.eth0"         = "17"
    "heimdall.eth0"        = "15"
    "gjallarbru.eth0"      = "16"
    "factorio.eth0"        = "18"
    "fulla.eth0"           = "19"
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

  # Phase 5h.3 — Ansible inventory group via tag. The dynamic
  # inventory plugin reads tags via `group_by: tag` (or its richer
  # `keyed_groups` variant) and projects every `ansible:<group>` tag
  # as an Ansible group named `<group>`. Computed from `role` via the
  # local map above so the tag stays consistent with the NetBox role.
  tags = ["ansible:${local.ansible_group_for_role[each.value.role]}"]

  depends_on = [netbox_custom_field.vmid, netbox_tag.this]
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

# State rebinding: the 6 VMs below were originally registered with
# capital-first-letter keys (matching the Norse display names) but the
# rest of the codebase — static hosts.yml, group_vars dict keys, role
# conventions — uses lowercase. NetBox-as-inventory-source returned
# capital `inventory_hostname` for these hosts, which broke any role
# dereferencing `keepalived_priorities[inventory_hostname]` etc.
# (Surfaced 2026-05-27 in Semaphore drift-check run #32: Mimir failed
# on the keepalived dict lookup while saga/kvasir succeeded.)
#
# Lowercasing the local-map keys above changes the TF resource address
# from e.g. netbox_virtual_machine.this["Mimir"] to
# netbox_virtual_machine.this["mimir"]; the `moved` blocks below
# re-key state in place so the apply is just a `name` attribute update
# (capital → lowercase) on the existing NetBox records, not a
# destroy+recreate.
moved {
  from = netbox_virtual_machine.this["PBS"]
  to   = netbox_virtual_machine.this["pbs"]
}
moved {
  from = netbox_virtual_machine.this["Mimir"]
  to   = netbox_virtual_machine.this["mimir"]
}
moved {
  from = netbox_virtual_machine.this["Bifrost"]
  to   = netbox_virtual_machine.this["bifrost"]
}
moved {
  from = netbox_virtual_machine.this["Heimdall"]
  to   = netbox_virtual_machine.this["heimdall"]
}
moved {
  from = netbox_virtual_machine.this["Gjallarbru"]
  to   = netbox_virtual_machine.this["gjallarbru"]
}
moved {
  from = netbox_virtual_machine.this["Fulla"]
  to   = netbox_virtual_machine.this["fulla"]
}

moved {
  from = netbox_interface.this["PBS.eth0"]
  to   = netbox_interface.this["pbs.eth0"]
}
moved {
  from = netbox_interface.this["Mimir.eth0"]
  to   = netbox_interface.this["mimir.eth0"]
}
moved {
  from = netbox_interface.this["Bifrost.eth0"]
  to   = netbox_interface.this["bifrost.eth0"]
}
moved {
  from = netbox_interface.this["Heimdall.eth0"]
  to   = netbox_interface.this["heimdall.eth0"]
}
moved {
  from = netbox_interface.this["Gjallarbru.eth0"]
  to   = netbox_interface.this["gjallarbru.eth0"]
}
moved {
  from = netbox_interface.this["Fulla.eth0"]
  to   = netbox_interface.this["fulla.eth0"]
}

moved {
  from = netbox_ip_address.vm["PBS.eth0"]
  to   = netbox_ip_address.vm["pbs.eth0"]
}
moved {
  from = netbox_ip_address.vm["Mimir.eth0"]
  to   = netbox_ip_address.vm["mimir.eth0"]
}
moved {
  from = netbox_ip_address.vm["Bifrost.eth0"]
  to   = netbox_ip_address.vm["bifrost.eth0"]
}
moved {
  from = netbox_ip_address.vm["Heimdall.eth0"]
  to   = netbox_ip_address.vm["heimdall.eth0"]
}
moved {
  from = netbox_ip_address.vm["Gjallarbru.eth0"]
  to   = netbox_ip_address.vm["gjallarbru.eth0"]
}
moved {
  from = netbox_ip_address.vm["Fulla.eth0"]
  to   = netbox_ip_address.vm["fulla.eth0"]
}

moved {
  from = netbox_primary_ip.this["PBS"]
  to   = netbox_primary_ip.this["pbs"]
}
moved {
  from = netbox_primary_ip.this["Mimir"]
  to   = netbox_primary_ip.this["mimir"]
}
moved {
  from = netbox_primary_ip.this["Bifrost"]
  to   = netbox_primary_ip.this["bifrost"]
}
moved {
  from = netbox_primary_ip.this["Heimdall"]
  to   = netbox_primary_ip.this["heimdall"]
}
moved {
  from = netbox_primary_ip.this["Gjallarbru"]
  to   = netbox_primary_ip.this["gjallarbru"]
}
moved {
  from = netbox_primary_ip.this["Fulla"]
  to   = netbox_primary_ip.this["fulla"]
}
