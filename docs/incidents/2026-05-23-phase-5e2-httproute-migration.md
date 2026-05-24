<!-- docs/incidents/2026-05-23-phase-5e2-httproute-migration.md -->

### 2026-05-23 — Phase 5e.2 sub-phase ordering: HTTPRoute migration skipped, surfaced 12h later

During Phase 5e.2 close-out (sub-phases `5e.2.f`–`5e.2.j`), the Authentik HTTPRoute migration step (`5e.2.g` — attach to midgard Gateway with new hostnames) was effectively skipped. The validation step (`5e.2.i`) reported "all green" but only verified WebFinger (which has its own apex-static route) — the external Authentik check would have failed had it actually run. Cleanup (`5e.2.j`) ran on top of the unmigrated state, removing the `authentik.niflheim.xiiisins.com` AdGuard rewrite and leaving the HTTPRoute *still attached to the niflheim Gateway with the old hostname*.

Symptom didn't surface until ~12h later, at the start of `5e.3.b` Terraform work, when the Authentik provider couldn't reach `https://authentik.xiiisins.com/api/v3/...` — every request 404'd. After ruling out Cloudflare/tunnel/cert issues, `kubectl get httproute -n authentik` showed `HOSTNAMES: ["authentik.niflheim.xiiisins.com"]` — never updated. Internal access via `authentik.midgard.xiiisins.com` also 404'd because Traefik had no route matching that hostname (AdGuard rewrite existed but rewrote to nothing).

Fix was straightforward: edit `httproute.yaml` to attach to midgard Gateway across both `websecure-midgard` and `websecure-apex-wildcard` listeners, with hostnames `authentik.midgard.xiiisins.com` + `authentik.xiiisins.com`. Reconcile, immediate green on both internal and external paths.

**Root cause:** validation step trusted "looks green" rather than actually exercising the external Authentik path. The validation curls listed in `5e.2.i` included WebFinger but the Authentik external check was either skipped or its 404 misread as expected.

**Findings:**
- *Validation steps must EXERCISE the thing they claim to validate.* WebFinger 200 doesn't prove Authentik 200; they're separate routes. Each external endpoint claimed in a phase's "done" needs its own concrete curl test that actually hits THAT endpoint and verifies a non-error response. "Validated" with no captured output is not validated.
- *Cleanup sub-phases that depend on prior migration sub-phases should fail loudly if the migration didn't happen.* `5e.2.j` removed the old `niflheim` rewrite without verifying the migrated route was live. A precondition check before cleanup (e.g. `kubectl get httproute -n authentik -o json | jq -e '.items[0].spec.parentRefs[].name == "midgard"'`) would have caught the missed migration.
- *Sub-phase plans should include explicit validation gates between dependent steps.* The `5e.2.g → h → i → j` chain has implicit dependencies; making them explicit (validation must pass before cleanup can run) is cheap and catches this class of error.

