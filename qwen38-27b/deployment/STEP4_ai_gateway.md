# STEP 4 · AI Gateway 엔드포인트 등록

**이 단계에서 하는 일**: 드라이버에서 도는 vLLM 을 Databricks 서빙 엔드포인트로 감싸, 표준 OpenAI 호환 URL 하나로 호출하게 만듭니다. 인증·호출 한도·사용량 추적이 함께 붙고 AI Playground 에 노출됩니다.  
**소요 시간**: [추정] 약 15분 (vLLM 이 이미 `--host 0.0.0.0` 으로 떠 있으면 약 10분). STEP1~3 만 실측입니다.  
**끝났는지 판단하는 기준**: `POST https://<워크스페이스>/serving-endpoints/<엔드포인트>/invocations` 가 **HTTP 200** 이고 `choices[0].message.content` 가 비어 있지 않을 때. 엔드포인트 상태가 `READY` 인 것만으로 판단하지 마십시오 — 상류 vLLM 이 죽어도 상태는 `READY` 로 남습니다.

**전제**: [STEP0_gateway_precheck.md](STEP0_gateway_precheck.md) 를 통과하고, [STEP3_vllm_serve.md](STEP3_vllm_serve.md) 까지 끝나, 드라이버에서 `curl http://127.0.0.1:8005/health` 가 200 인 상태.

---

## 1. 구조 — 무엇을 만드는가

```
외부 agent · AI Playground        Authorization: Bearer <호출자 토큰>
        ▼
https://<워크스페이스>/serving-endpoints/qwen38-27b-vllm/invocations
        │   서빙 엔드포인트 (external model · provider: custom)
        │   AI Gateway: 호출 한도 · 사용량 추적
        ▼   Authorization: Bearer <secret scope 의 PAT>
https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/chat/completions
        ▼   driver-proxy (드라이버 사설 IP 로 접속)
드라이버의 vLLM (0.0.0.0:8005)
```

**왜 driver-proxy 를 경유하는가** — 엔드포인트의 상류 URL 은 **HTTPS 만** 허용됩니다(`http://` 는 `please change to https` 로 거부). 드라이버에 직접 붙는 경로는 사설 IP·자체 인증서 때문에 이 조건을 만족시킬 수 없습니다. driver-proxy URL 은 워크스페이스 도메인을 그대로 쓰므로 공인 인증서·공개 DNS·Bearer 인증이 한 번에 해결됩니다.

---

## 2. vLLM 을 게이트웨이용으로 재기동

**이미 `--host 0.0.0.0` 이고 tool calling 플래그까지 켜져 있으면 이 절을 건너뛰십시오.** 이미 vLLM 이 떠 있어도 아래를 그대로 실행하면 됩니다 — 스크립트가 기존 프로세스를 정리하고 VRAM 반환을 확인한 뒤 다시 올립니다.

```bash
cd /local_disk0/scripts && \
  BIND_HOST=0.0.0.0 TOOL_CALL_PARSER=qwen3_xml PORT=8005 ./04_serve.sh
```

확인 방법: `ps -eo args | grep 'vllm serve'` 에 `--host 0.0.0.0` 과 `--tool-call-parser qwen3_xml` 모두 보이면 됩니다.

### 반드시 지켜야 할 3가지

| # | 지켜야 할 것 | 지키지 않으면 (실측) |
|---|---|---|
| 1 | `--host 0.0.0.0` 으로 기동 | 엔드포인트 호출이 전부 **502**. driver-proxy 는 드라이버 **사설 IP** 로 접속하므로 `127.0.0.1` 바인드에는 닿지 못합니다 |
| 2 | `--api-key` 는 **주지 마십시오** | 호출이 전부 **401**. driver-proxy 가 `Authorization` 헤더를 자신의 인증에 쓰고 상류로 전달하지 않습니다 |
| 3 | tool calling 플래그를 함께 지정 | `tools` 를 담은 요청이 무시되는 것이 아니라 **400** 으로 거절됩니다 (`"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser`) |

`--reasoning-parser qwen3` 과 동시에 사용해도 문제가 없습니다.

> **보안 영향** — `0.0.0.0` 은 클러스터 VNet 에 8005 포트를 노출하고, 위 2번 때문에 **이 포트에는 인증이 전혀 없습니다.** 단일 노드 클러스터를 쓰고 서브넷 NSG 규칙과 peering 구성을 점검하십시오.

---

## 3. PAT 발급과 secret 저장

AI Gateway 가 driver-proxy 에 인증할 때 쓸 토큰입니다. **API scope 는 `clusters` 하나만** 주십시오.

> ⚠️ **토큰은 반드시 노트북 밖에서 발급하십시오.**  
> 노트북 안에서 토큰 생성 API 를 호출하면 새 토큰이 만들어지지 않고 **노트북 컨텍스트에 묶인 토큰**이 반환됩니다(여러 번 호출해도 같은 `token_id`). 그 토큰은 워크스페이스 내부에서는 200 이지만 **외부에서 호출하면 403** 이고, AI Gateway 는 외부에서 호출하므로 엔드포인트가 반드시 403 이 됩니다. **노트북에서 테스트하면 정상으로 보이므로** 특히 주의하십시오.  
> **판별법**: `databricks tokens list` 결과에 그 토큰이 보이면 정상입니다.

scope 조합별로 워크스페이스 외부에서 driver-proxy 를 호출한 실측입니다.

| 지정한 scope | driver-proxy 응답 |
|---|---|
| (미지정 = all-apis) | 200 |
| **`clusters`** | **200** ← 이것만으로 충분합니다 |
| `command-execution` · `workspace` · `databricks-connect` · `model-serving-inference` | 403 |
| `ai-gateway` | **403** ← 이름 때문에 고르기 쉽지만 반대 방향의 권한입니다 |

`ai-gateway` scope 는 *게이트웨이를 호출하는* 쪽의 권한이고, 이 PAT 는 *게이트웨이가 driver-proxy 를 호출할* 때 쓰는 상류 인증입니다. `clusters` 는 선택 가능한 것 중 최소이지만 좁지는 않습니다 — 이 토큰만으로 **클러스터 재시작·종료·편집이 인가를 통과**하므로(`jobs` · `secrets` · `serving-endpoints` 계열은 403) 아래 secret ACL 관리를 반드시 함께 하십시오. 발급 시 **Auto-scope tokens 는 끄십시오**: scope 가 조용히 좁혀지면 엔드포인트는 `READY` 인 채로 호출만 403 이 됩니다.

> **근거의 한계 [등급:미검증]**: 위 표의 200 은 모두 워크스페이스 밖에서 **driver-proxy 를 직접 호출**한 결과입니다(GET `/health`·`/v1/models`, POST `/tokenize` 로 메서드 무관까지 확인). **게이트웨이가 이 `clusters` 전용 토큰으로 상류를 호출하는 종단 경로는 검증하지 못했습니다** — 검증 환경의 운영 토큰은 scope 미지정(all-apis)이었습니다. 토큰은 어느 경로에서든 같은 Bearer 값이므로 통할 것으로 보지만 단정하지 않습니다. 처음 적용하실 때는 아래 §5 의 엔드포인트 호출 1회로 200 을 확인하고, 403 `does not have required scopes` 가 나오면 all-apis 토큰으로 되돌려 원인을 분리하십시오.

### 발급 방법 A (권장) — 로컬 CLI, 토큰이 화면에 표시되지 않음

```bash
databricks secrets create-scope vllm-gateway --profile <PROFILE>

python3 -c "
from databricks.sdk import WorkspaceClient
w = WorkspaceClient(profile='<PROFILE>')
r = w.api_client.do('POST', '/api/2.0/token/create', body={
        'comment': 'ai-gateway-vllm driver-proxy', 'lifetime_seconds': 2592000,
        'scopes': ['clusters'], 'autoscope_enabled': False})
w.secrets.put_secret(scope='vllm-gateway', key='driver_proxy_pat',
                     string_value=r['token_value'])
print('token_id:', r['token_info']['token_id'], 'scopes:', r['token_info']['scopes'])"
```

토큰 값이 터미널·파일·노트북 어디에도 남지 않습니다. SDK 의 `tokens.create` 는 버전에 따라 `scopes` 를 받지 못하므로 `api_client.do` 를 씁니다.

### 발급 방법 B — UI 로 발급 후 노트북에 붙여넣기

1. 우상단 사용자 메뉴 → **설정 → 개발자 → 액세스 토큰 → 새 토큰 생성**, 수명 **30일**
2. **API Scopes → Other APIs** 에서 **Clusters** 만 체크 (**BI Tools** 를 고르면 SQL 계열 scope 가 들어가 driver-proxy 는 403 입니다) · **Auto-scope tokens** 는 끕니다
3. 아래 등록 노트북 **셀 3** 을 실행하면 `pat` 입력칸이 나타납니다 → 붙여넣고 셀을 **다시 실행** → 저장 확인 후 입력칸을 **비웁니다**. 셀 3 은 붙여넣은 값이 노트북 컨텍스트 토큰과 같으면 저장을 거부합니다

### auto-scoping 은 끄십시오 [등급:연역]

Databricks 는 30일 이상 수명의 토큰과 all-APIs 토큰에 대해 사용 현황을 관찰한 뒤 scope 를 **자동으로 좁힙니다.** 상류 인증 토큰이 그렇게 조용히 좁혀지면 엔드포인트는 계속 `READY` 인 채로 호출만 403 이 되어 원인을 찾기 어렵습니다(§8 의 502 와 같은 구조). scope 를 수동 지정하면 그 토큰의 auto-scoping 은 영구 비활성화됩니다. 단, 자동 축소가 실제로 게이트웨이를 깨뜨리는지는 30일 관찰이 필요해 **실측하지 못했습니다.**

### secret scope 접근 권한을 반드시 확인하십시오

이 scope 를 읽을 수 있는 주체는 driver-proxy 를 직접 호출해 **게이트웨이를 우회**할 수 있습니다(호출 한도와 사용량 추적도 함께 우회).

```bash
databricks secrets list-acls vllm-gateway --profile <PROFILE>
databricks secrets delete-acl vllm-gateway users --profile <PROFILE>   # users 가 보이면 제거
```

새로 만든 scope 는 **생성자에게만** `MANAGE` 를 줍니다. 다만 **이미 존재하는 scope 를 재사용하면 그 scope 의 기존 권한을 그대로 물려받습니다** — 검증 중 `users: MANAGE`(워크스페이스 전원 접근)인 scope 가 실제로 발견되었습니다. 또한 방법 A 는 **로컬 CLI 프로파일** 신원으로 scope 를 만들고 노트북은 **노트북 실행 신원**으로 읽으므로, 두 신원이 다르면 `databricks secrets put-acl <scope> <노트북 실행 신원> READ` 가 필요합니다.

---

## 4. 엔드포인트 생성 — `notebook_gateway_register.py`

손으로 옮겨 적는 값을 없앤 것이 이 노트북의 핵심입니다. 워크스페이스에 노트북으로 임포트하십시오 — UI 는 **Import → File**, CLI 는 아래입니다.

```bash
# 패키지 루트(qwen38-27b/)에서 실행합니다
databricks workspace import --language PYTHON --format SOURCE --overwrite \
  /Users/<사용자>/notebook_gateway_register \
  notebooks/notebook_gateway_register.py --profile <PROFILE>
```

> **반드시 §2 에서 vLLM 을 띄운 그 GPU 클러스터에 연결하십시오.** 노트북 셀 2 는 드라이버의 `127.0.0.1:8005` 와 사설 IP 를 호출하고 셀 1 은 클러스터 ID 를 읽습니다. 서버리스나 다른 클러스터에 붙이면 무관해 보이는 오류가 납니다.

**셀 1 만 고치고, 셀 1 → 2 → 3 → 4 를 순서대로 실행합니다.** 엔드포인트 이름은 `databricks-` 로 시작할 수 없습니다.

```python
ENDPOINT   = "qwen38-27b-vllm"   # 만들 엔드포인트 이름 (외부 agent 가 model 로 지정하는 값)
PORT       = 8005                # vLLM 이 듣고 있는 포트
SCOPE, KEY = "vllm-gateway", "driver_proxy_pat"   # PAT 를 담아둘 secret 위치
RATE_LIMIT = 120                 # 분당 호출 한도 (0 이면 설정하지 않음)
```

### 손으로 옮겨 적지 않는 값들

노트북이 환경에서 직접 읽으므로 오타로 인한 장애가 발생하지 않습니다.

| 값 | 읽는 방법 | 손으로 적었다면 틀렸을 때 |
|---|---|---|
| 조직 ID | `w.get_workspace_id()` — 모든 클라우드 동작 | 호스트명 파싱은 Azure 밖에서 잘못된 값을 만듭니다 |
| 클러스터 ID | `spark.conf` | 상류 URL 오류 |
| **모델 이름** | vLLM 의 `/v1/models` 에서 직접 읽음 | 상류 **404** `The model ... does not exist` |
| 컨텍스트 상한 | 동일 응답의 `max_model_len` | 장문 시험이 무의미해집니다 |
| 드라이버 사설 IP | `spark.conf.get("spark.driver.host")` | `socket.gethostbyname(socket.gethostname())` 은 이 환경에서 **`127.0.1.1`** 을 돌려주므로, 그 값으로 점검하면 `127.0.0.1` 바인드에서도 통과해 **아무것도 검증하지 못합니다.** 반드시 `spark.driver.host` 를 쓰십시오 |

### 만들어지는 설정

```json
{"name": "qwen38-27b-vllm",
 "config": {"served_entities": [{"external_model": {
   "name": "qwen38-27b", "provider": "custom", "task": "llm/v1/chat",
   "custom_provider_config": {
     "custom_provider_url": "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/chat/completions",
     "bearer_token_auth": {"token": "{{secrets/vllm-gateway/driver_proxy_pat}}"}}}}]},
 "ai_gateway": {"usage_tracking_config": {"enabled": true},
                "rate_limits": [{"calls": 120, "renewal_period": "minute", "key": "endpoint"}]}}
```

`provider` 가 **`custom`** 인 것이 중요합니다 — 요청 본문을 변형하지 않습니다(`openai` provider 는 파라미터 이름을 바꿔 상류에 전달합니다). secret 은 **참조 문자열**로 들어가며, 설정을 조회하면 `{{secrets/...}}` 가 그대로 돌아오고 토큰 값은 설정에 남지 않습니다.

### 여러 번 실행해도 안전합니다

셀 4 는 같은 이름이 있으면 갱신하고 없으면 생성합니다. 두 경로 모두 실측했습니다.

| 상황 | 동작 | 소요 |
|---|---|---|
| 신규 생성 | `POST` 한 번으로 `ai_gateway` 까지 함께 설정 | READY 까지 수십 초 |
| 기존 갱신 | `PUT .../config` + `PUT .../ai-gateway` **두 번** (설정 갱신 API 는 `ai_gateway` 를 함께 받지 않습니다) | 즉시 |

**주의 2가지**

· 같은 이름의 무관한 엔드포인트가 이미 있으면 그 설정을 덮어씁니다. 처음 실행하기 전에 `databricks serving-endpoints list` 로 이름 충돌을 확인하십시오.  
· `PUT .../ai-gateway` 는 **ai_gateway 블록 전체를 대체**합니다. 즉 `RATE_LIMIT = 0` 으로 다시 실행하면 기존 rate limit 이 사라지고, UI 에서 따로 설정한 guardrails·payload 로깅도 함께 지워집니다. 셀 4 는 `usage_tracking_config` 와 `rate_limits` 만 보냅니다.

클러스터를 **재시작**한 경우 `cluster_id` 는 그대로이므로 엔드포인트 설정을 고칠 필요가 없습니다(2026-09-09 실측). 클러스터를 **삭제 후 재생성**하면 `cluster_id` 가 바뀌므로 **셀 1 → 2 → 4** 를 다시 실행하십시오.

---

## 5. 검증

노트북 **셀 5** 가 한 번의 호출로 전 구간(엔드포인트 → AI Gateway → driver-proxy → vLLM)을 확인합니다.

| 항목 | 통과 기준 |
|---|---|
| 엔드포인트 호출 | HTTP 200 |
| 응답의 `model` | vLLM 의 `--served-model-name` 과 일치 |
| 추론 내용 전달 | `choices[].message.reasoning` 에 내용, `usage.completion_tokens_details.reasoning_tokens` 에 토큰 수 |
| `<think>` 유출 | `content` 에 태그가 섞이지 않음 |

노트북 없이 확인하려면 이 한 번으로 충분합니다. **기대: 200.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" -H "Content-Type: application/json" \
  "https://<워크스페이스>/serving-endpoints/qwen38-27b-vllm/invocations" \
  -d '{"max_tokens":1500,"messages":[{"role":"user","content":"안녕하세요"}]}'
```

실패했다면 최상위 `error_code` 를 믿지 마십시오 — **진짜 원인은 `external_model_error` 안에** 들어 있습니다. 증상별 분류는 [appendix/A2_troubleshooting.md](appendix/A2_troubleshooting.md) 를 보십시오.

### 운영 투입 전에 추가로 하십시오

아래 두 가지는 **엔드포인트 경유**로 확인해야 하며, 노트북에는 포함하지 않았습니다(rate limit 시험은 그 1분의 쿼터를 모두 소진해 직후 호출이 429 가 되기 때문입니다).

- **부하 시험** — 실제 예상 동시성으로 엔드포인트를 경유해 호출하십시오. vLLM 자체 동시성과 게이트웨이 경유 동시성은 다른 시험입니다
- **rate limit 실효 확인** — 한도를 의도적으로 초과시켜 429 가 나는지 확인하고, 확인 후 1분 기다린 뒤 정상 사용하십시오
- **장문·대용량 본문** — 실제 사용할 최대 길이의 프롬프트를 엔드포인트 경유로 보내십시오. 검증 환경에서는 프롬프트 78,693 토큰(본문 0.47MB)이 정상 통과했고, 본문 1.10MB 요청도 게이트웨이는 통과시켜 **컨텍스트 상한이 먼저 걸렸습니다**(vLLM 의 400 메시지가 그대로 전달). 즉 확인된 범위에서 병목은 게이트웨이 본문 크기가 아니라 `--max-model-len` 입니다. 다만 **첫 호출은 prefix 캐시가 비어 있어 느립니다** — 78K 토큰 기준 약 41초를 예상하십시오

---

## 6. 알아둘 응답 형식

| 항목 | 실측 동작 | 클라이언트가 해야 할 일 |
|---|---|---|
| 추론 내용 | `content` 가 아니라 **`choices[].message.reasoning`** 필드로 옵니다. `content` 에 `<think>` 태그가 섞이지 않습니다 | 노출하지 않으려면 버리면 됩니다. `reasoning` 을 아는 파서를 준비하십시오 |
| `completion_tokens` | **추론 토큰이 포함됩니다.** 사소한 질문(`1+1은?`)에서 출력 82토큰 중 **78토큰(95.1 %)** 이 추론이었습니다 | 과금·길이 산정을 이 값으로 하면 본문 길이를 크게 과대평가합니다 |
| `max_tokens` | 추론 토큰이 **먼저** 소비합니다. 한도가 작으면 추론만 하다 끝나고 `content` 가 **빈 문자열**로 돌아옵니다(오류가 아니라 `finish_reason: "length"`) | **1,500 이상**을 주고 `content` 가 비었을 때를 반드시 처리하십시오 |
| `cached_tokens` | **항상 `None`** 입니다 (prefix 캐시는 실제로 동작하지만 이 필드로는 보이지 않습니다) | 캐시 적중을 이 필드로 판단하지 마십시오 |
| 요청의 `model` | **게이트웨이가 엔드포인트 설정값으로 덮어씁니다.** 틀린 모델명을 보내도 200 입니다 | 모델 검증을 응답 `model` 로 하지 마십시오 |
| 알 수 없는 필드 | **조용히 무시**되고 200 이 옵니다 | 오타가 오류로 드러나지 않습니다 |
| 스트리밍 | SSE · **버퍼링 없음** (chunk 수 = `completion_tokens`) | 토큰 단위 수신을 그대로 쓰면 됩니다 |

빈 본문의 발생 경계도 실측했습니다(산수 질문 · 게이트웨이 경유) — `max_tokens` **100 → 6/6 정상**, **200 → 4/6**, **300 → 0/6**. 짧은 한도는 사실상 쓸 수 없습니다. 그리고 **Databricks CLI 의 `serving-endpoints query` 는 `reasoning` 필드를 표시하지 않습니다** — 응답 형식을 확인할 때는 직접 HTTP 호출이나 SDK 를 쓰십시오.

---

## 7. 다른 사용자에게 호출 권한 주기

엔드포인트 생성자에게는 `CAN MANAGE` 가 자동 부여됩니다. 다른 사용자·그룹에는 조회 권한만 주는 것을 권합니다.

```bash
databricks serving-endpoints get qwen38-27b-vllm --profile <PROFILE>      # id 확인
databricks permissions update serving-endpoints <ID> --profile <PROFILE> \
  --json '{"access_control_list":[{"group_name":"<그룹>","permission_level":"CAN_QUERY"}]}'
```

---

## 8. 운영 시 주의사항

### 클러스터 자동 종료가 엔드포인트를 죽입니다 — 가장 중요합니다

**실측**: 검증 환경의 클러스터는 게이트웨이 검증을 마친 뒤 **90분 만에 자동 종료**되었고(`INACTIVITY`, `inactivity_duration_min: 90`), 그 상태에서도 엔드포인트는 계속 `READY` 로 표시되었습니다. 즉 **모든 호출이 실패하는데 엔드포인트 상태만으로는 알 수 없습니다.**

Databricks 문서의 자동 종료 기준은 *"현재 시각과 **마지막 명령 실행** 시각의 차이"* 입니다 [등급:문서근거].  
**추론 트래픽이 이 타이머를 갱신하는지는 측정하지 못했습니다** — 그 구간에 추론 트래픽이 없었습니다 [등급:미측정]. 갱신되지 않는다고 가정하십시오 — 그러면 기본값 90분에서는 8시간 부하 시험이 약 90분 만에 끊깁니다.

| 운영 형태 | 설정 | 감수할 것 |
|---|---|---|
| 상시 서비스 | `autotermination_minutes: 0` ([STEP2_cluster_and_runtime.md](STEP2_cluster_and_runtime.md) 생성 전에 결정) | GPU 과금이 계속됩니다 |
| 시험·데모 | 기본값 유지 | 방치하면 조용히 죽습니다 |

### 엔드포인트가 `READY` 라는 것은 상류가 살아 있다는 뜻이 아닙니다

| 상황 | 엔드포인트 상태 | 조치 |
|---|---|---|
| 클러스터 **자동 종료 · 재시작** | `READY` 인데 호출은 전부 실패 | `/local_disk0` 이 비워집니다. venv·가중치·serve 를 다시 올리십시오([STEP1_model_weights.md](STEP1_model_weights.md)~§2). **`cluster_id` 는 그대로이므로 엔드포인트 설정은 고칠 필요가 없습니다** — 2026-09-09 재시작 후 설정을 전혀 고치지 않고 호출이 성공함을 실측 |
| 클러스터 **삭제 후 재생성** | `READY` 인데 호출은 전부 실패 | `cluster_id` 가 바뀌므로 노트북 **셀 1 → 2 → 4** 를 다시 실행하십시오 |
| **PAT 만료** | `READY` 인데 호출은 전부 실패 | 만료 전에 새 토큰을 같은 secret 에 저장하십시오. 엔드포인트는 secret 을 참조하므로 재생성이 필요 없습니다 |
| vLLM 프로세스 **종료** | `READY` 인데 호출은 전부 실패 | §2 재실행 |

상시 서비스로 운영한다면 `/health` 를 주기적으로 확인하고 자동 복구를 준비하십시오.

### 엔드포인트 생성자는 변경할 수 없습니다

Databricks 는 엔드포인트 생성 시 **호출한 신원을 생성자로 기록하며, 이후 변경할 수 없습니다** [등급:문서근거]. 그리고 설정 갱신 시 생성자의 워크스페이스 멤버십을 다시 확인하므로, **생성자가 워크스페이스를 떠나면 갱신이 `PERMISSION_DENIED` 로 실패**합니다. 클러스터를 재생성해 셀 4 를 다시 실행해야 하는 상황에서 이 문제를 만나면 삭제 후 재생성밖에 방법이 없습니다.

→ 장기 운영이라면 **팀이 소유한 서비스 프린시펄로 엔드포인트를 생성**하십시오. 노트북에서 실행하면 생성자는 실행한 사람이 됩니다. 서비스 프린시펄 PAT 가 driver-proxy 를 통과하는지는 **검증 환경에서 확인하지 못했습니다** [등급:미검증] — 사용자 PAT 로만 검증했습니다.

### PAT 회전

- 사용자 PAT 는 만료 약 7일 전에 본인에게 메일이 갑니다. 서비스 프린시펄 토큰은 워크스페이스 관리자에게 통보됩니다
- **90일 이상 사용되지 않은 PAT 는 자동으로 폐기됩니다** [등급:문서근거]. 예비 토큰을 미리 만들어 두는 방식은 이 정책에 걸립니다
- 교체는 **같은 secret 키에 새 값을 저장**하면 됩니다. 엔드포인트는 참조만 하므로 재생성이 필요 없습니다

### usage tracking 과 payload 로깅

| 기능 | 요구사항 | 검증 환경 실측 |
|---|---|---|
| `rate_limits` | 없음 | metastore 없이 **동작 확인** |
| `usage_tracking_config` | 설정은 metastore 없이 수락되지만, 기록은 Unity Catalog 시스템 테이블에 쌓입니다 | 설정 수락은 확인. **기록 조회는 검증하지 못했습니다**(검증 환경에 metastore 없음). 시스템 테이블 조회는 account 관리자 권한이 필요합니다 |
| `inference_table_config` (payload 로깅) | Unity Catalog metastore + 대상 스키마 `CREATE TABLE` 권한 | **미검증 [등급:미검증].** metastore 가 없어 설정 자체가 실패했습니다 |

---

## 9. 검증한 것과 검증하지 못한 것

### 이 절차에서 배포 확인한 항목

외부 클라이언트 → 엔드포인트 → AI Gateway → driver-proxy → vLLM 전 구간 · `provider: custom` 요청 본문 무변형 · secret 참조 동작 및 조회 시 참조 문자열 그대로 반환 · SSE 스트리밍 · OpenAI SDK 호환 · `reasoning` 필드 전달 · `<think>` 미유출 · tool calling · 상류 오류 메시지 전달 · rate limit 429 발동 · AI Playground 노출 및 대화 · **신규 생성과 기존 갱신 두 경로** · **클러스터 재시작 후 `cluster_id` 불변으로 엔드포인트 무수정 동작** · **자동 종료가 상류를 죽이며 엔드포인트는 `READY` 유지** · 새 secret scope 의 기본 권한이 생성자 한정.

### 이 절차에서 확인하지 못한 항목

| 항목 | 이유 | 위험도 |
|---|---|---|
| **Private Link · serverless egress 제한 워크스페이스** | 검증 환경은 공개 접근 가능 · **[STEP0](STEP0_gateway_precheck.md) 의 사전 시험으로 반드시 확인하십시오** | **고** |
| **clusters-only PAT 로 게이트웨이 경유 종단 동작** | driver-proxy 직접 호출로만 200 확인(§3). 운영 토큰은 all-apis 였음 | **중** |
| 서비스 프린시펄 PAT 로 driver-proxy 통과 | 사용자 PAT 로만 검증 | 중 |
| driver-proxy 에 필요한 최소 클러스터 권한 | 워크스페이스 관리자 권한으로만 검증 | 중 |
| Unity Catalog Volume 배포 경로 · payload 로깅 · usage 기록 조회 | 검증 환경에 metastore 없음 | 중 |
| Azure Key Vault 백엔드 secret scope | `DATABRICKS` 백엔드로만 검증 | 낮음 |
| PAT 만료 시점의 거동 | 만료시키지 않았음 | 낮음 |
| 추론 트래픽만으로 자동 종료가 발동하는지 | 문서 문구와 종료 실측에 근거한 판단 | 낮음 |
| AWS · GCP | Azure 에서만 검증 | 낮음 |

성능 측정의 한계는 [성능 트랙 부록 B5 · 측정하지 않은 것](../performance/appendix/B5_limitations.md) 를 참조하십시오(이것은 배포 절차의 한계와 다릅니다).

---

## 다음 단계

엔드포인트가 200 을 돌려줬다면 **[STEP5_test.md](STEP5_test.md)** 로 넘어가, 직접 돌려서 정상 여부를 판정하는 3단계 검증을 수행하십시오.

**문제가 났다면**:
- 증상별 분류: [appendix/A2_troubleshooting.md](appendix/A2_troubleshooting.md)
- 제공 파일과 역할: [appendix/A3_scripts_and_notebooks.md](appendix/A3_scripts_and_notebooks.md)
- serverless egress 차단 — 원인과 선택지: [appendix/A4_serverless_egress_allowlist.md](appendix/A4_serverless_egress_allowlist.md) (**허용 도메인 추가로는 해결되지 않습니다**)
- 성능과 비용: [../performance/P1_direct_vs_gateway.md](../performance/P1_direct_vs_gateway.md), [../performance/P3_capacity_and_cost.md](../performance/P3_capacity_and_cost.md)
