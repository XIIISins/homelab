#!/usr/bin/env python3
"""burst-reaper — TTL cost guard for the 10b2 burst substrate (runs on Frigg, root, systemd timer).

Destroys DigitalOcean droplets tagged `burst` that outlived their TTL, so a forgotten burst
cluster cannot bill for a month (3 x s-2vcpu-4gb ~ $72/mo). Independent of Terraform on purpose:
it must work when nobody remembers the cluster exists, and when the state/laptop is gone.

Safety rails (a bug here deletes droplets):
  * only droplets carrying the DO tag `burst` are ever listed (server-side filter) AND whose name
    matches NAME_RE (`burst-<n>`) are touched — do1 and the legacy droplets can never match;
  * a droplet is reaped when age > min(its `burst-ttl-<N>h` tag, else DEFAULT hours; HARD CAP hours);
  * --dry-run / BURST_REAPER_DRY_RUN=1 lists what WOULD be deleted and deletes nothing.

Secrets: the DO token is read from Vault (secret/ansible/frigg/iac-env, field digitalocean_token)
at run time with the Frigg AppRole and lives only in this process's memory — never in the unit,
the environment, argv or the journal. Config (non-secret) arrives via Environment= lines.

Exit codes: 0 ok (incl. nothing to reap), 1 failure (Vault/DO API error) — the unit shows failed
and a Hermod alert is attempted, because a dead cost guard must not be silent.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")
APPROLE_ENV_PATH = os.environ.get("BURST_REAPER_APPROLE_ENV_PATH", "/etc/frigg/vault-approle.env")
DO_TOKEN_VAULT_PATH = os.environ.get("BURST_REAPER_DO_TOKEN_VAULT_PATH", "secret/data/ansible/frigg/iac-env")
DO_TOKEN_FIELD = os.environ.get("BURST_REAPER_DO_TOKEN_FIELD", "digitalocean_token")
HERMOD_KEY_VAULT_PATH = os.environ.get("BURST_REAPER_HERMOD_KEY_VAULT_PATH", "secret/data/ansible/hermod/config-key")
HERMOD_BASE_URL = os.environ.get("BURST_REAPER_HERMOD_BASE_URL", "http://hermod.niflheim.xiiisins.com")
DO_API = os.environ.get("BURST_REAPER_DO_API", "https://api.digitalocean.com/v2")
TAG = os.environ.get("BURST_REAPER_TAG", "burst")
DEFAULT_HOURS = float(os.environ.get("BURST_REAPER_DEFAULT_HOURS", "4"))
HARD_CAP_HOURS = float(os.environ.get("BURST_REAPER_HARD_CAP_HOURS", "12"))
HTTP_TIMEOUT = 20

NAME_RE = re.compile(r"^burst-[0-9]+$")
TTL_TAG_RE = re.compile(r"^burst-ttl-([0-9]+)h$")


def log(msg):
    print(f"[burst-reaper] {msg}", flush=True)


def http_json(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def read_approle():
    creds = {}
    with open(APPROLE_ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["FRIGG_ROLE_ID"], creds["FRIGG_SECRET_ID"]


def vault_login():
    role_id, secret_id = read_approle()
    out = http_json("POST", f"{VAULT_ADDR}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    return out["auth"]["client_token"]


def vault_kv2_field(token, path, field):
    out = http_json("GET", f"{VAULT_ADDR}/v1/{path}", headers={"X-Vault-Token": token})
    return out["data"]["data"][field]


def hermod_notify(vault_token, title, body, tag="alert"):
    """Best-effort Discord/Hermod notification — never raises (the reap already happened)."""
    try:
        key = vault_kv2_field(vault_token, HERMOD_KEY_VAULT_PATH, "value")
        http_json("POST", f"{HERMOD_BASE_URL}/notify/{key}",
                  body={"title": title, "body": body, "tag": tag, "format": "markdown"})
        log("hermod_notify: posted")
    except Exception as exc:  # noqa: BLE001 — notification must not mask the real outcome
        log(f"hermod_notify failed: {exc}")


def parse_created(value):
    # DO returns RFC 3339, e.g. 2026-10-01T12:34:56Z
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def ttl_hours_for(droplet, default=DEFAULT_HOURS, cap=HARD_CAP_HOURS):
    """Allowed lifetime: the droplet's burst-ttl-<N>h tag if present, else `default`; never above `cap`."""
    ttl = default
    for tag in droplet.get("tags") or []:
        m = TTL_TAG_RE.match(tag)
        if m:
            ttl = float(m.group(1))
            break
    return min(ttl, cap)


def classify(droplets, now, default=DEFAULT_HOURS, cap=HARD_CAP_HOURS):
    """Return (reap, keep, skipped): lists of dicts with name/id/age_h/ttl_h. Pure — unit-testable."""
    reap, keep, skipped = [], [], []
    for d in droplets:
        name = d.get("name", "")
        if TAG not in (d.get("tags") or []) or not NAME_RE.match(name):
            skipped.append({"name": name, "id": d.get("id"), "why": "not a burst-N droplet carrying the tag"})
            continue
        age_h = (now - parse_created(d["created_at"])).total_seconds() / 3600.0
        ttl_h = ttl_hours_for(d, default, cap)
        row = {"name": name, "id": d["id"], "age_h": round(age_h, 2), "ttl_h": ttl_h}
        (reap if age_h > ttl_h else keep).append(row)
    return reap, keep, skipped


def list_burst_droplets(do_token):
    headers = {"Authorization": f"Bearer {do_token}"}
    url = f"{DO_API}/droplets?tag_name={TAG}&per_page=200"
    droplets = []
    while url:
        page = http_json("GET", url, headers=headers)
        droplets.extend(page.get("droplets", []))
        url = (page.get("links", {}).get("pages", {}) or {}).get("next")
    return droplets


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true",
                    default=os.environ.get("BURST_REAPER_DRY_RUN", "") in ("1", "true", "yes"),
                    help="list what would be reaped; delete nothing")
    ap.add_argument("--list", action="store_true", help="print burst droplets with age/ttl and exit (no deletes)")
    args = ap.parse_args()

    vault_token = None
    try:
        vault_token = vault_login()
        do_token = vault_kv2_field(vault_token, DO_TOKEN_VAULT_PATH, DO_TOKEN_FIELD)
        droplets = list_burst_droplets(do_token)
        now = datetime.datetime.now(datetime.timezone.utc)
        reap, keep, skipped = classify(droplets, now)

        for s in skipped:
            log(f"SKIP {s['name']!r} (id {s['id']}): {s['why']}")
        for k in keep:
            log(f"keep {k['name']} id={k['id']} age={k['age_h']}h ttl={k['ttl_h']}h")
        if not droplets:
            log("no burst droplets")
        if args.list:
            for r in reap:
                log(f"EXPIRED {r['name']} id={r['id']} age={r['age_h']}h ttl={r['ttl_h']}h")
            return 0

        reaped = []
        for r in reap:
            if args.dry_run:
                log(f"DRY-RUN would reap {r['name']} id={r['id']} age={r['age_h']}h ttl={r['ttl_h']}h")
                continue
            http_json("DELETE", f"{DO_API}/droplets/{r['id']}", headers={"Authorization": f"Bearer {do_token}"})
            log(f"REAPED {r['name']} id={r['id']} age={r['age_h']}h ttl={r['ttl_h']}h")
            reaped.append(r)

        if reaped:
            lines = "\n".join(f"- `{r['name']}` age {r['age_h']} h (ttl {r['ttl_h']} h)" for r in reaped)
            hermod_notify(
                vault_token,
                "Burst droplets reaped",
                f"The TTL reaper destroyed {len(reaped)} burst droplet(s):\n{lines}\n\n"
                "Run `scripts/burst/burst-down` so the Terraform state catches up.",
            )
        return 0
    except Exception as exc:  # noqa: BLE001 — report any failure; the cost guard must not die silently
        log(f"FAILED: {type(exc).__name__}: {exc}")
        if vault_token:
            hermod_notify(vault_token, "Burst reaper FAILED",
                          f"`burst-reaper` could not complete ({type(exc).__name__}). "
                          "Burst droplets may be running unguarded — check `journalctl -u burst-reaper`.",
                          tag="critical")
        return 1


if __name__ == "__main__":
    sys.exit(main())
