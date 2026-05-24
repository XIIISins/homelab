# terraform/netbox/clusters.tf
#
# Single virtualization cluster `niflheim` of type Proxmox. NetBox's
# Cluster + VirtualMachine model maps both VMs and LXCs onto the same
# `niflheim` cluster — the role field on each VM differentiates intent
# (k3s-control-plane vs k3s-worker vs db vs game-server vs …).
#
# site_id is deliberately UNSET (cluster is site-agnostic in our model
# — physical placement is on the Device-side via `home`). To link the
# cluster to the site later, add `site_id = netbox_site.home.id` and
# `terraform apply` will be the only operation needed.

resource "netbox_cluster_type" "proxmox" {
  name = "Proxmox"
  slug = "proxmox"
}

import {
  to = netbox_cluster_type.proxmox
  id = "1"
}

resource "netbox_cluster" "niflheim" {
  name            = "niflheim"
  cluster_type_id = netbox_cluster_type.proxmox.id
}

import {
  to = netbox_cluster.niflheim
  id = "1"
}
