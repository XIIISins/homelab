<!-- TODO.md — session handoff 2026-05-26 -->

# Pick up where 2026-05-26 left off

Session summary: closed 5h.2 (Hermod live failover validation), drafted Phase 8c.3–8c.6 (Zabbix SAML SSO), renumbered legacy phases 6/7/8 → 7/8/9, slotted new Phase 6 (Vault OIDC / human-facing secret management). All work is offline-safe — `terraform apply` and `git push` were unavailable for the session.

---

## Branch state

```
main (4 ahead of origin/main):
  62e78a4 Merge branch 'worktree-plan-phase-5k-vault-oidc'
  ├── 2bcc0ed docs(6): plan Phase 6 — Vault UI as human secret accesspoint
  ├── 4bd7e35 refactor(docs): renumber phases 6/7/8 → 7/8/9
  ├── 2593133 docs(5h.2): close phase — live failover validated
  └── 70ad3af fix(patroni): map 'primary' role in hermod-callback
```

```
worktree-draft-zabbix-saml (4 ahead of main, ff-merge ready):
  c65fc68 refactor: align with phase renumbering — 7c → 8c
  99e1602 docs(zabbix): procedure for 7c.3-7c.6 deploy
  ab804bd docs(zabbix): fix design-doc drift + annotate 7c.3-7c.5 drafted
  d38ec83 feat(zabbix): draft SAML SSO + Traefik fronting (7c.3-7c.5)
```

```
worktree-5h3-semaphore-drafts (5 behind main, 27 uncommitted files):
  in-progress Phase 5h.3 work — playbook renames, site.yml, NetBox dynamic inventory, Semaphore TF
```

```
worktree-plan-phase-5k-vault-oidc — already integrated via merge commit 62e78a4
  user requested to keep around for now as a reference point
```

---

## When network's back

**1. Push main** — `git push origin main` (4 commits queued).

**2. ff-merge the SAML draft branch** (offline-safe — can do before push if you prefer):
```fish
git merge --ff-only worktree-draft-zabbix-saml
git worktree remove .claude/worktrees/draft-zabbix-saml
git branch -d worktree-draft-zabbix-saml
git push origin main
```

**3. Deploy Phase 8c.3–8c.6 (Zabbix SAML SSO)** when ready. Full runbook at [`docs/procedures/zabbix-saml-deploy.md`](docs/procedures/zabbix-saml-deploy.md). Highlights:
- Pre-req: one-time openssl + `vault kv put secret/ansible/zabbix/saml-sp-keypair` (procedure §1)
- TF applies in order: `terraform/authentik/` → `terraform/cloudflare/` → `terraform/adguard/`
- Push for Flux reconcile (cloudflared configmap + zabbix-ingress)
- `ansible-playbook playbooks/zabbix-host.yml --tags zabbix:saml`
- 3-path validation (procedure §5)

---

## Pending operator actions (no network needed — can do offline)

- **Update 1P "Proxmox API - Terraform"** with the rotated token. Token leaked in transcript 2026-05-25 via failed BSD-sed redaction; new token id `terraform-rot-20260525-055802`, value stashed at `secret/temp/proxmox/api-token-rotated-20260525` field `full_token`. After updating 1P, `vault kv metadata delete secret/temp/proxmox/api-token-rotated-20260525`. (open-questions.md:172)
- **Create 1P item `Ansible - Vault - k3s`** with current AppRole creds (open-questions.md:67-87 has the field map). Then delete `~/.config/ansible/vault-approle.env` + `~/.config/fish/functions/ansible-vault-env.fish` once the smoketests below pass.
- **Smoketest the `homelab.fish` shim**: `homelab-env`, `set-vault-token root` + `set-vault-token approle`, `vault kv get secret/ansible/test/hello`, then the `rotate-approle` dry-run round-trip (open-questions.md:78-85). Closes the long-pending control-node tooling consolidation.

---

## In-progress work waiting for you

**`worktree-5h3-semaphore-drafts`** — Phase 5h.3 Ansible orchestration drafts. 27 modified/added/renamed files spanning playbook restructure (`*-host.yml` → `asgard-*.yml`), `site.yml` orchestrator, `ansible/inventory/netbox.yml` dynamic inventory, `terraform/authentik/semaphore.tf`, `terraform/vault/main.tf` Semaphore wiring, role README updates. 5 commits behind main (Phase 6 plan + Patroni fix + renumbering aren't there).

When you resume:
1. `cd .claude/worktrees/5h3-semaphore-drafts`
2. Decide whether to rebase onto current main first (probably yes — the renumbering may have implications; though 5h.3 work is mostly internal Ansible plumbing so conflicts should be minor) OR commit drafts first and rebase after.
3. Grep for any `Phase 6/7/8` references the draft picked up — apply 7→8 / 6→7 / 8→9 sweep if so. (Quick check: 5h.3 is Ansible orchestration plumbing, unlikely to reference Phase 6/7/8 directly.)
4. Continue the actual 5h.3 work per the plan in [`docs/architecture/ansible-orchestration.md`](docs/architecture/ansible-orchestration.md) + the open-questions sub-phase plan.

---

## Forward path (when SAML lands)

Next phase after 8c closes is operator-choice between:

1. **Phase 6 — Vault OIDC** (human secret-management). Plan in [`docs/operations/open-questions.md`](docs/operations/open-questions.md) at the Phase 6 entry. 5 sub-phases (6.a–6.e). No procedure doc yet — worth writing one alongside the deploy.
2. **Phase 5h.3 — Ansible orchestration** (Semaphore + drift-check). Already has live drafts in the worktree above. Sequenced after 5h.2 (now closed); 5h.3's drift-check is the first concrete `tag: alert` producer for Hermod.
3. **Phase 7 — Jotunheim K3s** (second cluster, learning sandbox). Big phase.
4. **Service backlog** — Immich, n8n, Privatebin, Startpage, Jellyfin.

---

## Cleanup options (when you decide)

- `git worktree remove .claude/worktrees/add-handoff-todo` + `git branch -d worktree-add-handoff-todo` — this very worktree, after ff-merge.
- `git worktree remove .claude/worktrees/draft-zabbix-saml` + `git branch -d worktree-draft-zabbix-saml` — after the SAML branch is ff-merged + pushed.
- `git worktree remove .claude/worktrees/plan-phase-5k-vault-oidc` + `git branch -d worktree-plan-phase-5k-vault-oidc` — already integrated; you asked to keep for now as a reference point.
- `git worktree remove .claude/worktrees/5h3-semaphore-drafts` — KEEP until 5h.3 work finishes + lands.

---

*Generated 2026-05-26 — delete this file once you're caught up.*
