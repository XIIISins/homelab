<!-- docs/incidents/2026-05-17-evening-authentik-redis.md -->

### 2026-05-17 evening — Authentik + Redis deploy

Phase 5e. First real K8s workload consuming the post-rebuild infrastructure. Took ~5 hours start to functional; ~half of that was Authentik-specific learning, the other half was infrastructure gaps the deploy surfaced.

**What landed in IaC:**
- `k8s/asgard/infrastructure/authentik/` — namespace, helmrepository (`charts.goauthentik.io`, pinned `2026.2.3`), externalsecret (5 keys from `secret/k8s/authentik/*` + 1 for blueprint user), helmrelease (3 server, 1 worker, external PG pointed at Fulla, embedded media volume as emptyDir, branding via configMap+populator init pattern preserved for future S3 swap), redis.yaml (hand-rolled StatefulSet, AOF persistence, 1Gi iSCSI PVC, chown init container), kustomization with `configMapGenerator` for blueprints.
- `k8s/asgard/infrastructure/authentik/blueprints/00-brand.yaml` — demotes shipped `authentik-default` brand, claims default for `authentik.niflheim.xiiisins.com`, wires the three default flows.
- `k8s/asgard/infrastructure/authentik/blueprints/01-users.yaml` — personal admin user, password sourced from Vault via `!Env AUTHENTIK_BLUEPRINT_USER_PASSWORD`.
- `ansible/roles/baseline/` — adds `/etc/resolv.conf` management + cloud-init `manage_resolv_conf: false` drop-in. Closes the DNS-fallback poisoning class.
- `terraform/proxmox/asgard-k3s/main.tf` — all three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → 2vCPU/4GB symmetrically.
- `k8s/asgard/infrastructure/sealed-secrets/kustomization.yaml` — added `namespace.yaml` to resources (was missing post-migration, parent reconcile broke until found).
- `k8s/asgard/infrastructure/kustomization.yaml` — parent migrated to sub-kustomization-only references.

**Findings, in rough chronological order of discovery:**

1. **Parent kustomization migration gap.** Migrating the parent `infrastructure/kustomization.yaml` to reference sub-directories (forced by Authentik's `configMapGenerator`) surfaced that `sealed-secrets/kustomization.yaml` was missing `namespace.yaml` from its resources. Worked previously because of accumulated state — `install.createNamespace: true` had created the ns as a side-effect on the original deploy. Same class as the asgard rebuild's findings.

2. **`prometheus.serviceMonitor` deprecated key** in chart 2026.2.3. Fail-fast deprecation guard in `templates/deprectations.yaml`. Lesson: when you set a values block to its default value for "documentation," you risk the key name moving and breaking install. Going forward, set only non-defaults.

3. **Authentik PG client connects to localhost when `authentik.postgresql.host` is set in values block but `AUTHENTIK_POSTGRESQL__HOST` env is unset.** The env var wins; the values block doesn't backfill it. Set all PG connection params explicitly in the ExternalSecret. Same rule for Redis. (CLAUDE.md gotcha.)

4. **CoreDNS cached NXDOMAIN poisoning.** Authentik worker couldn't resolve `fulla.niflheim.xiiisins.com` despite the same name resolving from `kubectl run dnstest` busybox. Traced: cloud-init had left `nameserver 1.1.1.1` as secondary on every K3s node; during a brief primary-resolver hiccup, libresolv fell back to Cloudflare, which returned NXDOMAIN for the internal zone; CoreDNS cached the NXDOMAIN and served it to long-lived consumers for the 30s TTL — but ongoing fallbacks kept the cache poisoned indefinitely. Fixed at the right layer: `roles/baseline` now manages resolv.conf via Ansible (cloud-init's `manage_resolv_conf: false`), only UCG as nameserver, no public-resolver fallback. CoreDNS restart purged the cache. (CLAUDE.md gotcha.)

5. **Postgres `hostssl`-only rejects plaintext with "no pg_hba.conf entry" — same error as CIDR mismatch.** The "no encryption" suffix is the diagnostic clue. Authentik's libpq default `sslmode=prefer` was being clobbered by something to plaintext. Fix: explicit `AUTHENTIK_POSTGRESQL__SSLMODE=require` in ExternalSecret. (CLAUDE.md gotcha.)

6. **Authentik brand `default: true` is mutually exclusive.** Blueprint must demote shipped `authentik-default` brand first. Single-entry blueprint fails with `Only a single brand can be set as default.` Two-entry blueprint (demote + claim) works, applies in document order. (CLAUDE.md gotcha.)

7. **`authentik_tenants.tenant` is wrong for single-instance deploys.** Was including the model entry by analogy with the brand. Tenants are for multi-schema Postgres isolation; on a single-tenant deploy the brand is the only object you need. Removed the tenant entry, renamed the blueprint file to `00-brand.yaml`.

8. **All three CPs under-spec'd.** Earlier doc claimed gondul was bumped during the rebuild; Terraform was actually unchanged. Authentik's first-deploy burst — Django migrations + blueprint reconciliation + 3 server pods starting + ESO sync — pushed hlokk into kernel-panic-adjacent I/O thrash, which dragged etcd quorum down via fsync contention, which made kubectl unresponsive cluster-wide. Recovery via systemd-driven k3s restart on the failed CP. **Fixed properly:** all three CPs bumped to 2vCPU/4GB symmetrically via Terraform locals, sequential reboots restored quorum cleanly.

9. **Workload concentration on einherjar-skuld.** 5 of 7 stateful PVCs landed on `.23` due to iSCSI session pinning persisting across rebuilds. Load 1.84 vs 0.34 on peers. Structural issue, not hot bug — flagged for the pending CP-taint work (gets CSI node-plugin off CPs) and possibly explicit StatefulSet pod anti-affinity. (CLAUDE.md gotcha.)

10. **HelmRelease remediation thrash masks the real failure.** Default `install.remediation.retries: 3` runs `helm uninstall` between retries, and ESO-managed Secrets' finalizers leave the uninstall stuck on Secret-termination, so by the time you go look at the failure, the original error is gone and the visible state is the cleanup's failure. Set `retries: -1` and `timeout: 15m` during first-deploy debugging. Restored to `retries: 3` after success. (CLAUDE.md gotcha.)

11. **`getaddrinfo` failures in Python on otherwise-resolving hosts** look identical to NSS library issues, broken `resolv.conf`, or glibc dual-stack bugs. The actual diagnostic that cut through it: raw DNS over UDP to CoreDNS at `10.43.0.10` showing `rcode=NXDOMAIN` for one name and `NOERROR` for another from the same pod. Once the answer was wire-level, the search narrowed to CoreDNS upstream rather than client-side. Worth documenting the technique — fastest path to root cause when DNS feels weird.

**Plus:** Sub-kustomization-per-component pattern formalized (closes the open design doc question). The branding-via-populator-init-container pattern was preserved as a forward-fit even though branding is currently empty — flipping to S3-sourced is a one-edit change later.

**Validation:** Phase 1 (PG DB provisioning) validated the `postgres_databases` per-service iteration pattern against a real consumer. Phase 3 deploy validated three classes of Postgres pattern (per-service DB, TLS-required, scram-sha-256) through Authentik's PG client. Standalone-Fulla-first was the right call — three PG-related findings surfaced cleanly through a real consumer rather than synthetic load.

**Resolution:** Authentik functional, niflheim brand is default, personal admin user exists with admin group membership. Bare-LB at `10.0.20.12`. Traefik + cert-manager next (Phase 5e.1, scheduled 2026-05-18) to put it behind HTTPS.

**Root-cause patterns:**
- Accumulated state in the live cluster but not in Git: sealed-secrets namespace, cloud-init's `1.1.1.1` fallback, undersized CPs. Same pattern as the morning's asgard rebuild — the cluster ran fine in steady state, then a new workload's burst load fired the latent gaps. Real consumers beat synthetic load every time.
- Chart values blocks vs env vars: when both exist, env vars win silently. Document-by-default (the chart's values.yaml) lies about which knob is actually live. ExternalSecret env vars are the authoritative path for service config going forward.
- DNS NXDOMAIN caching: a single bad answer from a fallback resolver poisons a service for the cache TTL. The fix is architectural (no public fallbacks for hosts that need internal resolution), not tactical (cache-flush + hope).

---

