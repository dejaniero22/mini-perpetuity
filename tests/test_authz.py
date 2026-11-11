from dataclasses import replace
from pathlib import Path

import pytest

from mini_perpetuity.authz import CLIENT_ACTIONS, OWNER_ACTIONS, Denied, Request, authorize
from mini_perpetuity.config import Config

CFG = Config(
    "work", "localhost", 5432, Path("/x/work.pw"), "synthetic_only", "human:rio",
    ("claude-desktop", "claude-code"),
)  # fmt: skip


@pytest.mark.parametrize("action", ["search", "get"])
def test_owner_may_read_own_instance(action: str) -> None:
    authorize(CFG, Request("human:rio", "work", action))


@pytest.mark.parametrize("client", ["claude-desktop", "claude-code"])
@pytest.mark.parametrize("action", ["search", "get"])
def test_registered_clients_may_read(client: str, action: str) -> None:
    authorize(CFG, Request(f"client:{client}", "work", action))


@pytest.mark.parametrize(
    "principal",
    [
        "human:mallory",  # not the owner
        "client:other",  # not registered
        "client:",  # empty name
        "claude-desktop",  # registered name without the prefix
        "human:claude-desktop",  # right name, wrong kind
        "client:Claude-Desktop",  # case matters
        "client:claude-desktop ",
        "rio",
        "",
    ],
)
def test_other_principals_denied(principal: str) -> None:
    with pytest.raises(Denied, match="not the owner or a registered client"):
        authorize(CFG, Request(principal, "work", "search"))


def test_no_clients_registered_means_no_clients() -> None:
    with pytest.raises(Denied):
        authorize(replace(CFG, clients=()), Request("client:claude-desktop", "work", "search"))


@pytest.mark.parametrize("principal", ["human:rio", "client:claude-desktop"])
@pytest.mark.parametrize("instance", ["personal", "", "WORK", "work "])
def test_other_instances_denied(principal: str, instance: str) -> None:
    with pytest.raises(Denied, match="different instance"):
        authorize(CFG, Request(principal, instance, "search"))


@pytest.mark.parametrize("principal", ["human:rio", "client:claude-desktop"])
@pytest.mark.parametrize("action", ["write", "propose", "approve", "SEARCH", "", "get "])
def test_unlisted_actions_denied(principal: str, action: str) -> None:
    with pytest.raises(Denied, match="not allowed"):
        authorize(CFG, Request(principal, "work", action))


def test_allow_lists_are_read_only() -> None:
    # Default deny: widening these sets must come with new tests (M6 adds "propose" for clients).
    assert OWNER_ACTIONS == {"search", "get"}
    assert CLIENT_ACTIONS == {"search", "get"}
