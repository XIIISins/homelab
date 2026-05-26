#!/usr/bin/env bash
# ansible/scripts/refresh-netbox-inventory.sh
#
# Wipes the netbox.netbox dynamic-inventory cache so the next
# inventory build re-queries NetBox. Invoked by the Semaphore
# `refresh-netbox-inventory` template on a 4h cron + on-demand.
#
# Cache location matches the netbox.yml plugin's
# `cache_connection: /var/lib/semaphore/inventory-cache` setting +
# the ANSIBLE_INVENTORY_CACHE_CONNECTION env var on the Semaphore
# pod. cache_prefix is `netbox_asgard_` per netbox.yml.
#
# Exit non-zero if the cache dir doesn't exist (something else is
# wrong) but ignore a missing cache file (cold pod, no prior run).
set -euo pipefail

CACHE_DIR="${ANSIBLE_INVENTORY_CACHE_CONNECTION:-/var/lib/semaphore/inventory-cache}"

if [[ ! -d "$CACHE_DIR" ]]; then
    echo "ERROR: cache dir $CACHE_DIR does not exist" >&2
    exit 1
fi

# rm -f swallows "no match" silently — perfect for the cold-pod case.
# The glob expands to nothing safely under nullglob, which we set
# explicitly so the literal pattern doesn't get passed to rm if no
# files exist.
shopt -s nullglob
files=("$CACHE_DIR"/netbox_asgard_*)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    echo "no cache files to wipe (cold or already cleared)"
    exit 0
fi

echo "wiping ${#files[@]} cache file(s):"
printf '  %s\n' "${files[@]}"
rm -f "${files[@]}"
echo "ok"
