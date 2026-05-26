<!-- ansible/roles/vlagent/README.md -->

# vlagent

Installs + configures vlagent (the VictoriaLogs project's log shipper) as a systemd binary. Pairs with the K8s DaemonSet at [`k8s/asgard/infrastructure/victoria-logs-collector/`](../../../k8s/asgard/infrastructure/victoria-logs-collector/) — DaemonSet handles K3s pod logs, this role handles everything that isn't a K3s pod (LXCs, K3s VM host journald, Proxmox hosts).

Phase 7 prep. Picked over Vector + Fluent Bit per the 2026 upstream benchmark: 4-10× lower CPU + no file-rotation correctness issues + single-vendor pairing with VL.

## What this role does

1. Installs `ca-certificates` + `curl` + `tar` (apt).
2. Creates `/etc/vlagent/`, `/var/lib/vlagent/`, `/var/log/vlagent/`.
3. Downloads the upstream `vlutils-linux-amd64-<version>.tar.gz` from the VictoriaLogs releases page.
4. Extracts the `vlagent-prod` binary to `/usr/local/bin/`, symlinks to `vlagent` for unit-file portability.
5. Renders `/etc/vlagent/vlagent.yaml` from template — list of file paths to tail + their per-input fields.
6. Installs + starts `vlagent.service` (systemd, runs as root for `/var/log/*` read + `/var/log/pve/*` read on Proxmox hosts).

## Ingest target

Off-cluster shippers reach VL via the HTTPRoute on the niflheim Gateway:

```
https://logs.niflheim.xiiisins.com/insert/jsonline
```

Path `/insert/*` is unauthenticated (the auth split on the HTTPRoute per [`k8s/asgard/apps/victorialogs/httproute.yaml`](../../../k8s/asgard/apps/victorialogs/httproute.yaml)). UI access at the same hostname's `/` path requires Authentik via Traefik ForwardAuth.

For K3s pod logs, the in-cluster DaemonSet uses Service DNS directly — that's a separate concern, this role doesn't touch K3s pods.

## Variables

See [`defaults/main.yml`](defaults/main.yml). The most-tuned per-host:

- `vlagent_log_inputs` — list of `{path, service[, format]}` entries. The defaults cover universal Debian/RHEL syslog. Extend per-host or per-group for service-specific logs:

  ```yaml
  # group_vars/postgres.yml
  vlagent_log_inputs:
    - path: /var/log/syslog
      service: syslog
    - path: /var/log/postgresql/postgresql-*.log
      service: postgresql
    - path: /var/log/patroni/patroni.log
      service: patroni
  ```

- `vlagent_host_label` — defaults to `inventory_hostname`. Override if your inventory hostname doesn't match what you want to see in VL's `host` field.

## Why text syslog instead of journald native

vlagent's native journald collection is upstream-pending ([VictoriaLogs issue #1274](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1274)). Bridge for now: rsyslog routes journald → `/var/log/syslog` (Debian) or `/var/log/messages` (RHEL), vlagent's filelog input tails the text file. Loses the structured journald metadata (priority, unit, etc.) — acceptable tradeoff vs running a separate systemd-journal-upload daemon. Revisit when issue #1274 lands.

## See also

- [`k8s/asgard/infrastructure/victoria-logs-collector/`](../../../k8s/asgard/infrastructure/victoria-logs-collector/) — K3s pod-log DaemonSet (pairs with this role)
- [`docs/services/observability.md`](../../../docs/services/observability.md) — full Phase 7 stack overview
- CLAUDE.md "Architectural invariants → Services / placement"
