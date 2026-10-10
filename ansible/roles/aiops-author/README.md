# aiops-author

Phase 10h2: the PR author on Frigg. Design: [`docs/plans/active/10h-predictive-change.md`](../../../docs/plans/active/10h-predictive-change.md).
Operations and threat model: [`docs/procedures/aiops-author.md`](../../../docs/procedures/aiops-author.md).

Three unprivileged-by-design parts and two small root helpers:

| Part | Runs as | Holds | Never has |
|---|---|---|---|
| `aiops-author.service` (dispatcher) | `aiops-author` | the GitHub token (via a git askpass), the Toolbelt author token | an LLM, the model key (it is copied into a job's env file only while a session runs) |
| `aiops-draft@<id>.service` (one session) | `aiops-draft` | the model key, the read-only tools token | the GitHub token, Vault, SSH keys, any private network except the Toolbelt |
| `aiops-author-creds.service` | root | the AppRole secret-zero | n/a: writes four 0400 files for the dispatcher, then exits |
| `aiops-draft-launch.{path,service}` | root | nothing | n/a: starts `aiops-draft@<digits>` when the dispatcher drops a marker |

Run `aiops-toolbelt` first (it installs the code tree and the Toolbelt's new roles). Needs `terraform/vault` applied (the two
bearer tokens and the `aiops-author` AppRole) and `secret/ansible/aiops/author-pat` seeded.

    ansible-playbook playbooks/asgard-control.yml --limit frigg --tags aiops-author \
      -e aiops_author_role_id=... -e aiops_author_secret_id=...      # secret-zero: first run only

The dispatcher refuses to claim anything while the token's account has admin/maintain on the repo (`aiops_author_allow_admin_token`).
