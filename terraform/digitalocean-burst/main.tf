# terraform/digitalocean-burst/main.tf
#
# Phase 10b2 — ephemeral burst droplets for destructive/expensive tests (offsite-backup restore
# drill, K3s heal/rebuild, fault injection). Plan: docs/plans/active/aiops-roadmap.md §10b2;
# procedure: docs/procedures/burst-substrate.md.
#
# Everything is gated on burst_count > 0, so a plain `terraform apply` (count 0) is a no-op.
#
# Cost guard (the whole point of this root being careful): the droplets carry the DO tag `burst`
# (+ `burst-ttl-<N>h`); the TTL reaper on Frigg destroys any `burst` droplet past its TTL even if
# nobody remembers it. After a reap run scripts/burst/burst-down so this state catches up.
#
# Deliberately NOT here: reserved IP, project, dedicated VPC (the token lacks those scopes, and
# none are needed), and no do1-style tag avoidance — the legacy firewalls bind tags `ots`,
# `portainer`, `tailscale`, never `burst`, so tagging is safe (known-issues/digitalocean.md).

locals {
  enabled = var.burst_count > 0

  droplets = {
    for i in range(var.burst_count) : "burst-${i + 1}" => {
      role = i < var.control_plane_count ? "cp" : "worker"
    }
  }
}

# The region's default VPC: droplets are placed in it automatically and get a private IPv4
# there. Cluster traffic (K3s, Calico VXLAN) rides it, free and never on the public interface.
data "digitalocean_vpc" "default" {
  count  = local.enabled ? 1 : 0
  region = var.region
}

# Existing keys — referenced, never created (see var.ssh_key_names).
data "digitalocean_ssh_key" "this" {
  for_each = local.enabled ? toset(var.ssh_key_names) : toset([])
  name     = each.value
}

resource "digitalocean_tag" "burst" {
  count = local.enabled ? 1 : 0
  name  = "burst"
}

resource "digitalocean_tag" "ttl" {
  count = local.enabled ? 1 : 0
  name  = "burst-ttl-${var.ttl_hours}h"
}

resource "digitalocean_droplet" "burst" {
  for_each = local.enabled ? local.droplets : {}

  name   = each.key
  region = var.region
  size   = var.droplet_size
  image  = var.image

  ssh_keys   = [for k in data.digitalocean_ssh_key.this : k.fingerprint]
  vpc_uuid   = data.digitalocean_vpc.default[0].id
  ipv6       = false
  monitoring = false # throwaway: no DO agent, nothing to alert on
  backups    = false

  tags = [digitalocean_tag.burst[0].name, digitalocean_tag.ttl[0].name]

  lifecycle {
    precondition {
      condition     = var.control_plane_count <= var.burst_count
      error_message = "control_plane_count (${var.control_plane_count}) cannot exceed burst_count (${var.burst_count})."
    }
  }
}

# THE firewall, bound by TAG so it covers every burst droplet (including ones added later) and
# only them. (Cloud firewalls are additive — known-issues/digitalocean.md — but no other firewall
# binds `burst`.) K3s API (6443) and everything else stay closed publicly: the cluster is reached
# over the tailnet from Frigg, and node-to-node traffic is allowed only from inside the VPC.
resource "digitalocean_firewall" "burst" {
  count = local.enabled ? 1 : 0
  name  = "burst"
  tags  = [digitalocean_tag.burst[0].name]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_source_cidrs
  }
  # direct WireGuard paths (without it Tailscale falls back to DERP relays: works, slower)
  inbound_rule {
    protocol         = "udp"
    port_range       = "41641"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }
  # intra-VPC: K3s (6443, 10250, 2379-2380), Calico VXLAN (udp 4789), kubelet, NodePorts
  inbound_rule {
    protocol         = "tcp"
    port_range       = "1-65535"
    source_addresses = [data.digitalocean_vpc.default[0].ip_range]
  }
  inbound_rule {
    protocol         = "udp"
    port_range       = "1-65535"
    source_addresses = [data.digitalocean_vpc.default[0].ip_range]
  }
  inbound_rule {
    protocol         = "icmp"
    source_addresses = [data.digitalocean_vpc.default[0].ip_range]
  }

  # A DO firewall with no outbound rules drops all egress (apt, GitHub K3s release, S3, tailnet).
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
