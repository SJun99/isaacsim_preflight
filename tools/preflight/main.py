"""금요일 사전 점검: PC 준비 → 최소 장면 검사 → 노트북 화면 확인."""
import argparse
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/host_setup'))
import checks
_host_spec = importlib.util.spec_from_file_location('preflight_host_setup', ROOT / 'tools/host_setup/main.py')
host_setup = importlib.util.module_from_spec(_host_spec)
_host_spec.loader.exec_module(host_setup)

IMAGE = 'lekiwi-preflight:5.1.0'
CLIENT_URL = 'https://downloads.isaacsim.nvidia.com/isaacsim-webrtc-streaming-client-1.1.5-linux-x64.AppImage'
MARKER = 'LEKIWI_PREFLIGHT result=PASS'


def data_root():
    return Path(os.environ.get('LEKIWI_DATA_DIR', ROOT / 'data')).resolve() / 'preflight'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def environment():
    directory = data_root()
    for name in ('home', 'cache/kit', 'kit-data', 'kit-logs', 'results'):
        (directory / name).mkdir(parents=True, exist_ok=True)
    return dict(os.environ, LEKIWI_UID=str(os.getuid()), LEKIWI_GID=str(os.getgid()),
                LEKIWI_PREFLIGHT_DATA=str(directory))


def compose(docker):
    if docker[0] == 'sudo':
        docker = ['sudo', '--preserve-env=LEKIWI_UID,LEKIWI_GID,LEKIWI_PREFLIGHT_DATA,ACCEPT_EULA', 'docker']
    return docker + ['compose', '--env-file', '/dev/null', '--project-name',
                     f'lekiwi-preflight-{os.getuid()}', '-f', str(ROOT / 'docker/compose.preflight.yaml')]


def require_license():
    if os.environ.get('ACCEPT_EULA') != 'Y':
        raise RuntimeError('README의 NVIDIA 라이선스를 확인하고 동의하면 export ACCEPT_EULA=Y를 설정하세요.')


def server_address(value):
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or address.is_loopback or int(address) == 0xffffffff:
        raise ValueError('학생 노트북이 접속할 데스크탑의 LAN IPv4 주소를 입력하세요.')
    # 로컬 인터페이스인지, 다른 영상 서버가 사용 중인지 실행 전에 확인합니다.
    for kind, port in ((socket.SOCK_STREAM, 49100), (socket.SOCK_DGRAM, 47998)):
        with socket.socket(socket.AF_INET, kind) as sock:
            if kind == socket.SOCK_STREAM:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((str(address), port))
    return str(address)


def validate_result(directory, code):
    """Kit이 오류 후 0으로 끝나도 실제 결과·이미지·완료 표식을 모두 요구합니다."""
    log = (directory / 'sim.log').read_text()
    if 'Killed' in log:
        raise RuntimeError('Isaac Sim 프로세스가 강제 종료됐습니다. RAM·스왑 여유와 운영체제의 메모리 부족 기록을 확인하세요.')
    try:
        report = json.loads((directory / 'verification.json').read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'Isaac Sim이 검사 결과를 만들기 전에 종료됐습니다. 종료 코드={code}') from exc
    if code != 0 or report.get('result') != 'PASS' or MARKER not in log.splitlines():
        raise RuntimeError(f'실행 검사 실패. 종료 코드={code}: ' + report.get('error', '완료 표식 또는 PASS 결과 없음'))
    preview = directory / 'preview.png'
    if hashlib.sha256(preview.read_bytes()).hexdigest() != report['render']['preview_sha256']:
        raise RuntimeError('미리보기 이미지가 없거나 저장 결과와 일치하지 않습니다.')
    return report


def stop_owned(docker, token, process):
    """이번 실행의 컨테이너만 종료합니다. 기존 실습·학습 컨테이너는 건드리지 않습니다."""
    ids = subprocess.check_output(docker + ['ps', '-q', '--filter',
        f'label=lekiwi.preflight.session={token}'], text=True, timeout=20).split()
    if ids:
        subprocess.run(docker + ['stop', '-t', '20', *ids], check=True, timeout=40,
                       stdout=subprocess.DEVNULL)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def run_scene(host=None):
    require_license()
    if host:
        host = server_address(host)
    info = checks.inspect(ROOT)
    assessment = checks.assess(info)
    if assessment['errors'] or not assessment['driver_ready']:
        raise RuntimeError('GPU·드라이버 준비가 필요합니다. ./lekiwi preflight install을 먼저 실행하세요.\n' +
                           '\n'.join(assessment['errors']))
    docker = host_setup.docker_command()
    subprocess.run(docker + ['image', 'inspect', IMAGE], check=True, stdout=subprocess.DEVNULL)
    env = environment()
    # 한 경로의 중복 실행이 같은 캐시·GPU를 동시에 사용하는 것을 막습니다.
    with (data_root() / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('이 폴더의 사전 점검이 이미 실행 중입니다. 기존 터미널에서 종료하세요.')
        directory = Path(tempfile.mkdtemp(prefix='check.', dir=data_root() / 'results'))
        save(directory / 'environment.json', {k: v for k, v in info.items() if k != 'driver_devices'})
        token = uuid.uuid4().hex
        command = compose(docker) + ['run', '--rm', '--no-deps', '-T', '--name',
            f'lekiwi-preflight-{os.getuid()}-{token[:12]}', '--label',
            f'lekiwi.preflight.session={token}', 'sim', '--output', f'/data/results/{directory.name}']
        if host:
            command += ['--host', host]
        result = {'result': 'RUNNING', 'mode': 'serve' if host else 'verify',
                  'video_received': 'NOT_CHECKED', 'directory': str(directory)}
        save(directory / 'result.json', result)
        print(f'로그: {directory / "sim.log"}', flush=True)
        print('첫 실행은 셰이더 준비로 수 분 걸릴 수 있습니다. 준비 제한 시간은 15분입니다.', flush=True)
        started = time.monotonic()
        process = None
        interrupted = False
        error = None
        try:
            with (directory / 'sim.log').open('w') as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, start_new_session=False)
                cursor = 0
                ready = False
                last_notice = started
                while process.poll() is None:
                    with (directory / 'sim.log').open() as output:
                        output.seek(cursor)
                        for line in output:
                            if 'LEKIWI_PREFLIGHT' in line or 'LEKIWI_STREAM' in line:
                                print(line.rstrip(), flush=True)
                            if 'LEKIWI_PREFLIGHT scene=READY' in line:
                                ready = True
                                print(f'노트북 클라이언트 Server: {host} | Play/Stop으로 낙하를 확인하세요. 종료: Ctrl+C', flush=True)
                        cursor = output.tell()
                    now = time.monotonic()
                    if not ready and now-started > 900:
                        raise RuntimeError('15분 내 실행 검사가 끝나지 않았습니다. sim.log를 확인하세요.')
                    if not ready and now-last_notice > 30:
                        print(f'Isaac Sim 준비·검사 중: {int(now-started)}초 경과', flush=True)
                        last_notice = now
                    time.sleep(.25)
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            error = exc
        finally:
            if process:
                try:
                    stop_owned(docker, token, process)
                except Exception as exc:
                    error = error or exc
        code = process.returncode if process else None
        try:
            # serve의 사용자 종료는 검사 성공과 별도로 STOPPED로 표시합니다.
            verification = validate_result(directory, 0 if host and interrupted else code)
            result.update(result='STOPPED' if interrupted else 'PASS', verification=verification)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            error = error or exc
            result['result'] = 'INTERRUPTED' if interrupted else 'FAIL'
        if error:
            result.update(result='INTERRUPTED' if interrupted else 'FAIL', error=str(error))
        result['process_exit_code'] = code
        save(directory / 'result.json', result)
        print(f'LEKIWI_PREFLIGHT result={result["result"]} report={directory / "result.json"}', flush=True)
        if error:
            raise RuntimeError(f'{error}\n상세 로그: {directory / "sim.log"}')
        if interrupted and not host:
            raise KeyboardInterrupt
        return directory


def client(no_sandbox=False):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('이 클라이언트 실행기는 Ubuntu x86_64 노트북용입니다.')
    if os.geteuid() == 0:
        raise RuntimeError('클라이언트 전체를 sudo로 실행하지 마세요.')
    directory = data_root() / 'client'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'isaacsim-webrtc-1.1.5.AppImage'
    if not path.exists():
        print('NVIDIA 공식 WebRTC 클라이언트 1.1.5 다운로드 중입니다.', flush=True)
        temporary = path.with_suffix('.part')
        with urllib.request.urlopen(CLIENT_URL, timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
        with temporary.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise RuntimeError('다운로드한 파일이 Linux 실행 파일이 아닙니다.')
        temporary.chmod(0o700)
        temporary.replace(path)
    # FUSE 설치 없이 공식 AppImage를 사용자 폴더에 추출하여 실행합니다.
    app = directory / 'squashfs-root/AppRun'
    if not app.exists():
        subprocess.run([str(path), '--appimage-extract'], cwd=directory, check=True,
                       stdout=subprocess.DEVNULL)
    print('Server에 데스크탑 IP를 입력하고 Connect하세요. 포트는 기본값을 사용합니다.', flush=True)
    env = dict(os.environ, APPDIR=str(app.parent))
    env.pop('DEBUG', None)  # 공식 AppRun의 환경 전체 출력 옵션은 전달하지 않습니다.
    subprocess.run([str(app)] + (['--no-sandbox'] if no_sandbox else []), env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check', help='데스크탑 환경 확인만 수행 (설치 없음)')
    commands.add_parser('install', help='580 드라이버·Docker 준비 후 최소 장면 검사')
    commands.add_parser('build', help='Isaac Sim 최소 이미지 준비')
    commands.add_parser('verify', help='설치 없이 기존 이미지로 물리·렌더링 검사')
    server = commands.add_parser('serve', help='최소 장면을 노트북에 송출')
    server.add_argument('--host', required=True, help='데스크탑의 LAN IPv4')
    viewer = commands.add_parser('client', help='노트북: 공식 화면 클라이언트 다운로드·실행')
    viewer.add_argument('--no-sandbox', action='store_true', help='Ubuntu에서 Electron sandbox 오류일 때만 사용')
    commands.add_parser('report', help='가장 최근 점검 결과와 로그 경로 표시')
    args = parser.parse_args()
    if args.command in {'check', 'install'}:
        command = ['/usr/bin/python3', '-B', '-u', str(ROOT / 'tools/host_setup/main.py'), '--preflight']
        if args.command == 'check':
            command += ['--check']
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            raise SystemExit(completed.returncode)
    elif args.command == 'build':
        require_license()
        subprocess.run(compose(host_setup.docker_command()) + ['build', 'sim'], env=environment(), check=True)
    elif args.command in {'verify', 'serve'}:
        run_scene(args.host if args.command == 'serve' else None)
    elif args.command == 'client':
        client(args.no_sandbox)
    elif args.command == 'report':
        reports = list((data_root() / 'results').glob('check.*/result.json'))
        if not reports:
            raise RuntimeError('실행 기록이 없습니다. ./lekiwi preflight install을 먼저 진행하세요.')
        path = max(reports, key=lambda p: p.stat().st_mtime_ns)
        print(path.read_text())
        print(f'실패 시 이 결과와 {path.parent / "sim.log"}를 강사에게 전달하세요.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('사용자가 종료했습니다.', file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'중단: {exc}', file=sys.stderr)
        sys.exit(1)
