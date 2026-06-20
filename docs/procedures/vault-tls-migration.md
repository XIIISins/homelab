<!-- docs/procedures/vault-tls-migration.md -->

# Vault listener TLS migration (plaintext → internal CA)

Flips Vault's single API listener from `tls_disable = 1` to TLS, served by a
cert-manager internal CA, so **no plaintext Vault API endpoint survives**.
Gated before Phase 7 (Jotunheim) so the second cluster inherits the finished
PKI pattern. Decision + motivation: [open-questions.md](../operations/open-questions.md)
("Vault listener TLS").

## Why an internal CA (not Let's Encrypt)

Vault's listener must present a cert valid for non-public names — in-cluster
Service DNS (`vault.vault.svc.cluster.local`), the headless `*.vault-internal`
(Raft join hits the API port), `127.0.0.1` (in-pod probes), and the MetalLB
VIP `10.0.20.11`. LE can't issue those, so a private CA is required. The
Traefik-fronted FQDN (`vault.niflheim.xiiisins.com`) keeps terminating on the
**public LE wildcard** — so FQDN consumers (operator CLI/TF over `VAULT_ADDR`,
Frigg) need **no** new trust. Only the three in-cluster hops do.

## Trust surface (who needs the internal CA)

| Consumer | Path | Trust mechanism |
|---|---|---|
| ESO | `https://vault.vault.svc…:8200` | `caProvider` → `vault-tls` Secret `ca.crt` |
| Traefik (FQDN backend) | → `vault-ui` Service | `BackendTLSPolicy` → `vault-ca-bundle` ConfigMap |
| Semaphore (Ansible) | `https://vault.vault.svc…:8200` | `ANSIBLE_HASHI_VAULT_CA_CERT` ← mounted `vault-ca-bundle` |
| operator CLI / TF / Frigg | FQDN (Traefik) | public LE cert — unchanged |
| break-glass `.11` direct | `https://10.0.20.11:8200` | `VAULT_CACERT`/`-tls-skip-verify` (manual) |
| MacBook Ansible+Vault | n/a | **Frigg-only now** (macOS fork-bug, no plaintext path remains) |

cert-manager only writes Secrets; Gateway API `BackendTLSPolicy` only accepts
a **ConfigMap** for the CA (no skip-verify). **trust-manager** bridges that —
it distributes the CA cert into `vault-ca-bundle` ConfigMaps in every
namespace.

## What the change set adds (this branch)

PKI (`k8s/asgard/cert-manager-config/`):
- `clusterissuer-selfsigned.yaml` — bootstrap SelfSigned issuer
- `internal-ca.yaml` — `homelab-internal-ca` Certificate (isCA, 10y, in
  `cert-manager`) + CA ClusterIssuer
- `certificate-vault.yaml` — `vault-tls` leaf (1y) in the `vault` ns, full SANs
- `trust-bundle-vault-ca.yaml` — trust-manager `Bundle` → `vault-ca-bundle`

Components / wiring:
- `k8s/asgard/infrastructure/trust-manager/` — trust-manager HelmRelease (0.23.0,
  jetstack repo) + added to `infrastructure/kustomization.yaml`
- `k8s/asgard/infrastructure/vault/helmrelease.yaml` — `global.tlsDisable:false`,
  TLS listener (`tls_cert_file`/`tls_key_file`), `server.volumes`/`volumeMounts`
  for `vault-tls`, `extraEnvironmentVars.VAULT_CACERT`
- `k8s/asgard/apps/vault-ingress/backendtlspolicy.yaml` — `BackendTLSPolicy` (v1)
- `k8s/asgard/infrastructure-config/clustersecretstore.yaml` — `https` + `caProvider`
- `terraform/semaphore/main.tf` — `ANSIBLE_HASHI_VAULT_ADDR` https + `_CA_CERT`
- `k8s/asgard/apps/semaphore/statefulset.yaml` — mount `vault-ca-bundle` at `/etc/vault-ca`
- `.config/{fish/conf.d/homelab.fish,scripts/homelab.sh}` — ansible addr → FQDN

## Pre-flight

- [ ] **Vault Raft snapshot** off-cluster (the standing backup before any
  storage/listener-touching maintenance):
  ```
  ROOT=$(op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password)
  kubectl exec -n vault vault-0 -c vault -- env VAULT_TOKEN=$ROOT \
    vault operator raft snapshot save /tmp/snap
  kubectl cp vault/vault-0:/tmp/snap ~/homelab-backups/vault/pre-tls-$(date +%F).snap -c vault
  kubectl exec -n vault vault-0 -c vault -- rm /tmp/snap
  ```
- [ ] Confirm `kubectl get autopilot state` (or `raft list-peers`) shows 3/3
  voters, `Failure Tolerance: 1` before starting.
- [ ] All apply/reconcile from the **main checkout**, not a worktree (repo rule).

## Rollout — TWO pushes (hard cutover; the listener has no http+https period)

The chicken-and-egg: the Vault HelmRelease (in `infrastructure`) mounts
`vault-tls`, but that Secret is created by `cert-manager-config`, which
`dependsOn: infrastructure` and reconciles **after** it. So PKI must land and
be verified **before** the Vault TLS flip.

### Push 1 — PKI + trust-manager only (no Vault/consumer change)

Commit *just*: `infrastructure/trust-manager/**`, the `infrastructure/kustomization.yaml`
trust-manager line, and the four `cert-manager-config/` PKI files + its
kustomization. **Do not** include the vault/ESO/Semaphore/BackendTLSPolicy/shim
changes yet.

```
flux reconcile kustomization infrastructure --with-source   # trust-manager CRD+pod
flux reconcile kustomization cert-manager-config            # issuers, cert, Bundle
```

**Verify before Push 2:**
```
kubectl get clusterissuer homelab-internal-ca           # READY=True
kubectl -n vault get secret vault-tls                   # has tls.crt/tls.key/ca.crt
kubectl -n vault get configmap vault-ca-bundle          # has key ca.crt
kubectl -n external-secrets get configmap vault-ca-bundle
kubectl -n semaphore get configmap vault-ca-bundle
# sanity: the leaf cert's ca.crt == the distributed bundle == the CA cert
kubectl -n vault get secret vault-tls -o jsonpath='{.data.ca\.crt}' | base64 -d \
  | openssl x509 -noout -subject   # subject = homelab-internal-ca
```
If the bundle key looks wrong, re-check `trust-bundle-vault-ca.yaml` source
`key:` (tls.crt vs ca.crt of the isCA Secret) — see [known-issues/vault.md].

### Push 2 — flip Vault + all consumers together

Commit the rest: vault `helmrelease.yaml`, `backendtlspolicy.yaml` (+ vault-ingress
kustomization), ESO `clustersecretstore.yaml`, Semaphore `statefulset.yaml`, shims.
`terraform/semaphore` is a separate apply (below).

```
flux reconcile kustomization infrastructure --with-source   # rolls Vault STS to TLS
flux reconcile kustomization infrastructure-config          # ESO https+caProvider
flux reconcile kustomization apps                            # BackendTLSPolicy, Semaphore STS
```

**Vault pod roll:** `global.tlsDisable:false` changes the pod template, forcing
a StatefulSet roll. With required pod-anti-affinity + 3 pods/3 workers, it goes
**one pod at a time** (no surge slot) — each terminates, restarts with TLS,
**KMS auto-unseals** (no manual unseal), rejoins Raft, readiness exec probe
(`vault status -tls-skip-verify`) passes, next proceeds. Watch:
```
kubectl -n vault get pods -w
kubectl exec -n vault vault-0 -c vault -- vault operator raft list-peers   # after roll
```
Quorum (2/3) holds throughout. If a pod sticks (e.g. cert SAN mismatch), the
roll halts with quorum intact — fix config, then `kubectl delete pod` the
stuck one. **Never** force-delete two Vault pods at once.

**Terraform (Semaphore env):** from the main checkout —
```
cd terraform/semaphore && terraform plan   # expect ANSIBLE_HASHI_VAULT_ADDR/_CA_CERT change
terraform apply
# then bounce Semaphore so it re-reads the env + picks up the CA mount:
kubectl -n semaphore rollout restart statefulset semaphore
```

**Operator shims:** pull the updated `homelab.fish`/`homelab.sh` and
`homelab-env --refresh` (only affects the now-FQDN ansible addr; `VAULT_ADDR`
already the FQDN).

## Verify end-to-end

```
# ESO still syncing (no SecretSyncedError):
kubectl get externalsecret -A | grep -iv 'SecretSynced\|True'   # expect empty
# force a resync + confirm:
kubectl -n vault annotate externalsecret <name> force-sync=$(date +%s) --overwrite

# FQDN path (LE cert at Traefik, internal CA Traefik→Vault):
curl -sS https://vault.niflheim.xiiisins.com/v1/sys/health | jq .sealed   # false

# break-glass .11 direct (internal CA):
curl -sS --cacert <(kubectl -n vault get secret vault-tls -o jsonpath='{.data.ca\.crt}' | base64 -d) \
  https://10.0.20.11:8200/v1/sys/health | jq .sealed

# Semaphore: run the asgard-drift-check template → green (Vault lookups resolve).
```

## Cert renewal (ongoing)

`vault-tls` is 1y / renewBefore 30d. cert-manager renews the Secret in place,
**but Vault does not auto-reload listener certs** — do a rolling restart to
load the new cert within the 30d grace:
```
kubectl -n vault rollout restart statefulset vault   # one-at-a-time, KMS auto-unseals
```
(The 10y CA needs no routine action.)

## Rollback

Revert Push 2 (git revert + `flux reconcile`), which restores
`tls_disable = 1`; Vault rolls back to plaintext, consumers revert to `http`.
The PKI/trust-manager from Push 1 is inert without consumers and can stay.
Worst case, restore the pre-flight Raft snapshot.

## Post-flight (after a verified flip)

- [ ] `docs/known-issues/vault.md` — replace the "Vault TLS disabled is
  deliberate" + plaintext-`.11` bullets with the TLS-enabled reality; update the
  `raft join http://…` recovery commands to `https://…` + `-ca-cert`/`VAULT_CACERT`.
- [ ] `CLAUDE.md` — line ~57 (Secrets invariant): drop "listener is `tls_disable = 1`",
  state TLS via internal CA; tick the Phase 6 gate line.
- [ ] `docs/architecture/identity-secrets.md` — same invariant flip.
- [ ] `docs/procedures/teardown-rebuild.md` + `docs/procedures/synology-storage-redesign.md`
  ("Class L") — Raft-join commands → `https://` + CA.
- [ ] `docs/operations/decisions.md` — already has the row (added with this branch).
- [ ] `docs/operations/open-questions.md` — tick the "Vault listener TLS" item.
- [ ] Forward-fit: the same `homelab-internal-ca` can later issue the Patroni
  leader cert (retires Outline's `sslmode=no-verify`) and jotunheim in-cluster TLS.
