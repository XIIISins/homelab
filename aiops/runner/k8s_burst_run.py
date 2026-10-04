#!/usr/bin/env python3
"""10h burst-cluster test for `k8s/` PRs, slice 2: apply the burst copy of the tree to a burst K3s and check it.

Design + fidelity rules: docs/operations/10h-k8s-burst-test.md. Planning/rendering logic: k8s_burst_plan.py (unit-tested, no network).
Procedure: docs/procedures/k8s-burst-test.md. Entry point for humans and the runner: scripts/burst/k8s-pr-test (which also builds and
ALWAYS tears down the cluster); this module only talks to the cluster named by --kubeconfig.

SAFETY, in this order:
  1. the cluster must be a burst cluster: every node named burst-N, or nothing is applied (guard());
  2. what is applied is a rendered COPY of k8s/asgard (Let's Encrypt, Cloudflare, KMS, MetalLB, Synology removed; see the plan module);
  3. every secret value is random and made here: nothing is read from prod and no value is printed or written to the summary (only names).

Phases (each timed, each recorded in the summary): guard, render, flux, infra, certs, vault, seed, config, apps, wait, checks."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import k8s_burst_plan as plan  # noqa: E402

# Mirrors terraform/vault/main.tf (the `eso` policy and role). A unit test holds the two equal.
ESO_POLICY = """path "secret/data/*" {
  capabilities = ["read"]
}
# Operator identity keys (operator-ssh.tf) are for the MacBook and Frigg only.
# An explicit deny beats the wildcard read above.
path "secret/data/operator/*" {
  capabilities = ["deny"]
}
path "secret/metadata/operator/*" {
  capabilities = ["deny"]
}
"""
VAULT_ENV = ["VAULT_ADDR=https://127.0.0.1:8200", "VAULT_CACERT=/vault/userconfig/vault-tls/ca.crt"]


class Fail(Exception):
    pass


class Kube:
    """kubectl with a pinned kubeconfig. Output is returned, never echoed: the only things logged are phase names and counts."""

    def __init__(self, kubeconfig: str):
        self.env = {**os.environ, "KUBECONFIG": kubeconfig}

    def run(self, args: list, stdin: str | None = None, timeout: int = 300, check: bool = False) -> tuple[int, str]:
        p = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, env=self.env, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        if check and p.returncode != 0:
            raise Fail(f"kubectl {' '.join(args[:3])}: {out[-300:]}")
        return p.returncode, out

    def json(self, args: list, timeout: int = 120):
        rc, out = self.run([*args, "-o", "json"], timeout=timeout)
        try:
            return json.loads(out) if rc == 0 else {}
        except ValueError:
            return {}


def guard(kube: Kube) -> list[str]:
    """Refuse anything that is not a burst cluster. Returns the node names."""
    nodes = [n["metadata"]["name"] for n in kube.json(["get", "nodes"]).get("items", [])]
    if not nodes or not all(re.fullmatch(r"burst-[0-9]+", n) for n in nodes):
        raise Fail(f"refusing: this is not a burst cluster (nodes: {nodes[:6]})")
    return nodes


def apply_with_retry(kube: Kube, manifest: str, what: str, attempts: int = 45, pause: int = 20) -> None:
    """Server-side apply, repeated until every kind exists: CRDs arrive from HelmReleases while the rest is already being applied (what Flux's
    retry loop does in prod)."""
    last = ""
    for i in range(attempts):
        rc, out = kube.run(["apply", "--server-side", "--force-conflicts", "-f", "-"], stdin=manifest, timeout=300)
        if rc == 0:
            return
        last = out
        if not re.search(r"no matches for kind|ensure CRDs are installed|not found|failed calling webhook|connection refused|context deadline", out):
            break
        time.sleep(pause)
    raise Fail(f"apply {what}: " + " | ".join(l for l in last.splitlines() if re.search(r"error|Error|invalid|denied|forbidden", l))[:600])


def kustomize(kube: Kube, path: Path) -> str:
    rc, out = kube.run(["kustomize", str(path)], timeout=120)
    if rc != 0:
        raise Fail(f"kustomize {path.name}: {out[-300:]}")
    return out


def wait(kube: Kube, args: list, timeout_s: int) -> bool:
    rc, _ = kube.run(["wait", *args, f"--timeout={timeout_s}s"], timeout=timeout_s + 30)
    return rc == 0


def vault_exec(kube: Kube, token: str | None, args: list, stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    env = VAULT_ENV + ([f"VAULT_TOKEN={token}"] if token else [])
    return kube.run(["exec", "-i", "-n", "vault", "vault-0", "--", "env", *env, "vault", *args], stdin=stdin, timeout=timeout)


def first_json(out: str) -> dict:
    """The first JSON object in a command's combined stdout+stderr (a kubectl exec appends notices such as 'Defaulted container' after it)."""
    i = out.find("{")
    if i < 0:
        raise Fail("no JSON in the command output")
    obj, _ = json.JSONDecoder().raw_decode(out[i:])
    return obj


def bootstrap_vault(kube: Kube, seeds: dict) -> dict:
    """Init (1 share, 1 threshold), unseal, the same engine/auth/policy/role as terraform/vault/main.tf, then one random secret per
    ExternalSecret reference. Returns {"seeded_paths": n, "seeded_properties": n}; no value ever leaves this function."""
    if not wait(kube, ["pod/vault-0", "-n", "vault", "--for=condition=PodScheduled"], 600):
        raise Fail("vault-0 was never scheduled")
    for _ in range(60):   # the container starts once vault-tls exists; `status` answers (sealed) once the listener is up
        rc, out = vault_exec(kube, None, ["status", "-format=json"])
        if "{" in out and '"initialized"' in out:
            break
        time.sleep(10)
    else:
        raise Fail("vault-0 never answered `vault status` (is vault-tls issued?)")
    st = first_json(out)
    if not st.get("initialized"):
        rc, out = vault_exec(kube, None, ["operator", "init", "-key-shares=1", "-key-threshold=1", "-format=json"])
        if rc != 0:
            raise Fail("vault operator init failed")
        init = first_json(out)
        key, token = init["unseal_keys_b64"][0], init["root_token"]
    else:
        raise Fail("vault was already initialised: refusing to reuse unknown state")
    rc, _ = vault_exec(kube, None, ["operator", "unseal", key])
    if rc != 0:
        raise Fail("vault unseal failed")
    steps = [
        (["secrets", "enable", "-path=secret", "-version=2", "kv"], None),
        (["auth", "enable", "kubernetes"], None),
        (["write", "auth/kubernetes/config", "kubernetes_host=https://kubernetes.default.svc", "disable_iss_validation=true"], None),
        (["policy", "write", "eso", "-"], ESO_POLICY),
        (["write", "auth/kubernetes/role/eso", "bound_service_account_names=external-secrets", "bound_service_account_namespaces=external-secrets",
          "token_policies=eso", "token_ttl=3600"], None),
    ]
    for args, stdin in steps:
        rc, out = vault_exec(kube, token, args, stdin=stdin)
        if rc != 0:
            raise Fail(f"vault {' '.join(args[:2])}: {out[-200:]}")
    props = 0
    for path, names in seeds.items():
        payload = json.dumps({n: random_value(n) for n in names})
        rc, out = vault_exec(kube, token, ["kv", "put", f"secret/{path}", "-"], stdin=payload)
        if rc != 0:
            raise Fail(f"seed {path}: {out[-200:]}")
        props += len(names)
    return {"seeded_paths": len(seeds), "seeded_properties": props}


def random_value(prop: str) -> str:
    """A random value shaped like what the property name suggests. Names only decide the SHAPE; nothing here is a real credential."""
    if prop.endswith("_json") or prop == "json":
        return json.dumps({"1": secrets.token_urlsafe(24)})
    if "key" in prop and "pem" in prop:
        return "-----BEGIN PRIVATE KEY-----\n" + secrets.token_urlsafe(48) + "\n-----END PRIVATE KEY-----\n"
    return secrets.token_urlsafe(32)


def collect(kube: Kube) -> dict:
    """What a green burst cluster looks like, as names and booleans only."""
    out: dict = {"helmreleases": [], "externalsecrets": [], "workloads": [], "pods_unhealthy": []}
    for hr in kube.json(["get", "helmreleases.helm.toolkit.fluxcd.io", "-A"]).get("items", []):
        conds = {c["type"]: c for c in hr.get("status", {}).get("conditions", [])}
        ready = conds.get("Ready", {})
        out["helmreleases"].append({"name": f"{hr['metadata']['namespace']}/{hr['metadata']['name']}", "ready": ready.get("status") == "True",
                                    "message": (ready.get("message") or "")[:160]})
    for es in kube.json(["get", "externalsecrets.external-secrets.io", "-A"]).get("items", []):
        conds = {c["type"]: c for c in es.get("status", {}).get("conditions", [])}
        out["externalsecrets"].append({"name": f"{es['metadata']['namespace']}/{es['metadata']['name']}", "ready": conds.get("Ready", {}).get("status") == "True",
                                       "message": (conds.get("Ready", {}).get("message") or "")[:160]})
    for kind in ("deployments", "statefulsets", "daemonsets"):
        for w in kube.json(["get", kind, "-A"]).get("items", []):
            st, spec = w.get("status", {}), w.get("spec", {})
            want = spec.get("replicas", st.get("desiredNumberScheduled", 1)) if kind != "daemonsets" else st.get("desiredNumberScheduled", 0)
            have = st.get("readyReplicas", st.get("numberReady", 0)) or 0
            out["workloads"].append({"name": f"{w['metadata']['namespace']}/{w['metadata']['name']}", "kind": kind, "want": want or 0, "ready": have})
    for p in kube.json(["get", "pods", "-A"]).get("items", []):
        phase = p.get("status", {}).get("phase")
        if phase in ("Running", "Succeeded"):
            cs = p.get("status", {}).get("containerStatuses", [])
            waiting = [c.get("state", {}).get("waiting", {}).get("reason") for c in cs if c.get("state", {}).get("waiting")]
            if not waiting:
                continue
        else:
            waiting = [phase]
        unsched = next((c.get("message") for c in p.get("status", {}).get("conditions", []) if c.get("type") == "PodScheduled" and c.get("status") == "False"), "")
        out["pods_unhealthy"].append({"name": f"{p['metadata']['namespace']}/{p['metadata']['name']}", "why": sorted({w for w in waiting if w})[:3],
                                      "detail": (unsched or "")[:240]})
    return out


def diagnostics(kube: Kube) -> dict:
    """What a human needs after a failed run, captured BEFORE the cluster is destroyed: HelmRelease and source conditions, unhealthy pods, the last
    warning events. Names, reasons and messages only (messages from controllers; no secret values are ever in them)."""
    d: dict = {"helmreleases": [], "sources": [], "events": [], "pods": []}
    for kind, key in (("helmreleases.helm.toolkit.fluxcd.io", "helmreleases"), ("helmrepositories.source.toolkit.fluxcd.io", "sources"),
                      ("helmcharts.source.toolkit.fluxcd.io", "sources")):
        for o in kube.json(["get", kind, "-A"]).get("items", []):
            conds = o.get("status", {}).get("conditions", [])
            ready = next((c for c in conds if c.get("type") == "Ready"), {})
            d[key].append({"name": f"{o['metadata'].get('namespace', '')}/{o['metadata']['name']}", "ready": ready.get("status"), "reason": ready.get("reason"),
                           "message": (ready.get("message") or "")[:300]})
    ev = kube.json(["get", "events", "-A", "--field-selector", "type=Warning"]).get("items", [])
    for e in sorted(ev, key=lambda x: x.get("lastTimestamp") or x.get("eventTime") or "")[-25:]:
        d["events"].append({"object": f"{e['involvedObject'].get('namespace', '')}/{e['involvedObject'].get('name', '')}", "reason": e.get("reason"),
                            "message": (e.get("message") or "")[:200]})
    d["pods"] = collect(kube)["pods_unhealthy"][:30]
    return d


def verdict(c: dict, expected_not_ready: set) -> dict:
    """pass = every HelmRelease Ready, every ExternalSecret Ready, every workload fully ready, except names listed in `expected_not_ready`
    (components that depend on something the burst cluster cannot have, e.g. NetBox's Postgres VIP). Those are reported, never hidden."""
    bad = []
    for kind in ("helmreleases", "externalsecrets"):
        bad += [f"{kind}: {x['name']} {x['message']}".strip() for x in c[kind] if not x["ready"] and x["name"] not in expected_not_ready]
    bad += [f"workload: {w['name']} {w['ready']}/{w['want']}" for w in c["workloads"] if w["ready"] < w["want"] and w["name"] not in expected_not_ready]
    down = {x["name"] for k in ("helmreleases", "externalsecrets") for x in c[k] if not x["ready"]}
    down |= {w["name"] for w in c["workloads"] if w["ready"] < w["want"]}
    return {"passed": not bad, "problems": bad, "waived": sorted(down & expected_not_ready)}


def markdown(summary: dict) -> str:
    v, r = summary["verdict"], summary["render"]
    lines = [f"**Burst-cluster test: {'PASSED' if v['passed'] else 'FAILED'}** (commit `{summary.get('commit', '?')[:12]}`, {summary['seconds']} s, {len(summary['nodes'])} burst nodes)", ""]
    lines += ["Installed: the platform core (" + ", ".join(plan.CORE) + ")" + (f" plus {', '.join(summary['only'])}" if summary["only"] else "") + ".",
              "Not installed here (burst cannot run them): " + (", ".join(sorted(r["skipped"])) or "none") + ".",
              "Differences from asgard: Vault single-node/Shamir with random seeds (" + f"{summary['vault'].get('seeded_paths', 0)} paths), "
              "Let's Encrypt replaced by the internal CA, asgard-only storage classes -> local-path."]
    if summary.get("diagnostics") and not summary["diagnostics"].get("error"):
        dg = summary["diagnostics"]
        bad = [h for h in dg["helmreleases"] if h["ready"] != "True"][:8]
        lines += ["", "Cluster state at failure:"] + [f"- HelmRelease {h['name']}: {h['reason']} {h['message']}"[:220] for h in bad]
        lines += [f"- source {x['name']}: {x['reason']} {x['message']}"[:220] for x in dg["sources"] if x["ready"] != "True"][:6]
        lines += [f"- pod {p['name']}: {','.join(p['why'])} {p.get('detail', '')}"[:260] for p in dg["pods"][:6]]
        lines += [f"- event {e['object']}: {e['reason']} {e['message']}"[:220] for e in dg["events"][-6:]]
    if v["problems"]:
        lines += ["", "Problems:"] + [f"- {p}" for p in v["problems"][:20]]
    if v["waived"]:
        why = summary.get("waiver_reasons", {})
        lines += ["", "Expected not ready on a burst cluster (listed, not hidden):"] + [f"- {n}: {why.get(n, 'depends on something outside the cluster')}" for n in v["waived"]]
    lines += ["", "Phases: " + ", ".join(f"{k} {s:.0f}s" for k, s in summary["phases"].items())]
    return "\n".join(lines) + "\n"


def offline_gate(repo: str, work: Path, only: list, summary: dict) -> tuple[dict, list]:
    """Everything that can be decided without a cluster (so a bad PR fails in seconds, before any droplet exists): render the burst copy, check
    that every Vault path an ExternalSecret reads is in the committed inventory, and that every directory Flux would reconcile still builds
    with `kubectl kustomize`. Returns (the seed plan, the problems)."""
    summary["render"] = plan.render_tree(repo, work, only=only)
    tree = work / "k8s" / "asgard"
    seeds = plan.seed_plan(plan.scan(work))
    problems: list = []
    unknown = plan.check_paths(seeds, plan_known_paths(Path(repo)))
    summary["unknown_vault_paths"] = unknown
    if unknown:
        problems.append("ExternalSecrets read Vault paths that no Terraform module or mirror-map entry declares: " + ", ".join(unknown))
    for d in ("infrastructure", "infrastructure-config", "cert-manager-config", "gateway-config", "apps"):
        if d == "apps" and not plan.has_resources(tree / "apps" / "kustomization.yaml"):
            continue
        p = subprocess.run(["kubectl", "kustomize", str(tree / d)], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            problems.append(f"{d} does not build: " + " ".join(p.stderr.split())[:240])
    return seeds, problems


def run(args, summary: dict, kube: Kube) -> dict:
    t0 = time.time()
    phases: dict = summary.setdefault("phases", {})
    summary.update({"only": args.only or [], "commit": args.commit or ""})

    def phase(name):
        class _P:
            def __enter__(self_):
                self_.t = time.time()
                print(f"[k8s-burst] {name} ...", flush=True)

            def __exit__(self_, *a):
                phases[name] = time.time() - self_.t
        return _P()

    with phase("guard"):
        summary["nodes"] = guard(kube)
    work = Path(args.work)
    with phase("render"):
        seeds, problems = offline_gate(args.repo, work, args.only or [], summary)
        tree = work / "k8s" / "asgard"
        if problems:
            raise Fail("offline gate: " + "; ".join(problems))
    with phase("flux"):
        kube.run(["apply", "--server-side", "--force-conflicts", "-f", str(tree / "flux-system" / "flux-system" / "gotk-components.yaml")], check=True, timeout=300)
        if not wait(kube, ["deploy", "--all", "-n", "flux-system", "--for=condition=available"], 300):
            raise Fail("flux controllers did not become available")
    with phase("infra"):
        apply_with_retry(kube, kustomize(kube, tree / "infrastructure"), "infrastructure")
        if not wait(kube, ["helmrelease/cert-manager", "-n", "cert-manager", "--for=condition=Ready"], 900):
            raise Fail("cert-manager HelmRelease never became Ready")
    with phase("certs"):
        apply_with_retry(kube, kustomize(kube, tree / "cert-manager-config"), "cert-manager-config")
    with phase("vault"):
        summary["vault"] = bootstrap_vault(kube, seeds)
    with phase("config"):
        apply_with_retry(kube, kustomize(kube, tree / "infrastructure-config"), "infrastructure-config")
        apply_with_retry(kube, kustomize(kube, tree / "gateway-config"), "gateway-config")
    with phase("apps"):
        if plan.has_resources(tree / "apps" / "kustomization.yaml"):    # a core-only run installs no apps: an empty kustomization is an error
            apply_with_retry(kube, kustomize(kube, tree / "apps"), "apps")
    with phase("wait"):
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            c = collect(kube)
            if all(x["ready"] for x in c["helmreleases"]) and all(x["ready"] for x in c["externalsecrets"]):
                break
            time.sleep(20)
    with phase("checks"):
        c = collect(kube)
        summary["checks"] = {k: len(v) for k, v in c.items()}
        summary["verdict"] = verdict(c, expected_not_ready(args.only or []))
        summary["waiver_reasons"] = waiver_reasons(args.only or [])
        summary["details"] = c
        if not summary["verdict"]["passed"]:
            summary["diagnostics"] = diagnostics(kube)    # before the cluster is destroyed
    summary["seconds"] = int(time.time() - t0)
    return summary


def expected_not_ready(only: list) -> set:
    """Names that cannot be ready on a burst cluster because they depend on something outside it (filled from baseline runs)."""
    names = set(EXPECTED_NOT_READY.get("*", {}))
    for o in only:
        names |= set(EXPECTED_NOT_READY.get(o, {}))
    return names


def waiver_reasons(only: list) -> dict:
    out = dict(EXPECTED_NOT_READY.get("*", {}))
    for o in only:
        out.update(EXPECTED_NOT_READY.get(o, {}))
    return out


# {component: {workload/object name: why it cannot be ready on a burst cluster}}. Added only from a baseline run on unchanged main, with the reason.
EXPECTED_NOT_READY: dict = {
    "startpage": {"startpage/startpage": "its init container clones a PRIVATE GitHub repo with a deploy key; the burst Vault holds a random key (2026-10-04 baseline)"},
}


def plan_known_paths(repo: Path) -> set:
    """The committed inventory of Vault KV paths under k8s/: every `vault_path` in scripts/secrets/mirror-map.toml (the map that mirrors EVERY Vault secret
    to 1Password, so it also covers operator-seeded ones) plus every `name = "k8s/..."` a Terraform module declares."""
    known: set = set()
    mm = repo / "scripts" / "secrets" / "mirror-map.toml"
    if mm.exists():
        known |= set(re.findall(r'^\s*vault_path\s*=\s*"(k8s/[^"]+)"', mm.read_text(), flags=re.M))
    for f in (repo / "terraform").rglob("*.tf"):
        known |= set(re.findall(r'^\s*name\s*=\s*"(k8s/[^"]+)"', f.read_text(), flags=re.M))
    return known


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="checkout of the commit under test")
    ap.add_argument("--work", required=True, help="scratch dir for the rendered copy")
    ap.add_argument("--kubeconfig", default="", help="the burst cluster's kubeconfig (not needed with --offline)")
    ap.add_argument("--offline", action="store_true", help="run only the gates that need no cluster (render, Vault path inventory, kustomize builds), then exit")
    ap.add_argument("--only", nargs="*", default=[], help="components to install besides the core (apps/<x> or infrastructure/<x>)")
    ap.add_argument("--commit", default="")
    ap.add_argument("--out", required=True, help="directory for summary.json and summary.md")
    ap.add_argument("--timeout", type=int, default=1500, help="seconds to wait for HelmReleases and ExternalSecrets")
    a = ap.parse_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    summary: dict = {"nodes": [], "render": {"skipped": {}}, "vault": {}, "seconds": 0}
    if a.offline:
        t0 = time.time()
        _, problems = offline_gate(a.repo, Path(a.work), a.only or [], summary)
        summary.update({"verdict": {"passed": not problems, "problems": problems, "waived": []}, "only": a.only, "commit": a.commit, "phases": {},
                        "seconds": int(time.time() - t0)})
        (Path(a.out) / "offline.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
        print("offline gate: " + ("passed" if not problems else "FAILED\n- " + "\n- ".join(problems)))
        return 0 if not problems else 1
    if not a.kubeconfig:
        ap.error("--kubeconfig is required unless --offline")
    kube = Kube(a.kubeconfig)
    try:
        run(a, summary, kube)
    except Fail as e:
        summary["verdict"] = {"passed": False, "problems": [str(e)], "waived": []}
        summary.update({"only": a.only, "commit": a.commit, "failed_phase": list(summary.get("phases", {}))[-1:] or ["guard"]})
        try:
            summary["diagnostics"] = diagnostics(kube)
        except Exception as ex:  # noqa: BLE001 - diagnostics are best effort
            summary["diagnostics"] = {"error": type(ex).__name__}
    (Path(a.out) / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (Path(a.out) / "summary.md").write_text(markdown(summary))
    print(markdown(summary))
    return 0 if summary["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
