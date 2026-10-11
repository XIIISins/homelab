<!-- docs/outline/diagrams/notifications.md -->

# Notifications diagram

Hermod fans alerts out to Discord by tag. Producers post one JSON shape; severity colours the message and the tag picks the channel.

![Notifications: Hermod fans alerts out to Discord by tag](/api/attachments.redirect?id=9bfb4361-8d09-4eff-baba-ba60067580cb)

---

## How to read it

- **Left: producers.** Zabbix (Disaster and High become critical, Average becomes alert), the Patroni callback (a leader change is an alert, lost replicas are critical), Semaphore and Ansible (apply or drift failures) and the `infra-health-check` script (certificates, quorum, PBS, tokens).
- **Middle: the Hermod LXC.** Stateless, with no HA. Caddy allows only listed source addresses (anything else gets 403) and writes JSON access logs. AppriseAPI takes `POST /notify/<config-key>` and routes **by tag, never by severity**; every routed URL carries an explicit tag.
- **Right: channels.** `critical` goes to `#infra-critical` (look within minutes, `@everyone`). `alert` goes to `#infra-alerts` (look within hours, no mention). `info` goes to `#infra-info`, where canary and non-prod alerts are capped. `media` goes to `#media`. A message with no tag lands in `#hermod-untagged`, a quarantine that means a producer has a bug.
- **Sonarr** keeps its own Discord connection to `#media` instead of going through Hermod.
- **Below: the supporting pieces.** Vault holds one webhook URL per tag plus the config key, and Ansible renders them onto the LXC. vlagent ships Hermod's logs to VictoriaLogs; routine success is a log line, never a notification.
- **Why an LXC, not a pod:** alerts about K3s being down still fire.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[notifications.excalidraw 49583](/api/attachments.redirect?id=c7013806-5269-42ab-8d55-0a5fb2fa5ec7)

[notifications.svg 61238](/api/attachments.redirect?id=59a58930-e9b5-4067-9561-fe79c67f560c)

---

## See also

- **Hermod** (Services): the service page and the tag taxonomy.
- **Observability** (Components): the full alerting picture.
- **Zabbix** (Services): the main producer.
