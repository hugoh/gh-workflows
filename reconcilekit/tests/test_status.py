import pytest

from reconcilekit import (
    Status,
    classify_status,
    partition_fields,
    summary_status,
    unavailable_suffix,
)


def test_classify_status_at_target_and_changed_is_ok():
    assert classify_status(at_target=True, changed=True) == Status.OK


def test_classify_status_at_target_and_unchanged_is_unchanged():
    assert classify_status(at_target=True, changed=False) == Status.UNCHANGED


def test_classify_status_not_at_target_and_changed_is_limited():
    assert classify_status(at_target=False, changed=True) == Status.LIMITED


def test_classify_status_not_at_target_and_unchanged_is_limited_unchanged():
    assert classify_status(at_target=False, changed=False) == Status.LIMITED_UNCHANGED


def test_partition_fields_splits_would_enable_and_unavailable():
    summary = partition_fields(
        {"a": (False, True), "b": (True, True), "c": (False, False)}
    )
    assert summary == {"would_enable": ["a"], "unavailable": ["c"]}


def test_partition_fields_preserves_field_order():
    summary = partition_fields(
        {"z": (False, True), "a": (False, True), "m": (False, True)}
    )
    assert summary["would_enable"] == ["z", "a", "m"]


def test_summary_status_ok_when_would_enable_and_nothing_gated():
    assert summary_status({"would_enable": ["a"], "unavailable": []}) == Status.OK


def test_summary_status_limited_when_gated_and_would_enable():
    assert (
        summary_status({"would_enable": ["a"], "unavailable": ["b"]}) == Status.LIMITED
    )


def test_summary_status_limited_unchanged_when_only_gated():
    assert (
        summary_status({"would_enable": [], "unavailable": ["b"]})
        == Status.LIMITED_UNCHANGED
    )


def test_summary_status_unchanged_when_nothing_to_do():
    assert summary_status({"would_enable": [], "unavailable": []}) == Status.UNCHANGED


def test_unavailable_suffix_empty_when_nothing_gated():
    assert unavailable_suffix([]) == ""


def test_unavailable_suffix_lists_names():
    assert unavailable_suffix(["a", "b"]) == " (unavailable: a, b)"


@pytest.mark.parametrize("status", [Status.OK, Status.LIMITED, Status.FAILED])
def test_status_is_hashable_and_distinct(status):
    assert status in set(Status)
