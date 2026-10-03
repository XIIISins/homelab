# terraform/vault/operator-ssh.tf
#
# The operator's personal SSH identity keys (git push, commit signing, ssh), held in Vault so
# work on the MacBook and on Frigg proceeds without a 1Password agent prompt.
#
#   secret/operator/ssh/<name>   fields: private_key (OpenSSH, trailing newline), public_key
#
# OPERATOR-PLACED, deliberately NOT a TF resource (same reason as ansible/frigg/ssh-private-key
# in frigg.tf): these are existing keys already registered at GitHub and on hosts, so they
# cannot be minted here, and a private key must not enter TF state. 1Password stays the
# offline copy. Placement commands: docs/procedures/operator-ssh-agent.md
#
# WHO CAN READ `secret/operator/*` (the access rule, enforced here):
#   - ansible-local  (the MacBook)  via operator-ssh-read below
#   - homelab-frigg  (Frigg)        already reads secret/data/*; no change
#   - root                          always
#   - NOT ansible-awx / Semaphore   their `ansible` policy only reads secret/data/ansible/*
#   - NOT eso, homelab-admin        both have a wildcard read; each carries an explicit
#                                   deny on secret/{data,metadata}/operator/* (main.tf, oidc.tf)
# A new policy that reads secret/data/* MUST add the same deny.

resource "vault_policy" "operator_ssh_read" {
  name = "operator-ssh-read"

  policy = <<-EOT
    path "secret/data/operator/ssh/*" {
      capabilities = ["read"]
    }
    path "secret/metadata/operator/ssh/*" {
      capabilities = ["read", "list"]
    }
  EOT
}
