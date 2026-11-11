"""The single authorization gate (INV-5, replaced by an in-code default-deny check; ADR-M1).

Default deny: a request is allowed only if it names this instance, an action on the allow-list,
and the instance's owner. Anything else raises Denied.
"""

from dataclasses import dataclass

from mini_perpetuity.config import Config

ALLOWED_ACTIONS = frozenset({"search"})  # add an action here only together with its tests


class Denied(Exception):
    pass


@dataclass(frozen=True)
class Request:
    principal: str  # who is asking, e.g. "human:rio"
    instance: str  # whose data
    action: str  # what for


def authorize(cfg: Config, request: Request) -> None:
    if request.instance != cfg.instance:
        raise Denied("request is for a different instance")
    if request.action not in ALLOWED_ACTIONS:
        raise Denied(f"action {request.action!r} is not allowed")
    if request.principal != cfg.owner:
        raise Denied("only the instance owner may do this")
