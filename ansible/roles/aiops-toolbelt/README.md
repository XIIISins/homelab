# aiops-toolbelt

The Toolbelt API (Phase 10d2) on Frigg: the AIOps diagnosis agent's only door into the homelab. The code is `aiops/toolbelt/` in this repo (tested in `aiops/tests/test_toolbelt.py`); this role installs it.

- **What it installs:** the code under `/opt/aiops-toolbelt/aiops/` (root-owned, so the service cannot rewrite itself), a hardened `aiops-toolbelt.service` running as the unprivileged `aiops-toolbelt` user with state in `/var/lib/aiops-toolbelt/`, and a root token loader.
- **Secrets:** none on disk, none through Ansible. `ExecStartPre=+` runs `aiops-toolbelt-token-load` as root; it uses Frigg's existing root-only AppRole file to read `secret/ansible/aiops/toolbelt-token` (minted by `terraform/vault`) and writes it to `/run/aiops-toolbelt/token` (tmpfs, 0400, owned by the service user).
- **Scheduled canary fault injection (10f soak):** `aiops_toolbelt_soak` (default true) adds `--soak`; it runs only while `/aiops autonomy on` and the `aiops-canary-fault` Semaphore template is applied (registry `canary-fault` `applied: true`), within the registry's `soak:` scope and end date. See `docs/procedures/aiops-autonomy.md`.
- **Who can call it:** the app allow-list (`aiops_toolbelt_allow`, Gná only) AND the unit's `IPAddressDeny=any` / `IPAddressAllow=`, plus the bearer token. Anything else never reaches the process.
- **Run:** `ansible-playbook playbooks/asgard-control.yml --tags aiops-toolbelt` (only after `terraform apply` in `terraform/vault` has created the token). The role's own checks: `/healthz` answers and an anonymous `/stats` is refused with 403.
- **Dependencies:** `python3-yaml` (routing table). Everything else is the Python standard library.

Not here yet: the read-only tool endpoints and their credentials (kube, Zabbix, NetBox, PVE, ...) arrive in later 10d2 changes, each with its own `IPAddressAllow` entry.
