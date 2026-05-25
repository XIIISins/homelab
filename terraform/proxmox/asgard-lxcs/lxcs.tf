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
# LXCs 1110/1111/1112 — AdGuard Home trio (Urd/Verd/Skuld)
# ----------------------------------------------------------------------------
# Saga (primary, 1110, urd), Mimir (1111, verd), Kvasir (1112, skuld).
# AdGuard Home is the homelab DNS — every node + LXC + container points
# at the VIP `10.0.10.200` (floated across the trio by keepalived on
# VLAN 10). Saga is the canonical config source; adguardhome-sync runs
# on Saga and pushes config to Mimir + Kvasir on a `*/1` cron.
#
# Dual-NIC topology (same class as the HAProxy/etcd trio):
#   eth0 (VLAN 11, HL-ASG-SVC) — service traffic (DNS queries arrive
#     here via the VIP DNAT; HTTP admin UI also on this segment) +
#     default gateway. Per-node IPs 10.0.11.201/202/203.
#   eth1 (VLAN 10, HL-ASG-VIP) — VRRP advertisements + VIP host. All
#     peers L2-adjacent on the VIP segment, required for advertisement
#     source + diagnostics. Per-node IPs 10.0.10.201/202/203, VIP
#     10.0.10.200/32 floats across whichever node holds it.
#
# VRRP virtual_router_id 51 (PG's HAProxy VIP is 61, spacing-by-10
# convention preserved — see CLAUDE.md "VRID collision on shared L2
# segment").
#
# Sizing: adguardhome is a Go binary, ~50MB RSS steady-state + DB.
# 1 vCPU / 512MB / 4GB disk is plenty for hundreds of devices.
#
# Phase 5b.2 — replaces the 2026-05-14-ish manual install. If the
# existing manual LXCs occupy 1110/1111/1112, this module's apply
# would collide; check `qm list` on each Proxmox node and either
# (a) destroy the manual LXCs first (Saga last so DNS stays up via
# the surviving replicas + VIP), or (b) `terraform import` the
# existing IDs and let in-place config converge under Ansible.
#
# See:
#   - docs/architecture/network.md → AGH addressing
#   - ansible/roles/adguard/README.md (TBD — 5b.2.c)
#   - ansible/roles/adguardhome-sync/README.md (TBD — 5b.2.d)
# ----------------------------------------------------------------------------

locals {
  adguard_nodes = {
    saga   = { node = "urd",   vmid = 1110, ip = "10.0.11.201", vlan10_ip = "10.0.10.201" }
    mimir  = { node = "verd",  vmid = 1111, ip = "10.0.11.202", vlan10_ip = "10.0.10.202" }
    kvasir = { node = "skuld", vmid = 1112, ip = "10.0.11.203", vlan10_ip = "10.0.10.203" }
  }
}

# Throwaway root passwords — Proxmox API requires one to create the
# container, but each LXC is configured for SSH-key-only auth. Never
# used; persisted in local state, which is gitignored.
resource "random_password" "adguard_root" {
  for_each = local.adguard_nodes

  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "adguard" {
  for_each = local.adguard_nodes

  # Post-import drift suppression. The bpg/proxmox provider doesn't return
  # `operating_system.template_file_id` or `initialization.user_account` from
  # the Proxmox API on read (Proxmox doesn't store the source template after
  # creation, and user_account is a create-only "seed"), so on every plan
  # after `terraform import` these read back as null and TF wants to set
  # them — both flagged "forces replacement", which would destroy + recreate
  # the running LXC. Ignoring them is correct: post-creation, both are
  # Ansible-managed (baseline + hardening own SSH state; the template is
  # only relevant at first-create). Applies to fresh creates too — the
  # values are still written at creation, ignore_changes only suppresses
  # subsequent diffs. Added when importing the manual AGH trio (Phase 5b.2).
  lifecycle {
    ignore_changes = [
      operating_system[0].template_file_id,
      initialization[0].user_account,
      # community-scripts installer set keyctl=true. The bpg API token
      # can't change feature flags other than nesting (see CLAUDE.md
      # "bpg/proxmox API token can change nesting, NOT other LXC features").
      # Switching to the root-aliased provider just to flip this is
      # heavier than the value — ignore it. AGH doesn't depend on keyctl
      # either way.
      features[0].keyctl,
    ]
  }

  description = "AdGuard Home node (${each.key})"

  node_name = each.value.node
  vm_id     = each.value.vmid
  tags      = ["asgard", "lxc", "adguard", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  # Sizing is cluster-wide, NOT per-node. Failover symmetry requires
  # identical resources — same rule as the PG + HAProxy trios.
  cpu {
    cores = 1
  }

  memory {
    dedicated = 512 # MB
    swap      = 512
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 4 # GB — adguardhome + sqlite query log
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
    # an address only (source-based policy routing in the keepalived
    # role handles replies sourced from VLAN-10 IPs).
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
      password = random_password.adguard_root[each.key].result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true # systemd 257 on Debian 13 — see gotchas
  }

  console {
    enabled = true
    type    = "tty"
  }
}

# ----------------------------------------------------------------------------
# LXC 1102 — Hugin (Zabbix server, Urd)
# ----------------------------------------------------------------------------
# Norse identity: Hugin, one of Odin's two ravens — "Thought" — flies
# out across the world each day and returns to report what he's seen.
# Pairs with Munin (the NAS — "Memory"): one observes, one remembers.
# Tag is the service function (`zabbix`), matching the rest of the
# fleet (AGH trio tagged `adguard`, PG tagged `postgres`, etc.).
#
# Monitoring server for the homelab. Stays outside K3s (per the
# architectural invariant) for monitoring independence — when K3s
# is the thing on fire, we don't want our visibility into the fire
# to share the failure domain.
#
# Stack:
#   - zabbix-server-pgsql 7.x — server backend
#   - zabbix-frontend-php + nginx — web UI on port 80
#   - zabbix-agent2 — local monitoring
#   - PostgreSQL backend via the niflheim Patroni HAProxy VIP
#     (10.0.10.210), dedicated `zabbix` DB
#
# zabbix-agent2 also deploys to every other host in the inventory
# (via a separate playbook + role) — they push metrics to this LXC's
# port 10051. See ansible/roles/zabbix-agent/.
#
# Sizing: 2 vCPU / 4GB / 8GB disk. Server + frontend share the
# memory; DB lives elsewhere (Patroni VIP) so no PG memory pressure
# here. 4GB is the realistic floor for Zabbix 7.0 server + frontend
# (PHP-FPM workers ~50MB each) with headroom for initial schema
# import + migration peaks. Bump further if dashboard latency or
# OOMKills appear in vmui.
#
# Placement: Urd (not Skuld). Original 7c.2 spec placed this on
# Skuld for ID-block locality (1101-1109 backup+mon) — relocated
# 2026-05-25 because (a) Skuld is the 16GB outlier with only ~3.6GB
# available after current workloads, (b) Skuld already carries Fulla
# (PG leader), Snotra (etcd), Sigrún (CP), Einherjar-skuld (worker),
# Kvasir (AGH), Gjallarbru (TS) — losing Skuld would also lose Zabbix
# precisely when monitoring matters most. Urd has 16GB available and
# only Saga/Bifrost/Factorio/Vor/Hlin + Göndul CP + Einherjar-urd.
# Future Jellyfin LXC on Urd will be heavy but Zabbix is light —
# coexistence is fine.
#
# See:
#   - docs/architecture/network.md → IP 10.0.11.21, VMID 1102 (urd)
#   - ansible/roles/zabbix-server/README.md (TBD — 7c.4)
#   - ansible/roles/zabbix-agent/README.md (TBD — 7c.8)
# ----------------------------------------------------------------------------

resource "random_password" "hugin_root" {
  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "hugin" {
  description = "Hugin — Zabbix server + frontend + agent"

  node_name = "urd"
  vm_id     = 1102
  tags      = ["asgard", "lxc", "zabbix", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  cpu {
    cores = 2
  }

  memory {
    dedicated = 4096 # MB — see header for sizing rationale
    swap      = 1024
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 8 # GB — server + frontend; DB is external (Patroni)
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = "hugin"

    ip_config {
      ipv4 {
        address = "10.0.11.21/24"
        gateway = "10.0.11.1"
      }
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.hugin_root.result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true # systemd 257 on Debian 13 — see gotchas
  }

  console {
    enabled = true
    type    = "tty"
  }

  # bpg/proxmox doesn't return template_file_id or user_account from the API
  # on read (Proxmox forgets the source template after creation; user_account
  # is create-only seed) — without ignore_changes, every post-create plan
  # would show both as "forces replacement", proposing destroy-recreate of
  # the running container. See CLAUDE.md "LXC / Proxmox" gotchas.
  lifecycle {
    ignore_changes = [
      operating_system[0].template_file_id,
      initialization[0].user_account,
    ]
  }
}

# ----------------------------------------------------------------------------
# LXC 1103 — Hermod (notifications hub, Verd)
# ----------------------------------------------------------------------------
# Norse identity: Hermod, Odin's messenger — rode Sleipnir to Hel to plead
# for Baldr's release. Single notification aggregation point for the homelab:
# alert producers (Zabbix, future VMAlert, Patroni callbacks, Ansible failure
# handlers, future Sonarr/Radarr) POST to one HTTP endpoint; Hermod fans out
# to per-tag Discord webhooks (Hrist / Mist / Olrun / Hel for critical /
# alert / media / untagged respectively).
#
# Stack (all native, no Docker — see decision in docs/services/notifications.md):
#   - AppriseAPI (pip+venv+uvicorn, listening 127.0.0.1:8000)
#   - Caddy reverse proxy on :80 with `remote_ip` allowlist matcher,
#     reverse_proxy to 127.0.0.1:8000, JSON access logs to
#     /var/log/caddy/access.log (vlagent ships to VL)
#   - vlagent (host syslog → VL via the existing role)
#
# Placement: Verd. Spreads notification infra across hosts (Hugin/Zabbix on
# Urd produces alerts; Hermod on Verd delivers them — independent failure
# domain from the primary producer). Verd is the cubi pair to Urd with
# 32GB RAM and currently the lightest LXC tenant (Mimir + Eir +
# Heimdall + Idunn + Einherjar-verd + Hlokk CP — ~6GB committed). Hermod
# is single-digit MB RSS, no meaningful pressure added.
#
# Sizing: 1 vCPU / 512MB / 4GB disk. AppriseAPI is stateless Python +
# uvicorn (~30MB RSS), Caddy is a Go binary (~15MB RSS), Apprise's
# notification fan-out is sub-millisecond. Single-digit MB headroom is
# comfortable.
#
# This is the second Hermod attempt — first iteration (5aff1dd, reverted in
# a493781 on 2026-05-25) chose Docker-in-LXC and was rolled back ~11 minutes
# later for breaking the all-native-systemd LXC pattern. Current shape
# explicitly avoids that footgun.
#
# See:
#   - docs/services/notifications.md (full design, JSON schema, source→tag
#     mapping, smoketest matrix)
#   - docs/architecture/network.md → IP 10.0.11.22, VMID 1103 (verd)
#   - ansible/roles/hermod-api/README.md (TBD — 5h.2.e)
#   - ansible/roles/caddy-reverse-proxy/README.md (TBD — 5h.2.d)
# ----------------------------------------------------------------------------

resource "random_password" "hermod_root" {
  length  = 32
  special = true
}

resource "proxmox_virtual_environment_container" "hermod" {
  description = "Hermod — notifications hub (AppriseAPI + Caddy)"

  node_name = "verd"
  vm_id     = 1103
  tags      = ["asgard", "lxc", "hermod", "managed-by-terraform"]

  unprivileged  = true
  start_on_boot = true
  started       = true

  cpu {
    cores = 1
  }

  memory {
    dedicated = 512 # MB — AppriseAPI + Caddy + vlagent
    swap      = 512
  }

  disk {
    datastore_id = var.lxc_storage
    size         = 4 # GB — apt packages + venv + Caddy logs
  }

  network_interface {
    name     = "eth0"
    bridge   = var.lxc_network_bridge
    vlan_id  = 11
    firewall = false
    enabled  = true
  }

  initialization {
    hostname = "hermod"

    ip_config {
      ipv4 {
        address = "10.0.11.22/24"
        gateway = "10.0.11.1"
      }
    }

    user_account {
      keys     = [trimspace(var.ssh_public_key)]
      password = random_password.hermod_root.result
    }
  }

  operating_system {
    template_file_id = var.lxc_template
    type             = "debian"
  }

  features {
    nesting = true # systemd 257 on Debian 13 — see gotchas
  }

  console {
    enabled = true
    type    = "tty"
  }

  # bpg/proxmox doesn't return template_file_id or user_account from the API
  # on read — see Hugin's identical block above for the gotcha details.
  lifecycle {
    ignore_changes = [
      operating_system[0].template_file_id,
      initialization[0].user_account,
    ]
  }
}
