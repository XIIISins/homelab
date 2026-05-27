#!/usr/bin/env bash
# ansible/scripts/refresh-netbox-inventory.sh
#
# Rebuild the NetBox dynamic-inventory cache. End state on success:
# $CACHE_DIR/netbox_asgard_* is warm with a fresh API response, and
# every consumer playbook for the next CACHE_TIMEOUT window
# (netbox.yml: 24h) reads from cache without ever touching NetBox.
#
# Invoked by the Semaphore `refresh-netbox-inventory` template on a
# 4h cron + on-demand. NetBox 4.6 has a documented transient
# `OperationalError: the connection is closed` blip (CLAUDE.md
# NetBox gotchas) — retry until the query succeeds AND returns at
# least REFRESH_MIN_HOSTS, to guard against the plugin silently
# falling through to implicit-localhost on a malformed response.
#
# Design: cache_timeout (24h) > refresh cadence (4h), so consumers
# always hit the cache in steady state. The only NetBox-live path
# for consumers is pod restart (emptyDir wipe) followed by a
# consumer running before the next refresh — and site.yml's
# preflight assert catches that case loud if NetBox is also blipping.
# Task 27 (2026-05-27) was the previous incarnation: silent success
# with every play `skipping: no hosts matched`.
set -euo pipefail

CACHE_DIR="${ANSIBLE_INVENTORY_CACHE_CONNECTION:-/var/lib/semaphore/inventory-cache}"
INVENTORY="${REFRESH_INVENTORY:-ansible/inventory/netbox.yml}"
ATTEMPTS="${REFRESH_ATTEMPTS:-10}"
SLEEP="${REFRESH_SLEEP:-30}"
MIN_HOSTS="${REFRESH_MIN_HOSTS:-20}"

if [[ ! -d "$CACHE_DIR" ]]; then
    echo "ERROR: cache dir $CACHE_DIR does not exist" >&2
    exit 1
fi

# Step 1: wipe stale cache files. Glob expands to nothing safely under
# nullglob — perfect for the cold-pod case.
shopt -s nullglob
files=("$CACHE_DIR"/netbox_asgard_*)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    echo "no stale cache files to wipe (cold pod or already cleared)"
else
    echo "wiping ${#files[@]} stale file(s):"
    printf '  %s\n' "${files[@]}"
    rm -f "${files[@]}"
fi

# Step 2: retry-loop rebuilding the cache. NetBox transient 500s
# usually clear on the next attempt; bound to ATTEMPTS so a real
# NetBox outage still fails the job (and fires the alert) within
# ~5 min instead of hanging indefinitely. `ansible-inventory --list`
# triggers the plugin's NetBox fetch + writes the jsonfile cache as
# a side effect.
tmp="$(mktemp)"
err="$(mktemp)"
trap 'rm -f "$tmp" "$err"' EXIT

for i in $(seq 1 "$ATTEMPTS"); do
    echo "attempt $i/$ATTEMPTS: ansible-inventory -i $INVENTORY --list"
    if ansible-inventory -i "$INVENTORY" --list > "$tmp" 2> "$err"; then
        # _meta.hostvars is the canonical host count in the dynamic-
        # inventory JSON schema. A plugin that silently fails (e.g.
        # falls through to implicit-localhost on a malformed response)
        # yields ~0–1 hosts, far below the floor.
        count="$(python3 -c "
import json
try:
    d = json.load(open('$tmp'))
    print(len(d.get('_meta', {}).get('hostvars', {})))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"

        if [[ "$count" -ge "$MIN_HOSTS" ]]; then
            echo "ok: $count hosts cached (cache TTL per netbox.yml)"
            exit 0
        fi
        echo "  query returned $count host(s), below floor $MIN_HOSTS"
        if [[ -s "$err" ]]; then
            echo "  stderr from this attempt:"
            sed 's/^/    /' "$err"
        fi
    else
        echo "  ansible-inventory exited non-zero:"
        sed 's/^/    /' "$err"
    fi

    if [[ "$i" -lt "$ATTEMPTS" ]]; then
        echo "  sleeping ${SLEEP}s before retry"
        sleep "$SLEEP"
    fi
done

echo "ERROR: exhausted $ATTEMPTS attempts; NetBox unreachable or returning <$MIN_HOSTS hosts" >&2
exit 1
