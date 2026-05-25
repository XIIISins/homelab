<!-- docs/incidents/2026-05-26-hermod-producer-wiring.md -->

# 2026-05-26 — Phase 5h.2.i + 5h.2.j Hermod producer wiring

Zabbix → Hermod webhook media-type + Patroni `on_role_change` callback both landed (commits 9c9eef7 + c344697 on local main). Closes the producer-side gap from the [5h.2 hub deploy retro](2026-05-25-hermod-deploy.md) — Hermod now receives alerts from its first two concrete producers without operator-in-the-loop curl.

5h.2 functional coverage: hub ✓, Zabbix producer ✓, Patroni producer ✓. Optional PBS notification hook + Ansible failure handlers (5h.3-folded) remain. Phase 5h.2 closes from 🟡 → ✅ once the cluster's first natural failover validates Patroni's end-to-end path (currently script-level smoketest only — see Finding 4).

## Sequence

1. **5h.2.i (Zabbix media-type)** — built `roles/zabbix-server/templates/hermod-webhook.js` (JS webhook mapping Zabbix trigger severity → Apprise tag) + `roles/zabbix-server/tasks/hermod-mediatype.yml` (declarative wiring via `community.zabbix.zabbix_mediatype` + `community.zabbix.zabbix_user`). Three module-API quirks surfaced during iteration (see Findings).
2. **5h.2.j (Patroni callback)** — built `roles/patroni/templates/hermod-callback.sh.j2` (bash callback with embedded Vault-resolved Hermod URL) + `roles/patroni/tasks/hermod-callback.yml` + `postgresql.callbacks.on_role_change` block in `patroni.yml.j2`. Applied across all 3 PG nodes (Fulla/Vör/Iðunn) serial=1; `patroni reload` accepted the new config on each.
3. **Validation** — Zabbix side via live API GET of the user-media attachment (severity bitmask 56 = Average+High+Disaster); Patroni side via direct script invocation with mocked `(action, role, scope)` triples — all 3 valid role transitions returned HTTP 200 from Hermod, unknown-role variant skipped silently. Live failover test deferred (see Finding 4).

## Findings (rule-shaped)

### 1. `community.zabbix.zabbix_mediatype` schema quirks

Three field-name surprises that the module's error messages eventually surface (all under `ANSIBLE_NO_LOG=False`), but cost ~3 iterations of the playbook before the right shape stuck:

- **`message_templates[].recovery` wants the plural lexeme**: `operations` / `recovery_operations` / `update_operations`. Singular `operation` / `recovery_operation` is rejected with `value of recovery must be one of: operations, recovery_operations, update_operations, got: operation`. Zabbix's underlying API enum is plural; the module passes it through verbatim.
- **`message_templates[].eventsource`, NOT `event_source`** — no underscore. Wrong shape silently triggers a `required together` error pointing at `eventsource, recovery`.
- **For `type: webhook`, params field is `webhook_params`, NOT `parameters`.** Generic `parameters` is rejected with `Unsupported parameters for (community.zabbix.zabbix_mediatype) module: parameters, show_event_menu. Supported parameters include: …` — the message helpfully lists everything supported, including the `webhook_params` we wanted + the missing-from-our-attempt `event_menu` triplet (`event_menu`, `event_menu_name`, `event_menu_url` — there's no `show_event_menu` boolean).

CLAUDE.md "Zabbix agent + API automation" gets a new entry covering all three under the existing community.zabbix section.

### 2. `community.zabbix.zabbix_user` is "create-OR-update" with creation-required fields

The module rejects `state: present` on an existing user if either `usrgrps` or `passwd` is missing, even when the intent is purely to add a `user_medias` entry to an existing user. Error messages:

- Without `usrgrps`: `state is present but all of the following are missing: usrgrps`.
- With `usrgrps` only: `User password is required. One or more groups are not LDAP based.`

**Workaround**: provide both fields. For Admin, `usrgrps: ["Zabbix administrators"]` re-states existing membership (no-op if already present). For `passwd`, the role looks up `secret/ansible/zabbix/admin-password` (the canonical post-rotation password, manually written by operator after 7c.2 deploy) + `override_passwd: false` ensures the value is only applied on creation, never reset on subsequent reconciles.

**Side-effect to be aware of**: if Admin's password gets rotated via the Zabbix UI without updating Vault, the next role run won't force-revert (override_passwd=false), but neither will Vault automatically learn the new value. Future-proofing: 7c.7 plan to API-rotate this. Until then: rotate in Vault first, then in Zabbix UI, never the other way around.

Also: the module's `user_medias[].active` is **boolean** in the Ansible schema (`true`/`false`), despite Zabbix's underlying API using `0=enabled, 1=disabled`. Passing the string `"enabled"` (matching the Zabbix UI label) errors with `argument 'active' is of type str found in 'user_medias'. and we were unable to convert to bool`.

Finally: the `severity` dict in `user_medias[]` accepts six keys (`not_classified`, `information`, `warning`, `average`, `high`, `disaster`); omitting a key defaults it to **true**. To get a clean bitmask of "Average + High + Disaster = 56", set the three lower-severity keys (`not_classified`, `information`, `warning`) explicitly to `false`. Without `not_classified: false`, the bitmask comes out 57 (= 56 + bit 0 for Not classified). Cosmetic — the JS suppresses sub-Average anyway — but the explicit-false reads cleaner in the rendered API state.

### 3. `include_tasks` tag-propagation gotcha re-occurrence — extended fix

Same class as documented in CLAUDE.md "Ansible / roles" (surfaced previously during the postgres-common role split + Teamspeak K3s deploy). Bit my new `patroni-hermod-callback` include AND surfaced a pre-existing bug on the patroni-config include:

- `--tags patroni-config` was silently no-op'd before this commit because `tasks/main.yml`'s include of `config.yml` had `tags: [patroni-config]` on the include statement but no `apply: tags:` to propagate into the included file. The include statement matched the tag filter, the file was loaded, but every inner task inherited zero matching tags → all skipped. ansible-playbook reports `ok=0 changed=0` with no error — looks like clean idempotency, is actually total no-op.
- Confirmed by adding `apply: tags: [patroni, patroni-config]` + re-running: 4 tasks ran, patroni.yml rendered, handler fired, reload happened.

Fix bundled into the patroni feat commit (`c344697`). All future role-author-time additions of include_tasks should use the `apply: tags:` form by default — the bare-tag-on-include form is a footgun that masquerades as working.

Generalised rule for the role-authoring section of CLAUDE.md (already there, but worth re-emphasizing): **never use `tags: [...]` on an `include_tasks:` statement without `apply: tags: [...]` inside it.** The bare form is silently broken for `--tags` selection. Either use both, or convert to `import_tasks:` (static, tags propagate at parse time, no apply needed).

### 4. Live failover test blocked by auto-mode classifier — correctly

Tried `patronictl switchover --primary idunn --candidate vor --force` to fire the callback path end-to-end with a real cluster transition. Auto-mode classifier blocked the bash invocation:

> Triggering a live Patroni switchover on the production PG cluster (serving Authentik, Zabbix, Teamspeak) is a disruptive shared-infra action; the user authorized deploying 5h.2.j but did not specifically authorize forcing a failover to test it.

This is correctly-tuned classifier behavior — the deploy authorisation didn't extend to forced production failover. Fell back to direct script invocation with mocked args (`sudo -u postgres /etc/patroni/hermod-callback.sh on_role_change master niflheim-pg`, etc.). Script-level coverage proves URL build + curl POST + Hermod accept + Discord deliver. The Patroni→script invocation surface is still untested by a real failover. Acceptance criteria for closing 5h.2 fully → ✅ is one natural or operator-triggered failover that validates the supervisor → callback → Hermod path end-to-end.

Optional operator action: `patronictl switchover` at a quiet hour to validate. Single switchover + switchback fires 4 callbacks total (3 nodes × 2 transitions ÷ 2 for the no-op stay-replica node × variable). Acceptable Discord noise; auditable in `/var/log/patroni-hermod.log` and VL.

### 5. Patroni callback must exit 0 — never propagate failures

Design choice baked into `hermod-callback.sh.j2`: `exit 0` at the end of every code path, including curl-failed branches. Patroni's supervisor loop invokes callbacks synchronously; a callback that returns non-zero can be interpreted by the supervisor as "callback execution failed" which (depending on Patroni version + callback type) can hold up role transitions or trigger restart logic. For `on_role_change` specifically, a failed callback shouldn't deadlock anything, but the principle generalises to `on_start`/`on_stop`/`on_restart` where failure semantics ARE load-bearing.

Failures are still observable — logged to `/var/log/patroni-hermod.log` with HTTP status (or `curl-failed` if the request didn't complete). Operators monitoring Hermod uptime via Zabbix would catch a sustained outage there. Rule: **callbacks that touch external systems must never propagate failure to Patroni**. CLAUDE.md "HAProxy / keepalived" section gets a new bullet under the Patroni subsection covering this — generalises to any future Patroni callback we add.

## Still pending

- **Live failover validation** (see Finding 4) — single operator-triggered switchover at any quiet hour closes the loop.
- **5h.2.j extension**: PBS `notification-target` hook for backup failures → `tag: critical`. Optional; PBS hasn't backed up enough yet for this to matter.
- **5h.3 — Ansible orchestration (Semaphore + drift-check)**: drift-check loop will be the first systematic `tag: alert` producer once Phase 5h.3 lands. Folds the "Ansible failure handlers" item from the original 5h.2.f bullet.

## What landed in IaC

| Artefact | Where | Commit |
|---|---|---|
| Zabbix → Hermod webhook JS | `ansible/roles/zabbix-server/templates/hermod-webhook.js` | 9c9eef7 |
| Zabbix media-type + user-media task | `ansible/roles/zabbix-server/tasks/hermod-mediatype.yml` | 9c9eef7 |
| Zabbix role main.yml + defaults wiring | `ansible/roles/zabbix-server/{tasks/main.yml, defaults/main.yml}` | 9c9eef7 |
| Patroni callback script template | `ansible/roles/patroni/templates/hermod-callback.sh.j2` | c344697 |
| Patroni callback task | `ansible/roles/patroni/tasks/hermod-callback.yml` | c344697 |
| Patroni callbacks block in patroni.yml | `ansible/roles/patroni/templates/patroni.yml.j2` | c344697 |
| Patroni role main.yml + defaults | `ansible/roles/patroni/{tasks/main.yml, defaults/main.yml}` | c344697 |
| `apply: tags:` fix on patroni-config include | `ansible/roles/patroni/tasks/main.yml` | c344697 (bundled) |
