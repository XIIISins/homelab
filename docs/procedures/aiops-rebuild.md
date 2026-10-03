<!-- docs/procedures/aiops-rebuild.md -->

# Procedure — AIOps rebuild runner: Terraform from a clean checkout, scoped to the canary pool (Phase 10g, slice B)

*Design: [`operations/10g-rebuild-loop.md`](../operations/10g-rebuild-loop.md) ("Running `terraform apply` from an automated path", option 2). Code: [`aiops/runner/rebuild_runner.py`](../../aiops/runner/rebuild_runner.py), pool and token in [`terraform/proxmox/asgard-pools/`](../../terraform/proxmox/asgard-pools/), Vault role in [`terraform/vault/rebuild-runner.tf`](../../terraform/vault/rebuild-runner.tf), Ansible role [`roles/aiops-rebuild-runner`](../../ansible/roles/aiops-rebuild-runner/README.md). Predecessors: [`aiops-actions.md`](aiops-actions.md), [`aiops-autonomy.md`](aiops-autonomy.md). The manual rebuild this automates: [`canary-pool.md`](canary-pool.md) ("Destroy / recreate").*

**State when written: code only.** Nothing here has been applied or deployed. The engine side (the Toolbelt client of the socket, the `rebuild-guest` action) is slice A; the converge/verify playbooks are slice C.

## What the runner is

A small service on Frigg, under its own unprivileged user, listening on a **unix socket** (`/run/aiops-rebuild/runner.sock`, mode 0660, group `aiops-rebuild-clients`). It speaks one JSON line in, one JSON line out, and accepts only three operations:

| Request | Meaning |
|---|---|
| `plan` + `class` + `target` | validate the target, sync the clean checkout to `origin/main`, run `terraform plan -replace=<addr> -target=<addr> -out=<file>`, machine-check the plan, return its **plan id** (sha256 of the saved plan file) and the commit it came from; valid for `plan_max_age_seconds` (15 min) |
| `apply` + `plan_id` | apply exactly that saved plan, once, if it is unexpired, unmodified and `origin/main` has not moved |
| `status` | busy flag and the last operation |

There is no way to send it a command, an address, a variable or a path. Errors carry a code: `plan-unknown`, `plan-expired`, `origin-moved`, `denied`, `terraform-failed`, `busy` (plus `bad-request` and `checkout-failed`; `plan-rejected` with the checker's problems when a plan fails the shape rules). Every operation writes a structured JSON audit line to the journal (so to VictoriaLogs through vlagent): plan id, address, commit, durations, never a credential.

## Threat model: what it can and cannot do

**What it can do:** destroy and re-create the three canary containers (VMIDs 1190-1192 on Urd) from the reviewed `main`, one at a time. That is all, because every layer below has to be wrong at once for anything else to happen.

| Layer | What it enforces | Independent of |
|---|---|---|
| Socket | group `aiops-rebuild-clients` (the Toolbelt user only) **and** a kernel-attested uid check (`SO_PEERCRED`) against an allow-list; line limit 4 KiB, 10 s read timeout | the Toolbelt |
| Request shape | exact key set per op; `class`/`target` are short lowercase words; `plan_id` is 64 hex chars; unknown fields refuse the request | the Toolbelt |
| Registry re-check | after syncing its **own** checkout the runner reads `aiops/actions.yml` itself: deny list (names and VMIDs), class allow-list, class/target agreement, then its own `CLASS_TABLE` (only `canary`, target `canary-[123]`, VMIDs 1190-1192). The other registry classes have **no row**, so they are `denied` by construction | the Toolbelt's validation |
| Plan check | `rebuild.check_plan` (reused, not copied): exactly one `replace` (or `create`, for a guest deleted behind Terraform) of the expected address; name, VMID, node, IP, VLAN, template unchanged; nothing else in the plan; no drift elsewhere | the model, the Toolbelt |
| Saved-plan apply | the plan file is hashed again before apply and must equal the id; the checkout must be clean, HEAD must equal the planned commit and `git fetch` must show `origin/main` unchanged; a plan is single use | time of check vs time of use |
| Single flight | one operation fleet-wide; a second caller gets `busy` | engine queue length 1 |
| **Proxmox** | the PVE API token belongs to `aiops-rebuild@pve`, whose ACLs are the `aiops-canary` pool plus the storage and SDN entries needed to allocate a rootfs and attach a NIC; **no ACL on `/`**. A guest outside the pool (PBS 1101 included) answers 403 to this token even if every check above were bypassed | all software |
| Credentials | the PVE token, a **state-only** AWS identity and the public SSH key; no operator admin credential, no Vault write, no Proxmox root. Held by systemd on tmpfs; the service user cannot read the env file | |

**What it cannot do:** touch any guest outside the pool; plan or apply any module other than `asgard-lxcs`; run a command; write to Vault or Proxmox ACLs; apply a plan it did not make; apply across a moved `main`; set LXC `features` other than nesting or device passthrough (PVE reserves those to `root@pam`, which is why the Tailscale LXCs stay out of its scope). **What it still is:** a privileged path on Frigg, T3 infrastructure: changes to the runner, its unit, its table or its Vault policy are human-reviewed. A bug in `CLASS_TABLE` is bounded by the pool; a compromise of the runner user is bounded by the pool plus the state identity (which can write Terraform state for `asgard-lxcs` and nothing else once the narrow IAM identity exists).

Why a pool-scoped token and not "trust the allow-list": the allow-list is code, the pool is Proxmox's own authorisation. The two fail differently.

Why not Semaphore, GitHub Actions or the PVE API directly: see the options table in the design (a worker rebuild must not depend on the cluster being rebuilt; hosted runners cannot reach PVE; direct API calls fork the source of truth).

## The switches

| Switch | Where | Default | Effect |
|---|---|---|---|
| `aiops_rebuild_runner_enabled` | Ansible variable | `true` | `false` installs the units and leaves the service stopped |
| `aiops_rebuild_runner_classes` | Ansible variable | `[canary]` | classes the runner serves; a class without a `CLASS_TABLE` row stops startup |
| `aiops_rebuild_runner_clients` | Ansible variable | `[aiops-toolbelt]` | who may connect |
| `rebuild.autonomy_rebuild` and the kill switch | Toolbelt (slice A) | off | whether the **engine** may ask for a rebuild unattended; the runner has no autonomy of its own: it only answers requests. `systemctl stop aiops-rebuild-runner` is the hard stop |
| the registry's `deny` list | `aiops/actions.yml` | pinned by lint | re-read by the runner at every plan |

## Class → module → address (how `CLASS_TABLE` is derived)

For each class the table row is read off the real module, never guessed: the Terraform **module directory**, the **resource address** (the `for_each` key is the registry target name) and a regex plus the VMIDs of the module's own definition. For `canary`: `terraform/proxmox/asgard-lxcs/lxcs.tf` declares `proxmox_virtual_environment_container.canary` with `for_each = local.canary_nodes` (keys `canary-1..3`), so the address is `proxmox_virtual_environment_container.canary["canary-2"]`, the exact one [`canary-pool.md`](canary-pool.md) already uses by hand. A unit test pins the row to the HCL (`TableTests`). To enable another class: verify its address against the module (a single resource? a `for_each`? which provider attributes carry name/VMID/IP, see `rebuild.IDENTITY_PATHS`), add the row, add its pool, extend the tests, and review it as T3.

## Deploy order (operator; Claude does none of these)

1. **Decide** the terraform-apply policy exception (option 2 of the design) and amend the CLAUDE.md invariant line plus a `decisions.md` row. Until then, stage A runs in **approval mode** only.
2. `terraform apply` in [`terraform/proxmox/asgard-pools`](../../terraform/proxmox/asgard-pools/) (main checkout; `PROXMOX_VE_PASSWORD` from 1Password, your own `VAULT_TOKEN`). Creates the pool, four roles, `aiops-rebuild@pve`, its ACLs and token `runner`; writes `secret/ansible/aiops/rebuild/pve-token`.
3. `terraform apply` in `terraform/proxmox/asgard-lxcs`: the canaries gain `pool_id = "aiops-canary"`. **The plan must show an in-place update for each canary. A `replace` means stop** and report.
4. `terraform apply` in `terraform/vault`: the `aiops-rebuild-runner` policy and AppRole.
5. Mint the **state identity** (`terraform/aws/rebuild-runner.tf`, user `rebuild-runner-state`, outputs `rebuild_runner_state_access_key_id` and the sensitive `..._secret_access_key`) with the Bootstrap identity (S3 get/put/delete on `proxmox/asgard-lxcs/terraform.tfstate` and its lock file, `ListBucket` on the bucket prefix, nothing else) and seed Vault `secret/ansible/aiops/rebuild/env` with `aws_access_key_id`, `aws_secret_access_key`, `ssh_public_key` (the `ansible` user's public key) and `aws_default_region`. Values are fetched inside the shell, never typed into a transcript.
6. Mint the AppRole SecretID: `vault write -f auth/approle/role/aiops-rebuild-runner/secret-id` (value into a shell variable); read the role id; note **the expiry date (90 days) in the calendar**.
7. `ansible-playbook playbooks/asgard-rebuild-runner.yml -e aiops_rebuild_runner_role_id=... -e aiops_rebuild_runner_secret_id=...` (from a shell variable). Add `-e aiops_rebuild_runner_enabled=false` first if steps 2-5 are not all done.
8. Mirror the new secrets to 1Password (`scripts/secrets/vault-1p-mirror`, [`secret-mirroring.md`](secret-mirroring.md)).
9. UCG: Frigg must reach PVE (`10.0.254.11:8006`), the S3 endpoint, GitHub and the Terraform provider registry (`registry.terraform.io`, `releases.hashicorp.com`, GitHub releases). The first four exist for the operator's tools on Frigg; verify the registry/GitHub egress on the first `plan`.
10. The Toolbelt unit restricts address families to `AF_INET`: it needs `AF_UNIX` to reach the socket (change in the `aiops-toolbelt` role, with the slice A client), then a Toolbelt restart (the role also restarts it once to pick up the new group).
11. **Reboot Frigg** and re-run the acceptance checks (CLAUDE.md persistence rule): the loader and the runner are units, the creds file is tmpfs.

## Acceptance

Run as the Toolbelt user (e.g. `sudo -u aiops-toolbelt python3 -c ...` sending `{"v":1,"op":"status"}` to the socket), then through the engine once slice A lands.

1. `status` answers `ok`, `busy:false`. As any other user (`ghost`, root): `denied: peer not allowed`.
2. **Plan only:** `plan canary-2` returns `action: replace`, `changes: 1`, identity `canary-2 / 1191 / urd / 10.0.11.191`, an `origin_main` equal to GitHub's `main`. Negatives, each refused with `denied` and **no terraform invocation in the journal**: `saga`, `pbs`, `gondul`, `mimir` (class `canary` and class `adguard-replica`), a name in no class.
3. **Expiry and movement:** a plan older than 15 min answers `plan-expired`; merge any commit to `main` between plan and apply: `origin-moved`; a second `apply` of the same id: `plan-unknown`.
4. **Apply (approval mode):** apply the plan for `canary-2`; the container is re-created (Terraform output in the audit line's duration only), NetBox unchanged; then the converge/verify steps of [`canary-pool.md`](canary-pool.md) ("Destroy / recreate", Build steps 3-4). Record the duration of `plan` and `apply`.
5. **Scoped-token negative tests (the Proxmox boundary):** with the runner's token (read from Vault inside a shell, never printed), `GET /nodes/urd/lxc/1101/status/current` and a `stop` of 1101 (PBS) and of a non-pool guest each answer **403**; a stop of `canary-3` (in the pool) is allowed. Record the exact privilege list that was needed: if the first apply reports a missing privilege, add exactly that privilege to `asgard-pools/main.tf` in a PR.
6. **Busy:** two clients at once: one gets the lock, the other `busy`. **Reboot test:** reboot Frigg; the socket returns, no stale plan (`status` last is empty), no half-applied state.
7. **Revocation drill:** remove the ACL (or disable the user) and plan `canary-3`: `terraform-failed`, nothing created; restore.

## Operating notes

- **The checkout is the runner's own.** `/var/lib/aiops-rebuild/checkout` is cloned by the runner on first use and hard-reset to `origin/main` (`fetch --depth 1`, `reset --hard FETCH_HEAD`, `clean -fdx` keeping only `.terraform.lock.hcl`) before every plan. Nobody edits it; the provider data dir lives outside it so a clean never breaks a saved plan.
- **Provider downloads happen on `init`** (plugin cache under the state directory). Lock files are not committed in this repo, so the version pins in `versions.tf` are the contract.
- **A restart invalidates every plan** (they live in memory and the plan files are deleted at startup): the next proposal simply plans again.
- **Terraform failures** return `terraform-failed` with a bounded, character-filtered tail of stderr; the full output is never returned. A failed apply may leave a half-built guest: the engine treats it as a breaker event (slice A) and a human looks.
- **SecretID expiry** stops the runner from starting after 90 days: re-mint (step 6) and re-run the playbook with the two `-e` values. A health alert for this expiry is an open item (the existing AppRole-expiry check does not know this role).
- **Deprecation warnings** on `terraform validate` for `proxmox_virtual_environment_acl` / `_user_token` (the provider renames them `proxmox_acl` / `proxmox_user_token` before 1.0) are the same ones the other Proxmox modules carry; migrate them together.

## Not built yet

The Toolbelt client and the `rebuild-guest` / `rebuild-plan` actions (slice A); converge and verify playbooks, runbooks, routing, the canary High trigger and the fault helper (slice C); the narrow state IAM identity (`terraform/aws`); the `aiops-replica` pool and every non-canary class.
