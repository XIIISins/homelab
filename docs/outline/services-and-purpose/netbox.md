<!-- docs/outline/services-and-purpose/netbox.md -->

# NetBox

The queryable record of what the homelab is: every device, VM and LXC, IP address, VLAN and prefix, at `netbox.niflheim.xiiisins.com`. It also feeds the Ansible dynamic inventory.

---

## The truth model

**Git is the specification; NetBox is the queryable view of it.** Terraform writes NetBox (`terraform/netbox/`); people do not edit it, apart from notes and tags. The standing pattern: **every new LXC or VM Terraform resource gets a matching NetBox VM, interface and IP declaration in the same change** (physical devices go in a separate file). Because the Ansible inventory is built from NetBox tags, a guest that is missing from NetBox is also missing from the fleet's playbooks.

The Terraform provider authenticates with an admin API token (stored in 1Password and Vault); the provider version is pinned because of known quirks. The Ansible inventory uses a separate read-only token.

---

## How it runs

| Aspect | Detail |
|---|---|
| Where | A Helm release in asgard K3s, from the project's OCI chart. Held on the 4.6 line because no released Terraform provider supports 4.7 and its database migration is irreversible. |
| Database | The shared Postgres cluster through the HAProxy VIP. |
| Cache | A bundled Valkey on emptyDir; rebuilds on restart. |
| Sign-in | Authentik OIDC for the `netbox-admins` group, plus a local superuser for break-glass. NetBox-side permissions are assigned manually on first login. |
| Access | Internal-only. Topology, IPs and device descriptions are reconnaissance gold, so even behind SSO there is no public ingress; remote access is over the tailnet. |
| Sizing | Explicit requests and limits from measurement: the server was once OOM-killed during a Terraform apply, which refreshes state against the API heavily. |

---

## Who uses it

- **Ansible**, as the dynamic inventory, filtered to active hosts with a primary IP.
- **Semaphore**, which refreshes the inventory cache every four hours.
- **The operator**, to answer "what is at this address" or "which VMID is that".

---

## See also

- **GitOps & automation** (Components): the inventory and playbook structure.
- **Compute & hypervisors** (Components): the ID scheme NetBox mirrors.
- **Semaphore** (this section): the scheduler that consumes it.
