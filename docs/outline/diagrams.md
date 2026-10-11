<!-- docs/outline/diagrams.md -->

# Diagrams

Pictures of the homelab. The first page shows the whole lab on one sheet; the pages that follow zoom into a single component or a single flow. Each diagram page carries the picture, a short guide to reading it, and the editable source.

---

## The diagrams

| Page | Shows |
|---|---|
| **Overview diagram** | The whole lab: the edge, the three Proxmox hosts and what runs on each, the asgard Kubernetes cluster, the storage tiers and the off-site services. |

More pages will be added here as components and flows get their own diagram.

---

## Conventions

- **No addresses or IDs in a diagram.** No IP addresses, VM or container IDs, VLAN numbers or subnets. Those live in the text pages (**Hardware**, **Components & interactions**), so a diagram can be shared or reused without leaking the addressing plan.
- **Colour means role, everywhere.** Blue is Kubernetes (VMs and platform), green is DNS and Tailscale, purple is PostgreSQL, orange is backup, monitoring and storage-side services, yellow is workloads people use, teal is automation and control, red is the firewall and edge, grey is outside the lab.
- **Hand-drawn on purpose.** The diagrams are drawn in Excalidraw's sketch style to read as a map, not a spec.

---

## Editing a diagram

Every diagram page has an `.excalidraw` file attached. To change one:

1. Download the `.excalidraw` file from its page.
2. Open <https://excalidraw.com>, choose **Open** (Cmd/Ctrl+O) and pick the file.
3. Edit, then export a PNG (turn on **Background**, leave **Dark mode** off) and an SVG.
4. Replace the attachments on the page.

The picture inside Outline is a PNG, so it only changes when the page is edited.
