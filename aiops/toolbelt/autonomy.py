"""Autonomous T1 healing policy (Phase 10f): what a policy requires, as pure functions.

The registry's `autonomy:` section (aiops/actions.yml) is the SCOPE a reviewed PR can widen: which hosts, which limits,
which policies. This module turns it into objects and answers the stateless half of "may this proposal run itself?".
The stateful half (master switch, kill switch, maintenance, breaker, rate limits) and the independent precheck of
reality live in actions.Engine, where the database and the Semaphore client are.

A diagnosis only ever supplies a PROPOSAL. Autonomy is the Toolbelt's own decision, taken from the registry, never from
what the model said it wants to do.
"""
from __future__ import annotations

from dataclasses import dataclass

CONF_RANK = {"low": 0, "medium": 1, "high": 2}
PRECHECKS = ("unit-not-active", "helmrelease-stalled", "drift-present")


@dataclass(frozen=True)
class Policy:
    name: str
    enabled: bool
    action: str
    runbook: str
    layers: tuple
    min_confidence: str
    precheck: str
    max_changed: int | None = None


@dataclass(frozen=True)
class Limits:
    per_target_per_hour: int = 2
    per_policy_per_day: int = 10
    breaker_failures: int = 3
    breaker_window_seconds: int = 3600


class Autonomy:
    def __init__(self, hosts: frozenset, limits: Limits, policies: dict[str, Policy]):
        self.hosts, self.limits, self.policies = hosts, limits, policies

    @classmethod
    def from_registry(cls, data: dict) -> "Autonomy | None":
        """None when the registry has no `autonomy` section (then nothing can ever run itself)."""
        au = data.get("autonomy")
        if not au:
            return None
        pol = {}
        for name, p in au["policies"].items():
            pol[name] = Policy(name=name, enabled=bool(p["enabled"]), action=p["action"], runbook=p["runbook"],
                               layers=tuple(p["layers"]), min_confidence=p["min_confidence"], precheck=p["precheck"],
                               max_changed=p.get("max_changed"))
        lim = au["limits"]
        return cls(frozenset(au["hosts"]),
                   Limits(int(lim["per_target_per_hour"]), int(lim["per_policy_per_day"]), int(lim["breaker_failures"]),
                          int(lim["breaker_window_seconds"])), pol)

    def policy_for(self, action_id: str) -> Policy | None:
        """The ENABLED policy covering an action (at most one is expected; the first wins)."""
        return next((p for p in self.policies.values() if p.enabled and p.action == action_id), None)

    def static_block(self, policy: Policy | None, proposal: dict, diag: dict) -> str | None:
        """Why this proposal may NOT run itself, judged from the registry and the diagnosis alone; None = eligible so far."""
        if proposal.get("replay"):
            return "replay"
        if proposal.get("source") != "diagnosis":
            return "not-a-diagnosis"
        if policy is None:
            return "no-policy"
        host = proposal["params"].get("target_host")
        if host is not None and host not in self.hosts:
            return "host-out-of-scope"
        if diag.get("layer") not in policy.layers:
            return "layer"
        if CONF_RANK.get(diag.get("confidence"), -1) < CONF_RANK[policy.min_confidence]:
            return "confidence"
        if diag.get("runbook_id") != policy.runbook:
            return "runbook-mismatch"
        return None

    @staticmethod
    def drift_verdict(policy: Policy, changed: object) -> tuple[str, str]:
        """The diff-scope gate for `drift-present`: ('go'|'skip'|'stop', why) from the dry run's changed count."""
        if not isinstance(changed, int) or isinstance(changed, bool):
            return "stop", "the dry run reported no changed count"
        if changed == 0:
            return "skip", "the dry run shows no drift: nothing to converge"
        if policy.max_changed is not None and changed > policy.max_changed:
            return "stop", f"the dry-run diff ({changed} changed) is larger than this policy may converge on its own ({policy.max_changed}): a human should read it"
        return "go", f"{changed} changed task(s) within scope"

    @staticmethod
    def unit_verdict(active_state: object) -> tuple[str, str]:
        """The `unit-not-active` precheck from a service-status read."""
        if active_state in ("failed", "inactive"):
            return "go", f"the unit is {active_state}"
        if active_state == "active":
            return "skip", "the unit is already active: the fault healed itself"
        return "stop", f"the unit is {active_state!r}, neither failed nor active: not interfering"

    @staticmethod
    def helmrelease_verdict(conditions: object) -> tuple[str, str]:
        """The `helmrelease-stalled` precheck from kube.get's condition list."""
        conds = {c.get("type"): c.get("status") for c in (conditions or []) if isinstance(c, dict)}
        if conds.get("Stalled") == "True":
            return "go", "the HelmRelease reads Stalled=True"
        if conds.get("Ready") == "True":
            return "skip", "the HelmRelease is Ready: the fault healed itself"
        return "stop", "the HelmRelease is neither Stalled nor Ready (reconciling or unknown): not interfering"
