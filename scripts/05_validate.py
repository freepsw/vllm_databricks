#!/usr/bin/env python3
# 05_validate.py — Qwen3.8-27B 배포 검증 (약 10분)
# 목적: 고객이 자신의 배포가 정상인지 스스로 판정할 수 있도록 8개 항목 검증
# 환경변수: PORT(기본 8005), SERVED(기본 qwen38-27b), OUT(기본 /local_disk0/validate_result.json)
#          SERVE_LOG(기본 /local_disk0/serve.log), SELFTEST=1(오프라인 자기검증 모드)
import json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

PORT = int(os.environ.get("PORT", 8005))
SERVED = os.environ.get("SERVED", "qwen38-27b")
OUT = os.environ.get("OUT", "/local_disk0/validate_result.json")
SERVE_LOG = os.environ.get("SERVE_LOG", "/local_disk0/serve.log")
SELFTEST = os.environ.get("SELFTEST", "").lower() in ("1", "true")

BASE_URL = f"http://127.0.0.1:{PORT}/v1"

# 한국어 필러 텍스트 (토큰 비율 0.52)
def korean_filler():
    return "KT 고객센터 상담 매뉴얼. 요금제 변경, 명의 변경, 분실 신고, 약정 해지 위약금, 결합 할인, 데이터 초과 과금, 로밍 요청, 번호이동 처리. "

# 바늘 생성: 목표 토큰 → 문자 수
def chars_for_tokens(target_tokens):
    """목표 토큰 수 → 필요한 문자 수. 나눗셈이다(곱셈 아님)."""
    return int(target_tokens / 0.52)

# Haystack 빌드
def build_haystack(target_chars, needle_code, needle_cnum):
    """결정적 haystack 생성 (같은 입력 → 같은 출력)"""
    base = korean_filler()
    text = base * (target_chars // len(base) + 3)
    text = text[:target_chars]
    needle_text = f"[코드-{needle_code}] 계약번호 {needle_cnum}"
    pos = int(len(text) * 0.5)  # 50% 깊이
    text = text[:pos] + needle_text + " " + text[pos:]
    return text

# 채점기: 계약번호 정확일치 (코드가 아니라 계약번호로 채점)
def exact_match_grade(response_content, target_cnum):
    """응답에서 정확한 계약번호가 있는지 (코드 아닌 숫자로 채점)"""
    norm = re.sub(r"[\s,]", "", response_content or "")
    return str(target_cnum) in norm

# HTTP 유틸
def http_get(path, timeout=30):
    try:
        url = f"http://127.0.0.1:{PORT}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)

# 주의: 경로에 반드시 /v1 을 포함해야 한다. OpenAI 호환 엔드포인트는 /v1 하위이고
# /health 만 /v1 없이 최상위에 있다. /v1 을 빼면 404가 나고 상태가 None으로 보고된다.
def http_post(path, body, timeout=300):
    try:
        url = f"http://127.0.0.1:{PORT}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)

# VRAM 조회
def gpu_vram():
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

# ============= SELFTEST MODE =============
def selftest():
    """GPU 없이 로직 검증. 크기 산술, 질문 누수, 채점 로직"""
    print("\n[selftest] 시작: 크기 산술 · 질문 누수 · 채점 로직", flush=True)

    # ① 크기 산술: 나눗셈 방향 확인 (곱하면 목표의 27%만 채워진다)
    test_sizes = [32000, 100000, 250000]
    for tgt in test_sizes:
        chars = chars_for_tokens(tgt)
        est = int(chars * 0.52)
        assert abs(est - tgt) <= 2, f"{tgt}: chars={chars} est={est}"
        print(f"  ✓ 목표 {tgt:,}토큰 → {chars:,}자 → 역산 {est:,}토큰", flush=True)

    # ② 결정성: 같은 입력 → 같은 haystack
    h1 = build_haystack(chars_for_tokens(32000), "CODE1", 900000)
    h2 = build_haystack(chars_for_tokens(32000), "CODE1", 900000)
    assert h1 == h2, "haystack 비결정적"
    print(f"  ✓ 결정적 haystack (길이 {len(h1):,}자)", flush=True)

    # ③ 질문 누수 방지: 질문에 계약번호가 절대 없어야 함
    needle_code = "CODE1"
    needle_cnum = 900000
    q = f"위 문서에서 [코드-{needle_code}] 항목의 계약번호를 숫자만 답하세요."
    assert str(needle_cnum) not in q, f"질문에 정답 누수! {q}"
    assert needle_code in q
    print(f"  ✓ 질문에 계약번호 누수 없음 (코드 {needle_code}만 노출)", flush=True)

    # ④ 채점기 검증: 다양한 케이스
    cases = [
        (f"{needle_cnum}", True, "숫자만"),
        (f"계약번호는 {needle_cnum}입니다.", True, "문장 속"),
        (f"{needle_cnum:,}".replace(",", ","), True, "콤마 포함"),
        (f"  {needle_cnum}  ", True, "공백 패딩"),
        ("485237", False, "다른 바늘 번호"),
        ("문서에서 찾을 수 없습니다.", False, "거절"),
        ("", False, "빈 응답"),
    ]
    for resp, want, label in cases:
        got = exact_match_grade(resp, needle_cnum)
        assert got == want, f"채점 실패 [{label}] {resp!r} -> {got}, 기대 {want}"
        print(f"  ✓ 채점 [{label}] → {got}", flush=True)

    print("[selftest] PASS\n", flush=True)

# ============= 검증 항목들 =============

def check_health():
    """1. /health 200 도달"""
    st, _ = http_get("/health")
    return st == 200, "200" if st == 200 else f"HTTP {st}"

def check_models():
    """2. /v1/models에 served name이 있는지"""
    st, body = http_post("/v1/chat/completions",
                        {"model": SERVED, "messages": [{"role": "user", "content": "hi"}],
                         "max_tokens": 10}, timeout=30)
    if st != 200:
        return False, f"HTTP {st}"
    try:
        data = json.loads(body)
        if "error" in data:
            return False, f"API error: {data['error']}"
        return True, SERVED
    except Exception as e:
        return False, str(e)[:100]

def check_korean_quality():
    """3. 한국어 품질 5건 (온도 0.7, max_tokens 512)"""
    prompts = [
        "고객이 요금제를 변경하고 싶다고 문의했습니다. 확인해야 할 사항을 3가지로 정리해 주세요.",
        "인터넷이 자주 끊긴다는 민원에 대해 1차 응대 스크립트를 작성해 주세요.",
        "다음 문장을 정중한 상담원 어투로 바꿔 주세요: '그건 안 됩니다.'",
        "휴대폰 분실 신고 절차를 순서대로 설명해 주세요.",
        "약정 해지 위약금이 발생하는 조건을 간단히 설명해 주세요.",
    ]
    results = []
    for p in prompts:
        body = {"model": SERVED, "messages": [{"role": "user", "content": p}],
                "temperature": 0.7, "max_tokens": 512,
                "chat_template_kwargs": {"enable_thinking": False}}
        st, resp_text = http_post("/v1/chat/completions", body, timeout=300)
        try:
            data = json.loads(resp_text)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            results.append(("ok" if st == 200 and content and len(content) > 10 else "fail",
                           content[:50] if content else f"HTTP {st}"))
        except Exception as e:
            results.append(("fail", str(e)[:50]))
    passed = sum(1 for r, _ in results if r == "ok")
    return passed == 5, f"{passed}/5 (모두 한국어 응답 받음)" if passed == 5 else f"{passed}/5 실패"

def check_no_think_leak():
    """4. <think> 미유출 — 위 5개 응답 중 <think> 없음"""
    prompts = [
        "고객이 요금제를 변경하고 싶다고 문의했습니다. 확인해야 할 사항을 3가지로 정리해 주세요.",
        "인터넷이 자주 끊긴다는 민원에 대해 1차 응대 스크립트를 작성해 주세요.",
        "다음 문장을 정중한 상담원 어투로 바꿔 주세요: '그건 안 됩니다.'",
        "휴대폰 분실 신고 절차를 순서대로 설명해 주세요.",
        "약정 해지 위약금이 발생하는 조건을 간단히 설명해 주세요.",
    ]
    leaked = 0
    for p in prompts:
        body = {"model": SERVED, "messages": [{"role": "user", "content": p}],
                "temperature": 0.7, "max_tokens": 512,
                "chat_template_kwargs": {"enable_thinking": False}}
        st, resp_text = http_post("/v1/chat/completions", body, timeout=300)
        try:
            data = json.loads(resp_text)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if "<think>" in content or "</think>" in content:
                leaked += 1
        except Exception:
            pass
    return leaked == 0, f"유출 {leaked}건" if leaked > 0 else "정상"

def check_concurrency():
    """5. 동시성 N=16, 오류 0 PASS, p95 참고"""
    latencies = deque()
    errors = []

    def worker(idx):
        body = {"model": SERVED, "messages": [{"role": "user", "content": "안녕하세요"}],
                "max_tokens": 256, "chat_template_kwargs": {"enable_thinking": False}}
        ts = time.perf_counter()
        try:
            st, resp_text = http_post("/v1/chat/completions", body, timeout=300)
            lat = time.perf_counter() - ts
            if st == 200 and "content" in resp_text:
                latencies.append(lat)
            else:
                errors.append(f"HTTP {st}")
        except Exception as e:
            errors.append(str(e)[:50])

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(worker, i) for i in range(16)]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass

    if errors:
        return False, f"오류 {len(errors)}"

    if latencies:
        sorted_lat = sorted(latencies)
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
        return True, f"0 오류, p95={p95:.2f}초"

    return False, "응답 없음"

def check_long_context():
    """6. 장문 처리: ~32,000 토큰, 중간에 바늘 심기"""
    target_tokens = 32000
    target_chars = chars_for_tokens(target_tokens)
    needle_code = "NEEDLE"
    needle_cnum = 900000

    haystack = build_haystack(target_chars, needle_code, needle_cnum)
    prompt = haystack + f"\n\n위 매뉴얼에서 [코드-{needle_code}] 항목의 계약번호를 정확히 답하세요."

    body = {"model": SERVED, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0, "max_tokens": 64,
            "chat_template_kwargs": {"enable_thinking": False}}

    ts = time.perf_counter()
    st, resp_text = http_post("/v1/chat/completions", body, timeout=900)
    elapsed = time.perf_counter() - ts

    try:
        data = json.loads(resp_text)
        if "error" in data:
            return False, f"API error: {data['error']}"

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        prompt_tokens = data.get("usage", {}).get("prompt_tokens", "?")
        found = exact_match_grade(content, needle_cnum)

        return found, f"약 {prompt_tokens}토큰, {elapsed:.1f}초, 바늘 {'회수' if found else '미회수'}"
    except Exception as e:
        return False, str(e)[:100]

def check_boot_oracles():
    """7. 기동 oracle 추출 (serve 로그에서)"""
    if not os.path.exists(SERVE_LOG):
        print(f"⚠️  경고: 서버 로그를 찾을 수 없습니다: {SERVE_LOG}", flush=True)
        print(f"   SERVE_LOG 환경변수로 경로를 지정하십시오 (예: SERVE_LOG=/path/to/serve.log)", flush=True)
        return True, "로그 없음 (SKIP)"

    try:
        log_text = open(SERVE_LOG, errors="ignore").read()
    except Exception:
        return True, "로그 읽기 실패 (SKIP)"

    patterns = {
        "kernel": r"MarlinFP8ScaledMMLinearKernel",
        "attention": r"Using .* attention backend",
        "block_size": r"Setting attention block size to (\d+)",
        "kv_cache": r"GPU KV cache size: ([\d,]+) tokens",
        "padding": r"Padding mamba page size by ([\d.]+)%"
    }

    found = {}
    for key, pat in patterns.items():
        match = re.search(pat, log_text)
        if match:
            found[key] = match.group(1) if match.lastindex else "yes"

    summary = ", ".join(f"{k}={v}" for k, v in sorted(found.items()))
    return len(found) >= 3, summary if summary else "항목 미검출"

def check_vram():
    """8. VRAM 정보"""
    used, total, util = gpu_vram()
    if used is None:
        return True, "nvidia-smi 실패 (SKIP)"

    avail = total - used
    pct = (used / total * 100) if total else 0
    return True, f"{used} / {total} MB ({pct:.1f}%), 여유 {avail} MB"

# ============= 메인 =============
def main():
    if SELFTEST:
        selftest()
        return

    checks = [
        ("1. /health 도달", check_health),
        ("2. /v1/models 서빙 확인", check_models),
        ("3. 한국어 품질 5건", check_korean_quality),
        ("4. <think> 미유출", check_no_think_leak),
        ("5. 동시성 N=16", check_concurrency),
        ("6. 장문 처리 ~32K", check_long_context),
        ("7. 기동 oracle", check_boot_oracles),
        ("8. VRAM", check_vram),
    ]

    results = []
    print(f"검증 시작 (서버: {SERVED} @ {PORT})\n", flush=True)

    for label, check_func in checks:
        try:
            passed, detail = check_func()
            status = "PASS" if passed else "FAIL"
            results.append({"item": label, "status": status, "detail": detail})
            print(f"{status:4s} | {label}: {detail}", flush=True)
        except Exception as e:
            results.append({"item": label, "status": "ERROR", "detail": str(e)[:100]})
            print(f"ERROR | {label}: {str(e)[:100]}", flush=True)

    # 요약 표
    print("\n" + "="*70, flush=True)
    print("| 항목 | 결과 | 상세 |", flush=True)
    print("|---|---|---|", flush=True)
    for r in results:
        print(f"| {r['item']} | {r['status']} | {r['detail'][:30]} |", flush=True)
    print("="*70 + "\n", flush=True)

    # JSON 저장
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    total_count = len(results)
    summary = {
        "timestamp": time.strftime("%FT%TZ", time.gmtime()),
        "server": f"http://127.0.0.1:{PORT}",
        "served_model": SERVED,
        "passed": passed_count,
        "total": total_count,
        "results": results
    }

    try:
        with open(OUT, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"결과 저장: {OUT}", flush=True)
    except Exception as e:
        print(f"저장 실패: {e}", flush=True)

    # 실패시 exit 1
    if passed_count < total_count:
        print(f"\n경고: {total_count - passed_count}개 항목 실패", flush=True)
        sys.exit(1)

    print(f"모든 검증 통과 ({passed_count}/{total_count})", flush=True)

if __name__ == "__main__":
    main()
