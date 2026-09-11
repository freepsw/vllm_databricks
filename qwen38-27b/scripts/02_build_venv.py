#!/usr/bin/env python3
"""
02_build_venv.py — Build isolated venv with vLLM 0.28.0

Environment cleanup and venv build for Qwen3.8-27B-FP8 + vLLM 0.28.0 on DBR 19.
Uses /local_disk0 for caching to preserve cluster storage.
"""

import os
import subprocess
import sys
import time
from pathlib import Path


def unset_fips_env():
    """Strip OPENSSL_* and FIPS_* variables to avoid FATAL FIPS SELFTEST FAILURE (rc=134)."""
    for key in list(os.environ.keys()):
        if key.startswith(("OPENSSL_", "FIPS_")):
            del os.environ[key]
    for key in ("PYTHONPATH", "LD_LIBRARY_PATH"):
        os.environ.pop(key, None)


def sh(cmd, timeout=2400, prefix_env_cleanup=True):
    """Execute shell command with optional environment cleanup prefix."""
    if prefix_env_cleanup:
        pre = (
            "unset PYTHONPATH LD_LIBRARY_PATH; "
            "for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done; "
        )
        cmd = pre + cmd

    print(f"$ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout
    )

    output = result.stdout + result.stderr
    lines = output.strip().splitlines()

    # Print last 25 lines
    if lines:
        print("\n".join(lines[-25:]))
    else:
        print("(no output)")

    print(f"  -> rc={result.returncode}")
    return result.returncode, output


def main():
    unset_fips_env()

    VENV = "/local_disk0/vllm028"

    # 캐시 디렉터리 설정
    os.makedirs("/local_disk0/pipcache", exist_ok=True)
    os.makedirs("/local_disk0/tmp", exist_ok=True)

    env_vars = (
        "TMPDIR=/local_disk0/tmp "
        "PIP_CACHE_DIR=/local_disk0/pipcache "
        "UV_CACHE_DIR=/local_disk0/uvcache"
    )

    t0 = time.time()

    # venv 이미 존재 확인
    if Path(f"{VENV}/bin/python").exists():
        print(f"[02] venv가 이미 {VENV}에 있습니다. 버전 확인 중...")
        rc, _ = sh(
            f"export {env_vars}; "
            f"{VENV}/bin/python -c "
            '"import vllm, torch, transformers; '
            'print(f\\"vllm {vllm.__version__} torch {torch.__version__} {torch.version.cuda} '
            'tf {transformers.__version__}\\")"',
            prefix_env_cleanup=True
        )
        if rc == 0:
            print("[02] venv가 이미 준비되었습니다. 재빌드를 건너뜁니다.")
            print(f"[02] 총 경과시간 {time.time()-t0:.0f}초")
            return 0
        else:
            print("[02] venv가 존재하지만 손상됨. 재빌드 중...")

    # Try to use uv
    uv_check = subprocess.run("which uv", shell=True, capture_output=True)
    have_uv = uv_check.returncode == 0

    if not have_uv:
        print("[02] uv를 찾을 수 없습니다. DBR 환경에 설치 중...")
        rc, _ = sh(
            f"export {env_vars}; {sys.executable} -m pip install --quiet uv",
            timeout=600,
            prefix_env_cleanup=True
        )
        have_uv = subprocess.run(
            f"{sys.executable} -m uv --version",
            shell=True,
            capture_output=True
        ).returncode == 0

    uv_cmd = "uv" if subprocess.run("which uv", shell=True, capture_output=True).returncode == 0 else f"{sys.executable} -m uv"

    if have_uv:
        print(f"[02] {uv_cmd} 사용 중")
        # venv 생성. `A || export ...; B` 형태는 `;`가 명령을 분리해 B가 무조건 실행되고
        # "already exists" 오류를 낸다. 중괄호로 묶어 진짜 폴백이 되게 한다.
        if os.path.exists(f"{VENV}/bin/python"):
            print(f"[02] venv가 이미 있습니다 — 생성 단계를 건너뜁니다: {VENV}")
            rc = 0
        else:
            rc, _ = sh(
                f"export {env_vars}; "
                f"{{ {uv_cmd} venv {VENV} --python 3.12 || {uv_cmd} venv {VENV}; }}",
                prefix_env_cleanup=True
            )
            if rc != 0:
                print(f"[02] [오류] venv 생성 실패 rc={rc}")
        rc, _ = sh(
            f"export {env_vars}; VIRTUAL_ENV={VENV} {uv_cmd} pip install --python {VENV}/bin/python "
            f'"vllm==0.28.0" "transformers>=5.8.0"',
            prefix_env_cleanup=True
        )
    else:
        print("[02] virtualenv + pip로 폴백 중")
        sh(f"export {env_vars}; virtualenv {VENV}", prefix_env_cleanup=True)
        rc, _ = sh(
            f"export {env_vars}; {VENV}/bin/pip install "
            f'"vllm==0.28.0" "transformers>=5.8.0"',
            prefix_env_cleanup=True
        )

    elapsed = time.time() - t0
    print(f"\n[02] venv 빌드 완료 {elapsed:.0f}초  rc={rc}")

    # Show disk usage
    sh(f"du -sh {VENV} 2>/dev/null; df -h /local_disk0 | tail -1", prefix_env_cleanup=False)

    # Show versions
    sh(
        f"export {env_vars}; {VENV}/bin/python -c "
        '"import vllm, torch, transformers; '
        'print(f\\"[02] SUCCESS: vllm {vllm.__version__} torch {torch.__version__} {torch.version.cuda} '
        'tf {transformers.__version__}\\")"',
        timeout=600,
        prefix_env_cleanup=True
    )

    return rc


if __name__ == "__main__":
    sys.exit(main())
