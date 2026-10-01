<!-- aiops/README.md -->

# aiops/ — machine-readable ops (Phase 10c)

*Stage 0 of [`docs/operations/aiops-roadmap.md`](../docs/operations/aiops-roadmap.md): the data the diagnosis session (10d), the approval-gated executor (10e) and the autonomous T1 loop (10f) consume. Software and docs only: nothing here touches a live system. Decision rows: [`decisions.md`](../docs/operations/decisions.md) ("AIOps machine-readable layer").*

**Why a top-level `aiops/` and not `docs/`:** these are inputs to code (a webhook bridge, an executor, CI), not prose; they carry schemas, fixtures and a linter, and `docs/` is Markdown the owner reads. The prose stays in `docs/` (known-issues, procedures) and is *referenced* from here. It sits next to `terraform/`, `ansible/` and `k8s/` as a first-class repo area.

## Layout

| Path | What |
|---|---|
| [`schema/alert.v1.schema.json`](schema/alert.v1.schema.json) | The normalized alert (10c1). Critical alerts must carry a `runbook_id`; the fingerprint is recomputable |
| [`alert-mapping.md`](alert-mapping.md) | Per-source mapping: Zabbix, S4 prober, Patroni, Semaphore, Frigg, anything else via Hermod |
| [`alert-routing.yml`](alert-routing.yml) | Ordered match rules: message -> `host`/`service`/`check`/`runbook_id`; every source ends in a catch-all |
| [`tools/normalize.py`](tools/normalize.py) | Reference normalizer (Hermod wire payload -> alerts), pure functions |
| [`fixtures/cases/`](fixtures/cases/), [`fixtures/invalid/`](fixtures/invalid/) | Worked examples (raw wire + expected alerts) and must-fail cases |
| [`runbooks.yml`](runbooks.yml) | Runbook sidecar metadata (10c2): `runbook_id`, tier, `automatable`, preconditions, verify, selection rationale |
| [`actions.yml`](actions.yml) | Action registry (10c3): named action -> Semaphore template, typed extra-vars, tier, guard, verify, rollback |
| [`schema/runbooks.v1.schema.json`](schema/runbooks.v1.schema.json), [`schema/actions.v1.schema.json`](schema/actions.v1.schema.json), [`schema/routing.v1.schema.json`](schema/routing.v1.schema.json) | JSON Schemas for the three YAML files |
| [`tools/lint.py`](tools/lint.py), [`tests/test_aiops.py`](tests/test_aiops.py) | Cross-file consistency linter and its tests (the linter is itself tested against deliberately broken inputs) |
| [`requirements.txt`](requirements.txt) | `PyYAML` + `jsonschema`, pinned; everything else is stdlib |

Playbooks for the registry actions live where the repo already keeps them (`ansible/playbooks/aiops-*.yml`) and their Semaphore templates in `terraform/semaphore/templates.tf`; the linter checks every registry reference against both.

## Run it

```bash
pip install -r aiops/requirements.txt
python3 aiops/tools/lint.py                          # schemas + cross-file consistency + fixtures
python3 -m unittest discover -s aiops/tests -v       # linter negative tests + normalizer behaviour
python3 aiops/tools/normalize.py --received-at 2026-10-01T12:00:00Z < payload.json
```

CI runs the first two as the `aiops` job (path-filtered into the single `CI gate`, see [`procedures/ci.md`](../docs/procedures/ci.md)).

## Runbook markers

The Markdown under `docs/known-issues/`, `docs/procedures/` and `docs/services/` keeps its format. Each documented runbook carries one invisible marker directly above its bullet or heading:

```
<!-- runbook: RB-FLUX-HR-STALLED -->
- **A `Stalled=True / RetriesExceeded` HelmRelease ignores a plain `flux reconcile` ...
```

`runbooks.yml` is keyed by that id. The linter fails if a marker is missing, duplicated, in the wrong file, or orphaned (a marker with no entry). To add a runbook: add the marker above the entry, add the sidecar entry, run the linter. Defaults are deliberately conservative: `automatable: none` unless a registry action exists and its replay safety is shown; nothing is `auto`; nothing T3 is above `none` (both enforced).

## Adding an action

1. Add the entry to `actions.yml` (tier, typed `extra_vars`, `guard`, `verify`, `rollback`; mutators start at `max_autonomy: approval`).
2. Add the playbook (`ansible/playbooks/aiops-<name>.yml`): re-enforce the guard inside the playbook (read it from `actions.yml`), emit an `AIOPS_RESULT {...}` line, and keep T0 actions read-only.
3. Add the template to `terraform/semaphore/templates.tf` (name and `playbook` must match the registry). Apply is an operator step from the main checkout; then set `semaphore.applied: true`.
4. If a runbook should use it, list it under `diagnostics` (T0 only) or `remediation`.

## What 10d needs from here

The bridge calls `normalize()` on each Hermod POST, looks up `runbook_id` in `runbooks.yml`, hands the diagnosis session the alert + the runbook's `preconditions` + the doc pointed to by `source`, and lists the registry actions the session may *propose*. Open questions for 10d are in [`open-questions.md`](../docs/operations/open-questions.md) ("Phase 10c follow-ups").
