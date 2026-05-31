<!-- docs/outline/procedures/netbox-initial-data-import.md -->

# NetBox initial data import

First-time population of NetBox with the homelab's inventory — physical devices, VMs, LXCs, VLANs, prefixes, and IPs. Run this against a fresh (or freshly-wiped) NetBox database.

> **Mostly a bootstrap/DR artifact now.** Day-to-day, NetBox records are managed by the Terraform standing pattern in `terraform/netbox/`. This manual import is what you do *once* to seed the database before that module adopts the records — or after a full NetBox wipe. It's not a routine operation.

---

## Preconditions

- NetBox is deployed and reachable at `netbox.niflheim.xiiisins.com`.
- You have admin credentials (the local superuser, password from Vault).
- The database is empty — re-running against populated data creates duplicates.

---

## The import, in dependency order

NetBox enforces foreign keys: a child object is rejected if its parent doesn't exist yet. **Order is load-bearing.** Create objects in this sequence:

1. **Site** — the single homelab site.
2. **Manufacturers** — MSI, Synology, etc.
3. **Device types** — per manufacturer (Cubi 5, DS223J, …).
4. **Device roles** — hypervisor, NAS, switch, router.
5. **Platforms** — Proxmox VE, RHEL, Debian (if used).
6. **Devices** — the physical machines: Urd, Verd, Skuld, Munin, network gear.
7. **Cluster type + cluster** — the Proxmox `niflheim` cluster.
8. **Virtual machines** — every VM and LXC.
9. **VLANs + prefixes** — the ten VLANs and their subnets.
10. **Interfaces** — device and VM interfaces.
11. **IP addresses** — assign to interfaces, then set each device/VM's primary IP.

The exact field values for every object live in the source procedure in the repo; this runbook is the order and the rationale.

---

## After the import

Hand off to Terraform: the `terraform/netbox/` module adopts the imported records via `import {}` blocks and manages them from then on. From that point, **NetBox records are changed through Terraform**, not the UI — the manual import has done its one job.

---

## Danger points

- **Wrong order** → foreign-key errors and a half-populated database. Follow the sequence.
- **Re-running on populated data** → duplicate objects. Wipe first if you're starting over.

## See also

- **NetBox** (Services) — what NetBox is and the Git-spec / NetBox-view truth model.
- **GitOps & automation** (Components) — the Terraform → NetBox standing pattern that takes over after this import.
