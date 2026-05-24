<!-- docs/incidents/2026-05-23-cloudflare-bot-webfinger.md -->

### 2026-05-23 — Cloudflare bot protections blocked Tailscale WebFinger probe (403)

During `5e.3.c` (Tailscale custom-OIDC tailnet creation via `login.tailscale.com/start/oidc`), Tailscale's WebFinger probe to `https://xiiisins.com/.well-known/webfinger?resource=acct:ghost@xiiisins.com` returned HTTP 403 from Cloudflare's edge. The pod-side WebFinger response was confirmed working via browser + curl-with-default-headers; only Tailscale's specific probe headers (likely missing or generic User-Agent) triggered the 403.

This is documented in the upstream Authentik-Tailscale community guides as a known Cloudflare-front interaction. Root cause is one or more of Cloudflare's bot/integrity protections (Bot Fight Mode, Browser Integrity Check, Managed Rules) firing on non-browser-shaped requests to public endpoints.

**Resolution:** Disabled the relevant Cloudflare protections globally for the `xiiisins.com` zone to unblock 5e.3.c. Tailscale WebFinger discovery succeeded immediately after, full OIDC + token exchange flow completed, tailnet created with Authentik-bound `ghost@xiiisins.com` user.

**Tracked: Cloudflare re-harden after stability.** Heavy-handed global disable is the *unblock* fix, not the *correct* fix. Correct fix is per-path WAF skip rules scoped to:
- `/.well-known/webfinger` (Tailscale's probe path)
- `/.well-known/openid-configuration` (Tailscale also fetches this for OIDC discovery; will be similarly blocked if protections re-enable globally)
- Possibly the Authentik `/application/o/token/` endpoint if Tailscale's token-exchange POST also fires bot rules

WAF rules should land in `terraform/cloudflare/` (Cloudflare resources in IaC decision row) — not manual UI. Tracked as pending task.

**Findings:**
- *Public endpoints behind Cloudflare need explicit allowlisting for non-browser callers.* The "bot protections on by default" posture is fine for browser-driven traffic but breaks every machine-to-machine integration (Tailscale WebFinger, OIDC, ActivityPub, RSS, ACME — anything HTTP that isn't Chrome).
- *Per-path WAF skip is the right granularity.* Globally disabling bot protection is the unblock; production posture is "default protected, narrowly skipped where protocol-public endpoints exist."
- *WAF/security rules belong in Terraform.* Manual UI configuration drifts; the `terraform/cloudflare/` module already exists and should grow to include zone-level security configuration.

---

