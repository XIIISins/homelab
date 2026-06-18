<!-- docs/outline/services-and-purpose/teamspeak.md -->

# TeamSpeak

Voice chat for gaming and hanging out — low-latency, push-to-talk, the kind of always-on voice server a friend group lives in.

---

## Where it runs

TeamSpeak (TS3 server 3.13.7) runs in **asgard K3s** (`teamspeak` namespace) as a single-replica StatefulSet. It's a stateful, singleton service — there's one voice server, and it keeps its channel tree and permissions in Postgres.

- **Database:** Postgres via the Patroni HAProxy VIP (`10.0.10.210`), using TeamSpeak's first-party `ts3db_postgresql` plugin with `sslmode=require`.
- **Identity:** TeamSpeak's own token/identity system. The server-admin privilege key is captured at first boot.

---

## Networking — the shared VIP

Voice and file transfer share a single MetalLB VIP, `10.0.20.12`:

- **UDP 9987** — voice.
- **TCP 30033** — file transfer.

The two ports share one IP via MetalLB's `allow-shared-ip` annotation. The Service uses `externalTrafficPolicy: Local` so the client's real source IP is preserved (TeamSpeak's identity and ban system care about source IP).

---

## The SRV failover ring

Clients don't connect to a raw IP — they resolve an SRV record:

```
_ts3._udp.ts3.xiiisins.com
  ├── hel-ts3   (homelab — primary)
  └── do-ts3    (DigitalOcean — fallback)
```

If the homelab is unreachable, the SRV ring points clients at a DigitalOcean fallback so voice keeps working during an outage. The SRV record lives outside Terraform (it predates the IaC for this service).

---

## See also

- **Storage & data** (Components) — the Patroni Postgres cluster TeamSpeak persists to.
- **Network** (Components) — MetalLB shared-IP VIPs and `externalTrafficPolicy: Local`.
- **URLs** — connection details in the directory.
