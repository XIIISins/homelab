# terraform/netbox/tags.tf
#
# Tag definitions used across NetBox resources. Three families:
#   - iac:*       — provenance (terraform vs manually-installed)
#   - ansible:*   — which Ansible role manages a workload
#   - lxc / vm    — Proxmox container vs VM (NetBox stores both as
#                   VirtualMachine; this tag is the human-facing
#                   distinguisher)
#
# Tag APPLICATIONS (which resource carries which tag) are NOT defined
# here. They live on each consuming resource's `tags` attribute. This
# file just imports the definitions so the catalog itself is IaC-
# managed; per-resource tag rollout is incremental, future work.
#
# Color choices match the hand-entered values from the 5i.e import.
# NetBox slugs auto-strip colons → "ansible:patroni" → slug
# "ansiblepatroni". Provider preserves whatever slug already exists.

locals {
  tags = {
    # Per-role tags (existing — define what's INSTALLED on a host).
    # NOT all applied yet; per-resource rollout is incremental.
    "ansible:baseline"        = { slug = "ansiblebaseline", color_hex = "4caf50" }
    "ansible:etcd"            = { slug = "ansibleetcd", color_hex = "ff9800" }
    "ansible:factorio"        = { slug = "ansible", color_hex = "795548" }
    "ansible:haproxy"         = { slug = "ansiblehaproxy", color_hex = "cddc39" }
    "ansible:hardening"       = { slug = "ansiblehardening", color_hex = "4caf50" }
    "ansible:k3s"             = { slug = "ansiblek3s", color_hex = "e91e63" }
    "ansible:keepalived-vrrp" = { slug = "ansiblekeepalived-vrrp", color_hex = "ffc107" }
    "ansible:patroni"         = { slug = "ansiblepatroni", color_hex = "ff5722" }
    "ansible:postgres"        = { slug = "ansiblepostgres", color_hex = "03a9f4" }
    "ansible:tailscale"       = { slug = "ansibletailscale", color_hex = "3f51b5" }

    # Per-group tags (Phase 5h.3 — drive Ansible inventory group
    # membership via `group_by: tag` in the netbox.netbox.nb_inventory
    # plugin). Convention: `ansible:<group>` where <group> matches the
    # static `ansible/inventory/hosts.yml` group name 1:1. The dynamic
    # inventory's `keyed_groups` filter projects ONLY these (filtering
    # `tag.startswith("ansible:")` and stripping the prefix) into
    # Ansible groups; the role tags above stay as NetBox-side
    # documentation without polluting the inventory graph.
    "ansible:adguard"      = { slug = "ansibleadguard", color_hex = "00bcd4" }
    "ansible:haproxy-etcd" = { slug = "ansiblehaproxy-etcd", color_hex = "8bc34a" }
    "ansible:hermod"       = { slug = "ansiblehermod", color_hex = "ff9800" }
    "ansible:k3s-cp"       = { slug = "ansiblek3s-cp", color_hex = "e91e63" }
    "ansible:k3s-worker"   = { slug = "ansiblek3s-worker", color_hex = "f06292" }
    "ansible:pbs"          = { slug = "ansiblepbs", color_hex = "607d8b" }
    "ansible:proxmox"      = { slug = "ansibleproxmox", color_hex = "e64a19" }
    "ansible:semaphore"    = { slug = "ansiblesemaphore", color_hex = "9c27b0" }
    "ansible:synology"     = { slug = "ansiblesynology", color_hex = "009688" }
    "ansible:zabbix"       = { slug = "ansiblezabbix", color_hex = "d32f2f" }

    # Provenance + container-vs-VM
    "iac:manual"    = { slug = "iacmanual", color_hex = "f44336" }
    "iac:terraform" = { slug = "iacterraform", color_hex = "9c27b0" }
    "lxc"           = { slug = "lxc", color_hex = "ff66ff" }
    "vm"            = { slug = "vm", color_hex = "00ffff" }
  }

  tag_import_ids = {
    "ansible:baseline"        = "8"
    "ansible:etcd"            = "6"
    "ansible:factorio"        = "11"
    "ansible:haproxy"         = "14"
    "ansible:hardening"       = "9"
    "ansible:k3s"             = "12"
    "ansible:keepalived-vrrp" = "7"
    "ansible:patroni"         = "5"
    "ansible:postgres"        = "10"
    "ansible:tailscale"       = "13"
    "iac:manual"              = "4"
    "iac:terraform"           = "3"
    "lxc"                     = "1"
    "vm"                      = "2"
  }
}

resource "netbox_tag" "this" {
  for_each = local.tags

  name      = each.key
  slug      = each.value.slug
  color_hex = each.value.color_hex
}

import {
  for_each = local.tag_import_ids
  to       = netbox_tag.this[each.key]
  id       = each.value
}
