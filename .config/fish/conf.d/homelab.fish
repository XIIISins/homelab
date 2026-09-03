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
#                           1P-canonical (used for ansible-local on MacBook)
#   rotate-semaphore-approle — rotate Semaphore's ansible-awx SecretID
#                              Vault-KV-canonical (Vault KV → TF apply → Semaphore)
#   rotate-vault-root-token — rotate the Vault root token + update 1P (needs
#                           an interactive terminal — confirms before revoke)
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
# set-vault-token + set-aws-creds + set-proxmox-password also rewrite the
# cache on success, so subshells sourcing the cache inherit the latest
# tokens without having to re-run homelab-env.
#
# Cache is considered fresh while file mtime is within
# $__homelab_cache_ttl_seconds; on hit, homelab-env sources the file and
# skips 1P entirely.
#
# All credentials come from the 1Password "Homelab 2.0" vault.

# === Config ===

set -g __homelab_op_vault 'Homelab 2.0'

# 1P item UUIDs for machine-accessed credentials. Referenced by UUID (not
# title) so the 1P items can be renamed/reorganised freely without breaking
# homelab-env or Terraform. The comment on each line is the *current* 1P
# title — informational only; the UUID is the stable key. To find a UUID:
#   op item list --vault 'Homelab 2.0' --format=json | jq -r '.[]|"\(.id)\t\(.title)"'
set -g __op_ansible_vault_k3s '4srpqv2mt2vditxo7g5rqjquti' # [Asgard] - Ansible - Vault - AppRole (ansible-local)
set -g __op_ansible_vault_frigg 'kcmjziie2a75zk4qytdixrprpe' # [Asgard] - Ansible - Vault - AppRole (ansible-frigg)
set -g __op_cloudflare_tf 'ps4mc2hv7a777tzsef755te64m' # [Asgard] - Terraform - Cloudflare - API token
set -g __op_authentik_admin '4pxuhyvygrqqeo3vro24bjrhwa' # [Asgard] - Terraform - Authentik - Admin API token
set -g __op_adguard_admin 'hvh3d7hlivcsbjqqye34f3d7a4' # [Asgard] - Terraform - AdGuard - Admin login
set -g __op_netbox_admin 'lsqb4z5mbeijeqbxx43y5pkl5q' # [Asgard] - Terraform - NetBox - Admin API token
set -g __op_semaphore_admin '24fmbstdhqzwk6eeru4vvaixsm' # [Asgard] - Terraform - Semaphore - Admin API token
set -g __op_aws_tf_bootstrap 'lhf4xzp3uqehkkease5gidthci' # [Asgard] - Terraform - AWS - Bootstrap access key
set -g __op_aws_tf_state 'jnvf6aokgml7vkjj4ho2xlcvua' # [Asgard] - Terraform - AWS - State access key
set -g __op_proxmox_root '6vv32uzlahikgmkvkiqfnkgshy' # [Infra] - Terraform - Proxmox - Root password
set -g __op_vault_root '7g4grolyien2yqkm7me2jficmy' # [Bootstrap] - Manual - Vault - Root token

# Each entry: "ENV_VAR|1P item UUID|field"
# Fetched from 1Password by homelab-env, cached to disk.
set -g __homelab_env_map \
    "VAULT_ADDR|$__op_ansible_vault_k3s|url" \
    "ANSIBLE_HASHI_VAULT_AUTH_METHOD|$__op_ansible_vault_k3s|method" \
    "ANSIBLE_HASHI_VAULT_ROLE_ID|$__op_ansible_vault_k3s|username" \
    "ANSIBLE_HASHI_VAULT_SECRET_ID|$__op_ansible_vault_k3s|password" \
    "CLOUDFLARE_API_TOKEN|$__op_cloudflare_tf|credential" \
    "AUTHENTIK_TOKEN|$__op_authentik_admin|credential" \
    "AUTHENTIK_URL|$__op_authentik_admin|url" \
    "ADGUARD_USERNAME|$__op_adguard_admin|username" \
    "ADGUARD_PASSWORD|$__op_adguard_admin|password" \
    "NETBOX_API_TOKEN|$__op_netbox_admin|credential" \
    "SEMAPHOREUI_API_TOKEN|$__op_semaphore_admin|credential"

# Each entry: "ENV_VAR|literal value"
# Static (non-1P) env vars — written into the cache alongside 1P vars on
# every refresh. Edit + run: homelab-env --refresh
# (Cache hit sources the cached values; changes here require --refresh.)
set -g __homelab_static_env_map \
    "KUBECONFIG|$HOME/.kube/niflheim-asgard.yaml" \
    "ADGUARD_HOST|10.0.11.201" \
    "ADGUARD_SCHEME|http" \
    "AWS_DEFAULT_REGION|eu-west-1" \
    "NETBOX_SERVER_URL|https://netbox.niflheim.xiiisins.com" \
    "SEMAPHOREUI_API_BASE_URL|https://semaphore.niflheim.xiiisins.com/api" \
    "ANSIBLE_VAULT_PASSWORD_FILE|$HOME/.vault-pass" \
    "ANSIBLE_PRIVATE_KEY_FILE|$HOME/.ssh/ansible_niflheim"

# Dual-format cache (see header). Both files have the same TTL — freshness
# is checked against the fish file's mtime (both are written together).
set -g __homelab_cache_dir "$HOME/.cache/homelab"
set -g __homelab_cache_path_fish "$__homelab_cache_dir/env.fish"
set -g __homelab_cache_path_sh "$__homelab_cache_dir/env.sh"
set -g __homelab_cache_ttl_seconds 86400

# Each entry: "approle role name|1P item UUID"
# 1P item must have fields: username (RoleID), password (SecretID),
# secret_id_accessor, expires_at.
set -g __homelab_approle_items \
    "ansible-local|$__op_ansible_vault_k3s" \
    "ansible-frigg|$__op_ansible_vault_frigg"
# "ansible-awx|<uuid>"   # add when AWX is deployed

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
    # PROXMOX_VE_PASSWORD comes from set-proxmox-password (manually called —
    # only needed for terraform/proxmox/asgard-lxcs-root/ applies). Cache it
    # if set so subshells inherit; absent otherwise.
    if set -q PROXMOX_VE_PASSWORD
        set -a vars PROXMOX_VE_PASSWORD
    end
    # ANSIBLE_INVENTORY: warm NetBox cache path, set on macOS only (see
    # __homelab_ensure_netbox_inventory). Cache it for a bare `source env.fish`;
    # the guard keeps it out of Linux (Frigg) caches.
    if set -q ANSIBLE_INVENTORY
        set -a vars ANSIBLE_INVENTORY
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
        __homelab_ensure_netbox_inventory
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
    # Ensure NetBox inventory cache + ANSIBLE_INVENTORY BEFORE cache_write so
    # the export lands in env.{sh,fish} for a bare `source`.
    __homelab_ensure_netbox_inventory refresh

    __homelab_cache_write
    set -l ttl_h (math --scale=1 "$__homelab_cache_ttl_seconds / 3600")
    echo "Cached for "$ttl_h"h ($__homelab_cache_dir/env.{fish,sh})"

    return $choice_status
end

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
# short-lived separate process, then point ANSIBLE_INVENTORY at it so playbook
# runs do zero HTTP in the forking process. Refreshed on every `homelab-env`
# (re)fetch; run manually with `refresh-netbox-inventory`.
function refresh-netbox-inventory --description "Materialize NetBox inventory to a static cache (macOS fork-crash fix)"
    if test (uname -s) != Darwin
        echo "refresh-netbox-inventory: macOS-only (Linux controllers use the live NetBox inventory)."
        return 0
    end
    set -l repo (git rev-parse --show-toplevel 2>/dev/null)
    if test -z "$repo"
        echo "refresh-netbox-inventory: run from within the homelab repo." >&2
        return 1
    end
    set -l script "$repo/.config/scripts/refresh-netbox-inventory-cache.sh"
    set -l cache "$repo/ansible/inventory/.netbox-cache.yml"
    if not test -x "$script"
        echo "refresh-netbox-inventory: missing/!x $script" >&2
        return 1
    end
    "$script"; or return 1
    set -gx ANSIBLE_INVENTORY "$cache"
    echo "refresh-netbox-inventory: ANSIBLE_INVENTORY -> $cache"
end

# Internal: called by homelab-env / vault-homelab-env. Best-effort, non-fatal,
# silent no-op off macOS or outside the repo. $argv[1]=refresh forces a
# re-materialize (fresh NetBox pull); otherwise it only (re)exports
# ANSIBLE_INVENTORY when the cache exists, and materializes only if missing.
function __homelab_ensure_netbox_inventory
    test (uname -s) = Darwin; or return 0
    set -l repo (git rev-parse --show-toplevel 2>/dev/null)
    test -n "$repo"; or return 0
    set -l cache "$repo/ansible/inventory/.netbox-cache.yml"
    if test "$argv[1]" = refresh; or not test -f "$cache"
        if not "$repo/.config/scripts/refresh-netbox-inventory-cache.sh"
            echo "  ↳ NetBox inventory cache refresh failed; ansible will fall back to the live" >&2
            echo "    inventory (may fork-crash on macOS). Fix + rerun: refresh-netbox-inventory" >&2
        end
    end
    test -f "$cache"; and set -gx ANSIBLE_INVENTORY "$cache"
end

# === Public: vault tokens ===

function vault-root-token --description "Echo the Vault root token from 1Password"
    __homelab_op_field $__op_vault_root password
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
# Two AWS identities live in 1P (referenced by UUID via $__op_aws_tf_*):
#   - bootstrap — admin, for re-applying terraform/aws/
#   - state     — narrow S3-only, for downstream terraform
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
            set item $__op_aws_tf_bootstrap
        case state s
            set item $__op_aws_tf_state
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

function set-proxmox-password --description "Set PROXMOX_VE_PASSWORD from 1P (Proxmox root)"
    set -l value (__homelab_op_field $__op_proxmox_root password)
    if test $status -ne 0
        echo "set-proxmox-password: failed to read password from 1P (Proxmox root, UUID $__op_proxmox_root)" >&2
        return 1
    end
    set -gx PROXMOX_VE_PASSWORD $value
    echo "PROXMOX_VE_PASSWORD set (from 1P)"

    # Persist to the shared cache so subshells sourcing
    # ~/.cache/homelab/env.{sh,fish} inherit the password. Without this,
    # set-proxmox-password would only affect the current shell.
    __homelab_cache_write
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
    # Read the SecretID's REAL expiration from Vault — authoritative, reflects
    # the mount's max_lease_ttl. Don't assume the role's secret_id_ttl: a +90d
    # guess stamped 1P 58d past the true date back when the mount clamped to 32d.
    # (Surfaced 2026-05-31 Wave S7; mount ceiling since raised to 90d.)
    set -l expires_at (vault write -format=json \
        auth/approle/role/$role/secret-id-accessor/lookup \
        secret_id_accessor=$new_accessor 2>/dev/null \
        | jq -r '.data.expiration_time[0:10] // empty' 2>/dev/null)
    test -n "$expires_at"; or set expires_at \
        "(lookup failed — run: vault write auth/approle/role/$role/secret-id-accessor/lookup secret_id_accessor=$new_accessor)"

    echo ""
    echo "Update 1P item \"$item\" with these values:"
    echo ""
    echo "  password           = $new_secret_id"
    echo "  secret_id_accessor = $new_accessor"
    echo "  expires_at         = $expires_at"
    echo ""
    echo "Both old and new SecretIDs are currently valid."
    # `_` is a read-only var in fish (last command name). Use a real name
    # for the throwaway target. Surfaced 2026-05-27 mid-rotation: helper
    # bombed AFTER 1P paste + BEFORE revoke, leaving the leaked SecretID
    # active. Manual revoke required to finish the job.
    read -P "Press enter once 1P is updated (Ctrl+C aborts; run --fix to recover orphans): " _ack

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
    if test "$role" = ansible-frigg
        echo "Next: re-seed Frigg's secret-zero file (it doesn't read 1P):"
        echo "  ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/asgard-control.yml \\"
        echo "    --tags control-node:vault-env -e control_node_frigg_secret_id=<new-secret-id> -l frigg"
        echo "Then confirm on Frigg: . /usr/local/bin/homelab-env && homelab-env"
    else
        echo "Next: homelab-env  (re-pull new SecretID into env)"
        echo "Then: set -e VAULT_TOKEN; homelab-env --refresh  (clears the cached copy too)"
    end
end

# rotate-vault-root-token — mint a fresh Vault root token, have the human
# paste it into 1P (print-and-paste, NOT `op item edit` — a live run proved
# that reports success while silently not writing; see the step-3 comment
# below), hash-verify the paste, THEN revoke the old token. Same
# mint-before-revoke safety shape as rotate-approle, but for the one
# credential that unlocks everything else — so it adds two checks
# rotate-approle doesn't need: the new token must -orphan (else revoking the
# old PARENT token cascades and kills the brand-new child too — the single
# most important correctness detail here) and must pass a LIVE lookup
# against Vault before the old one is touched. Requires an interactive
# terminal (the confirm-before-revoke prompt reads stdin) — won't complete
# end-to-end from a non-interactive caller, by design. Bash/zsh twin:
# .config/scripts/homelab.sh — keep both in sync.
function rotate-vault-root-token --description "Rotate the Vault root token + update 1P"
    if not set -q VAULT_TOKEN
        echo "rotate-vault-root-token: VAULT_TOKEN not set. Run: set-vault-token root" >&2
        return 1
    end
    if not set -q VAULT_ADDR
        echo "rotate-vault-root-token: VAULT_ADDR not set. Run: homelab-env" >&2
        return 1
    end

    echo "Rotating Vault root token (1P item: \"$__op_vault_root\")"

    # Step 1: snapshot the CURRENT token's accessor before anything else.
    # `vault token lookup` has NO `-field` flag (only `-format`) — capture
    # the full `-format=json` (which DOES include the literal token value at
    # .data.id) into a local var, pull out only the accessor via jq, then
    # `set -e` the var. Never echo/return `$old_lookup_json` itself.
    set -l old_lookup_json (vault token lookup -format=json 2>&1)
    if test $status -ne 0
        echo "rotate-vault-root-token: couldn't look up the current token — is VAULT_TOKEN actually a valid root token?" >&2
        echo "$old_lookup_json" >&2
        return 1
    end
    set -l old_accessor (echo $old_lookup_json | jq -r '.data.accessor')
    set -e old_lookup_json
    if test -z "$old_accessor"; or test "$old_accessor" = null
        echo "rotate-vault-root-token: couldn't parse the accessor out of the lookup response." >&2
        return 1
    end
    echo "Old accessor: $old_accessor"

    # Step 2: mint the replacement. -orphan is load-bearing (see header) —
    # without it the new token is a CHILD of the one about to be revoked in
    # Step 6, and would die with its parent. -ttl=0 matches a root token's
    # conventional shape (no expiration; confirmed against the live token's
    # own `creation_ttl: 0` / `expire_time: null` before writing this).
    echo "Minting new root token (orphan, no TTL)..."
    set -l new_json (vault token create -policy=root -orphan -ttl=0 -format=json 2>&1)
    if test $status -ne 0
        echo "rotate-vault-root-token: mint failed. Old token untouched." >&2
        echo "$new_json" >&2
        return 1
    end
    set -l new_token (echo $new_json | jq -r '.auth.client_token')
    set -l new_accessor (echo $new_json | jq -r '.auth.accessor')
    if test -z "$new_token"; or test "$new_token" = null
        echo "rotate-vault-root-token: couldn't parse the new token out of the mint response. Old token untouched." >&2
        return 1
    end
    echo "New accessor: $new_accessor"

    # Step 3: human pastes into 1P — NOT an automated `op item edit`.
    # rotate-approle already established why: 1Password's CLI is documented
    # as unreliable for editing certain item fields (known-issues/vault.md —
    # a Password item's field can reject or silently no-op an edit). A live
    # run of this exact function proved it: `op item edit` returned success
    # but the value never actually landed. Print-and-paste is the proven
    # working pattern here, same as rotate-approle.
    echo ""
    echo "Update 1P item \"$__op_vault_root\" with this value:"
    echo ""
    echo "  password = $new_token"
    echo ""
    echo "Both old and new tokens are currently valid."
    read -P "Press enter once 1P is updated (Ctrl+C aborts, leaving both valid): " _ack

    # Step 4: hash-verify what the human actually pasted — never diff
    # literal secret values. `op read` appends a trailing newline to
    # stdout; piping it straight into shasum hashes "token\n", not "token"
    # — capture into a variable FIRST (command substitution strips
    # trailing newlines in fish too) so both sides hash the identical
    # newline-free value.
    set -l source_hash (echo -n "$new_token" | shasum -a 256 | cut -d' ' -f1)
    set -l verify_value (op read "op://$__homelab_op_vault/$__op_vault_root/password" 2>/dev/null)
    set -l verify_hash (echo -n "$verify_value" | shasum -a 256 | cut -d' ' -f1)
    set -e verify_value
    if test "$source_hash" != "$verify_hash"
        echo "rotate-vault-root-token: 1P doesn't hold the new token (hash mismatch) — do NOT proceed. Old token is untouched and still works." >&2
        echo "Check the paste (field must be named exactly 'password') and re-run this function — it'll mint a fresh token again, which is fine, the un-pasted one here is just abandoned." >&2
        return 1
    end
    echo "✓ 1P write verified (hash match)."

    # Step 5: prove the new token actually works, live, before burning the
    # only other valid one. A parse-succeeded mint is not the same as a
    # working credential.
    env VAULT_TOKEN="$new_token" vault token lookup >/dev/null 2>&1
    if test $status -ne 0
        echo "rotate-vault-root-token: new token failed a live lookup against Vault. NOT revoking the old one. Investigate before retrying." >&2
        return 1
    end
    echo "✓ New token verified live against Vault."

    # Step 6: revoke old. By accessor, not by value — never puts the old
    # token's literal value anywhere.
    echo "Revoking old token..."
    vault token revoke -accessor "$old_accessor"
    if test $status -ne 0
        echo "rotate-vault-root-token: revoke failed. New token is live + saved in 1P; old token may still work too — revoke manually:" >&2
        echo "  vault token revoke -accessor $old_accessor" >&2
        return 1
    end
    echo "✓ Old root token revoked."
    echo ""
    echo "Next: set -e VAULT_TOKEN; set-vault-token root   (loads the new token into this shell)"
    echo "Any OTHER shell/session still holding the old VAULT_TOKEN (incl. ~/.cache/homelab/env.sh"
    echo "if this session ran set-vault-token root earlier) is now dead — re-run set-vault-token root there too."
end

# === rotate-semaphore-approle: Semaphore-specific AppRole rotation ===
#
# Semaphore consumes the `ansible-awx` AppRole (inherited from when AWX
# was the planned consumer of that role). Credentials are stored at
# Vault KV `secret/k8s/semaphore/vault-approle` (NOT 1P — different
# storage convention from rotate-approle ansible-local), and propagated
# to Semaphore env id 1 via `terraform apply` in terraform/semaphore/.
#
# This is structurally different from rotate-approle (1P-canonical), so
# it's a separate function rather than a flag on the existing one.
# Don't conflate the two — running `rotate-approle ansible-local` does
# NOT rotate Semaphore's credentials (different AppRole entirely).
#
# Surfaced 2026-05-27: a transcript leak of ansible-local's RoleID +
# SecretID triggered a `rotate-approle ansible-local` rotation, which
# was correct for MacBook BUT didn't touch Semaphore (which uses
# ansible-awx and was never compromised by that leak). When the
# operator manually wrote ansible-local's new SecretID into Semaphore's
# Vault KV (assuming Semaphore used ansible-local), Semaphore auth
# broke — role_id stored at the Vault KV path was ansible-awx's
# (c43a483d-), and the freshly-minted SecretID was ansible-local's
# (mismatched pair). This function exists to make the correct flow
# explicit + scripted.
function rotate-semaphore-approle --description "Rotate Semaphore's AppRole SecretID (ansible-awx via Vault KV + TF apply)"
    set -l role ansible-awx
    set -l vault_kv_path k8s/semaphore/vault-approle
    set -l repo_root (git rev-parse --show-toplevel 2>/dev/null)

    if test -z "$repo_root"
        echo "rotate-semaphore-approle: must be run from within the homelab repo" >&2
        return 1
    end
    set -l tf_module $repo_root/terraform/semaphore
    if not test -d $tf_module
        echo "rotate-semaphore-approle: $tf_module not a directory" >&2
        return 1
    end

    if not set -q VAULT_TOKEN
        echo "rotate-semaphore-approle: VAULT_TOKEN not set. Run: set-vault-token root" >&2
        return 1
    end
    if not set -q VAULT_ADDR
        echo "rotate-semaphore-approle: VAULT_ADDR not set. Run: homelab-env" >&2
        return 1
    end

    # Snapshot existing accessors BEFORE mint so we can revoke after.
    set -l old_accessors (vault list -format=json auth/approle/role/$role/secret-id 2>/dev/null | jq -r '.[]?')
    if test (count $old_accessors) -eq 0
        echo "rotate-semaphore-approle: no existing SecretIDs found for $role." >&2
        echo "(Bootstrap case? Run vault write -f auth/approle/role/$role/secret-id manually.)" >&2
        return 1
    end
    echo "Rotating $role (Vault KV: secret/$vault_kv_path, TF: $tf_module)"
    echo "Old accessor(s) to revoke after success:"
    printf '  %s\n' $old_accessors
    echo ""

    # Mint new SecretID.
    echo "Minting new SecretID..."
    set -l json (vault write -format=json -f auth/approle/role/$role/secret-id)
    if test $status -ne 0
        echo "rotate-semaphore-approle: mint failed. Old SecretIDs untouched." >&2
        return 1
    end
    set -l new_sid (echo $json | jq -r .data.secret_id)
    set -l new_accessor (echo $json | jq -r .data.secret_id_accessor)
    echo "  new accessor: $new_accessor"

    # Write new SecretID to Vault KV. Argv exposure is <100ms; acceptable
    # on a single-user MacBook.
    echo "Writing to Vault KV (secret/$vault_kv_path)..."
    vault kv patch -mount=secret $vault_kv_path secret_id="$new_sid" > /dev/null
    if test $status -ne 0
        echo "rotate-semaphore-approle: Vault KV write failed." >&2
        echo "  New SecretID minted but not propagated." >&2
        echo "  Manual revoke: vault write auth/approle/role/$role/secret-id-accessor/destroy secret_id_accessor=$new_accessor" >&2
        set -e new_sid json new_accessor
        return 1
    end

    # Verify the KV write actually persisted the right value. The
    # `secret_id=-` stdin pattern silently produced length-correct but
    # content-wrong values during the 2026-05-27 rotation — hash-compare
    # is the defense.
    set -l kv_hash (vault kv get -mount=secret -field=secret_id $vault_kv_path 2>/dev/null | shasum -a 256 | string sub -l 16)
    set -l mint_hash (printf '%s' $new_sid | shasum -a 256 | string sub -l 16)
    if test "$kv_hash" != "$mint_hash"
        echo "rotate-semaphore-approle: HASH MISMATCH after Vault KV write." >&2
        echo "  Vault KV did not store the value we sent. Aborting." >&2
        echo "  Manual revoke: vault write auth/approle/role/$role/secret-id-accessor/destroy secret_id_accessor=$new_accessor" >&2
        set -e new_sid json new_accessor
        return 1
    end
    set -e new_sid json
    echo "✓ Vault KV updated (hash verified)"
    echo ""

    # Terraform apply to propagate Vault KV → Semaphore env id 1.
    echo "Running terraform apply in $tf_module..."
    pushd $tf_module > /dev/null
    terraform apply
    set -l tf_status $status
    popd > /dev/null
    if test $tf_status -ne 0
        echo "rotate-semaphore-approle: terraform apply failed/declined." >&2
        echo "  Old SecretIDs still valid; new SecretID present in Vault KV but" >&2
        echo "  not yet propagated to Semaphore. Re-run terraform apply manually" >&2
        echo "  or revoke the new SecretID:" >&2
        echo "    vault write auth/approle/role/$role/secret-id-accessor/destroy secret_id_accessor=$new_accessor" >&2
        return 1
    end
    echo ""

    # Prompt for validation BEFORE revoking — old SecretIDs are still
    # valid, so if Semaphore is somehow broken by the new credential
    # operator can rollback by re-pointing Vault KV at the old
    # SecretID's accessor (well, can't actually rollback the SecretID
    # value itself, but at least don't compound by revoking old).
    echo "Trigger a Semaphore drift-check now to validate the new SecretID works."
    echo "Watch the output for 'Inventory has N hosts — proceeding' (success)"
    echo "vs 'permission denied' (failure)."
    read -P "Press enter once validated (Ctrl+C aborts; old SecretIDs still valid): " _ack

    # Revoke old SecretIDs.
    echo ""
    echo "Revoking "(count $old_accessors)" old SecretID(s)..."
    for acc in $old_accessors
        echo "  destroying $acc"
        vault write auth/approle/role/$role/secret-id-accessor/destroy secret_id_accessor=$acc > /dev/null
        if test $status -ne 0
            echo "  ⚠ failed to revoke $acc — destroy manually" >&2
        end
    end
    echo "✓ Done. Active accessor: $new_accessor"
end

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
# rotate-approle updates 1Password (rotate-approle is 1P-canonical). After a
# rotation, re-sync it once (with 1P available): `homelab-env; seed-vault-approle`.
#
# Caveat: the cached VAULT_TOKEN is an AppRole token (~30min ttl) and the 3h
# cache outlives it — re-run `vault-homelab-env --refresh` to mint a fresh
# token mid-window. (Same short-lived-token note as homelab-env's cache.)

# --- Config ---
# Secret-zero file: ANSIBLE_HASHI_VAULT_{AUTH_METHOD,URL,ROLE_ID,SECRET_ID}.
set -g __vault_homelab_approle_env "$HOME/.config/ansible/vault-approle.env"
# Fallback Vault address when the secret-zero file carries no URL.
set -g __vault_homelab_default_addr 'https://vault.niflheim.xiiisins.com'
# Vault address for ANSIBLE lookups specifically. The Vault listener is now
# TLS-only (the listener-TLS flip — no plaintext path survives), so the old
# plaintext-vault-ui-LB workaround for the macOS forked-worker crash is gone.
# On macOS, ansible's community.hashi_vault runs in a forked worker that hits
# a fork-unsafe framework on TLS/DNS ("A worker was found in a dead state");
# OBJC_DISABLE_INITIALIZE_FORK_SAFETY + no_proxy can't prevent it, and there
# is no longer a plaintext path. NOTE: that crash was NOT the vault lookup /
# VAULT_ADDR — it was the NetBox dynamic inventory doing HTTP in the main
# process (macOS CoreFoundation is not fork-safe). The MacBook runs these roles
# fine now via the warm-inventory cache (refresh-netbox-inventory /
# __homelab_ensure_netbox_inventory above). This var stays the FQDN (public LE
# cert at Traefik → no internal-CA trust needed, parity with VAULT_ADDR). See
# CLAUDE.md "Frigg / control-node watchtower" + docs/procedures/vault-tls-migration.md.
set -g __vault_homelab_ansible_addr 'https://vault.niflheim.xiiisins.com'

# Consolidated IaC bundle in Vault (KV v2, mount `secret`). Same paths Frigg
# reads; kept in lockstep with ansible/roles/control-node/defaults/main.yml.
set -g __vault_homelab_iac_path 'secret/ansible/frigg/iac-env'
set -g __vault_homelab_kubeconfig_path 'secret/ansible/frigg/kubeconfig'
set -g __vault_homelab_ansible_vault_pw_path 'secret/ansible/frigg/ansible-vault-password'

# iac-env field → exported env var. Mirror of control_node_iac_env_fields
# (Frigg). proxmox_api_token → TF_VAR_proxmox_api_token; the rest map
# field → UPPERCASE(field) with two SEMAPHOREUI_ renames.
set -g __vault_homelab_iac_map \
    "aws_access_key_id|AWS_ACCESS_KEY_ID" \
    "aws_secret_access_key|AWS_SECRET_ACCESS_KEY" \
    "aws_default_region|AWS_DEFAULT_REGION" \
    "netbox_api_token|NETBOX_API_TOKEN" \
    "netbox_server_url|NETBOX_SERVER_URL" \
    "authentik_token|AUTHENTIK_TOKEN" \
    "authentik_url|AUTHENTIK_URL" \
    "adguard_host|ADGUARD_HOST" \
    "adguard_scheme|ADGUARD_SCHEME" \
    "adguard_username|ADGUARD_USERNAME" \
    "adguard_password|ADGUARD_PASSWORD" \
    "cloudflare_api_token|CLOUDFLARE_API_TOKEN" \
    "proxmox_api_token|TF_VAR_proxmox_api_token" \
    "semaphore_api_token|SEMAPHOREUI_API_TOKEN" \
    "semaphore_api_base_url|SEMAPHOREUI_API_BASE_URL"

# Files written from Vault + the static SSH key path. Canonical, shared with
# homelab-env (single cluster / single secret) so kubectl + ansible behave
# identically whichever loader populated the env.
set -g __vault_homelab_kubeconfig_out "$HOME/.kube/niflheim-asgard.yaml"
set -g __vault_homelab_ansible_vault_pw_out "$HOME/.vault-pass"
set -g __vault_homelab_ssh_key "$HOME/.ssh/ansible_niflheim"

# Separate 3h cache (NOT the 1P homelab-env cache). Away-from-home subshells
# source it directly: . ~/.cache/homelab/vault-env.sh
set -g __vault_homelab_cache_path_fish "$__homelab_cache_dir/vault-env.fish"
set -g __vault_homelab_cache_path_sh "$__homelab_cache_dir/vault-env.sh"
set -g __vault_homelab_cache_ttl_seconds 10800

# --- Helpers (private) ---

function __vault_homelab_read_approle --argument-names key \
    --description "Read KEY= from the local AppRole secret-zero file"
    test -r $__vault_homelab_approle_env; or return 1
    set -l line (grep -E "^$key=" $__vault_homelab_approle_env | head -n1)
    test -n "$line"; or return 1
    # -m1 keeps any '=' in the value intact; tail grabs the value half.
    string split -m1 '=' -- $line | tail -n1
end

function __vault_homelab_cache_age_seconds \
    --description "Echo seconds since vault-env cache mtime; return 1 if missing"
    test -f $__vault_homelab_cache_path_fish; or return 1
    set -l mtime (stat -f %m $__vault_homelab_cache_path_fish); or return 1
    math (date +%s) - $mtime
end

function __vault_homelab_cache_is_fresh \
    --description "True if vault-env cache exists and is younger than its TTL"
    set -l age (__vault_homelab_cache_age_seconds); or return 1
    test $age -lt $__vault_homelab_cache_ttl_seconds
end

function __vault_homelab_cache_write \
    --description "Persist the Vault-sourced env to BOTH vault-env.{fish,sh} atomically"
    mkdir -p $__homelab_cache_dir
    chmod 700 $__homelab_cache_dir

    # VAULT_ADDR, then the iac-env env vars, then the file/static vars, then
    # the ansible hashi_vault approle vars, then VAULT_TOKEN if set.
    set -l vars VAULT_ADDR
    for entry in $__vault_homelab_iac_map
        set -l parts (string split -m1 '|' -- $entry)
        set -a vars $parts[2]
    end
    set -a vars KUBECONFIG ANSIBLE_VAULT_PASSWORD_FILE ANSIBLE_PRIVATE_KEY_FILE
    set -a vars ANSIBLE_HASHI_VAULT_ADDR ANSIBLE_HASHI_VAULT_AUTH_METHOD \
        ANSIBLE_HASHI_VAULT_ROLE_ID ANSIBLE_HASHI_VAULT_SECRET_ID
    if set -q VAULT_TOKEN
        set -a vars VAULT_TOKEN
    end
    # ANSIBLE_INVENTORY: warm NetBox cache path, macOS-only (see
    # __homelab_ensure_netbox_inventory). Cache for a bare `source vault-env.fish`.
    if set -q ANSIBLE_INVENTORY
        set -a vars ANSIBLE_INVENTORY
    end

    set -l ts (date -u '+%Y-%m-%dT%H:%M:%SZ')
    set -l header_fish "# vault-homelab env cache (fish) — written $ts, TTL "$__vault_homelab_cache_ttl_seconds"s
# Vault-backed (no 1P). Sourced when fresh. Do not edit; run: vault-homelab-env --refresh"
    set -l header_sh "# vault-homelab env cache (sh/bash/zsh) — written $ts, TTL "$__vault_homelab_cache_ttl_seconds"s
# Vault-backed (no 1P). Sourced when fresh. Do not edit; run: vault-homelab-env --refresh"

    set -l tmp_fish (mktemp "$__homelab_cache_dir/vault-env.fish.XXXXXX"); or return 1
    set -l tmp_sh (mktemp "$__homelab_cache_dir/vault-env.sh.XXXXXX")
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

    mv $tmp_fish $__vault_homelab_cache_path_fish
    mv $tmp_sh $__vault_homelab_cache_path_sh
end

# --- Public: Vault-backed env loading ---

function vault-homelab-env --description "Load IaC env from Vault via AppRole, no 1Password (cached 3h)"
    argparse -n vault-homelab-env h/help r/refresh c/clear -- $argv
    or return

    if set -q _flag_help
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
    end

    if set -q _flag_clear
        set -l removed 0
        for path in $__vault_homelab_cache_path_fish $__vault_homelab_cache_path_sh
            if test -f $path
                rm $path
                echo "Cleared $path"
                set removed (math $removed + 1)
            end
        end
        test $removed -eq 0; and echo "No cache to clear."
        return 0
    end

    if not set -q _flag_refresh; and __vault_homelab_cache_is_fresh
        source $__vault_homelab_cache_path_fish
        set -l age (__vault_homelab_cache_age_seconds)
        set -l remaining_h (math --scale=1 "($__vault_homelab_cache_ttl_seconds - $age) / 3600")
        echo "Loaded vault-homelab env from cache (refresh in "$remaining_h"h, or: vault-homelab-env --refresh)"
        __homelab_ensure_netbox_inventory
        return 0
    end

    # === Fresh fetch from Vault ===
    if not command -v vault >/dev/null 2>&1
        echo "vault-homelab-env: vault CLI not found on PATH" >&2
        return 1
    end

    set -l role_id (__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_ROLE_ID)
    set -l secret_id (__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_SECRET_ID)
    set -l file_addr (__vault_homelab_read_approle ANSIBLE_HASHI_VAULT_URL)
    if test -z "$role_id"; or test -z "$secret_id"
        echo "vault-homelab-env: cannot read AppRole creds from $__vault_homelab_approle_env" >&2
        echo "  Seed it first (with 1P available): homelab-env; and seed-vault-approle" >&2
        return 1
    end

    if test -n "$file_addr"
        set -gx VAULT_ADDR $file_addr
    else
        set -gx VAULT_ADDR $__vault_homelab_default_addr
    end

    # AppRole login → token. -field=token emits only the raw token (never a
    # human-readable dump). RoleID/SecretID stay in shell-local vars.
    set -l vault_token (vault write -field=token auth/approle/login \
        role_id=$role_id secret_id=$secret_id 2>/dev/null)
    if test -z "$vault_token"
        echo "vault-homelab-env: Vault AppRole login failed against $VAULT_ADDR" >&2
        echo "  SecretID may be stale/revoked, or Vault unreachable (tailnet up?)." >&2
        echo "  Re-seed: homelab-env; and seed-vault-approle" >&2
        return 1
    end
    set -gx VAULT_TOKEN $vault_token

    # ---- IaC env bundle: one fetch to a 0600 temp file, jq each field out ----
    # (Fish collapses newlines in captured command output — write JSON to a
    #  file and read with jq instead of capturing into a variable.)
    set -l loaded 0
    set -l jtmp (mktemp)
    if not vault kv get -format=json $__vault_homelab_iac_path > $jtmp 2>/dev/null; or not test -s $jtmp
        rm -f $jtmp
        echo "vault-homelab-env: could not read $__vault_homelab_iac_path from Vault" >&2
        return 1
    end
    for entry in $__vault_homelab_iac_map
        set -l parts (string split -m1 '|' -- $entry)
        set -l value (jq -r --arg f $parts[1] '.data.data[$f] // empty' $jtmp)
        if test -n "$value"
            set -gx $parts[2] $value
            set loaded (math $loaded + 1)
        else
            echo "vault-homelab-env: warning — field '$parts[1]' missing from iac-env" >&2
        end
    end
    rm -f $jtmp

    # ---- kubeconfig (write straight to a temp file — preserves newlines) ----
    set -l ktmp (mktemp)
    if vault kv get -field=config $__vault_homelab_kubeconfig_path > $ktmp 2>/dev/null; and test -s $ktmp
        chmod 600 $ktmp
        mkdir -p (dirname $__vault_homelab_kubeconfig_out)
        mv $ktmp $__vault_homelab_kubeconfig_out
        set -gx KUBECONFIG $__vault_homelab_kubeconfig_out
    else
        rm -f $ktmp
        echo "vault-homelab-env: warning — kubeconfig not found at $__vault_homelab_kubeconfig_path" >&2
    end

    # ---- ansible-vault password ----
    set -l ptmp (mktemp)
    if vault kv get -field=value $__vault_homelab_ansible_vault_pw_path > $ptmp 2>/dev/null; and test -s $ptmp
        chmod 600 $ptmp
        mv $ptmp $__vault_homelab_ansible_vault_pw_out
        set -gx ANSIBLE_VAULT_PASSWORD_FILE $__vault_homelab_ansible_vault_pw_out
    else
        rm -f $ptmp
        echo "vault-homelab-env: warning — ansible-vault password not found at $__vault_homelab_ansible_vault_pw_path" >&2
    end

    # ---- static + ansible community.hashi_vault approle vars ----
    set -gx ANSIBLE_PRIVATE_KEY_FILE $__vault_homelab_ssh_key
    # HTTP LB, not $VAULT_ADDR (the FQDN) — see __vault_homelab_ansible_addr.
    set -gx ANSIBLE_HASHI_VAULT_ADDR $__vault_homelab_ansible_addr
    set -gx ANSIBLE_HASHI_VAULT_AUTH_METHOD approle
    set -gx ANSIBLE_HASHI_VAULT_ROLE_ID $role_id
    set -gx ANSIBLE_HASHI_VAULT_SECRET_ID $secret_id

    # Ensure NetBox inventory cache + ANSIBLE_INVENTORY BEFORE cache_write so
    # the export lands in vault-env.{sh,fish} for a bare `source`.
    __homelab_ensure_netbox_inventory refresh

    __vault_homelab_cache_write
    set -l ttl_h (math --scale=1 "$__vault_homelab_cache_ttl_seconds / 3600")
    echo "Loaded vault-homelab env from Vault ($loaded iac-env var(s) + kubeconfig + ansible-vault + approle)."
    echo "Cached for "$ttl_h"h ($__vault_homelab_cache_path_sh + .fish)"
end

# --- Public: re-seed the local AppRole secret-zero from the loaded env ---
#
# rotate-approle is 1P-canonical: it updates the 1P item, NOT the local
# vault-approle.env file. After a rotation, run `homelab-env` (pulls the new
# SecretID from 1P into env), then `seed-vault-approle` to push it into the
# local file so vault-homelab-env keeps working away-from-home.
function seed-vault-approle --description "(Re)write the local ansible-local secret-zero file from the loaded env"
    if not set -q ANSIBLE_HASHI_VAULT_ROLE_ID; or not set -q ANSIBLE_HASHI_VAULT_SECRET_ID
        echo "seed-vault-approle: AppRole creds not in env." >&2
        echo "  Run homelab-env (1P) first so ANSIBLE_HASHI_VAULT_ROLE_ID/SECRET_ID are set." >&2
        return 1
    end

    set -l addr $__vault_homelab_default_addr
    if set -q VAULT_ADDR
        set addr $VAULT_ADDR
    else if set -q ANSIBLE_HASHI_VAULT_ADDR
        set addr $ANSIBLE_HASHI_VAULT_ADDR
    end
    set -l method approle
    set -q ANSIBLE_HASHI_VAULT_AUTH_METHOD; and set method $ANSIBLE_HASHI_VAULT_AUTH_METHOD

    mkdir -p (dirname $__vault_homelab_approle_env)
    set -l tmp (mktemp)
    chmod 600 $tmp
    # All four lines go to the file (mode 0600) — the SecretID never hits stdout.
    begin
        echo "# ansible-local AppRole secret-zero — managed by seed-vault-approle."
        echo "# Read by vault-homelab-env (+ ansible) to log in to Vault without 1Password."
        echo "ANSIBLE_HASHI_VAULT_AUTH_METHOD=$method"
        echo "ANSIBLE_HASHI_VAULT_URL=$addr"
        echo "ANSIBLE_HASHI_VAULT_ROLE_ID=$ANSIBLE_HASHI_VAULT_ROLE_ID"
        echo "ANSIBLE_HASHI_VAULT_SECRET_ID=$ANSIBLE_HASHI_VAULT_SECRET_ID"
    end > $tmp
    mv $tmp $__vault_homelab_approle_env
    chmod 600 $__vault_homelab_approle_env

    echo "Seeded $__vault_homelab_approle_env (ansible-local secret-zero, URL $addr)."
    echo "vault-homelab-env can now run without 1Password (RoleID $ANSIBLE_HASHI_VAULT_ROLE_ID)."
end
