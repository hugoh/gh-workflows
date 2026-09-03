"""reconcilekit -- a tiny stateless fetch-diff-apply reconciliation kernel.

The pattern every fleet-wide config tool repeats: enumerate targets, fetch each
one's current state, compare to the desired state, either report the plan
(dry run) or apply it, classify the outcome into a small fixed vocabulary, and
run the whole thing in bounded parallel with per-target failure isolation.
"""

from __future__ import annotations

from .kernel import (
    DEFAULT_JOBS,
    HasName,
    ReconcileError,
    Result,
    run_parallel,
    run_reconcile,
)
from .render import print_status, progress_bar, result_line
from .status import (
    Status,
    classify_status,
    partition_fields,
    summary_status,
    unavailable_suffix,
)

__all__ = [
    "DEFAULT_JOBS",
    "HasName",
    "ReconcileError",
    "Result",
    "Status",
    "classify_status",
    "partition_fields",
    "print_status",
    "progress_bar",
    "result_line",
    "run_parallel",
    "run_reconcile",
    "summary_status",
    "unavailable_suffix",
]
