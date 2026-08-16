"""Execution-accuracy metric tests."""

from evals.metrics import results_match


def test_identical_rows_match() -> None:
    assert results_match([(1, "a")], [(1, "a")])


def test_order_insensitive_by_default() -> None:
    assert results_match([(1,), (2,)], [(2,), (1,)])


def test_order_sensitive_mode() -> None:
    assert not results_match([(1,), (2,)], [(2,), (1,)], order_sensitive=True)
    assert results_match([(1,), (2,)], [(1,), (2,)], order_sensitive=True)


def test_multiset_semantics() -> None:
    # Duplicate rows matter: [1, 1] != [1]
    assert not results_match([(1,), (1,)], [(1,)])


def test_float_tolerance() -> None:
    assert results_match([(2328.599999,)], [(2328.6,)])


def test_mismatch_detected() -> None:
    assert not results_match([(275,)], [(274,)])


def test_bools_equal_ints() -> None:
    assert results_match([(True,)], [(1,)])
