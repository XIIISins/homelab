<!-- docs/outline/troubleshooting/identity-and-edge.md -->

# Identity & edge

Symptoms with sign-in, certificates, the public tunnel, Traefik and SSH. The path of a web request: client → AdGuard (internal) or Cloudflare (public) → Traefik `10.0.20.10` (or Cloudflare tunnel → `cloudflared` → Traefik's ClusterIP) → HTTPRoute → app. Sign-in is Authentik (OIDC/SAML natively, or a ForwardAuth middleware for tools without SSO).

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
```

---

## 1. First diagnostics (read-only)

```bash
# What does the edge say? Status, redirect target, cert subject/expiry
H=wiki.xiiisins.com    # or any hostname
curl -sI -m 8 https://$H | head -8
echo | openssl s_client -connect $H:443 -servername $H 2>/dev/null | openssl x509 -noout -subject -enddate -issuer

# Traefik and the Gateways
kubectl -n traefik get pods -o wide                                   # 3 pods, one per worker
kubectl get gateway -A; kubectl get httproute -A | head -40           # Programmed/Accepted?
kubectl -n traefik logs deploy/traefik --tail=40

# Certificates: all True?
kubectl get certificate -A
kubectl get certificaterequest,order,challenge -A 2>/dev/null | grep -v "valid\|True" | head    # stuck issuance shows here

# Tunnel
kubectl -n cloudflared get pods
kubectl -n cloudflared logs deploy/cloudflared --tail=30               # "Registered tunnel connection" x4 is healthy
kubectl -n cloudflared get pods -o wide                                # 3 Ready; readiness = at least one live edge connection

# Authentik
kubectl -n authentik get pods                                          # server x3, worker, redis
curl -s -o /dev/null -w 'authentik public: %{http_code}\n' https://authentik.xiiisins.com/-/health/ready/
curl -s -o /dev/null -w 'authentik internal: %{http_code}\n' https://authentik.midgard.xiiisins.com/-/health/ready/
```

Test each path separately: **public** (`authentik.xiiisins.com`), **internal alias** (`*.midgard.xiiisins.com`), **internal-only** (`*.niflheim.xiiisins.com`). If only one fails, the problem is in that path.

---

## 2. Sign-in problems (Authentik)

### "Permission denied. Request has been denied. Explanation: Policy binding 'None' returned result 'False'"

Not a bug: the user is not in the app's group. The literal `None` is how Authentik prints an anonymous group-membership policy. Fix in Git: add the user to the group in `terraform/authentik/users.yaml` (groups such as `outline-users`, `immich-users`, `media-admins`, `zabbix-admins`, `netbox-admins`, `monitoring-admins`, `tailscale-users`), PR, then:

```bash
terraform -chdir=terraform/authentik plan       # main checkout, root Vault token (operator)
terraform -chdir=terraform/authentik apply
```

### After a host or brand change, protected apps still redirect to the OLD host

The embedded **outpost** caches its own `config.authentik_host`, separate from the brand. It is pinned by the `02-embedded-outpost.yaml` blueprint; if it drifted, PATCH the outpost through the API and restart the server pods:

```bash
OUTPOST=$(curl -s "$AUTHENTIK_URL/api/v3/outposts/instances/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq -r '.results[]|select(.name|test("embedded";"i"))|.pk')
curl -s "$AUTHENTIK_URL/api/v3/outposts/instances/$OUTPOST/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq '.config.authentik_host'
# if wrong: PATCH config.authentik_host to the correct URL (then restart)
kubectl -n authentik rollout restart deploy/authentik-server
```

### A protected tool (vmui, logs, Sonarr, SABnzbd, MicroBin `/list`) loops on the login page or returns 404 on `/outpost.goauthentik.io/…`

A new `forward_single` proxy provider must be **attached to the embedded outpost once** (Terraform does not manage the outpost). List and attach through the API (or in the Authentik admin UI: Outposts → embedded → add the provider):

```bash
curl -s "$AUTHENTIK_URL/api/v3/outposts/instances/$OUTPOST/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq '.providers'        # does the provider pk appear?
# attach: PATCH {"providers":[<existing pks…>,<new pk>]}
```

For a partially-gated app (MicroBin), the `/outpost.goauthentik.io` path must also route through the ForwardAuth rule, or the callback never completes.

### OIDC discovery returns 404 at the host root

Discovery is per application: `https://authentik.xiiisins.com/application/o/<slug>/.well-known/openid-configuration`. Use that. For a WAF/CDN rule, allow the `/application/o/*` prefix.

### An app ignores settings you put in the Helm values

For Authentik, environment variables (from the ExternalSecret) override the values block silently. Change the env var in the ExternalSecret, not `values`.

### Authentik is down or nobody can sign in

- Check the pods and database: `kubectl -n authentik logs deploy/authentik-server --tail=50`; Postgres VIP (**Vault & Postgres** B1).
- Every app keeps a **local break-glass admin** (stored in 1Password). Zabbix: the `hugin-direct.niflheim.xiiisins.com` backdoor with the local `Admin`. Vault: the root token (operator).
- Zabbix SAML specifics: `Response is not signed and SP requires signed response` → the provider needs `sign_response = true`; `idp_sso_url_invalid` → run `terraform apply -refresh-only` once after creating a SAML provider; a `…uid is missing from user attrs` error means `username_attribute` names a property mapping that is not attached.
- A user locked out of Zabbix after five bad logins: on the Postgres leader, `UPDATE users SET attempt_failed=0, attempt_clock=0, attempt_ip='' WHERE username='Admin';` (run via `sudo -iu postgres psql zabbix`).
- Terraform for Authentik shows a perpetual `key_data -> null` diff on certificate-only key pairs: a write-only field; the provider cannot converge it. Ignore.

---

## 3. Certificates will not issue or renew

Three wildcards (`*.niflheim`, `*.midgard`, `*.xiiisins.com` apex) issue by **DNS-01 against Cloudflare** with a dedicated token (`cert-manager-niflheim-dns01`), and an internal CA issues the Vault/in-cluster certificates.

```bash
kubectl get certificate -A
kubectl -n <ns> describe certificate <name> | tail -20
kubectl -n <ns> get certificaterequest,order,challenge
kubectl -n cert-manager logs deploy/cert-manager --tail=60 | grep -iE "error|cloudflare|challenge"
# Is the Cloudflare token cert-manager uses valid? (token is read from the Secret, never printed)
kubectl -n cert-manager get secret cloudflare-api-token -o jsonpath='{.data.api-token}' | base64 -d | { read -r T; curl -s -H "Authorization: Bearer $T" https://api.cloudflare.com/client/v4/user/tokens/verify | jq '.success,.result.status'; }
# Expiry as served
for h in vault.niflheim.xiiisins.com hugin.midgard.xiiisins.com wiki.xiiisins.com; do echo -n "$h "; echo | openssl s_client -connect $h:443 -servername $h 2>/dev/null | openssl x509 -noout -enddate; done
```

| Finding | Cause | Fix |
|---|---|---|
| Challenge pending with a Cloudflare 401/403 | The cert-manager token is stale/invalid | **Credential rotation** §3 (the cert-manager token is separate from Terraform's; never overwrite its path with the Terraform token) |
| `/user/tokens/verify` says "Invalid" for a token that works elsewhere | You used the wrong verify endpoint for the prefix | `cfut_` = `…/user/tokens/verify`; `cfat_` = `…/accounts/<id>/tokens/verify` |
| Overwrote `secret/k8s/cert-manager/cloudflare` by mistake | Wrong token written | `vault kv rollback -version=<previous> secret/k8s/cert-manager/cloudflare`, force-sync the ExternalSecret |
| Certificate `Ready` but the browser shows the old one | Traefik caches the certificate Secret | `kubectl -n traefik rollout restart deploy/traefik` |
| Semaphore `infra-health-check` posts "cert expires in N days" | Renewal is failing | Work the rows above; renewal starts well before expiry |

## 4. Public tunnel and Traefik

| Symptom | Check / cause | Fix |
|---|---|---|
| Public hostname returns Cloudflare 1033/530 or times out | `cloudflared` pods not connected | `kubectl -n cloudflared get pods; kubectl -n cloudflared logs deploy/cloudflared --tail=40`; restart `kubectl -n cloudflared rollout restart deploy/cloudflared`; credentials come from the `cloudflared` ExternalSecret |
| Public hostname returns **404** from the tunnel | No ingress rule for it (the catch-all is 404) | Add the rule in `k8s/asgard/infrastructure/cloudflared/configmap.yaml` and the Cloudflare DNS record in `terraform/cloudflare/main.tf`; both through Git. Internal-only names must have **neither** |
| Tunnel connects but wrong app/blank | Host header not set per rule, or it targets a MetalLB IP | Each rule targets `https://traefik.traefik.svc.cluster.local:443` with `httpHostHeader` set |
| 502/503 for `jellyfin.midgard…`, `hugin…` | The LXC behind the Service+EndpointSlice is down | Use the direct names to confirm (`jellyfin-direct`, `hugin-direct.niflheim…`); **Hosts & Proxmox** |
| Traefik rollout stuck Pending | Surge pod with required anti-affinity | `updateStrategy` at the chart root with `maxSurge: 0`: **Kubernetes & Flux** §3 |
| An HTTPRoute is not attached | The Gateway listener's allowed namespaces/hostnames | `kubectl describe httproute <name> -n <ns> | tail -20` (look for `Accepted: False` and its reason) |
| Security headers missing | The entrypoint-default middleware | `curl -sI https://paste.xiiisins.com \| grep -iE 'strict-transport\|x-content-type\|referrer-policy'` after any Traefik change |
| Client IP missing in logs / Teamspeak bans wrong | `externalTrafficPolicy: Local` not in effect | `kubectl get svc -A \| grep LoadBalancer`; check `.spec.externalTrafficPolicy` |
| Pod cannot reach an OIDC provider on `authentik.midgard…` | Pod-to-VIP hairpin | CoreDNS rewrite to the Traefik service: **DNS & networking** §5 |

## 5. SSH and host keys

| Symptom | Cause | Fix |
|---|---|---|
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The guest was rebuilt at the same IP | `ssh-keygen -R <ip>; ssh-keygen -R <name>` on your workstation. Ansible is unaffected (host key checking is off) |
| `sshd: no hostkeys available -- exiting` after a hard crash | Zero-byte host key files from a mid-write crash | `ls -la /etc/ssh/ssh_host_*_key` (zero bytes, old mtime); from the Proxmox host `pct exec <ctid> -- ssh-keygen -A && systemctl restart ssh` |
| `Too many authentication failures` | The hardening role limits `MaxAuthTries` to 3 and an agent offers several keys | `ssh -o IdentitiesOnly=true -i <key> …` |
| `Permission denied (publickey)` from Frigg | Frigg's fleet key is in a memory-only agent that is empty after a reboot | Re-load the key per the Frigg runbook (**Monitoring & automation** §6) |
| Cannot SSH as a personal user | LDAP-backed SSH is not deployed; root login is locked | Use key-based `ansible`, Tailscale SSH, or the `recovery` break-glass user; or `pct exec` from the Proxmox host |
| `last reboot` shows several "still running" entries | Hard crashes, not reboots | A first pass for "broken since some time": `last reboot \| head; journalctl --list-boots \| tail -5` |

---

## See also

- **Edge** and **Identity & secrets** (Components).
- **Credential rotation** (Procedures): replacing a stale token.
- **DNS & networking** (this section): when the name does not resolve at all.
