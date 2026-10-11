<!-- docs/outline/diagrams/netbox.md -->

# NetBox diagram

The queryable view of the lab. Git is the spec; Terraform writes NetBox and people mostly read it. Internal only, because the topology is useful to an attacker.

![NetBox: the queryable view of the lab](/api/attachments.redirect?id=810e3d41-000c-41ed-8188-d3b0d6a12025)

---

## How to read it

- **Top row: the only way in.** The operator, on the LAN or tailnet, reaches NetBox through an AdGuard rewrite (`netbox.niflheim`) to Traefik's niflheim Gateway. There is no midgard listener, no tunnel and no public ingress.
- **The `netbox` namespace.** NetBox web (granian), an RQ worker for background jobs and Valkey on an `emptyDir` for cache and queue. NetBox is held on the 4.6 line until the Terraform provider supports 4.7. A local superuser is kept for break-glass.
- **Left: who writes and who reads.** Terraform mirrors every LXC and VM, with its interface and IP (physical devices too), using an admin API token. The Ansible inventory plugin and Semaphore read hosts and groups with a separate inventory token. Kubernetes workloads are not in NetBox: it is IPAM and DCIM, not cluster state.
- **Right: dependencies.** PostgreSQL through the VIP (TLS, read-write sessions only). Authentik OIDC for login: it auto-creates users with zero permissions, which are raised by hand, and the OIDC back-channel goes CoreDNS to Traefik. Vault through External Secrets supplies the secret key, token peppers, OIDC client and superuser.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[netbox.excalidraw 40443](/api/attachments.redirect?id=8d8cd0fa-52f7-4926-a8e0-39b1b2b471e2)

[netbox.svg 52501](/api/attachments.redirect?id=1209d67a-3e19-4f4b-b12d-8aee391fcbc9)

---

## See also

- **NetBox** (Services): the service page and its truth model.
- **GitOps & automation** (Components): the inventory and playbook structure.
