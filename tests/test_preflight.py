"""사전 설치 범위·오류 판정·기존 작업 보존을 검사합니다. 실제 GPU/패키지 변경 없음."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight_test', ROOT / 'tools/preflight/main.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def completed(tmp_path):
    data = b'synthetic-test-image'
    (tmp_path / 'preview.png').write_bytes(data)
    (tmp_path / 'sim.log').write_text(preflight.MARKER + '\n')
    preflight.save(tmp_path / 'verification.json', {
        'result': 'PASS', 'render': {'preview_sha256': hashlib.sha256(data).hexdigest()}})
    return tmp_path


def test_real_completion_requires_zero_exit_marker_and_matching_preview(completed):
    assert preflight.validate_result(completed, 0)['result'] == 'PASS'
    with pytest.raises(RuntimeError):
        preflight.validate_result(completed, 139)
    (completed / 'sim.log').write_text('Required completion marker missing: ' + preflight.MARKER)
    with pytest.raises(RuntimeError):
        preflight.validate_result(completed, 0)
    (completed / 'sim.log').write_text(preflight.MARKER + '\n')
    (completed / 'preview.png').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='이미지'):
        preflight.validate_result(completed, 0)


@pytest.mark.parametrize('report', [{'result': 'RUNNING'}, {'result': 'FAIL'}, {}])
def test_exit_zero_does_not_hide_incomplete_or_failed_scene(completed, report):
    preflight.save(completed / 'verification.json', report)
    with pytest.raises(RuntimeError):
        preflight.validate_result(completed, 0)


@pytest.mark.parametrize('host', ['127.0.0.1', '0.0.0.0', '224.1.2.3', '255.255.255.255', '::1', 'localhost'])
def test_wrong_server_addresses_rejected_before_socket_access(host, monkeypatch):
    monkeypatch.setattr(preflight.socket, 'socket', lambda *a: pytest.fail('주소 검증 전 소켓 접근'))
    with pytest.raises(ValueError):
        preflight.server_address(host)


def test_nonlocal_or_busy_interface_rejected_before_docker(monkeypatch):
    class Busy:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, value): raise OSError('address already in use')
    monkeypatch.setattr(preflight.socket, 'socket', lambda *a: Busy())
    monkeypatch.setenv('ACCEPT_EULA', 'Y')
    monkeypatch.setattr(preflight.checks, 'inspect', lambda *a: pytest.fail('포트 오류 후 GPU 실행 시도'))
    with pytest.raises(OSError):
        preflight.run_scene('192.168.0.171')


def test_missing_license_prevents_any_docker_or_file_changes(monkeypatch):
    monkeypatch.delenv('ACCEPT_EULA', raising=False)
    monkeypatch.setattr(preflight.checks, 'inspect', lambda *a: pytest.fail('동의 전 환경 접근'))
    with pytest.raises(RuntimeError, match='라이선스'):
        preflight.run_scene()


def test_compose_uses_only_preflight_and_explicit_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('LEKIWI_DATA_DIR', str(tmp_path))
    env = preflight.environment()
    assert env['LEKIWI_PREFLIGHT_DATA'] == str(tmp_path / 'preflight')
    command = preflight.compose(['sudo', 'docker'])
    assert str(ROOT / 'compose.yaml') not in command
    assert command[-1] == str(ROOT / 'docker/compose.preflight.yaml')
    assert command[command.index('--env-file') + 1] == '/dev/null'
    assert 'LEKIWI_PREFLIGHT_DATA' in command[1]


def test_cleanup_targets_only_current_label(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight.subprocess, 'check_output',
        lambda command, **kw: calls.append(command) or 'owned-id\n')
    monkeypatch.setattr(preflight.subprocess, 'run', lambda command, **kw: calls.append(command))
    preflight.stop_owned(['docker'], 'unique-token', SimpleNamespace(poll=lambda: 0))
    assert calls == [
        ['docker', 'ps', '-q', '--filter', 'label=lekiwi.preflight.session=unique-token'],
        ['docker', 'stop', '-t', '20', 'owned-id']]


@pytest.mark.parametrize('mode', ['check', 'install'])
def test_host_entry_delegates_to_existing_driver_installer(monkeypatch, mode):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['preflight', mode])
    monkeypatch.setattr(preflight.subprocess, 'run',
        lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=0))
    preflight.main()
    expected = ['/usr/bin/python3', '-B', '-u', str(ROOT / 'tools/host_setup/main.py'), '--preflight']
    assert calls == [expected + (['--check'] if mode == 'check' else [])]


def test_failed_installer_preserves_exit_code_without_obscuring_error(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['preflight', 'install'])
    monkeypatch.setattr(preflight.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=7))
    with pytest.raises(SystemExit) as error:
        preflight.main()
    assert error.value.code == 7


@pytest.mark.parametrize('verify', [False, True])
def test_preflight_installer_never_builds_or_tests_lerobot(monkeypatch, verify):
    host = preflight.host_setup
    calls = []
    monkeypatch.setattr(host, 'inspect', lambda root: dict(docker=False, driver_devices=''))
    monkeypatch.setattr(host, 'assess', lambda info: dict(errors=[], warnings=[], driver_ready=True))
    monkeypatch.setattr(host.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(host, 'read', lambda path: '{}')
    monkeypatch.setattr(host.Path, 'exists', lambda self: False)
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('ACCEPT_EULA', 'Y')
    monkeypatch.setattr(host, 'install_driver', lambda info: None)
    monkeypatch.setattr(host, 'install_host', lambda info: ['docker'])
    monkeypatch.setattr(host, 'docker_command', lambda: ['docker'])
    monkeypatch.setattr(host, 'save_state', lambda value: None)
    monkeypatch.setattr(host, 'confirm', lambda message: None)
    monkeypatch.setattr(host, 'verify', lambda *a: pytest.fail('전체 교육 검사를 실행함'))
    monkeypatch.setattr(host, 'run', lambda command, **kw: calls.append(list(map(str, command))))
    monkeypatch.setattr(sys, 'argv', ['installer', '--preflight'] + (['--verify'] if verify else []))
    host.main()
    expected = [[str(ROOT / 'lekiwi'), 'preflight', mode]
                for mode in (['verify'] if verify else ['build', 'verify'])]
    assert calls == expected


def test_client_does_not_use_docker_or_gpu_inspection(monkeypatch, tmp_path):
    monkeypatch.setenv('LEKIWI_DATA_DIR', str(tmp_path))
    directory = tmp_path / 'preflight/client'
    (directory / 'squashfs-root').mkdir(parents=True)
    (directory / 'isaacsim-webrtc-1.1.5.AppImage').write_bytes(b'cached')
    app = directory / 'squashfs-root/AppRun'
    app.touch()
    monkeypatch.setattr(preflight.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(preflight.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(preflight.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(preflight.checks, 'inspect', lambda *a: pytest.fail('노트북 GPU 검사'))
    monkeypatch.setattr(preflight.host_setup, 'docker_command', lambda: pytest.fail('노트북 Docker 사용'))
    calls = []
    monkeypatch.setenv('DEBUG', '1')
    monkeypatch.setattr(preflight.subprocess, 'run', lambda command, **kw: calls.append((command, kw)))
    preflight.client(no_sandbox=True)
    assert calls[0][0] == [str(app), '--no-sandbox']
    assert calls[0][1]['env']['APPDIR'] == str(app.parent)
    assert 'DEBUG' not in calls[0][1]['env']


def test_killed_process_reports_memory_diagnostic(completed):
    (completed / 'sim.log').write_text('python.sh: Killed python3')
    with pytest.raises(RuntimeError, match='RAM·스왑'):
        preflight.validate_result(completed, 1)
