<!-- docs/outline/procedures.md -->

# Procedures

Runbooks for the operations that are worth doing the same way every time. This section is the wiki-readable summary: it explains what each procedure is for, the order of operations, and what to watch for. The full step-by-step commands live in the repository under `docs/procedures/`, next to the code they drive.

---

## Philosophy

- **Ordered by blast radius.** Changing a document is not rebuilding a cluster. The index below runs from routine to catastrophic, and the care required rises with it.
- **Prerequisites first.** Each procedure names what must already be healthy. Several refuse to start against a degraded system.
- **One thing at a time.** Anything touching a quorum (etcd, Postgres, Vault, the Proxmox cluster) is strictly serial, and waits for 3 of 3 before moving on.
- **Persistence is part of done.** After a change that must survive a reboot (network config, sysctls, systemd units, kernel), reboot the node before declaring victory. A runtime fix can mask a broken file that bites weeks later.
- **Secrets never appear in a command line or a transcript.** Credentials are resolved inside the shell process. If one leaks, tell the owner and rotate; never auto-rotate.
- **Rebuild beats repair** when the guest is cattle, and the IaC is the recovery path.

---

## Index

| Procedure | Blast radius | Use when |
|---|---|---|
| **Landing a change** | Low | Any change to the repository |
| **Credential rotation** | Low to medium | A credential leaked, or on a schedule |
| **AppRole rotation** | Low | Vault AppRole SecretIDs, every 90 days |
| **Backups & restore drills** | Low (read-mostly) | Verifying that recovery would work |
| **K3s & Calico upgrade** | Medium | A new Kubernetes or CNI minor version |
| **Proxmox host patching** | High | Patching the hypervisors |
| **Vault recovery** | High | Raft node lost, sealed, stuck init or token lost |
| **K3s node rebuild** | High | Replacing a control plane or worker VM |
| **Teardown & rebuild** | Highest | Disaster recovery, a rebuild from the Proxmox layer up |

---

## Order of attack in an outage

1. **Is the network up?** (UCG-Ultra, DNS VIP.) Nothing else is diagnosable without it.
2. **Is Postgres reachable on the VIP?** Most apps depend on it.
3. **Is Vault unsealed?** New secret syncs and Ansible lookups need it.
4. **Is the cluster healthy?** Nodes Ready, Flux reconciling, Traefik answering.
5. Then the application layer.

See **Troubleshooting** for the symptom-first version.

---

## See also

- **Troubleshooting**: diagnosing before you act.
- **Components & interactions**: how the pieces fit.
- **Identity & secrets** (Components): the model several procedures protect.
