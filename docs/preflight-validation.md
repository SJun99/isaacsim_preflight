# 사전 환경 점검 검증

공개 배포 대상은 `SJun99/isaacsim_preflight`의 `develop` 브랜치이며 실행 명령은 `./lekiwi preflight`입니다.
원본 `lekiwi_isaacsim`의 `test` 커밋 `683c7f9db74291064739b932218f800a4d0f8b89`에서 설치기·점검 장면·영상 연결과 관련 테스트만 가져왔습니다.
아래 실제 실행 기록은 2026-09-10 원본 코드에서 확인한 결과입니다. 5070·5080 실물 검증 기록은 아닙니다.

## 설치 범위

- 기존 Ubuntu 설치기의 580 계열(580.65.06 이상) 정책, 지원 GPU 후보 선택, 설치 전 확인, 재부팅 후 재검사를 재사용합니다.
- 사전 점검에서는 공식 Isaac Sim 5.1.0에 장면 스크립트와 WebRTC 설정만 추가합니다.
- 로봇 자산·교재·LeRobot·학습 패키지는 사전 점검 Docker 이미지에 포함하지 않습니다.
- 학생 노트북의 화면 클라이언트 명령은 Docker나 NVIDIA 드라이버 검사를 실행하지 않습니다.
- SDK 초기화는 CPU 작업 스레드를 최대 4개로 제한하고 단일 GPU를 사용합니다.

## 실제 실행

| 항목 | 확인 결과 |
|---|---|
| 개발 노트북 | Ubuntu 24.04, RTX 5060 Laptop 약 8GB, RAM 약 15GiB, 드라이버 580.173.02 |
| 서브 노트북 | Ubuntu 24.04, RTX 3070 Laptop 8GB, RAM 약 15.5GiB, 드라이버 580.173.02 |
| 설치 명령 연결 | 준비된 개발 PC에서 `preflight install` → 최소 이미지 빌드 → 실제 물리·렌더 검사 PASS |
| 최종 장면 자동 검사 | 개발 PC에서 시작 Z=1.0m → 최종 Z=0.0999997m, 640×480 PNG 및 해시 검사 PASS |
| 초기 실행 시간 | 개발 PC의 초기 셰이더 준비 포함 약 406초, 이후 최종 장면 재검사 약 25초. 기기별 시간 보장 아님 |
| 서브 노트북 송출 | 최소 장면 자동 검사 PASS 및 WebRTC 서버 준비 확인 |
| 공식 클라이언트 | 다운로드·사용자 폴더 추출·실행 확인. Ubuntu에서는 `--no-sandbox` 옵션으로 검증 |
| 두 PC 영상·입력 | 1280×720 실제 영상 수신, 표준 Play 입력 후 큐브 낙하 확인 |
| 종료 | Ctrl+C 후 해당 점검 컨테이너 종료 및 결과 STOPPED 보존 확인 |

## 실패를 성공으로 처리하지 않는 검사

- 종료 코드 0만으로 PASS를 표시하지 않습니다. 완료 표식, 결과 JSON, 저장 이미지 해시를 모두 검사합니다.
- 실제로 빈 카메라 결과 후 Kit이 0으로 종료하는 경우를 재현해 FAIL 판정을 확인했습니다.
- 준비 중인 결과나 누락된 결과, 이미지 변조, 오류 종료 코드도 실패로 처리합니다.
- sudo 인증은 원래 터미널 세션에서 유지하며 해당 실행의 컨테이너 라벨로만 정리합니다.
- 사용 중인 서버 포트와 다른 PC의 주소를 거부합니다. TCP 종료 대기 상태는 재실행을 막지 않도록 처리합니다.
- 최초 로컬 시도에서 운영체제의 메모리 부족 종료를 확인했습니다. 기본 부하를 줄이고 동일 PC의 통과를 확인했으나 여유 메모리가 다른 PC의 실행은 보장하지 않습니다.
- 클라이언트 추출 후 AppRun에 설치 경로를 명시해 옵션 전달 시 실행 파일을 못 찾는 문제를 수정했습니다.

## 자동 검사와 남은 현장 확인

관련 검사: `tests/test_preflight.py`, `tests/test_host_setup.py` — 71개 통과.
원본 전체 프로젝트에서는 회귀 검사 422개 통과, 호스트 USD 등 별도 환경이 필요한 8개를 생략했습니다. 이 공개 저장소에는 사전 점검과 설치기의 관련 테스트만 포함합니다.
Python·Bash 구문, README 상대 링크와 diff 검사도 통과했습니다.

2026-09-11 공개 저장소 분리 후 관련 검사 71개를 다시 통과했습니다.
분리 당시 설치기·점검 장면·영상 연결·Docker 구성 파일이 원본과 동일함을 확인했고, 독립 실행 메뉴와 문서 링크를 검사했습니다.
개발 PC에서 분리된 패키지의 읽기 전용 `preflight check`도 PASS를 확인했습니다.
개발 PC에 설치된 ROS용 pytest 플러그인과의 충돌을 피하려고 테스트에는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`을 사용합니다.

## 2026-09-11 빈 카메라 프레임 검사 수정

- 학생 RTX 5070 Ti의 로그에서 앱 초기화 완료 후 약 2초 만에 `(0,)` 크기의 빈 이미지로 실패한 것을 확인했습니다. 해당 PC의 정확한 내부 상태는 아직 확인되지 않았습니다.
- 개발 RTX 5060 Laptop에서 카메라 자동 생성을 끈 조건으로 같은 빈 이미지 오류를 재현했습니다. 원본은 물리·렌더 반복만 수행해 카메라 데이터 준비에 의존했습니다.
- 물리 낙하 검사 후 `rep.orchestrator.step(rt_subframes=4, delta_time=0.0, pause_timeline=False)`으로 촬영을 요청하고 완료 후 픽셀을 읽도록 수정했습니다. 물리 시간은 촬영 중 증가시키지 않습니다.
- 수정본은 자동 생성을 끈 상태에서도 실제 GPU 검사 PASS: 최종 큐브 높이 약 0.1m, 640×480 이미지, 공간 표준편차 약 40.2. 생성된 이미지에서 바닥과 큐브도 확인했습니다.
- 배포 이미지를 다시 빌드한 뒤 `preflight serve`에서도 물리·카메라 PASS와 `scene=READY`를 확인했습니다. 이번 수정 검증에서 노트북 클라이언트의 영상 수신을 다시 측정한 것은 아닙니다.
- 기존 오류·이미지·설치기 관련 자동 검사 71개를 다시 통과했습니다. 5070 Ti에서 수정본 재검사는 아직 필요합니다.

공식 API 근거: [시뮬레이션 특정 시점의 카메라 데이터 읽기](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/replicator_tutorials/tutorial_replicator_isaac_snippets.html#synthetic-data-access-at-specific-simulation-timepoints).

새 PC의 드라이버 실제 교체·Secure Boot 등록·재부팅은 이번 검증에서 수행하지 않았습니다.
학교망의 기기 간 통신 허용, 실제 학생 노트북의 화면 수신과 조작, 여러 학생 동시 접속은 현장에서 확인해야 합니다.
최소 장면의 PASS는 교육 전체 장면 성능·리더암·데이터 기록·학습·추론 검증을 대신하지 않습니다.

공식 근거: [Isaac Sim 5.1 WebRTC](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html),
[Ubuntu NVIDIA 드라이버](https://ubuntu.com/server/docs/nvidia-drivers-installation),
[Docker Ubuntu 설치](https://docs.docker.com/engine/install/ubuntu/),
[NVIDIA Container Toolkit 설치](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
