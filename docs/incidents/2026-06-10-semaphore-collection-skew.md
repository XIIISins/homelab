<!-- docs/incidents/2026-06-10-semaphore-collection-skew.md -->
# 2026-06-10 — Semaphore ran image-bundled collections, not the requirements.yml pins

**Phase:** post-5h.3 (Semaphore operational) · **Trigger:** `asgard-apply` failing on the `zabbix-agent : Register host in Zabbix` task from Semaphore (tasks 255 / 258), while the same task passed from Frigg.

## Summary

The `zabbix-agent` `register-host` task (which uses `community.zabbix` 4.x's httpapi auth pattern) failed every time it ran from Semaphore but succeeded from the MacBook/Frigg controllers. Root cause: **Semaphore executed the stock image's bundled `community.zabbix 2.3.1`, never the pinned 4.2.0** — it does not run `ansible-galaxy collection install`, and the repo's vendored `ansible/collections/` is gitignored, so the `ansible/requirements.yml` pins never reached the Semaphore runtime. Fixed by a custom GHCR image that bakes the pinned collections into the ansible venv's site-packages. The fix took two rounds: the first attempt targeted the wrong resolution channel and produced a **false-positive verification**.

This is also the homelab's **first GHCR-hosted custom image** — the `docker/<name>/Dockerfile` + GitHub Actions → `ghcr.io/xiiisins/<name>` pattern is now established and reusable. (The repo was made public during this work; see findings 7–8.)

## Findings

1. **Semaphore runs image-bundled collections, not the pins.** The stock `semaphoreui/semaphore:<ver>-ansible<ver>` image ships its own Galaxy set; Semaphore never galaxy-installs `ansible/requirements.yml`, and the repo's vendored `ansible/collections/` is gitignored (`.gitignore:32`) so the checkout doesn't carry them either. Every pinned collection was older at runtime: zabbix **2.3.1 vs 4.2.0**, postgresql **3.4.0 vs 4.2.0**, crypto **2.18.0 vs 3.2.1**, netbox 3.17.0 vs 3.22.0. The 2.x↔4.x `community.zabbix` auth-model change is what made it visible. **A pin is only real where it's actually installed.**

2. **"Transient" was the wrong first call.** Initial diagnosis leaned transient (the Zabbix host record was already fully correct, the audit log showed zero mutation that day, single-host failure). But a deterministic "passes on controller A, fails on controller B" is a **controller difference**, not a blip. Lesson: when the same task passes from one runner and fails from another, diff the runners before reaching for "transient" — same correct end-state + single-host failure can still be deterministic environmental skew.

3. **`no_log: true` hid the error; forensics + a controlled repro recovered it without leaking.** The register task carries `no_log` (token in `vars`). Worked around it via (a) Zabbix audit-log / live-state comparison to rule out config problems, then (b) an in-pod repro of the exact module call with the token fed via **stdin into a pod file** (never argv/chat/VL) — proving 4.2.0 succeeds and isolating version as the cause.

4. **r1 fixed the wrong channel and verified falsely.** First attempt installed the collections to `/usr/share/ansible/collections` + set a pod-spec `ANSIBLE_COLLECTIONS_PATH`. `kubectl exec` resolved 4.2.0 — but **`kubectl exec` inherits the pod env, and a Semaphore task does not**: Semaphore builds the task subprocess env itself (it injects `ANSIBLE_HASHI_VAULT_*` etc.) and the task runs under the checkout's `collections_path = collections` (an empty vendored dir). So the real task still resolved the bundled 2.3.1 via Ansible's import-finder, and r1 still failed. **Lesson: verify via the real execution path, not a proxy that happens to have a different environment.** `kubectl exec` env ≠ Semaphore task env.

5. **r2 fixed the right channel: the ansible venv site-packages.** The only collection source a Semaphore task sees is Ansible's import-based finder scanning `ansible_collections` on `sys.path` — i.e. the ansible venv's site-packages. Installing the pins there with `--force` (overwriting the bundle) makes Semaphore resolve 4.2.0 exactly as the MacBook/Frigg do (whose venv site-packages already hold 4.2.0). No `ansible.cfg` edit, no env var. Verified the **task-context finder** (`_AnsibleCollectionFinder` installed with the configured `COLLECTIONS_PATHS`, from the checkout dir, env unset) resolves 4.2.0/4.2.0/3.2.1, then confirmed the live register task as a no-op success via a real Semaphore run.

6. **Diagnosing collection resolution: don't trust `ansible-galaxy collection list` or a raw `python import`.** Both bypass Ansible's finder — `list` enumerates every path informationally (it showed the venv bundle *first*), and a bare `import ansible_collections...` falls back to plain `sys.path`. The authoritative check is to install the finder with the real `COLLECTIONS_PATHS` and inspect the resolved module's `__file__`.

7. **Semaphore task env ≠ pod env (generalised).** Config that must reach a task goes via the checkout's `ansible.cfg`, the Semaphore **Environment** object, or (for collections) the venv site-packages — never a pod-spec env var. Also: this Semaphore project has `allow_override_args_in_task = false`, so the task-create API's `limit` param is **silently ignored** — a scoped-to-one-host verification run actually executed the full fleet apply.

8. **Repo went public mid-work.** Secret-handling became high-stakes: every push secret-scanned, git history scanned clean (external "V12" scan), and the `ansible-vault` files (now publicly downloadable) rest on a 32-char passphrase. SealedSecrets are public-safe by design. The CLAUDE.md `Repo: private GitHub` invariant was corrected as part of this work.

## Resolution

- `docker/semaphore/Dockerfile` + `.github/workflows/build-semaphore-image.yml` → `ghcr.io/xiiisins/semaphore-homelab:<base>-rN` (public package; Flux pulls without a secret).
- `k8s/asgard/apps/semaphore/statefulset.yaml` points at the image, drops the pod-start `pip install` (baked) — no env var, no ansible.cfg change.
- **Bump discipline:** when `ansible/requirements.yml` or the base image moves, bump `BASE_IMAGE` (Dockerfile) + `IMAGE_TAG` rN (workflow) + the StatefulSet tag in one commit.

Full rule + diagnostic: CLAUDE.md "Known gotchas → Semaphore". Design: [`docs/architecture/ansible-orchestration.md`](../architecture/ansible-orchestration.md) "Custom image".
