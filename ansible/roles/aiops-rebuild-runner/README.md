# aiops-rebuild-runner

The Phase 10g rebuild runner on Frigg: the only place Terraform runs unattended. The code is `aiops/runner/rebuild_runner.py` (tested in
`aiops/tests/test_rebuild_runner.py`); this role installs it. Operations, threat model and acceptance: [`docs/procedures/aiops-rebuild.md`](../../../docs/procedures/aiops-rebuild.md).

- **Installs:** the code under `/opt/aiops-rebuild-runner/aiops/` (root-owned), an unprivileged `aiops-rebuild` user, the `aiops-rebuild-clients` group (the runner's socket group; the Toolbelt user is added to it), a hardened `aiops-rebuild-runner.service`, and a root credential loader unit `aiops-rebuild-creds.service`.
- **State:** `/var/lib/aiops-rebuild/` (0700): a clean `main` checkout the runner syncs itself (`git fetch --depth 1` + hard reset, only in that directory, before every plan), saved plans (single use, minutes of life), the Terraform data dir and plugin cache.
- **Secrets:** one AppRole secret-zero, `/etc/aiops-rebuild/approle.env` (root 0600, supplied once with `-e`, never in Git). The loader logs in with it (policy `aiops-rebuild-runner`: read-only on `secret/ansible/aiops/rebuild/*`), and writes `/run/aiops-rebuild-creds/env` (tmpfs, root 0400) which systemd injects into the service; the service user cannot read it.
- **Run:** `ansible-playbook playbooks/asgard-rebuild-runner.yml` (see the playbook header for the first-run flags and prerequisites). `-e aiops_rebuild_runner_enabled=false` installs without starting.
- **Dependencies:** `python3-yaml`, `git`, `terraform` (control-node role), and the `aiops-toolbelt` role having run. The Toolbelt's unit must also allow `AF_UNIX` (it restricts address families) to connect to the socket.
