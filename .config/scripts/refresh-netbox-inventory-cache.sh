#!/usr/bin/env bash
#
# refresh-netbox-inventory-cache.sh — materialize the NetBox dynamic inventory
# to a static, leak-free YAML cache for the macOS control node.
#
# WHY THIS EXISTS
# --------------
# On macOS, ansible-playbook SIGSEGVs its forked task workers ("A worker was
# found in a dead state") whenever the main process has already done HTTP —
# because macOS CoreFoundation (proxy detection via _scproxy, Network.framework
# path evaluation, os_log → CFPreferences) is NOT fork-safe, and the forked
# child inherits a poisoned CF state. The `netbox.netbox.nb_inventory` plugin
# does exactly that: it makes HTTPS calls to NetBox in the main ansible process
# at startup. So on the Mac, ANY playbook that also does a forked lookup
# (e.g. community.hashi_vault) crashes. Linux controllers (Frigg/Semaphore) are
# immune. Full diagnosis: docs/known-issues/frigg-control-node.md.
#
# THE FIX
# -------
# Materialize the NetBox inventory HERE, in a short-lived separate process that
# does the HTTP and exits — it never forks playbook workers, so it can't crash.
# Playbook runs then read the static cache (zero HTTP in the forking process).
#
# LEAK SAFETY
# -----------
# `ansible-inventory --list` merges adjacent group_vars/ (incl. the ansible-vault
# vault.yml) into hostvars. To avoid baking secrets into the cache, we copy ONLY
# netbox.yml + hosts.yml into a tempdir with NO adjacent group_vars/, so the
# output carries only NetBox host/group metadata. group_vars are re-merged
# in-memory at playbook time from the real inventory/group_vars/ (the cache lives
# in inventory/, so they're adjacent again then).
#
# Output: <repo>/ansible/inventory/.netbox-cache.yml  (git-ignored)
# Consume: ANSIBLE_INVENTORY=inventory/.netbox-cache.yml ansible-playbook ...
#          (the homelab-env shim exports this automatically on macOS)
#
# Requires: ansible-inventory on PATH, python3 (stdlib only), and Vault AppRole
# creds in the env (ANSIBLE_HASHI_VAULT_* — netbox.yml fetches its token via a
# community.hashi_vault lookup). Run after `homelab-env` so creds are loaded.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
ansible_dir="$repo_root/ansible"
inv_dir="$ansible_dir/inventory"
cache="$inv_dir/.netbox-cache.yml"

if ! command -v ansible-inventory >/dev/null 2>&1; then
    echo "refresh-netbox-inventory-cache: ansible-inventory not on PATH" >&2
    exit 1
fi
if [ ! -f "$inv_dir/netbox.yml" ] || [ ! -f "$inv_dir/hosts.yml" ]; then
    echo "refresh-netbox-inventory-cache: expected $inv_dir/{netbox,hosts}.yml" >&2
    exit 1
fi

tmpd="$(mktemp -d "${TMPDIR:-/tmp}/nb-inv.XXXXXX")"
trap 'rm -rf "$tmpd"' EXIT
cp "$inv_dir/netbox.yml" "$inv_dir/hosts.yml" "$tmpd/"

# Materialize in this (separate) process. cd into the tempdir + unset
# ANSIBLE_CONFIG so no repo group_vars/ can be discovered and merged (leak-safe);
# enable the plugins explicitly since we're outside the repo ansible.cfg.
raw="$tmpd/raw.json"
if ! ( cd "$tmpd" && env -u ANSIBLE_CONFIG \
        ANSIBLE_INVENTORY_ENABLED="netbox.netbox.nb_inventory,ansible.builtin.yaml,ansible.builtin.ini" \
        ansible-inventory -i "$tmpd/netbox.yml" -i "$tmpd/hosts.yml" --list ) > "$raw" 2>"$tmpd/err"; then
    echo "refresh-netbox-inventory-cache: ansible-inventory failed (NetBox unreachable? creds not loaded — run homelab-env):" >&2
    sed 's/^/  /' "$tmpd/err" >&2
    exit 1
fi

# Transform `--list` output -> native inventory. JSON is valid YAML, and the
# `yaml` inventory plugin is already enabled in ansible.cfg, so we emit JSON
# (stdlib only — no PyYAML) into a .yml file. Fails loudly if 0 hosts.
python3 - "$raw" "$tmpd/out.yml" <<'PY'
import json, sys
raw, outp = sys.argv[1], sys.argv[2]
d = json.load(open(raw))
hv = d.get("_meta", {}).get("hostvars", {})
if not hv:
    sys.stderr.write("no hosts in materialized inventory\n"); sys.exit(1)
out = {"all": {"children": {}}}
for g, gd in d.items():
    if g in ("_meta", "all"):
        continue
    e = {}
    if gd.get("hosts"):
        e["hosts"] = {h: hv.get(h, {}) for h in gd["hosts"]}
    if gd.get("children"):
        e["children"] = {c: None for c in gd["children"]}
    if gd.get("vars"):
        e["vars"] = gd["vars"]
    out["all"]["children"][g] = e
with open(outp, "w") as f:
    json.dump(out, f, indent=0)
PY

hosts="$(python3 -c "import json,sys; print(len(json.load(open('$raw'))['_meta']['hostvars']))")"
mv "$tmpd/out.yml" "$cache"          # atomic swap into place
chmod 600 "$cache"
echo "refresh-netbox-inventory-cache: wrote $cache ($hosts hosts)"
