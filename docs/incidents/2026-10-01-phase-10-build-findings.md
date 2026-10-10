<!-- docs/incidents/2026-10-01-phase-10-build-findings.md -->

# 2026-10-01/02 — Phase 10 build (10a/10b/10c, PBS move, Frigg ssh-agent): findings

*Not an outage: a retrospective on a one-day build that surfaced a lot of small, mostly-first-run problems. Plan: [`aiops-roadmap.md`](../plans/active/aiops-roadmap.md). One separate security event has its own file: [`2026-10-01-discord-webhook-transcript-leak.md`](2026-10-01-discord-webhook-transcript-leak.md).*

## What happened

PBS was moved off Skuld to Urd, 10c (machine-readable ops) landed, the offsite node `do1` was rebuilt from IaC and cut over, the canary pool, Gatus watcher and burst substrate went live, and Frigg got a memory-only ssh-agent. Every item below was found by running the thing for real; most were invisible to `--check`, `terraform plan` and CI. All are fixed or have a tracked follow-up.

## Findings

1. **Check mode and CI do not catch first-run service problems.** Four separate bugs only showed on a real run against a fresh host: Caddy's first start failing on root-owned log files created by `caddy validate` *after* the log-dir chown (the play reported ok while `caddy` was `failed`); Gatus's `ExecStartPre` timing out because the unit's `RestrictAddressFamilies` also confines `ip` (needs `AF_NETLINK`; fixed with a `+` prefix); the PlantNet image build failing because HeyLeaf `.gitignore`s `package-lock.json` (`npm ci` cannot build from an archive of the pinned commit; the role now ships a hash-pinned lockfile); and a failed image build never being retried (nothing "changed" on the next run). See `known-issues/digitalocean.md`, `caddy.md`.
2. **Local linters were laxer than CI's pinned ones.** PR #20 failed CI on `name[play]` because local `ansible-lint` was 26.9.0 and CI pins 24.12.2 (+ `ansible-core` 2.16.5, which needs Python 3.13). New standing rule: lint with the CI-pinned toolchain before every push; recipe in the session memory and `.github/ci-requirements.txt`.
3. **The DO API token's scopes were wrong twice.** A custom-scope token lacked `reserved_ip` and `project` (403 on the reserved IP), and later `tag:create/delete` (the burst substrate failed at tag creation). A "full access" token that also 403s on `/account` is not full access. Final state: a full-access token in Vault (open item to narrow it). Probe scope with read-only `GET`s on each resource type before applying.
4. **Several shim/secret assumptions were wrong.** The Vault shim never wrote `~/.ssh/ansible_niflheim` (docs and a `frigg.tf` comment said it did), so the first Frigg-driven burst run died minutes in; `burst-up` only checked the variable was *set*. Fixed with `roles/frigg-ssh-agent` (memory-only agent; [`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md)). Also: the shim's 3-hour cache serves the OLD value after a Vault patch or a shim change until `vault-homelab-env --refresh` is run on EVERY machine (hit twice: stale DO token on the Mac broke `burst-down` tag deletion; the new shim logic did not load on Frigg until refreshed).
5. **NetBox's pod was OOMKilled by Terraform's default parallelism** (~1 min of 503s, self-recovered; provider error `invalid character 'B'`). `-parallelism=2` is now the rule for `terraform/netbox`; raising the memory limit is open. Also: a non-Proxmox node with no `VMID` gives a perpetual `custom_fields` diff — solved by reserving VMIDs 9900–9999 for DO nodes (`do1` = 9900).
6. **Tailscale quirks.** The first key creation after a *new* tag owner is added fails once (`Failed to create key`); retry. `tailscale ping` (discovery/TSMP) is answered even when the ACL blocks everything, so it is not an isolation test — use ICMP/TCP. The four 90-day node keys had silently expired on 2026-08-21 and were re-minted by the apply (harmless for joined tagged nodes). `do1` needed `--accept-routes` (opt-in `tailscale_accept_routes`) for its Gatus probe of Hermod; its container-bridge drop rule was widened to `10.0.0.0/16` to match.
7. **A pinned-but-unprovable assumption cost a round trip:** the TS3 SQLite dump the plan relied on was not on Frigg (re-taken via the SQLite backup API because the live DB is WAL-mode). Mitigated further by the operator's call that the TS3 fallback is temporary and a DB wipe is acceptable.
8. **Operational pitfalls in the tooling itself:** `pgrep -f <name>` over ssh matches its own command line (use a bracketed pattern); macOS has no `flock` (use a python `fcntl` lock); zsh treats `$B:path` as a modifier (use `${B}:path`); the sandboxed session blocks `source`/`PATH=` overrides inside an isolated worktree (leave it with `ExitWorktree keep` to run Ansible/Terraform). Recorded in `known-issues/shell-tooling.md`.

## Changes

`terraform/digitalocean`, `terraform/digitalocean-burst`, `terraform/tailscale`, `terraform/cloudflare/ts3.tf`, `terraform/netbox/offsite.tf` + canaries, `terraform/proxmox/asgard-lxcs`, `terraform/semaphore`; roles `offsite-node`, `do-reserved-egress`, `gatus`, `gatus-heartbeat`, `burst-reaper`, `frigg-ssh-agent`, plus fixes to `caddy-reverse-proxy`, `tailscale`, `k3s`; `aiops/`; `scripts/burst/`; the shim (bash + fish). Decisions rows for each architectural choice are in [`decisions.md`](../operations/decisions.md).

## Follow-ups

Tracked in [`open-questions.md`](../operations/open-questions.md): 7-day soak then `do1` cleanup (on/after 2026-10-08), narrow the DO token, NetBox memory limit, 1P mirrors of the new secrets, PBS capacity, the offsite-backup restore drill, and the HeyLeaf lockfile/base-image items.
