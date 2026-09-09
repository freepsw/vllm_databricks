# Databricks notebook source
# MAGIC %md
# MAGIC # Qwen3.8-27B (FP8) × vLLM 0.28.0 배포 실행 노트북
# MAGIC
# MAGIC 이 노트북은 `DEPLOYMENT_GUIDE.md`의 §2.4 ~ §5.2를 순서대로 실행합니다.
# MAGIC 각 셀을 **위에서 아래로 하나씩** 실행하십시오.
# MAGIC
# MAGIC ### 전제 조건
# MAGIC
# MAGIC 1. 가이드 §1.3으로 클러스터를 생성하고 **RUNNING** 상태입니다
# MAGIC 2. 이 노트북을 그 클러스터에 연결했습니다 (오른쪽 위 **Connect**)
# MAGIC 3. 가이드 §2.1 · §2.2 · §2.3 중 하나로 `scripts/` 폴더를 업로드했습니다
# MAGIC
# MAGIC ### 소요 시간
# MAGIC
# MAGIC | 셀 | 작업 | 소요 시간 |
# MAGIC |---|---|---|
# MAGIC | 1 | 스크립트 배포 | 몇 초 |
# MAGIC | 2 | 사전 점검 | 10초 이내 |
# MAGIC | 3 | venv 빌드 | 약 62초 |
# MAGIC | 4 | 가중치 확보 | 약 72초 (약 29 GB) |
# MAGIC | 5 | vLLM serve 기동 | 약 310초 |
# MAGIC | 6 | 검증 8항목 | 약 10분 |
# MAGIC | 7~9 | 장시간 안정성 (선택) | 기본 8시간 |
# MAGIC | 10 | 정리 | 몇 초 |
# MAGIC
# MAGIC ### 주의
# MAGIC
# MAGIC - `/local_disk0`은 **클러스터 재시작 시 초기화**됩니다. 재시작했다면 셀 1부터 다시 실행하십시오.
# MAGIC - 셀 5(serve)는 **5분 넘게 아무 출력이 없는 구간**이 있습니다. 정상입니다. 중단하지 마십시오.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. 스크립트 배포 (가이드 §2.4)
# MAGIC
# MAGIC 업로드한 스크립트를 드라이버의 `/local_disk0/scripts`로 복사합니다.
# MAGIC 위쪽에 나타나는 **`scripts_src`** 입력란을 자신의 업로드 경로로 맞추십시오.
# MAGIC
# MAGIC - UC Volume을 사용했다면 (§2.1): `/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts`
# MAGIC - DBFS를 사용했다면 (§2.2): `/dbfs/FileStore/qwen38-scripts`
# MAGIC - 워크스페이스 파일을 사용했다면 (§2.3): `/Workspace/Users/<사용자>/qwen38-scripts`
# MAGIC
# MAGIC **성공 판정**: 파일 7개가 나열되면 성공입니다.

# COMMAND ----------

dbutils.widgets.text("scripts_src", "/dbfs/FileStore/qwen38-scripts", "1. 스크립트 업로드 경로")
dbutils.widgets.text("soak_duration_s", "28800", "2. soak 시험 시간(초)")

SRC = dbutils.widgets.get("scripts_src")
DST = "/local_disk0/scripts"

import os, shutil, glob, stat
os.makedirs(DST, exist_ok=True)
copied = 0
for f in glob.glob(f"{SRC}/*"):
    shutil.copy(f, DST)
    copied += 1
for f in glob.glob(f"{DST}/*.sh"):
    os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)

if copied == 0:
    raise RuntimeError(
        f"'{SRC}' 에서 파일을 찾지 못했습니다. scripts_src 경로를 확인하십시오. "
        f"(UC Volume은 /Volumes/... , DBFS는 /dbfs/FileStore/... 로 시작합니다)")

print(f"SRC = {SRC}")
print(f"Scripts copied to {DST}. Files: {sorted(os.listdir(DST))}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. 사전 점검 (가이드 §1.8)
# MAGIC
# MAGIC GPU·드라이버·디스크·도구·네트워크를 점검합니다.
# MAGIC
# MAGIC **성공 판정**: 마지막 줄이 `[PASS] 모든 필수 조건 확인 완료`이고 `[종료코드] 0`입니다.
# MAGIC
# MAGIC `sensitive_env`가 `[WARN]`로 나오는 것은 **정상**입니다
# MAGIC (`OPENSSL_FORCE_FIPS_MODE`는 각 스크립트가 실행 시점에 제거합니다).

# COMMAND ----------

import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/00_check_prereq.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. venv 빌드 (가이드 §3.3)
# MAGIC
# MAGIC vLLM 0.28.0 전용 격리 환경을 만듭니다. DBR의 torch를 건드리지 않습니다.
# MAGIC
# MAGIC **성공 판정**: `[02] SUCCESS: vllm 0.28.0 torch 2.13.0+cu130 13.0 tf 5.16.1` · `[종료코드] 0`
# MAGIC (약 62초 · 7.6 GB)

# COMMAND ----------

import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/02_build_venv.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. 가중치 확보 (가이드 §3.4)
# MAGIC
# MAGIC 아래 셀은 **HuggingFace에서 직접 다운로드**합니다 (§3.4 A).
# MAGIC
# MAGIC **성공 판정**: `[03] Shard 개수: 66 (예상: 66)` · `[03] 검증 성공` · `[종료코드] 0`
# MAGIC (약 72초 · 약 29 GB)
# MAGIC
# MAGIC ### 폐쇄망이라면 (§3.4 B)
# MAGIC
# MAGIC 아래 셀의 `subprocess.run` **앞에** 두 줄을 추가하십시오.
# MAGIC `ALLOW_HF_DOWNLOAD=false`를 함께 주면 Volume 복사 실패 시 HF로 우회하지 않고 즉시 중단합니다.
# MAGIC
# MAGIC ```python
# MAGIC os.environ["VOLUME_PATH"] = "/Volumes/<catalog>/<schema>/<volume>/Qwen3.8-27B-FP8"
# MAGIC os.environ["ALLOW_HF_DOWNLOAD"] = "false"
# MAGIC ```

# COMMAND ----------

import os, subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/03_stage_weights.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. vLLM serve 기동 (가이드 §4)
# MAGIC
# MAGIC `04_serve.sh`가 환경 정리 → 분리 실행 → `/health` 대기 → 기동 로그 확인까지 수행합니다.
# MAGIC
# MAGIC ### ⚠️ 이 셀은 5분 이상 걸립니다
# MAGIC
# MAGIC `대기 중... (N초/1200초)`가 30초마다 찍히다가 **300초 부근에서 통과**합니다.
# MAGIC 그 사이 출력이 멈춘 것처럼 보이는 것은 정상입니다. **중단하지 마십시오.**
# MAGIC
# MAGIC **성공 판정**: `✓ /health 정상 응답` 후 `✅ serve 기동 완료`.
# MAGIC 기동 로그 oracle이 아래와 같으면 검증된 구성과 동일합니다.
# MAGIC
# MAGIC | 항목 | 기대값 |
# MAGIC |---|---|
# MAGIC | 커널 | `MarlinFP8ScaledMMLinearKernel` |
# MAGIC | attention 백엔드 | `FLASHINFER` |
# MAGIC | attention block size | `1568` |
# MAGIC | mamba padding | `0.13%` |
# MAGIC | GPU KV cache | `1,188,386` 토큰 · 동시성 `9.07x` |
# MAGIC | Available KV cache | `39.06 GiB` |
# MAGIC
# MAGIC 262K 컨텍스트가 필요하면 `MAX_MODEL_LEN=262144 bash /local_disk0/scripts/04_serve.sh`로 실행하십시오.

# COMMAND ----------

# MAGIC %sh
# MAGIC bash /local_disk0/scripts/04_serve.sh

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. 검증 (가이드 §5.1)
# MAGIC
# MAGIC 8개 항목을 점검합니다. 약 10분 걸리며 **출력은 끝에 한 번에** 표시됩니다.
# MAGIC
# MAGIC **성공 판정**: `모든 검증 통과 (8/8)` · `[종료코드] 0`
# MAGIC
# MAGIC 5번 항목의 p95 지연은 **참고값**이며 판정 기준이 아닙니다
# MAGIC (프롬프트 길이에 따라 달라집니다).

# COMMAND ----------

import os
os.environ["PORT"] = "8005"
os.environ["SERVED"] = "qwen38-27b"
os.environ["OUT"] = "/local_disk0/validate_result.json"
os.environ["SERVE_LOG"] = "/local_disk0/serve.log"

import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/05_validate.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. (선택) 장시간 안정성 시험 시작 (가이드 §5.2)
# MAGIC
# MAGIC 이미 떠 있는 serve에 지속 부하를 줍니다. **serve를 새로 띄우지 않습니다.**
# MAGIC
# MAGIC 위쪽 **`soak_duration_s`** 입력란으로 시험 시간을 정합니다.
# MAGIC
# MAGIC - `28800` = 8시간 (기본, 프로덕션 판정용)
# MAGIC - `600` = 10분 (계측과 결과 JSON만 빠르게 확인할 때)
# MAGIC
# MAGIC 분리 실행이므로 이 셀은 **즉시 끝납니다.** 진행 상황은 셀 8로 확인하십시오.

# COMMAND ----------

import os, subprocess, sys

DURATION_S = dbutils.widgets.get("soak_duration_s")

env = dict(os.environ)
env.update({
    "DURATION_S": DURATION_S,
    "CONCURRENCY": "22",
    "MAX_TOKENS": "256",
    "PORT": "8005",
    "SERVED": "qwen38-27b",
    "OUT": "/local_disk0/SOAK_result.json",
})

# 8시간 실행이므로 노트북 셀이 붙잡고 있지 않도록 분리 실행한다.
# 셀이 끊겨도 시험은 계속되며, 진행 상황은 다음 셀로 확인한다.
for stale in ("/local_disk0/SOAK_DONE", "/local_disk0/SOAK_result.json"):
    if os.path.exists(stale):
        os.remove(stale)

_p = subprocess.Popen([sys.executable, "/local_disk0/scripts/06_soak.py"],
                      stdout=open("/local_disk0/soak_stdout.log", "w"),
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      env=env, start_new_session=True)
print(f"분리 실행 시작 pid={_p.pid}  기간={DURATION_S}초")
print("로그: /local_disk0/soak_stdout.log")
print("진행 확인: 다음 셀을 반복 실행하십시오 (결과 JSON은 60초마다 갱신됩니다)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. (선택) 진행 상황 확인
# MAGIC
# MAGIC 이 셀은 **몇 번이든 반복 실행**할 수 있습니다. 결과 JSON은 60초마다 갱신됩니다.
# MAGIC
# MAGIC | 판정 항목 | 정상 |
# MAGIC |---|---|
# MAGIC | 오류 | **0** |
# MAGIC | VRAM drift | ±0.3% 이내 (기준선 0.122% / 8시간) |
# MAGIC | p95 지연 | 3시간 이후 평탄 |
# MAGIC | `/health` | 전부 200 |
# MAGIC
# MAGIC VRAM 누수는 **워밍업 구간(처음 30분)을 제외하고 2시간 이후 기울기**로 판정하십시오.
# MAGIC 처음 값과 마지막 값의 단순 비교는 워밍업이 앞 값을 낮춰 증가처럼 보입니다.

# COMMAND ----------

import json, os

OUT = "/local_disk0/SOAK_result.json"
DONE = "/local_disk0/SOAK_DONE"
LOG = "/local_disk0/soak_stdout.log"

done = os.path.exists(DONE)
print("완료 마커:", "있음 (시험 종료)" if done else "없음 (진행 중)")

if os.path.exists(OUT):
    with open(OUT) as f:
        R = json.load(f)
    t = R.get("totals", {})
    cfg = R.get("config", {})
    el = t.get("elapsed_s") or 0
    dur = cfg.get("duration_s") or 0
    ratio = (el / dur * 100) if dur else 0
    # verdict는 시험이 끝나기 전에도 "completed"로 채워져 있으므로
    # 완료 마커가 없으면 진행 중으로 표시한다.
    state = t.get("verdict") if done else "진행 중"
    print(f"경과 {el:.0f}초 / {dur}초 ({ratio:.1f}%)   상태: {state}")
    print(f"요청 성공 {t.get('ok')} · 오류 {t.get('err')} · 처리량 {t.get('rps')} rps")
    p50, p95 = t.get("recent_p50"), t.get("recent_p95")
    print(f"지연 p50 {p50}초 · p95 {p95}초" if p50 is not None else "지연: 집계 전 (첫 응답 대기 중)")
    print(f"VRAM {t.get('vram_first_mb')} → {t.get('vram_last_mb')} MB "
          f"(peak {t.get('vram_peak_mb')} · drift {t.get('vram_drift_pct')}% · "
          f"측정 창 {t.get('vram_window_h')}시간 · 샘플 {t.get('vram_samples')})")
    if t.get("cache_usage_max") is not None:
        print(f"KV 캐시 사용률 first {t.get('cache_usage_first')} · "
              f"last {t.get('cache_usage_last')} · max {t.get('cache_usage_max')}")
    hc = R.get("health_checks", [])
    print(f"/health 점검 {sum(1 for h in hc if h.get('status') == 200)}/{len(hc)} 200")
    if R.get("hourly"):
        print("시간대별:", {h: {"p50": d.get("p50"), "p95": d.get("p95"), "err": d.get("err")}
                          for h, d in R["hourly"].items()})
    if t.get("recent_errors"):
        print("최근 오류:", t["recent_errors"])
    if t.get("early_stop_reason"):
        print("조기 중단 사유:", t["early_stop_reason"])
else:
    print(f"{OUT} 아직 없습니다 (최초 스냅샷은 시작 후 약 60초)")

if os.path.exists(LOG):
    with open(LOG) as f:
        lines = f.readlines()
    print(f"\n--- soak_stdout.log 마지막 12줄 (총 {len(lines)}줄) ---")
    print("".join(lines[-12:]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. 정리 (가이드 §4.10)
# MAGIC
# MAGIC serve를 내리고 VRAM을 반환합니다. **재기동 전에 반드시 실행하십시오.**
# MAGIC
# MAGIC **성공 판정**: `VRAM 반환 확인: 0 MiB`
# MAGIC
# MAGIC ### ⚠️ `%sh` 셀에서 `pkill -f 'vllm serve'`를 쓰지 마십시오
# MAGIC
# MAGIC `%sh` 셸은 명령 문자열을 자기 argv에 담으므로 **자기 자신이 패턴에 매칭되어 즉사**합니다.
# MAGIC 셀은 출력 한 줄 없이 끝나는데 Databricks는 **성공으로 표시**합니다.
# MAGIC 또한 실제로 VRAM을 쥐고 있는 `VLLM::EngineCore`는 그 패턴에 걸리지 않아 살아남습니다.
# MAGIC
# MAGIC 시험을 중단하고 싶을 때는 아래 셀을 실행하면 soak 워커도 함께 정리됩니다.

# COMMAND ----------

import os, subprocess, time

PATTERNS = ("/vllm028/bin/vllm", "VLLM::EngineCore", "VllmWorker", "resource_tracker")
ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
killed = []
for line in ps.splitlines()[1:]:
    pid, _, args = line.strip().partition(" ")
    if pid == str(os.getpid()):
        continue
    if (any(x in args for x in PATTERNS) and "vllm028" in args) or "VLLM::" in args:
        try:
            os.kill(int(pid), 9); killed.append((pid, args[:40]))
        except Exception as e:
            print("skip", pid, e)
print("killed:", killed)

for i in range(24):
    used = int(subprocess.run(
        "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits",
        shell=True, capture_output=True, text=True).stdout.strip().split("\n")[0])
    if used < 5000:
        print(f"VRAM 반환 확인: {used} MiB ({i*2}초)"); break
    time.sleep(2)
else:
    print(f"VRAM 미반환: {used} MiB — 클러스터 재시작이 필요할 수 있습니다")

print(subprocess.run("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader",
                     shell=True, capture_output=True, text=True).stdout or "(GPU 점유 프로세스 없음)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. 클러스터 종료
# MAGIC
# MAGIC 시험이 끝나면 **클러스터를 종료해야 과금이 멈춥니다.**
# MAGIC `autotermination_minutes: 0`으로 설정했다면 자동 종료되지 않습니다.
# MAGIC
# MAGIC 로컬 머신에서 실행하십시오.
# MAGIC
# MAGIC ```bash
# MAGIC databricks clusters delete <CLUSTER_ID> --profile <PROFILE>
# MAGIC ```
# MAGIC
# MAGIC `terminate` 서브커맨드는 존재하지 않습니다. `delete`를 사용하십시오.
