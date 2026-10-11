<!-- docs/outline/services-and-purpose/teamspeak.md -->

# Teamspeak

Voice chat for friends. A TeamSpeak 3 server in Kubernetes with an offsite standby for when the homelab is down.

![TeamSpeak 3: voice for friends, with an offsite failover](/api/attachments.redirect?id=25384197-591a-422a-be90-c7efbcf57a92)

---

## How it runs

A single-replica StatefulSet in asgard K3s. Its database is a role on the shared Postgres cluster, reached through the HAProxy VIP; file transfers use a small NFS volume. It started life as a planned LXC and moved to Kubernetes for GitOps uniformity; high availability comes from the client-side failover below, not from the pod.

## Reaching it

Teamspeak is raw UDP and TCP, so it bypasses Traefik and Cloudflare entirely:

```
Friend's client → DNS SRV for ts3.xiiisins.com → hel-ts3.xiiisins.com → home IP
 → UCG-Ultra port-forward (UDP 9987 voice, TCP 30033 files)
 → MetalLB VIP 10.0.20.12 → a worker → the pod
```

The service uses a pinned MetalLB address, `externalTrafficPolicy: Local` (so per-IP bans see real source addresses) and the VLAN 20 policy routing that keeps replies symmetric. The admin ServerQuery ports are not forwarded; they are reached only with a port-forward from inside.

## The failover ring

The DNS SRV record lists two targets: the homelab at priority 1 and an offsite node on DigitalOcean at priority 99. TeamSpeak clients fall through to the second when the first does not answer. The offsite server runs the same version with its own independent database, restored from a snapshot, and is explicitly best-effort: if it misbehaves, wiping its database is acceptable and users reconnect. The DNS records are managed in Terraform.

## Notes

- LAN and tailnet clients reach the server through the public IP and hairpin at the router. A local DNS rewrite would avoid that if it ever matters.
- Port-forwards on the UCG-Ultra are not in IaC; they are recorded in the docs.

---

## See also

- **Network** (Components): MetalLB and the policy-routing rules.
- **Storage & data** (Components): the Postgres cluster behind it.
- **Edge** (Components): the non-HTTP path.
- **TeamSpeak diagram** (Diagrams): the normal path and the DNS failover.
