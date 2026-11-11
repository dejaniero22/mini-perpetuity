from pathlib import Path

import pytest

from mini_perpetuity.authz import ALLOWED_ACTIONS, Denied, Request, authorize
from mini_perpetuity.config import Config

CFG = Config("work", "localhost", 5432, Path("/x/work.pw"), "synthetic_only", "human:rio")


def test_owner_may_search_own_instance() -> None:
    authorize(CFG, Request("human:rio", "work", "search"))


@pytest.mark.parametrize("principal", ["human:mallory", "client:claude", "human:", "rio", ""])
def test_other_principals_denied(principal: str) -> None:
    with pytest.raises(Denied, match="owner"):
        authorize(CFG, Request(principal, "work", "search"))


@pytest.mark.parametrize("instance", ["personal", "", "WORK", "work "])
def test_other_instances_denied(instance: str) -> None:
    with pytest.raises(Denied, match="different instance"):
        authorize(CFG, Request("human:rio", instance, "search"))


@pytest.mark.parametrize("action", ["write", "get", "approve", "SEARCH", "", "search "])
def test_unlisted_actions_denied(action: str) -> None:
    with pytest.raises(Denied, match="not allowed"):
        authorize(CFG, Request("human:rio", "work", action))


def test_allow_list_is_exactly_search() -> None:
    # Default deny: widening this set must come with new tests (plan M4/M5/M6).
    assert ALLOWED_ACTIONS == {"search"}
