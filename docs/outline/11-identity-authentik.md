# Identity — Authentik & SSSD

Authentik is the single identity provider for everything — infrastructure SSH access via LDAP + SSSD, and web application SSO via OIDC. One user, one password, one place to manage access.

## Authentik providers

```
Authentik (single source of truth)
  │
  ├── LDAP provider
  │     ├── SSSD on Proxmox nodes    → SSH login + sudo
  │     ├── SSSD on LXCs             → SSH login + sudo
  │     ├── SSSD on K3s VMs          → SSH login + sudo
  │     ├── AWX                      → AWX user authentication
  │     └── Grafana                  → Grafana user authentication
  │
  └── OIDC provider
        ├── Outline                  → SSO login
        ├── Immich                   → SSO login
        ├── n8n                      → SSO login
        ├── Jellyfin                 → SSO login
        └── All other web apps       → SSO login
```

## SSSD configuration

SSSD is installed on every managed host by Ansible. It queries Authentik's LDAP provider for user authentication and group membership.

Key configuration:
- `cache_credentials = true` — users can log in even if Authentik is temporarily unreachable, using cached credentials
- Group membership in Authentik controls sudo access — being in the `homelab-admins` group grants passwordless sudo
- The `ansible` and `recovery` accounts bypass SSSD entirely — they are local accounts

## OIDC applications

Every web application that supports OIDC/OAuth2 uses Authentik as its identity provider. Users log in once via Authentik and are redirected back to the application with a token. No per-application user management.

Applications configured with Authentik OIDC:
- Outline, Immich, n8n, Jellyfin, Homepage, AWX, Grafana, Vault UI, phpIPAM (via LDAP)

## Authentik deployment

Runs in K3s as part of the can-run tier. Uses PostgreSQL LXC as its database backend.

**The dependency concern:** Authentik is in K3s, but SSSD on your infrastructure hosts depends on it. If K3s is down, can you still SSH in?

Yes — two safety nets:
1. `cache_credentials = true` in SSSD — recently authenticated sessions continue to work
2. The `recovery` local account completely bypasses SSSD — always available regardless of Authentik state

## User lifecycle

Add a user → create in Authentik → user can immediately SSH into any host and log into any OIDC app.

Remove a user → disable in Authentik → SSH access revoked on next SSSD cache refresh (minutes), OIDC sessions expire on next token refresh.

No per-host user management. No per-app user management.
