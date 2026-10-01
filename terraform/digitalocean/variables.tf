# terraform/digitalocean/variables.tf

variable "ssh_public_keys" {
  description = <<-EOT
    Map of key name -> OpenSSH public key, injected as root's authorized_keys at
    droplet create time. Needs (a) the ansible runtime key (day-1 bootstrap
    runs `-e ansible_user=root`) and (b) the operator key. Public keys are not
    secret; they live in terraform.tfvars (gitignored) like the proxmox modules.
  EOT
  type        = map(string)
}

variable "droplet_size" {
  description = "Droplet size slug. s-1vcpu-1gb (~$6/mo) is the 10a footprint; the Gatus watcher (10b3) is ~100 MB."
  type        = string
  default     = "s-1vcpu-1gb"
}
