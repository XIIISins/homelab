<!-- docs/incidents/2026-05-24-cloudflare-reharden.md -->

### 2026-05-24 — Cloudflare re-harden after 5e.3.c global-disable

Closed the standing "Cloudflare re-harden after stability" task. New `terraform/cloudflare/security.tf` imports the zone's auto-created `http_request_firewall_custom` entrypoint ruleset and lands a single skip rule for protocol-public endpoints, then re-enables `browser_check` + Bot Fight Mode. Two-phase apply via `--target` to ensure skip rules are live before protections flip (no race window where Tailscale-style probes could 403).

End-to-end mechanical validation: WebFinger probe returns 200 even with `Go-http-client` UA (Tailscale-shape worst case), confirming the skip rule beats Bot Fight Mode + Browser Integrity Check. `/application/o/*` skip is verified by rule contents + API readback; live external probe deferred to next real consumer flow (Authentik was in PG-DNS-flapping restart loop at validation time — all three `authentik-server` pods 0/1 Ready, see open task).

**Findings:**
- *Cloudflare API token scopes: `Bot Management:Edit` is a separate Free-plan permission, not folded under `Zone Settings:Edit`.* Initial token extension added Zone Settings:Edit + WAF:Edit on the assumption Bot Fight Mode (the only Free-plan bot toggle) would ride on the Zone Settings scope. Wrong — `cloudflare_bot_management` apply errored until the dedicated `Zone:Bot Management:Edit` permission was added. Gotcha added.
- *Cloudflare ruleset import ID in provider v5 requires the `zones/` discriminator prefix.* Provider v4 accepted `<zone_id>/<ruleset_id>`; v5 demands `zones/<zone_id>/<ruleset_id>` (or `accounts/<account_id>/<ruleset_id>` for account-scoped). Error message is explicit. Gotcha added.
- *Authentik OIDC discovery lives per-app at `/application/o/<slug>/.well-known/openid-configuration`, NOT at host root.* Initial skip rule expression defensively included `authentik.xiiisins.com/.well-known/openid-configuration`; that path 404s (not a real endpoint). Tightened to drop the dead pattern. The real OIDC discovery URL Tailscale fetches is built from the WebFinger `links.href` → `<issuer>/.well-known/openid-configuration` → resolves under `/application/o/<slug>/.well-known/openid-configuration`, covered by the `/application/o/*` prefix match. Gotcha added.
- *Pre-existing zone `http_request_firewall_custom` entrypoint was non-empty.* Found a disabled+broken geofencing rule from before the rebuild — expression used `or` where intent was `and` (would have matched 100% of traffic if enabled). Cleanly dropped via the import → in-place update.

---

