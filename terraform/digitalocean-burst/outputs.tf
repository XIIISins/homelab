# terraform/digitalocean-burst/outputs.tf
#
# Consumed by scripts/burst/burst-inventory.py, which turns `droplets` into the Ansible
# inventory for playbooks/burst-k3s.yml (nothing is committed; the inventory is generated).

output "droplets" {
  description = "One entry per burst droplet, in name order (burst-1 first = the K3s init node)."
  value = [
    for name in sort(keys(digitalocean_droplet.burst)) : {
      name         = name
      role         = local.droplets[name].role
      public_ipv4  = digitalocean_droplet.burst[name].ipv4_address
      private_ipv4 = digitalocean_droplet.burst[name].ipv4_address_private
      id           = digitalocean_droplet.burst[name].id
    }
  ]
}

output "vpc_ip_range" {
  description = "CIDR of the VPC the droplets sit in — Calico node-IP autodetection is pinned to it."
  value       = local.enabled ? data.digitalocean_vpc.default[0].ip_range : null
}

output "ttl_hours" {
  value = var.ttl_hours
}
