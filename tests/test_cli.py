import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from novus_wechat import cli, cli_commands
from novus_wechat.paths import ROOT
from novus_wechat.service import Service


def test_cli_emits_utf8_errors_with_ascii_stdio(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0",
           "NOVUS_WECHAT_DATA": str(tmp_path)}
    result = subprocess.run([sys.executable, "-X", "utf8=0", str(ROOT / "boot.py"), "init-key"],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 1
    assert "微信" in json.loads(result.stderr.decode("utf-8"))["error"]
    assert not result.stdout


@pytest.fixture
def terminal(tmp_path, wechat_fixture):
    root = tmp_path / "个人数据 with spaces"
    service = Service(root)
    service.configure({"source_dir": str(wechat_fixture[0])})
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "NOVUS_WECHAT_DATA": str(root)}
    process = subprocess.Popen([sys.executable, str(ROOT / "boot.py"), "serve", "--port", str(port)],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("CLI test service did not start")

        def run(*args, success=True):
            result = subprocess.run([sys.executable, str(ROOT / "boot.py"), *args, "--port", str(port)],
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            if success:
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)
            return result
        yield run, service
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_real_cli_sync_query_pagination_and_export(terminal, tmp_path):
    run, service = terminal
    assert run("sync", "--wait")["state"] == "complete"
    chat = run("query", "chats", "--keyword", "项目群")["items"][0]
    first = run("messages", "--chat-id", chat["id"], "--limit", "2")
    second = run("messages", "--chat-id", chat["id"], "--limit", "2", "--cursor", first["next_cursor"])
    ids = [m["id"] for m in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 3
    assert run("messages", "--keyword", "设计稿")["items"][0]["sender"] == "小林"
    assert run("chats", "--keyword", "Alice")["total"] == 1
    exported = tmp_path / "导出记录.json"
    assert run("export", chat["id"], "--output", str(exported))["bytes"] > 0
    content = json.loads(exported.read_text(encoding="utf-8"))
    assert len(content["messages"]) == 3
    assert {m["id"] for m in content["messages"]} == set(ids)
    result = run("export", chat["id"], "--output", str(exported), success=False)
    assert result.returncode == 1 and "已存在" in result.stderr
    assert run("import", str(exported))["messages"] == 3
    with service.store.db() as db:
        assert db.execute("SELECT count(*) FROM sends").fetchone()[0] == 0


def test_cli_export_covers_more_than_one_server_page(terminal, tmp_path):
    run, service = terminal
    chat = service.store.create_chat("分页导出")
    service.store.ingest([{"entity": "message", "id": f"message-{i:04}", "chat_id": chat["id"],
                          "timestamp": 1000 + i, "content": "记录", "source": "local"} for i in range(503)])
    path = tmp_path / "all.json"
    run("export", chat["id"], "--output", str(path))
    messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
    assert len(messages) == len({m["id"] for m in messages}) == 503
    assert messages[0]["timestamp"] == 1000 and messages[-1]["timestamp"] == 1502
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_cli_wrong_source_and_unsupported_send_fail(terminal, tmp_path):
    run, _ = terminal
    result = run("chats", "--data-dir", str(tmp_path / "wrong-account"), success=False)
    assert result.returncode == 1 and "另一个数据目录" in result.stderr
    result = run("send", "wechat:fake:someone", "--request-id", "explicit-one", "--text", "test", success=False)
    assert result.returncode == 1
    assert json.loads(result.stderr)["command"] == "send"


@pytest.mark.parametrize("argv,port", [(["--port", "23456", "status"], 23456), (["status", "--port", "23457"], 23457)])
def test_common_options_before_or_after_command(argv, port):
    assert cli_commands.parser().parse_args(argv).port == port


@pytest.mark.parametrize("argv", [["messages", "--limit", "501"], ["messages", "--start", "bad"],
                                  ["send", "id", "--text", "hello"],
                                  ["send", "id", "--request-id", "x", "--text", "hello", "--file", "x.txt"]])
def test_invalid_cli_arguments_exit_before_service_start(argv):
    with pytest.raises(SystemExit) as exc:
        cli_commands.parser().parse_args(argv)
    assert exc.value.code == 2


def test_send_network_error_is_unknown_and_never_retried(monkeypatch):
    requests = []
    def fail(req, **kwargs):
        requests.append(req)
        raise URLError("connection lost after request")
    monkeypatch.setattr(cli_commands, "urlopen", fail)
    with pytest.raises(RuntimeError, match="send-status original-id"):
        cli_commands.Client(18765).call("POST", "send", {"request_id": "original-id"})
    assert len(requests) == 1


def test_sync_timeout_and_failure_are_not_success(monkeypatch):
    class Failed:
        def call(self, *args, **kwargs):
            return {"state": "partial", "errors": ["wrong key"]}
    args = cli_commands.parser().parse_args(["sync", "--wait"])
    with pytest.raises(RuntimeError, match="wrong key"):
        cli_commands.execute(args, Failed())
    class Running:
        def call(self, *args, **kwargs):
            return {"state": "running"}
    clock = iter([1, 1000])
    monkeypatch.setattr(cli_commands.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="后台同步"):
        cli_commands.execute(args, Running())


def test_mcp_config_preserves_custom_data_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NOVUS_WECHAT_DATA", str(tmp_path))
    cli.main(["config", "--port", "23456"])
    value = capsys.readouterr().out
    data, toml = value.split("\n# Codex config.toml\n")
    config = json.loads(data)["mcpServers"]["novus-wechat"]
    assert config["env"]["NOVUS_WECHAT_DATA"] == str(tmp_path.resolve())
    import tomllib
    assert tomllib.loads(toml)["mcp_servers"]["novus-wechat"]["env"] == config["env"]
