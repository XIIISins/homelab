# terraform/digitalocean-burst/variables.tf

variable "burst_count" {
  description = <<-EOT
    Number of burst droplets. 0 (the default) makes a plain `terraform apply` a no-op: no tag,
    no firewall, no droplet. scripts/burst/burst-up sets this (default 3).
  EOT
  type        = number
  default     = 0

  validation {
    condition     = var.burst_count >= 0 && var.burst_count <= 6 && floor(var.burst_count) == var.burst_count
    error_message = "burst_count must be an integer 0..6 (a cost ceiling: 6 x s-2vcpu-4gb is ~$0.72/h)."
  }
}

variable "control_plane_count" {
  description = "How many of the burst droplets are K3s control planes (the first N by name). 1 = one CP + (burst_count-1) workers; 3 = three CPs (etcd quorum tests)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 3], var.control_plane_count)
    error_message = "control_plane_count must be 1 or 3 (etcd needs an odd member count)."
  }
}

variable "droplet_size" {
  description = "Droplet size slug. s-2vcpu-4gb (~$0.036/h, $24/mo list) matches the asgard worker shape closely enough for restore/heal drills."
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "region" {
  description = "DO region. ams3 = same as do1 (and the closest to the homelab, so the S3 restore pull from eu-west-1 stays quick)."
  type        = string
  default     = "ams3"
}

variable "image" {
  description = "Droplet image slug. Debian 13 matches Frigg/do1 (the tailscale role's apt repo is the trixie one)."
  type        = string
  default     = "debian-13-x64"
}

variable "ttl_hours" {
  description = <<-EOT
    Intended lifetime. Stamped on each droplet as the DO tag `burst-ttl-<N>h`; the reaper on Frigg
    (roles/burst-reaper) destroys a `burst` droplet older than this tag's N hours (or its own
    default when the tag is absent), never longer than its hard cap.
  EOT
  type        = number
  default     = 4

  validation {
    condition     = var.ttl_hours >= 1 && var.ttl_hours <= 24 && floor(var.ttl_hours) == var.ttl_hours
    error_message = "ttl_hours must be an integer 1..24."
  }
}

variable "ssh_key_names" {
  description = <<-EOT
    Names of EXISTING DO account SSH keys injected as root's authorized_keys. Referenced via data
    source, never created: DO rejects a duplicate public key ("SSH Key is already in use"), and the
    ansible_niflheim key already exists as `homelab-offsite-ansible` (created by terraform/digitalocean).
    Consequence: this root needs the do1 root applied (and its key not destroyed) first.
  EOT
  type        = list(string)
  default     = ["homelab-offsite-ansible"]
}

variable "ssh_source_cidrs" {
  description = "Source CIDRs allowed to reach tcp/22 (Ansible runs from Frigg over the droplets' public IPs). Default is open (key-only auth); set it to Frigg's public egress IP/32 in terraform.tfvars (gitignored) to close it. Never commit the home IP."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}
