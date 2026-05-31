<!-- docs/outline/services-and-purpose/netbox.md -->

# NetBox

The homelab's IPAM/DCIM — the source of truth for what exists on the network: every device, VM, LXC, IP address, VLAN, and prefix. When you need to know "what is `10.0.11.230`" or "which VLAN is the storage network," NetBox is the authoritative answer.

---

## Where it runs

NetBox 4.6.1 runs in **asgard K3s** (`netbox` namespace).

- **Postgres** (external, via the Patroni VIP) for its data.
- **Valkey** (a Redis fork), bundled with the chart in standalone mode, for caching and the task queue.
- **Authentik OIDC** for login.
- Internal-only at `netbox.niflheim.xiiisins.com`.

---

## The truth model — Git spec, NetBox view

NetBox sits in a specific relationship with the rest of the infrastructure-as-code:

- **Git stays the spec.** The Terraform and Ansible in the repo are what *declare* the homelab into existence.
- **NetBox is the queryable view of reality.** It mirrors what's actually deployed, in a form you can search, filter, and reason about — which flat HCL files don't give you.

The two are kept in sync by a standing pattern in `terraform/netbox/`: **every new LXC or VM Terraform resource must have a matching NetBox virtual-machine + interface + IP-address declaration.** Provisioning a host isn't done until its NetBox row exists. That discipline is what keeps NetBox trustworthy as the source of truth rather than a stale side-record.

---

## Access and permissions

- Login is Authentik OIDC. A first-time login provisions a NetBox user record automatically — but with **zero permissions**. NetBox-side permissions are assigned manually in the NetBox UI (via a break-glass local-superuser login) until automated group→permission mapping is worth building.

---

## See also

- **GitOps & automation** (Components) — how NetBox feeds Ansible's dynamic inventory, and the Terraform → NetBox standing pattern.
- **Network** (Components) — the VLAN and IP plan NetBox is the truth for.
- **Compute & hypervisors** (Components) — the VM/LXC creation flow that includes the NetBox declaration step.
