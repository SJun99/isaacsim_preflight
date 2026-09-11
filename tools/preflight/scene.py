"""외부 자산 없이 큐브 낙하·바닥 충돌·실제 카메라 렌더링을 검사합니다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import traceback


def write_report(output, value):
    path = output / 'verification.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host', help='지정한 서버 IPv4로 WebRTC 화면을 제공합니다')
    args = parser.parse_args()
    if os.environ.get('ACCEPT_EULA') != 'Y':
        raise RuntimeError('NVIDIA 라이선스 동의가 필요합니다: ACCEPT_EULA=Y')
    args.output.mkdir(parents=True, exist_ok=True)
    write_report(args.output, {'result': 'RUNNING'})
    started = time.monotonic()
    app = None
    failed = False
    try:
        from app_runtime import launch_config, enable_streaming
        if args.host:
            os.environ.update(LEKIWI_DISPLAY_MODE='webrtc', LEKIWI_STREAM_HOST=args.host)
        from isaacsim import SimulationApp
        app = SimulationApp(launch_config({
            'headless': True, 'hide_ui': False, 'width': 1280, 'height': 720,
            # 작은 점검 장면은 4개 CPU 작업 스레드와 단일 GPU로 메모리 부담을 줄입니다.
            'multi_gpu': False, 'limit_cpu_threads': 4, 'anti_aliasing': 0,
            'extra_args': ['--/exts/isaacsim.core.throttling/enable_async=false',
                           '--/app/hydraEngine/waitIdle=1',
                           '--/app/updateOrder/checkForHydraRenderComplete=1000']}))
        enable_streaming(app)
        import numpy as np
        import omni.usd
        from PIL import Image
        from pxr import Gf, UsdGeom, UsdLux
        from isaacsim.core.api import World
        from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
        import omni.replicator.core as rep
        from omni.kit.viewport.utility import get_active_viewport

        # 카메라 초기화 속도와 저장된 설정에 의존하지 않고 검사 시점에 직접 촬영합니다.
        rep.orchestrator.set_capture_on_play(False)
        world = World(physics_dt=1/60, rendering_dt=1/60, stage_units_in_meters=1.0)
        stage = omni.usd.get_context().get_stage()
        world.scene.add(FixedCuboid('/World/Ground', name='ground',
            position=np.array([0., 0., -.05]), scale=np.array([5., 5., .1]),
            color=np.array([.18, .23, .3])))
        cube = world.scene.add(DynamicCuboid('/World/TestCube', name='cube',
            position=np.array([0., 0., 1.]), scale=np.array([.2, .2, .2]),
            mass=.1, color=np.array([.9, .2, .05])))
        UsdLux.DomeLight.Define(stage, '/World/Light').CreateIntensityAttr(1000)
        camera_prim = UsdGeom.Camera.Define(stage, '/World/TestCamera')
        matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(3, -3, 2),
                                       Gf.Vec3d(0, 0, .4), Gf.Vec3d(0, 0, 1)).GetInverse()
        camera_prim.AddTransformOp().Set(matrix)
        camera_prim.CreateFocalLengthAttr(24)  # 낙하 전 높이 1 m의 큐브도 화면 안에 보이도록 시야를 넓힙니다.
        camera_prim.CreateClippingRangeAttr(Gf.Vec2f(.01, 100))
        world.reset()
        # 6장과 같은 Render Product/Annotator 경로로 실제 GPU 이미지를 읽습니다.
        product = rep.create.render_product('/World/TestCamera', (640, 480))
        rgb_reader = rep.AnnotatorRegistry.get_annotator('rgb')
        rgb_reader.attach([product.path])
        get_active_viewport().set_active_camera('/World/TestCamera')
        initial_z = float(cube.get_world_pose()[0][2])
        for _ in range(180):
            world.step(render=True)
        final_z = float(cube.get_world_pose()[0][2])
        if not initial_z > .8 or not .07 < final_z < .14:
            raise RuntimeError(f'큐브 낙하·바닥 충돌 검사 실패: z={initial_z} → {final_z} m')
        print(f'LEKIWI_PREFLIGHT physics=PASS initial_z={initial_z} final_z={final_z}', flush=True)
        # step은 Replicator 그래프를 준비하고 촬영 완료를 기다립니다.
        # 물리 시간은 그대로 유지해 낙하 검사 직후의 장면을 읽습니다.
        print('LEKIWI_PREFLIGHT camera=CAPTURING', flush=True)
        rep.orchestrator.step(rt_subframes=4, delta_time=0.0, pause_timeline=False)
        rgba = np.asarray(rgb_reader.get_data())
        if rgba.shape != (480, 640, 4) or not np.isfinite(rgba).all():
            raise RuntimeError(f'카메라 이미지 크기/수치 오류: {rgba.shape}')
        rgb = rgba[:, :, :3].astype(np.uint8)
        # 채널 간 색상 차이가 아닌 공간상의 변화를 확인합니다. 단색 화면은 통과하지 않습니다.
        variation = float(np.std(rgb.astype(float), axis=(0, 1)).max())
        if variation < 5:
            raise RuntimeError(f'카메라가 비어 있거나 단색입니다: spatial_std={variation}')
        preview = args.output / 'preview.png'
        Image.fromarray(rgb).save(preview)
        write_report(args.output, {
            'result': 'PASS', 'isaac_sim': '5.1.0',
            'physics': {'initial_z_m': initial_z, 'final_z_m': final_z},
            'render': {'width': 640, 'height': 480, 'spatial_std': variation,
                       'preview_sha256': hashlib.sha256(preview.read_bytes()).hexdigest()},
            'elapsed_s': round(time.monotonic()-started, 2),
            'scope': '최소 장면의 낙하·충돌·카메라 렌더링. 노트북 영상 수신·본 수업 성능은 별도 확인.'})
        print('LEKIWI_PREFLIGHT result=PASS', flush=True)
        rgb_reader.detach([product.path])
        product.destroy()
        if args.host:
            # 같은 초기 상태에서 학생이 표준 Play/Stop으로 직접 반복합니다.
            world.reset()
            world.stop()
            print('LEKIWI_PREFLIGHT scene=READY: Connect 후 Play를 눌러 큐브 낙하를 확인하세요.', flush=True)
            while app.is_running():
                world.step(render=True)
        world.stop()
    except Exception as exc:
        failed = True
        write_report(args.output, {'result': 'FAIL', 'error': str(exc)})
        traceback.print_exc()
    finally:
        if app:
            app.close()
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
