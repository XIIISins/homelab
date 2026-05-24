<!-- docs/incidents/2026-05-23-authentik-pg-dns-flapping.md -->

### 2026-05-23 — Authentik PG DNS resolution flapping recurrence (58 restarts in 24h)

While diagnosing the HTTPRoute issue above, surfaced a separate latent issue: `authentik-server-86cf768686-7h4z6` showed **58 restarts in 24h** (other Authentik pods: 5 and 4 restarts in same window). Last 50 lines of `--previous` logs showed the pattern: pod boots → loops on `PostgreSQL connection failed, retrying... (failed to resolve host 'fulla.niflheim.xiiisins.com': [Errno -2] Name or service not known)` for ~30 seconds → gunicorn dies → pod restarts → next pod tries → same loop. Pods that ARE running successfully resolved the same name on their own boot — the failures are intermittent, not consistent.

This is the **cached-NXDOMAIN class** identified during the original 2026-05-17 Authentik deploy and documented in the DNS-fallback-resolver decision row. CoreDNS or pod-internal resolver caches NXDOMAIN from a brief moment where the K3s node queried `1.1.1.1` (public resolver) via secondary fallback; cached NXDOMAIN survives the AdGuard primary becoming reachable again. Cache eventually expires, next boot succeeds, but the failed boots accumulate as restart counts.

**Not blocking 5e.3 progress** — Authentik UI works, OIDC works, the pods that ARE up serve traffic correctly. But 58 restarts is *operationally* unhealthy. Needs a real root-cause investigation, not just "retry until it works."

Hypotheses to investigate (logged as pending task):
1. **CoreDNS NXDOMAIN cache TTL** — what TTL is CoreDNS using, and is it longer than the AGH-restart-recovery window?
2. **Pod resolver `options ndots`** — Authentik pods may be issuing queries with `ndots:5` and falling through to `1.1.1.1` on transient AGH unresponsiveness.
3. **AGH sync interval `*/1` recent change** — was Saga briefly unresponsive during a sync? Could explain timing-correlated failures.
4. **K3s node `/etc/resolv.conf` fallback** — if K3s nodes still have a public resolver as secondary in their host `resolv.conf`, that's the upstream path CoreDNS would consult.

Related to but distinct from the 2026-05-17 Authentik deploy finding — that one was a one-time hit during the deploy itself; this is a recurring class that's been firing intermittently for 24+ hours.

