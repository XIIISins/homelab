#!/usr/bin/env python3
"""aiops-draft-launch: start or stop a drafting-session unit on the dispatcher's request. Runs as root, triggered by a systemd
path unit (aiops-draft-launch.path, DirectoryNotEmpty on the markers directory) when a marker appears.

This is the only privileged step in the author, and it is deliberately dumb: it never reads a job's contents and never runs
anything from them. A marker is a small regular file named `<digits>.ready` or `<digits>.stop` in ONE fixed directory; the
launcher acts on it only if jobs/<digits> is a real directory (no symlink), by running
`systemctl start --no-block aiops-draft@<digits>.service` or `systemctl stop ...`. The dispatcher (unprivileged, sandboxed)
cannot start anything else through it, and the session it starts runs as a different, unprivileged user. Everything else found
in the markers directory is deleted so that a stray entry can never keep the path unit re-triggering.

(Why a fixed directory: a glob over jobs/*/ready needs systemd to add an inotify watch on each new job directory, and a marker
written before that watch exists is never seen. Found 2026-10-04, change request 6.)
"""
import os
import re
import subprocess
import sys

JOBS = os.environ.get("AIOPS_AUTHOR_JOBS", "/var/lib/aiops-author/jobs")
MARKERS = os.environ.get("AIOPS_AUTHOR_MARKERS", "/var/lib/aiops-author/markers")
UNIT = os.environ.get("AIOPS_DRAFT_UNIT", "aiops-draft")
MARKER = re.compile(r"^(\d{1,9})\.(ready|stop)$")


def log(msg):
    print(f"[aiops-draft-launch] {msg}", flush=True)


def take(entry):
    """Remove one marker entry. Returns True only for a small regular file (not a symlink)."""
    ok = False
    try:
        st = os.lstat(entry.path)
        ok = os.path.isfile(entry.path) and not os.path.islink(entry.path) and st.st_size <= 1024
        if os.path.isdir(entry.path) and not os.path.islink(entry.path):
            os.rmdir(entry.path)
        else:
            os.unlink(entry.path)
    except OSError as e:
        log(f"could not remove {entry.name}: {type(e).__name__}")
        return False
    return ok


def main():
    wanted = {}
    with os.scandir(MARKERS) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        m = MARKER.match(e.name)
        if not m:
            log(f"removing an entry that is not a marker: {e.name[:40]!r}")
            take(e)
            continue
        if not take(e):
            log(f"ignoring {e.name}: not a small regular file")
            continue
        job = os.path.join(JOBS, m.group(1))
        if not os.path.isdir(job) or os.path.islink(job):
            log(f"ignoring {e.name}: no real job directory {m.group(1)}")
            continue
        wanted.setdefault(int(m.group(1)), set()).add(m.group(2))
    for cid in sorted(wanted):
        unit = f"{UNIT}@{cid}.service"
        if "stop" in wanted[cid]:
            subprocess.run(["/usr/bin/systemctl", "stop", unit], check=False, timeout=60)
            log(f"stopped {unit}")
        if "ready" in wanted[cid]:
            subprocess.run(["/usr/bin/systemctl", "reset-failed", unit], check=False, timeout=30)
            subprocess.run(["/usr/bin/systemctl", "start", "--no-block", unit], check=True, timeout=30)
            log(f"started {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
