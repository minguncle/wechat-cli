from pathlib import Path

import pytest

from novus_wechat import native_preflight as native


def setup(monkeypatch, tmp_path, observations):
    runtime = {'dll': str(tmp_path / 'Weixin.dll'), 'pid': 123}
    monkeypatch.setattr(native, 'discover', lambda: runtime)
    monkeypatch.setattr(native, 'verify_identity', lambda value: None)
    rows = iter(observations)
    monkeypatch.setattr(native, 'observe', lambda *args: [
        {'thread': thread, 'alive': True, 'nonNull': 1} for thread in next(rows)])
    calls = []
    monkeypatch.setattr(native, 'build_agent', lambda dll, thread, resource: thread)
    def execute(thread, *args):
        calls.append(thread)
        return [{'kind': 'probe_result', 'sending_called': False, 'resolved_path': 'cache'}]
    monkeypatch.setattr(native, 'run_agent', execute)
    monkeypatch.setattr(native, 'verify_resource_account', lambda *args: Path('cache'))
    return calls


@pytest.mark.parametrize('observations,expected', [
    ([[1], [1]], 1),
    ([[1, 2], [1], [1]], 1),
    ([[1], [], [2], [2]], 2),
    ([[1], [2], [2]], 2),
])
def test_only_inspects_account_after_two_consecutive_unique_samples(monkeypatch, tmp_path, observations, expected):
    calls = setup(monkeypatch, tmp_path, observations)
    result = native.preflight(tmp_path, tmp_path / 'result.json')
    assert result['candidate_thread'] == expected
    assert result['sending_called'] is False
    assert calls == [expected]


@pytest.mark.parametrize('observations', [
    [[1, 2]] * 6,
    [[1], [2], [1], [2], [1], [2]],
    [[]] * 6,
])
def test_persistent_ambiguity_never_invokes_account_function(monkeypatch, tmp_path, observations):
    calls = setup(monkeypatch, tmp_path, observations)
    with pytest.raises(ValueError, match='未调用发送'):
        native.preflight(tmp_path, tmp_path / 'result.json')
    assert calls == []
    assert not (tmp_path / 'result.json').exists()
