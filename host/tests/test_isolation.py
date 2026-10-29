"""Guards for the autouse isolation fixtures in conftest.py."""

from pathlib import Path

from clawd_tank_daemon.garmin_client import load_config
from clawd_tank_daemon.socket_server import SocketServer


def test_socket_server_default_path_is_not_the_live_socket(tmp_path):
    server = SocketServer(on_message=None)
    assert server._socket_path == tmp_path / "sock"
    assert server._socket_path != Path.home() / ".clawd-tank" / "sock"


def test_garmin_config_default_path_is_not_the_real_file(tmp_path):
    assert load_config() is None  # tmp_path/garmin.json does not exist
    (tmp_path / "garmin.json").write_text('{"url": "https://x.example/s", "token": "t"}')
    assert load_config()["url"] == "https://x.example/s"
