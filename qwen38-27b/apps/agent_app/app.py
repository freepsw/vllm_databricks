#!/usr/bin/env python3
"""Databricks Apps 에서 도는 agent 앱 — driver-proxy 를 통해 드라이버의 vLLM 을 호출한다.

STEP4B 문서와 함께 씁니다. AI Gateway(STEP4)를 만들지 않고 vLLM 을 쓰는 경로입니다.

설계 결정 세 가지 (바꾸기 전에 STEP4B §7 을 읽으십시오)
  1. **표준 라이브러리만 씁니다.** requirements.txt 가 없으므로 앱 기동 시 pypi 로 나가지
     않습니다 — serverless egress 가 제한된 환경에서 실패 요인 하나를 없앱니다.
  2. **driver-proxy 인증은 PAT** 로 합니다(secret 으로 주입). 앱 서비스 프린시펄 토큰과
     사용자 OBO 토큰은 전용(single-user) 클러스터의 driver-proxy 에서 거부됩니다 — STEP4B §2.
  3. **워크스페이스 API 도구 호출은 앱 서비스 프린시펄** 신원으로 합니다. PAT 는 driver-proxy
     한 곳에만 씁니다.

토큰 값은 로그·응답·화면에 절대 넣지 않습니다.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 설정 (모두 app.yaml 에서 주입) ──────────────────────────────────────────
HOST = (os.environ.get("DATABRICKS_HOST") or "").rstrip("/")
if HOST and not HOST.startswith("http"):
    HOST = "https://" + HOST
CLIENT_ID = os.environ.get("DATABRICKS_CLIENT_ID", "")        # 앱 SP (런타임이 주입)
CLIENT_SECRET = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
PAT = os.environ.get("DRIVER_PAT", "")                        # secret 리소스로 주입
CLUSTER_ID = os.environ.get("VLLM_CLUSTER_ID", "")            # 필수
PORT_VLLM = os.environ.get("VLLM_PORT", "8005")
MODEL = os.environ.get("VLLM_MODEL", "qwen38-27b")
RESULT_PATH = os.environ.get("RESULT_PATH", "/Shared/qwen_agent_selftest.json")

# egress 정책이 실제로 강제되고 있는지 판정할 대조군 두 개.
#   allowed = 정책의 allowed_internet_destinations 에 넣은 도메인 → 200 이어야 한다
#   denied  = 허용 목록에 없는 도메인 → 강제되면 DNS 해석부터 실패한다
PROBE_ALLOWED_URL = os.environ.get("PROBE_ALLOWED_URL", "https://pypi.org/simple/")
PROBE_DENIED_URL = os.environ.get("PROBE_DENIED_URL", "https://example.com/")

# 조직ID(orgId)는 워크스페이스 호스트명에서 뽑는다. Azure 가 아니거나 호스트 형태가
# 다르면 app.yaml 에 VLLM_ORG_ID 를 직접 주십시오.
ORG_ID = os.environ.get("VLLM_ORG_ID") or ""
if not ORG_ID:
    m = re.search(r"adb-(\d+)\.", HOST)
    ORG_ID = m.group(1) if m else ""

BASE = f"{HOST}/driver-proxy-api/o/{ORG_ID}/{CLUSTER_ID}/{PORT_VLLM}"

_tok = {"v": None, "exp": 0.0}
_lock = threading.Lock()


def sp_token() -> str:
    """앱 서비스 프린시펄 OAuth 토큰(client_credentials). 만료 60초 전에 갱신한다."""
    with _lock:
        if _tok["v"] and time.time() < _tok["exp"] - 60:
            return _tok["v"]
        body = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "scope": "all-apis"}
        ).encode()
        basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        req = urllib.request.Request(
            f"{HOST}/oidc/v1/token", data=body,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        _tok["v"] = d["access_token"]
        _tok["exp"] = time.time() + float(d.get("expires_in", 3600))
        return _tok["v"]


def http(method, url, payload=None, timeout=300, auth=True, bearer=None):
    """(status, body_text, elapsed_ms). 예외는 status 0 으로 환원한다.

    bearer 를 주면 그 토큰을 쓰고, 없으면 앱 SP 토큰을 쓴다.
    status 0 = 연결·DNS·타임아웃 실패이며 body_text 에 사유가 들어온다.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    hdr = {"Content-Type": "application/json"}
    if auth:
        hdr["Authorization"] = f"Bearer {bearer or sp_token()}"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), int((time.time() - t0) * 1000)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000)


# ── agent 가 쓸 도구 2개 (예시 — 여기에 업무 도구를 추가하십시오) ──────────
TOOLS = [
    {"type": "function", "function": {
        "name": "get_cluster_state",
        "description": "Databricks 클러스터의 현재 상태와 드라이버 노드 타입을 조회한다.",
        "parameters": {"type": "object",
                       "properties": {"cluster_id": {"type": "string", "description": "클러스터 ID"}},
                       "required": ["cluster_id"]}}},
    {"type": "function", "function": {
        "name": "get_model_info",
        "description": "vLLM 서버가 보고하는 모델 이름과 최대 컨텍스트 길이를 조회한다.",
        "parameters": {"type": "object", "properties": {}}}},
]


def tool_get_cluster_state(cluster_id: str = "") -> dict:
    """워크스페이스 API — 앱 서비스 프린시펄 신원으로 호출한다(PAT 를 쓰지 않는다)."""
    cid = cluster_id or CLUSTER_ID
    st, body, _ = http("GET", f"{HOST}/api/2.0/clusters/get?cluster_id={cid}")
    if st != 200:
        return {"error": f"HTTP {st}", "detail": body[:300]}
    d = json.loads(body)
    return {"cluster_id": d.get("cluster_id"), "state": d.get("state"),
            "driver_node_type_id": d.get("driver_node_type_id"),
            "spark_version": d.get("spark_version")}


def tool_get_model_info() -> dict:
    """vLLM 의 /v1/models — driver-proxy 를 지나므로 PAT 신원으로 호출한다."""
    st, body, _ = http("GET", f"{BASE}/v1/models", timeout=60, bearer=PAT or None)
    if st != 200:
        return {"error": f"HTTP {st}", "detail": body[:300]}
    m = (json.loads(body).get("data") or [{}])[0]
    return {"model": m.get("id"), "max_model_len": m.get("max_model_len")}


DISPATCH = {"get_cluster_state": tool_get_cluster_state, "get_model_info": tool_get_model_info}

SYSTEM = ("너는 Databricks 운영 보조 agent 다. 도구를 쓸 수 있으면 반드시 도구로 사실을 확인한 뒤 "
          "한국어로 간결하게 답한다. 도구 결과에 없는 값을 추측하지 않는다.")


def chat(messages, tools=None, max_tokens=1500, temperature=0.0, timeout=300, bearer=None):
    """vLLM 의 OpenAI 호환 chat completions 호출.

    max_tokens 를 넉넉히 주십시오. 이 모델은 추론 토큰을 먼저 소비하므로 값이 작으면
    `finish_reason: length` 로 끊기고 `content` 가 null 로 옵니다(STEP4B §8).
    """
    payload = {"model": MODEL, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return http("POST", f"{BASE}/v1/chat/completions", payload, timeout=timeout,
                bearer=bearer or PAT or None)


def agent(question: str, max_steps: int = 4, bearer=None) -> dict:
    """도구 호출 루프. 각 스텝의 원시 응답 요약을 trace 로 남긴다."""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    trace, t0 = [], time.time()
    identity = "obo" if bearer else ("pat" if PAT else "app_sp")
    for step in range(1, max_steps + 1):
        st, body, ms = chat(msgs, tools=TOOLS, bearer=bearer)
        if st != 200:
            trace.append({"step": step, "http": st, "ms": ms, "body": body[:500]})
            return {"ok": False, "identity": identity, "trace": trace,
                    "total_ms": int((time.time() - t0) * 1000)}
        try:
            _j = json.loads(body)
            ch = (_j.get("choices") or [None])[0]
            if ch is None:
                raise ValueError("choices 가 비었습니다")
            msg = ch.get("message") or {}
        except Exception as e:
            trace.append({"step": step, "http": st, "ms": ms,
                          "parse_error": f"{type(e).__name__}: {e}", "body": body[:300]})
            return {"ok": False, "identity": identity, "steps": step, "trace": trace,
                    "reason": f"응답 파싱 실패: {type(e).__name__}",
                    "total_ms": int((time.time() - t0) * 1000)}
        calls = msg.get("tool_calls") or []
        trace.append({"step": step, "http": st, "ms": ms,
                      "finish_reason": ch.get("finish_reason"),
                      "tool_calls": [c["function"]["name"] for c in calls],
                      "content_head": (msg.get("content") or "")[:200],
                      "reasoning_head": (msg.get("reasoning") or "")[:200],
                      "usage": json.loads(body).get("usage")})
        if not calls:
            # 종료 스텝의 품질까지 본다. 도구를 한 번도 부르지 않았거나(1스텝),
            # finish_reason 이 stop 이 아니거나(추론 토큰 소진 등), 답이 비면 실패다.
            answer = msg.get("content") or ""
            finish = ch.get("finish_reason")
            reasons = []
            if step < 2:
                reasons.append("도구를 호출하지 않고 1스텝에서 종료")
            if finish != "stop":
                reasons.append(f"finish_reason={finish!r} (stop 이어야 함)")
            if not answer.strip():
                reasons.append("최종 답이 비어 있음 (max_tokens 를 늘리십시오)")
            return {"ok": not reasons, "identity": identity, "answer": answer,
                    "steps": step, "trace": trace,
                    **({"reason": "; ".join(reasons)} if reasons else {}),
                    "total_ms": int((time.time() - t0) * 1000)}
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": calls})
        for c in calls:
            fn = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = DISPATCH[fn](**args) if fn in DISPATCH else {"error": f"unknown tool {fn}"}
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            trace.append({"step": step, "tool": fn, "args": args, "result": result})
            msgs.append({"role": "tool", "tool_call_id": c.get("id", fn),
                         "content": json.dumps(result, ensure_ascii=False)})
    return {"ok": False, "identity": identity, "reason": "max_steps", "trace": trace,
            "total_ms": int((time.time() - t0) * 1000)}


# ── 진단 ────────────────────────────────────────────────────────────────────
def probe_egress() -> dict:
    """정책 강제 여부 판정. denied 가 200 이면 아직 강제 전이므로 판정을 보류해야 한다."""
    out = {}
    for name, url in (("external_allowed", PROBE_ALLOWED_URL),
                      ("external_denied", PROBE_DENIED_URL)):
        st, body, ms = http("GET", url, timeout=20, auth=False)
        out[name] = {"url": url, "http": st, "ms": ms, "err": body[:160] if st == 0 else ""}
    e = out["external_denied"]["err"]
    out["denied_blocked"] = (out["external_denied"]["http"] == 0
                             and ("name resolution" in e or "Name or service" in e
                                  or "Temporary failure" in e or "timed out" in e))
    return out


def probe_identity(obo=None) -> dict:
    """같은 driver-proxy URL 을 세 신원으로 호출해 무엇이 통과하는지 보여준다."""
    out = {"obo_header_present": bool(obo), "results": {}}
    for name, tok in (("app_sp", None), ("pat", PAT or None), ("obo", obo)):
        if name == "pat" and not PAT:
            out["results"][name] = {"skipped": "DRIVER_PAT 미주입 — app.yaml 의 secret 연결 확인"}
            continue
        if name == "obo" and not obo:
            out["results"][name] = {"skipped": "x-forwarded-access-token 헤더 없음(사용자 인증 미설정)"}
            continue
        st, body, ms = http("GET", f"{BASE}/health", timeout=60,
                            bearer=(None if name == "app_sp" else tok))
        out["results"][name] = {"http": st, "ms": ms, "body": body[:220]}
    return out


_last = {"state": "pending"}


def publish(obj) -> str:
    """결과를 워크스페이스 파일로 자가보고한다 — 앱 URL 에 접속하지 않고도 회수할 수 있다."""
    content = base64.b64encode(json.dumps(obj, ensure_ascii=False, indent=1).encode()).decode()
    st, body, _ = http("POST", f"{HOST}/api/2.0/workspace/import",
                       {"path": RESULT_PATH, "format": "RAW", "overwrite": True,
                        "content": content}, timeout=60)
    return f"HTTP {st} {body[:200]}"


def selftest() -> dict:
    """STEP4B §5 의 판정 기준을 그대로 자동화한 자기시험."""
    out = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "driver_proxy_identity": "pat(secret)" if PAT else "app_sp",
           "model": MODEL, "tests": {}}

    out["tests"]["T0_egress_controls"] = probe_egress()

    st, body, ms = http("GET", f"{BASE}/health", timeout=60, bearer=PAT or None)
    out["tests"]["T1_health"] = {"http": st, "ms": ms, "body": body[:200]}

    st, body, ms = http("GET", f"{BASE}/v1/models", timeout=60, bearer=PAT or None)
    out["tests"]["T2_models"] = {"http": st, "ms": ms, "body": body[:400]}

    st, body, ms = chat([{"role": "user", "content": "한 문장으로 자기소개를 하라."}],
                        max_tokens=1500)
    t3 = {"http": st, "ms": ms}
    if st == 200:
        try:
            d = json.loads(body)
            _c = (d.get("choices") or [{}])[0]
            t3["content"] = (_c.get("message") or {}).get("content")
            t3["finish_reason"] = _c.get("finish_reason")
            t3["usage"] = d.get("usage")
        except Exception as e:
            t3["parse_error"] = f"{type(e).__name__}: {e}"
            t3["body"] = body[:300]
    else:
        t3["body"] = body[:500]
    out["tests"]["T3_plain_chat"] = t3

    out["tests"]["T4_agent_tool_loop"] = agent(
        f"클러스터 {CLUSTER_ID} 의 상태와 지금 서비스 중인 모델의 최대 컨텍스트 길이를 "
        "도구로 확인해서 알려줘.")

    st, body, ms = http("GET", f"{BASE}/nonexistent-xyz", timeout=60, bearer=PAT or None)
    out["tests"]["T5_negative_404"] = {"http": st, "ms": ms, "body": body[:200]}

    def _t5_is_vllm_404(body):
        """정확히 vLLM(FastAPI) 의 `{"detail":"Not Found"}` 인지 본다.
        중간 계층이 만든 404 페이지도 "Not Found" 라는 문자열을 포함하므로,
        부분문자열 검사는 '상류까지 도달했다'는 증명이 되지 못한다."""
        try:
            return json.loads(body or "").get("detail") == "Not Found"
        except Exception:
            return False

    out["app_client_id"] = CLIENT_ID   # 어느 앱이 쓴 파일인지 식별용(비밀 아님)
    out["app_name_env"] = os.environ.get("DATABRICKS_APP_NAME") or None

    _t4 = out["tests"]["T4_agent_tool_loop"]
    _t5 = out["tests"]["T5_negative_404"]
    ok = (out["tests"]["T1_health"]["http"] == 200
          and out["tests"]["T2_models"]["http"] == 200
          and out["tests"]["T3_plain_chat"]["http"] == 200
          and bool((out["tests"]["T3_plain_chat"].get("content") or "").strip())
          # 도구 루프는 2스텝 이상이어야 한다 — 1스텝 종료는 도구를 안 쓴 것이다
          and _t4.get("ok") is True and (_t4.get("steps") or 0) >= 2
          # 음성 시험은 404 이면서 **vLLM(FastAPI) 의 본문**이어야 한다.
          # 중간 계층이 만든 404 는 상류 도달의 증거가 못 된다.
          and _t5["http"] == 404 and _t5_is_vllm_404(_t5.get("body")))
    enforced = out["tests"]["T0_egress_controls"]["denied_blocked"] is True
    if ok and enforced:
        out["verdict"] = "PASS_UNDER_ENFORCED_POLICY"
    elif ok:
        out["verdict"] = "PASS_POLICY_NOT_ENFORCED_OR_FULL_ACCESS"
    else:
        out["verdict"] = "FAIL"
    # 자기보고. 결과 JSON 안에 자기 자신의 성공 여부를 넣을 수는 없으므로 로그로만 남깁니다.
    print("[selftest]", out["verdict"], "| publish:", publish(out), flush=True)
    return out


def run_selftest_bg(attempts: int = 20, gap: int = 60):
    """상류 vLLM 이 기동 중일 수 있으므로 통과할 때까지 재시도한다."""
    time.sleep(3)
    for i in range(1, attempts + 1):
        try:
            _last.update({"state": f"running attempt {i}"})
            try:
                r = selftest()
            except Exception as e:
                # 여기서 조용히 죽으면 /Shared 의 **지난 PASS 파일이 그대로 남아**
                # 다음 검증에서 "신선한 PASS" 로 읽힌다. 실패를 반드시 덮어쓴다.
                fail = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "app_client_id": CLIENT_ID,
                        "driver_proxy_identity": "pat(secret)" if PAT else "app_sp",
                        "verdict": "FAIL",
                        "error": f"{type(e).__name__}: {e}", "attempt": i}
                print("[selftest] CRASH", fail["error"], "| publish:", publish(fail), flush=True)
                raise
            r["attempt"] = i
            _last.update({"state": "done", "result": r})
            if r["verdict"].startswith("PASS"):
                return
        except Exception as e:
            _last.update({"state": "error", "attempt": i, "error": f"{type(e).__name__}: {e}"})
        time.sleep(gap)


PAGE = """<!doctype html><meta charset="utf-8"><title>Qwen agent (driver-proxy)</title>
<style>body{font:14px system-ui;max-width:860px;margin:24px auto;padding:0 16px}
textarea{width:100%;height:74px}pre{background:#f5f5f5;padding:10px;overflow:auto;white-space:pre-wrap}
button{padding:8px 14px;margin:0 8px 8px 0}.m{color:#666;font-size:12px}</style>
<h2>Databricks Apps agent → driver-proxy → vLLM</h2>
<p class=m>모델 __MODEL__ · 도구 2개(get_cluster_state · get_model_info) ·
driver-proxy 인증 = secret PAT · 워크스페이스 API = 앱 서비스 프린시펄</p>
<textarea id=q>클러스터 __CLUSTER__ 의 상태와 지금 서비스 중인 모델의 최대 컨텍스트 길이를 도구로 확인해서 알려줘.</textarea>
<p><button onclick=ask()>agent 실행</button><button onclick=ident()>신원 진단</button>
<button onclick=egress()>egress 대조군</button><button onclick=self_()>자기시험 결과</button></p>
<pre id=o>준비됨</pre>
<script>
async function j(u,o){const r=await fetch(u,o);document.getElementById('o').textContent=
 JSON.stringify(await r.json(),null,1);}
function ask(){document.getElementById('o').textContent='호출 중…';
 return j('/api/agent',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({q:document.getElementById('q').value})});}
function ident(){return j('/api/identity');}
function egress(){return j('/api/egress');}
function self_(){return j('/api/selftest');}
</script>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._send(200,
                              PAGE.replace("__MODEL__", MODEL).replace("__CLUSTER__", CLUSTER_ID),
                              "text/html; charset=utf-8")
        if p == "/api/identity":
            return self._send(200, json.dumps(
                probe_identity(self.headers.get("x-forwarded-access-token")), ensure_ascii=False))
        if p == "/api/egress":
            return self._send(200, json.dumps(probe_egress(), ensure_ascii=False))
        if p == "/api/selftest":
            return self._send(200, json.dumps(_last, ensure_ascii=False))
        if p == "/api/rerun":
            threading.Thread(target=run_selftest_bg, daemon=True).start()
            return self._send(200, json.dumps({"started": True}))
        if p == "/healthz":
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/agent":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            q = json.loads(self.rfile.read(n) or b"{}").get("q") or ""
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "bad json"}))
        # 기본 신원은 PAT. identity=obo 로 강제하면 사용자 OBO 토큰을 쓰지만,
        # driver-proxy 는 clusters 스코프를 요구하므로 403 이 됩니다(STEP4B §2).
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        want = (qs.get("identity") or ["pat"])[0]
        obo = self.headers.get("x-forwarded-access-token") if want == "obo" else None
        return self._send(200, json.dumps(agent(q, bearer=obo), ensure_ascii=False))

    def log_message(self, fmt, *a):
        print("[http]", fmt % a, flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    print(f"[boot] host={HOST} org={ORG_ID} cluster={CLUSTER_ID} port={port} "
          f"model={MODEL} pat_injected={bool(PAT)}", flush=True)
    if not CLUSTER_ID:
        print("[boot] ⚠️ VLLM_CLUSTER_ID 가 비어 있습니다 — app.yaml 을 확인하십시오", flush=True)
    threading.Thread(target=run_selftest_bg, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
