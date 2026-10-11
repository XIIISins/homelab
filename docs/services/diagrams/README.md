<!-- docs/services/diagrams/README.md -->

# Service diagrams

One hand-drawn picture per service page in [`docs/services/`](../), embedded at the top of that page. They show how the service fits together (who talks to whom, what it stores, what it depends on); the page text carries the detail. The whole-lab picture is [`homelab-overview.excalidraw`](../../architecture/homelab-overview.excalidraw).

Each diagram is two files: `<service>.excalidraw` (the editable source) and `<service>.svg` (the export the page embeds; the sketch font is inside it). `jotunheim-k3s.md` has no diagram: that cluster was dropped.

## Conventions

- **No addresses or IDs.** No IP addresses, VM or container IDs, VLAN numbers or subnets. They live in the text docs, so a diagram can be shared without leaking the addressing plan.
- **Colour means role**, the same as the overview: blue K3s / platform, green DNS / Tailscale, purple PostgreSQL, orange backup / monitoring / storage, yellow workloads people use, teal automation / control, red firewall / edge, grey outside the lab. Each diagram has a legend for the colours it uses.
- **Dark theme, transparent background.** Drawn in Excalidraw's dark mode with no background fill, so the page behind the image (Outline, a dark-theme viewer) is the background. On a light page the light ink is hard to read.
- **Sketch style:** Excalidraw "Artist" roughness, Excalifont, flat fills, rounded boxes. Solid arrows are data or requests, dashed arrows are control, fallback or secret hand-offs.

## Editing

1. Open <https://excalidraw.com>, choose **Open** (Ctrl/Cmd+O) and pick the `.excalidraw` file.
2. Edit it. Keep the conventions above.
3. Export **SVG** with *Dark mode* on, *Background* off, *Embed scene* off, and replace the `.svg` next to the source. Commit both.

A diagram changes when the service's shape changes (a new dependency, a moved storage tier), not when a version or size does.
