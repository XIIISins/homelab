#!/usr/bin/env python3
"""Copy the Toolbelt's read-only Kubernetes credential into Vault and prove the RBAC (Phase 10d2).

    (homelab env with an admin KUBECONFIG + a Vault identity that can write secret/ansible/aiops/*)
    python3 aiops/tools/mint_kube_ro.py

The credential is the `aiops-readonly-token` Secret in namespace `aiops` (k8s/asgard/infrastructure/aiops-readonly, applied by
Flux): a ServiceAccount token the control plane fills in. This script reads it ONCE with the admin kubeconfig and writes
{token, ca, servers} to `secret/ansible/aiops/kube-token`, which the Frigg root loader copies to the Toolbelt as
creds/kube.json. It then proves the ClusterRole holds by building a temporary kubeconfig (0600, deleted) from the NEW token and
asking `kubectl auth can-i`: the diagnostic reads must be allowed and every secret / write / exec / proxy must be denied.
Only outcomes are printed. Idempotent: re-running refreshes the Vault copy from the Secret. Revoke by deleting the Secret.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

VAULT_PATH = "secret/ansible/aiops/kube-token"
NS, SECRET = "aiops", "aiops-readonly-token"
SA = f"system:serviceaccount:{NS}:aiops-readonly"

# (description, can-i arguments, expected "yes"/"no")
MATRIX = [
    ("list pods cluster-wide", ["list", "pods", "--all-namespaces"], "yes"),
    ("read pod logs", ["get", "pods/log", "-n", "kube-system"], "yes"),
    ("list nodes", ["list", "nodes"], "yes"),
    ("read events", ["list", "events", "--all-namespaces"], "yes"),
    ("read Flux HelmReleases", ["list", "helmreleases.helm.toolkit.fluxcd.io", "--all-namespaces"], "yes"),
    ("read Flux Kustomizations", ["list", "kustomizations.kustomize.toolkit.fluxcd.io", "--all-namespaces"], "yes"),
    ("read deployments", ["list", "deployments.apps", "--all-namespaces"], "yes"),
    ("get secrets", ["get", "secrets", "--all-namespaces"], "no"),
    ("list secrets", ["list", "secrets", "--all-namespaces"], "no"),
    ("read configmaps", ["get", "configmaps", "--all-namespaces"], "no"),
    ("delete pods", ["delete", "pods", "--all-namespaces"], "no"),
    ("create pods", ["create", "pods", "-n", "default"], "no"),
    ("exec into pods", ["create", "pods/exec", "-n", "default"], "no"),
    ("attach to pods", ["create", "pods/attach", "-n", "default"], "no"),
    ("proxy through nodes", ["get", "nodes/proxy"], "no"),
    ("proxy through services", ["get", "services/proxy", "-n", "default"], "no"),
    ("mint ServiceAccount tokens", ["create", "serviceaccounts/token", "-n", NS], "no"),
    ("patch a Flux HelmRelease", ["patch", "helmreleases.helm.toolkit.fluxcd.io", "-n", "flux-system"], "no"),
    ("edit RBAC", ["create", "clusterrolebindings.rbac.authorization.k8s.io"], "no"),
]


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(args[:3])} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def main() -> int:
    sec = json.loads(sh("kubectl", "-n", NS, "get", "secret", SECRET, "-o", "json"))
    data = sec.get("data") or {}
    if not data.get("token"):
        sys.exit("the token Secret has no token yet (has Flux applied k8s/asgard/infrastructure/aiops-readonly?)")
    token = base64.b64decode(data["token"]).decode()
    ca = base64.b64decode(data["ca.crt"]).decode()
    ips = sh("kubectl", "get", "nodes", "-l", "node-role.kubernetes.io/control-plane", "-o",
             "jsonpath={.items[*].status.addresses[?(@.type=='InternalIP')].address}").split()
    servers = [f"https://{ip}:6443" for ip in ips]
    if not servers:
        sys.exit("found no control-plane node addresses")
    print(f"token Secret read; {len(servers)} API server(s): {', '.join(servers)}")

    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"token": token, "ca": ca, "servers": servers}, fh)
        r = subprocess.run(["vault", "kv", "put", VAULT_PATH, f"@{path}"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("vault write failed (can this Vault identity write secret/ansible/aiops/*?)")
        print(f"stored at {VAULT_PATH}")
    finally:
        os.remove(path)

    # Prove it with the NEW token (a temporary kubeconfig, never a command-line token)
    kc = {"apiVersion": "v1", "kind": "Config", "current-context": "aiops",
          "clusters": [{"name": "c", "cluster": {"server": servers[0], "certificate-authority-data": base64.b64encode(ca.encode()).decode()}}],
          "users": [{"name": "u", "user": {"token": token}}], "contexts": [{"name": "aiops", "context": {"cluster": "c", "user": "u"}}]}
    fd, kpath = tempfile.mkstemp(suffix=".kubeconfig")
    ok = True
    try:
        os.chmod(kpath, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(kc, fh)
        who = sh("kubectl", "--kubeconfig", kpath, "auth", "whoami", "-o", "jsonpath={.status.userInfo.username}", check=False)
        print(f"authenticates as: {who or '(whoami unavailable)'}")
        for label, args, want in MATRIX:
            r = subprocess.run(["kubectl", "--kubeconfig", kpath, "auth", "can-i", *args], capture_output=True, text=True)
            got = r.stdout.strip().split()[0] if r.stdout.strip() else "error"
            good = got == want
            ok &= good
            print(f"  {'OK ' if good else 'BAD'} {label}: {got} (want {want})")
    finally:
        os.remove(kpath)
    print("read-only proof:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
