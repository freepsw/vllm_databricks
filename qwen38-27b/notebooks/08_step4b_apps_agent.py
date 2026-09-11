# Databricks notebook source
# MAGIC %md
# MAGIC # Qwen3.8-27B · STEP 4B 검증 — Databricks Apps agent 앱
# MAGIC
# MAGIC **이 노트북은 무엇인가**: STEP4B(Databricks Apps를 통한 driver-proxy 직접 호출) 배포 이후,
# MAGIC **앱과 상류 vLLM이 정말 동작하는지** 검증합니다.
# MAGIC
# MAGIC **이 노트북이 하지 않는 것**:
# MAGIC - ❌ PAT 생성 (노트북 컨텍스트 토큰이 되어 앱에서 403이 됩니다 — STEP4B §3에서 UI로 발급하십시오)
# MAGIC - ❌ 앱 생성 (UI 또는 CLI로 먼저 생성하고 secret 연결까지 완료한 상태로 시작)
# MAGIC - ❌ 앱 배포 (이 노트북은 검증만 합니다)
# MAGIC - ❌ T0(egress 대조군) — 노트북은 classic 컴퓨트라 serverless 정책 강제를 측정할 수 없습니다(셀 4 주석)
# MAGIC
# MAGIC **전제 조건**:
# MAGIC 1. 앱 `qwen-agent-ui` (또는 지정한 이름)이 생성·배포되어 `/Shared/qwen_agent_selftest.json` 을 쓸 권한이 있을 것
# MAGIC 2. PAT를 secret scope `qwen-agent` 의 `driver_pat` 키에 저장해 앱에 연결했을 것 (STEP4B §3·§4)
# MAGIC 3. vLLM이 **`--host 0.0.0.0`** 과 **`--tool-call-parser qwen3_xml`** 으로 기동되어 있을 것
# MAGIC 4. 이 노트북을 **Spark 컨텍스트가 있는 classic 클러스터**에 연결하고 실행할 것
# MAGIC    (호출은 전부 워크스페이스 URL → driver-proxy 로 나가므로 vLLM 이 도는 그 클러스터일 필요는 없습니다)
# MAGIC
# MAGIC **통과 기준**: 마지막 셀이 `✓ PASS` 를 출력할 때. **두 묶음이 모두** 통과해야 합니다
# MAGIC (증거는 `/Shared/step4b_evidence_<UTC>.json` 파일 **하나**로 남습니다).
# MAGIC
# MAGIC **① 노트북이 직접 부른 결과**
# MAGIC - T1 `/health` 200 · T2 `/v1/models` 200 **+ 모델 이름 일치 + `max_model_len` ≥ 기대값**
# MAGIC - T3 단발 대화 200 **+ 본문이 공백이 아님**
# MAGIC - T4 도구 루프 **2스텝 이상 + 종료 스텝 `finish_reason: stop` + 최종 답 있음 + 도구 인자 파싱 성공**
# MAGIC - T5 없는 경로가 **404 이고 본문이 정확히 `{"detail":"Not Found"}`**(중간 계층이 만든 404 를 걸러냅니다)
# MAGIC
# MAGIC **② 앱이 스스로 쓴 자기시험 파일**
# MAGIC - `verdict` 가 `PASS_` 로 시작 · `T4.ok` 이고 `steps` ≥ 2 · `T5` 404 + vLLM 본문
# MAGIC - **파일이 신선함**(기본 1시간 · 미래 시각도 거부) — 앱은 통과하면 재시도를 멈춰 파일이 갱신되지 않습니다
# MAGIC - **그 파일이 이 앱의 것임이 확인됨** — 자기시험 경로는 앱마다 공유되므로 파일의 `app_client_id` 를
# MAGIC   앱의 서비스 프린시펄과 대조합니다(모델·클러스터만으로는 같은 소스로 배포한 두 앱을 구분할 수 없습니다)
# MAGIC - 앱 조회가 성공하고 `app_status` 가 `RUNNING`
# MAGIC
# MAGIC | 셀 | 하는 일 | 소요 |
# MAGIC |---|---|---|
# MAGIC | 1 | 머리글 (이 셀) | — |
# MAGIC | 2 | 설정 및 위젯 | — |
# MAGIC | 3 | preflight — PAT 확인 · driver-proxy 도달성 · 모델/컨텍스트 대조 | 5초 |
# MAGIC | 4 | T1~T5 (노트북에서 직접) | 약 10초 |
# MAGIC | 5 | 앱 조회 + 앱 자기시험 파일 검증(신선도·귀속) | 5초 |
# MAGIC | 6 | 최종 판정 + 증거 기록 | 2초 |
# MAGIC
# MAGIC preflight 가 실패해도 **중간에 죽지 않고** 마지막 셀이 `❌ FAIL` 과 증거 파일을 남깁니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 2 · 설정 및 위젯
# MAGIC
# MAGIC 실행 환경에 맞게 정하십시오. 워크스페이스 호스트와 조직ID는 자동으로 인식합니다.

# COMMAND ----------
# ── 위젯 정의 ──
dbutils.widgets.text("cluster_id", "<클러스터ID>", "1. vLLM 클러스터 ID (필수)")
dbutils.widgets.text("port", "8005", "2. vLLM 포트")
dbutils.widgets.text("secret_scope", "qwen-agent", "3. secret scope")
dbutils.widgets.text("secret_key", "driver_pat", "4. secret 키")
dbutils.widgets.text("app_name", "<앱이름>", "5. 앱 이름 (필수)")
dbutils.widgets.text("expect_model", "qwen38-27b", "6. 기대하는 모델 이름")
dbutils.widgets.text("expect_ctx", "131072", "7. 최소 max_model_len (0=검사 안 함)")
dbutils.widgets.text("stale_limit_s", "3600", "8. 앱 자기시험 허용 나이(초)")

CLUSTER_ID   = dbutils.widgets.get("cluster_id").strip()
PORT         = dbutils.widgets.get("port").strip()
SCOPE        = dbutils.widgets.get("secret_scope").strip()
KEY          = dbutils.widgets.get("secret_key").strip()
APP_NAME     = dbutils.widgets.get("app_name").strip()
EXPECT_MODEL = dbutils.widgets.get("expect_model").strip()
def _int(widget, default):
    """`131,072` 처럼 콤마가 들어간 값도 받습니다(이 노트북이 그렇게 출력하므로)."""
    raw = dbutils.widgets.get(widget).strip().replace(",", "").replace("_", "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"위젯 '{widget}' 에 숫자를 넣으십시오 (받은 값: {raw!r}). 0 이면 그 검사를 건너뜁니다.")

EXPECT_CTX    = _int("expect_ctx", 0)
STALE_LIMIT_S = _int("stale_limit_s", 3600)

# 비어 있으면 조용히 잘못된 것을 검사하게 되므로 여기서 막습니다.
def _need(value, widget, hint):
    """자리표시자를 그대로 두고 실행하면 엉뚱한 대상을 검사하게 되므로 여기서 막습니다.
    값은 위젯 패널에 넣거나, 이 셀의 기본값을 직접 고쳐도 됩니다."""
    if not value or value.startswith("<"):
        raise ValueError(f"'{widget}' 를 채우십시오 — {hint} "
                         f"(위젯 패널에 입력하거나 이 셀의 기본값을 고치십시오.)")
    return value

CLUSTER_ID = _need(CLUSTER_ID, "1. vLLM 클러스터 ID",
                   "`databricks clusters list` 또는 클러스터 화면 URL 의 마지막 조각")
APP_NAME   = _need(APP_NAME, "5. 앱 이름", "UI 로 만든 앱 이름(앱 화면 제목)")

# ── 환경에서 자동 인식 ──
import json, time, urllib.request, urllib.error
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
HOST = "https://" + spark.conf.get("spark.databricks.workspaceUrl").rstrip("/")
ORG_ID = w.get_workspace_id()

BASE             = f"{HOST}/driver-proxy-api/o/{ORG_ID}/{CLUSTER_ID}/{PORT}"
DRIVER_PROXY_URL = f"{BASE}/v1/chat/completions"
HEALTH_URL       = f"{BASE}/health"
MODELS_URL       = f"{BASE}/v1/models"
NONEXISTENT_URL  = f"{BASE}/nonexistent-xyz"

print(f"워크스페이스   : {HOST}")
print(f"클러스터 ID    : {CLUSTER_ID}")
print(f"driver-proxy   : {BASE}")
print(f"secret         : {SCOPE}/{KEY}")
print(f"앱             : {APP_NAME}")
print(f"기대값         : 모델 {EXPECT_MODEL} · max_model_len {EXPECT_CTX:,}")
print(f"자기시험 허용 나이: {STALE_LIMIT_S}초")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 3 · Preflight — driver-proxy 도달성 점검
# MAGIC
# MAGIC **포지티브 테스트**: vLLM이 실제로 응답하는지 드라이버-프록시를 통해 확인합니다.
# MAGIC
# MAGIC ⚠️ **호출은 전부 워크스페이스 URL → driver-proxy 로 나갑니다(노트북이 vLLM 클러스터에 붙어 있을 필요는 없습니다).**

# COMMAND ----------
# ① secret 에서 PAT 읽기 — 값은 출력하지 않습니다.
try:
    PAT = dbutils.secrets.get(scope=SCOPE, key=KEY)
except Exception as e:
    raise RuntimeError(f"secret 읽기 실패: {SCOPE}/{KEY} — {type(e).__name__}: {e}")

if not PAT or not PAT.strip():
    raise RuntimeError(f"{SCOPE}/{KEY} 가 비어 있습니다. STEP4B §3 을 실행하십시오.")
if not (PAT.startswith("dapi") and len(PAT) >= 20):
    raise RuntimeError(
        "secret 에 든 값이 Databricks PAT 형식이 아닙니다(`dapi…`). "
        "STEP4B §3.1 에서 발급한 토큰을 저장했는지 확인하십시오.")
print("✓ secret 에서 PAT 를 읽었습니다 (값은 출력하지 않습니다)")

# ② 노트북 컨텍스트 토큰인지 확인 — 검사를 못 했으면 '통과' 라고 말하지 않습니다.
#    한계: 이것은 *이번 세션* 의 토큰과만 대조합니다. 다른 노트북 세션에서 만든
#    컨텍스트 토큰은 이 방법으로 잡을 수 없습니다(그래서 §3 은 UI 발급을 요구합니다).
ctx_check = "미검증"
try:
    nb_token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
    if PAT == nb_token:
        raise RuntimeError(
            "❌ secret 에 든 값이 **이 노트북 세션의 컨텍스트 토큰**입니다.\n"
            "   앱(serverless 외부 프로세스)에서 쓰면 403 이 됩니다.\n"
            "   Settings → Developer → Access tokens 에서 새로 발급해 secret 을 덮어쓰십시오(STEP4B §3).")
    ctx_check = "이번 세션 토큰과 다름"
except RuntimeError:
    raise
except Exception as e:
    print(f"⚠️  노트북 컨텍스트 토큰 검사를 실행할 수 없었습니다({type(e).__name__}) — **미검증**으로 둡니다")
print(f"{'✓' if ctx_check != '미검증' else '⚠️ '} 컨텍스트 토큰 검사: {ctx_check}")

# ③ 공통 호출 헬퍼
def _scrub(s):
    """기록·출력 전에 토큰 흔적을 지웁니다. 순서가 중요합니다 —
    본문이 잘리기 전에 PAT 원문을 먼저 치환해야 잘린 조각으로 남지 않습니다."""
    import re
    out = s or ""
    if PAT:
        out = out.replace(PAT, "<PAT>")
    out = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", out)
    out = re.sub(r"dapi[0-9a-f]{6,}", "dapi<redacted>", out)
    return out

def call(url, method="GET", body=None, timeout=120):
    req = urllib.request.Request(url, method=method,
                                data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {PAT}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), int((time.time() - t0) * 1000)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000)

# ④ 도달성 — 실패 시 원인을 갈라서 알려 줍니다.
preflight_ok, preflight_err = True, None
st, body, ms = call(HEALTH_URL)
if st != 200:
    print(f"❌ driver-proxy /health {st} ({ms}ms)  응답: {_scrub(body)[:400]}")
    if st == 403 and "Single-user" in body:
        print("   원인: PAT 신원이 클러스터 single user 가 아닙니다 (STEP4B §2).")
    elif st == 400 and "Terminated" in body:
        print("   원인: 클러스터가 종료됐습니다. 400 을 '경로가 열렸다'로 읽지 마십시오 (STEP4B §2).")
    elif st == 401:
        print("   원인: PAT 만료·무효 (STEP4B §8).")
    elif st == 502:
        print("   원인: vLLM 이 0.0.0.0 으로 바인드되지 않았거나 죽었습니다 (STEP4B §7 규칙 1).")
    preflight_ok, preflight_err = False, f"/health HTTP {st}"
else:
    preflight_ok, preflight_err = True, None
    print(f"✓ driver-proxy /health 200 ({ms}ms · 본문 {len(body)}바이트 — 정상 응답은 0바이트입니다)")

# ⑤ 모델 확인 — 기대값과 대조합니다(200 만으로는 판정하지 않습니다).
#    실패해도 여기서 죽지 않습니다 — 마지막 셀이 FAIL 판정과 증거 파일을 남기게 합니다.
st, body, ms = call(MODELS_URL)
data = []
if st != 200:
    preflight_ok, preflight_err = False, f"/v1/models HTTP {st}"
    print(f"❌ /v1/models {st} — {_scrub(body)[:200]}")
else:
    try:
        data = json.loads(body).get("data") or []
    except Exception as e:
        preflight_ok, preflight_err = False, f"/v1/models 응답이 JSON 아님({type(e).__name__})"
        print(f"❌ {preflight_err}: {_scrub(body)[:200]}")
if not data:
    preflight_ok = preflight_ok and False
    preflight_err = preflight_err or "/v1/models 에 모델이 없음"
    data = [{}]

# LoRA 어댑터가 함께 오는 경우가 있으므로 기대 이름을 먼저 찾습니다.
model_entry   = next((m for m in data if m.get("id") == EXPECT_MODEL), data[0])
model_name    = model_entry.get("id")
max_model_len = model_entry.get("max_model_len")
ctx_str       = f"{max_model_len:,}" if isinstance(max_model_len, int) else str(max_model_len)
print(f"{'✓' if preflight_ok else '❌'} 모델 {model_name} · max_model_len {ctx_str} · "
      f"서빙 목록 {[m.get('id') for m in data]}")
if model_name != EXPECT_MODEL:
    print(f"⚠️  기대 모델({EXPECT_MODEL})과 다릅니다 — T2 에서 실패로 처리합니다")
if EXPECT_CTX and not (isinstance(max_model_len, int) and max_model_len >= EXPECT_CTX):
    print(f"⚠️  max_model_len 이 최소값({EXPECT_CTX:,}) 미만입니다 — T2 에서 실패로 처리합니다")
if not preflight_ok:
    print(f"\n❌ preflight 실패({preflight_err}) — 이후 시험은 의미가 없지만, "
          "마지막 셀이 FAIL 판정과 증거를 남기도록 계속 진행합니다.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 4 · T1~T5 자기시험 (노트북에서)
# MAGIC
# MAGIC 앱의 자기시험과 **같은 5개 시험**을 노트북에서 실행합니다.
# MAGIC
# MAGIC **T0(egress 대조군)은 여기서 하지 않습니다** — egress 정책은 *serverless* 워크로드에
# MAGIC 걸리고 이 노트북은 classic 클러스터에서 돕니다. 노트북에서 측정하면 앱(serverless)의
# MAGIC 강제 여부와 무관한 값이 나와 **거짓 통과**가 됩니다. T0 은 앱의 자기시험(셀 5)에서만 읽습니다.

# COMMAND ----------
test_results = {}

def chat(messages, tools=None, max_tokens=1500, timeout=300):
    payload = {"model": model_name, "messages": messages,
               "max_tokens": max_tokens, "temperature": 0.0}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return call(DRIVER_PROXY_URL, method="POST", body=payload, timeout=timeout)

def parse(body):
    """(choice, error) — 200 이어도 JSON 이 아닐 수 있으므로 반드시 통과시킵니다."""
    try:
        j = json.loads(body)
    except Exception as e:
        return None, f"JSON 파싱 실패({type(e).__name__})"
    ch = (j.get("choices") or [None])[0]
    if not ch:
        return None, "choices 가 비었습니다"
    return ch, None

# ── T1 /health ──────────────────────────────────────────────────────────────
st, body, ms = call(HEALTH_URL)
test_results["T1_health"] = {"http": st, "ms": ms, "ok": st == 200}

# ── T2 /v1/models — 200 + 모델 이름 + max_model_len 을 모두 봅니다 ──────────
st, body, ms = call(MODELS_URL)
t2_model, t2_ctx = None, None
if st == 200:
    try:                                   # 이 호출의 본문으로 판정합니다(preflight 값 재사용 금지)
        _d = (json.loads(body).get("data") or [])
        _e = next((m for m in _d if m.get("id") == EXPECT_MODEL), (_d or [{}])[0])
        t2_model, t2_ctx = _e.get("id"), _e.get("max_model_len")
    except Exception:
        pass
t2_model_ok = (t2_model == EXPECT_MODEL)
t2_ctx_ok   = (not EXPECT_CTX) or (isinstance(t2_ctx, int) and t2_ctx >= EXPECT_CTX)
test_results["T2_models"] = {
    "http": st, "ms": ms, "model": t2_model, "max_model_len": t2_ctx,
    "expected_model": EXPECT_MODEL, "min_max_model_len": EXPECT_CTX or None,
    "ok": st == 200 and t2_model_ok and t2_ctx_ok,
}
if not t2_model_ok:
    print(f"❌ T2: 모델 이름이 기대와 다릅니다 — {model_name!r} ≠ {EXPECT_MODEL!r}")
if not t2_ctx_ok:
    print(f"❌ T2: max_model_len 이 기대와 다릅니다 — {max_model_len} ≠ {EXPECT_CTX}")

# ── T3 단발 대화 — content 가 공백만인 경우도 실패입니다 ────────────────────
st, body, ms = chat([{"role": "user", "content": "한 문장으로 자기소개를 하라."}], max_tokens=1500)
rec = {"http": st, "ms": ms, "ok": False}
if st == 200:
    ch, err = parse(body)
    if err:
        rec["error"] = err
    else:
        content = (ch.get("message", {}) or {}).get("content") or ""
        rec.update({"finish_reason": ch.get("finish_reason"),
                    "content_len": len(content), "content_head": content[:100],
                    "ok": bool(content.strip())})
        if not rec["ok"]:
            print(f"❌ T3: content 가 비었습니다 (finish_reason={ch.get('finish_reason')}) "
                  f"— 추론 토큰이 예산을 다 쓴 경우입니다. max_tokens 를 늘리십시오(STEP4B §8).")
else:
    rec["error"] = _scrub(body)[:200]
test_results["T3_plain_chat"] = rec

# ── T4 도구 호출 루프 ───────────────────────────────────────────────────────
# 앱과 같은 표면을 시험하기 위해 **필수 인자를 받는 도구**를 하나 둡니다 —
# 인자 직렬화/파싱(`--tool-call-parser qwen3_xml`)이 깨지는 것이 가장 흔한 회귀입니다.
TOOLS = [
    {"type": "function", "function": {
        "name": "get_cluster_state",
        "description": "주어진 Databricks 클러스터의 상태를 조회한다.",
        "parameters": {"type": "object",
                       "properties": {"cluster_id": {"type": "string", "description": "클러스터 ID"}},
                       "required": ["cluster_id"]}}},
    {"type": "function", "function": {
        "name": "get_model_info",
        "description": "지금 서비스 중인 모델과 최대 컨텍스트 길이를 조회한다.",
        "parameters": {"type": "object", "properties": {}}}},
]
OFFERED = {t["function"]["name"] for t in TOOLS}

def agent_loop(question, max_steps=4):
    msgs = [{"role": "system", "content": "너는 도구로 확인한 뒤 답한다. 도구 결과에 없는 것은 추측하지 않는다."},
            {"role": "user", "content": question}]
    trace, args_seen, unknown = [], {}, []
    for step in range(1, max_steps + 1):
        st, body, ms = chat(msgs, tools=TOOLS, max_tokens=1500)
        if st != 200:
            trace.append({"step": step, "http": st, "error": _scrub(body)[:200]})
            return {"ok": False, "steps": step, "trace": trace, "reason": f"HTTP {st}"}
        ch, err = parse(body)
        if err:
            trace.append({"step": step, "http": st, "error": err})
            return {"ok": False, "steps": step, "trace": trace, "reason": err}
        msg     = ch.get("message", {}) or {}
        finish  = ch.get("finish_reason")
        calls   = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        names   = [c.get("function", {}).get("name") for c in calls]
        trace.append({"step": step, "http": st, "ms": ms, "finish_reason": finish,
                      "tool_calls": names, "content_len": len(content)})
        unknown += [n for n in names if n not in OFFERED]

        if not calls:
            # 종료 스텝의 품질까지 봅니다: stop 이어야 하고 본문이 있어야 합니다.
            # 판정 대상은 **프로토콜이 도는지**입니다 — 모델이 어느 도구를 고르는지는 아닙니다.
            reasons, notes = [], []
            if step < 2:              reasons.append("도구를 한 번도 부르지 않음(1스텝 종료)")
            if finish != "stop":      reasons.append(f"finish_reason={finish!r} (stop 이어야 함)")
            if not content.strip():   reasons.append("최종 답이 비었음")
            if not args_seen:         reasons.append("도구 인자를 하나도 파싱하지 못함")
            if unknown:               reasons.append(f"제공하지 않은 도구 호출: {sorted(set(unknown))}")
            # 인자 값 자체는 모델이 다시 써 넣는 값이므로 **경고**로만 봅니다.
            _got = (args_seen.get("get_cluster_state") or {}).get("cluster_id")
            if _got is not None and str(_got).strip().casefold() != CLUSTER_ID.strip().casefold():
                notes.append(f"도구 인자 cluster_id 가 다릅니다: {_got!r} (판정에는 반영하지 않음)")
            return {"ok": not reasons, "steps": step, "trace": trace,
                    "answer_head": content[:200], "tool_args": args_seen,
                    "notes": notes or None,
                    "reason": "; ".join(reasons) or None}

        msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
        for c in calls:
            name = c.get("function", {}).get("name")
            raw  = c.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception as e:
                return {"ok": False, "steps": step, "trace": trace,
                        "reason": f"도구 인자가 JSON 이 아님({name}): {type(e).__name__} — tool-call-parser 확인"}
            args_seen[name] = args
            result = ({"cluster_id": args.get("cluster_id"), "state": "RUNNING"}
                      if name == "get_cluster_state"
                      else {"model": model_name, "max_model_len": max_model_len})
            msgs.append({"role": "tool", "tool_call_id": c.get("id", name),
                         "content": json.dumps(result, ensure_ascii=False)})
    return {"ok": False, "steps": max_steps, "trace": trace, "tool_args": args_seen,
            "reason": "max_steps 안에 stop 으로 끝나지 않음"}

agent_result = agent_loop(
    f"클러스터 {CLUSTER_ID} 의 상태와 지금 서비스 중인 모델의 최대 컨텍스트 길이를 도구로 확인해서 알려줘.")
test_results["T4_agent_tool_loop"] = agent_result

# ── T5 음성 시험 — 404 이면서 vLLM(FastAPI) 의 본문이어야 합니다 ────────────
def _is_vllm_404(b):
    """`{"detail":"Not Found"}` 정확 일치. 중간 계층 404 페이지도 "Not Found" 를 포함하므로
    부분문자열로는 상류 도달을 증명하지 못합니다."""
    try:
        return json.loads(b or "").get("detail") == "Not Found"
    except Exception:
        return False

st, body, ms = call(NONEXISTENT_URL)
t5_ok = (st == 404 and _is_vllm_404(body))
test_results["T5_negative_404"] = {"http": st, "ms": ms, "ok": t5_ok,
                                   "body_head": _scrub(body)[:150]}
if st == 404 and not t5_ok:
    print(f"❌ T5: 404 이지만 본문이 vLLM 의 `{{\"detail\":\"Not Found\"}}` 가 아닙니다 — "
          f"중간 계층이 만든 404 일 수 있습니다: {_scrub(body)[:120]}")
if st == 200:
    print("❌ T5: 없는 경로가 200 입니다 — 앞단이 상류를 보지 않고 응답하는 구성입니다.")
    print("   이 경우 T1~T4 의 200 도 증거로 쓸 수 없습니다(STEP4B §5.1).")

# ── 요약 ────────────────────────────────────────────────────────────────────
def mark(k): return "✓" if test_results[k]["ok"] else "❌"
print("\n노트북 자기시험 요약")
print(f"  T1 /health      {mark('T1_health')} HTTP {test_results['T1_health']['http']} · {test_results['T1_health']['ms']}ms")
print(f"  T2 /v1/models   {mark('T2_models')} HTTP {test_results['T2_models']['http']} · {model_name} · {ctx_str}")
print(f"  T3 단발 대화    {mark('T3_plain_chat')} HTTP {test_results['T3_plain_chat']['http']} · {test_results['T3_plain_chat'].get('content_len','-')}자 · {test_results['T3_plain_chat']['ms']}ms")
print(f"  T4 도구 루프    {mark('T4_agent_tool_loop')} {agent_result['steps']}스텝 · 인자 {agent_result.get('tool_args')} {'· ' + agent_result['reason'] if agent_result.get('reason') else ''}")
print(f"  T5 음성 404     {mark('T5_negative_404')} HTTP {test_results['T5_negative_404']['http']} · {test_results['T5_negative_404']['body_head']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 5 · 앱의 자기시험 결과 검증
# MAGIC
# MAGIC 앱이 자동 실행 후 `/Shared/qwen_agent_selftest.json` 에 쓴 파일을 읽고 검증합니다.

# COMMAND ----------
import json
from datetime import datetime, timezone

SELFTEST_PATH = "/Shared/qwen_agent_selftest.json"

# 셀을 따로 돌렸을 때를 대비한 방어값 (전체 실행에서는 셀 4 가 정합니다)
try:
    preflight_ok
except NameError:
    preflight_ok, preflight_err = False, "preflight 셀을 실행하지 않았습니다"

# 앱을 조회합니다. 여기서 실패하면 **검증 실패**입니다 — 앱을 확인하지 못한 채
# 파일만 읽으면 "다른 앱의 PASS" 를 이 앱의 결과로 읽게 됩니다.
app_state = app_compute = app_sp_client_id = app_deploy_time = None
app_err = None
try:
    _app = w.apps.get(name=APP_NAME)
    _st = getattr(_app.app_status, "state", None)
    app_state = _st.value if _st is not None else None
    _cs = getattr(_app.compute_status, "state", None)
    app_compute = _cs.value if _cs is not None else None
    app_sp_client_id = _app.service_principal_client_id
    _ad = getattr(_app, "active_deployment", None)
    app_deploy_time = getattr(_ad, "create_time", None) if _ad else None
    print(f"{'✓' if app_state == 'RUNNING' else '❌'} 앱 {APP_NAME}: "
          f"app_status={app_state} · compute={app_compute} · 배포 {app_deploy_time}")
except Exception as e:
    app_err = f"{type(e).__name__}: {e}"
    print(f"❌ 앱 {APP_NAME} 을 조회하지 못했습니다 — {app_err}")
    print("   앱 이름이 맞는지, 그 앱에 대한 권한이 있는지 확인하십시오. **검증 실패로 처리합니다.**")

# ① 파일 읽기 — 워크스페이스 파일입니다(/dbfs 가 아닙니다).
#    앱은 /api/2.0/workspace/import 로 쓰므로 워크스페이스 네임스페이스에 있습니다.
selftest_data, read_err = {}, None
try:
    raw = w.workspace.download(SELFTEST_PATH).read().decode("utf-8")
    selftest_data = json.loads(raw)
    print(f"✓ {SELFTEST_PATH} 읽음 ({len(raw)} 바이트)")
except Exception as e:
    read_err = f"{type(e).__name__}: {e}"
    print(f"❌ {SELFTEST_PATH} 를 읽지 못했습니다 — {read_err}")
    print("   ① 앱이 배포·기동됐는지 ② 앱 SP 가 /Shared 에 쓸 수 있는지 확인하십시오.")
    print("   (앱이 살아 있으면 앱 화면의 '자기시험 결과' 버튼으로도 볼 수 있습니다.)")

tests   = selftest_data.get("tests") or {}
verdict = selftest_data.get("verdict") or ""

def http_of(name): return (tests.get(name) or {}).get("http")

# ② 신선도 — 과거로 너무 오래된 것도, **미래 시각**도 실패입니다.
#    앱은 통과할 때까지만 재시도하므로 통과 후에는 파일이 갱신되지 않습니다.
ts_str, fresh_ok, age_s = selftest_data.get("ts_utc", ""), False, None
if ts_str:
    try:
        ts_obj = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts_obj.tzinfo is None:
            ts_obj = ts_obj.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - ts_obj).total_seconds()
        fresh_ok = (-60 <= age_s <= STALE_LIMIT_S)     # 미래 시각(음수)도 거부
        print(f"{'✓' if fresh_ok else '❌'} 파일 시각 {ts_str} · {age_s:+.0f}초"
              f"{'' if fresh_ok else '  ← 허용 범위(-60 ~ ' + str(STALE_LIMIT_S) + '초) 밖'}")
        if not fresh_ok and age_s > STALE_LIMIT_S:
            print("   → 같은 소스로 앱을 **다시 Deploy** 하면 기동 시 자기시험이 다시 돌아 갱신됩니다.")
        if age_s is not None and age_s < -60:
            print("   → 앱 호스트 시계가 앞서 있습니다. 미래 시각 파일을 증거로 쓰지 않습니다.")
    except Exception as e:
        print(f"❌ ts_utc 파싱 실패({type(e).__name__}) — 신선도 미확인으로 실패 처리합니다")
elif not read_err:
    print("❌ ts_utc 가 없습니다 — 신선도를 판정할 수 없어 실패 처리합니다")

# ③ 이 파일이 **정말 이 앱의 것인가.**
#    자기시험 경로는 앱마다 공유되므로, 모델·클러스터로는 구분되지 않습니다 —
#    같은 소스로 배포한 두 앱은 모델도 클러스터도 같기 때문입니다(실제로 그런 상황이 있었습니다).
#    그래서 앱이 파일에 남긴 `app_client_id`(= 앱 서비스 프린시펄) 로 확정합니다.
attributed, attribution_notes = False, []
file_client_id = selftest_data.get("app_client_id")
if selftest_data:
    file_app_name = selftest_data.get("app_name_env")
    if file_client_id and app_sp_client_id:
        attributed = (file_client_id == app_sp_client_id)
        if not attributed:
            attribution_notes.append("파일을 쓴 앱이 이 앱이 아닙니다 "
                                     f"(파일 {file_client_id[:8]}… ≠ {APP_NAME} {app_sp_client_id[:8]}…)")
        # 앱 런타임이 넣어 주는 앱 이름(`DATABRICKS_APP_NAME`)도 있으면 함께 봅니다.
        if attributed and file_app_name and file_app_name != APP_NAME:
            attributed = False
            attribution_notes.append(f"파일의 앱 이름이 다릅니다: {file_app_name!r} ≠ {APP_NAME!r}")
    elif not file_client_id:
        attribution_notes.append("파일에 `app_client_id` 가 없습니다 — 앱이 구버전 app.py 로 배포됐습니다. "
                                 "최신 app.py 로 재배포하면 확정할 수 있습니다")
    else:
        attribution_notes.append("앱 조회에 실패해 대조할 수 없습니다")

    # 보조 확인: 파일 시각이 **이 앱의** 배포 시각 이후여야 합니다.
    if attributed and app_deploy_time and selftest_data.get("ts_utc"):
        try:
            _dep = datetime.fromisoformat(str(app_deploy_time).replace("Z", "+00:00"))
            _ts  = datetime.fromisoformat(selftest_data["ts_utc"].replace("Z", "+00:00"))
            if _ts.tzinfo is None: _ts = _ts.replace(tzinfo=timezone.utc)
            if _ts < _dep:
                attributed = False
                attribution_notes.append(f"파일 시각({selftest_data['ts_utc']})이 이 앱의 배포 시각({app_deploy_time}) 이전입니다")
        except Exception:
            pass
    print(f"{'✓' if attributed else '❌'} 귀속 확인: "
          + (f"파일을 쓴 앱 = {file_app_name or '(이름 없음)'} · app_client_id 가 이 앱의 서비스 프린시펄과 일치" if attributed
             else " / ".join(attribution_notes)))
    if not attributed:
        print(f"   {SELFTEST_PATH} 는 **앱마다 공유되는 경로**입니다(STEP4B §8).")

# ④ 판정 — 문서 §5.1 기준 + 신선도 + 귀속
t1_ok = http_of("T1_health") == 200
t2_ok = http_of("T2_models") == 200
t3    = tests.get("T3_plain_chat") or {}
t3_ok = t3.get("http") == 200 and bool((t3.get("content") or "").strip())
t4    = tests.get("T4_agent_tool_loop") or {}
t4_ok = t4.get("ok") is True and (t4.get("steps") or 0) >= 2
t5    = tests.get("T5_negative_404") or {}
t5_ok = t5.get("http") == 404 and _is_vllm_404(t5.get("body"))
t0_blocked = (tests.get("T0_egress_controls") or {}).get("denied_blocked")

print(f"\n앱 자기시험 (driver-proxy 신원: {selftest_data.get('driver_proxy_identity','unknown')})")
print(f"  판정            : {verdict or '(없음)'}")
print(f"  T0 egress 강제  : denied_blocked={t0_blocked}  (true/false 모두 정상 — §5.1)")
print(f"  T1 /health      : {'✓' if t1_ok else '❌'} HTTP {http_of('T1_health')}")
print(f"  T2 /v1/models   : {'✓' if t2_ok else '❌'} HTTP {http_of('T2_models')}")
print(f"  T3 단발 대화    : {'✓' if t3_ok else '❌'} HTTP {t3.get('http')} · {len((t3.get('content') or ''))}자")
print(f"  T4 도구 루프    : {'✓' if t4_ok else '❌'} ok={t4.get('ok')} steps={t4.get('steps')}")
print(f"  T5 음성 404     : {'✓' if t5_ok else '❌'} HTTP {t5.get('http')} · {(t5.get('body') or '')[:40]!r}")

app_ok = (app_err is None) and (app_state == "RUNNING")
required_pass = (bool(verdict.startswith("PASS_")) and t4_ok and t5_ok
                 and fresh_ok and attributed and app_ok)

if not required_pass:
    print("\n앱 검증 실패 사유:")
    if read_err:                          print(f"  - 자기시험 파일을 읽지 못함: {read_err}")
    if not verdict.startswith("PASS_"):   print(f"  - verdict 가 PASS_ 로 시작하지 않음: {verdict!r}")
    if not t4_ok:                         print(f"  - T4 미통과 (ok={t4.get('ok')}, steps={t4.get('steps')} · 2 이상 필요)")
    if not t5_ok:                         print(f"  - T5 가 404 + vLLM 본문이 아님 (HTTP {t5.get('http')})")
    if not fresh_ok:                      print(f"  - 신선도 밖 (age={None if age_s is None else round(age_s)}초)")
    if not attributed:                    print(f"  - 이 앱의 결과라고 확정할 수 없음 ({'; '.join(attribution_notes) or '파일 없음'})")
    if app_err:                   print(f"  - 앱 조회 실패: {app_err}")
    elif app_state != "RUNNING":  print(f"  - 앱 상태가 RUNNING 이 아님: {app_state} (compute={app_compute})")

print(f"\n앱 검증 결과: {'✓ 통과' if required_pass else '❌ 실패'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 6 · 최종 판정
# MAGIC
# MAGIC 노트북 실행 결과와 앱 결과를 종합해 최종 판정을 합니다.

# COMMAND ----------
import json, time
from datetime import datetime, timezone

# ── 노트북 쪽 통과 조건: T1~T5 **전부** + 2스텝 이상 ────────────────────────
# T5 를 빼면 "앞단이 무조건 200 을 주는" 구성에서도 PASS 가 납니다 — §5.1 의 핵심 경고.
notebook_pass = (preflight_ok
                 and all(test_results[k]["ok"] for k in
                         ("T1_health", "T2_models", "T3_plain_chat", "T4_agent_tool_loop", "T5_negative_404"))
                 and (agent_result.get("steps") or 0) >= 2)

# 신선도는 **판정 시점에** 다시 계산합니다(셀을 나눠 다시 돌려도 낡은 값을 쓰지 않도록).
age_now = None
if selftest_data.get("ts_utc"):
    try:
        _ts = datetime.fromisoformat(selftest_data["ts_utc"].replace("Z", "+00:00"))
        if _ts.tzinfo is None: _ts = _ts.replace(tzinfo=timezone.utc)
        age_now = (datetime.now(timezone.utc) - _ts).total_seconds()
    except Exception:
        age_now = None
fresh_now = age_now is not None and -60 <= age_now <= STALE_LIMIT_S

app_pass     = bool(required_pass) and fresh_now
overall_pass = notebook_pass and app_pass
final_verdict = "PASS_NOTEBOOK_AND_APP" if overall_pass else "FAIL"

print("=" * 66)
print("✓ PASS" if overall_pass else "❌ FAIL")
print("=" * 66)
print(f"\n노트북 검증: {'✓' if notebook_pass else '❌'}")
for k, label in (("T1_health", "T1 /health"), ("T2_models", "T2 /v1/models"),
                 ("T3_plain_chat", "T3 단발 대화"), ("T4_agent_tool_loop", "T4 도구 루프"),
                 ("T5_negative_404", "T5 음성 404")):
    r = test_results[k]
    detail = (f"{agent_result.get('steps')}스텝" if k == "T4_agent_tool_loop"
              else f"HTTP {r.get('http', '-')}")
    print(f"  {label:14} {'✓' if r['ok'] else '❌'} {detail}")
print(f"\n앱 검증:     {'✓' if app_pass else '❌'}  "
      f"(verdict={verdict or '(없음)'} · T4 ok={t4_ok} · T5 ok={t5_ok} · "
      f"age={None if age_now is None else round(age_now)}s · 귀속={attributed})")

# ── 증거 기록 — 워크스페이스 파일로 씁니다(표기와 실제 위치를 일치시킵니다) ──
evidence = {
    "overall_verdict": final_verdict,
    "status": "PASS" if overall_pass else "FAIL",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "workspace_host": HOST,
    "cluster_id": CLUSTER_ID,
    "port": PORT,
    "app_name": APP_NAME,
    "app_status": app_state,
    "app_compute_status": app_compute,
    "app_sp_client_id": app_sp_client_id,
    "app_deploy_time": str(app_deploy_time) if app_deploy_time else None,
    "secret": f"{SCOPE}/{KEY}",
    "model": model_name,
    "max_model_len": max_model_len,
    "expected": {"model": EXPECT_MODEL, "max_model_len": EXPECT_CTX or None},
    "stale_limit_s": STALE_LIMIT_S,
    "preflight_ok": preflight_ok,
    "preflight_error": preflight_err,
    "notebook_tests": test_results,
    # 앱 결과에는 상류 응답 본문이 그대로 들어 있습니다. PAT·Bearer 흔적을 지워서 넣습니다.
    "app_selftest": json.loads(_scrub(json.dumps(selftest_data, ensure_ascii=False))),
    "summary": {
        "notebook_pass": notebook_pass,
        "app_pass": app_pass,
        "overall_pass": overall_pass,
        "t4_steps": agent_result.get("steps"),
        "t4_tool_args": agent_result.get("tool_args"),
        "t5_negative_404_http": test_results["T5_negative_404"]["http"],
        "app_selftest_age_s": None if age_now is None else round(age_now),
        "app_selftest_attributed": attributed,
        "app_selftest_verdict": verdict or None,
        "t0_denied_blocked": t0_blocked,
        "context_token_check": ctx_check,
        "app_lookup_error": app_err,
        "selftest_read_error": read_err,
        "attribution_notes": attribution_notes or None,
        "t4_notes": agent_result.get("notes"),
    },
}

evidence_path = f"/Shared/step4b_evidence_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
try:
    from databricks.sdk.service.workspace import ImportFormat
    w.workspace.upload(evidence_path,
                       json.dumps(evidence, indent=2, ensure_ascii=False).encode("utf-8"),
                       format=ImportFormat.AUTO, overwrite=True)
    print(f"\n증거 파일: {evidence_path}")
    print("  회수: databricks workspace export <위 경로>")
    print("  ⚠️  이 파일에는 워크스페이스 FQDN·클러스터 ID 가 그대로 들어 있습니다 — 외부로 보낼 때 가리십시오.")
except Exception as e:
    evidence_path = None
    print(f"\n⚠️  증거 파일 기록 실패 — {type(e).__name__}: {e} (판정에는 영향 없음)")

print("\n" + "=" * 66)
print(("✓ PASS — STEP4B 검증 완료: 앱 → driver-proxy → vLLM 이 도구 호출까지 동작합니다."
       if overall_pass else "❌ FAIL — 위 실패 사유를 해결한 뒤 다시 실행하십시오."))
print(f"앱 {APP_NAME} · 모델 {model_name} · 컨텍스트 {ctx_str} · 클러스터 {CLUSTER_ID}")
print("=" * 66)

final_result = {"verdict": final_verdict, "overall_pass": overall_pass,
                "notebook_pass": notebook_pass, "app_pass": app_pass,
                "evidence_file": evidence_path}
print(f"\n반환값: {json.dumps(final_result, indent=2, ensure_ascii=False)}")

