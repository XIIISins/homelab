# terraform/netbox/offsite.tf
#
# Phase 10a1 — NetBox declaration for `do1`, the DigitalOcean offsite node
# (standing TF→NetBox rule: every new VM/LXC gets a virtual_machine +
# interface + ip_address). Kept in its OWN file rather than the local.vms
# map in vms.tf on purpose: do1 is not a niflheim/Proxmox VM (no cluster,
# no Proxmox host device, no VMID), so it does not fit the vms.tf for_each
# shape, and a separate file avoids merge churn with concurrent vms.tf edits.
#
# APPLY ORDER: terraform/digitalocean FIRST — the reserved IP is read from
# that module's remote state, so `plan` here fails until the DO state exists.
#
# DELIBERATELY no netbox_primary_ip binding. The NetBox dynamic inventory
# (inventory/netbox.yml) only returns hosts WITH a primary IP, and the
# fleet-wide `hosts: all` plays (vlagent.yml, zabbix-agent.yml, Semaphore's
# 30-min apply) must not reach a public-internet host over its public IP.
# do1 is converged by hand via playbooks/do1.yml (static inventory group
# `offsite`). Bind a primary IP later only if/when do1 should join the
# reconcile loop (e.g. over the tailnet address, with a tailnet-reachable
# Semaphore) — a decision, not an oversight.

data "terraform_remote_state" "digitalocean" {
  backend = "s3"
  config = {
    bucket = "xiiisins-homelab-tfstate"
    key    = "digitalocean/terraform.tfstate"
    region = "eu-west-1"
  }
}

resource "netbox_site" "digitalocean_ams3" {
  name   = "digitalocean-ams3"
  slug   = "digitalocean-ams3"
  status = "active"
}

resource "netbox_device_role" "offsite_node" {
  name        = "offsite-node"
  slug        = "offsite-node"
  color_hex   = "9e9e9e"
  vm_role     = true
  description = "Offsite DigitalOcean node (TS3 failover, PlantNet proxy, outside watcher)"
}

resource "netbox_tag" "ansible_offsite" {
  name      = "ansible:offsite"
  slug      = "ansibleoffsite"
  color_hex = "ff5722"
}

resource "netbox_virtual_machine" "do1" {
  name      = "do1"
  site_id   = netbox_site.digitalocean_ams3.id
  role_id   = netbox_device_role.offsite_node.id
  status    = "active"
  vcpus     = 1
  memory_mb = 1024
  comments  = "DigitalOcean droplet ${data.terraform_remote_state.digitalocean.outputs.droplet_id}, ams3, s-1vcpu-1gb. terraform/digitalocean + ansible/playbooks/do1.yml."

  # Not a Proxmox guest, so no real vmid: 9900-9999 is the reserved VMID
  # cross-reference range for DigitalOcean nodes (network.md "Resource ID
  # scheme"). Without a value the provider keeps planning a VMID=null removal.
  custom_fields = {
    VMID = "9900"
  }

  tags = [netbox_tag.ansible_offsite.name]
}

resource "netbox_interface" "do1_eth0" {
  virtual_machine_id = netbox_virtual_machine.do1.id
  name               = "eth0"
  enabled            = true
}

# The reserved (public) IPv4 — the address PlantNet allowlists and DNS points at.
resource "netbox_ip_address" "do1_reserved" {
  ip_address                   = "${data.terraform_remote_state.digitalocean.outputs.reserved_ip}/32"
  status                       = "active"
  virtual_machine_interface_id = netbox_interface.do1_eth0.id
}
