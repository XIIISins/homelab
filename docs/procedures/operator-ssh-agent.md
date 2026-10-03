<!-- docs/procedures/operator-ssh-agent.md -->

# Procedure — the operator's SSH keys from Vault (no 1Password prompt)

*Tool: [`scripts/ssh/operator-agent`](../../scripts/ssh/operator-agent). Policy: [`terraform/vault/operator-ssh.tf`](../../terraform/vault/operator-ssh.tf). Why: the 1Password SSH agent asks for approval on every `git push`, commit signature and `ssh <node>`, which stops unattended sessions (see the 2026-10-03 entry in [`decisions.md`](../operations/decisions.md)).*

## What is stored where

Three personal keys from the 1Password `Dev` vault (`Personal SSH-RSA` — the git signing key —, `Luxuria - ed25519`, `id_ed25519`) live at `secret/operator/ssh/<name>` with fields `private_key` and `public_key`. The three work keys (`X2com - Myron`, `id_rsa_sentia`, `work key: id_ed25519`) are deliberately NOT in Vault. 1Password keeps its copy of every key.

**Who may read `secret/operator/*`:** the MacBook (`ansible-local`, policy `operator-ssh-read`), Frigg (`homelab-frigg`, already reads `secret/data/*`) and root. Not Semaphore/AWX (`ansible` policy is `secret/data/ansible/*` only). `eso` and `homelab-admin` read `secret/data/*` and therefore carry an explicit `deny` on `secret/{data,metadata}/operator/*`; **any new policy that reads `secret/data/*` must add the same deny.**

## One-time setup (operator, MacBook)

1. Merge the PR, then apply the policy from the main checkout with your own admin token: `cd terraform/vault && terraform apply`.
2. Place each key, using the root token (the Homelab-vault 1P item is the source of the token, never typed into a command):
   ```bash
   scripts/secrets/vault-1p-mirror mirror-to vault <1p-item-id>/'private key' operator/ssh/<name>/private_key --apply
   scripts/secrets/vault-1p-mirror mirror-to vault <1p-item-id>/'public key'  operator/ssh/<name>/public_key  --apply
   ```
   `op item list --categories 'SSH Key' --format=json | jq -r '.[]|"\(.id)\t\(.vault.name)\t\(.title)"'` finds the ids. The tool reads each value back and compares its hash.
3. Install the ssh override once: `scripts/ssh/operator-agent ssh-config > ~/.ssh/config.d/90-operator-agent`.

## Everyday use

```bash
eval "$(scripts/ssh/operator-agent env)"          # fish: scripts/ssh/operator-agent env --shell fish | source
git push                                            # signed with the Vault-held key, no prompt
scripts/ssh/operator-agent status                   # fingerprints only
scripts/ssh/operator-agent down                     # drop the keys now
```

Keys are loaded for 12 h (`--lifetime`) and expire on their own. The exports only affect the shell that evaluates them; your global git and ssh config keep using 1Password everywhere else. `env` sets `SSH_AUTH_SOCK`, `HOMELAB_OPERATOR_AGENT=1` (switches on the `Match exec` ssh block), `GIT_SSH_COMMAND` and `gpg.ssh.program=ssh-keygen` (via `GIT_CONFIG_*`, replacing `op-ssh-sign`).

## Verify

- `operator-agent status` lists the three fingerprints (match them against `ssh-keygen -lf` of the `.pub` files).
- `HOMELAB_OPERATOR_AGENT=1 ssh -G github.com | grep -i identityagent` shows the Vault agent socket; without the variable it shows the 1Password socket.
- `git commit -S --allow-empty -m test` succeeds with 1Password locked, and `git cat-file -p HEAD` shows a signature.

## Failure modes

- `no keys at secret/operator/ssh/`: step 2 not done. `permission denied` on Vault: the TF apply (step 1) has not run, or the `ansible-local` SecretID expired (`homelab-env && seed-vault-approle`).
- A `git push` that still prompts: the shell did not run `eval "$(…env)"` (check `echo $HOMELAB_OPERATOR_AGENT`).
