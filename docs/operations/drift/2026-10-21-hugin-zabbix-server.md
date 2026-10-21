<!-- docs/operations/drift/2026-10-21-hugin-zabbix-server.md -->

DRAFT: agent-written, operator to edit

# Drift note: `hugin` `zabbix-server : Enable + start zabbix-server, nginx, php-fpm, zabbix-agent2` in `asgard-drift-check` task 4056 (2026-10-21)

## What the run reported

Source: Semaphore task 4056 (`semaphore.tasks {"task_id": 4056}`), status `error`, started 2026-10-21T12:15:01Z, ended
2026-10-21T12:23:49Z, repo commit `466242f1`. The wrapper runs `site.yml` in `--check` mode (per earlier notes in this directory), so
"changed" means "would change".

- One changed task: `TASK [zabbix-server : Enable + start zabbix-server, nginx, php-fpm, zabbix-agent2]` on `hugin`, line
  `changed: [hugin] => (item=nginx)`. Only the `nginx` loop item changed. The `diff` list is **empty**, so the difference is not visible.
- Recap for `hugin`: `ok=49 changed=1 unreachable=0 failed=0 skipped=40`. Every other host in the returned recap shows `changed=0`.
- `localhost` shows `ok=2 changed=0 failed=1`, which is why the task status is `error`. The call returned no failing task text for it, so
  I do not know what failed. The recap has 20 entries (alphabetical, ending at `mimir`) so it may be truncated; I did not establish this.
- The task is in `ansible/roles/zabbix-server/tasks/services.yml`: `ansible.builtin.systemd` with `enabled: true`, `state: started`, looping over
  `zabbix-server`, `nginx`, the pinned php-fpm service and `zabbix-agent2`.

## Likely cause (hypothesis)

- The run's commit `466242f` (#307) is a docs commit by its title. `git log` on `ansible/roles/zabbix-server/tasks/services.yml` lists only
  `466242f`, and `git show --stat` of it lists the whole tree (it is the first commit in this clone's history), so it does not
  isolate a role change. No named commit explains the drift.
- **Hypothesis**: `nginx` on `hugin` was not enabled or not running at check time (for example stopped or restarted by hand, or mid-reload),
  and the other three units were fine. The empty diff means I cannot tell enabled-state from running-state. Incident #111 was reported as
  diagnosed in the diagnoses channel; I could not read that diagnosis, so I do not rely on it.
- Live check: `zabbix.problems {"host":"hugin"}` returned `HTTP 502 zabbix unreachable`, so I have no live evidence about `hugin`.

## Resolution

Not resolved as far as I can tell. This note changes no code. No later run was read, so it is unknown whether the next scheduled check is clean.

## Follow-ups

- Operator: on `hugin`, check `systemctl is-enabled nginx` and `systemctl is-active nginx`, and the journal for a restart near 12:15-12:23Z on
  2026-10-21. If nginx is healthy, check whether the next drift run is clean.
- Operator: find why `localhost` failed in task 4056 (full task log in Semaphore); that is separate from the `hugin` drift.
- Operator: link incident #111's diagnosis here if it names a cause, and decide whether the `hugin` Gatus probes of `:80` (commit `8dddeb5`)
  saw nginx down at that time (not checked).
- If a role fix is wanted, it needs a separate `drift` request stating the direction (repo or live).
