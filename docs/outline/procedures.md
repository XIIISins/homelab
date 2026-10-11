<!-- docs/outline/procedures.md -->

# Procedures

Runbooks written so the person on call can work through them from a terminal without having built the system. Every procedure has the same shape: **when to use it, what must be true first, numbered steps with copy-paste commands, how to verify, and how to back out.** If a step's output does not match what the runbook says to expect, stop and escalate; do not improvise.

Commands are bash/zsh unless a block says **fish**. Replace anything in `<angle brackets>`.

---

## 0. Session setup (do this first, every time)

You need three things: the repository checkout, a shell with credentials loaded (without typing any), and a way onto the hosts.

```bash
# 1. Go to the repository checkout (path differs per machine)
cd ~/homelab                      # or ~/Dev/xiiisins/homelab on the Mac
git pull --ff-only                # runbooks and playbooks must be current

# 2. Load credentials into THIS shell. Vault-backed: needs no 1Password prompt.
#    (Needs vault and jq on PATH. On the Mac: PATH="/opt/homebrew/bin:$PATH")
source "$(git rev-parse --show-toplevel)/.config/scripts/homelab.sh" \
  && vault-homelab-env >/dev/null

# 3. Prove it worked
kubectl get nodes                 # 6 nodes, all Ready
vault token lookup >/dev/null && echo "vault token ok"
echo "$ANSIBLE_PRIVATE_KEY_FILE"  # SSH identity for the fleet (on Frigg this is the PUBLIC key file: a memory-only ssh-agent holds the private half)
```

If `vault-homelab-env` fails or the cache is stale, use the warm cache, then try the operator's 1Password path:

```bash
. ~/.cache/homelab/env.sh         # operator-warmed cache. Source it; never cat it
homelab-env --refresh             # 1Password-backed refresh (needs `op` signed in)
```

The cache lives three hours. After anyone patches a secret, refresh it on every machine that uses it: `vault-homelab-env --refresh >/dev/null 2>&1`.

**Reaching hosts**

```bash
# LXCs and VMs: key-only user `ansible`, passwordless sudo. Name or IP both work.
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@hugin.niflheim.xiiisins.com

# Proxmox hosts: root
ssh root@urd.niflheim.xiiisins.com        # also verd, skuld

# Any LXC, even when its network or SSH is broken: via its Proxmox host
ssh root@urd.niflheim.xiiisins.com 'pct exec 1130 -- patronictl -c /etc/patroni/patroni.yml list'

# The control node (Frigg) runs everything the Mac cannot (see "Where to run")
ssh ansible@frigg.niflheim.xiiisins.com
sudo -iu ghost
. /usr/local/bin/homelab-env && homelab-env
cd ~/homelab && git -c core.fileMode=false pull --ff-only
```

Break-glass SSH is the `recovery` user (key in 1Password, `[Infra] - recovery - SSH - Private key`).

**Where to run what**

| Task | Run from | Why |
|---|---|---|
| `kubectl`, `flux`, read-only checks | Anywhere with the shim | |
| `ansible-playbook` against LXCs/VMs | **Frigg** (or the Mac for roles with no Vault lookups) | The macOS fork bug crashes Ansible workers on the Mac |
| `terraform apply` | The **main** checkout only, never a worktree | State is shared; apply discipline |
| Proxmox host patching | The Mac | The orchestrator must not live on a host it reboots |
| Anything needing the Vault **root** token | Operator only | See *Escalate* below |

---

## Rules that apply to every procedure

1. **No credential literals** in a command, a chat message or a ticket. Resolve inside the shell: `$(vault kv get -field=… secret/…)` or `$(op read 'op://…')`. If one leaks, tell the owner and recommend rotation; do not rotate on your own.
2. **Never `kubectl apply` or `kubectl edit` against production.** Flux reconciles from Git. Fix Git, then `flux reconcile`. The only direct mutations allowed here are restarts, scaling for a documented recovery, deleting a broken pod, and force-syncing a secret.
3. **One `ansible-playbook` at a time**, across everyone. If another run may be in progress, ask. Check Semaphore for a running task first (see *Monitoring & automation* troubleshooting).
4. **Only one Proxmox host, one control plane, one quorum member at a time.** Wait for 3 of 3 before touching the next.
5. **Never replace Calico's addon files by hand.** Use the upgrade playbook.
6. **After a change that must survive a reboot, reboot the node** and re-check.
7. **Read before you write.** Each runbook starts with read-only checks; trust them.

### Escalate to the owner when

- Two members of any trio (etcd, Patroni, Vault, Proxmox) look unhealthy at once.
- The Vault root token or the AWS KMS unseal credentials are needed.
- Data would be deleted (a PVC, a LUN, a datastore) or a restore is the next step.
- A step's output differs from the runbook and you cannot explain it.
- A secret appeared in a transcript or log.

---

## Index

Ordered by blast radius. Start at the top; do not skip to the bottom.

| Procedure | Blast radius | Use when |
|---|---|---|
| **Everyday operations** | Low | Restart a workload, force a secret sync, run a playbook, look at logs |
| **Landing a change** | Low | Any change to the repository |
| **Postgres operations** | Medium | Check the cluster, switchover, re-seed a replica, add or restore a database |
| **Secret mirroring** | Low | Copy a Vault secret to 1Password, or back |
| **Credential rotation** | Low to medium | A credential leaked or is due |
| **AppRole rotation** | Low to medium | AppRole SecretIDs, every 90 days |
| **Backups & restore drills** | Low (read-mostly) | Verify that recovery would work; run a restore |
| **K3s & Calico upgrade** | Medium | A new Kubernetes or CNI minor |
| **K3s node rebuild** | High | Replace one control plane or worker VM |
| **Proxmox host patching** | High | Patch the hypervisors |
| **Vault recovery** | High | Raft node lost, sealed, stuck init, token trouble |
| **Teardown & rebuild** | Highest | Disaster recovery from the Proxmox layer up |

## Order of attack in an outage

1. **Network and DNS.** UCG-Ultra reachable? `dig @10.0.10.200 pbs.niflheim.xiiisins.com`.
2. **Postgres on the VIP** (`10.0.10.210`). Many apps die together when it has no leader.
3. **Vault unsealed**, then the cluster (nodes Ready, Flux reconciling, Traefik answering).
4. Then individual applications.

The **Troubleshooting** section has the one-shot health sweep and a symptom index.

---

## See also

- **Troubleshooting**: diagnose first, then pick the procedure.
- **Components & interactions**: how the pieces connect.
