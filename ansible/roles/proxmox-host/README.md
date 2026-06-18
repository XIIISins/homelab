<!-- ansible/roles/proxmox-host/README.md -->
# proxmox-host

Config-idempotency for the PVE hypervisor hosts (urd/verd/skuld).

**Scope is deliberately narrow.** Today this role manages only the apt
repo posture — disable the enterprise repo (401s without a paid
subscription) and ensure the `pve-no-subscription` source exists. It is
pure config: **it does not upgrade packages and never reboots.**

OS patching for PVE hosts is a high-blast-radius rolling operation
(a host reboot takes every VM/LXC on it down) and lives in its own
deliberately-triggered playbook:
[`ansible/playbooks/proxmox-host-patching.yml`](../../playbooks/proxmox-host-patching.yml)
— see [`docs/procedures/proxmox-host-patching.md`](../../../docs/procedures/proxmox-host-patching.md).

Keeping this role config-only means it's safe to run under
`site.yml` / `drift-check` (it's wired into `asgard-proxmox.yml`).

## What it does

| Task | Effect |
|------|--------|
| Remove enterprise sources | `pve-enterprise.sources`, `ceph.sources`, legacy `.list` variants → absent |
| Ensure no-subscription | **Default off** — templates `pve-no-subscription.sources` only when `proxmox_host_manage_no_subscription: true` |
| apt cache refresh | Handler runs `apt update` after any repo change |

**The existing fleet already has a hand-rolled no-sub repo at
`/etc/apt/sources.list.d/proxmox.sources`** (+ the
`proxmox-archive-keyring.gpg` keyring), so `proxmox_host_manage_no_subscription`
defaults **off** to avoid creating a duplicate source. On the live fleet
the role's only effect is removing the enterprise `pve-enterprise.sources`
+ `ceph.sources` (both 401 without a subscription — they don't run Ceph).

For a fresh/rebuilt node with no no-sub repo, flip the var to `true`;
codename (`{{ ansible_distribution_release }}`) + keyring path resolve
across PVE 8 (bookworm) / 9 (trixie). See `defaults/main.yml`.

> If you'd rather keep `ceph.sources` (e.g. switch it to the no-sub Ceph
> repo later), drop it from `proxmox_host_enterprise_sources_absent`.

## Usage

```bash
# Drift-safe converge (also runs under site.yml / drift-check):
ansible-playbook playbooks/asgard-proxmox.yml --tags proxmox-host

# Repos only:
ansible-playbook playbooks/asgard-proxmox.yml --tags proxmox-host:repos
```

## Why no-subscription isn't relied-upon-as-present

`pbs-repo-fix.yml` only *removes* the enterprise repo because the PBS
installer already drops a no-sub line. The PVE installer does **not** —
it only adds enterprise — so a freshly-installed or rebuilt node needs
the no-sub source laid down explicitly. On an existing node that already
has a no-sub line in `/etc/apt/sources.list`, apt may emit a benign
"configured multiple times" warning; the `.sources` file is the
canonical home (clean up the stray `sources.list` line by hand).
