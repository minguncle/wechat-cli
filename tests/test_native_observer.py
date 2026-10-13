"""Isolated agent lifecycle checks; these do not attach to WeChat."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import runpy
import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for isolated JS checks")


def exercise(scenario):
    source = Path(__file__).parents[1] / "scripts" / "observe_native_send.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    agent = next(ast.literal_eval(item.value) for item in tree.body
                 if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "AGENT" for t in item.targets))
    spec = {"path": "fixture/Weixin.dll", "entries": [
        {"name": "coroutine_context", "rva": 100, "prefix": "90" * 32},
        {"name": "image_helper_candidate", "rva": 200, "prefix": "90" * 32},
    ]}
    if scenario.startswith("vector"):
        spec["entries"][1]["name"] = "message_send_candidate"
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let installed = 0, detached = 0;
const callbacks = [], events = [];
function ptr(offset) {
  return {
    add(n) { return ptr(offset + n); },
    sub(other) { return {toString() { return String(offset - other.offset); }}; },
    offset,
    readPointer() {
      if (offset === 0) return ptr(4000);
      if (offset === 8) return ptr(input.scenario === 'vector_oversize' ? 4144 : 4016);
      if (offset === 4000) return ptr(8000);
      throw new Error('unexpected pointer access');
    },
    readU32() { return 3; },
    readByteArray(n) { return new Uint8Array(n).fill(input.scenario === 'mismatch' && offset === 200 ? 0 : 0x90).buffer; },
    readU64() { return {toString() { return offset % 32 === 16 ? '7' : '15'; }}; },
    readU8() { return 1; }
  };
}
const context = {
  Process: {arch: input.scenario === 'architecture' ? 'arm64' : 'x64', pointerSize:8,
    getModuleByName() { return {path:'fixture/Weixin.dll', base:ptr(0)}; }},
  Interceptor: {attach(address, handlers) {
    if (input.scenario === 'partial' && installed === 1) throw new Error('fixture installation failure');
    installed++; callbacks.push(handlers);
    return {detach() { detached++; }};
  }},
  send(event) { events.push(event); }, rpc: {exports:{}}
};
let error = null;
try {
  vm.runInNewContext(input.agent, context);
  if (input.scenario === 'normal') {
    for (let i = 0; i < 300; i++) {
      const call = {threadId:17};
      callbacks[0].onEnter.call(call, []);
      callbacks[1].onEnter.call(call, [ptr(0),ptr(0),ptr(0),ptr(0)]);
      callbacks[1].onLeave.call(call);
    }
    context.rpc.exports.stop();
  } else if (input.scenario.startsWith('vector')) {
    callbacks[1].onEnter.call({threadId:17}, [ptr(0),ptr(0),ptr(0)]);
    context.rpc.exports.stop();
  }
} catch (e) { error = e.message; }
process.stdout.write(JSON.stringify({installed, detached, events, error}));
"""
    result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps({
        "scenario": scenario, "agent": agent.replace("SPEC", json.dumps(spec), 1),
    }), text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["mismatch", "architecture"])
def test_validation_precedes_all_hooks(scenario):
    result = exercise(scenario)
    assert result["error"]
    assert result["installed"] == 0


def test_partial_installation_is_cleaned_up():
    result = exercise("partial")
    assert result["error"] == "fixture installation failure"
    assert result["installed"] == result["detached"] == 1


def test_normal_stop_and_metadata_limit():
    result = exercise("normal")
    assert result["error"] is None
    assert result["installed"] == result["detached"] == 2
    assert result["events"][0] == {"kind": "ready", "hooks": 2}
    assert len(result["events"]) == 201  # Ready plus at most 200 observations.
    for event in result["events"][1:]:
        assert set(event) <= {"kind", "thread", "arg2", "arg3", "successBit", "errorString"}
        for key in ("arg2", "arg3", "errorString"):
            if key in event:
                assert set(event[key]) == {"length", "capacity"}


def test_message_vector_records_only_shapes():
    result = exercise("vector")
    assert result["error"] is None
    messages = result["events"][1]["messages"]
    assert messages["count"] == 1
    item = messages["items"][0]
    assert set(item) == {"type118", "typeD8", "type9C", "recipientShape", "contentShape"}
    assert item["type118"] == 3
    assert set(item["recipientShape"]) == {"length", "capacity"}


def test_oversized_vector_is_not_dereferenced():
    result = exercise("vector_oversize")
    assert result["error"] is None
    assert result["events"][1]["messages"] == {"bounded": False}


def test_initialization_failure_keeps_log_open_during_cleanup(monkeypatch, tmp_path):
    calls = []
    class Script:
        def on(self, _name, callback):
            self.callback = callback

        def load(self):
            self.callback({"type": "error", "description": "fixture rejected"}, None)

        def unload(self):
            # Represents an in-flight observation delivered during cleanup.
            self.callback({"payload": {"kind": "fixture_cleanup"}}, None)
            calls.append("unload")

    session = SimpleNamespace(create_script=lambda _: Script(), detach=lambda: calls.append("detach"))
    monkeypatch.setitem(sys.modules, "frida", SimpleNamespace(attach=lambda _: session))
    monkeypatch.setitem(sys.modules, "pefile", SimpleNamespace())
    source = Path(__file__).parents[1] / "scripts" / "observe_native_send.py"
    module = runpy.run_path(str(source))
    main = module["main"]
    monkeypatch.setitem(main.__globals__, "build_agent", lambda _: "fixture")
    output = tmp_path / "events.jsonl"
    monkeypatch.setattr(sys, "argv", [str(source), "--dll", "fixture.dll", "--pid", "1", "--output", str(output)])
    with pytest.raises(RuntimeError, match="Observer failed to initialize"):
        main()
    assert calls == ["unload", "detach"]
    events = [json.loads(line)["event"]["kind"] for line in output.read_text().splitlines()]
    assert events == ["agent_error", "fixture_cleanup"]
