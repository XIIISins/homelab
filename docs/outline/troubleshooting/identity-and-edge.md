<!-- docs/outline/troubleshooting/identity-and-edge.md -->

# Identity & edge

Login flows, the reverse-proxy edge, and the external entry path. These symptoms usually present as "I can't log in" or "the cert/token is wrong."

---

## After changing the brand domain, protected-app logins redirect to the old host

**Symptom:** you changed Authentik's brand domain, but login buttons on protected apps (vmui, the VictoriaLogs UI, anything behind a proxy provider) still redirect to the *old* host.

**Cause:** the Authentik embedded outpost caches its own `config.authentik_host` at startup, separate from the brand. The brand controls the apex UI and most flows; the outpost has its own source of truth.

**Fix:** PATCH the outpost's `config.authentik_host` to the new host via the API and restart the `authentik-server` pods. Make it durable by pinning `config.authentik_host` in the outpost blueprint so a rebuild reconstitutes it.

---

## A user is denied from an app — "Policy binding 'None' returned result 'False'"

**Symptom:** a user authenticates to Authentik but is denied a specific app with `Permission denied ... Policy binding 'None' returned result 'False'`.

**Cause:** the app is gated on group membership and the user isn't in the group. The `None` is literal — it's how Authentik renders an anonymous group-membership policy on the deny page, not a missing variable.

**Fix:** add the user to the app's group in `terraform/authentik/users.yaml` (membership is declared on the user side) and `terraform apply`. Every per-app gate is opt-in per user — granting Authentik access isn't the same as granting any one app.

---

## Authentik OIDC discovery returns 404

**Symptom:** `https://authentik.<host>/.well-known/openid-configuration` is a hard 404.

**Cause:** Authentik's discovery is **per-app**, not at the host root.

**Fix:** use the per-app path: `https://authentik.<host>/application/o/<slug>/.well-known/openid-configuration`. For WAF/CDN skip rules, the `/application/o/*` prefix covers discovery, authorize, token, userinfo, jwks, and end-session.

---

## `terraform apply` against Cloudflare returns 401

**Symptom:** every Cloudflare TF resource 401s, with no signal that the token itself is the problem.

**Cause:** the Cloudflare API token is stale — rotated upstream but never propagated, or replaced incorrectly. 1Password stores tokens but can't validate them.

**Diagnose:** verify the token directly before running TF:
```
curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  https://api.cloudflare.com/client/v4/user/tokens/verify
```
A healthy token returns `"status":"active"`.

**Fix:** mint a new token with the required scopes, update the 1Password item, `homelab-env --refresh` to repopulate the env cache, and verify again before retrying TF.

---

## SSH "REMOTE HOST IDENTIFICATION HAS CHANGED" after a rebuild

**Symptom:** SSH to a host refuses with the big warning banner after that host was rebuilt at the same IP.

**Cause:** the host got fresh SSH host keys; your `~/.ssh/known_hosts` still has the old ones. (Ansible itself is unaffected — it has host-key checking off.)

**Fix:** remove the stale entries on your workstation:
```
ssh-keygen -R <ip>
ssh-keygen -R <hostname>      # if an AdGuard rewrite name is also cached
```

## See also

- **Identity & secrets** (Components) — Authentik's OIDC/SAML/LDAP model and the proxy-provider outposts.
- **Edge** (Components) — Cloudflare tunnels, cert-manager, the three DNS zones.
- **Cloudflare / DNS** behaviour is also touched in **DNS & networking** (this section).
