<!-- docs/outline/diagrams/asgard-k3s.md -->

# Asgard K3s diagram

The one production Kubernetes cluster: the six VMs it runs on, how Flux deploys everything onto it, and what the platform and the apps are made of.

![Asgard K3s: the one production cluster](/api/attachments.redirect?id=a4973a96-6a88-43ac-9557-120ff03f1ee7)

---

## How to read it

- **Top box: six VMs.** A control plane and a worker on each of Urd, Verd and Skuld. The control planes are tainted `NoSchedule`, so only the workers run workloads. Each worker has two network cards (node traffic and MetalLB) and a 50 GB `/data` disk.
- **Top right: what is not Flux.** The Ansible `k3s` role installs K3s, the taints and the etcd snapshots; `k3s-upgrade.yml` moves minors. Calico is K3s addon files on the init node, moved only by `calico-upgrade.yml`.
- **Middle row: the deploy chain.** GitHub `main` is the source; merging is deploying. Flux reconciles `k8s/asgard` into `infrastructure` (every HelmRelease and platform manifest), then the seven `<component>-config` Kustomizations (CRD-dependent objects, each its own failure domain, all `dependsOn` infrastructure). Apps also depend on infrastructure.
- **Platform box: four columns.** Edge and names (cloudflared, Traefik, MetalLB, `coredns-custom`). Certificates and secrets (cert-manager, trust-manager, Vault, External Secrets, Sealed Secrets). Identity, data and watching (Authentik, Garage, the observability agents, VPA, the read-only AIOps identity). Storage classes.
- **Storage classes.** None is the default. iSCSI and NFS both land on Munin; `local-path` is each worker's `/data`; `emptyDir` is for cache-class data.
- **Bottom: the apps.** Mostly behind Traefik. Vault, Zabbix and Jellyfin appear as route-only shims: they run elsewhere (Zabbix and Jellyfin are LXCs by design) and the cluster only fronts them.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[asgard-k3s.excalidraw 91182](/api/attachments.redirect?id=133986fc-0d08-4a80-a7cc-878a8e63b39e)

[asgard-k3s.svg 107157](/api/attachments.redirect?id=64426774-4d03-4c1d-b8cf-ac3767bebf02)

---

## See also

- **Compute & hypervisors** (Components): the VM specs and the K3s install.
- **GitOps & automation** (Components): the Flux layering behind the middle row.
- **Storage & data** (Components): the four storage classes.
- **Network** (Components): the multi-homed worker rules.
