import pytest

from mini_perpetuity.seatbelt import Rejected, check_text

# Fake secrets are assembled at runtime so secret scanners (gitleaks in CI) do not flag this file.
FAKE_AWS_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_API_KEY = "API_" + "KEY=" + "abcdef123456"


@pytest.mark.parametrize(
    "text",
    [
        "SSN is 123-45-6789 for the record",
        "-----BEGIN RSA PRIVATE KEY-----",
        f"key {FAKE_AWS_ID} in the notes",
        "password: hunter2hunter2",
        FAKE_API_KEY,
        "token = s3cr3tvalue",
    ],
)
def test_rejects_secret_shaped_text(text: str) -> None:
    with pytest.raises(Rejected) as e:
        check_text("harmless", text)
    assert "seatbelt" in str(e.value)
    assert text not in str(e.value)  # never echo the matched text


@pytest.mark.parametrize(
    "text",
    [
        "Decided on 2024-01-15 to use PostgreSQL",
        "Call 555-1212 about section 12-34-56",  # not SSN-shaped
        "Rotate the token weekly",  # the word alone is fine
        "password policy: see section 4",  # no assigned value
        "",
        None,
    ],
)
def test_allows_ordinary_text(text: str | None) -> None:
    check_text(text)
