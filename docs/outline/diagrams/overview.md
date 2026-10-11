<!-- docs/outline/diagrams/overview.md -->

# Overview diagram

The whole homelab on one sheet. It is a map, not a reference: it shows what exists and how the layers connect. **Components & interactions** explains each layer, and **Hardware** carries the addresses and specs that are left out here on purpose.

![Homelab overview](/api/attachments.redirect?id=e7a7b97d-1437-42c9-a9d9-9bde9cdfe265)

---

## How to read it

- **Top row: the edge.** Public names reach the lab two ways. Web traffic goes Internet to Cloudflare, then down an outbound tunnel that `cloudflared` dials from inside the cluster, to Traefik. Everything else comes through the KPN modem (a pass-through) into the UCG-Ultra, the only firewall. Home clients and the tailnet reach the lab through the UCG-Ultra and the Tailscale containers.
- **Middle: the Proxmox cluster `niflheim`.** Three identical hosts, each with the same shape: infrastructure containers on top (DNS, PostgreSQL, backup, monitoring, a few services), and at the bottom one Kubernetes control-plane VM and one worker VM. The strip above the hosts shows the two services that span all three: the AdGuard DNS address and the PostgreSQL address (HAProxy in front of Patroni).
- **Bottom left: the asgard K3s cluster.** It runs on the six Kubernetes VMs above (dashed lines). Inside: ingress and certificates, the platform services, the apps people use, and the four storage classes that Kubernetes volumes come from.
- **Bottom right: storage and outside.** Munin (the NAS) backs the file and block storage classes and holds the backup datastore. The grey boxes are things outside the lab that it depends on.
- **Colours** follow the role legend at the bottom; the same colours are used in every diagram.

---

## Files

The editable source (open it at excalidraw.com; the export settings are on the **Diagrams** page) and a vector copy for zooming in or reusing elsewhere.

[homelab-overview.excalidraw 142227](/api/attachments.redirect?id=2879cfa7-c684-4e98-a767-46a3eaf8fa74)

[homelab-overview.svg 279452](/api/attachments.redirect?id=0e70051d-5a88-46ac-a6d9-134aa9e511fe)
