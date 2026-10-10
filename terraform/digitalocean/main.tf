# terraform/digitalocean/main.tf
#
# Phase 10a1 — offsite node `do1`. Built BESIDE the unmanaged legacy droplet
# (docker-ubuntu-s-1vcpu-1gb-ams3-01); nothing here touches it. Duties: TS3
# failover, HeyLeaf PlantNet proxy (fixed IPv4 for the PlantNet allowlist),
# and later the Gatus outside watcher (10b3). Plan: docs/plans/active/aiops-roadmap.md.

resource "digitalocean_project" "offsite" {
  name        = "homelab-offsite"
  purpose     = "Service or API"
  environment = "Production"
  description = "Offsite node do1: TS3 failover + PlantNet proxy + outside watcher (Terraform-managed)"
  resources = [
    digitalocean_droplet.do1.urn,
    digitalocean_reserved_ip.do1.urn,
  ]
}

# Explicit SSH keys — never reuse the three stale keys already in the account.
resource "digitalocean_ssh_key" "this" {
  for_each   = var.ssh_public_keys
  name       = "homelab-offsite-${each.key}"
  public_key = each.value
}

resource "digitalocean_droplet" "do1" {
  name   = "do1"
  region = "ams3"
  size   = var.droplet_size
  image  = "debian-13-x64"

  ssh_keys   = [for k in digitalocean_ssh_key.this : k.fingerprint]
  ipv6       = false # IPv4-only: TS3 SRV + PlantNet allowlist are v4; less surface
  monitoring = true  # DO metrics agent (free); does not open ports
  backups    = false # state is reproducible from IaC; TS3 SQLite is restored from dump (10a3)

  # Deliberately NO droplet tags. Cloud firewalls bind by tag as well as by
  # droplet ID and are additive (known-issues/digitalocean.md); the legacy
  # `portainer`/`ots`/`tailscale` tags would pull legacy firewalls onto this
  # node. The one firewall below binds by droplet ID.

  lifecycle {
    # A newer debian-13 image slug must not force-replace a running node.
    ignore_changes = [image, user_data]
  }
}

# Free while attached; survives droplet rebuilds so the PlantNet allowlist
# entry (the reason this is a droplet) is stable.
#
# GOTCHA: outbound traffic does NOT use the reserved IP by default — only
# inbound does. PlantNet sees the droplet's own IP unless the default route is
# moved to the anchor gateway; the ansible `do-reserved-egress` role does
# that (known-issues/digitalocean.md).
resource "digitalocean_reserved_ip" "do1" {
  region = "ams3"
}

resource "digitalocean_reserved_ip_assignment" "do1" {
  ip_address = digitalocean_reserved_ip.do1.ip_address
  droplet_id = digitalocean_droplet.do1.id
}

# THE firewall. Bound by droplet ID, owns the whole rule set. TS3 query
# (10011), file transfer / TSDNS (30033/41144) stay closed publicly.
resource "digitalocean_firewall" "do1" {
  name        = "do1"
  droplet_ids = [digitalocean_droplet.do1.id]

  dynamic "inbound_rule" {
    for_each = {
      "ssh"       = { proto = "tcp", port = "22" }
      "http"      = { proto = "tcp", port = "80" }  # ACME HTTP-01 + redirect
      "https"     = { proto = "tcp", port = "443" } # Caddy: plantnet proxy
      "ts3"       = { proto = "udp", port = "9987" }
      "tailscale" = { proto = "udp", port = "41641" } # direct WireGuard paths
    }
    content {
      protocol         = inbound_rule.value.proto
      port_range       = inbound_rule.value.port
      source_addresses = ["0.0.0.0/0", "::/0"]
    }
  }

  # A DO firewall with no outbound rules drops all egress.
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
