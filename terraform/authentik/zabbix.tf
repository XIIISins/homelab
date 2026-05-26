# terraform/authentik/zabbix.tf
#
# Authentik SAML provider + Application for Zabbix (Hugin, LXC 1102).
# Group gate: members of `zabbix-admins` (set via users.yaml) can
# authenticate; Zabbix-side JIT provisioning maps them into the built-in
# `Zabbix administrators` usergroup.
#
# Native SAML 2.0, NOT Traefik ForwardAuth — Zabbix 7.0 has built-in
# SAML support, and the "K3s-down emergency observability" failure-
# domain rationale rules out making the auth path K3s-dependent. See
# decisions.md "Zabbix UI auth — native SAML, not Traefik ForwardAuth".
#
# Auth flow:
#   1. User browses hugin.midgard or hugin.xiiisins.com → Traefik
#   2. Zabbix login page → "Sign in with SAML" → AuthnRequest signed
#      with Zabbix's SP private key (ansible/zabbix/saml-sp-keypair)
#   3. Browser redirected to Authentik SAML SSO URL
#   4. Authentik authenticates (if not already) + policy-binding check
#      against zabbix-admins → SAML Response signed with Authentik's
#      default signing keypair
#   5. Browser POSTs response to Zabbix's ACS endpoint (/index_sso.php?acs)
#   6. Zabbix verifies signature against IdP cert, extracts NameID (email)
#      + username + groups attrs, JIT-provisions into Zabbix usergroup
#
# SP keypair sourcing: operator generates ONCE with
#   openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
#     -subj '/CN=hugin.midgard.xiiisins.com' -keyout sp.key -out sp.crt
# and stores in Vault at secret/ansible/zabbix/saml-sp-keypair with
# fields `key` (PEM private) + `cert` (PEM public). TF reads only the
# public cert (registers it in Authentik for AuthnRequest sig verify);
# the Ansible role reads both halves to write them on the LXC.

# -----------------------------------------------------------------------------
# SAML property mapping data sources — Authentik's built-in defaults.
# These ship with Authentik; reference by `managed` slug to keep the
# binding to the well-known managed objects (more stable than `name`,
# which has localised variants).
# -----------------------------------------------------------------------------

# NameID = user.email — matches the design's "NameID = email" decision.
# Zabbix's userdirectory_saml.username_attribute will pull NameID into
# the `username` field, and JIT provisioning uses the email as the
# unique identifier.
data "authentik_property_mapping_provider_saml" "email" {
  managed = "goauthentik.io/providers/saml/email"
}

# Username (uPN-shape) attribute. Sent alongside NameID so Zabbix has
# both — useful if we later split NameID from username (e.g. opaque
# NameIDs with email-on-the-side).
data "authentik_property_mapping_provider_saml" "name" {
  managed = "goauthentik.io/providers/saml/name"
}

# Groups attribute (list of Authentik group names the user is in).
# The Zabbix userdirectory_saml.group_name attribute will read this
# claim and dispatch into the right Zabbix usergroup via the
# group→usergroup mapping declared in the Ansible role.
data "authentik_property_mapping_provider_saml" "groups" {
  managed = "goauthentik.io/providers/saml/groups"
}

# UPN — User Principal Name. Provides a stable username-like claim
# distinct from email. Authentik's default UPN mapping yields the
# user's username. We include it so Zabbix's username_attribute can
# pick from a known SAML attr without relying on NameID parsing.
data "authentik_property_mapping_provider_saml" "upn" {
  managed = "goauthentik.io/providers/saml/upn"
}

# -----------------------------------------------------------------------------
# SP certificate registration in Authentik
#
# Zabbix signs AuthnRequests with its SP private key. Authentik needs
# the SP's PUBLIC cert to verify those signatures (the verification_kp
# attribute on authentik_provider_saml). The cert is operator-minted
# off-band (see header comment) and stashed in Vault; TF reads the
# public half + registers it as an Authentik certificate object.
# -----------------------------------------------------------------------------

data "vault_kv_secret_v2" "zabbix_sp_keypair" {
  mount = "secret"
  name  = "ansible/zabbix/saml-sp-keypair"
}

# Cert-only registration in Authentik. `key_data` is NOT set — Authentik
# only verifies SP signatures (it doesn't sign anything WITH the SP
# key). Keeping the private key out of Authentik's data plane matches
# least-privilege: the private key lives in Vault + on the Zabbix LXC,
# nowhere else.
resource "authentik_certificate_key_pair" "zabbix_sp" {
  name             = "Zabbix SP (hugin)"
  certificate_data = data.vault_kv_secret_v2.zabbix_sp_keypair.data["cert"]
}

# -----------------------------------------------------------------------------
# SAML Provider
# -----------------------------------------------------------------------------

# ACS URL on Zabbix's frontend. The `acs` query-string trigger is what
# Zabbix's index_sso.php expects (built into the frontend, not
# operator-configurable).
#
# IMPORTANT: authentik_provider_saml supports a SINGLE acs_url, not a
# list. Zabbix's frontend bakes the current request's Host header into
# the AuthnRequest's AssertionConsumerServiceURL — so SAML logins only
# work cleanly from the hostname pinned here. The apex
# (hugin.xiiisins.com) is the canonical choice because:
#   * Public WAN clients land there via cloudflared tunnel directly.
#   * LAN clients can also resolve it (DNS resolves through public
#     Cloudflare → tunnel → Traefik backchannel), so a LAN user hitting
#     hugin.xiiisins.com works end-to-end too.
#   * Picking the midgard alias would make the public WAN path break
#     (Authentik would reject the apex ACS).
#
# The midgard alias (hugin.midgard.xiiisins.com) still works for
# already-authenticated browsing IF session cookies are portable across
# hostnames — but Zabbix session cookies bind to hostname by default,
# so in practice users land back at the canonical apex hostname for
# every fresh login. Bookmark accordingly.
resource "authentik_provider_saml" "zabbix" {
  name = "Zabbix"

  # ACS — the SP endpoint where Authentik POSTs the SAML Response.
  # Zabbix's frontend wires this internally at /index_sso.php?acs.
  acs_url = "https://hugin.xiiisins.com/index_sso.php?acs"

  # Audience = SP entityID. Zabbix defaults to its base URL; pinning
  # explicitly here avoids ambiguity across the multi-FQDN setup.
  # Whichever URL is canonical for the SP, Authentik's audience
  # restriction in the SAML assertion will be set to this value, and
  # Zabbix will reject responses whose audience doesn't match.
  audience = "https://hugin.xiiisins.com"

  # Issuer = IdP entityID. Authentik's default issuer pattern is
  # `https://<authentik-host>/`. Pin explicitly so the role doesn't
  # have to introspect Authentik's URL — gets baked into the Vault
  # IdP-info blob.
  issuer = "https://authentik.midgard.xiiisins.com/"

  # POST binding — Zabbix supports both POST and Redirect for the SP
  # side. POST avoids the URL-length limits that Redirect imposes on
  # large assertions (group lists in particular).
  sp_binding = "post"

  # Signing — Authentik signs the SAML Response with the default
  # self-signed cert (same one we use for OIDC token signing). Zabbix
  # validates against the cert PEM we hand it via Vault.
  signing_kp = data.authentik_certificate_key_pair.default.id

  # Verification — Authentik verifies AuthnRequest signatures from
  # Zabbix using the SP cert we registered above.
  verification_kp = authentik_certificate_key_pair.zabbix_sp.id

  # Flows — match the OIDC patterns from tailscale.tf / outline.tf.
  # `authorization_flow` is required; `invalidation_flow` is used for
  # SLO (single logout). `authentication_flow` falls back to the
  # default-authentication-flow when null (which is what we want).
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id

  # NameID = email. Zabbix's userdirectory_saml.username_attribute
  # consumes NameID (or a named attribute — we'll wire username
  # explicitly via the UPN attribute on the Zabbix side, but NameID
  # stays as email for the canonical user identifier).
  name_id_mapping = data.authentik_property_mapping_provider_saml.email.id

  # Property mappings — these are the attributes Authentik will
  # include in the SAML Response. Zabbix reads them out by name to
  # populate user record fields + group membership.
  property_mappings = [
    data.authentik_property_mapping_provider_saml.email.id,
    data.authentik_property_mapping_provider_saml.name.id,
    data.authentik_property_mapping_provider_saml.upn.id,
    data.authentik_property_mapping_provider_saml.groups.id,
  ]
}

# -----------------------------------------------------------------------------
# Application
# -----------------------------------------------------------------------------

# Zabbix is exposed at both midgard (LAN) and apex (WAN via
# cloudflared). meta_launch_url points at the apex so the user portal
# tile sends off-LAN users to the public-reachable hostname; on-LAN
# users hit midgard directly via AGH rewrite, never seeing the launcher.
resource "authentik_application" "zabbix" {
  name               = "Zabbix"
  slug               = "zabbix"
  protocol_provider  = authentik_provider_saml.zabbix.id
  meta_launch_url    = "https://hugin.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "zabbix_admins_gate" {
  target = authentik_application.zabbix.uuid
  group  = authentik_group.this["zabbix-admins"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# TF → Vault hand-off
#
# Writes the IdP-side info the Ansible role needs to wire SAML in
# Zabbix's userdirectory_saml record. Reading from Authentik's
# computed/exported attributes (URLs are read-back from the API after
# provider creation) means a single source of truth — if the provider
# is recreated with a new PK, the URLs update + the role re-reads them
# on next play.
#
# Cert PEM is pulled from the same default Authentik signing cert
# (data.authentik_certificate_key_pair.default.certificate_data) used
# elsewhere — Zabbix uses it to verify SAML Response signatures.
#
# Vault path: ansible/zabbix/saml-idp. Consumer-domain prefix is
# `ansible/` because the consumer is the Ansible role running on the
# LXC, not a K8s workload. (Design doc previously sketched
# `k8s/zabbix/saml-idp` — that's a path-convention drift; corrected
# here + in docs/services/zabbix.md to follow the consumer-domain rule.)
# -----------------------------------------------------------------------------

resource "vault_kv_secret_v2" "zabbix_saml_idp" {
  mount = "secret"
  name  = "ansible/zabbix/saml-idp"
  data_json = jsonencode({
    # IdP entity ID — Zabbix's userdirectory_saml.idp_entityid.
    entity_id = authentik_provider_saml.zabbix.issuer

    # SSO URL (Redirect binding) — where Zabbix sends users for sign-in.
    # Authentik exposes Redirect + POST variants; Redirect is what
    # Zabbix's "Sign in with SAML" button hits to start the round-trip.
    sso_url = authentik_provider_saml.zabbix.url_sso_redirect

    # SLO URL (Redirect binding) — where Zabbix sends users for
    # single-logout. Even if we don't surface a logout button initially,
    # capturing the URL keeps userdirectory_saml fully populated.
    slo_url = authentik_provider_saml.zabbix.url_slo_redirect

    # IdP signing cert — Zabbix's userdirectory_saml.idp_certificate.
    # PEM format, including BEGIN/END markers. Zabbix validates every
    # SAML Response's signature against this.
    signing_cert = data.authentik_certificate_key_pair.default.certificate_data
  })
}
