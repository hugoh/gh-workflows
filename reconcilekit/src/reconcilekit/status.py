"""The status vocabulary every reconcile result is classified into, plus the
"enable a set of toggles, some of which are gated" summarising helpers.
"""

from __future__ import annotations

import enum


class Status(enum.Enum):
    """How a target's result relates to the desired end state -- distinct from
    a Result's tag, which is per-command bookkeeping for end-of-run summaries.
    """

    OK = "ok"  # full target reached, changed this run
    UNCHANGED = "unchanged"  # full target reached, already was
    LIMITED = "limited"  # best-effort (capped by plan/data) reached, changed this run
    LIMITED_UNCHANGED = "limited_unchanged"  # best-effort reached, already was
    FAILED = "failed"  # worker raised


def classify_status(at_target: bool, changed: bool) -> Status:
    if at_target:
        return Status.OK if changed else Status.UNCHANGED
    return Status.LIMITED if changed else Status.LIMITED_UNCHANGED


def partition_fields(fields: dict[str, tuple[bool, bool]]) -> dict[str, list[str]]:
    """Splits a `name -> (currently_enabled, available_here)` mapping into the
    two lists every "enable a set of toggles" command reports: `would_enable`
    (available but off) and `unavailable` (gated).
    """
    return {
        "would_enable": [
            name
            for name, (enabled, available) in fields.items()
            if available and not enabled
        ],
        "unavailable": [
            name for name, (_enabled, available) in fields.items() if not available
        ],
    }


def summary_status(summary: dict[str, list[str]]) -> Status:
    """Status for a partition_fields() summary: at target once nothing is
    gated, changed when there's anything left to enable.
    """
    return classify_status(
        at_target=not summary["unavailable"], changed=bool(summary["would_enable"])
    )


def unavailable_suffix(unavailable: list[str]) -> str:
    return f" (unavailable: {', '.join(unavailable)})" if unavailable else ""
