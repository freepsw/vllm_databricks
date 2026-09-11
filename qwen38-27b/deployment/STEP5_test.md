# STEP 5 · 검증

**이 단계에서 하는 일**: 배포가 끝난 엔드포인트를 직접 호출해 스모크 → 기능 → 부하 순으로 정상 여부를 판정합니다.  
**소요 시간**: 스모크 **1분** + 기능 검증 **10분** + (선택) 부하·안정성 **10분 ~ 8시간**.  
**검증 방식**: 3단계 사다리 — 빠른 스모크에서 시작해 필요하면 심화 단계로 진행.

**전제**: STEP4 완료 상태 또는 vLLM 이 `http://127.0.0.1:8005` 에서 실행 중.

---

## (1) 스모크 — 1분

### 확인 항목

**호출 대상**: `http://127.0.0.1:8005/v1/chat/completions` (직접 호출) 또는 엔드포인트(STEP4 경유)  
**기대**: HTTP **200** + 응답 본문 비어 있지 않음

### 실행 (Python)

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8005/v1", api_key="dummy")
r = client.chat.completions.create(
    model="qwen38-27b", max_tokens=100,
    messages=[{"role": "user", "content": "안녕하세요"}])
print(r.choices[0].message.content)
```

### 실행 (curl)

```bash
curl -X POST http://127.0.0.1:8005/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"qwen38-27b",
    "max_tokens":100,
    "messages":[{"role":"user","content":"안녕하세요"}]
  }'
```

**HTTP 200 이고 본문이 비어 있지 않으면 정상입니다.**

---

## (2) 기능 검증 — 약 10분

### 2-1. 한국어 품질과 `<think>` 유출 없음

**확인하는 것**: 한국어 응답이 정상이고 추론 내용이 본문으로 새지 않는지.

**방법**: 한국어 질문 5건을 보내고 응답을 검사합니다. `05_validate.py` 가 항목 3·4 를 자동 검증합니다.

**기대값**: 한국어 **5/5 성공** · `message.content` 에 `<think>` **0건** · 추론 내용은 `message.reasoning` 필드로 분리됨.

```python
for q in ["날씨는?", "서울 인구는?", "파이썬 설명해줘", "논문 요약해줘", "회의록 정리해줘"]:
    r = client.chat.completions.create(model="qwen38-27b", max_tokens=500,
        messages=[{"role": "user", "content": q}])
    assert "<think>" not in r.choices[0].message.content
    print(f"{q} ✓")
```

### 2-2. 스트리밍(SSE) — 토큰 단위 도착 · 버퍼링 없음

**확인하는 것**: 게이트웨이가 응답을 모아서 한 번에 주지 않는지.

**방법**: `stream=True` 로 호출하며 chunk 수를 세고 `completion_tokens` 와 비교.

**기대값**: **chunk 수 ≈ `completion_tokens`** · 1~3토큰 차이 정상 · TTFT p50 약 0.27초 · 토큰 간 간격 p50 약 21 ms.

```python
n = 0
for c in client.chat.completions.create(model="qwen38-27b", stream=True,
        max_tokens=500, messages=[{"role":"user","content":"5문장 써 주세요"}]):
    if c.choices and c.choices[0].delta.content:
        n += 1
print(f"chunk 수: {n} (1~3토큰 차이 정상)")
```

### 2-3. 도구 호출(단일·병렬)

**확인하는 것**: function calling 이 정확히 오는지 (이름·인자 스키마).

**방법**: 도구 정의 → 단일 호출 10회 → 병렬 호출 3회 → 오탐(도구 불필요한 질문) 10회.

**기대값**: 단일 **10/10** · 병렬 **8/8**(각 3콜) · 오탐 **0/10** · 한국어 질문 **8/8**.

```python
tools = [{"type":"function","function":{
    "name":"weather", "description":"날씨 조회",
    "parameters":{"type":"object","properties":{
        "city":{"type":"string"}}}}}]

# 단일 호출
r = client.chat.completions.create(model="qwen38-27b", max_tokens=100,
    messages=[{"role":"user","content":"서울 날씨?"}],
    tools=tools, tool_choice="auto")
assert r.choices[0].message.tool_calls[0].function.name == "weather"
print("도구 호출 ✓")
```

### 2-4. 구조화 출력

**확인하는 것**: JSON 스키마 강제 필드가 보존되는지 (특히 한국어 키).

**방법**: `response_format: {"type":"json_object"}` 에 한국어 키 스키마 포함.

**기대값**: **18/18 성공** · **한국어 키 보존** · 파싱 성공.

```python
r = client.chat.completions.create(model="qwen38-27b", max_tokens=200,
    messages=[{"role":"user","content":"이름과 나이를 JSON으로 주세요"}],
    response_format={"type":"json_object"})
import json
data = json.loads(r.choices[0].message.content)
assert "이름" in data  # 한국어 키 유지
print("구조화 ✓")
```

### 2-5. `tool_choice` 로 특정 함수 강제는 작동하지 않습니다

**확인하는 것**: 강제 지정에 의존하는 코드가 있는지 (있으면 고쳐야 합니다).

**방법**: 도구를 부를 이유가 **없는** 질문에 `tool_choice: {"type":"function","function":{"name":"..."}}` 지정.

**기대값**: **도구가 호출되지 않습니다** · `auto` 처럼 동작 · **이것이 정상입니다** · 직접 호출 경로에서도 동일.

```python
# "날씨" 도구는 이미 정의되어 있음
r = client.chat.completions.create(model="qwen38-27b", max_tokens=100,
    messages=[{"role":"user","content":"파이썬 설명해줘"}],
    tools=tools,
    tool_choice={"type":"function","function":{"name":"weather"}})
# 도구는 호출되지 않음 (Qwen + vLLM 특성)
print("강제 무시 (정상) ✓")
```

> **강제 지정을 구조화 대신 사용하지 마십시오.** 필요하면 2-4 의 `json_object` 를 쓰고, 도구는 `auto` + 프롬프트 유도로 설계하십시오.

### 2-6. 컨텍스트 상한

**확인하는 것**: 긴 입력이 어디서 끊기고 얼마나 빨리 거절되는지.

**방법**: 사용 가능한 프롬프트는 **`max_model_len − max_tokens`** 입니다. 상한을 살짝 넘는 요청 1회.

**기대값**: HTTP **400 `BAD_REQUEST`** 가 **0.38~0.45초** 안에 즉시 옵니다(대기 없음). 한국어 밀도는 장문 실측 **0.543 tok/자** 이므로, `max_tokens=1500` 일 때 넣을 수 있는 프롬프트는 129,572 토큰 ≈ 약 **238,000자**.

---

## (3) 부하·안정성 검증 — 선택

### 주의

**실행 위치가 중요합니다.** 두 스크립트 모두 `127.0.0.1:8005` 로 접속하므로 **vLLM 이 떠 있는 그 드라이버에서** 실행해야 합니다(원격·다른 노드 불가). 즉 이 단계는 vLLM 자체를 봅니다. 게이트웨이 경유 부하는 별도입니다.

### 빠른 검증 — 약 10분

`05_validate.py` 가 8개 항목을 점검합니다.

```bash
PORT=8005 SERVED=qwen38-27b OUT=/local_disk0/validate_result.json \
  SERVE_LOG=/local_disk0/serve.log python3 /local_disk0/scripts/05_validate.py
```

**기대**: 모든 검증 통과 (8/8) · 항목 5 의 p95 지연은 **참고값이며 판정 기준이 아닙니다** — 프롬프트·응답 길이에 따라 달라집니다.

### 장시간 안정성 — 기본 8시간

`06_soak.py` 는 이미 떠 있는 serve 에만 부하를 줍니다.

```python
import os, subprocess, sys

# 이전 실행의 잔여 마커를 반드시 먼저 지웁니다
for stale in ("/local_disk0/SOAK_DONE", "/local_disk0/SOAK_result.json"):
    if os.path.exists(stale):
        os.remove(stale)

os.environ.update({
    "DURATION_S": "28800",  # 8시간(초). 10분 테스트는 "600"
    "CONCURRENCY": "22",
    "MAX_TOKENS": "256",
    "PORT": "8005",
    "SERVED": "qwen38-27b",
    "OUT": "/local_disk0/SOAK_result.json"
})
subprocess.Popen(
    [sys.executable, "/local_disk0/scripts/06_soak.py"],
    stdout=open("/local_disk0/soak_stdout.log", "w"),
    stderr=subprocess.STDOUT,
    start_new_session=True
)
print("분리 실행 중 ... /local_disk0/SOAK_DONE 파일 생성 시 완료")
```

### 완료 판정 — 두 함정

#### 함정 1: `verdict` 로 완료 판정하면 안 됩니다

**틀린 방법:**
```python
import json
with open("/local_disk0/SOAK_result.json") as f:
    data = json.load(f)
    if data["totals"]["verdict"] == "completed":  # ❌ 시험 중에도 "completed"
        print("완료!")
```

**올바른 방법:**
```python
if os.path.exists("/local_disk0/SOAK_DONE"):  # ✓ 파일 존재만 확인
    print("완료!")
```

#### 함정 2: 기본값은 8시간입니다

`DURATION_S=28800` (8시간). 빠르게 확인하려면 `DURATION_S=600` (10분)으로 줄일 수 있지만, **10분 실행은 안정성을 증명하지 않습니다.**

VRAM 누수 판정은 **워밍업 30분을 제외하고 2시간 이후** 정상상태에서만 유효하므로, 누수를 판정하려면 8시간을 완주해야 합니다.

### 클러스터 자동종료 확인

클러스터 `autotermination_minutes` 가 `0` 이 아니면 이 시험이 도중에 죽을 수 있습니다. 추론 트래픽이 자동 종료 타이머를 갱신하는지는 측정되지 않았으므로(실측: 명령 없는 상태에서 90분 후 `INACTIVITY` 로 종료) 갱신되지 않는다고 가정하십시오.

```bash
databricks clusters get <CLUSTER_ID> | grep autotermination_minutes
```

---

## 판정표 — 기대값

| 항목 | 실측 기준선 | 범위 또는 변동 | 비고 |
|---|---|---|---|
| 스모크 응답 | HTTP 200 | 본문 비어 있지 않음 | — |
| 첫 토큰까지(TTFT) p50 | 0.271초 | 참고값 | — |
| 본문 첫 글자까지(TTFCT) p50 | 3.549초 | 프롬프트·응답 길이에 따라 변동 | — |
| 토큰 간 간격(ITL) p50 | 21.4 ms | 프롬프트 길이와 무관 | n=2,299 |
| 출력 생성 속도 | 45.62~46.15 tok/s | — | 같은 GPU·플래그 기준 |
| 한국어 품질 | 5/5 통과 | `<think>` 0건 | — |
| 도구 호출 단일/병렬/오탐 | 10/10 · 8/8 · 0/10 | — | — |
| 구조화 출력 `json_object` | 18/18 성공 | 한국어 키 보존 | — |
| 컨텍스트 초과 | 400 응답 | 0.38~0.45초 | 느리면 상한 재계산 |
| 8시간 soak | 82,129 요청 · 0 오류 | VRAM drift 0.122% | 측정 창 7.998시간 |

---

## 실패했을 때 — 증상 분류

| 증상 | 1차 원인 | 확인 사항 |
|---|---|---|
| **`CUSTOMER_UNAUTHORIZED … serverless network policy`** | 서빙 엔드포인트(serverless 컴퓨트)가 워크스페이스 FQDN 으로 나갈 수 없음 — **vLLM·PAT·바인드와 무관** | 노트북에서 driver-proxy 를 직접 호출해 **200** 이면 상류는 정상입니다. 선택지는 [STEP0 §4](STEP0_gateway_precheck.md) · [부록 A4](appendix/A4_serverless_egress_allowlist.md) — **허용 도메인 추가로는 해결되지 않습니다** |
| **502** (`Bad Gateway`) | 상류 vLLM 사망 · `--host 127.0.0.1` · 클러스터 자동 종료 · 재기동 중 | `/health` 상태 · 프로세스 실행 여부 |
| **403** | PAT scope 부족(`clusters`) · 권한 없음 | 토큰 권한 · 엔드포인트 접근 권한 |
| **400** | tool 플래그 없이 `tools` 요청 · 컨텍스트 초과 · 잘못된 파라미터 | serve 로그 · 입력 길이 |
| **429** (`REQUEST_LIMIT_EXCEEDED`) | 분당 호출 한도 초과 (기본 120/분) | 요청 빈도 · 동시성 조정 |
| **200 인데 본문 비어 있음** | 추론 폭주 — `max_tokens` 를 추론 토큰이 다 씀 (`finish_reason == "length"`) | `finish_reason` 필드 · `max_tokens` 증가 |

---

## 운영 투입 전 반드시 할 것 — 3개

### ① `finish_reason == "length"` 를 실패로 처리

HTTP 200 이지만 본문이 0자인 **추론 폭주** 를 구분하려면 `finish_reason` 을 봐야 합니다. 같은 어려운 질문 7건 중 **6건이 본문 0자** 로 돌아왔습니다.

```python
r = client.chat.completions.create(...)
if r.choices[0].finish_reason == "length":
    # 실패 처리: 재시도 또는 max_tokens 증가
    print("추론 폭주: 본문이 나올 수 없었음")
```

### ② 429 재시도는 클라이언트가 백오프

**`Retry-After` 헤더가 없습니다.** 실측(400건/5.2초 발사 = 4,573 req/분)에서 **200 133건 · 429 267건** 이었고 **+10초 후 다시 200** 이었습니다. 지수 백오프와 요청 큐를 클라이언트에 구현하십시오.

### ③ 클러스터 자동 종료 확인

`autotermination_minutes` 가 `0` 인지 확인하십시오. 자동 종료되면 상류가 죽는데 **엔드포인트는 계속 `READY` 로 보입니다** — 상태만으로는 알 수 없습니다.

상시 서비스라면 드라이버의 `/health` 를 주기적으로 확인하고 자동 복구를 준비하십시오. 클러스터 재시작 시 `/local_disk0` 이 비워지므로 venv·가중치·serve 를 다시 올려야 하지만, 엔드포인트 설정은 고치지 않아도 됩니다.

---

**다음 단계** — 문제가 있으면 [appendix/A2_troubleshooting.md](appendix/A2_troubleshooting.md) 참조. 제공 파일·환경변수 상세는 [appendix/A3_scripts_and_notebooks.md](appendix/A3_scripts_and_notebooks.md) 참조.
