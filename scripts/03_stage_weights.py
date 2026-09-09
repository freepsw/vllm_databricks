#!/usr/bin/env python3
"""
03_stage_weights.py — Stage Qwen3.8-27B-FP8 model weights

Supports two paths:
  (A) HuggingFace direct download (default, requires egress to HF Hub)
  (B) UC Volume staging (requires pre-populated Volume)

Environment variables (os.environ only, no sys.argv):
  - MODEL_HF_ID: default "Qwen/Qwen3.8-27B-FP8"
  - LOCAL_PATH: default "/local_disk0/models/Qwen3.8-27B-FP8"
  - VOLUME_PATH: if set, copy from Volume instead of HF download
  - ALLOW_HF_DOWNLOAD: default "true" (set to "false" to require VOLUME_PATH)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def unset_fips_env():
    """Strip OPENSSL_* and FIPS_* variables to avoid FATAL FIPS SELFTEST FAILURE (rc=134)."""
    for key in list(os.environ.keys()):
        if key.startswith(("OPENSSL_", "FIPS_")):
            del os.environ[key]
    for key in ("PYTHONPATH", "LD_LIBRARY_PATH", "PYSPARK_PYTHON", "PYSPARK_DRIVER_PYTHON", "SPARK_HOME"):
        os.environ.pop(key, None)


def sh(cmd, timeout=7200):
    """Execute shell command with environment cleanup prefix."""
    pre = (
        "unset PYTHONPATH LD_LIBRARY_PATH PYSPARK_PYTHON PYSPARK_DRIVER_PYTHON SPARK_HOME; "
        "for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done; "
    )
    full_cmd = pre + cmd
    print(f"$ {cmd[:100]}{'...' if len(cmd) > 100 else ''}")
    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] timeout after {timeout}s")
        return -1, "(timeout)"


def download_from_hf(model_id, local_path, venv_path):
    """HuggingFace Hub에서 모델 가중치 다운로드"""
    print(f"\n[03] HuggingFace Hub에서 {model_id} 다운로드 중...")
    os.makedirs(local_path, exist_ok=True)

    # Build Python code for snapshot_download
    dl_script = (
        "import os;"
        "os.environ['HF_HUB_ENABLE_HF_TRANSFER']='1';"
        "os.environ['HF_HOME']='/local_disk0/hf';"
        "from huggingface_hub import snapshot_download;"
        f"p=snapshot_download('{model_id}',local_dir='{local_path}',"
        "max_workers=8,ignore_patterns=['*.pt','original/*']);"
        "print(f'Downloaded to: {{p}}')"
    )

    t0 = time.time()
    rc, output = sh(f"{venv_path}/bin/python -c {json.dumps(dl_script)}", timeout=7200)
    elapsed = time.time() - t0

    # Show last part of output
    lines = output.strip().splitlines()
    if lines:
        print("\n".join(lines[-10:]))

    print(f"[03] HF 다운로드 완료 rc={rc} 경과시간={elapsed:.0f}초")
    return rc == 0, elapsed


def stage_from_volume(volume_path, local_path):
    """UC Volume에서 /local_disk0로 모델 가중치 복사"""
    print(f"\n[03] {volume_path} → {local_path}에서 스테이징 중...")

    if not Path(volume_path).exists():
        print(f"[03] 오류: Volume 경로를 찾을 수 없습니다: {volume_path}")
        return False, 0

    if Path(local_path).exists():
        print(f"[03] 불완전한 스테이징 제거 중: {local_path}")
        shutil.rmtree(local_path)

    Path(local_path).mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    file_count = 0
    total_bytes = 0

    # Skip .cache directory
    for src_path in Path(volume_path).rglob("*"):
        if ".cache" in src_path.parts:
            continue

        rel_path = src_path.relative_to(volume_path)
        dst_path = Path(local_path) / rel_path

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            file_count += 1
            total_bytes += src_path.stat().st_size

    elapsed = time.time() - t0
    print(f"[03] {file_count}개 파일, {total_bytes / (1024**3):.2f} GB를 {elapsed:.0f}초에 스테이징 완료")

    return True, elapsed


def verify_model(local_path):
    """중요 파일 및 설정 검증"""
    print(f"\n[03] {local_path}의 모델 검증 중...")

    critical_files = ["config.json", "tokenizer_config.json", "model.safetensors.index.json"]
    missing = [f for f in critical_files if not Path(local_path, f).exists()]

    # Check safetensors count
    # 이 저장소의 shard 파일명은 layers-N.safetensors 형태다.
    # "model-*.safetensors"로 세면 0개가 나와 정상인데도 문제처럼 보인다.
    safetensors_files = list(Path(local_path).glob("*.safetensors"))
    shard_count = len(safetensors_files)

    # Get total size
    rc, du_out = sh(f"du -sh {local_path} 2>/dev/null")
    du_line = du_out.strip().split("\n")[0] if du_out.strip() else "unknown"

    print(f"[03] 디렉터리 크기: {du_line}")
    print(f"[03] Shard 개수: {shard_count} (예상: 66)"
          + ("" if shard_count == 66 else "  ← 예상과 다르면 다운로드 완료 여부를 확인하십시오"))

    # Verify config
    config_path = Path(local_path) / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = json.load(f)

            arch = cfg.get("architectures", [])
            quant_method = cfg.get("quantization_config", {}).get("quant_method")
            fmt = cfg.get("quantization_config", {}).get("fmt")
            # 이 모델은 max_position_embeddings가 text_config 하위에 있다.
            # 최상위에서 찾으면 None이 나와 정상인데도 문제처럼 보인다.
            max_pos = (cfg.get("text_config", {}).get("max_position_embeddings")
                       or cfg.get("max_position_embeddings"))

            print(f"[03] arch={arch} quant_method={quant_method} fmt={fmt} max_pos={max_pos}")

            # 중요 값 확인
            if "Qwen3_5ForConditionalGeneration" not in str(arch):
                print("[03] [경고] 예상치 못한 arch, 예상: Qwen3_5ForConditionalGeneration")
            if quant_method != "fp8":
                print("[03] [경고] 예상치 못한 quant_method, 예상: fp8")
            if fmt != "e4m3":
                print("[03] [경고] 예상치 못한 fmt, 예상: e4m3")

        except Exception as e:
            print(f"[03] [경고] config 파싱 실패: {e}")
    else:
        print("[03] [경고] config.json을 찾을 수 없습니다")

    if missing:
        print(f"[03] [오류] 누락된 중요 파일: {missing}")
        return False

    print("[03] 검증 성공")
    return True


def main():
    unset_fips_env()

    # Read environment variables
    model_id = os.environ.get("MODEL_HF_ID", "Qwen/Qwen3.8-27B-FP8")
    local_path = os.environ.get("LOCAL_PATH", "/local_disk0/models/Qwen3.8-27B-FP8")
    volume_path = os.environ.get("VOLUME_PATH")
    allow_hf = os.environ.get("ALLOW_HF_DOWNLOAD", "true").lower() in ("true", "1", "yes")
    venv_path = "/local_disk0/vllm028"

    print(f"[03] 설정: model={model_id} local={local_path}")
    if volume_path:
        print(f"[03]      volume={volume_path}")

    marker_file = Path(local_path) / ".stage_complete"

    # 이미 스테이징된 경우 확인
    if marker_file.exists():
        print(f"[03] 마커 존재 — 스테이징 스킵 ({local_path})")
        verify_model(local_path)
        return 0

    # UC Volume 경로가 제공된 경우 먼저 시도
    if volume_path:
        print(f"[03] VOLUME_PATH 제공됨. Volume → 로컬 스테이징 시도 중...")
        success, _ = stage_from_volume(volume_path, local_path)
        if success:
            marker_file.touch()
            verify_model(local_path)
            return 0
        else:
            print(f"[03] Volume 스테이징 실패")
            if not allow_hf:
                print("[03] ALLOW_HF_DOWNLOAD=false, 포기")
                return 1
            print("[03] HF 다운로드로 폴백 중")

    # HF 다운로드로 폴백
    if not allow_hf:
        print("[03] [오류] VOLUME_PATH가 설정되지 않았고 ALLOW_HF_DOWNLOAD=false")
        return 1

    # venv 존재 확인
    if not Path(venv_path).exists():
        print(f"[03] [오류] venv를 {venv_path}에서 찾을 수 없습니다. 먼저 02_build_venv.py를 실행하세요")
        return 1

    success, elapsed = download_from_hf(model_id, local_path, venv_path)
    if not success:
        print("[03] [오류] HF 다운로드 실패")
        return 1

    # 완료 마커 작성
    marker_file.touch()

    # 검증
    verify_result = verify_model(local_path)
    if not verify_result:
        print("[03] [오류] 다운로드 후 검증 실패")
        return 1

    print("[03] 스테이징 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
