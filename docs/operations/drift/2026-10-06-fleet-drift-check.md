<!-- docs/operations/drift/2026-10-06-fleet-drift-check.md -->

DRAFT: agent-written, operator to edit

# Drift note: fleet, `asgard-drift-check` task 2169 (2026-10-06)

## What the run reported

Source: Semaphore task 2169 (`semaphore.tasks {"task_id": 2169}`), status `success`, started 2026-10-06T12:15:01Z, ended
2026-10-06T12:24:53Z, repo commit `6856ddc2`. As in [the 2026-10-05 note](2026-10-05-fleet-hardening.md), the run is a `--check` pass, so "changed" means "would change" (not re-verified here).

Changed tasks (all from the same call):

- `hardening : Configure login banner` on `fulla`, `idunn`, `vor`, `eir`, `snotra`, `hlin`, `hermod`, `gna`, `hugin`, `frigg`. Only `eir` and `snotra` returned a diff: in `/etc/issue.net`, `* All activity is logged and monitored.` would become `* All activity is logged, monitored and audited.`. The other hosts' diffs were **empty**.
- `proxmox-host : Let the zabbix agent read this boot's kernel journal, and only that` on `skuld`, `verd`, `urd`. The diff shows a new `/etc/sudoers.d/zabbix-igpu-journal` (file did not exist before) with `zabbix ALL=(root) NOPASSWD: /usr/bin/journalctl -k -b --no-pager`.
- `proxmox-host : Install the iGPU hang-count UserParameter` on `skuld`, `verd`, `urd`. The diff shows a new `/etc/zabbix/zabbix_agent2.d/proxmox-igpu.conf` defining `UserParameter=pve.igpu.hang.count`.
- `vlagent : Flush handlers` on `skuld`, `verd`, `urd` (empty diff).
- `frigg` only: `aiops-toolbelt : Install the unit`, `aiops-toolbelt : Flush handlers so changed code/unit is live before the checks`, `aiops-author : Install the drafting agent's definition from the repo ...`, `aiops-author : Flush handlers ...` (empty diffs), plus the banner task. That makes 5 changed on `frigg`, matching the request.
- Per-host counts in the request (eir=1, frigg=5, skuld=3, urd=3, verd=3, others 1) match the changed lines above.
- Recap: `frigg` `ok=144 changed=5`; `eir`, `fulla`, `gna`, `hermod`, `hlin`, `hugin`, `idunn` `changed=1`. The recap has 20 entries: it lists `einherjar-skuld/urd/verd` with `changed=0` (the changed lines name `skuld`, `verd`, `urd`), and has no entries for `snotra`, `vor`, `urd`, `verd`, `skuld` under those names. I did not establish why. No host has `failed` or `unreachable` above 0.

## Likely cause (hypothesis)

- **Banner**: `ansible/roles/hardening/tasks/main.yml` line 111 holds the new wording. `git.log` for that role shows `947d24b` (2026-10-04, "drift: Banner wording (#149)") as the latest change. **Hypothesis**: the same cause as the 2026-10-05 note; the repo was changed and `hardening` was not applied since, so the old text is still live. The two visible diffs fit; for hosts with empty diffs it is unconfirmed. The drift persists one day after the previous note.
- **proxmox-host iGPU items**: `git.log` for `ansible/roles/proxmox-host` shows `a61fc6d` (2026-10-06, "Zabbix templates for the QSV smoke test, media mount and iGPU hangs (J6) (#212)") as the latest change, and the diffs add files that did not exist. **Hypothesis**: #212 added these files to the role and they have not been applied to the three PVE hosts yet. I did not read the commit's file list.
- **Run commit** `6856ddc` (#214, `git.show`): touches only `ansible/roles/jellyfin/tasks/install.yml` and `media-mount.yml`. It does not explain any changed task above. The newer commit `cdb0054` (#215, plantnet proxy) is not the run's commit.
- **vlagent flush handlers** on the PVE hosts: **hypothesis**, a handler fired because the `proxmox-host` change above ran in the same pass; not verified.
- **frigg aiops-toolbelt / aiops-author**: `git.log` for `ansible/roles/aiops-author` shows `374e071` (2026-10-04, #144) as the latest change. Diffs were empty, so which unit or definition would change is not visible. Not explained.

## Resolution

Not resolved, as far as I can tell. I saw no later run and no apply. This note changes no code.

## Follow-ups

- Operator: decide whether to apply `hardening` fleet-wide (the intended state after #149), and `proxmox-host` on `skuld`, `verd`, `urd` (intended state after #212).
- Operator: read `/etc/issue.net` on a host with an empty diff to confirm the cause is the old wording.
- Operator: inspect what would change in the `aiops-toolbelt` and `aiops-author` units on `frigg` before a deploy (a toolbelt deploy restarts services).
- Operator: the request says incident #50 was diagnosed in the diagnoses channel. I did not read it (no tool for it); compare it with this note.
- Operator: the recap naming and omissions (`einherjar-*`, missing `snotra`/`vor`) are worth checking in how the tool summarizes the run.
