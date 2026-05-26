<!-- docs/incidents/2026-05-26-zabbix-saml-deploy.md -->

# Zabbix SAML SSO deploy — Phase 8c.3–8c.6 (2026-05-26)

End-to-end first deploy of Authentik native SAML 2.0 against Zabbix 7.0 LTS on Hugin (LXC 1102). Procedure: [`docs/procedures/zabbix-saml-deploy.md`](../procedures/zabbix-saml-deploy.md). Outcome: ✅ all three documented validation paths work; 7 first-deploy findings landed as fix commits + 1 architectural limitation re-confirmed (midgard alias unusable for SAML, by design).

## What landed

- **Authentik**: SAML provider + Application + policy binding gated on new `zabbix-admins` group, SP cert registered, signing keypair attached, ACS URL pinned to apex (`https://hugin.xiiisins.com/index_sso.php?acs`). 6 added + 1 changed in `terraform/authentik/`.
- **Cloudflare**: `hugin.xiiisins.com` CNAME → tunnel UUID. 1 added.
- **AdGuard**: `hugin.midgard.xiiisins.com` rewrite → Traefik VIP `10.0.20.10`. 1 added (LAN/midgard rewrite kept despite SAML limitation — see below).
- **Flux/K8s**: `cloudflared` ConfigMap entry for `hugin.xiiisins.com` ingress (rollout-restart required, subPath-mount gotcha); `k8s/asgard/apps/zabbix-ingress/` namespace + Service + EndpointSlice (10.0.11.21:80) + HTTPRoute (`hugin.midgard.xiiisins.com` + `hugin.xiiisins.com`) on the midgard Gateway.
- **Hugin (Ansible)**: 3 cert PEMs at `/usr/share/zabbix/conf/certs/{sp.key,sp.crt,idp.crt}` (root:www-data 0640), `userdirectory_saml` row "Authentik SAML" in DB, global SAML auth enabled with JIT provisioning + `disabled_usrgroup` parked at Zabbix's built-in `Disabled` group.

## Findings (7 fix commits, all post-flight on main)

1. **`5ef72f1` — `provision_groups[].role` (not `role_name`).** `community.zabbix.zabbix_user_directory` (v4.x) expects `role` per provision_groups entry; the draft used `role_name`. First fail: "missing required arguments: role found in provision_groups."

2. **`4d70db4` — SAML fields flat at module level (not nested in `saml:`).** community.zabbix 4.2.0 flattens SAML fields directly onto `zabbix_user_directory`; the draft nested them. Second fail: "Unsupported parameters for ... module: saml."

3. **`62ea3e8` — `disabled_usrgroup` required when JIT enabled.** `community.zabbix.zabbix_authentication` enforces `disabled_usrgroup` whenever `saml_jit_status` (or `ldap_jit_status`) is true. Third fail: "'disabled_usrgroup' must be provided if 'ldap_jit_status' or 'saml_jit_status' enabled." Pinned to Zabbix's built-in `Disabled` usergroup (no access by default).

4. **`71abdbd` — `web-config.yml` template-render tasks need explicit `zabbix:saml` tag.** Cert PEMs were written by `saml.yml`, but `zabbix.conf.php` (rendered by `web-config.yml`) still had the SAML lines commented out — the `--tags zabbix:saml` invocation skipped `web-config.yml` entirely because those tasks had no tags. Tagged the conf.php render with `zabbix:saml` + `zabbix:saml-conf`.

5. **`2ab39d0` — nginx `X-Forwarded-Proto` → `HTTPS` reflection for ACS URL scheme.** Zabbix's PHP frontend built `http://...` ACS URLs because nginx (behind Traefik) saw plain HTTP from upstream and didn't reflect the original scheme. Fix in `nginx.conf.j2`: derive `$forwarded_https` from `$http_x_forwarded_proto`, pass `fastcgi_param HTTPS $forwarded_https`. Backdoor (no XFF) stays `off` — correct for the plaintext path. Also tagged the nginx render with `zabbix:saml`.

6. **`89f205d` — `sign_response = true` on `authentik_provider_saml`.** Authentik signs only the inner Assertion by default; the outer Response is unsigned. Zabbix's `sign_messages: true` (in `userdirectory_saml`) requires the Response signed too. Error: "Response is not signed and SP requires signed response." TF resource gained explicit `sign_response = true`.

7. **`2e0bcce` — `username_attribute` must match an actually-attached property mapping.** Role default was `http://schemas.goauthentik.io/2021/02/saml/uid`, but the TF provider only attached email/name/upn/groups property mappings — the `uid` mapping was never emitted. Switched to UPN's actual saml_name `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn`; UPN's Authentik expression falls back to user.email when no UPN is set, giving stable email-rooted Zabbix usernames consistent with NameID = email.

## Mid-deploy TF-quirk surfaced

**`authentik_provider_saml` computed URL attributes return literal `"-"` until `terraform refresh`.** Initial apply landed `sso_url = "-"` and `slo_url = "-"` in Vault → Zabbix DB → login errored with `idp_sso_url_invalid, idp_slo_url_invalid`. Workaround: `terraform apply -refresh-only` after the initial apply re-reads the provider's URL attrs (which become populated once the Application bind is fully realised IdP-side), then a regular apply propagates the real URLs into Vault. NOT a fix commit — a procedural footnote: any future `authentik_provider_saml` resource needs a refresh-then-apply chain on first deploy.

**Computed URLs use the default-brand apex hostname, not the `issuer` value.** Authentik returned `https://authentik.xiiisins.com/application/saml/zabbix/...` for `url_sso_redirect`, even though `issuer = https://authentik.midgard.xiiisins.com/`. Both hostnames reach the same backend (CF tunnel + AGH rewrite), and the SAML Response's `Issuer` claim still uses the TF-hardcoded `issuer` value (which Zabbix's `idp_entityid` matches), so the cross-hostname shape works end-to-end — but worth noting for any future provider whose URL emission gets sensitive to the resolved hostname.

## Architectural limitation re-confirmed

`authentik_provider_saml.acs_url` is a single string, not a list. SAML logins must start from the canonical apex hostname (`hugin.xiiisins.com`). LAN clients hitting `hugin.midgard.xiiisins.com` fail with "ACS URL ... doesn't match Provider ACS URL." This was documented in advance — see `terraform/authentik/zabbix.tf:103-120` comment + procedure §5 Path B — and confirmed at deploy time as expected behavior, not a bug.

The midgard alias remains live (AGH rewrite + HTTPRoute) for:
- The Zabbix-direct backdoor scenario (Internal auth from `hugin-direct.niflheim`, separate hostname)
- Non-SAML browsing once authenticated via apex (though session cookies are hostname-bound, so it doesn't extend the apex session in practice)

Operator decision (2026-05-26): keep as-is, document the limitation. Future workaround options if it becomes friction: (a) drop midgard alias entirely, (b) Traefik redirect midgard→apex.

## 3-path validation results

| Path | Hostname | Result |
|------|----------|--------|
| A — WAN SAML (off-LAN) | `https://hugin.xiiisins.com` | ✅ (mobile cellular) |
| B — LAN SAML (canonical, apex) | `https://hugin.xiiisins.com` from LAN | ✅ |
| C — Backdoor | `http://hugin-direct.niflheim.xiiisins.com` | ✅ (path resolves to Zabbix login over plain HTTP; full Internal `Admin` auth not retested but operator confirmed works pre-deploy) |
| (not a documented path) LAN → midgard | `https://hugin.midgard.xiiisins.com` | ❌ by design (single-ACS limitation) |

## Process notes

- **Mutating-ops cadence under "I drive deploy"**: classifier flagged the Authentik apply as too eager (chained from rebase+merge without re-asking). Future: confirm before each production-DNS / Authentik / Vault-write apply, even when the operator has authorized the overall sequence. Re-asked + got "proceed with the full push" → ran CF+AGH+push+Ansible in chain after that.
- **Worktree discipline held**: every code fix went through `EnterWorktree` → commit → ff-merge → `ExitWorktree`. 8 worktrees created + cleaned during this deploy. None of the fix branches survived past their merge.
- **Schema-shape findings dominated**: 4 of 7 fixes (1, 2, 3, 7) were "I had the wrong shape for an external API I didn't first-line-verify against the actual module/provider version." The draft predates the live community.zabbix install, so a `ansible-doc community.zabbix.zabbix_user_directory` pre-flight would have caught #1, #2, #3 in one pass. #7 needed Authentik metadata introspection, harder to pre-flight without the IdP already up.
- **`no_log: true` made all 7 failures debugging-hostile.** Per the existing Zabbix gotcha "no_log: true blanks both stdout and stderr": failures had to be re-derived from error messages alone, with no module context. For this class of work (declarative API calls against a documented schema), `no_log: false` on a first-deploy pass + a re-run with `no_log: true` once verified would have shaved iterations.

## What stays open

- Procedure §6 post-validation tightening: disable Zabbix sign-up in UI to force SSO-only for non-Admin users. Deferred — operator decision when ready.
- Procedure §0 still describes the rebase as a hands-on operator step; the actual deploy proved it ff-merges fine post-rebase. Minor doc drift.
