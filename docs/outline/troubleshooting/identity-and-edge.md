<!-- docs/outline/troubleshooting/identity-and-edge.md -->

# Identity & edge

Symptoms with sign-in, certificates, the tunnel and SSH.

---

## First diagnostics

```
curl -sI https://<host> | head      # status, headers, which cert
kubectl get certificate -A          # Ready? expiry?
kubectl get gateway,httproute -A    # Accepted / Programmed?
```

## Sign-in (Authentik)

| Symptom | Likely cause | What to do |
|---|---|---|
| After changing the brand domain, protected apps redirect to the **old** host | The embedded outpost caches its own host URL at start-up | Patch the outpost's `authentik_host` through the API and restart it. |
| A protected app loops on the login page | The proxy provider is not attached to the embedded outpost | Attach it once in the Authentik UI. |
| "Permission denied … Policy binding 'None' returned result 'False'" | The user is not in the per-app group (the literal `None` is how Authentik prints a group-membership policy) | Add the user to the group in `users.yaml` and apply. |
| OIDC discovery 404 at the host root | Discovery is per application: `/application/o/<slug>/.well-known/openid-configuration` | Use the per-app URL; for WAF rules allow the `/application/o/*` prefix. |
| Authentik settings from the Helm values are ignored | Environment variables win silently over the values block | Set it through the ExternalSecret env vars. |
| A blueprint fails on the brand | Two brands claim default | Demote the shipped default first; use brand blueprints, not tenants. |
| Terraform shows a perpetual `key_data -> null` diff | The API never returns the key material | The resource ignores it (a write-only field). |
| Everything is locked out | Authentik is down or no credential works | Local break-glass accounts on each app; for the API, mint a token directly against the database. |

## Certificates and the edge

| Symptom | Likely cause | What to do |
|---|---|---|
| A wildcard certificate will not renew | The Cloudflare token is stale or invalid, or you checked it against the wrong verify endpoint | There are separate tokens (Terraform, cert-manager DNS-01, an unused one). `cfut_` tokens verify on the user endpoint, `cfat_` on the account endpoint. Do not overwrite cert-manager's Vault path with the Terraform token. |
| An external hostname returns 404 from the tunnel | No Cloudflared ingress rule for it (the catch-all is 404) | Add the rule and the Cloudflare DNS record; internal-only names must not have either. |
| Tunnel traffic works but routes wrongly | The Host header is not set per rule, or Cloudflared targets a MetalLB address | Target Traefik's ClusterIP service with the Host header set. |
| A route returns 502 for a Jellyfin or Zabbix name | The LXC behind the EndpointSlice is down | Use the direct name to confirm (`jellyfin-direct`, `hugin-direct`). |
| Security headers missing on a route | Entrypoint defaults apply to Gateway routes in practice but are not documented | `curl -sI` the route after any Traefik change. |
| An OIDC backchannel from a pod fails | Pod-to-VIP hairpinning | CoreDNS rewrite to the Traefik service. |

## SSH and hosts

| Symptom | Likely cause | What to do |
|---|---|---|
| `REMOTE HOST IDENTIFICATION HAS CHANGED` after a rebuild | The guest was rebuilt at the same IP | `ssh-keygen -R` the address and name on the workstation. Ansible is unaffected (host key checking is off). |
| `sshd: no hostkeys available` after a hard crash | Zero-byte host key files from a mid-write crash | Regenerate the host keys. |
| `Too many authentication failures` with an agent | The hardening role limits `MaxAuthTries` to 3 and the agent offers several keys | Offer only the intended key. |
| `last reboot` shows several "still running" entries | Hard crashes, not reboots | A useful first pass for "broken since some time ago". |
| Cannot SSH as a personal user | LDAP-backed SSH is not deployed | Use key-based access or Tailscale SSH; root login is locked by hardening, and the `recovery` user exists for emergencies. |

---

## See also

- **Edge** and **Identity & secrets** (Components).
- **Credential rotation** (Procedures): replacing a stale token.
- **DNS & networking** (this section): when the name does not resolve in the first place.
