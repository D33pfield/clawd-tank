"""Shared test fixtures."""

import pytest

import clawd_tank_daemon.session_store as session_store


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    """Redirect session persistence to a temp dir for all tests.

    Prevents tests from reading/writing the real ~/.clawd-tank/sessions.json.
    """
    monkeypatch.setattr(session_store, "SESSIONS_PATH", tmp_path / "sessions.json")
