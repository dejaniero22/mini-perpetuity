"""Rate limits and get_memory against a real PostgreSQL (MP_ADMIN_DSN)."""

from collections.abc import Callable

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from mini_perpetuity import access
from mini_perpetuity.access import RateLimited
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import add_object
from mini_perpetuity.search import SearchError, get_memory, search_memory

Make = Callable[[], Config]
Register = Callable[..., Config]


def add(cfg: Config, content: str, **kw: object) -> int:
    args: dict[str, object] = {
        "epistemic_type": "human_decision",
        "agent": "human:test",
        "activity": "test",
        "status": "approved",
    }
    args.update(kw)
    with app_connect(cfg) as c:
        return add_object(c, cfg, content=content, **args)  # type: ignore[arg-type]


def search(cfg: Config, principal: str, query: str = "apples") -> int:
    with app_connect(cfg) as c:
        return len(search_memory(c, cfg, Request(principal, cfg.instance, "search"), query))


def count(cfg: Config, activity: str) -> int:
    with app_connect(cfg) as c:
        row = c.execute("SELECT count(*) FROM events WHERE activity = %s", (activity,)).fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


# ---- request quota ----------------------------------------------------------------------------


def test_clients_are_limited_per_minute_and_per_principal(
    make_instance: Make, register_clients: Register, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = register_clients(make_instance(), "alpha", "beta")
    add(cfg, "apples")
    monkeypatch.setattr(access, "MAX_REQUESTS_PER_MINUTE", 3)
    for _ in range(3):
        assert search(cfg, "client:alpha") == 1
    with pytest.raises(RateLimited):
        search(cfg, "client:alpha")
    assert count(cfg, "memory.search") == 3  # the refused request wrote nothing
    assert search(cfg, "client:beta") == 1  # another client has its own quota
    assert search(cfg, cfg.owner) == 1  # the owner is exempt
    for _ in range(5):
        search(cfg, cfg.owner)


def test_old_events_do_not_count(
    make_instance: Make,
    register_clients: Register,
    admin_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    add(cfg, "apples")
    dsn = make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as a:
        for _ in range(5):
            a.execute(
                "INSERT INTO events (occurred_at, actor, activity, instance) "
                "VALUES (now() - interval '2 minutes', 'client:alpha', 'memory.search', %s)",
                (cfg.instance,),
            )
    monkeypatch.setattr(access, "MAX_REQUESTS_PER_MINUTE", 3)
    assert search(cfg, "client:alpha") == 1  # five old events, none in the last minute


# ---- denial cap -----------------------------------------------------------------------------


def test_denials_stop_being_recorded_after_the_cap(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_instance()
    add(cfg, "apples")
    monkeypatch.setattr(access, "MAX_DENIALS_PER_MINUTE", 2)
    for _ in range(2):
        with pytest.raises(Denied):
            search(cfg, "human:mallory")
    for _ in range(3):  # past the cap: still refused, no longer written
        with pytest.raises(RateLimited):
            search(cfg, "human:mallory")
    assert count(cfg, "authz.denied") == 2
    assert search(cfg, cfg.owner) == 1  # legitimate requests are unaffected


# ---- get_memory -----------------------------------------------------------------------------


def req(cfg: Config, principal: str, action: str = "get") -> Request:
    return Request(principal, cfg.instance, action)


def test_get_memory_visibility(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    plain = add(cfg, "plain fact")
    secret = add(cfg, "secret fact", sensitivity="sensitive")
    with app_connect(cfg) as c:
        got = get_memory(c, cfg, req(cfg, "client:alpha"), plain)
        assert got is not None and got.content == "plain fact"
        # A client cannot tell a sensitive object from a missing one.
        assert get_memory(c, cfg, req(cfg, "client:alpha"), secret) is None
        assert get_memory(c, cfg, req(cfg, "client:alpha"), 999_999) is None
        # The owner can see it.
        owned = get_memory(c, cfg, req(cfg, cfg.owner), secret)
        assert owned is not None and owned.sensitivity == "sensitive"
    with app_connect(cfg) as c:
        rows = c.execute("SELECT payload FROM events WHERE activity = 'memory.get' ORDER BY id")
        found = [r[0]["found"] for r in rows.fetchall()]  # type: ignore[index]
    assert found == [True, False, False, True]  # the misses are audited too


def test_get_memory_rules(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    with app_connect(cfg) as c:
        for bad in (0, -1):
            with pytest.raises(SearchError):
                get_memory(c, cfg, req(cfg, "client:alpha"), bad)
        with pytest.raises(Denied):  # a search request cannot fetch
            get_memory(c, cfg, req(cfg, "client:alpha", "search"), 1)
    with app_connect(cfg) as c, pytest.raises(Denied):
        get_memory(c, cfg, req(cfg, "client:stranger"), 1)


def test_sensitive_objects_are_hidden_from_clients_in_search_too(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    add(cfg, "visible apples")
    add(cfg, "secret apples", sensitivity="sensitive")
    with app_connect(cfg) as c:
        for history in (False, True):
            client = search_memory(
                c, cfg, req(cfg, "client:alpha", "search"), "apples", history=history
            )
            assert [m.content for m in client] == ["visible apples"]
            owner = search_memory(c, cfg, req(cfg, cfg.owner, "search"), "apples", history=history)
            assert sorted(m.content for m in owner) == ["secret apples", "visible apples"]
