"""10h burst-cluster test for `k8s/` PRs, slice 1: the pure planning logic (design: docs/operations/10h-k8s-burst-test.md).

No network, no subprocess, no Kubernetes. Given a checkout of the repo (or a tree of YAML) it answers:

  scan(root)               every ExternalSecret and SealedSecret under k8s/ with the Vault paths / key names they need
  seed_plan(scan)          {vault path: [property, ...]} the throwaway Vault must hold so every ExternalSecret resolves
  check_paths(plan, known) the seeded paths that are NOT in the committed Vault inventory (a renamed or invented key)
  sealed_stubs(scan)       the plain Secrets that replace SealedSecrets (same name/namespace/keys, values filled at run time)
  components(changed)      which Flux component directories a PR's changed paths touch, and which of them the burst cluster skips
  storage_remap(text)      the storage classes that cannot exist on a burst cluster, rewritten to local-path

Nothing here generates or reads a secret VALUE: the runner fills seeds with random bytes at run time and never prints them."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Components the burst cluster cannot run (no L2 VLAN, no Munin, no tunnel credentials, no cluster sealing key) and why.
SKIPPED = {
    "metallb": "no L2 VLAN on the burst substrate",
    "metallb-config": "no L2 VLAN on the burst substrate",
    "synology-csi": "no Synology/Munin; PVCs fall back to local-path",
    "synology-csi-config": "no Synology/Munin; PVCs fall back to local-path",
    "csi-driver-nfs": "no Munin NFS share; PVCs fall back to local-path",
    "cloudflared": "no tunnel credentials",
    "sealed-secrets": "SealedSecrets are replaced by plain Secrets with random values",
    "vault-config": "the AWS-KMS unseal secret does not exist; the throwaway Vault is Shamir-unsealed by the harness",
}
# Storage classes that exist only in asgard, and what they become on the burst cluster.
STORAGE_REMAP = {"synology-csi-iscsi-retain-vol2": "local-path", "nfs-client": "local-path"}

_COMPONENT = re.compile(r"^k8s/asgard/(?:(infrastructure|apps)/([^/]+)|([a-z0-9-]+-config))(?:/|$)")


def _key(k: str) -> str:
    """ESO's Vault provider accepts a key with or without the KV mount (`secret/k8s/x` and `k8s/x` are the same secret, the provider inserts
    `data/` after the mount), and cert-manager-config uses the long form. Normalise to the short form so one path is one entry."""
    k = str(k).lstrip("/")
    return k[len("secret/"):] if k.startswith("secret/") else k


def _docs(path: Path):
    try:
        for d in yaml.safe_load_all(path.read_text()):
            if isinstance(d, dict):
                yield d
    except (yaml.YAMLError, UnicodeDecodeError):
        return


def scan(root: str | Path) -> dict:
    """{"externalsecrets": [...], "sealedsecrets": [...]} from every *.yaml / *.yml under <root>/k8s."""
    root = Path(root)
    ext, sealed = [], []
    for p in sorted((root / "k8s").rglob("*.y*ml")):
        if p.suffix not in (".yaml", ".yml"):
            continue
        rel = str(p.relative_to(root))
        for d in _docs(p):
            kind, md = d.get("kind"), d.get("metadata") or {}
            if kind == "ExternalSecret":
                refs = []
                spec = d.get("spec") or {}
                for item in spec.get("data") or []:
                    rr = (item or {}).get("remoteRef") or {}
                    if rr.get("key"):
                        refs.append({"key": _key(rr["key"]), "property": rr.get("property")})
                for item in spec.get("dataFrom") or []:
                    ex = (item or {}).get("extract") or {}
                    if ex.get("key"):
                        refs.append({"key": _key(ex["key"]), "property": None})   # the whole secret: any property set will do
                ext.append({"file": rel, "namespace": md.get("namespace", ""), "name": md.get("name", ""), "refs": refs,
                            "store": ((spec.get("secretStoreRef") or {}).get("name"))})
            elif kind == "SealedSecret":
                spec = d.get("spec") or {}
                tmpl = (spec.get("template") or {}).get("metadata") or {}
                sealed.append({"file": rel, "namespace": md.get("namespace", "") or tmpl.get("namespace", ""),
                               "name": tmpl.get("name") or md.get("name", ""), "keys": sorted((spec.get("encryptedData") or {}).keys()),
                               "type": (spec.get("template") or {}).get("type")})
    return {"externalsecrets": ext, "sealedsecrets": sealed}


def seed_plan(scanned: dict, store: str = "vault") -> dict:
    """{vault path (under the `secret/` KV v2 mount): sorted properties}. A path read whole (`dataFrom.extract`) gets one placeholder
    property so the secret exists. Only ExternalSecrets that use the `vault` store are seeded."""
    plan: dict[str, set] = {}
    for es in scanned["externalsecrets"]:
        if es.get("store") != store:
            continue
        for r in es["refs"]:
            props = plan.setdefault(r["key"], set())
            if r["property"]:
                props.add(r["property"])
    return {k: sorted(v or {"value"}) for k, v in sorted(plan.items())}


def check_paths(plan: dict, known_paths: set) -> list[str]:
    """Seeded paths missing from the committed inventory of Vault paths. A seed derived from the PR would make a typo'd key pass on the
    burst cluster and fail in prod, so the inventory (not the PR) is the authority."""
    return sorted(k for k in plan if k not in known_paths)


def sealed_stubs(scanned: dict) -> list[dict]:
    """The plain Secrets that stand in for SealedSecrets: name, namespace, key NAMES (values are random at run time)."""
    return [{"namespace": s["namespace"], "name": s["name"], "keys": s["keys"], "type": s.get("type") or "Opaque"}
            for s in scanned["sealedsecrets"] if s["name"]]


def components(changed: list[str]) -> dict:
    """Map a PR's changed paths to Flux components. Returns {"touched": [name...], "skipped": {name: reason}, "other": [paths]}.
    `infrastructure/<x>` and `apps/<x>` are named `<x>`, `<x>-config` directories name themselves; anything else under k8s/ (flux-system,
    the shared kustomization.yaml files) counts as `other` and means "test the whole tree"."""
    touched, skipped, other = [], {}, []
    for path in changed:
        if not path.startswith("k8s/"):
            continue
        m = _COMPONENT.match(path)
        if not m:
            other.append(path)
            continue
        name = m.group(2) or m.group(3)
        if name in SKIPPED:
            skipped[name] = SKIPPED[name]
        elif name not in touched:
            touched.append(name)
    return {"touched": sorted(touched), "skipped": dict(sorted(skipped.items())), "other": sorted(other)}


def storage_remap(text: str) -> tuple[str, list[str]]:
    """Rewrite asgard-only storage classes in a manifest to local-path. Returns (new text, the classes that were rewritten)."""
    hit = []
    for old, new in STORAGE_REMAP.items():
        if old in text:
            text = text.replace(old, new)
            hit.append(old)
    return text, sorted(hit)
