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
#   homelab-env           — load homelab env vars from 1P (cached 24h, --refresh|--clear)
#   vault-homelab-env     — load IaC env from Vault via AppRole, NO 1Password
#                           (cached 3h to a SEPARATE vault-env.{fish,sh}). For
#                           the MacBook as a control node away from home.
#   seed-vault-approle    — (re)write the local ansible-local secret-zero file
#                           from the loaded env; run after a rotate-approle so
#                           vault-homelab-env keeps working without 1P.
#   set-vault-token       — set VAULT_TOKEN from a named source (root|approle)
#   vault-root-token      — echo the Vault root token from 1P (value-producer)
#   set-aws-creds         — set AWS_ACCESS_KEY_ID/SECRET from 1P (bootstrap|state)
#   set-proxmox-password  — set PROXMOX_VE_PASSWORD from 1P (manual; for asgard-lxcs-root)
#   rotate-approle        — rotate a Vault AppRole SecretID (--help, --fix)
#   rotate-vault-root-token — rotate the Vault root token + update 1P (needs
#                           an interactive terminal — confirms before revoke)
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
# set-vault-token + set-aws-creds + set-proxmox-password also rewrite the
# cache on success, so subshells (bash/zsh/fish) sourcing the cache inherit
# the latest tokens without having to re-run homelab-env.
#
# This file sources env.sh on a cache hit. The fish sibling sources env.fish.
# Permissions: 0600 file under 0700 parent dir. Freshness: file mtime + TTL.

# === Config ===

__homelab_op_vault='Homelab 2.0'

# 1P item UUIDs for machine-accessed credentials. Referenced by UUID (not
# title) so the 1P items can be renamed/reorganised freely without breaking
# homelab-env or Terraform. The comment on each line is the *current* 1P
# title — informational only; the UUID is the stable key. To find a UUID:
#   op item list --vault 'Homelab 2.0' --format=json | jq -r '.[]|"\(.id)\t\(.title)"'
__op_ansible_vault_k3s='4srpqv2mt2vditxo7g5rqjquti' # [Asgard] - Ansible - Vault - AppRole (ansible-local)
__op_ansible_vault_frigg='kcmjziie2a75zk4qytdixrprpe' # [Asgard] - Ansible - Vault - AppRole (ansible-frigg)
__op_cloudflare_tf='ps4mc2hv7a777tzsef755te64m'     # [Asgard] - Terraform - Cloudflare - API token
__op_authentik_admin='4pxuhyvygrqqeo3vro24bjrhwa'   # [Asgard] - Terraform - Authentik - Admin API token
__op_adguard_admin='hvh3d7hlivcsbjqqye34f3d7a4'     # [Asgard] - Terraform - AdGuard - Admin login
__op_netbox_admin='lsqb4z5mbeijeqbxx43y5pkl5q'      # [Asgard] - Terraform - NetBox - Admin API token
__op_semaphore_admin='24fmbstdhqzwk6eeru4vvaixsm'   # [Asgard] - Terraform - Semaphore - Admin API token
__op_aws_tf_bootstrap='lhf4xzp3uqehkkease5gidthci'  # [Asgard] - Terraform - AWS - Bootstrap access key
__op_aws_tf_state='jnvf6aokgml7vkjj4ho2xlcvua'      # [Asgard] - Terraform - AWS - State access key
__op_proxmox_root='6vv32uzlahikgmkvkiqfnkgshy'      # [Infra] - Terraform - Proxmox - Root password
__op_vault_root='7g4grolyien2yqkm7me2jficmy'        # [Bootstrap] - Manual - Vault - Root token

# Each entry: "ENV_VAR|1P item UUID|field"
# Fetched from 1Password by homelab-env, cached to disk.
__homelab_env_map=(
    "VAULT_ADDR|${__op_ansible_vault_k3s}|url"
    "ANSIBLE_HASHI_VAULT_AUTH_METHOD|${__op_ansible_vault_k3s}|method"
    "ANSIBLE_HASHI_VAULT_ROLE_ID|${__op_ansible_vault_k3s}|username"
    "ANSIBLE_HASHI_VAULT_SECRET_ID|${__op_ansible_vault_k3s}|password"
    "CLOUDFLARE_API_TOKEN|${__op_cloudflare_tf}|credential"
    "AUTHENTIK_TOKEN|${__op_authentik_admin}|credential"
    "AUTHENTIK_URL|${__op_authentik_admin}|url"
    "ADGUARD_USERNAME|${__op_adguard_admin}|username"
    "ADGUARD_PASSWORD|${__op_adguard_admin}|password"
    "NETBOX_API_TOKEN|${__op_netbox_admin}|credential"
    "SEMAPHOREUI_API_TOKEN|${__op_semaphore_admin}|credential"
)

# Each entry: "ENV_VAR|literal value"
# Static (non-1P) env vars — written into the cache alongside 1P vars on
# every refresh. Edit + run: homelab-env --refresh
__homelab_static_env_map=(
    "KUBECONFIG|$HOME/.kube/niflheim-asgard.yaml"
    "ADGUARD_HOST|10.0.11.201"
    "ADGUARD_SCHEME|http"
    "AWS_DEFAULT_REGION|eu-west-1"
    "NETBOX_SERVER_URL|https://netbox.niflheim.xiiisins.com"
    "SEMAPHOREUI_API_BASE_URL|https://semaphore.niflheim.xiiisins.com/api"
    "ANSIBLE_VAULT_PASSWORD_FILE|$HOME/.vault-pass"
    "ANSIBLE_PRIVATE_KEY_FILE|$HOME/.ssh/ansible_niflheim"
)

# Dual-format cache (see header). Both files have the same TTL — freshness
# is checked against the .sh file's mtime (both are written together).
__homelab_cache_dir="$HOME/.cache/homelab"
__homelab_cache_path_fish="$__homelab_cache_dir/env.fish"
__homelab_cache_path_sh="$__homelab_cache_dir/env.sh"
__homelab_cache_ttl_seconds=86400

# Each entry: "approle role name|1P item UUID"
# 1P item must have fields: username (RoleID), password (SecretID),
# secret_id_accessor, expires_at.
__homelab_approle_items=(
    "ansible-local|${__op_ansible_vault_k3s}"
    "ansible-frigg|${__op_ansible_vault_frigg}"
    # "ansible-awx|<uuid>"   # add when AWX is deployed
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
    # `date -r <file> +%s` gives file mtime as epoch seconds identically on
    # BSD/macOS date AND GNU/Linux date — unlike `stat`, whose -f flag means
    # opposite things on the two (BSD: format string; GNU: filesystem, not
    # file, status). This file is sourced on both the macOS MacBook and
    # Frigg (Linux) — a bare `stat -f %m` broke this function on Frigg with
    # "cannot read file system information for '%m'" (found + fixed
    # 2026-09-03, alongside the zsh `local path` PATH-emptying bug — see
    # known-issues/shell-tooling.md for both).
    local mtime now
    mtime=$(date -r "$__homelab_cache_path_sh" +%s) || return 1
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
    # AWS creds come from set-aws-creds, not env_map. Cache whichever identity
    # is currently in env so a new shell inherits it on cache-hit.
    [ -n "${AWS_ACCESS_KEY_ID:-}" ]     && vars+=("AWS_ACCESS_KEY_ID")
    [ -n "${AWS_SECRET_ACCESS_KEY:-}" ] && vars+=("AWS_SECRET_ACCESS_KEY")
    # PROXMOX_VE_PASSWORD comes from set-proxmox-password (manually called —
    # only needed for terraform/proxmox/asgard-lxcs-root/ applies). Cache it
    # if set so subshells inherit; absent otherwise.
    [ -n "${PROXMOX_VE_PASSWORD:-}" ]   && vars+=("PROXMOX_VE_PASSWORD")
    # ANSIBLE_INVENTORY: set by __homelab_ensure_netbox_inventory on macOS
    # (points at the warm NetBox cache). Cache it so a bare `source env.sh`
    # gets it. Never set on Linux (Frigg) — the conditional keeps it out there.
    [ -n "${ANSIBLE_INVENTORY:-}" ]     && vars+=("ANSIBLE_INVENTORY")

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
    local refresh=0 clear=0 cache_file removed=0
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
        for cache_file in "$__homelab_cache_path_fish" "$__homelab_cache_path_sh"; do
            if [ -f "$cache_file" ]; then
                rm "$cache_file"
                echo "Cleared $cache_file"
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
        __homelab_ensure_netbox_inventory
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

    # AWS creds via canonical loader. State is the default; swap to admin via
    # `set-aws-creds bootstrap` when re-applying terraform/aws/.
    if ! set-aws-creds state; then
        return 1
    fi

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
    # Ensure the NetBox inventory cache + ANSIBLE_INVENTORY BEFORE cache_write,
    # so the export lands in env.{sh,fish} for a bare `source`.
    __homelab_ensure_netbox_inventory refresh

    __homelab_cache_write
    ttl_h=$(( __homelab_cache_ttl_seconds / 3600 ))
    echo "Cached for ${ttl_h}h ($__homelab_cache_dir/env.{fish,sh})"

    return $choice_status
}

# === Public: NetBox inventory warm cache (macOS control node) ===
#
# On macOS, ansible-playbook SIGSEGVs its forked task workers whenever the main
# process has already done HTTP — macOS CoreFoundation (proxy detection, network
# path evaluation, os_log→CFPreferences) is not fork-safe. The NetBox dynamic
# inventory (netbox.netbox.nb_inventory) does HTTP in the main process at
# startup, so any playbook that also does a forked lookup (community.hashi_vault)
# crashes. Linux controllers (Frigg/Semaphore) are immune. Full diagnosis:
# docs/known-issues/frigg-control-node.md.
#
# Fix: materialize the NetBox inventory to a static, leak-free cache in a
# short-lived separate process (which does the HTTP + exits — no forked workers,
# no crash), then point ANSIBLE_INVENTORY at it so playbook runs do zero HTTP in
# the forking process. group_vars (incl. the ansible-vault vault.yml) still load
# normally because the cache lives in inventory/. Refreshed on every
# `homelab-env` (re)fetch; run manually with `refresh-netbox-inventory`.
refresh-netbox-inventory() {
    if [ "$(uname -s)" != Darwin ]; then
        echo "refresh-netbox-inventory: macOS-only (Linux controllers use the live NetBox inventory)."
        return 0
    fi
    local repo script cache
    if ! repo=$(git rev-parse --show-toplevel 2>/dev/null); then
        echo "refresh-netbox-inventory: run from within the homelab repo." >&2
        return 1
    fi
    script="$repo/.config/scripts/refresh-netbox-inventory-cache.sh"
    cache="$repo/ansible/inventory/.netbox-cache.yml"
    if [ ! -x "$script" ]; then
        echo "refresh-netbox-inventory: missing/!x $script" >&2
        return 1
    fi
    "$script" || return 1
    export ANSIBLE_INVENTORY="$cache"
    echo "refresh-netbox-inventory: ANSIBLE_INVENTORY -> $cache"
}

# Internal: called by homelab-env. Best-effort, non-fatal, silent no-op off
# macOS or outside the repo. $1=refresh forces a re-materialize (fresh NetBox
# pull); otherwise it only (re)exports ANSIBLE_INVENTORY when the cache exists,
# and materializes only if it's missing.
__homelab_ensure_netbox_inventory() {
    [ "$(uname -s)" = Darwin ] || return 0
    local repo cache
    repo=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
    cache="$repo/ansible/inventory/.netbox-cache.yml"
    if [ "${1:-}" = refresh ] || [ ! -f "$cache" ]; then
        if ! "$repo/.config/scripts/refresh-netbox-inventory-cache.sh"; then
            echo "  ↳ NetBox inventory cache refresh failed; ansible will fall back to the live" >&2
            echo "    inventory (may fork-crash on macOS). Fix + rerun: refresh-netbox-inventory" >&2
        fi
    fi
    [ -f "$cache" ] && export ANSIBLE_INVENTORY="$cache"
}

# === Public: vault tokens ===

vault-root-token() {
    __homelab_op_field "$__op_vault_root" password
}

set-vault-token() {
    local value json
    if [ $# -lt 1 ]; then
        echo "Usage: set-vault-token <source>" >&2
        echo "  Sources: root, approle" >&2
        return 1
    fi
    # VAULT_TOKEN is useless without VAULT_ADDR; the check also guarantees
    # homelab-env has run so __homelab_cache_write below captures the full env
    # rather than truncating the cache to just VAULT_TOKEN.
    if [ -z "${VAULT_ADDR:-}" ]; then
        echo "set-vault-token: VAULT_ADDR not set. Run homelab-env first." >&2
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

    # Persist to the shared cache so bash/zsh/fish subshells sourcing
    # ~/.cache/homelab/env.{sh,fish} inherit the token. Without this,
    # set-vault-token would only affect the current shell.
    __homelab_cache_write
}

# === Public: AWS creds ===
#
# Two AWS identities live in 1P:
#   - bootstrap — admin, for re-applying terraform/aws/ (UUID $__op_aws_tf_bootstrap)
#   - state     — narrow S3-only, for downstream terraform (UUID $__op_aws_tf_state)
#
# set-aws-creds is the canonical AWS-creds loader. homelab-env autocalls
# `set-aws-creds state` after the env_map loop; `set-aws-creds bootstrap` is
# the escape hatch for admin operations. After bootstrap work, swap back via:
#   set-aws-creds state    — direct swap back
#   homelab-env --refresh  — also re-pulls everything else from 1P

set-aws-creds() {
    local source item access_key secret_key
    if [ $# -lt 1 ]; then
        echo "Usage: set-aws-creds <source>" >&2
        echo "  Sources: bootstrap, state" >&2
        return 1
    fi
    source=$1
    case "$source" in
        bootstrap|b) item="${__op_aws_tf_bootstrap}" ;;
        state|s)     item="${__op_aws_tf_state}" ;;
        *)
            echo "set-aws-creds: unknown source '$source'" >&2
            echo "  Sources: bootstrap, state" >&2
            return 1
            ;;
    esac
    if ! access_key=$(__homelab_op_field "$item" username); then
        echo "set-aws-creds: failed to read username from 1P item \"$item\"" >&2
        return 1
    fi
    if ! secret_key=$(__homelab_op_field "$item" credential); then
        echo "set-aws-creds: failed to read credential from 1P item \"$item\"" >&2
        return 1
    fi
    export AWS_ACCESS_KEY_ID="$access_key"
    export AWS_SECRET_ACCESS_KEY="$secret_key"
    echo "AWS creds set ($source, from 1P)"
}

# === Public: Proxmox root@pam password ===
#
# Only needed for terraform/proxmox/asgard-lxcs-root/ (and any other op
# that talks to Proxmox API endpoints requiring ticket auth — see
# CLAUDE.md "bpg/proxmox API token can change nesting, NOT other LXC
# features"). NOT autocalled by homelab-env — the password's only
# consumer is a single TF module + occasional ad-hoc work, so loading
# it on every shell would be wasted 1P churn.
#
# Usage:
#   set-proxmox-password          — load PROXMOX_VE_PASSWORD from 1P, cache it
#   terraform -chdir=terraform/proxmox/asgard-lxcs-root plan|apply
#
# Cache lifetime mirrors the rest of homelab-env (24h TTL). After 24h
# the cached copy is stale; either re-run set-proxmox-password or let
# homelab-env --refresh rewrite the cache (it preserves whatever's in
# env, so call set-proxmox-password first, then --refresh).

set-proxmox-password() {
    local value
    if ! value=$(__homelab_op_field "${__op_proxmox_root}" password); then
        echo "set-proxmox-password: failed to read password from 1P (Proxmox root, UUID ${__op_proxmox_root})" >&2
        return 1
    fi
    export PROXMOX_VE_PASSWORD="$value"
    echo "PROXMOX_VE_PASSWORD set (from 1P)"

    # Persist to the shared cache so subshells sourcing
    # ~/.cache/homelab/env.{sh,fish} inherit the password. Without this,
    # set-proxmox-password would only affect the current shell.
    __homelab_cache_write
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
    # Read the SecretID's REAL expiration from Vault — authoritative, reflects
    # the mount's max_lease_ttl. Don't assume the role's secret_id_ttl: a +90d
    # guess stamped 1P 58d past the true date back when the mount clamped to 32d.
    # (Surfaced 2026-05-31 Wave S7; mount ceiling since raised to 90d.)
    expires_at=$(vault write -format=json \
        "auth/approle/role/$role/secret-id-accessor/lookup" \
        "secret_id_accessor=$new_accessor" 2>/dev/null \
        | jq -r '.data.expiration_time[0:10] // empty' 2>/dev/null)
    [ -n "$expires_at" ] || expires_at="(lookup failed — look up accessor $new_accessor manually)"

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
    if [ "$role" = "ansible-frigg" ]; then
        echo "Next: re-seed Frigg's secret-zero file (it doesn't read 1P):"
        echo "  ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/asgard-control.yml \\"
        echo "    --tags control-node:vault-env -e control_node_frigg_secret_id=<new-secret-id> -l frigg"
        echo "Then confirm on Frigg: . /usr/local/bin/homelab-env && homelab-env"
    else
        echo "Next: homelab-env  (re-pull new SecretID into env)"
        echo "Then: unset VAULT_TOKEN; homelab-env --refresh  (clears the cached copy too)"
    fi
}

# rotate-vault-root-token — mint a fresh Vault root token, verify it, save it
# to 1P (hash-verified), THEN revoke the old one. Same mint-before-revoke
# safety shape as rotate-approle, but for the one credential that unlocks
# everything else — so it adds two checks rotate-approle doesn't need: the
# new token must -orphan (else revoking the old PARENT token cascades and
# kills the brand-new child too — the single most important correctness
# detail here) and must pass a LIVE lookup against Vault before the old one
# is touched. Requires an interactive terminal (the confirm-before-revoke
# prompt reads stdin) — won't complete end-to-end from a non-interactive
# caller, by design.
rotate-vault-root-token() {
    local old_lookup_json old_accessor new_json new_token new_accessor source_hash verify_hash _ignored

    if [ -z "${VAULT_TOKEN:-}" ]; then
        echo "rotate-vault-root-token: VAULT_TOKEN not set. Run: set-vault-token root" >&2
        return 1
    fi
    if [ -z "${VAULT_ADDR:-}" ]; then
        echo "rotate-vault-root-token: VAULT_ADDR not set. Run: homelab-env" >&2
        return 1
    fi

    echo "Rotating Vault root token (1P item: \"$__op_vault_root\")"

    # Step 1: snapshot the CURRENT token's accessor before anything else.
    # `vault token lookup` has NO `-field` flag (only `-format`) — capture
    # the full `-format=json` (which DOES include the literal token value at
    # .data.id) into a local var, pull out only the accessor via jq, then
    # `unset` the var. Never echo/return `$old_lookup_json` itself.
    if ! old_lookup_json=$(vault token lookup -format=json 2>&1); then
        echo "rotate-vault-root-token: couldn't look up the current token — is VAULT_TOKEN actually a valid root token?" >&2
        echo "$old_lookup_json" >&2
        return 1
    fi
    old_accessor=$(printf '%s' "$old_lookup_json" | jq -r '.data.accessor')
    unset old_lookup_json
    if [ -z "$old_accessor" ] || [ "$old_accessor" = "null" ]; then
        echo "rotate-vault-root-token: couldn't parse the accessor out of the lookup response." >&2
        return 1
    fi
    echo "Old accessor: $old_accessor"

    # Step 2: mint the replacement. -orphan is load-bearing (see header) —
    # without it the new token is a CHILD of the one about to be revoked in
    # Step 6, and would die with its parent. -ttl=0 matches a root token's
    # conventional shape (no expiration; confirmed against the live token's
    # own `creation_ttl: 0` / `expire_time: null` before writing this).
    echo "Minting new root token (orphan, no TTL)..."
    if ! new_json=$(vault token create -policy=root -orphan -ttl=0 -format=json 2>&1); then
        echo "rotate-vault-root-token: mint failed. Old token untouched." >&2
        echo "$new_json" >&2
        return 1
    fi
    new_token=$(printf '%s' "$new_json" | jq -r '.auth.client_token')
    new_accessor=$(printf '%s' "$new_json" | jq -r '.auth.accessor')
    if [ -z "$new_token" ] || [ "$new_token" = "null" ]; then
        echo "rotate-vault-root-token: couldn't parse the new token out of the mint response. Old token untouched." >&2
        return 1
    fi
    echo "New accessor: $new_accessor"

    # Step 3: write to 1P. Shell-var arg form, never stdin — the
    # `vault kv patch <field>=-` stdin pattern has silently produced wrong
    # values before (known-issues/vault.md); same risk applies to `op`.
    echo "Writing new token to 1P..."
    if ! op item edit "$__op_vault_root" "password=$new_token" >/dev/null 2>&1; then
        echo "rotate-vault-root-token: 1P write failed. The new token (accessor $new_accessor) is live but saved NOWHERE durable yet — do not close this terminal." >&2
        echo "Retry: op item edit \"$__op_vault_root\" \"password=\$new_token\" (the value is in \$new_token in THIS shell if you're debugging inline)." >&2
        echo "Or abandon it: vault token revoke -accessor $new_accessor" >&2
        return 1
    fi

    # Step 4: hash-verify the round-trip — never diff literal secret values.
    # `op read` appends a trailing newline to its stdout; piping it straight
    # into shasum hashes "token\n", not "token" — capture into a variable
    # FIRST (command substitution strips trailing newlines in bash/zsh) so
    # both sides hash the identical newline-free value.
    local verify_value
    source_hash=$(printf '%s' "$new_token" | shasum -a 256 | cut -d' ' -f1)
    verify_value=$(op read "op://$__homelab_op_vault/$__op_vault_root/password" 2>/dev/null)
    verify_hash=$(printf '%s' "$verify_value" | shasum -a 256 | cut -d' ' -f1)
    unset verify_value
    if [ "$source_hash" != "$verify_hash" ]; then
        echo "rotate-vault-root-token: 1P write verification FAILED (hash mismatch) — do NOT proceed. Old token is still untouched and still works." >&2
        return 1
    fi
    echo "✓ 1P write verified (hash match)."

    # Step 5: prove the new token actually works, live, before burning the
    # only other valid one. A parse-succeeded mint is not the same as a
    # working credential.
    if ! VAULT_TOKEN="$new_token" vault token lookup >/dev/null 2>&1; then
        echo "rotate-vault-root-token: new token failed a live lookup against Vault. NOT revoking the old one. Investigate before retrying." >&2
        return 1
    fi
    echo "✓ New token verified live against Vault."
    echo ""
    echo "Both tokens are currently valid."
    _ignored=$(__homelab_prompt "Press enter to revoke the OLD root token (accessor $old_accessor) now — Ctrl+C aborts, leaving both valid: ")

    # Step 6: revoke old. By accessor, not by value — never puts the old
    # token's literal value anywhere.
    echo "Revoking old token..."
    if ! vault token revoke -accessor "$old_accessor"; then
        echo "rotate-vault-root-token: revoke failed. New token is live + saved in 1P; old token may still work too — revoke manually:" >&2
        echo "  vault token revoke -accessor $old_accessor" >&2
        return 1
    fi
    echo "✓ Old root token revoked."
    echo ""
    echo "Next: unset VAULT_TOKEN; set-vault-token root   (loads the new token into this shell)"
    echo "Any OTHER shell/session still holding the old VAULT_TOKEN (incl. ~/.cache/homelab/env.sh"
    echo "if this session ran set-vault-token root earlier) is now dead — re-run set-vault-token root there too."
}

# === Vault-backed env loading (no 1Password) ===
#
# vault-homelab-env is the 1P-free twin of homelab-env. It authenticates to
# Vault with the `ansible-local` AppRole (secret-zero in a local 0600 file)
# and pulls every IaC credential from Vault — letting the MacBook act as a
# control node away from home with no `op` dependency. It mirrors Frigg's
# Vault-backed shim (ansible/roles/control-node) but writes a SEPARATE 3h
# cache (vault-env.{fish,sh}) so it never collides with the 1P-backed
# homelab-env cache (env.{fish,sh}).
#
# The MacBook's `ansible-local` AppRole policy is `read secret/data/ansible/*`,
# which already covers the consolidated bundle Frigg reads at
# secret/ansible/frigg/* — no Vault/TF change is needed.
#
# Secret-zero ($__vault_homelab_approle_env) is NOT auto-synced when
# rotate-approle updates 1Password. After a rotation, re-sync it once (with 1P
# available): `homelab-env && seed-vault-approle`.
#
# Caveat: the cached VAULT_TOKEN is an AppRole token (~30min ttl) and the 3h
# cache outlives it — re-run `vault-homelab-env --refresh` to mint a fresh
# token mid-window.

# --- Config ---
__vault_homelab_approle_env="$HOME/.config/ansible/vault-approle.env"
__vault_homelab_default_addr='https://vault.niflheim.xiiisins.com'
# Vault address for ANSIBLE lookups specifically. The Vault listener is now
# TLS-only (listener-TLS flip — no plaintext path survives), so the old
# plaintext-vault-ui-LB workaround for the macOS forked-worker crash is gone.
# NOTE: that crash was NOT the vault lookup / VAULT_ADDR — it was the NetBox
# dynamic inventory doing HTTP in the main process (macOS CoreFoundation is not
# fork-safe). The MacBook runs these roles fine now via the warm-inventory cache
# (refresh-netbox-inventory / __homelab_ensure_netbox_inventory above). This var
# stays the FQDN (public LE cert at Traefik → no internal-CA trust needed). See
# CLAUDE.md "Frigg / control-node watchtower" + docs/procedures/vault-tls-migration.md.
__vault_homelab_ansible_addr='https://vault.niflheim.xiiisins.com'

__vault_homelab_iac_path='secret/ansible/frigg/iac-env'
__vault_homelab_kubeconfig_path='secret/ansible/frigg/kubeconfig'
__vault_homelab_ansible_vault_pw_path='secret/ansible/frigg/ansible-vault-password'

# iac-env field → exported env var. Mirror of control_node_iac_env_fields
# (Frigg). proxmox_api_token → TF_VAR_proxmox_api_token.
__vault_homelab_iac_map=(
    "aws_access_key_id|AWS_ACCESS_KEY_ID"
    "aws_secret_access_key|AWS_SECRET_ACCESS_KEY"
    "aws_default_region|AWS_DEFAULT_REGION"
    "netbox_api_token|NETBOX_API_TOKEN"
    "netbox_server_url|NETBOX_SERVER_URL"
    "authentik_token|AUTHENTIK_TOKEN"
    "authentik_url|AUTHENTIK_URL"
    "adguard_host|ADGUARD_HOST"
    "adguard_scheme|ADGUARD_SCHEME"
    "adguard_username|ADGUARD_USERNAME"
    "adguard_password|ADGUARD_PASSWORD"
    "cloudflare_api_token|CLOUDFLARE_API_TOKEN"
    "proxmox_api_token|TF_VAR_proxmox_api_token"
    "semaphore_api_token|SEMAPHOREUI_API_TOKEN"
    "semaphore_api_base_url|SEMAPHOREUI_API_BASE_URL"
)

# Files written from Vault + the static SSH key path. Canonical, shared with
# homelab-env so kubectl + ansible behave identically whichever loader ran.
__vault_homelab_kubeconfig_out="$HOME/.kube/niflheim-asgard.yaml"
__vault_homelab_ansible_vault_pw_out="$HOME/.vault-pass"
__vault_homelab_ssh_key="$HOME/.ssh/ansible_niflheim"

# Separate 3h cache (NOT the 1P homelab-env cache). Away-from-home subshells
# source it directly: . ~/.cache/homelab/vault-env.sh
__vault_homelab_cache_path_fish="$__homelab_cache_dir/vault-env.fish"
__vault_homelab_cache_path_sh="$__homelab_cache_dir/vault-env.sh"
__vault_homelab_cache_ttl_seconds=10800

# --- Helpers (private) ---

__vault_homelab_read_approle() {
    # $1 = key name in the secret-zero file. '=' inside the value is preserved.
    [ -r "$__vault_homelab_approle_env" ] || return 1
    local line
    line=$(grep -E "^$1=" "$__vault_homelab_approle_env" | head -n1) || return 1
    [ -n "$line" ] || return 1
    printf '%s' "${line#*=}"
}

__vault_homelab_cache_age_seconds() {
    [ -f "$__vault_homelab_cache_path_sh" ] || return 1
    # See __homelab_cache_age_seconds above for why `date -r` and not `stat -f`.
    local mtime now
    mtime=$(date -r "$__vault_homelab_cache_path_sh" +%s) || return 1
    now=$(date +%s)
    printf '%s\n' $((now - mtime))
}

__vault_homelab_cache_is_fresh() {
    local age
    age=$(__vault_homelab_cache_age_seconds) || return 1
    [ "$age" -lt "$__vault_homelab_cache_ttl_seconds" ]
}

# Persist the Vault-sourced env to BOTH vault-env.{fish,sh} atomically.
__vault_homelab_cache_write() {
    local entry env_var ts header_fish header_sh tmp_fish tmp_sh value
    local -a vars=("VAULT_ADDR")

    mkdir -p "$__homelab_cache_dir"
    chmod 700 "$__homelab_cache_dir"

    for entry in "${__vault_homelab_iac_map[@]}"; do
        vars+=("${entry#*|}")
    done
    vars+=("KUBECONFIG" "ANSIBLE_VAULT_PASSWORD_FILE" "ANSIBLE_PRIVATE_KEY_FILE")
    vars+=("ANSIBLE_HASHI_VAULT_ADDR" "ANSIBLE_HASHI_VAULT_AUTH_METHOD" \
           "ANSIBLE_HASHI_VAULT_ROLE_ID" "ANSIBLE_HASHI_VAULT_SECRET_ID")
    [ -n "${VAULT_TOKEN:-}" ] && vars+=("VAULT_TOKEN")
    # ANSIBLE_INVENTORY: warm NetBox cache path, set on macOS only (see
    # __homelab_ensure_netbox_inventory). Cached so a bare `source vault-env.sh`
    # gets it; the conditional keeps it out of Linux (Frigg) caches.
    [ -n "${ANSIBLE_INVENTORY:-}" ] && vars+=("ANSIBLE_INVENTORY")

    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    header_fish="# vault-homelab env cache (fish) — written $ts, TTL ${__vault_homelab_cache_ttl_seconds}s
# Vault-backed (no 1P). Sourced when fresh. Do not edit; run: vault-homelab-env --refresh"
    header_sh="# vault-homelab env cache (sh/bash/zsh) — written $ts, TTL ${__vault_homelab_cache_ttl_seconds}s
# Vault-backed (no 1P). Sourced when fresh. Do not edit; run: vault-homelab-env --refresh"

    tmp_fish=$(mktemp "$__homelab_cache_dir/vault-env.fish.XXXXXX") || return 1
    tmp_sh=$(mktemp "$__homelab_cache_dir/vault-env.sh.XXXXXX") || { rm -f "$tmp_fish"; return 1; }
    chmod 600 "$tmp_fish" "$tmp_sh"

    printf '%s\n' "$header_fish" > "$tmp_fish"
    printf '%s\n' "$header_sh" > "$tmp_sh"
    for env_var in "${vars[@]}"; do
        if value=$(printenv "$env_var"); then
            printf 'set -gx %s %s\n' "$env_var" "$(__homelab_fish_quote "$value")" >> "$tmp_fish"
            printf 'export %s=%s\n' "$env_var" "$(__homelab_posix_quote "$value")" >> "$tmp_sh"
        fi
    done

    mv "$tmp_fish" "$__vault_homelab_cache_path_fish"
    mv "$tmp_sh" "$__vault_homelab_cache_path_sh"
}

# --- Public: Vault-backed env loading ---

vault-homelab-env() {
    local refresh=0 clear=0 cache_file removed=0
    local role_id secret_id file_addr vault_token loaded=0
    local entry field env_var value jtmp ktmp ptmp age remaining_h ttl_h

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                echo "Usage:"
                echo "  vault-homelab-env            Source 3h cache if fresh, else fetch from Vault + cache."
                echo "  vault-homelab-env --refresh  Skip cache, re-fetch from Vault, rewrite cache."
                echo "  vault-homelab-env --clear    Remove the vault-env cache files."
                echo "  vault-homelab-env --help     This help."
                echo ""
                echo "1P-free twin of homelab-env: AppRole-logs-in to Vault with the"
                echo "ansible-local secret-zero in $__vault_homelab_approle_env and pulls"
                echo "every IaC cred from secret/ansible/frigg/*. Re-sync the secret-zero"
                echo "after a rotation: homelab-env (1P) then seed-vault-approle."
                echo ""
                echo "Cache:     $__vault_homelab_cache_path_sh (+ .fish)"
                echo "Subshells: . ~/.cache/homelab/vault-env.sh   (NOT env.sh)"
                echo "TTL:       $__vault_homelab_cache_ttl_seconds seconds (3h)"
                echo "Note:      cached VAULT_TOKEN is an AppRole token (~30m); --refresh re-mints."
                return 0
                ;;
            -r|--refresh) refresh=1; shift ;;
            -c|--clear)   clear=1;   shift ;;
            --)           shift; break ;;
            -*)
                echo "vault-homelab-env: unknown flag '$1'. See: vault-homelab-env --help" >&2
                return 1
                ;;
            *)
                echo "vault-homelab-env: unexpected argument '$1'. See: vault-homelab-env --help" >&2
                return 1
                ;;
        esac
    done

    if [ $clear -eq 1 ]; then
        for cache_file in "$__vault_homelab_cache_path_fish" "$__vault_homelab_cache_path_sh"; do
            if [ -f "$cache_file" ]; then
                rm "$cache_file"
                echo "Cleared $cache_file"
                removed=$((removed + 1))
            fi
        done
        [ $removed -eq 0 ] && echo "No cache to clear."
        return 0
    fi

    if [ $refresh -eq 0 ] && __vault_homelab_cache_is_fresh; then
        # shellcheck disable=SC1090
        . "$__vault_homelab_cache_path_sh"
        age=$(__vault_homelab_cache_age_seconds)
        remaining_h=$(( (__vault_homelab_cache_ttl_seconds - age) / 3600 ))
        echo "Loaded vault-homelab env from cache (refresh in ${remaining_h}h, or: vault-homelab-env --refresh)"
        __homelab_ensure_netbox_inventory
        return 0
    fi

    # === Fresh fetch from Vault ===
    if ! command -v vault >/dev/null 2>&1; then
        echo "vault-homelab-env: vault CLI not found on PATH" >&2
        return 1
    fi

    role_id=$(__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_ROLE_ID)
    secret_id=$(__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_SECRET_ID)
    file_addr=$(__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_URL)
    if [ -z "$role_id" ] || [ -z "$secret_id" ]; then
        echo "vault-homelab-env: cannot read AppRole creds from $__vault_homelab_approle_env" >&2
        echo "  Seed it first (with 1P available): homelab-env && seed-vault-approle" >&2
        return 1
    fi

    if [ -n "$file_addr" ]; then
        export VAULT_ADDR="$file_addr"
    else
        export VAULT_ADDR="$__vault_homelab_default_addr"
    fi

    # AppRole login → token. -field=token emits only the raw token.
    vault_token=$(vault write -field=token auth/approle/login \
        role_id="$role_id" secret_id="$secret_id" 2>/dev/null)
    if [ -z "$vault_token" ]; then
        echo "vault-homelab-env: Vault AppRole login failed against $VAULT_ADDR" >&2
        echo "  SecretID may be stale/revoked, or Vault unreachable (tailnet up?)." >&2
        echo "  Re-seed: homelab-env && seed-vault-approle" >&2
        return 1
    fi
    export VAULT_TOKEN="$vault_token"

    # ---- IaC env bundle: one fetch to a 0600 temp file, jq each field out ----
    jtmp=$(mktemp)
    if ! vault kv get -format=json "$__vault_homelab_iac_path" > "$jtmp" 2>/dev/null || [ ! -s "$jtmp" ]; then
        rm -f "$jtmp"
        echo "vault-homelab-env: could not read $__vault_homelab_iac_path from Vault" >&2
        return 1
    fi
    for entry in "${__vault_homelab_iac_map[@]}"; do
        field=${entry%%|*}
        env_var=${entry#*|}
        value=$(jq -r --arg f "$field" '.data.data[$f] // empty' "$jtmp")
        if [ -n "$value" ]; then
            export "$env_var=$value"
            loaded=$((loaded + 1))
        else
            echo "vault-homelab-env: warning — field '$field' missing from iac-env" >&2
        fi
    done
    rm -f "$jtmp"

    # ---- kubeconfig (write straight to a temp file — preserves newlines) ----
    ktmp=$(mktemp)
    if vault kv get -field=config "$__vault_homelab_kubeconfig_path" > "$ktmp" 2>/dev/null && [ -s "$ktmp" ]; then
        chmod 600 "$ktmp"
        mkdir -p "$(dirname "$__vault_homelab_kubeconfig_out")"
        mv "$ktmp" "$__vault_homelab_kubeconfig_out"
        export KUBECONFIG="$__vault_homelab_kubeconfig_out"
    else
        rm -f "$ktmp"
        echo "vault-homelab-env: warning — kubeconfig not found at $__vault_homelab_kubeconfig_path" >&2
    fi

    # ---- ansible-vault password ----
    ptmp=$(mktemp)
    if vault kv get -field=value "$__vault_homelab_ansible_vault_pw_path" > "$ptmp" 2>/dev/null && [ -s "$ptmp" ]; then
        chmod 600 "$ptmp"
        mv "$ptmp" "$__vault_homelab_ansible_vault_pw_out"
        export ANSIBLE_VAULT_PASSWORD_FILE="$__vault_homelab_ansible_vault_pw_out"
    else
        rm -f "$ptmp"
        echo "vault-homelab-env: warning — ansible-vault password not found at $__vault_homelab_ansible_vault_pw_path" >&2
    fi

    # ---- static + ansible community.hashi_vault approle vars ----
    export ANSIBLE_PRIVATE_KEY_FILE="$__vault_homelab_ssh_key"
    # HTTP LB, not $VAULT_ADDR (the FQDN) — see __vault_homelab_ansible_addr.
    export ANSIBLE_HASHI_VAULT_ADDR="$__vault_homelab_ansible_addr"
    export ANSIBLE_HASHI_VAULT_AUTH_METHOD="approle"
    export ANSIBLE_HASHI_VAULT_ROLE_ID="$role_id"
    export ANSIBLE_HASHI_VAULT_SECRET_ID="$secret_id"

    # Ensure NetBox inventory cache + ANSIBLE_INVENTORY BEFORE cache_write, so
    # the export lands in vault-env.{sh,fish} for a bare `source`.
    __homelab_ensure_netbox_inventory refresh

    __vault_homelab_cache_write
    ttl_h=$(( __vault_homelab_cache_ttl_seconds / 3600 ))
    echo "Loaded vault-homelab env from Vault ($loaded iac-env var(s) + kubeconfig + ansible-vault + approle)."
    echo "Cached for ${ttl_h}h ($__vault_homelab_cache_path_sh + .fish)"
}

# --- Public: re-seed the local AppRole secret-zero from the loaded env ---
#
# rotate-approle is 1P-canonical: it updates the 1P item, NOT the local
# vault-approle.env file. After a rotation, run `homelab-env` (pulls the new
# SecretID from 1P into env), then `seed-vault-approle` to push it into the
# local file so vault-homelab-env keeps working away-from-home.
seed-vault-approle() {
    local addr method tmp
    if [ -z "${ANSIBLE_HASHI_VAULT_ROLE_ID:-}" ] || [ -z "${ANSIBLE_HASHI_VAULT_SECRET_ID:-}" ]; then
        echo "seed-vault-approle: AppRole creds not in env." >&2
        echo "  Run homelab-env (1P) first so ANSIBLE_HASHI_VAULT_ROLE_ID/SECRET_ID are set." >&2
        return 1
    fi

    addr="$__vault_homelab_default_addr"
    if [ -n "${VAULT_ADDR:-}" ]; then
        addr="$VAULT_ADDR"
    elif [ -n "${ANSIBLE_HASHI_VAULT_ADDR:-}" ]; then
        addr="$ANSIBLE_HASHI_VAULT_ADDR"
    fi
    method="${ANSIBLE_HASHI_VAULT_AUTH_METHOD:-approle}"

    mkdir -p "$(dirname "$__vault_homelab_approle_env")"
    tmp=$(mktemp)
    chmod 600 "$tmp"
    # All four lines go to the file (mode 0600) — the SecretID never hits stdout.
    {
        echo "# ansible-local AppRole secret-zero — managed by seed-vault-approle."
        echo "# Read by vault-homelab-env (+ ansible) to log in to Vault without 1Password."
        echo "ANSIBLE_HASHI_VAULT_AUTH_METHOD=$method"
        echo "ANSIBLE_HASHI_VAULT_URL=$addr"
        echo "ANSIBLE_HASHI_VAULT_ROLE_ID=$ANSIBLE_HASHI_VAULT_ROLE_ID"
        echo "ANSIBLE_HASHI_VAULT_SECRET_ID=$ANSIBLE_HASHI_VAULT_SECRET_ID"
    } > "$tmp"
    mv "$tmp" "$__vault_homelab_approle_env"
    chmod 600 "$__vault_homelab_approle_env"

    echo "Seeded $__vault_homelab_approle_env (ansible-local secret-zero, URL $addr)."
    echo "vault-homelab-env can now run without 1Password (RoleID $ANSIBLE_HASHI_VAULT_ROLE_ID)."
}
