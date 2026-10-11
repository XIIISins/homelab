<!-- docs/outline/diagrams/teamspeak.md -->

# TeamSpeak diagram

Voice for friends, with an offsite failover. A single replica on MetalLB with source IPs preserved for bans; if the homelab is down, the client falls over by DNS.

![TeamSpeak 3: voice for friends, with an offsite failover](/api/attachments.redirect?id=25384197-591a-422a-be90-c7efbcf57a92)

---

## How to read it

- **Left to right: the normal path.** A friend's TS3 client looks up Cloudflare DNS, which has an SRV record listing the homelab first. The traffic crosses the KPN Experia Box (a DMZ pass-through) to the UCG-Ultra, whose port-forwards carry voice (UDP) and file transfer (TCP) into asgard K3s.
- **In the cluster (blue box).** A MetalLB VIP in L2 mode: one address, two Services, and some worker answers. It fronts a one-replica TeamSpeak 3 StatefulSet with `externalTrafficPolicy: Local`, so per-IP bans see real client addresses.
- **Below the pod.** A 1 Gi NFS volume holds logs and file transfers. PostgreSQL through the VIP, over TLS, holds channels, permissions and identity: the source of truth.
- **Down the left: the failover.** If the homelab is unreachable, the second SRV record (priority 99) points to the DigitalOcean node (`do1`), which runs its own TeamSpeak container restored from a database snapshot. It is the standard client-side SRV fallback, with no extra tooling.
- **Middle: who manages it.** Terraform owns the A and SRV records. The UCG port-forwards are manual, and the KPN box is never in IaC.
- **Reply path.** Replies leave through the node's second network card (policy routing), so the UCG's stateful NAT sees the answer.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[teamspeak.excalidraw 39848](/api/attachments.redirect?id=82a6b9d1-f2eb-4b75-b432-d31683b21056)

[teamspeak.svg 51491](/api/attachments.redirect?id=08eed173-e55c-4c50-b796-ab073edb0030)

---

## See also

- **Teamspeak** (Services): the service page and the failover ring.
- **Network** (Components): MetalLB and the policy-routing rules.
- **Edge** (Components): the non-HTTP path.
