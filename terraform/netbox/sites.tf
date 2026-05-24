# terraform/netbox/sites.tf
#
# Single site `home` — the physical location of the homelab. The
# minimal-model decision from Phase 5i scope keeps this flat: no
# region, no rack, no location. A future retrofit can introduce a
# rack ("shelf-1") if the rack model becomes useful.

resource "netbox_site" "home" {
  name   = "home"
  slug   = "home"
  status = "active"
}

import {
  to = netbox_site.home
  id = "1"
}
