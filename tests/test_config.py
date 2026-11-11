from pathlib import Path

import pytest

from mini_perpetuity.cli import main
from mini_perpetuity.config import ConfigError, load_config, validate_name


def write(tmp_path: Path, name: str = "work", body: str | None = None, mode: int = 0o600) -> Path:
    pw = tmp_path / f"{name}.pw"
    pw.write_text("secret\n")
    pw.chmod(0o600)
    p = tmp_path / f"{name}.toml"
    p.write_text(
        body
        if body is not None
        else f'instance = "{name}"\nhost = "localhost"\nport = 5432\npassword_file = "{pw}"\n'
    )
    p.chmod(mode)
    return p


def load(p: Path) -> object:
    return load_config({"MP_CONFIG": str(p)})


def test_valid_config(tmp_path: Path) -> None:
    cfg = load_config({"MP_CONFIG": str(write(tmp_path))})
    assert (cfg.instance, cfg.dbname, cfg.user) == ("work", "mp_work", "mp_work_app")
    assert cfg.data_policy == "synthetic_only"
    assert cfg.password() == "secret"


@pytest.mark.parametrize("name", ["personal", "work", "test", "a", "a" * 24, "a1"])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "Work", "w_rk", "w-rk", "1a", "a" * 25, "a b", "a\n", "é"])
def test_invalid_names(name: str) -> None:
    with pytest.raises(ConfigError):
        validate_name(name)


def test_no_env() -> None:
    with pytest.raises(ConfigError, match="MP_CONFIG"):
        load_config({})


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(tmp_path / "nope.toml")


def test_malformed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="malformed"):
        load(write(tmp_path, body="instance = ["))


def test_name_must_match_file(tmp_path: Path) -> None:
    p = write(tmp_path, "work")
    p.write_text(p.read_text().replace('"work"', '"personal"', 1))
    with pytest.raises(ConfigError, match="does not match"):
        load(p)


def test_unknown_instance_name_chars(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(write(tmp_path, "Work"))


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_loose_permissions(tmp_path: Path, mode: int) -> None:
    with pytest.raises(ConfigError, match="0600"):
        load(write(tmp_path, mode=mode))


def test_loose_password_file(tmp_path: Path) -> None:
    cfg = load_config({"MP_CONFIG": str(write(tmp_path))})
    cfg.password_file.chmod(0o644)
    with pytest.raises(ConfigError, match="0600"):
        cfg.password()


@pytest.mark.parametrize(
    "extra",
    ['data_policy = "whatever"', "bogus = 1", 'port = "5432"'],
)
def test_bad_fields(tmp_path: Path, extra: str) -> None:
    p = write(tmp_path)
    body = p.read_text()
    p.write_text(
        body.replace("port = 5432\n", "") + extra + "\n" if "port" in extra else body + extra + "\n"
    )
    with pytest.raises(ConfigError):
        load(p)


def test_missing_key_and_relative_password_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(write(tmp_path, body='instance = "work"\nhost = "h"\nport = 1\n'))
    with pytest.raises(ConfigError, match="absolute"):
        load(write(tmp_path, body='instance = "work"\nhost = "h"\nport = 1\npassword_file = "x"\n'))


def test_cli_refuses_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MP_CONFIG", raising=False)
    assert main(["status"]) == 2


def test_cli_refuses_bad_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MP_CONFIG", str(write(tmp_path, mode=0o644)))
    assert main(["status"]) == 2


HEAD = 'instance = "work"\nhost = "h"\nport = 1\npassword_file = "/x/w.pw"\n'


def test_clients_default_to_none_and_parse_when_given(tmp_path: Path) -> None:
    assert load_config({"MP_CONFIG": str(write(tmp_path))}).clients == ()
    p = write(tmp_path, body=HEAD + 'clients = ["claude-desktop", "claude-code"]\n')
    assert load_config({"MP_CONFIG": str(p)}).clients == ("claude-desktop", "claude-code")


@pytest.mark.parametrize(
    "value",
    ['"claude"', '["Claude"]', '["a b"]', '[""]', "[1]", '["a", "a"]', '["-a"]', '["a_b"]',
     '["' + "x" * 33 + '"]', "{}"],
)  # fmt: skip
def test_bad_clients_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="clients"):
        load(write(tmp_path, body=HEAD + f"clients = {value}\n"))
