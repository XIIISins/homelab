# .config/scripts/homelab.sh — Control-node tooling for the homelab (bash/zsh).
#
# Repo:   <homelab-repo>/.config/scripts/homelab.sh
# Source from .bashrc / .zshrc:
#   . ~/.config/scripts/homelab.sh
#
# Fish-native equivalent: <homelab-repo>/.config/fish/conf.d/homelab.fish
# Both files MUST stay in sync — same public surface, same 1P items, same
# behaviour. When you add a new env var / AppRole role / token source,
# update BOTH.
#
# This file MUST be sourced (not executed) — env vars set in a child
# process don't reach the parent shell.
#
# Public functions:
#   homelab-env          — load homelab env vars (cached 24h, --refresh|--clear)
#   set-vault-token      — set VAULT_TOKEN from a named source (root|approle)
#   vault-root-token     — echo the Vault root token from 1P (value-producer)
#   rotate-approle       — rotate a Vault AppRole SecretID (--help, --fix)
#
# Extending:
#   - New env var loaded by homelab-env: append to __homelab_env_map.
#     (After editing the map, run: homelab-env --refresh)
#   - New static (non-1P) var: append to __homelab_static_env_map.
#   - New VAULT_TOKEN source: add a case to set-vault-token's switch.
#   - New AppRole role for rotation: append to __homelab_approle_items.
#
# Cache: homelab-env writes the loaded env (static + 1P + VAULT_TOKEN if set)
# to TWO sibling files atomically so the fish + bash/zsh tooling share state:
#
#   $__homelab_cache_path_fish — fish-format (set -gx KEY 'value')
#   $__homelab_cache_path_sh   — POSIX-format (export KEY='value')
#
# This file sources env.sh on a cache hit. The fish sibling sources env.fish.
# Permissions: 0600 file under 0700 parent dir. Freshness: file mtime + TTL.

# === Config ===

__homelab_op_vault='Homelab 2.0'

# Each entry: "ENV_VAR|1P item name|field"
# Fetched from 1Password by homelab-env, cached to disk.
__homelab_env_map=(
    "VAULT_ADDR|Ansible - Vault - k3s|url"
    "ANSIBLE_HASHI_VAULT_AUTH_METHOD|Ansible - Vault - k3s|method"
    "ANSIBLE_HASHI_VAULT_ROLE_ID|Ansible - Vault - k3s|username"
    "ANSIBLE_HASHI_VAULT_SECRET_ID|Ansible - Vault - k3s|password"
    "CLOUDFLARE_API_TOKEN|Cloudflare - Terraform|credential"
    "AUTHENTIK_TOKEN|Asgard - Authentik - akadmin API token|credential"
    "AUTHENTIK_URL|Asgard - Authentik - akadmin API token|url"
    "ADGUARD_USERNAME|Adguard - admin|username"
    "ADGUARD_PASSWORD|Adguard - admin|password"
)

# Each entry: "ENV_VAR|literal value"
# Static (non-1P) env vars — written into the cache alongside 1P vars on
# every refresh. Edit + run: homelab-env --refresh
__homelab_static_env_map=(
    "KUBECONFIG|$HOME/.kube/niflheim-asgard.yaml"
    "ADGUARD_HOST|10.0.11.201"
    "ADGUARD_SCHEME|http"
)

# Dual-format cache (see header). Both files have the same TTL — freshness
# is checked against the .sh file's mtime (both are written together).
__homelab_cache_dir="$HOME/.cache/homelab"
__homelab_cache_path_fish="$__homelab_cache_dir/env.fish"
__homelab_cache_path_sh="$__homelab_cache_dir/env.sh"
__homelab_cache_ttl_seconds=86400

# Each entry: "approle role name|1P item name"
# 1P item must have fields: username (RoleID), password (SecretID),
# secret_id_accessor, expires_at.
__homelab_approle_items=(
    "ansible-local|Ansible - Vault - k3s"
    # "ansible-awx|Ansible - Vault - AWX"   # add when AWX is deployed
)

# === Helpers (private) ===

__homelab_op_field() {
    # $1 = item name, $2 = field name
    op read "op://$__homelab_op_vault/$1/$2"
}

__homelab_apply_static_env() {
    local entry env_var value
    for entry in "${__homelab_static_env_map[@]}"; do
        env_var=${entry%%|*}
        value=${entry#*|}
        export "$env_var=$value"
    done
}

# POSIX single-quoted form: ' inside '...' is impossible, so the idiom is
# '\'' (close, literal, reopen). Safe to source from sh/bash/zsh/dash.
__homelab_posix_quote() {
    local v=$1 escaped
    escaped=$(printf '%s' "$v" | sed "s/'/'\\\\''/g")
    printf "'%s'" "$escaped"
}

# Fish single-quoted form: only \\ and \' are escapes. Escape backslash
# FIRST so the new backslashes don't get reprocessed.
__homelab_fish_quote() {
    local v=$1 escaped
    escaped=$(printf '%s' "$v" | sed -e 's/\\/\\\\/g' -e "s/'/\\\\'/g")
    printf "'%s'" "$escaped"
}

__homelab_cache_age_seconds() {
    # Returns 1 if cache missing or stat fails; else prints seconds since mtime.
    [ -f "$__homelab_cache_path_sh" ] || return 1
    # macOS BSD stat; control node is macOS.
    local mtime now
    mtime=$(stat -f %m "$__homelab_cache_path_sh") || return 1
    now=$(date +%s)
    printf '%s\n' $((now - mtime))
}

__homelab_cache_is_fresh() {
    local age
    age=$(__homelab_cache_age_seconds) || return 1
    [ "$age" -lt "$__homelab_cache_ttl_seconds" ]
}

# Write env (static + 1P + VAULT_TOKEN if set) to BOTH fish + sh cache files
# atomically (mktemp + mv). Bash/zsh-only — uses arrays + ${!var}.
__homelab_cache_write() {
    local entry env_var ts header_fish header_sh tmp_fish tmp_sh
    local -a vars=()

    mkdir -p "$__homelab_cache_dir"
    chmod 700 "$__homelab_cache_dir"

    # Order: static first (KUBECONFIG etc.), then 1P, then VAULT_TOKEN.
    for entry in "${__homelab_static_env_map[@]}"; do
        vars+=("${entry%%|*}")
    done
    for entry in "${__homelab_env_map[@]}"; do
        vars+=("${entry%%|*}")
    done
    [ -n "${VAULT_TOKEN:-}" ] && vars+=("VAULT_TOKEN")

    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    header_fish="# homelab env cache (fish) — written $ts, TTL ${__homelab_cache_ttl_seconds}s
# Sourced by homelab-env when fresh. Do not edit; run: homelab-env --refresh"
    header_sh="# homelab env cache (sh/bash/zsh) — written $ts, TTL ${__homelab_cache_ttl_seconds}s
# Sourced by homelab-env when fresh. Do not edit; run: homelab-env --refresh"

    tmp_fish=$(mktemp "$__homelab_cache_dir/env.fish.XXXXXX") || return 1
    tmp_sh=$(mktemp "$__homelab_cache_dir/env.sh.XXXXXX") || { rm -f "$tmp_fish"; return 1; }
    chmod 600 "$tmp_fish" "$tmp_sh"

    printf '%s\n' "$header_fish" > "$tmp_fish"
    printf '%s\n' "$header_sh" > "$tmp_sh"
    for env_var in "${vars[@]}"; do
        # printenv works in both bash + zsh; cached vars are all exported.
        local value
        if value=$(printenv "$env_var"); then
            printf 'set -gx %s %s\n' "$env_var" "$(__homelab_fish_quote "$value")" >> "$tmp_fish"
            printf 'export %s=%s\n' "$env_var" "$(__homelab_posix_quote "$value")" >> "$tmp_sh"
        fi
    done

    mv "$tmp_fish" "$__homelab_cache_path_fish"
    mv "$tmp_sh" "$__homelab_cache_path_sh"
}

__homelab_approle_item_for() {
    local role=$1 entry name item
    for entry in "${__homelab_approle_items[@]}"; do
        IFS='|' read -r name item <<<"$entry"
        if [ "$name" = "$role" ]; then
            printf '%s\n' "$item"
            return 0
        fi
    done
    return 1
}

# POSIX-ish prompt that works in bash + zsh; printf to stderr so the
# returned value is clean for $(...) capture.
__homelab_prompt() {
    local _msg=$1 _reply
    printf '%s' "$_msg" >&2
    IFS= read -r _reply
    printf '%s' "$_reply"
}

__homelab_rotate_approle_help() {
    echo "Usage:"
    echo "  rotate-approle <role>         Mint new SecretID, update 1P, revoke old."
    echo "  rotate-approle --fix <role>   Destroy SecretIDs in Vault that aren't in 1P."
    echo "  rotate-approle --help         This help."
    echo ""
    echo "Known roles:"
    local entry role item
    for entry in "${__homelab_approle_items[@]}"; do
        IFS='|' read -r role item <<<"$entry"
        echo "  $role → 1P item: \"$item\""
    done
    echo ""
    echo "Hazard:"
    echo "  Any partial rotation that updates 1P but leaves the old SecretID alive"
    echo "  in Vault — Ctrl+C between paste and revoke, OR a failed revoke step —"
    echo "  produces an orphan SecretID. 1P now points at the NEW accessor, so"
    echo "  re-running the normal rotation would target the wrong SecretID for"
    echo "  revocation. Recovery: rotate-approle --fix <role>"
}

__homelab_rotate_approle_fix() {
    local role=$1 item=$2
    local canonical json all acc canonical_present=0
    local orphans=()
    local meta created last_updated num_uses confirm destroyed=0 errors=0

    if ! canonical=$(__homelab_op_field "$item" secret_id_accessor); then
        echo "rotate-approle --fix: failed to read secret_id_accessor from 1P item \"$item\"" >&2
        return 1
    fi
    echo "Canonical accessor (in 1P): $canonical"
    echo ""

    if ! json=$(vault list -format=json "auth/approle/role/$role/secret-id" 2>/dev/null); then
        echo "rotate-approle --fix: no SecretIDs in Vault for role '$role'." >&2
        echo "If 1P holds an accessor, it's stale — the SecretID was already destroyed." >&2
        echo "Resolution: full re-bootstrap (see AppRole bootstrap runbook)." >&2
        return 1
    fi

    # vault list -format=json can return either a bare array or {data:{keys:[...]}}
    # depending on version. Try wrapped form first, fall back to bare.
    all=$(echo "$json" | jq -r '.data.keys[]?' 2>/dev/null)
    if [ -z "$all" ]; then
        all=$(echo "$json" | jq -r '.[]?' 2>/dev/null)
    fi

    while IFS= read -r acc; do
        [ -z "$acc" ] && continue
        if [ "$acc" = "$canonical" ]; then
            canonical_present=1
        else
            orphans+=("$acc")
        fi
    done <<<"$all"

    if [ $canonical_present -eq 0 ]; then
        echo "WARNING: 1P's accessor ($canonical) does NOT exist in Vault." >&2
        echo "1P is stale — playbooks WILL fail. Resolution: full re-bootstrap." >&2
        return 1
    fi

    if [ ${#orphans[@]} -eq 0 ]; then
        echo "✓ No orphans. Vault and 1P are in sync."
        return 0
    fi

    echo "Found ${#orphans[@]} orphan accessor(s):"
    echo ""
    for acc in "${orphans[@]}"; do
        echo "  $acc"
        if meta=$(vault write -format=json \
            "auth/approle/role/$role/secret-id-accessor/lookup" \
            "secret_id_accessor=$acc" 2>/dev/null); then
            created=$(echo "$meta" | jq -r '.data.creation_time // "?"')
            last_updated=$(echo "$meta" | jq -r '.data.last_updated_time // "?"')
            num_uses=$(echo "$meta" | jq -r '.data.secret_id_num_uses // "?"')
            echo "    created:      $created"
            echo "    last_updated: $last_updated"
            echo "    num_uses:     $num_uses"
        fi
        echo ""
    done

    confirm=$(__homelab_prompt "Destroy all ${#orphans[@]} orphan(s)? [y/N] ")
    if [ "$confirm" != y ] && [ "$confirm" != Y ]; then
        echo "Aborted. No changes made."
        return 1
    fi

    for acc in "${orphans[@]}"; do
        if vault write "auth/approle/role/$role/secret-id-accessor/destroy" \
            "secret_id_accessor=$acc" >/dev/null; then
            echo "  ✓ destroyed $acc"
            destroyed=$((destroyed + 1))
        else
            echo "  ✗ failed:    $acc" >&2
            errors=$((errors + 1))
        fi
    done
    echo ""
    echo "Destroyed $destroyed orphan(s)."
    if [ $errors -gt 0 ]; then
        echo "$errors failure(s)." >&2
        return 1
    fi
}

# === Public: env loading ===

homelab-env() {
    local refresh=0 clear=0 path removed=0
    local errors=0 loaded=0 entry env_var item field value choice choice_status=0
    local age remaining_h ttl_h

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
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
                ;;
            -r|--refresh) refresh=1; shift ;;
            -c|--clear)   clear=1;   shift ;;
            --)           shift; break ;;
            -*)
                echo "homelab-env: unknown flag '$1'. See: homelab-env --help" >&2
                return 1
                ;;
            *)
                echo "homelab-env: unexpected argument '$1'. See: homelab-env --help" >&2
                return 1
                ;;
        esac
    done

    if [ $clear -eq 1 ]; then
        for path in "$__homelab_cache_path_fish" "$__homelab_cache_path_sh"; do
            if [ -f "$path" ]; then
                rm "$path"
                echo "Cleared $path"
                removed=$((removed + 1))
            fi
        done
        [ $removed -eq 0 ] && echo "No cache to clear."
        return 0
    fi

    if [ $refresh -eq 0 ] && __homelab_cache_is_fresh; then
        # shellcheck disable=SC1090
        . "$__homelab_cache_path_sh"
        age=$(__homelab_cache_age_seconds)
        remaining_h=$(( (__homelab_cache_ttl_seconds - age) / 3600 ))
        echo "Loaded homelab env from cache (refresh in ${remaining_h}h, or: homelab-env --refresh)"
        return 0
    fi

    # Cache miss: apply static, fetch 1P, prompt, write cache.
    __homelab_apply_static_env

    for entry in "${__homelab_env_map[@]}"; do
        IFS='|' read -r env_var item field <<<"$entry"
        if value=$(__homelab_op_field "$item" "$field"); then
            export "$env_var=$value"
            loaded=$((loaded + 1))
        else
            echo "  ↳ failed to load $env_var from \"$item\"/$field" >&2
            errors=$((errors + 1))
            continue
        fi
    done
    if [ $errors -gt 0 ]; then
        echo "" >&2
        echo "Loaded $loaded vars, $errors errors." >&2
        echo "If 1Password isn't signed in, run: op signin" >&2
        return 1
    fi
    echo "Loaded $loaded homelab env vars from 1P"

    echo ""
    choice=$(__homelab_prompt "Set VAULT_TOKEN? [root/approle/skip]: ")
    echo ""
    case "$choice" in
        root|r)
            set-vault-token root
            choice_status=$?
            ;;
        approle|a)
            set-vault-token approle
            choice_status=$?
            ;;
        skip|s|"")
            echo "Skipped VAULT_TOKEN (unchanged)."
            ;;
        *)
            echo "Unknown choice '$choice'; VAULT_TOKEN unchanged." >&2
            choice_status=1
            ;;
    esac

    # Write cache regardless of VAULT_TOKEN outcome — env vars loaded fine.
    __homelab_cache_write
    ttl_h=$(( __homelab_cache_ttl_seconds / 3600 ))
    echo "Cached for ${ttl_h}h ($__homelab_cache_dir/env.{fish,sh})"

    return $choice_status
}

# === Public: vault tokens ===

vault-root-token() {
    __homelab_op_field "Asgard - Vault - Root Token" password
}

set-vault-token() {
    local value json
    if [ $# -lt 1 ]; then
        echo "Usage: set-vault-token <source>" >&2
        echo "  Sources: root, approle" >&2
        return 1
    fi
    case "$1" in
        root)
            if ! value=$(vault-root-token); then
                echo "set-vault-token: failed to read root token from 1P" >&2
                return 1
            fi
            export VAULT_TOKEN="$value"
            echo "VAULT_TOKEN set (root, from 1P)"
            ;;
        approle)
            if [ -z "${VAULT_ADDR:-}" ]; then
                echo "set-vault-token: VAULT_ADDR not set. Run homelab-env first." >&2
                return 1
            fi
            if [ -z "${ANSIBLE_HASHI_VAULT_ROLE_ID:-}" ] \
                || [ -z "${ANSIBLE_HASHI_VAULT_SECRET_ID:-}" ]; then
                echo "set-vault-token: AppRole creds not in env. Run homelab-env first." >&2
                return 1
            fi
            if ! json=$(vault write -format=json auth/approle/login \
                role_id="$ANSIBLE_HASHI_VAULT_ROLE_ID" \
                secret_id="$ANSIBLE_HASHI_VAULT_SECRET_ID"); then
                echo "set-vault-token: AppRole login failed" >&2
                return 1
            fi
            export VAULT_TOKEN="$(echo "$json" | jq -r '.auth.client_token')"
            echo "VAULT_TOKEN set (approle, freshly minted)"
            ;;
        *)
            echo "set-vault-token: unknown source '$1'" >&2
            echo "  Sources: root, approle" >&2
            return 1
            ;;
    esac
}

# === Public: AppRole rotation ===

rotate-approle() {
    local fix=0 role item old_accessor json new_secret_id new_accessor expires_at _ignored

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                __homelab_rotate_approle_help
                return 0
                ;;
            --fix)
                fix=1
                shift
                ;;
            --)
                shift
                break
                ;;
            -*)
                echo "rotate-approle: unknown flag '$1'. See: rotate-approle --help" >&2
                return 1
                ;;
            *)
                break
                ;;
        esac
    done

    if [ $# -eq 0 ]; then
        __homelab_rotate_approle_help
        return 0
    fi

    if [ $# -ne 1 ]; then
        echo "rotate-approle: expected 1 role argument, got $#. See: rotate-approle --help" >&2
        return 1
    fi

    role=$1

    if ! item=$(__homelab_approle_item_for "$role"); then
        echo "rotate-approle: unknown role '$role'. See: rotate-approle --help" >&2
        return 1
    fi

    if [ -z "${VAULT_TOKEN:-}" ]; then
        echo "rotate-approle: VAULT_TOKEN not set. Run: set-vault-token root" >&2
        return 1
    fi
    if [ -z "${VAULT_ADDR:-}" ]; then
        echo "rotate-approle: VAULT_ADDR not set. Run: homelab-env" >&2
        return 1
    fi

    if [ $fix -eq 1 ]; then
        __homelab_rotate_approle_fix "$role" "$item"
        return $?
    fi

    # === Normal rotation flow ===

    # Step 1: snapshot old accessor BEFORE 1P gets updated.
    if ! old_accessor=$(__homelab_op_field "$item" secret_id_accessor); then
        echo "rotate-approle: failed to read secret_id_accessor from 1P item \"$item\"" >&2
        echo "Field must exist on the item — see AppRole bootstrap runbook." >&2
        return 1
    fi

    echo "Rotating AppRole '$role' (1P item: \"$item\")"
    echo "Old accessor: $old_accessor"
    echo ""

    # Step 2: mint new SecretID. Both old and new now valid — safe window for
    # the human to update 1P. Old is revoked only after enter is pressed.
    echo "Minting new SecretID..."
    if ! json=$(vault write -f -format=json "auth/approle/role/$role/secret-id"); then
        echo "rotate-approle: mint failed. Old SecretID untouched." >&2
        return 1
    fi

    new_secret_id=$(echo "$json" | jq -r '.data.secret_id')
    new_accessor=$(echo "$json" | jq -r '.data.secret_id_accessor')
    # macOS date first, GNU date fallback.
    expires_at=$(date -u -v+90d '+%Y-%m-%d' 2>/dev/null \
        || date -u -d '+90 days' '+%Y-%m-%d')

    echo ""
    echo "Update 1P item \"$item\" with these values:"
    echo ""
    echo "  password           = $new_secret_id"
    echo "  secret_id_accessor = $new_accessor"
    echo "  expires_at         = $expires_at"
    echo ""
    echo "Both old and new SecretIDs are currently valid."
    _ignored=$(__homelab_prompt "Press enter once 1P is updated (Ctrl+C aborts; run --fix to recover orphans): ")

    # Step 3: revoke old.
    echo "Revoking old SecretID..."
    if ! vault write "auth/approle/role/$role/secret-id-accessor/destroy" \
        "secret_id_accessor=$old_accessor"; then
        echo "rotate-approle: revoke failed. Run: rotate-approle --fix $role" >&2
        return 1
    fi
    echo "✓ Old SecretID revoked."
    echo ""
    echo "Next: homelab-env  (re-pull new SecretID into env)"
    echo "Then: unset VAULT_TOKEN  (don't leave root token in env)"
}
