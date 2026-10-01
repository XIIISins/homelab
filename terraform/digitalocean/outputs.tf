# terraform/digitalocean/outputs.tf

output "reserved_ip" {
  description = "Reserved IPv4 of do1 — the PlantNet allowlist entry + DNS target (also read by terraform/netbox via remote state)."
  value       = digitalocean_reserved_ip.do1.ip_address
}

output "droplet_ipv4" {
  description = "do1's own (non-reserved) public IPv4 — SSH bootstrap target before DNS exists."
  value       = digitalocean_droplet.do1.ipv4_address
}

output "droplet_id" {
  value = digitalocean_droplet.do1.id
}
