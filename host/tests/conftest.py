"""Shared test fixtures."""

import pytest

import clawd_tank_daemon.garmin_client as garmin_client
import clawd_tank_daemon.session_store as session_store
import clawd_tank_daemon.socket_server as socket_server


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    """Redirect session persistence to a temp dir for all tests.

    Prevents tests from reading/writing the real ~/.clawd-tank/sessions.json.
    """
    monkeypatch.setattr(session_store, "SESSIONS_PATH", tmp_path / "sessions.json")


@pytest.fixture(autouse=True)
def _isolate_live_files(tmp_path, monkeypatch):
    """Keep tests away from the user's running daemon and config.

    SocketServer deletes its socket path on start and stop, so a test using the real
    ~/.clawd-tank/sock silently disconnects every Claude Code hook from a running daemon.
    The Garmin config holds a relay token; tests must never load the real one.
    """
    monkeypatch.setattr(socket_server, "SOCKET_PATH", tmp_path / "sock")
    monkeypatch.setattr(garmin_client, "CONFIG_PATH", tmp_path / "garmin.json")
