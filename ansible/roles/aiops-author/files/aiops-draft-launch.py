#!/usr/bin/env python3
"""aiops-draft-launch: start or stop a drafting-session unit on the dispatcher's request. Runs as root, triggered by a systemd
path unit (aiops-draft-launch.path) when a `ready` or `stop` marker appears under the jobs directory.

This is the only privileged step in the author, and it is deliberately dumb: it never reads a job's contents and never runs
anything from them. It acts only on a DIRECTORY NAME that is all digits, for a real directory (no symlink), by running
`systemctl start --no-block aiops-draft@<digits>.service` or `systemctl stop ...`. The dispatcher (unprivileged, sandboxed)
cannot start anything else through it, and the session it starts runs as a different, unprivileged user.
"""
import os
import subprocess
import sys

JOBS = os.environ.get("AIOPS_AUTHOR_JOBS", "/var/lib/aiops-author/jobs")
UNIT = os.environ.get("AIOPS_DRAFT_UNIT", "aiops-draft")


def log(msg):
    print(f"[aiops-draft-launch] {msg}", flush=True)


def take_marker(job_fd_path, name):
    """True when `name` is a regular file (not a symlink) inside the job directory; the marker is removed."""
    p = os.path.join(job_fd_path, name)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    if not os.path.isfile(p) or os.path.islink(p) or st.st_size > 1024:
        log(f"ignoring a {name} marker that is not a small regular file in {job_fd_path}")
        os.unlink(p)
        return False
    os.unlink(p)
    return True


def main():
    with os.scandir(JOBS) as it:
        for e in sorted(it, key=lambda x: x.name):
            if not e.name.isdigit() or len(e.name) > 9 or not e.is_dir(follow_symlinks=False):
                continue
            unit = f"{UNIT}@{int(e.name)}.service"
            if take_marker(e.path, "stop"):
                subprocess.run(["/usr/bin/systemctl", "stop", unit], check=False, timeout=60)
                log(f"stopped {unit}")
            if take_marker(e.path, "ready"):
                subprocess.run(["/usr/bin/systemctl", "reset-failed", unit], check=False, timeout=30)
                subprocess.run(["/usr/bin/systemctl", "start", "--no-block", unit], check=True, timeout=30)
                log(f"started {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
