"""Phase 10h2, class `capacity`: turn an open forecast finding into the text of a change request for the PR author.

The request only NAMES the finding and the remedy shape; the drafting session reads the repository and makes the one-value change.
Which forecasts have a remedy that lives in the repository is decided here, in one table, so the button only appears where a
value change can actually be proposed:

  fleet-fs-used     a guest's filesystem filling   -> the LXC / VM disk size in Terraform (grow only)
  memory-used       steady memory creep            -> the LXC memory value in Terraform (grow only)
  pve-storage-used  thin pool / PBS datastore / NAS -> NO button: the PBS prune policy and the NAS share are not in the repository
  k8s-pvc-used-ratio, victorialogs-data-size        -> NO button: the values live under k8s/, outside the capacity class's paths

The change is a proposal for a human to review and apply (`terraform apply` is the operator's); nothing here runs anything."""
from __future__ import annotations

import json

REMEDIES = {
    "vfs.fs.dependent.size[*,pused]": {
        "what": "a filesystem is filling",
        "where": "the `disk { size = N }` block of the guest in terraform/proxmox/asgard-lxcs/*.tf, terraform/proxmox/asgard-lxcs-root/*.tf or "
                 "terraform/proxmox/asgard-vms/*.tf (the series' host name is the guest's `hostname`/`name`)",
        "rule": "Grow only, by about 50 % rounded up to a whole GB, never more than double the current size. If the filesystem is /boot, "
                "a PVE host, a K3s node, or the guest is not in those files, make no change.",
    },
    "vm.memory.size[pavailable]": {
        "what": "memory headroom is shrinking steadily",
        "where": "the `memory { dedicated = N }` block of the guest in terraform/proxmox/asgard-lxcs/*.tf, terraform/proxmox/asgard-lxcs-root/*.tf "
                 "or terraform/proxmox/asgard-vms/*.tf",
        "rule": "Grow only, by about 25 % rounded up to the next 256 MB, never more than double. If the guest is not in those files or is a "
                "K3s node or a PVE host, make no change.",
    },
}


def has_remedy(metric: str) -> bool:
    return metric in REMEDIES


def capacity_request(fc: dict) -> tuple[str, str]:
    """(title, body) for forecast `fc` (a row of the forecasts table, as the Toolbelt serves it). Raises KeyError for a metric with
    no remedy table entry: callers check `has_remedy` first."""
    rem = REMEDIES[fc["metric"]]
    ev = (fc.get("evidence") or {}).get("evidence") or {}
    facts = {k: ev[k] for k in ("current", "capacity", "slope_per_day", "r2", "points") if k in ev}
    eta = fc.get("days_to_full")
    title = f"Capacity: {str(fc['target'])[:90]}"
    body = (
        f"Forecast #{int(fc['id'])} says {rem['what']} on `{fc['target']}` ({fc.get('kind')}"
        + (f", about {eta:.0f} day(s) to its limit" if eta is not None else "")
        + f"). Evidence: {json.dumps(facts, sort_keys=True)}.\n\n"
        f"Make the smallest change that gives headroom: edit {rem['where']}. {rem['rule']}\n\n"
        "Read the file first and find the guest by name. Change exactly one value in one file. In the PR summary say which guest, the old and "
        "new value, and that `terraform apply` for that module is the operator's step (a disk grow also needs the guest's filesystem "
        "grown inside it). If the repository holds no value that fixes this (a different guest type, a path outside the allowed files, "
        "or growing would not help because something is writing too much), change nothing and say why in the summary."
    )
    return title, body
