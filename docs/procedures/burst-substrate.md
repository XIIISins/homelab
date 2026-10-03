<!-- docs/procedures/burst-substrate.md -->

# Procedure — burst substrate (ephemeral DigitalOcean K3s) and the offsite-backup restore drill

*Phase 10b2 ([`aiops-roadmap.md`](../operations/aiops-roadmap.md) §10b2). Code: `terraform/digitalocean-burst/`, `terraform/tailscale/` (`tag:burst`), `ansible/playbooks/burst-k3s.yml`, `ansible/inventory-burst/`, `ansible/roles/burst-reaper/`, `scripts/burst/`. Gotchas: [`digitalocean.md`](../known-issues/digitalocean.md), [`tailscale.md`](../known-issues/tailscale.md). Decision row: [`decisions.md`](../operations/decisions.md) ("Burst/test substrate").*

A throwaway 3-node K3s cluster on plain DigitalOcean droplets, built with the **existing `k3s` role** (not DOKS: DOKS is not K3s and its node auto-repair would confound heal/rebuild tests). It exists for tests that must not touch prod: the offsite-backup restore drill, K3s heal/rebuild, fault injection, firewall probes from an independent vantage. Default shape: 3 × `s-2vcpu-4gb`, ams3, Debian 13, 1 control plane + 2 workers, TTL 4 h.

> **Status:** code merged-ready, **nothing applied**. Every live step below is an operator/parent action from a **main checkout** (Terraform applies and playbooks never run from a worktree). The restore drill section is a documented plan, **not yet run**.

## Design in one screen

| Concern | Choice |
|---|---|
| State | Own key `digitalocean-burst/terraform.tfstate` in the shared state bucket. Never in the `do1` root: `destroy` here cannot touch do1, an apply of do1 cannot touch a burst cluster. |
| Default is inert | `burst_count = 0` → no tag, no firewall, no droplet. A plain `terraform apply` is a no-op. |
| Tags | DO tag `burst` (+ `burst-ttl-<N>h`) on every droplet; the cloud firewall binds by that tag. No legacy firewall binds `burst` (they bind `ots`, `portainer`, `tailscale`). |
| Network | Droplets sit in the region's **default VPC** (no `vpc:create` scope). K3s `node-ip` = VPC private IP, so cluster traffic and Calico VXLAN never use the public interface. Public inbound is only tcp/22 (restrictable via `ssh_source_cidrs`) and udp/41641. |
| Tailnet | `tag:burst`, ephemeral pre-authorized reusable key from `terraform/tailscale` → Vault `secret/ansible/tailscale/authkeys/burst`. Grants: Frigg (`tag:server`) → `tag:burst` (all ports); `tag:burst` is **not a src of any grant** → no path to prod or to other burst nodes. The existing `autogroup:member → *` rule (operator devices) is intentional and untouched; the `tag:offsite` grants are untouched. Burst nodes do not `--accept-routes`, so the subnet routers' `10.0.0.0/16` never lands on them. |
| K3s | Existing role, own `burst` inventory tree (`ansible/inventory-burst/`, generated hosts, own `k3s_token` per run, pod `172.24.0.0/16` / service `172.25.0.0/16` — cannot collide with prod `10.42/10.43`, any DO VPC `10.x/20`, or the tailnet `100.64/10`), `k3s_etcd_s3_enabled: false` (a burst CP must never write into the prod backup folder). The role gained portability knobs (SELinux, VLAN-20 routing, `node-ip`, extra SANs, Calico CIDR, Debian prerequisites); asgard renders were verified byte-identical. |
| No baseline/hardening | Ephemeral (hours), key-only root login, DO firewall, no inbound from prod. Skipping `baseline`/`hardening` also avoids needing `vault.yml` (and its Ansible Vault password) for a run. |
| **SSH access** | Droplets are created with the **existing** DO key `homelab-offsite-ansible` (the `ansible_niflheim` public key), referenced by `data "digitalocean_ssh_key"` by name — DO rejects a duplicate public key ("SSH Key is already in use"), so the burst root never creates keys. Ansible logs in as `root` with `ANSIBLE_PRIVATE_KEY_FILE` (Frigg's shim materializes it). Dependency: the do1 root must stay applied (destroying it deletes the key). |
| Control host | Run `burst-up`/`burst-down` from **Frigg** (Vault env, tailnet reach to the cluster API; the fleet SSH key comes from Frigg's memory-only `frigg-ssh-agent`, see `known-issues/frigg-control-node.md`). The operator's Mac must not run Tailscale at home; it can still drive the Terraform/Ansible half, but `kubectl` against the burst API needs the tailnet. |

## DO token scopes — fit

The shared custom-scope token (Vault `secret/ansible/frigg/iac-env` → `digitalocean_token`) has droplet, firewall, reserved_ip, ssh_key, project, tag (+ the read-only image/regions/sizes/actions/vpc scopes) and **no** account/billing scopes (replaced 2026-10-03; the do1 root needs reserved_ip + project, this one does not). This root needs: `droplet:create/read/update/delete`, `firewall:create/read/update/delete`, `tag:create/read/delete`, `ssh_key:read` (data source), `vpc:read` (data source), plus the read scopes for image/size/region lookups. It uses **no** reserved IP, project, account or `vpc:create`, so the token **fits as-is**. The reaper needs only `droplet:read` + `droplet:delete`. **Exercised 2026-10-03** against the replacement scoped token: `terraform plan` of `terraform/digitalocean` (no changes), then `burst-up -n 1 -c 1 -t 1 --yes` (4 resources: tag, TTL tag, firewall, droplet; K3s role 0 failed), the reaper `--list` and a forced real reap (`droplet:read` + `droplet:delete`), and `burst-down`. No 403. `droplet:admin` is not needed. If a future change 403s, widen by exactly the scope the error names.

## Apply order (each step gates the next; all from a main checkout)

1. **`terraform/tailscale`** — `plan`: expect ACL policy in-place update (new `tag:burst` owner + one `tag:server → tag:burst` grant), `tailscale_tailnet_key.burst` (+1), `vault_kv_secret_v2.burst_authkey` (+1). Apply, then confirm nothing lost reach (`tailscale ping` from Frigg / operator device — read-only).
2. **`terraform/digitalocean-burst` `plan` with the default `burst_count = 0`** — expect **no changes** (the data sources are gated on `count`; only provider auth is exercised). `terraform init` here also proves the state backend.
3. **Frigg reaper role** — dry-run first: `ansible-playbook playbooks/asgard-control.yml --tags burst-reaper --limit frigg -e burst_reaper_dry_run=true --check --diff`, then for real, then again **without** the dry-run flag. Verify: `systemctl list-timers burst-reaper.timer`, `sudo systemctl start burst-reaper.service && journalctl -u burst-reaper -n 20` → "no burst droplets". One playbook at a time across agents.
4. **DO billing alert** (manual, below).
5. **First smoke test** (below) — the first `terraform apply` with `burst_count > 0`.

## Cost guard (non-negotiable — a forgotten cluster must not run for a month)

- **TTL reaper on Frigg** (`roles/burst-reaper`, in `asgard-control.yml` so a Frigg rebuild restores it): `burst-reaper.timer` every 15 min → `/usr/local/sbin/burst-reaper` (root). It lists droplets by the DO tag `burst` and destroys those whose age exceeds their `burst-ttl-<N>h` tag (default 4 h when absent), **never longer than the 12 h hard cap**, and only droplets named `burst-<n>` (a droplet tagged `burst` but named `do1` is skipped). The DO token is fetched from Vault at run time with the Frigg AppRole (in memory; not in the unit, env, argv or journal). Logs go to the journal (`journalctl -u burst-reaper`). On a reap it posts to Hermod (Discord `alert`); if the reaper itself fails it posts `critical` — a dead cost guard must not be silent. Modes: `--list` (ages, no deletes), `--dry-run` / `burst_reaper_dry_run: true`.
- **After a reap** run `scripts/burst/burst-down` — the reaper deletes droplets behind Terraform's back; `destroy` reconciles the state and removes the tag + firewall.
- **DO billing alert — console only, manual (DO has no API for it).** DO console → Settings → Billing → set a billing alert (email) at a threshold just above the expected steady-state (~$6/mo do1 now ~$12; suggest **$20/mo**), so a leak the reaper somehow missed (dead Frigg, revoked token) shows up within days. Record that it was set in `open-questions.md`; it is not in Terraform and cannot be drift-checked.
- Worst case if the reaper is dead and nobody notices: 3 × `s-2vcpu-4gb` ≈ $0.107/h ≈ $2.57/day ≈ **$72/mo** (hourly-billed, capped monthly per droplet at $24).

## Run it

```bash
# On Frigg, Vault env loaded ('. homelab-env' — DIGITALOCEAN_TOKEN, AWS_*, ANSIBLE_PRIVATE_KEY_FILE, ANSIBLE_HASHI_VAULT_*)
cd ~/homelab && git pull
scripts/burst/burst-up -n 3 -c 1 -t 4        # terraform apply -> inventory -> ansible burst-k3s.yml
KUBECONFIG=~/.kube/burst-k3s.yaml kubectl get nodes
scripts/burst/burst-down                      # destroy + remove token/inventory/kubeconfig
```

`burst-up` is re-runnable (the per-run k3s token is kept in `~/.cache/homelab/burst/vars.yml` until `burst-down`). The kubeconfig points at the init node's **tailnet IP** (`:6443`, never public). Ephemeral tailnet nodes disappear on their own once offline.

## First smoke test (also the acceptance test for 10b2)

Cost ≈ **$0.11** (3 × $0.0357/h × 1 h).

1. `scripts/burst/burst-up -n 3 -c 1 -t 1`. Expect: 3 nodes Ready (`kubectl get nodes -o wide` shows VPC `INTERNAL-IP`s), `calico-system` pods Running, `kubectl cluster-info dump | grep -m1 cluster-cidr` = `172.24.0.0/16`.
2. Tailnet reach (Frigg, read-only): `tailscale ping <burst-1 tailnet ip>` answers; `ssh frigg 'curl -sk https://<burst-1 tailnet ip>:6443/healthz'` → `ok` or 401 (reachable, unauthenticated).
3. **No path to prod (negative test)** — on `burst-1`: `ping -c1 -W2 10.0.11.30` fails (no route) and **ordinary traffic** to Frigg's tailnet IP is blocked: `ping -c2 -W3 <frigg tailnet ip>` fails and `timeout 3 bash -c '</dev/tcp/<frigg tailnet ip>/22'` fails (also 80/443/6443/8080/8200). **Do not use `tailscale ping` as the test**: its discovery/TSMP pings are answered by design (the ACL packet filter does not apply to them; verified 2026-10-02), so it says "pong" even though no traffic can pass. `do1` should not even appear in `tailscale status` on the burst node (`no matching peer`). The `tag:burst` node must be a dead end.
4. Reaper: `sudo /usr/local/sbin/burst-reaper --list` shows the 3 droplets with their age and 1 h TTL; force-expire in **dry-run first**: `sudo env BURST_REAPER_HARD_CAP_HOURS=0 /usr/local/sbin/burst-reaper --dry-run` → three `DRY-RUN would reap` lines. Then prove the real path once: `sudo env BURST_REAPER_HARD_CAP_HOURS=0 /usr/local/sbin/burst-reaper` → three `REAPED` lines + a Hermod message; `doctl compute droplet list --tag-name burst` is empty.
5. `scripts/burst/burst-down` (reconciles the state after the reap). Confirm in the DO console that no `burst-*` droplet or `burst` firewall remains.

## Restore drill for the offsite backups (run 2026-10-03, S3 legs pass)

Goal: prove the three S3 legs ([`offsite-backups.md`](offsite-backups.md)) restore onto a scratch cluster, then destroy it. Use the burst substrate with **prod's CIDRs and token** for this cluster only (it is isolated: no route to prod, no S3 write path), because the restored etcd contains prod's node/pod-CIDR state:

```bash
# 0600 vars file with prod's k3s_token (Ansible Vault; value never echoed), prod CIDRs
( umask 077; ansible-vault view ansible/inventory/group_vars/all/vault.yml | grep '^k3s_token:' > "$HOME/.cache/homelab/burst/drill-vars.yml" )
scripts/burst/burst-up -n 1 -c 1 -t 3 -- -e @"$HOME/.cache/homelab/burst/drill-vars.yml" \
    -e k3s_pod_cidr=10.42.0.0/16 -e k3s_service_cidr=10.43.0.0/16
```

**Credentials (decided 2026-10-03, path B):** the etcd IAM user lists/reads only `etcd/*` and the Vault-Raft/Calico writer is PutObject-only, so a dedicated read-only user `homelab-backup-restore` (`terraform/aws/backups.tf`, no Terraform-made key) reads all three prefixes; its key is minted by `scripts/secrets/mint-restore-key` and kept in 1Password only (see [`offsite-backups.md`](offsite-backups.md), "Restore credentials"). Fetch the three objects onto Frigg with that key in ONE shell (read from 1Password with `op`, exported there, never written to disk or to the droplet) and `scp` them to the burst node.

1. **etcd.** Snapshot → burst-1 (a CP): stop K3s, `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot>` with the S3 flags (or a pre-downloaded file) and the **original token** (`--token`; a snapshot restored onto a fresh node needs the token that sealed its bootstrap data), then start K3s. Pass: API up, `kubectl get ns` / `kubectl get secrets -A | wc -l` match prod, nodes listed as prod's (`gondul`, …) `NotReady` — that is expected (no kubelets). Do not re-join workers; the proof is the datastore.
2. **Calico objects (CRDs → Installation → objects).** On a fresh drill cluster the role has already laid down the operator CRDs, tigera-operator and the Installation (the first two steps of the documented order). Wait for `calico-node` Ready and the datastore CRDs, then `kubectl apply -f calico-datastore-<ts>.yaml` (strip `resourceVersion`/`uid`/`status` if the API rejects it). Pass: `kubectl get ippools.crd.projectcalico.org` shows the prod pool `10.42.0.0/16`, IPAMBlocks/BlockAffinities present, no CRD-prune of existing objects.
3. **Vault Raft.** `vault operator raft snapshot inspect <file>` first (integrity, no unseal needed). A real restore needs a scratch single-node Vault with Raft storage **and the same seal**: the snapshot's barrier keys are wrapped by prod's AWS KMS unseal key, so the scratch Vault needs `kms:Decrypt` on that key (use the KMS identity from the bootstrap AWS credentials, exported into this one drill shell and placed on the *ephemeral* droplet only; it dies with the droplet). Then `vault operator raft snapshot restore -force <file>`; pass: a known KV path (`secret/…` metadata) reads back and the userpass/OIDC auth methods are listed. The KMS identity is the existing `vault-unseal` IAM user from the 1Password bootstrap item (`kms:Encrypt/Decrypt/DescribeKey` on that one key, checked 2026-10-03; the scratch Vault needs Encrypt to initialise, so decrypt-only would not work). It goes onto the ephemeral droplet only and dies with it; no new KMS grant is needed.
4. `scripts/burst/burst-down`; delete `drill-vars.yml`; record the date + findings in `offsite-backups.md` ("Status") and close the open-questions item.

**What the 2026-10-03 run needed beyond the steps above** (all verified; results in [`offsite-backups.md`](offsite-backups.md) "Status"):

- **Run from Frigg as the operator user** (`~/homelab` is its checkout, `. homelab-env` / the shim provides every variable). Burst droplets are reached as **`root`** (`ansible_user: root` in `inventory-burst/group_vars/all.yml`) through the Frigg ssh-agent: a non-interactive shell needs the shim sourced for `SSH_AUTH_SOCK` and `ssh -o IdentitiesOnly=yes -i "$ANSIBLE_PRIVATE_KEY_FILE"`, otherwise ssh fails with "Permission denied" or "Too many authentication failures".
- **Never put the AWS key on Frigg or the droplet.** Presign the object on the machine that holds the key (`aws s3 presign --expires-in 600` with the restore key read from 1Password into that one process), hand the URL to Frigg as a 0600 file that is deleted on use, and pass it to the droplet over ssh **stdin** (`read -r u; curl -o ... "$u"`): the droplet downloads straight from S3 (27 MB in about 1 s) and the URL never appears in an argument.
- **Calico export:** strip `^    (resourceVersion|uid|generation): ` lines and apply with `--validate=false`.
- **Vault leg:** the KMS key id is not in the 1Password item (only the access key pair); its recovery copy is `aws_kms_key_id` in `group_vars/all/vault.yml` (read with `ansible-vault view`, never printed). Stream the credentials and the script together over ssh into `bash -s` on the droplet so nothing is written to a disk; download the pinned Vault (`1.21.2`, the chart tag) from releases.hashicorp.com, run it with `storage "raft"` + `seal "awskms" {}` (key and credentials from the environment), `operator init -recovery-shares=1`, then `operator raft snapshot restore -force`; compare `vault status` cluster ID with prod's (unauthenticated).
- **Teardown:** `scripts/burst/burst-down --yes`, delete `drill-vars.yml`, check `burst-reaper --list` shows nothing.

Record the observed restore time per leg — it is the real RTO number the AIOps roadmap lacks.

## Risks / things not proven

- The whole path (Terraform apply, Debian 13 + K3s v1.36 via the role, Calico on droplets, tag-bound firewall, the reaper unit) has been validated **offline only** (`terraform validate`, ansible-lint at the CI pin, template render comparison, reaper unit logic). The first smoke test is the first real exercise.
- `data "digitalocean_vpc"` is looked up by `region` (the default VPC); if the provider rejects that form on a real plan, switch to `name = "default-ams3"`.
- A reaped-but-not-destroyed state makes the next `burst-up` try to recreate droplets Terraform still believes exist; always `burst-down` first. If `burst-down` runs right after a reap it can fail with `Error waiting for droplet to be unlocked for destroy: unexpected state 'Not Found'` (Terraform raced the already-deleted droplet): run `burst-down` again, it refreshes and finishes (seen 2026-10-03).
