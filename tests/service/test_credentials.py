import pytest

from autocam_service.credentials import CredentialsError, load_credentials

SECRET = "s3cr3t-value-that-must-not-leak"


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_reads_dotenv(tmp_path):
    env = write_env(tmp_path, f"TRELLO_API_KEY=key\nTRELLO_TOKEN={SECRET}\nUNRELATED=1\n")
    creds = load_credentials(env, environ={})
    assert creds.get("TRELLO_TOKEN") == SECRET
    assert creds.missing("trello") == []
    assert creds.missing("onshape") == ["ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY"]


def test_environment_overrides_dotenv(tmp_path):
    env = write_env(tmp_path, "TRELLO_API_KEY=from-file\n")
    creds = load_credentials(env, environ={"TRELLO_API_KEY": "from-env"})
    assert creds.get("TRELLO_API_KEY") == "from-env"


def test_missing_file_and_empty_values_count_as_missing(tmp_path):
    env = write_env(tmp_path, "ONSHAPE_ACCESS_KEY=\n")
    assert not load_credentials(env, environ={}).has("ONSHAPE_ACCESS_KEY")
    assert load_credentials(tmp_path / "nope.env", environ={}).missing() == [
        "TRELLO_API_KEY", "TRELLO_TOKEN", "ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY"]


def test_require_names_missing_keys_without_values(tmp_path):
    env = write_env(tmp_path, f"ONSHAPE_ACCESS_KEY={SECRET}\n")
    creds = load_credentials(env, environ={})
    with pytest.raises(CredentialsError) as exc:
        creds.require("onshape")
    assert "ONSHAPE_SECRET_KEY" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_values_never_appear_in_repr(tmp_path):
    env = write_env(tmp_path, f"TRELLO_API_KEY={SECRET}\nTRELLO_TOKEN={SECRET}\n")
    creds = load_credentials(env, environ={})
    for text in (repr(creds), str(creds), f"{creds}"):
        assert SECRET not in text
        assert "TRELLO_API_KEY=<set>" in text and "ONSHAPE_SECRET_KEY=<missing>" in text


def test_unknown_key_is_rejected():
    with pytest.raises(CredentialsError):
        load_credentials(None, environ={}).get("GITHUB_TOKEN")
