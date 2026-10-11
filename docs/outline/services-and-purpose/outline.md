<!-- docs/outline/services-and-purpose/outline.md -->

# Outline

The wiki you are reading. It holds the operational notebook: how-tos, how-to-use-the-homelab pages for family, and notes that are still evolving. The git-tracked `docs/` tree is the other half: it records how the homelab is *built* (decisions, services, incidents, runbooks).

![Outline: the operational wiki, fed by every infra layer](/api/attachments.redirect?id=61b4afbb-cfd5-43f8-82af-7612f5594ff7)

---

## These pages come from Git

The pages in the `Homelab` collection are generated from Markdown in the repository (`docs/outline/`) by a sync script, which updates or creates pages through Outline's API. Edit the Markdown, run the sync, and the wiki converges. A page edited only in Outline is not reverted until its source changes, so edit at the source.

---

## What it is wired to

Outline is a showcase of an app that touches every layer.

| Need | Provided by |
|---|---|
| Database | The shared Postgres cluster, via the HAProxy VIP |
| File attachments | **Garage** S3 (bucket and key provisioned by Terraform, credentials in Vault) |
| Queues and collaboration state | A small Redis on emptyDir; all of it is transient and recoverable from Postgres |
| Sign-in | Authentik OIDC, restricted to the `outline-users` group |
| Public access | `wiki.xiiisins.com` through the Cloudflare tunnel; `wiki.midgard.xiiisins.com` and `wiki.niflheim.xiiisins.com` are internal names for the same instance |

The deployment uses the **Recreate** strategy because the container runs its database migrations at start-up, and two replicas rolling at once would race for the migration lock.

The container image comes from Docker Hub, not GitHub's registry, which refuses anonymous pulls.

---

## Secrets

Four Vault paths feed it (app keys, database password, S3 credentials, OIDC client). The Redis secret is separate from the app's, so Redis can start before S3 and OIDC exist; the app pod waits in a visible "secret not synced" state until Terraform has provisioned the rest.

---

## See also

- **Storage & data** (Components): Garage and the Postgres cluster.
- **Identity & secrets** (Components): the OIDC flow and where the secrets live.
- **Edge** (Components): the three hostnames.
- **Outline diagram** (Diagrams): the three hostnames and everything behind them.
