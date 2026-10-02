#!/usr/bin/env python3
"""Build the guest -> hypervisor placement map from NetBox (Phase 10d2). Stdlib only.

The Toolbelt groups a burst of alerts by shared hypervisor (a node freeze takes down every guest on it at once, and
must become ONE incident, not twenty). NetBox records each VM's hypervisor in its `device` field; this job reads
that with the Toolbelt's view-only token and writes {"<vm name>": "<hypervisor>"} to a JSON file the API hot-reloads.

Safety: the output is replaced atomically and ONLY if the answer looks complete (at least `--min-guests` placed
guests). A NetBox outage or an empty answer therefore leaves the previous map in place instead of silently
degrading grouping to "unplaced".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def fetch_placement(base_url: str, bearer: str, timeout: float = 15.0, opener=urllib.request.urlopen) -> dict[str, str]:
    placement: dict[str, str] = {}
    # NOTE: never send `brief` at all, not even brief=0: NetBox treats any non-empty value as "brief mode", which drops
    # the `device` (hypervisor) field and made this job see zero placed guests.
    url = f"{base_url.rstrip('/')}/api/virtualization/virtual-machines/?{urllib.parse.urlencode({'limit': 500})}"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer, "Accept": "application/json"}, method="GET")
        with opener(req, timeout=timeout) as r:
            page = json.load(r)
        for vm in page.get("results", []):
            hv = (vm.get("device") or {}).get("name")
            if vm.get("name") and hv:
                placement[vm["name"]] = hv
        url = page.get("next")
    return placement


def write_atomic(path: Path, data: dict[str, str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--creds", required=True, help="netbox.json written by the root loader ({value, url})")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-guests", type=int, default=10)
    args = ap.parse_args(argv)
    cred = json.loads(Path(args.creds).read_text())
    try:
        placement = fetch_placement(cred["url"], cred["value"])
    except (OSError, ValueError, urllib.error.URLError) as e:
        print(f"placement-sync: NetBox unreachable ({type(e).__name__}); keeping the previous map", file=sys.stderr)
        return 1
    if len(placement) < args.min_guests:
        print(f"placement-sync: only {len(placement)} placed guest(s) (< {args.min_guests}); keeping the previous map", file=sys.stderr)
        return 1
    write_atomic(Path(args.out), placement)
    nodes = sorted(set(placement.values()))
    print(f"placement-sync: {len(placement)} guests on {len(nodes)} hypervisors ({', '.join(nodes)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
