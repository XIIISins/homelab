<!-- docs/known-issues/digitalocean.md -->

# Known gotchas — DigitalOcean (offsite droplets / firewalls)

*Surfaced 2026-10-01 while auditing the unmanaged offsite droplet ahead of its IaC rebuild ([`../operations/aiops-roadmap.md`](../operations/aiops-roadmap.md) §10a). Incident retros in [`../incidents/`](../incidents/).*

## Cloud firewalls

- **DO cloud firewalls are additive, and they bind by TAG as well as by droplet ID.** `doctl compute firewall list` showing an empty `Droplet IDs` column does **not** mean unattached — check the `Tags` column against the droplet's tags (`doctl compute droplet get <id> --format Tags`). The effective policy is the **union** of every matching firewall, so a second firewall can only *add* open ports. To tighten, edit the one that actually matches (`doctl compute firewall remove-rules <id> --inbound-rules "protocol:tcp,ports:<p>,address:0.0.0.0/0,address:::/0"`; reversible with `add-rules`). 2026-10-01: `ghost-cloud` (tags `ots,portainer`) was bound to the droplet via its `portainer` tag; a new firewall created to "close ports" would instead have opened 30033/41144. Terraform should bind one explicit firewall by droplet ID and own the whole rule set.
- **A source-IP allowlist rule makes scans from the operator's network meaningless.** The old firewall allowed *all TCP from the operator's home IP*, so every port-scan from the Mac/Frigg (same NAT) showed everything open. Judge exposure by reading the rules (`doctl compute firewall get <id> -o json`) or probing from an independent vantage (a burst droplet), never from the home network. Do not commit the home public IP to this public repo.
- **Docker-published ports bypass UFW.** On a Docker droplet, port policy has to live in the DO cloud firewall; UFW (even if enabled) never sees published ports.
- **The DO droplet tag `tailscale` is DO metadata, unrelated to Tailscale ACL tags.** Same word, different systems — Tailscale tags (`tag:offsite`, …) are set in `policy.hujson` / the Tailscale API.

## Billing / lifecycle

- **A powered-off droplet is still billed**, and any firewall bound to it stays live. Destroy, don't just power off. Droplet snapshots are billed by size — take one before risky changes (`doctl compute droplet-action snapshot <id> --snapshot-name <n> --wait`) and delete it after the soak.
- **A passing `doctl` call does not prove the token is least-privilege.** The `doctl` token on the operator workstation was broad. Terraform gets its own scoped token from Vault; revoke the broad one once the IaC rebuild lands.

## Placement constraints

- **Anything that needs a fixed source IP (PlantNet allowlist) must be a droplet.** App Platform has no static egress without the Dedicated Egress IP add-on (~$25/mo per app, a *pair* of IPs, not available to VPC-connected apps); Functions are excluded entirely. Use a reserved IP (free while attached) so the address survives rebuilds. Decision row: [`decisions.md`](../operations/decisions.md) ("PlantNet proxy stays on a droplet…").
- **DOKS is not K3s and auto-repairs nodes.** Don't use it as the heal/rebuild test substrate; use plain droplets + the existing `k3s` role (decision row "Burst/test substrate…").
