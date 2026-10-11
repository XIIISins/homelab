<!-- docs/outline/services-and-purpose/hermod.md -->

# Hermod

The notification hub: the single place every alert-producing system sends to, which fans alerts out to the right Discord channel by tag. Named for the messenger of the gods. When Postgres fails over or Zabbix trips a trigger, this is how the operator finds out.

---

## Where it runs

LXC 1103 on Verd (`10.0.11.22`), outside Kubernetes, so alerts about K3s being down still fire. It is two things:

- **AppriseAPI**, installed natively (a Python venv under gunicorn and systemd, no Docker), does the dispatch, bound to localhost.
- **Caddy** on port 80 in front, with a source-IP allowlist as the real access gate. The config key in the URL path is only a soft second factor, since it appears in access logs.

It is stateless and internal-only: a single LXC with no HA, because if it is down, notifications are not delivered but the producers' failures still land in the log store.

## The tag taxonomy

The tag chooses the channel; severity only sets the embed colour. Definitions come from how fast someone should respond.

| Tag | Response | Channel |
|---|---|---|
| `critical` | Within minutes, even at night: quorum lost, service hard-down | **Hrist**, with `@everyone` |
| `alert` | Within hours: degraded but up, drift detected | **Mist** |
| `info` | Never needs a response; the cap for non-production and canary signals | **Randgrid** |
| `media` | Media-stack events | **Ölrún** |
| *(untagged)* | A producer bug | **Hel**, a quarantine so nothing vanishes |

Routine successes (a clean drift check) are not sent: they are log lines.

## Who sends

- **Zabbix**, through a webhook media type on its standard "report problems" action.
- **Patroni**, through an `on_role_change` callback on every Postgres node, so a promotion or demotion posts within seconds.
- **Semaphore**, for drift, failed applies, health-check findings and restore-test results.
- **The AIOps components.**

Sonarr is the one deliberate exception: it posts to the media webhook directly because it cannot produce Apprise's payload.

---

## See also

- **Observability** (Components): the full alerting picture.
- **Storage & data** (Components): the Patroni failover events.
- **Semaphore** (this section): the drift alerts.
