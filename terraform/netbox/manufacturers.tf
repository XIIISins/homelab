# terraform/netbox/manufacturers.tf
#
# Hardware vendors + per-vendor device types referenced by physical
# devices in devices.tf (Stage 4). Manufacturer + device_type are a
# tightly-coupled pair — keep them in the same file so a new piece of
# hardware lands as a single edit.
#
# Model names match what's hand-entered in NetBox UI during 5i.e —
# fuller marketing names (e.g. "Mini S12 Pro" not "MINI-S12"), since
# NetBox is the human-facing IPAM/DCIM view.

locals {
  manufacturers = {
    msi      = { name = "MSI", slug = "msi" }
    beelink  = { name = "Beelink", slug = "beelink" }
    synology = { name = "Synology", slug = "synology" }
    ubiquiti = { name = "Ubiquiti", slug = "ubiquiti" }
  }

  device_types = {
    msi_cubi = {
      manufacturer = "msi"
      model        = "Cubi 5 12M-407BEU"
      slug         = "cubi-5-12m-407beu"
    }
    beelink_mini_s12 = {
      manufacturer = "beelink"
      model        = "Mini S12 Pro"
      slug         = "mini-s12-pro"
    }
    synology_ds223j = {
      manufacturer = "synology"
      model        = "DS223J"
      slug         = "ds223j"
    }
    ubiquiti_ucg_ultra = {
      manufacturer = "ubiquiti"
      model        = "UCG-Ultra"
      slug         = "ucg-ultra"
    }
  }

  # Import IDs sourced from /api/dcim/manufacturers/ + /api/dcim/device-types/
  # at the time of the 5i.3 import retrofit (see Stage 2 commit). If NetBox
  # is wiped and re-imported, IDs will change — re-derive from the API.
  manufacturer_import_ids = {
    msi      = "1"
    beelink  = "2"
    synology = "3"
    ubiquiti = "4"
  }
  device_type_import_ids = {
    msi_cubi           = "1"
    beelink_mini_s12   = "2"
    synology_ds223j    = "3"
    ubiquiti_ucg_ultra = "4"
  }
}

resource "netbox_manufacturer" "this" {
  for_each = local.manufacturers

  name = each.value.name
  slug = each.value.slug
}

import {
  for_each = local.manufacturer_import_ids
  to       = netbox_manufacturer.this[each.key]
  id       = each.value
}

resource "netbox_device_type" "this" {
  for_each = local.device_types

  manufacturer_id = netbox_manufacturer.this[each.value.manufacturer].id
  model           = each.value.model
  slug            = each.value.slug
  is_full_depth   = false # NUC-class + small NAS + small router; nothing is full-depth-rack
}

import {
  for_each = local.device_type_import_ids
  to       = netbox_device_type.this[each.key]
  id       = each.value
}
