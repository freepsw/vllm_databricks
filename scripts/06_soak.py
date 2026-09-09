#!/usr/bin/env python3
# 06_soak.py — 8시간 안정성 테스트 (선택)
# 목적: 장시간 운영에서 VRAM 누수, 지연 열화, 오류 누적 없음을 확인
# 환경변수: DURATION_S(기본 28800=8h), CONCURRENCY(기본 22), MAX_TOKENS(기본 256)
#          PORT(기본 8005), SERVED(기본 qwen38-27b), OUT(기본 /local_disk0/SOAK_result.json)
# 전제: serve가 이미 띄어 있어야 함 (04_serve.sh 또는 별도 실행)
import json, os, re, subprocess, threading, time, urllib.request, urllib.error
from collections import deque

DURATION_S = int(os.environ.get("DURATION_S", 28800))   # 8h
CONCURRENCY = int(os.environ.get("CONCURRENCY", 22))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", 256))
PORT = int(os.environ.get("PORT", 8005))
SERVED = os.environ.get("SERVED", "qwen38-27b")
OUT = os.environ.get("OUT", "/local_disk0/SOAK_result.json")
DONE_MARKER = os.environ.get("DONE_MARKER", "/local_disk0/SOAK_DONE")
METRICS_LOG = os.environ.get("METRICS_LOG", "")  # 선택: 60초 간격 메트릭 로그

# KT 고객센터 상담 프롬프트 (8가지)
PROMPTS = [
    "고객이 요금제를 변경하고 싶다고 문의했습니다. 확인해야 할 사항을 3가지로 정리해 주세요.",
    "인터넷이 자주 끊긴다는 민원에 대해 1차 응대 스크립트를 작성해 주세요.",
    "다음 문장을 정중한 상담원 어투로 바꿔 주세요: '그건 안 됩니다.'",
    "휴대폰 분실 신고 절차를 순서대로 설명해 주세요.",
    "약정 해지 위약금이 발생하는 조건을 간단히 설명해 주세요.",
    "결합 할인이 적용되지 않는다는 문의에 어떻게 답변해야 하나요?",
    "데이터 초과 과금이 발생한 고객에게 안내할 내용을 정리해 주세요.",
    "해외 로밍 신청 방법과 주의사항을 알려 주세요.",
]

def http(path, body=None, timeout=300):
    """HTTP 요청 (GET 또는 POST)"""
    url = f"http://127.0.0.1:{PORT}{path}"
    try:
        if body is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                        headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)

def gpu_vram():
    """nvidia-smi로 GPU 메모리 조회 (MB)"""
    try:
        out = subprocess.run("nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu"
                            " --format=csv,noheader,nounits",
                            shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
        if not out:
            return None, None, None
        parts = out.split("\n")[0].split(",")
        used = int(parts[0].strip())
        total = int(parts[1].strip())
        util = int(parts[2].strip())
        return used, total, util
    except Exception:
        return None, None, None

def pct(values, quantile):
    """백분위수 계산"""
    if not values:
        return None
    s = sorted(values)
    idx = min(int(len(s) * quantile), len(s) - 1)
    return round(s[idx], 3)

# ============= 메인 루프 =============
print(f"[soak] 안정성 테스트 시작", flush=True)
print(f"  기간: {DURATION_S}초 ({DURATION_S/3600:.1f}시간)", flush=True)
print(f"  동시성: {CONCURRENCY}", flush=True)
print(f"  최대 토큰: {MAX_TOKENS}", flush=True)
print(f"  서버: http://127.0.0.1:{PORT}/{SERVED}", flush=True)

# /health 확인 및 대기
print(f"[soak] /health 확인...", flush=True)
health_ok = False
for attempt in range(12):
    st, _ = http("/health", timeout=20)
    if st == 200:
        health_ok = True
        print(f"[soak] /health 200 OK", flush=True)
        break
    print(f"  시도 {attempt+1}/12... (상태: {st})", flush=True)
    time.sleep(10)

if not health_ok:
    print(f"[soak] /health 타임아웃 — serve가 올라와 있는지 확인하세요", flush=True)
    result = {"error": "health_timeout", "timestamp": time.strftime("%FT%TZ", time.gmtime())}
    try:
        with open(OUT, "w") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception:
        pass
    with open(DONE_MARKER, "w") as f:
        f.write("FAILED_HEALTH")
    exit(1)

# 상태 변수
R = {
    "config": {
        "duration_s": DURATION_S,
        "concurrency": CONCURRENCY,
        "max_tokens": MAX_TOKENS,
        "port": PORT,
        "served_model": SERVED,
    },
    "timestamp_start": time.strftime("%FT%TZ", time.gmtime()),
    "vram_long": [],         # 60초 간격 전구간
    "vram_stats": {},        # 첫값·피크·마지막·샘플수·시간
    "metrics": [],           # 300초 간격 메트릭 (최근 구간만 유지)
    "health_checks": [],     # 1800초 간격 /health
    "hourly": {},            # 시간별 p50/p95
    "totals": {}
}

stop = threading.Event()
lock = threading.Lock()
n_ok = 0
n_err = 0
errs = deque(maxlen=40)
recent = deque(maxlen=4000)  # (t_rel, latency)
hourly = {}                  # hour -> list[latency]
t0 = time.perf_counter()

# Serve 다운 감지 임계값
CONSECUTIVE_HEALTH_FAILURES = 3  # /health 연속 실패 횟수
RECENT_WINDOW_MINUTES = 5        # 최근 창 시간 (분)
MIN_SUCCESS_IN_WINDOW = 1        # 최근 창에서 필요한 최소 성공 수
health_failure_count = 0
early_stop_reason = None

def bucket(tr, lat, ok):
    """시간대별 버킷에 기록"""
    h = int(tr // 3600)
    d = hourly.setdefault(h, {"lat": [], "ok": 0, "err": 0})
    if ok:
        d["lat"].append(lat)
        d["ok"] += 1
    else:
        d["err"] += 1

def worker(wid):
    """워커 스레드: 지속적으로 요청 송출"""
    global n_ok, n_err
    i = wid
    while not stop.is_set():
        i += CONCURRENCY
        # 요청마다 고유 접미사 → prefix cache가 전부 hit되지 않게 (보수적 조건)
        prompt = f"{PROMPTS[i % len(PROMPTS)]} (문의번호 {i})"
        body = {
            "model": SERVED,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
            "top_k": 20,
            "min_p": 0.0
        }

        ts = time.perf_counter()
        try:
            st, txt = http("/v1/chat/completions", body, timeout=300)
            lat = time.perf_counter() - ts
            # 성공: HTTP 200 + 실제 content가 있어야 함
            good = st == 200 and '"content"' in txt and len(txt) > 50

            with lock:
                if good:
                    n_ok += 1
                else:
                    n_err += 1
                    errs.append(f"HTTP{st}:{txt[:80]}" if st else f"HTTP None")
                recent.append((round(time.perf_counter() - t0, 1), round(lat, 3)))
                bucket(time.perf_counter() - t0, lat, good)
        except Exception as e:
            with lock:
                n_err += 1
                errs.append(f"{type(e).__name__}:{str(e)[:80]}")
                bucket(time.perf_counter() - t0, time.perf_counter() - ts, False)

def snapshot(final=False):
    """결과 저장"""
    with lock:
        # 최근 구간만 자라 파일 크기 억제 (전구간은 vram_long/vram_stats로 보존)
        R["metrics"] = R["metrics"][-200:]
        R["totals"] = {
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "ok": n_ok,
            "err": n_err,
            "rps": round(n_ok / max(time.perf_counter() - t0, 1), 2),
            "recent_p50": pct([l for _, l in recent], 0.5),
            "recent_p95": pct([l for _, l in recent], 0.95),
            "recent_errors": list(errs)[-8:],
            "verdict": "early_stop" if early_stop_reason else "completed"
        }
        if early_stop_reason:
            R["totals"]["early_stop_reason"] = early_stop_reason

        R["hourly"] = {
            str(h): {
                "ok": d["ok"],
                "err": d["err"],
                "n": len(d["lat"]),
                "p50": pct(d["lat"], 0.5),
                "p95": pct(d["lat"], 0.95)
            }
            for h, d in sorted(hourly.items())
        }

        # VRAM 회계: 전구간 보존 (처음·피크·마지막·샘플수·시간)
        st = R.get("vram_stats") or {}
        if st.get("first_mb"):
            f = st["first_mb"]
            l = st["last_mb"]
            R["totals"]["vram_first_mb"] = f
            R["totals"]["vram_last_mb"] = l
            R["totals"]["vram_peak_mb"] = st.get("peak_mb")
            R["totals"]["vram_drift_pct"] = round((l - f) / f * 100, 3) if f else None
            R["totals"]["vram_window_h"] = round((st["last_t"] - st["first_t"]) / 3600, 3)
            R["totals"]["vram_samples"] = st.get("n")

        # KV cache 사용률 (만약 있다면)
        cu = [m.get("gpu_cache_usage") for m in R["metrics"]
              if m.get("gpu_cache_usage") is not None]
        if len(cu) >= 2:
            R["totals"]["cache_usage_first"] = cu[0]
            R["totals"]["cache_usage_last"] = cu[-1]
            R["totals"]["cache_usage_max"] = max(cu)

        if final:
            R["timestamp_end"] = time.strftime("%FT%TZ", time.gmtime())

        try:
            with open(OUT, "w") as f:
                json.dump(R, f, indent=2, default=str)
        except Exception as e:
            print(f"[soak] 저장 실패: {e}", flush=True)

def sampler():
    """주기적 샘플링: VRAM, 메트릭, /health"""
    last_m = last_h = last_p = 0.0
    vram_long_last_append_t = 0.0

    while not stop.is_set():
        now = time.perf_counter()
        tr = round(now - t0, 1)

        # VRAM 샘플 (10초 간격)
        u, t, g = gpu_vram()
        if u is not None:
            with lock:
                st = R["vram_stats"]
                if "first_mb" not in st:
                    st["first_mb"] = u
                    st["first_t"] = tr
                st["peak_mb"] = max(st.get("peak_mb", 0), u)
                st["last_mb"] = u
                st["last_t"] = tr
                st["n"] = st.get("n", 0) + 1

                # 60초 간격 장기 시계열 유지 (8시간 = 약 480개)
                if not R["vram_long"] or tr - R["vram_long"][-1]["t"] >= 60:
                    R["vram_long"].append({"t": tr, "used_mb": u, "util": g})
                    vram_long_last_append_t = tr

        # 메트릭 샘플 (300초 간격)
        if now - last_m >= 300:
            last_m = now
            try:
                st, mt = http("/metrics", timeout=30)
                if st == 200:
                    # vLLM 0.28.0 실측 메트릭 이름
                    f = lambda p: (lambda m: float(m[0]) if m else None)(re.findall(p, mt))
                    with lock:
                        R["metrics"].append({
                            "t": tr,
                            "gpu_cache_usage": f(r"vllm:kv_cache_usage_perc\S*\s+([\d.e+-]+)"),
                            "running": f(r"vllm:num_requests_running\S*\s+([\d.e+-]+)"),
                            "waiting": f(r"vllm:num_requests_waiting\S*\s+([\d.e+-]+)"),
                            "prefix_hits": f(r"vllm:prefix_cache_hits_total\S*\s+([\d.e+-]+)"),
                            "prefix_queries": f(r"vllm:prefix_cache_queries_total\S*\s+([\d.e+-]+)")
                        })
            except Exception as e:
                with lock:
                    R["metrics"].append({"t": tr, "err": str(e)[:80]})

        # /health 점검 (1800초 간격 = 30분) + serve 다운 감지
        if now - last_h >= 1800:
            last_h = now
            global health_failure_count, early_stop_reason
            try:
                st, _ = http("/health", timeout=30)
                health_ok = (st == 200)
            except Exception as e:
                st = f"ERR {type(e).__name__}"
                health_ok = False

            with lock:
                R["health_checks"].append({"t": tr, "status": st})

                # Serve 다운 감지: /health 연속 실패 + 최근 창에 성공 없음
                if not health_ok:
                    health_failure_count += 1
                    if health_failure_count >= CONSECUTIVE_HEALTH_FAILURES:
                        # 최근 RECENT_WINDOW_MINUTES 동안의 성공 수 확인
                        window_start = now - (RECENT_WINDOW_MINUTES * 60)
                        recent_ok_count = sum(1 for t_rel, _ in recent if t0 + t_rel >= window_start and recent)
                        if recent_ok_count < MIN_SUCCESS_IN_WINDOW:
                            early_stop_reason = (
                                f"serve_down: /health failed {health_failure_count} times consecutive + "
                                f"only {recent_ok_count} success in last {RECENT_WINDOW_MINUTES}m"
                            )
                            stop.set()
                else:
                    health_failure_count = 0

            print(f"[soak] t={tr:.0f}s ok={n_ok} err={n_err} health={st}", flush=True)

        # 증분 저장 (60초 간격)
        if now - last_p >= 60:
            last_p = now
            snapshot()

        # 메트릭 로그 (선택, 60초 간격)
        if METRICS_LOG and now - vram_long_last_append_t < 5:
            try:
                with open(METRICS_LOG, "a") as f:
                    f.write(json.dumps({"t": tr, "ok": n_ok, "err": n_err,
                                       "vram_mb": u if u else None}) + "\n")
            except Exception:
                pass

        time.sleep(10)

# ============= 실행 =============
print(f"[soak] 워밍업", flush=True)
for _ in range(3):
    try:
        http("/v1/chat/completions",
             {"model": SERVED, "messages": [{"role": "user", "content": "안녕하세요"}],
              "max_tokens": 32, "chat_template_kwargs": {"enable_thinking": False}},
             timeout=300)
    except Exception:
        pass

print(f"[soak] 시작: {DURATION_S}초 / {CONCURRENCY} 동시성", flush=True)
threading.Thread(target=sampler, daemon=True).start()
workers = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(CONCURRENCY)]
for w in workers:
    w.start()

deadline = t0 + DURATION_S
hard_deadline = t0 + DURATION_S + 1800  # +30분 이상 돌면 무조건 종료

while time.perf_counter() < deadline and time.perf_counter() < hard_deadline:
    time.sleep(15)

print("[soak] 워커 정지 중", flush=True)
stop.set()
for w in workers:
    w.join(timeout=310)

snapshot(final=True)
print("[soak] 완료", flush=True)
print(json.dumps(R.get("totals", {}), indent=2, default=str), flush=True)

# 완료 마커
try:
    with open(DONE_MARKER, "w") as f:
        f.write(json.dumps(R.get("totals", {}), default=str))
    print(f"[soak] 완료 마커: {DONE_MARKER}", flush=True)
except Exception as e:
    print(f"[soak] 완료 마커 실패: {e}", flush=True)
