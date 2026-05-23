# homelab.fish — Control-node tooling for the homelab.
#
# Repo:    <homelab-repo>/.config/fish/conf.d/homelab.fish
# Symlink: ~/.config/fish/conf.d/homelab.fish → repo file
#
# conf.d/ is the right home (not functions/) — fish's functions/ uses
# autoload-by-filename, one function per file. This file holds many.
# Sourced once at shell init; env vars are only set when functions are
# invoked.
#
# Public functions:
#   homelab-env          — load homelab env vars from 1Password
#   set-vault-token      — set VAULT_TOKEN from a named source (root|approle)
#   vault-root-token     — echo the Vault root token from 1P (value-producer)
#   rotate-approle       — rotate a Vault AppRole SecretID (--help, --fix)
#
# Extending:
#   - New env var loaded by homelab-env: append to $__homelab_env_map.
#   - New VAULT_TOKEN source: add a case to set-vault-token's switch.
#   - New AppRole role for rotation: append to $__homelab_approle_items.
#
# All credentials come from the 1Password "Homelab" vault. Nothing
# sensitive on disk in this file.

# === Config ===

set -g __homelab_op_vault 'Homelab 2.0'

# Each entry: "ENV_VAR|1P item name|field"
set -g __homelab_env_map \
    "VAULT_ADDR|Ansible - Vault - k3s|url" \
    "ANSIBLE_HASHI_VAULT_AUTH_METHOD|Ansible - Vault - k3s|method" \
    "ANSIBLE_HASHI_VAULT_ROLE_ID|Ansible - Vault - k3s|username" \
    "ANSIBLE_HASHI_VAULT_SECRET_ID|Ansible - Vault - k3s|password" \
    "CLOUDFLARE_API_TOKEN|Cloudflare - Terraform|credential" \
    "AUTHENTIK_TOKEN|Asgard - Authentik - akadmin API token|credential" \
    "AUTHENTIK_URL|Asgard - Authentik - akadmin API token|url"

# Each entry: "approle role name|1P item name"
# 1P item must have fields: username (RoleID), password (SecretID),
# secret_id_accessor, expires_at.
set -g __homelab_approle_items \
    "ansible-local|Ansible - Vault - k3s"
# "ansible-awx|Ansible - Vault - AWX"   # add when AWX is deployed

# === Helpers (private) ===

function __homelab_op_field --argument-names item field \
    --description "Read a field from a 1P item in the Homelab vault"
    op read "op://$__homelab_op_vault/$item/$field"
end

function __homelab_approle_item_for --argument-names role \
    --description "Look up the 1P item name for an AppRole role"
    for entry in $__homelab_approle_items
        set -l parts (string split "|" -- $entry)
        if test "$parts[1]" = "$role"
            echo $parts[2]
            return 0
        end
    end
    return 1
end

function __homelab_rotate_approle_help \
    --description "Print rotate-approle usage"
    echo "Usage:"
    echo "  rotate-approle <role>         Mint new SecretID, update 1P, revoke old."
    echo "  rotate-approle --fix <role>   Destroy SecretIDs in Vault that aren't in 1P."
    echo "  rotate-approle --help         This help."
    echo ""
    echo "Known roles:"
    for entry in $__homelab_approle_items
        set -l parts (string split "|" -- $entry)
        echo "  $parts[1] → 1P item: \"$parts[2]\""
    end
    echo ""
    echo "Hazard:"
    echo "  Any partial rotation that updates 1P but leaves the old SecretID alive"
    echo "  in Vault — Ctrl+C between paste and revoke, OR a failed revoke step —"
    echo "  produces an orphan SecretID. 1P now points at the NEW accessor, so"
    echo "  re-running the normal rotation would target the wrong SecretID for"
    echo "  revocation. Recovery: rotate-approle --fix <role>"
end

function __homelab_rotate_approle_fix --argument-names role item \
    --description "Destroy SecretIDs in Vault that aren't the one in 1P"
    set -l canonical (__homelab_op_field $item secret_id_accessor)
    if test $status -ne 0
        echo "rotate-approle --fix: failed to read secret_id_accessor from 1P item \"$item\"" >&2
        return 1
    end
    echo "Canonical accessor (in 1P): $canonical"
    echo ""

    set -l json (vault list -format=json auth/approle/role/$role/secret-id 2>/dev/null)
    if test $status -ne 0
        echo "rotate-approle --fix: no SecretIDs in Vault for role '$role'." >&2
        echo "If 1P holds an accessor, it's stale — the SecretID was already destroyed." >&2
        echo "Resolution: full re-bootstrap (see AppRole bootstrap runbook)." >&2
        return 1
    end

    # vault list -format=json can return either a bare array or {data:{keys:[...]}}
    # depending on version. Try wrapped form first, fall back to bare.
    set -l all (echo $json | jq -r '.data.keys[]?' 2>/dev/null)
    if test (count $all) -eq 0
        set all (echo $json | jq -r '.[]?' 2>/dev/null)
    end

    # Sanity: canonical must exist in Vault, else 1P is stale.
    set -l canonical_present 0
    for acc in $all
        if test "$acc" = "$canonical"
            set canonical_present 1
            break
        end
    end
    if test $canonical_present -eq 0
        echo "WARNING: 1P's accessor ($canonical) does NOT exist in Vault." >&2
        echo "1P is stale — playbooks WILL fail. Resolution: full re-bootstrap." >&2
        return 1
    end

    # Orphans = everything in Vault except canonical.
    set -l orphans
    for acc in $all
        if test "$acc" != "$canonical"
            set orphans $orphans $acc
        end
    end
    if test (count $orphans) -eq 0
        echo "✓ No orphans. Vault and 1P are in sync."
        return 0
    end

    echo "Found "(count $orphans)" orphan accessor(s):"
    echo ""
    for acc in $orphans
        echo "  $acc"
        set -l meta (vault write -format=json \
            auth/approle/role/$role/secret-id-accessor/lookup \
            secret_id_accessor=$acc 2>/dev/null)
        if test $status -eq 0
            set -l created (echo $meta | jq -r '.data.creation_time // "?"')
            set -l last_updated (echo $meta | jq -r '.data.last_updated_time // "?"')
            set -l num_uses (echo $meta | jq -r '.data.secret_id_num_uses // "?"')
            echo "    created:      $created"
            echo "    last_updated: $last_updated"
            echo "    num_uses:     $num_uses"
        end
        echo ""
    end

    read -P "Destroy all "(count $orphans)" orphan(s)? [y/N] " confirm
    if test "$confirm" != y -a "$confirm" != Y
        echo "Aborted. No changes made."
        return 1
    end

    set -l destroyed 0
    set -l errors 0
    for acc in $orphans
        vault write auth/approle/role/$role/secret-id-accessor/destroy \
            secret_id_accessor=$acc >/dev/null
        if test $status -eq 0
            echo "  ✓ destroyed $acc"
            set destroyed (math $destroyed + 1)
        else
            echo "  ✗ failed:    $acc" >&2
            set errors (math $errors + 1)
        end
    end
    echo ""
    echo "Destroyed $destroyed orphan(s)."
    if test $errors -gt 0
        echo "$errors failure(s)." >&2
        return 1
    end
end

# === Public: env loading ===

function homelab-env --description "Load homelab environment from 1Password"
    set -l errors 0
    set -l loaded 0
    for entry in $__homelab_env_map
        set -l parts (string split "|" -- $entry)
        set -l env_var $parts[1]
        set -l item $parts[2]
        set -l field $parts[3]
        set -l value (__homelab_op_field $item $field)
        if test $status -ne 0
            echo "  ↳ failed to load $env_var from \"$item\"/$field" >&2
            set errors (math $errors + 1)
            continue
        end
        set -gx $env_var $value
        set loaded (math $loaded + 1)
    end
    if test $errors -gt 0
        echo "" >&2
        echo "Loaded $loaded vars, $errors errors." >&2
        echo "If 1Password isn't signed in, run: op signin" >&2
        return 1
    end
    echo "Loaded $loaded homelab env vars"
end

# === Public: vault tokens ===

function vault-root-token --description "Echo the Vault root token from 1Password"
    __homelab_op_field "Asgard - Vault - Root Token" password
end

function set-vault-token --description "Set VAULT_TOKEN from a named source"
    if test (count $argv) -lt 1
        echo "Usage: set-vault-token <source>" >&2
        echo "  Sources: root, approle" >&2
        return 1
    end
    switch $argv[1]
        case root
            set -l value (vault-root-token)
            if test $status -ne 0
                echo "set-vault-token: failed to read root token from 1P" >&2
                return 1
            end
            set -gx VAULT_TOKEN $value
            echo "VAULT_TOKEN set (root, from 1P)"
        case approle
            if not set -q VAULT_ADDR
                echo "set-vault-token: VAULT_ADDR not set. Run homelab-env first." >&2
                return 1
            end
            if not set -q ANSIBLE_HASHI_VAULT_ROLE_ID
                or not set -q ANSIBLE_HASHI_VAULT_SECRET_ID
                echo "set-vault-token: AppRole creds not in env. Run homelab-env first." >&2
                return 1
            end
            set -l json (vault write -format=json auth/approle/login \
                role_id=$ANSIBLE_HASHI_VAULT_ROLE_ID \
                secret_id=$ANSIBLE_HASHI_VAULT_SECRET_ID)
            if test $status -ne 0
                echo "set-vault-token: AppRole login failed" >&2
                return 1
            end
            set -gx VAULT_TOKEN (echo $json | jq -r '.auth.client_token')
            echo "VAULT_TOKEN set (approle, freshly minted)"
        case '*'
            echo "set-vault-token: unknown source '$argv[1]'" >&2
            echo "  Sources: root, approle" >&2
            return 1
    end
end

# === Public: AppRole rotation ===

function rotate-approle --description "Rotate a Vault AppRole SecretID"
    argparse -n rotate-approle h/help fix -- $argv
    or return

    if set -q _flag_help; or test (count $argv) -eq 0
        __homelab_rotate_approle_help
        return 0
    end

    if test (count $argv) -ne 1
        echo "rotate-approle: expected 1 role argument, got "(count $argv)". See: rotate-approle --help" >&2
        return 1
    end

    set -l role $argv[1]

    set -l item (__homelab_approle_item_for $role)
    if test $status -ne 0
        echo "rotate-approle: unknown role '$role'. See: rotate-approle --help" >&2
        return 1
    end

    if not set -q VAULT_TOKEN
        echo "rotate-approle: VAULT_TOKEN not set. Run: set-vault-token root" >&2
        return 1
    end
    if not set -q VAULT_ADDR
        echo "rotate-approle: VAULT_ADDR not set. Run: homelab-env" >&2
        return 1
    end

    if set -q _flag_fix
        __homelab_rotate_approle_fix $role $item
        return $status
    end

    # === Normal rotation flow ===

    # Step 1: snapshot old accessor BEFORE 1P gets updated.
    set -l old_accessor (__homelab_op_field $item secret_id_accessor)
    if test $status -ne 0
        echo "rotate-approle: failed to read secret_id_accessor from 1P item \"$item\"" >&2
        echo "Field must exist on the item — see AppRole bootstrap runbook." >&2
        return 1
    end

    echo "Rotating AppRole '$role' (1P item: \"$item\")"
    echo "Old accessor: $old_accessor"
    echo ""

    # Step 2: mint new SecretID. Both old and new now valid — safe window for
    # the human to update 1P. Old is revoked only after enter is pressed.
    echo "Minting new SecretID..."
    set -l json (vault write -f -format=json auth/approle/role/$role/secret-id)
    if test $status -ne 0
        echo "rotate-approle: mint failed. Old SecretID untouched." >&2
        return 1
    end

    set -l new_secret_id (echo $json | jq -r '.data.secret_id')
    set -l new_accessor (echo $json | jq -r '.data.secret_id_accessor')
    # macOS date first, GNU date fallback.
    set -l expires_at (date -u -v+90d '+%Y-%m-%d' 2>/dev/null; \
        or date -u -d '+90 days' '+%Y-%m-%d')

    echo ""
    echo "Update 1P item \"$item\" with these values:"
    echo ""
    echo "  password           = $new_secret_id"
    echo "  secret_id_accessor = $new_accessor"
    echo "  expires_at         = $expires_at"
    echo ""
    echo "Both old and new SecretIDs are currently valid."
    read -P "Press enter once 1P is updated (Ctrl+C aborts; run --fix to recover orphans): " _

    # Step 3: revoke old.
    echo "Revoking old SecretID..."
    vault write auth/approle/role/$role/secret-id-accessor/destroy \
        secret_id_accessor=$old_accessor
    if test $status -ne 0
        echo "rotate-approle: revoke failed. Run: rotate-approle --fix $role" >&2
        return 1
    end
    echo "✓ Old SecretID revoked."
    echo ""
    echo "Next: homelab-env  (re-pull new SecretID into env)"
    echo "Then: set -e VAULT_TOKEN  (don't leave root token in env)"
end
