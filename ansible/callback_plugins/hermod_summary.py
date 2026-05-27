# ansible/callback_plugins/hermod_summary.py
"""End-of-run summary callback that POSTs to Hermod.

Activated by ANSIBLE_CALLBACKS_ENABLED=hermod_summary. Reads
HERMOD_URL from env (set by the Semaphore project environment).
Mode (drift vs apply) is inferred from the FIRST playbook the
callback sees — the wrapper:

    playbooks/drift-check.yml  -> mode=drift  (alert on changes/failure)
    playbooks/apply.yml        -> mode=apply  (critical on failure only)
    playbooks/fleet-agents.yml -> mode=apply  (critical on failure only;
                                  daily fleet-wide agent reconverge)

Any other wrapper is a no-op — the callback is meant for the Semaphore
wrapper playbooks; per-host ad-hoc runs from the operator's MacBook
should not POST to Hermod.

import_playbook semantics: Ansible runs each imported playbook with
its own TaskQueueManager, so v2_playbook_on_stats fires once per
imported playbook. We aggregate stats across all of them and POST
exactly once at process exit (atexit). v2_playbook_on_start sets the
mode from the FIRST playbook only — subsequent calls (for site.yml +
its imports) are ignored.

POST failure is logged but never raised — the callback must never
break the playbook run.
"""

from __future__ import absolute_import, division, print_function

import atexit
import json
import os
import urllib.error
import urllib.request
from collections import defaultdict

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    callback: hermod_summary
    type: notification
    short_description: POST playbook summary to Hermod
    description:
      - Posts an aggregated run summary to Hermod at process exit.
      - Activated by ANSIBLE_CALLBACKS_ENABLED=hermod_summary.
      - Reads HERMOD_URL from environment.
    requirements:
      - whitelisting in ANSIBLE_CALLBACKS_ENABLED
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "hermod_summary"
    CALLBACK_NEEDS_WHITELIST = True

    _MODES = {
        "drift-check.yml": "drift",
        "apply.yml": "apply",
        "fleet-agents.yml": "apply",
    }

    def __init__(self):
        super().__init__()
        self.mode = None
        self.wrapper_name = None
        # per-host: { host: {ok, changed, failed, unreachable} }
        self.totals = defaultdict(lambda: {"ok": 0, "changed": 0, "failed": 0, "unreachable": 0})
        self.posted = False
        atexit.register(self._post_once)

    def v2_playbook_on_start(self, playbook):
        # First playbook only — the wrapper. Imports of site.yml fire
        # this hook again; ignore them.
        if self.mode is not None:
            return
        name = os.path.basename(playbook._file_name)
        self.mode = self._MODES.get(name)
        self.wrapper_name = name

    def v2_playbook_on_stats(self, stats):
        # Aggregate; final POST happens in atexit so per-imported-playbook
        # stats calls all roll up into one Hermod notification.
        for host in stats.processed:
            s = stats.summarize(host)
            for k in self.totals[host]:
                self.totals[host][k] += s.get(k, 0)

    # --- POST ---

    def _post_once(self):
        if self.posted or self.mode is None:
            return
        self.posted = True

        url = os.environ.get("HERMOD_URL", "").rstrip("/")
        if not url:
            self._display.warning("hermod_summary: HERMOD_URL unset, skipping notification")
            return

        grand = {"ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
        for host, s in self.totals.items():
            for k in grand:
                grand[k] += s[k]

        had_failure = grand["failed"] > 0 or grand["unreachable"] > 0
        had_changes = grand["changed"] > 0
        changed_hosts = sum(1 for h, s in self.totals.items() if s["changed"] > 0)
        failed_hosts = sum(1 for h, s in self.totals.items() if s["failed"] > 0 or s["unreachable"] > 0)

        # Routing matrix:
        #   drift mode + failure         -> alert
        #   drift mode + changes only    -> alert (drift detected)
        #   drift mode + clean           -> no notification
        #   apply mode + failure         -> critical
        #   apply mode + success         -> no notification
        if self.mode == "drift":
            if not (had_failure or had_changes):
                return
            tag = "alert"
            if had_failure:
                title = "Drift check failed: {} host(s) failed/unreachable".format(failed_hosts)
            else:
                title = "Drift detected: {} task(s) on {} host(s)".format(grand["changed"], changed_hosts)
        elif self.mode == "apply":
            if not had_failure:
                return
            tag = "critical"
            title = "Apply failed: {} host(s) failed/unreachable".format(failed_hosts)
        else:
            return

        per_host = sorted(
            ("- `{h}`: ok={ok} changed={changed} failed={failed} unreachable={unreachable}".format(h=h, **s)
             for h, s in self.totals.items()),
        )
        body = "\n".join([
            "**Wrapper:** `{}`".format(self.wrapper_name),
            "**Totals:** ok={ok} changed={changed} failed={failed} unreachable={unreachable}".format(**grand),
            "",
            "**Per host:**",
        ] + per_host)

        payload = {
            "title": title,
            "body": body,
            "type": "failure" if had_failure else "warning",
            "tag": tag,
            "format": "markdown",
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    self._display.warning("hermod_summary: POST returned HTTP {}".format(resp.status))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            self._display.warning("hermod_summary: POST failed: {}".format(e))
