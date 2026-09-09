# Databricks notebook source
# MAGIC %md
# MAGIC # Qwen3.8-27B 을 AI Gateway 엔드포인트로 등록
# MAGIC
# MAGIC 드라이버에서 동작 중인 vLLM 을 **Databricks 서빙 엔드포인트**로 감싸서, 외부 agent 가
# MAGIC 표준 OpenAI 호환 API 로 호출하고 AI Playground 에서 바로 대화할 수 있게 만듭니다.
# MAGIC
# MAGIC | 셀 | 하는 일 | 소요 |
# MAGIC |---|---|---|
# MAGIC | 1 | 설정 (**이 셀만 고칩니다**) | — |
# MAGIC | 2 | vLLM 도달성 점검 + 모델 정보 자동 인식 | 5초 |
# MAGIC | 3 | PAT 를 secret scope 에 저장 | 10초 |
# MAGIC | 4 | 엔드포인트 생성/갱신 | 30초 |
# MAGIC | 5 | 스모크 테스트 + 응답 형식 확인 | 10초 |
# MAGIC | 6 | 외부 agent 연결 예시 (SDK·스트리밍·tool calling) | 1분 |
# MAGIC
# MAGIC ### 실행 전 반드시 확인할 2가지
# MAGIC
# MAGIC | # | 확인할 것 | 지키지 않으면 |
# MAGIC |---|---|---|
# MAGIC | 1 | vLLM 이 **`--host 0.0.0.0`** 으로 떠 있어야 합니다 | 엔드포인트 호출이 **502**. 가이드 §2 참조 |
# MAGIC | 2 | PAT 를 **노트북 밖에서** 발급해야 합니다 | 엔드포인트 호출이 **403**. 가이드 §3 참조 |
# MAGIC
# MAGIC > 이 노트북은 **외부 파일에 의존하지 않습니다.** 노트북 하나만 워크스페이스에 임포트해
# MAGIC > GPU 클러스터에 연결하고 위에서부터 실행하십시오.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 1 · 설정 — 이 셀만 환경에 맞게 고치십시오

# COMMAND ----------

ENDPOINT   = "qwen38-27b-vllm"   # 만들 엔드포인트 이름 (외부 agent 가 model 로 지정하는 값)
PORT       = 8005                # vLLM 이 듣고 있는 포트
SCOPE, KEY = "vllm-gateway", "driver_proxy_pat"   # PAT 를 담아둘 secret 위치
RATE_LIMIT = 120                 # 분당 호출 한도 (0 이면 설정하지 않음)

# ── 아래는 고치지 않습니다 (환경에서 자동으로 읽습니다) ──────────────────────
from databricks.sdk import WorkspaceClient

w       = WorkspaceClient()
HOST    = "https://" + spark.conf.get("spark.databricks.workspaceUrl")
ORG     = w.get_workspace_id()   # 모든 클라우드에서 동작 (호스트명 파싱보다 안전)
CLUSTER = spark.conf.get("spark.databricks.clusterUsageTags.clusterId")
PROXY   = f"{HOST}/driver-proxy-api/o/{ORG}/{CLUSTER}/{PORT}/v1/chat/completions"

print("워크스페이스 :", HOST)
print("상류(vLLM) URL:", PROXY)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 2 · vLLM 도달성 점검 + 모델 정보 자동 인식
# MAGIC
# MAGIC driver-proxy 는 드라이버의 **사설 IP** 로 접속합니다. `127.0.0.1` 로 바인드된 vLLM 은
# MAGIC `127.0.0.1/health` 에는 정상 응답하면서 엔드포인트 호출만 502 가 되므로,
# MAGIC **사설 IP 로 직접 확인**합니다.
# MAGIC
# MAGIC 모델 이름(`--served-model-name`)은 vLLM 에서 직접 읽으므로 손으로 옮겨 적지 않습니다.

# COMMAND ----------

import requests

# ① vLLM 이 살아 있는지
assert requests.get(f"http://127.0.0.1:{PORT}/health", timeout=10).ok, \
    f"vLLM 이 응답하지 않습니다 (포트 {PORT}). 가이드 §2 로 serve 를 먼저 기동하십시오."

# ② driver-proxy 가 실제로 접속하는 경로(드라이버 사설 IP)로 도달되는지
#    주의: socket.gethostbyname(socket.gethostname()) 은 이 환경에서 127.0.1.1 을 돌려주므로
#    점검이 무의미해집니다. spark.driver.host 를 쓰십시오 (예: 10.139.64.4).
DRIVER_IP = spark.conf.get("spark.driver.host")
assert not DRIVER_IP.startswith("127."), \
    f"드라이버 사설 IP 를 찾지 못했습니다 ({DRIVER_IP}). 점검을 건너뛰지 말고 가이드 §4 를 보십시오."
try:
    requests.get(f"http://{DRIVER_IP}:{PORT}/health", timeout=10).raise_for_status()
except Exception as e:
    raise RuntimeError(
        f"사설 IP({DRIVER_IP}:{PORT}) 로 접속되지 않습니다 → 엔드포인트 호출이 전부 502 가 됩니다.\n"
        f"BIND_HOST=0.0.0.0 으로 vLLM 을 다시 기동하십시오 (가이드 §2). 원인: {e}")

# ③ 모델 이름과 컨텍스트 상한을 vLLM 에서 직접 읽는다 (이름 불일치로 인한 404 를 원천 차단)
info          = requests.get(f"http://127.0.0.1:{PORT}/v1/models", timeout=10).json()["data"][0]
SERVED        = info["id"]
MAX_MODEL_LEN = info["max_model_len"]

print(f"OK  /health 200 · 사설 IP {DRIVER_IP} 도달 → driver-proxy 경로 확보")
print(f"    모델 이름     : {SERVED}")
print(f"    컨텍스트 상한 : {MAX_MODEL_LEN:,} 토큰")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 3 · PAT 를 secret scope 에 저장
# MAGIC
# MAGIC AI Gateway 가 driver-proxy 에 인증할 때 쓸 토큰입니다. 엔드포인트 설정에는 평문이 아니라
# MAGIC **secret 참조**로 들어갑니다.
# MAGIC
# MAGIC > ⚠️ **토큰은 반드시 노트북 밖에서 발급하십시오.**
# MAGIC > 노트북 안에서 토큰 생성 API 를 부르면 새 토큰이 생기지 않고 **노트북 컨텍스트 토큰**이
# MAGIC > 반환됩니다. 그 토큰은 워크스페이스 **안에서는 200, 밖에서는 403** 입니다.
# MAGIC > AI Gateway 는 밖에서 호출하므로 엔드포인트가 반드시 403 이 됩니다.
# MAGIC >
# MAGIC > 발급: 우상단 사용자 메뉴 → **설정 → 개발자 → 액세스 토큰 → 새 토큰 생성**
# MAGIC
# MAGIC 아래 셀을 실행하면 `pat` 입력칸이 나타납니다. 값을 붙여넣고 셀을 **다시 실행**하십시오.
# MAGIC 저장이 끝나면 입력칸을 **비우십시오**.

# COMMAND ----------

dbutils.widgets.text("pat", "", "PAT (노트북 밖에서 발급한 값)")
PAT = dbutils.widgets.get("pat").strip()

from databricks.sdk.errors import ResourceAlreadyExists

try:
    w.secrets.create_scope(scope=SCOPE)
    print(f"secret scope 생성: {SCOPE}")
except ResourceAlreadyExists:
    print(f"secret scope 기존 사용: {SCOPE}")   # 이미 있으면 기존 권한을 물려받습니다

if PAT:
    w.secrets.put_secret(scope=SCOPE, key=KEY, string_value=PAT)
    print(f"저장 완료: {SCOPE}/{KEY}  → 위젯의 pat 칸을 이제 비우십시오")
elif KEY not in [x.key for x in w.secrets.list_secrets(scope=SCOPE)]:
    raise RuntimeError(f"{SCOPE}/{KEY} 에 저장된 값이 없습니다. 위에 나타난 'pat' 입력칸에 "
                       "노트북 밖에서 발급한 PAT 를 붙여넣고 이 셀을 다시 실행하십시오.")

# 이 노트북 세션에 주어진 토큰(= 노트북 컨텍스트 토큰). secret 에 이 값이 들어가 있으면
# 워크스페이스 밖에서 호출하는 AI Gateway 가 403 을 받으므로 여기서 막는다.
NOTEBOOK_TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
assert dbutils.secrets.get(SCOPE, KEY) != NOTEBOOK_TOKEN, \
    "secret 에 담긴 값이 노트북 컨텍스트 토큰입니다 → 엔드포인트가 전부 403 이 됩니다. " \
    "설정 → 개발자 → 액세스 토큰에서 새로 발급한 값을 넣으십시오."
print("OK  저장된 PAT 는 노트북 컨텍스트 토큰이 아님")

# 이 scope 를 읽을 수 있는 주체 = 게이트웨이를 우회할 수 있는 주체.
# 아래 출력에 users 가 보이면 워크스페이스 전원이 PAT 를 읽을 수 있다는 뜻입니다.
#   databricks secrets delete-acl <scope> users --profile <P>      (가이드 §3)
print("scope 접근 권한 :", [(a.principal, str(a.permission).split(".")[-1])
                            for a in w.secrets.list_acls(scope=SCOPE)])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 4 · 엔드포인트 생성/갱신
# MAGIC
# MAGIC `provider: custom` 인 external model 로 등록합니다. 이미 같은 이름이 있으면 갱신하므로
# MAGIC 여러 번 실행해도 안전합니다. 클러스터를 재생성해 `cluster_id` 가 바뀐 경우에는
# MAGIC **셀 1 → 2 → 4** 를 다시 실행하십시오 (셀 4 는 셀 1·2 의 값에 의존합니다).

# COMMAND ----------

import time
from datetime import timedelta

EXTERNAL = {
    "name": SERVED,                 # 셀 2 에서 vLLM 이 알려준 이름
    "provider": "custom",           # 요청 본문을 변형하지 않는 provider
    "task": "llm/v1/chat",          # AI Playground 에 노출되는 형식
    "custom_provider_config": {
        "custom_provider_url": PROXY,                       # /v1/chat/completions 까지 전체 경로
        # f-string 으로 바꾸지 마십시오. 중괄호를 4개로 겹쳐야 하므로 깨지기 쉽습니다.
        "bearer_token_auth": {"token": "{{secrets/%s/%s}}" % (SCOPE, KEY)},
    },
}
GATEWAY = {"usage_tracking_config": {"enabled": True}}
if RATE_LIMIT:
    GATEWAY["rate_limits"] = [{"calls": RATE_LIMIT, "renewal_period": "minute", "key": "endpoint"}]

if ENDPOINT in [e.name for e in w.serving_endpoints.list()]:
    w.api_client.do("PUT", f"/api/2.0/serving-endpoints/{ENDPOINT}/config",
                    body={"served_entities": [{"external_model": EXTERNAL}]})
    w.api_client.do("PUT", f"/api/2.0/serving-endpoints/{ENDPOINT}/ai-gateway", body=GATEWAY)
    print(f"기존 엔드포인트 갱신: {ENDPOINT} (호출 URL 은 그대로 유지됩니다)")
else:
    w.api_client.do("POST", "/api/2.0/serving-endpoints",
                    body={"name": ENDPOINT,
                          "config": {"served_entities": [{"external_model": EXTERNAL}]},
                          "ai_gateway": GATEWAY})
    print(f"엔드포인트 생성: {ENDPOINT}")

d = w.serving_endpoints.wait_get_serving_endpoint_not_updating(ENDPOINT, timeout=timedelta(minutes=10))

# 갱신이 실제로 반영됐는지 상류 URL 로 확인합니다.
# (NOT_UPDATING 이 갱신 반영 전에 보일 수 있어, 확인하지 않으면 이전 설정을 시험하게 됩니다.)
for _ in range(30):
    d   = w.serving_endpoints.get(ENDPOINT)
    got = d.config.served_entities[0].external_model.custom_provider_config.custom_provider_url
    if got == PROXY:
        break
    time.sleep(2)
else:
    raise RuntimeError(f"상류 URL 이 아직 반영되지 않았습니다.\n  기대: {PROXY}\n  현재: {got}")

limits = (d.ai_gateway.rate_limits or []) if d.ai_gateway else []
print("상태        :", d.state.ready.value, "/", d.state.config_update.value)
print("상류 URL    : 반영 확인")
print("rate limits :", ", ".join(f"{x.calls}회/{x.renewal_period.value}" for x in limits) or "없음")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 5 · 스모크 테스트 + 응답 형식 확인
# MAGIC
# MAGIC 한 번의 호출로 전체 경로(엔드포인트 → AI Gateway → driver-proxy → vLLM)를 확인하고,
# MAGIC 클라이언트가 파싱해야 할 응답 형식을 함께 확인합니다.

# COMMAND ----------

import requests

TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
URL   = f"{HOST}/serving-endpoints/{ENDPOINT}/invocations"
HEAD  = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.post(URL, headers=HEAD, timeout=300, json={
    "max_tokens": 900,
    "messages": [{"role": "user",
                  "content": "사과가 17개씩 든 상자 23개에서 46개를 먹었다. 남은 수를 구하라."}]})

if not r.ok:
    # 502 → vLLM 바인드(셀 2) · 403 → PAT 출처(셀 3) · 404 → 상류 모델명
    # 실제 원인은 최상위 error_code 가 아니라 external_model_error 안에 들어 있습니다
    print("실패", r.status_code, r.text[:400])
r.raise_for_status()

body    = r.json()
message = body["choices"][0]["message"]
content = message.get("content") or ""

assert body["model"] == SERVED, f"응답 model={body['model']} ≠ {SERVED}"
assert message.get("reasoning"), "reasoning 필드가 비어 있습니다 (--reasoning-parser qwen3 확인)"
assert "<think>" not in content, "content 에 <think> 태그가 유출되었습니다 (--reasoning-parser qwen3 확인)"
assert "345" in content, f"정답 345 가 응답에 없습니다 → 모델 응답 품질 확인 필요: {content[:150]}"
print("호출 URL     :", URL)
print("content      :", content.strip()[:100])
print("reasoning    :", len(message.get("reasoning") or ""), "자 ·",
      (body["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens"), "토큰")
print("<think> 유출 :", "<think>" in content, " ← False 여야 정상")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 셀 6 · 외부 agent 연결 예시
# MAGIC
# MAGIC 고객 시스템에 그대로 옮겨 쓰는 코드입니다. `base_url` 은 **엔드포인트 경로가 아니라
# MAGIC `/serving-endpoints` 까지**만 주고, `model` 에 **엔드포인트 이름**을 넣습니다.
# MAGIC
# MAGIC 워크스페이스 밖에서 호출할 때는 `api_key` 에 **노트북 밖에서 발급한 PAT**(또는 서비스
# MAGIC 프린시펄 토큰)를 사용하십시오.

# COMMAND ----------

# %pip 은 Python REPL 을 재시작해 "Run all" 을 중단시키므로, 재시작 없는 방식으로 설치합니다
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                "--disable-pip-version-check", "openai"], check=True)

from openai import OpenAI

client = OpenAI(base_url=f"{HOST}/serving-endpoints", api_key=TOKEN)

# ① 스트리밍
#    max_tokens 는 넉넉히 주십시오. 이 모델은 추론(reasoning) 토큰을 **먼저** 소비하므로
#    한도가 작으면 추론만 하다 끝나고 본문(content)이 비어 버립니다 (실측: 300 은 대부분 빈 응답).
print("── 스트리밍 ──")
printed = 0
for chunk in client.chat.completions.create(
        model=ENDPOINT, stream=True, max_tokens=1500,
        messages=[{"role": "user", "content": "한국 통신사가 사내 LLM 을 도입할 때 고려할 점 3가지"}]):
    if chunk.choices and chunk.choices[0].delta.content:
        printed += 1
        print(chunk.choices[0].delta.content, end="")
if printed == 0:
    print("(본문이 비었습니다 → max_tokens 를 늘리십시오. 추론 토큰이 먼저 소비됩니다.)")

# ② tool calling (function calling)
TOOLS = [{"type": "function", "function": {
    "name": "get_weather", "description": "도시의 현재 날씨를 조회한다",
    "parameters": {"type": "object",
                   "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]
try:
    t = client.chat.completions.create(model=ENDPOINT, tools=TOOLS, max_tokens=256,
            messages=[{"role": "user", "content": "서울 날씨 알려줘"}])
    print("\n\n── tool calling ──\n", t.choices[0].message.tool_calls)
except Exception as e:
    print(f"\n\ntool calling 실패: {e}")
    print("→ tool 플래그 없이 vLLM 을 띄우면 tools 요청은 무시가 아니라 400 입니다.")
    print("→ TOOL_CALL_PARSER=qwen3_xml 로 vLLM 을 다시 기동하십시오 (가이드 §2).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 완료
# MAGIC
# MAGIC | 항목 | 값 |
# MAGIC |---|---|
# MAGIC | 호출 URL | `https://<워크스페이스>/serving-endpoints/<엔드포인트>/invocations` |
# MAGIC | OpenAI 호환 | `base_url=https://<워크스페이스>/serving-endpoints`, `model=<엔드포인트 이름>` |
# MAGIC | AI Playground | 좌측 **AI/ML → Playground** 에서 엔드포인트 선택 |
# MAGIC
# MAGIC ### 운영 전에 꼭 읽으십시오
# MAGIC
# MAGIC - **클러스터 자동 종료가 엔드포인트를 죽입니다.** 자동 종료는 *마지막 명령 실행* 기준이며
# MAGIC   추론 트래픽은 명령이 아닙니다. 상시 운영이면 `autotermination_minutes` 를 `0` 으로 두고,
# MAGIC   그만큼 GPU 과금이 계속됨을 감안하십시오. (가이드 §7)
# MAGIC - **엔드포인트가 `READY` 라는 것은 vLLM 이 살아 있다는 뜻이 아닙니다.** `/health` 를 따로
# MAGIC   감시하십시오.
# MAGIC - 클러스터를 **재시작**하면 `/local_disk0` 이 비워지므로 venv·가중치·serve 를 다시 올려야
# MAGIC   하지만, `cluster_id` 는 그대로이므로 **엔드포인트 설정은 고치지 않아도 됩니다.**
# MAGIC   클러스터를 **삭제 후 재생성**하면 `cluster_id` 가 바뀌므로 셀 4 를 다시 실행하십시오.
