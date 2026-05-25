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
# Shell-independent sibling (bash/zsh): <homelab-repo>/.config/scripts/homelab.sh
# Both files MUST stay in sync — same public surface, same 1P items.
#
# Public functions:
#   homelab-env          — load homelab env vars (cached 24h, --refresh|--clear)
#   set-vault-token      — set VAULT_TOKEN from a named source (root|approle)
#   vault-root-token     — echo the Vault root token from 1P (value-producer)
#   set-aws-creds        — set AWS_ACCESS_KEY_ID/SECRET from 1P (bootstrap|state)
#   rotate-approle       — rotate a Vault AppRole SecretID (--help, --fix)
#
# Extending:
#   - New env var loaded by homelab-env: append to $__homelab_env_map.
#     (After editing the map, run: homelab-env --refresh)
#   - New VAULT_TOKEN source: add a case to set-vault-token's switch.
#   - New AppRole role for rotation: append to $__homelab_approle_items.
#
# Cache: homelab-env writes the loaded env (1P-sourced vars + static vars
# like KUBECONFIG, + VAULT_TOKEN if set) to TWO sibling files atomically:
#
#   $__homelab_cache_path_fish — fish-format (set -gx KEY 'value')
#   $__homelab_cache_path_sh   — POSIX-format (export KEY='value')
#
# Both files are written on every cache update so bash/zsh and fish can
# share the same cache. Each shell sources its own format. The bash/zsh
# sibling (.config/scripts/homelab.sh) reads the .sh file; this fish file
# reads the .fish file. Permissions: 0600 under 0700 parent dir.
#
# set-vault-token + set-aws-creds also rewrite the cache on success, so
# subshells sourcing the cache inherit the latest tokens without having
# to re-run homelab-env.
#
# Cache is considered fresh while file mtime is within
# $__homelab_cache_ttl_seconds; on hit, homelab-env sources the file and
# skips 1P entirely.
#
# All credentials come from the 1Password "Homelab 2.0" vault.

# === Config ===

set -g __homelab_op_vault 'Homelab 2.0'

# Each entry: "ENV_VAR|1P item name|field"
# Fetched from 1Password by homelab-env, cached to disk.
set -g __homelab_env_map \
    "VAULT_ADDR|Ansible - Vault - k3s|url" \
    "ANSIBLE_HASHI_VAULT_AUTH_METHOD|Ansible - Vault - k3s|method" \
    "ANSIBLE_HASHI_VAULT_ROLE_ID|Ansible - Vault - k3s|username" \
    "ANSIBLE_HASHI_VAULT_SECRET_ID|Ansible - Vault - k3s|password" \
    "CLOUDFLARE_API_TOKEN|Cloudflare - Terraform|credential" \
    "AUTHENTIK_TOKEN|Asgard - Authentik - akadmin API token|credential" \
    "AUTHENTIK_URL|Asgard - Authentik - akadmin API token|url" \
    "ADGUARD_USERNAME|Adguard - admin|username" \
    "ADGUARD_PASSWORD|Adguard - admin|password"

# Each entry: "ENV_VAR|literal value"
# Static (non-1P) env vars — written into the cache alongside 1P vars on
# every refresh. Edit + run: homelab-env --refresh
# (Cache hit sources the cached values; changes here require --refresh.)
set -g __homelab_static_env_map \
    "KUBECONFIG|$HOME/.kube/niflheim-asgard.yaml" \
    "ADGUARD_HOST|10.0.11.201" \
    "ADGUARD_SCHEME|http" \
    "AWS_DEFAULT_REGION|eu-west-1"

# Dual-format cache (see header). Both files have the same TTL — freshness
# is checked against the fish file's mtime (both are written together).
set -g __homelab_cache_dir "$HOME/.cache/homelab"
set -g __homelab_cache_path_fish "$__homelab_cache_dir/env.fish"
set -g __homelab_cache_path_sh "$__homelab_cache_dir/env.sh"
set -g __homelab_cache_ttl_seconds 86400

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

function __homelab_apply_static_env \
    --description "Set the static (non-1P) env vars from __homelab_static_env_map"
    for entry in $__homelab_static_env_map
        set -l parts (string split -m 1 "|" -- $entry)
        set -gx $parts[1] $parts[2]
    end
end

function __homelab_posix_quote --argument-names v \
    --description "POSIX single-quoted form, handling embedded single quotes"
    # POSIX: ' inside '...' is impossible; the idiom is '\'' (close, literal, reopen).
    set -l escaped (printf '%s' $v | sed "s/'/'\\\\''/g")
    printf "'%s'" $escaped
end

function __homelab_cache_age_seconds \
    --description "Echo seconds since fish-cache mtime; return 1 if missing"
    if not test -f $__homelab_cache_path_fish
        return 1
    end
    # macOS BSD stat; the control node is macOS so no GNU fallback needed.
    set -l mtime (stat -f %m $__homelab_cache_path_fish)
    or return 1
    set -l now (date +%s)
    math $now - $mtime
end

function __homelab_cache_is_fresh \
    --description "True if cache exists and is younger than the TTL"
    set -l age (__homelab_cache_age_seconds)
    or return 1
    test $age -lt $__homelab_cache_ttl_seconds
end

function __homelab_cache_write \
    --description "Persist env (static + 1P + VAULT_TOKEN) to BOTH fish + sh cache files atomically"
    mkdir -p $__homelab_cache_dir
    chmod 700 $__homelab_cache_dir

    # Order of emission: static first (KUBECONFIG etc.), then 1P, then VAULT_TOKEN.
    set -l vars
    for entry in $__homelab_static_env_map
        set -l parts (string split -m 1 "|" -- $entry)
        set -a vars $parts[1]
    end
    for entry in $__homelab_env_map
        set -l parts (string split "|" -- $entry)
        set -a vars $parts[1]
    end
    if set -q VAULT_TOKEN
        set -a vars VAULT_TOKEN
    end
    # AWS creds come from set-aws-creds, not env_map. Cache whichever identity
    # is currently in env so a new shell inherits it on cache-hit.
    if set -q AWS_ACCESS_KEY_ID
        set -a vars AWS_ACCESS_KEY_ID
    end
    if set -q AWS_SECRET_ACCESS_KEY
        set -a vars AWS_SECRET_ACCESS_KEY
    end

    set -l ts (date -u '+%Y-%m-%dT%H:%M:%SZ')
    set -l header_fish "# homelab env cache (fish) — written $ts, TTL "$__homelab_cache_ttl_seconds"s
# Sourced by homelab-env when fresh. Do not edit; run: homelab-env --refresh"
    set -l header_sh "# homelab env cache (sh/bash/zsh) — written $ts, TTL "$__homelab_cache_ttl_seconds"s
# Sourced by homelab-env when fresh. Do not edit; run: homelab-env --refresh"

    set -l tmp_fish (mktemp "$__homelab_cache_dir/env.fish.XXXXXX")
    or return 1
    set -l tmp_sh (mktemp "$__homelab_cache_dir/env.sh.XXXXXX")
    or begin; rm -f $tmp_fish; return 1; end
    chmod 600 $tmp_fish $tmp_sh

    echo $header_fish > $tmp_fish
    echo $header_sh > $tmp_sh
    for env_var in $vars
        if set -q $env_var
            echo "set -gx $env_var "(string escape -- $$env_var) >> $tmp_fish
            echo "export $env_var="(__homelab_posix_quote $$env_var) >> $tmp_sh
        end
    end

    mv $tmp_fish $__homelab_cache_path_fish
    mv $tmp_sh $__homelab_cache_path_sh
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

function homelab-env --description "Load homelab env vars (cached 24h, --refresh|--clear)"
    argparse -n homelab-env h/help r/refresh c/clear -- $argv
    or return

    if set -q _flag_help
        echo "Usage:"
        echo "  homelab-env             Source cache if fresh, else fetch from 1P + cache."
        echo "  homelab-env --refresh   Skip cache, re-fetch from 1P, rewrite cache."
        echo "  homelab-env --clear     Remove the cache files (both fish + sh)."
        echo "  homelab-env --help      This help."
        echo ""
        echo "Cache (fish): $__homelab_cache_path_fish"
        echo "Cache (sh):   $__homelab_cache_path_sh"
        echo "TTL:          $__homelab_cache_ttl_seconds seconds"
        return 0
    end

    if set -q _flag_clear
        set -l removed 0
        for path in $__homelab_cache_path_fish $__homelab_cache_path_sh
            if test -f $path
                rm $path
                echo "Cleared $path"
                set removed (math $removed + 1)
            end
        end
        if test $removed -eq 0
            echo "No cache to clear."
        end
        return 0
    end

    if not set -q _flag_refresh; and __homelab_cache_is_fresh
        source $__homelab_cache_path_fish
        set -l age (__homelab_cache_age_seconds)
        set -l remaining_h (math --scale=1 "($__homelab_cache_ttl_seconds - $age) / 3600")
        echo "Loaded homelab env from cache (refresh in "$remaining_h"h, or: homelab-env --refresh)"
        return 0
    end

    # Cache miss: apply static, fetch 1P, prompt, write cache.
    __homelab_apply_static_env

    # === Fresh fetch from 1P ===

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
    echo "Loaded $loaded homelab env vars from 1P"

    # AWS creds via canonical loader. State is the default; swap to admin via
    # `set-aws-creds bootstrap` when re-applying terraform/aws/.
    if not set-aws-creds state
        return 1
    end

    echo ""
    read -P "Set VAULT_TOKEN? [root/approle/skip]: " choice
    set -l choice_status 0
    switch $choice
        case root r
            set-vault-token root
            set choice_status $status
        case approle a
            set-vault-token approle
            set choice_status $status
        case skip s ''
            echo "Skipped VAULT_TOKEN (unchanged)."
        case '*'
            echo "Unknown choice '$choice'; VAULT_TOKEN unchanged." >&2
            set choice_status 1
    end

    # Write cache regardless of VAULT_TOKEN outcome — env vars loaded fine.
    # If VAULT_TOKEN is set (from this run or a prior one), it gets cached too.
    __homelab_cache_write
    set -l ttl_h (math --scale=1 "$__homelab_cache_ttl_seconds / 3600")
    echo "Cached for "$ttl_h"h ($__homelab_cache_dir/env.{fish,sh})"

    return $choice_status
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
    # VAULT_TOKEN is useless without VAULT_ADDR; the check also guarantees
    # homelab-env has run so __homelab_cache_write below captures the full env
    # rather than truncating the cache to just VAULT_TOKEN.
    if not set -q VAULT_ADDR
        echo "set-vault-token: VAULT_ADDR not set. Run homelab-env first." >&2
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

    # Persist to the shared cache so bash/zsh/fish subshells sourcing
    # ~/.cache/homelab/env.{sh,fish} inherit the token. Without this,
    # set-vault-token would only affect the current shell.
    __homelab_cache_write
end

# === Public: AWS creds ===
#
# Two AWS identities live in 1P:
#   - "AWS - Terraform - Bootstrap" — admin, for re-applying terraform/aws/
#   - "AWS - Terraform - State"     — narrow S3-only, for downstream terraform
#
# set-aws-creds is the canonical AWS-creds loader. homelab-env autocalls
# `set-aws-creds state` after the env_map loop; `set-aws-creds bootstrap` is
# the escape hatch for admin operations. After bootstrap work, swap back via:
#   set-aws-creds state    — direct swap back
#   homelab-env --refresh  — also re-pulls everything else from 1P

function set-aws-creds --description "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY from a named source"
    if test (count $argv) -lt 1
        echo "Usage: set-aws-creds <source>" >&2
        echo "  Sources: bootstrap, state" >&2
        return 1
    end
    set -l item
    switch $argv[1]
        case bootstrap b
            set item 'AWS - Terraform - Bootstrap'
        case state s
            set item 'AWS - Terraform - State'
        case '*'
            echo "set-aws-creds: unknown source '$argv[1]'" >&2
            echo "  Sources: bootstrap, state" >&2
            return 1
    end
    set -l access_key (__homelab_op_field $item username)
    if test $status -ne 0
        echo "set-aws-creds: failed to read username from 1P item \"$item\"" >&2
        return 1
    end
    set -l secret_key (__homelab_op_field $item credential)
    if test $status -ne 0
        echo "set-aws-creds: failed to read credential from 1P item \"$item\"" >&2
        return 1
    end
    set -gx AWS_ACCESS_KEY_ID $access_key
    set -gx AWS_SECRET_ACCESS_KEY $secret_key
    echo "AWS creds set ($argv[1], from 1P)"
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
    echo "Then: set -e VAULT_TOKEN; homelab-env --refresh  (clears the cached copy too)"
end
