"""The single authorization gate (INV-5, replaced by an in-code default-deny check; ADR-M1, ADR-M5).

Default deny. A request is allowed only if it names this instance and its principal is either
the instance owner or a client registered in the instance config, and the action is on that
principal's allow-list. Anything else raises Denied.
"""

from dataclasses import dataclass

from mini_perpetuity.config import Config

CLIENT_PREFIX = "client:"
# Add an action here only together with its tests.
OWNER_ACTIONS = frozenset({"search", "get", "review", "approve", "reject", "correct", "retire"})
CLIENT_ACTIONS = frozenset(
    {"search", "get", "propose"}
)  # a client can never approve, reject, correct or retire


class Denied(Exception):
    pass


@dataclass(frozen=True)
class Request:
    principal: str  # who is asking: "human:<os user>" or "client:<name>"
    instance: str  # whose data
    action: str  # what for


def authorize(cfg: Config, request: Request) -> None:
    if request.instance != cfg.instance:
        raise Denied("request is for a different instance")
    if request.principal == cfg.owner:
        allowed = OWNER_ACTIONS
    elif _is_registered_client(cfg, request.principal):
        allowed = CLIENT_ACTIONS
    else:
        raise Denied("principal is not the owner or a registered client")
    if request.action not in allowed:
        raise Denied(f"action {request.action!r} is not allowed")


def _is_registered_client(cfg: Config, principal: str) -> bool:
    return principal.startswith(CLIENT_PREFIX) and principal[len(CLIENT_PREFIX) :] in cfg.clients
