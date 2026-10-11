<!-- docs/outline/diagrams/asgard-lxcs.md -->

# Asgard LXCs diagram

What runs outside Kubernetes, and on which Proxmox host. One column per host; pairs and trios are spread so no single host takes a whole service down.

![Asgard LXCs: what runs outside K3s, and where](/api/attachments.redirect?id=ead5cdd6-34d9-4db3-84e7-f895e9e55dff)

---

## How to read it

- **Three columns: Urd, Verd, Skuld.** Each box is one LXC, coloured by role: orange backup and monitoring, green DNS and Tailscale, purple PostgreSQL, yellow workloads people use, teal automation.
- **Trios, one per host.** AdGuard (Saga, Mimir, Kvasir), Patroni members (Fulla, Vör, Idunn), HAProxy plus etcd (Hlin, Eir, Snotra) and the Tailscale trio each put one member on every host, so losing a host costs one member of each.
- **Singletons avoid Skuld.** Skuld has an open hardware fault (it hard-freezes), so singleton roles such as PBS and the Tailscale exit node live on Urd or Verd; only pair members and replicas sit on Skuld.
- **Dashed outline: the root-auth Terraform module.** The Tailscale routers and the exit node, and Jellyfin, need create-time settings (device passthrough, NFS mounts) that only root ticket authentication allows. Everything else uses the API-token module (right).
- **Shared addresses.** The AdGuard trio shares one VIP; PostgreSQL is reached through the HAProxy trio's VIP.
- **Every LXC** is also declared in NetBox, listed in the Ansible inventory and has a role and playbook. PBS snapshots each one.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[asgard-lxcs.excalidraw 49372](/api/attachments.redirect?id=7ffdf440-038f-4781-babe-16f8e206e28a)

[asgard-lxcs.svg 68027](/api/attachments.redirect?id=9940ed50-1ad0-4962-85ac-f509db3051ab)

---

## See also

- **Compute & hypervisors** (Components): the LXC table with VMID ranges, and VM vs LXC vs pod.
- **Hypervisors** (Hardware): the three nodes, and why Skuld is special.
- **Network** (Components): the VIPs and keepalived.
