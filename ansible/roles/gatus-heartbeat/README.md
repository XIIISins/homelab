# gatus-heartbeat

Frigg's half of the Gatus dead-man's switch: a hardened systemd timer (every 60 s) that POSTs to the watcher's external-endpoint API over the tailnet with a bearer token from Vault `secret/ansible/do1/gatus-heartbeat` (`token`; operator-seeded, role fails early if absent). Target address/key come from the watcher host's group vars (`group_vars/offsite.yml`). Run via `playbooks/gatus-heartbeat.yml`; procedure in `docs/procedures/offsite-watcher.md`.
