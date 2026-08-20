"""Spend caps: the only thing standing between a public demo and a drained key."""

import time

import pytest

from dbagent.budget import (
    DEMO_BUDGET,
    BudgetedClient,
    BudgetExceeded,
    SessionBudget,
    SharedKeyLimiter,
    UsageTally,
    new_budget,
)
from test_loop import FakeClient, text_reply


def test_new_budget_copies_caps_and_zeroes_usage() -> None:
    budget = new_budget(DEMO_BUDGET)
    budget.start_question()
    fresh = new_budget(DEMO_BUDGET)
    assert fresh.questions == 0
    assert fresh.max_questions == DEMO_BUDGET.max_questions
    assert DEMO_BUDGET.questions == 0  # the template is never mutated


def test_question_cap_blocks_before_any_call() -> None:
    budget = SessionBudget(max_questions=1)
    budget.start_question()
    with pytest.raises(BudgetExceeded) as error:
        budget.start_question()
    assert error.value.limit == "questions"


def test_token_cap_blocks_the_next_call() -> None:
    budget = SessionBudget(max_tokens=100)
    budget.record_call({"prompt_tokens": 80, "completion_tokens": 30})
    with pytest.raises(BudgetExceeded) as error:
        budget.check_call()
    assert error.value.limit == "tokens"


def test_zero_means_unlimited() -> None:
    budget = SessionBudget()
    for _ in range(50):
        budget.start_question()
        budget.record_call({"prompt_tokens": 9_999, "completion_tokens": 9_999})
    budget.check_call()
    assert not budget.is_capped
    assert budget.meters() == []


def test_meters_only_report_capped_dimensions() -> None:
    budget = SessionBudget(max_llm_calls=5)
    budget.record_call({"prompt_tokens": 1, "completion_tokens": 1})
    assert budget.meters() == [("model calls", 1, 5)]


def test_budgeted_client_meters_real_usage() -> None:
    budget = SessionBudget(max_llm_calls=2)
    client = BudgetedClient(FakeClient([text_reply("hi"), text_reply("again")]), budget)
    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])
    assert budget.llm_calls == 2
    assert budget.tokens == 30  # 2 x (10 prompt + 5 completion)
    with pytest.raises(BudgetExceeded):
        client.chat([{"role": "user", "content": "3"}])


def test_budgeted_client_stops_before_calling_the_provider() -> None:
    """The cap must be checked first — an exhausted session costs zero tokens."""
    inner = FakeClient([])  # any call would raise "ran out of scripted replies"
    client = BudgetedClient(inner, SessionBudget(max_llm_calls=1, llm_calls=1))
    with pytest.raises(BudgetExceeded):
        client.chat([{"role": "user", "content": "x"}])
    assert inner.seen_messages == []


def test_shared_limiter_is_a_sliding_window() -> None:
    limiter = SharedKeyLimiter(max_calls=2, window_seconds=3600)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(BudgetExceeded) as error:
        limiter.acquire()
    assert error.value.limit == "shared_key"
    assert limiter.used() == 2


def test_shared_limiter_forgets_old_calls() -> None:
    limiter = SharedKeyLimiter(max_calls=1, window_seconds=0.01)
    limiter.acquire()
    with pytest.raises(BudgetExceeded):
        limiter.acquire()
    time.sleep(0.02)
    limiter.acquire()  # the first call has aged out of the window
    assert limiter.used() == 1


def test_tally_accumulates_across_turns() -> None:
    tally = UsageTally()
    tally.add(llm_calls=3, usage={"prompt_tokens": 100, "completion_tokens": 20}, seconds=1.5)
    tally.add(llm_calls=1, usage={"prompt_tokens": 50, "completion_tokens": 5}, seconds=0.5)
    assert (tally.llm_calls, tally.total_tokens, tally.seconds) == (4, 175, 2.0)
