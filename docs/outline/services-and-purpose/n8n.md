<!-- docs/outline/services-and-purpose/n8n.md -->

# n8n

Workflow automation — the operator's tool for wiring services together with scheduled and event-driven flows. The **editor UI and API are internal-only and gated**; the **webhook/form trigger endpoints are public** so third-party services can drive workflows.

---

## Where it runs

A single-replica deployment in **asgard K3s**, Postgres-backed via the Patroni HAProxy VIP (`10.0.10.210`). The pod is **stateless on disk** — all state (workflows, encrypted credentials) lives in Postgres, so there's no PVC. The one load-bearing secret is the encryption key (Vault `secret/k8s/n8n/app`) that encrypts every stored credential in the database.

---

## Access model — deliberately asymmetric

Two routes with different exposure:

| Hostname | Exposure | Serves | Auth |
|---|---|---|---|
| `n8n.niflheim.xiiisins.com` | internal (LAN / tailnet) | the full app (editor UI + API + webhooks) | Authentik ForwardAuth (`n8n-admins`) |
| `n8n.xiiisins.com` | public (Cloudflare tunnel) | trigger paths only (`/webhook*`, `/form*`) | none — n8n authenticates webhooks per workflow |
| `n8n.midgard.xiiisins.com` | internal LAN | trigger paths only | none |

The operator uses the gated **niflheim** host. The **public apex** exposes *only* the webhook/form handlers — any other path 404s at Traefik — so the public attack surface is the trigger handler, not the editor. Webhook nodes advertise the public apex URL (`WEBHOOK_URL`) independently of the editor URL; that split is the whole point.

### Why ForwardAuth, not native SSO

n8n's built-in SAML/OIDC is an Enterprise-licensed feature; the community edition can't do it. So SSO is enforced at the edge with Authentik ForwardAuth, with n8n's own login behind the gate as defence-in-depth. Membership in `n8n-admins` means "can reach the login screen," not automatic access.

---

## See also

- **GitOps & automation** (Components) — where n8n sits among the automation tooling.
- **Identity & secrets** (Components) — the `n8n-admins` gate and the encryption-key secret.
- **Storage & data** (Components) — the Patroni Postgres backend n8n's state lives in.
- **Edge** (Components) — the split between the public tunnel route and the internal gated route.
