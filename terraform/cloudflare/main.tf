# Smoke test for 5e.2.a: resolve the xiiisins.com zone.
# Confirms the provider authenticates and the token has the expected
# zone-scoped permissions (Zone:DNS:Edit + Zone:Zone:Read).
#
# Tunnel + DNS resources land in 5e.2.d.
data "cloudflare_zone" "xiiisins" {
  filter = {
    name = "xiiisins.com"
  }
}
