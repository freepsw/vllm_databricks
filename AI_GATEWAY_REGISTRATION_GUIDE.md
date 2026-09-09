# Qwen3.8-27B 을 AI Gateway 엔드포인트로 등록하는 방법

**대상 환경**: 고객 Azure Databricks 워크스페이스 · GPU 클러스터(A100) 드라이버에서 vLLM 동작 중  
**결과물**: 표준 OpenAI 호환 서빙 엔드포인트 + AI Playground 노출 + rate limit · usage tracking  
**실행 자산**: `notebook_gateway_register.py` (노트북 1개, 셀 6개, 외부 파일 의존 없음)  
**최종 실측일**: 2026-09-09 (Azure koreacentral · DBR 19.5 GPU ML · vLLM 0.28.0)

---

## 이 문서의 범위와 전제

이 문서는 **이미 동작 중인 vLLM 을 엔드포인트로 등록하는 것**만 다룹니다.

| 구분 | 내용 | 문서 |
|---|---|---|
| **전제** | 클러스터 생성 · venv 빌드 · 가중치 준비 · vLLM serve 기동 | `DEPLOYMENT_GUIDE.md` §1~§5 |
| **이 문서** | vLLM 재기동(게이트웨이용) → PAT → 엔드포인트 생성 → 검증 → 사용 | 이 문서 |

전제가 충족된 상태란 드라이버에서 다음이 성공하는 상태입니다.

```bash
curl http://127.0.0.1:8005/health
```

> **주의**: vLLM 이 **`--host 0.0.0.0`** 으로 떠 있어야 합니다. `127.0.0.1` 이면 엔드포인트를
> 만들어도 **호출이 전부 502** 가 됩니다.
>
> - `DEPLOYMENT_GUIDE.md` §4.1 의 결정 표에서 이미 `0.0.0.0` 을 선택했다면 **§2 를 건너뛰고
>   §3 으로 가십시오.** (노트북 셀 2 가 자동으로 검증합니다.)
> - `127.0.0.1` 로 띄웠다면 §2 에서 재기동해야 합니다.
>
> 확인 방법: `ps -eo args | grep 'vllm serve'` 에 `--host 0.0.0.0` 이 보이면 됩니다.

---

## 0. 시작 전 점검 — 5분

여기서 막히는 항목이 있으면 **뒤 작업 전체가 헛수고가 됩니다.** 30GB 가중치와 GPU 시간을
투입하기 전에 먼저 확인하십시오.

### 0.1 워크스페이스 전제조건

로컬 터미널에서 실행합니다(`<P>` 는 CLI 프로파일 이름).

| # | 확인 명령 | 통과 기준 | 실패 시 |
|---|---|---|---|
| 1 | `databricks api get "/api/2.0/workspace-conf?keys=enableTokensConfig" --profile <P>` | `"true"` | **작업 불가.** PAT 가 비활성이면 상류 인증 수단이 없습니다 (아래 설명) |
| 2 | `databricks api get "/api/2.0/workspace-conf?keys=maxTokenLifetimeDays" --profile <P>` | 값 확인 | 이 값보다 짧은 수명으로 PAT 를 발급하십시오 |
| 3 | `databricks current-user me --profile <P>` | 자기 정보가 정상 반환 | 인증·프로파일을 먼저 해결하십시오. 엔드포인트 생성에는 `workspace-access` 권한이 필요하지만 이 명령 출력에는 표시되지 않으므로, 4번 이후가 막히면 관리자에게 확인하십시오 |
| 4 | Azure 포털에서 워크스페이스 리소스의 **위치(Location)** 확인 | External models 지원 리전 | 미지원 리전이면 엔드포인트 생성 자체가 불가 |
| 5 | `databricks secrets list-scopes --profile <P>` | `backend_type` 확인 | Azure Key Vault 백엔드 scope 는 Databricks 쪽에서 값을 쓸 수 없을 가능성이 높습니다(미검증). `DATABRICKS` 백엔드 scope 를 새로 만드는 편이 안전합니다 |

**1번이 가장 중요합니다.** 이 설계는 상류(driver-proxy) 인증에 **정적 토큰**을 씁니다. 엔드포인트
설정이 받아들이는 인증 방식은 `bearer_token_auth` 와 `api_key_auth` 두 가지뿐이며, 둘 다 정적
값입니다. 서비스 프린시펄 OAuth(M2M) 토큰은 단기 토큰이라 넣을 수 없습니다. 따라서 워크스페이스가
PAT 를 전면 비활성화했다면 **이 방식은 성립하지 않습니다.** 관리자에게 PAT 활성화를 요청하거나
다른 방식(관리형 GPU 서빙 등)을 검토하십시오.

**리전 주의**: External models 는 리전별로 지원 여부가 다릅니다. koreacentral 은 지원됩니다.
`japanwest` · `ukwest` · `australiacentral` · `westcentralus` 등 일부 리전은 미지원이므로
Databricks 의 리전별 기능 지원 표를 먼저 확인하십시오.

### 0.2 도달성 사전 시험 — 네트워크 차단 여부를 먼저 결판냅니다

이 설계는 **AI Gateway 가 워크스페이스 도메인의 driver-proxy 를 호출**하는 구조입니다.
Private Link 전용 워크스페이스나 serverless egress 제한 정책이 걸린 워크스페이스에서
이 경로가 막히는지는 **Databricks 공식 문서에 서술이 없습니다.** 그러므로 측정으로 확인합니다.

가중치도 venv 도 필요 없습니다. 아무 GPU 클러스터(혹은 작은 클러스터)에서 3분이면 끝납니다.

```python
# 노트북 셀 1개 — 드라이버에 임시 HTTP 서버를 띄웁니다
import subprocess
subprocess.Popen(["python3", "-m", "http.server", "8005"], start_new_session=True)
print("org  :", spark.conf.get("spark.databricks.clusterUsageTags.clusterOwnerOrgId"))
print("clu  :", spark.conf.get("spark.databricks.clusterUsageTags.clusterId"))
print("host :", spark.conf.get("spark.databricks.workspaceUrl"))
```

> 이 임시 서버는 **인증이 없습니다.** 시험이 끝나면 반드시 정리하십시오
> (`import subprocess; subprocess.run(["pkill","-f","http.server"])`). 또한 포트가 이미
> 사용 중이면 다른 프로세스가 응답해 **거짓 통과**가 될 수 있으므로, 응답 본문이 파일 목록인지
> 확인하십시오.

그 다음 **워크스페이스 밖(고객 노트북 PC)** 에서, 노트북 밖에서 발급한 PAT 로 호출합니다.

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/"
```

**200 이 나와야 합니다.** 200 이 아니면 §1 이후를 시작하지 마시고 네트워크 정책을 먼저
해결하십시오. 확인할 것은 세 가지입니다.

- 워크스페이스의 public network access 차단 여부
- account console 의 Network Policies 에 restricted 정책이 이 워크스페이스에 걸려 있는지
  (걸려 있으면 워크스페이스 FQDN 을 허용 목록에 추가)
- IP access list 설정

> **참고**: `driver-proxy-api` 는 Databricks 공식 문서에 기재되지 않은 내부 경로입니다.
> 고객 아키텍처 리뷰에서 지적될 수 있으므로 미리 인지하십시오. 이 문서의 구성은 실측으로
> 동작을 확인한 것이며, 향후 플랫폼 변경에 영향받을 수 있습니다.

---

## 1. 구조 — 무엇을 만드는가

```
외부 agent · AI Playground
        │  Authorization: Bearer <호출자 토큰>
        ▼
https://<워크스페이스>/serving-endpoints/qwen38-27b-vllm/invocations
        │  ← Databricks 서빙 엔드포인트 (external model, provider=custom)
        │    AI Gateway: rate limit · usage tracking
        │  Authorization: Bearer <secret scope 의 PAT>
        ▼
https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/chat/completions
        │  ← Databricks driver-proxy (드라이버 사설 IP 10.139.x.x 로 접속)
        ▼
드라이버의 vLLM (0.0.0.0:8005)
```

**왜 driver-proxy 를 경유하는가** — 엔드포인트의 상류 URL 은 **HTTPS 만** 허용됩니다
(`http://` 로 만들면 `please change to https` 오류로 거부됩니다). 드라이버에 직접 붙는 경로는
사설 IP·자체 인증서 문제로 이 조건을 만족시킬 수 없습니다. driver-proxy URL 은 워크스페이스
도메인을 그대로 쓰므로 공인 인증서·공개 DNS·Bearer 인증이 한 번에 해결됩니다.

---

## 2. Step 1 · vLLM 을 게이트웨이용으로 재기동

**이미 `--host 0.0.0.0` 이고 tool calling 플래그까지 켜져 있으면 이 절을 건너뛰십시오.**
`127.0.0.1` 로 띄웠거나 tool 플래그가 없다면, 환경변수를 지정해 다시 올립니다.

| 환경변수 | 값 | 이유 |
|---|---|---|
| `BIND_HOST` | `0.0.0.0` | **필수.** driver-proxy 는 드라이버 **사설 IP** 로 접속합니다 |
| `TOOL_CALL_PARSER` | `qwen3_xml` | 외부 agent 의 function calling 을 쓰려면 필요 |

```bash
cd /local_disk0/scripts && \
  BIND_HOST=0.0.0.0 \
  TOOL_CALL_PARSER=qwen3_xml \
  PORT=8005 \
  ./04_serve.sh
```

이미 vLLM 이 떠 있어도 그대로 실행하면 됩니다. 스크립트가 기존 프로세스를 정리하고 VRAM 반환을
확인한 뒤 다시 올립니다. 기동에는 **약 5분**이 걸립니다.

### 반드시 지켜야 할 3가지

| # | 지켜야 할 것 | 지키지 않으면 (실측) |
|---|---|---|
| 1 | `--host 0.0.0.0` 으로 기동 | 엔드포인트 호출이 **502** (`INTERNAL_ERROR`) |
| 2 | `--api-key` 는 **주지 마십시오** | 호출이 전부 **401**. driver-proxy 가 `Authorization` 헤더를 자신의 인증에 쓰고 상류로 전달하지 않습니다 |
| 3 | tool calling 플래그를 함께 지정 | `tools` 를 담은 요청이 무시되는 것이 아니라 **400** 으로 거절됩니다 |

3번의 오류 메시지는 다음과 같습니다.

```
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser
```

`--reasoning-parser qwen3` 과 **동시에 사용해도 문제가 없습니다.**

> **바인드 주소의 보안 영향** — `0.0.0.0` 은 클러스터가 속한 VNet 에 8005 포트를 노출합니다.
> 그리고 위 2번 때문에 **이 포트에는 인증이 전혀 없습니다.** VNet 내부에서 도달 가능한 경로가
> 하나라도 있으면 게이트웨이·rate limit·usage tracking 을 모두 우회한 무인증 접근이 가능합니다.
> 단일 노드 클러스터를 쓰고, 클러스터 서브넷의 NSG 규칙과 peering 구성을 점검하십시오.

---

## 3. Step 2 · PAT 발급과 secret 저장

AI Gateway 가 driver-proxy 에 인증할 때 쓸 토큰입니다.

> ⚠️ **토큰은 반드시 노트북 밖에서 발급하십시오.**
>
> 노트북 안에서 토큰 생성 API 를 호출하면 **새 토큰이 만들어지지 않고 노트북 컨텍스트에 묶인
> 토큰이 반환됩니다** (여러 번 호출해도 같은 `token_id`). 그 토큰은 워크스페이스 내부에서는
> 정상 동작하지만 **외부에서 호출하면 403** 입니다.
>
> | 호출 위치 | 노트북에서 얻은 토큰 | 노트북 밖에서 발급한 PAT |
> |---|---|---|
> | 워크스페이스 내부(노트북) | 200 | 200 |
> | 워크스페이스 외부 | **403** | 200 |
>
> AI Gateway 는 **외부에서 호출**하므로 노트북 토큰을 쓰면 엔드포인트가 반드시 403 이 됩니다.  
> **노트북에서 테스트할 때는 정상으로 보이므로** 특히 주의해야 합니다.  
> **판별법**: `databricks tokens list` 결과에 그 토큰이 보이면 정상입니다.

### API Scope 는 `clusters` 하나만 주십시오

PAT 는 발급 시 **API scope** 를 지정할 수 있고(scoped PAT), UI 의 **새 토큰 생성** 화면은
scope 선택을 요구합니다. **driver-proxy 도달에 필요한 scope 는 `clusters` 하나입니다.**

scope 조합별로 단기 토큰을 발급해 **워크스페이스 외부**(= 게이트웨이와 같은 위치)에서
driver-proxy 를 호출한 실측입니다.

| 지정한 scope | driver-proxy 응답 |
|---|---|
| (미지정 = all-apis) | 200 |
| **`clusters`** | **200** ← 이것만으로 충분합니다 |
| `command-execution` · `workspace` · `databricks-connect` · `model-serving-inference` | 403 |
| `ai-gateway` | **403** |

> ⚠️ **이름 때문에 `ai-gateway` 를 고르기 쉽지만 403 입니다.** 그 scope 는 *게이트웨이
> 엔드포인트를 호출하는* 쪽(외부 agent)의 권한이고, 이 PAT 는 반대 방향 — *게이트웨이가
> driver-proxy 를 호출할* 때 쓰는 **상류 인증**입니다. 서로 다른 토큰입니다.

scope 가 부족하면 driver-proxy 가 **필요한 scope 이름을 그대로 알려줍니다.**

```json
HTTP 403
{"error_code":403,
 "message":"Provided access token does not have required scopes: clusters [ReqId: ...]"}
```

전체 scope 목록은 `databricks api get /api/2.0/token-scopes --profile <P>` 로 확인할 수 있습니다
(검증 환경 55개).

**auto-scoping 은 끄십시오.** Databricks 는 30일 이상 수명의 토큰과 all-APIs 토큰에 대해 사용
현황을 관찰한 뒤 scope 를 **자동으로 좁힙니다.** 상류 인증 토큰이 그렇게 조용히 좁혀지면
엔드포인트는 계속 `READY` 인 채로 호출만 403 이 되어 원인을 찾기 어렵습니다(§8 의 502 와 같은
구조). scope 를 수동 지정하면 그 토큰의 auto-scoping 은 영구 비활성화됩니다.
단, 자동 축소가 실제로 게이트웨이를 깨뜨리는지는 30일 관찰이 필요해 **실측하지 못했습니다.**

### 발급 방법 A (권장) — 로컬 CLI, 토큰이 화면에 표시되지 않음

```bash
databricks secrets create-scope vllm-gateway --profile <P>

python3 -c "
from databricks.sdk import WorkspaceClient
w = WorkspaceClient(profile='<P>')
r = w.api_client.do('POST', '/api/2.0/token/create', body={
        'comment': 'ai-gateway-vllm driver-proxy',
        'lifetime_seconds': 2592000,     # 30일
        'scopes': ['clusters'],          # 최소 권한
        'autoscope_enabled': False})
w.secrets.put_secret(scope='vllm-gateway', key='driver_proxy_pat',
                     string_value=r['token_value'])
print('token_id:', r['token_info']['token_id'], 'scopes:', r['token_info']['scopes'])"
```

토큰 값이 터미널·파일·노트북 어디에도 남지 않습니다.

> **`w.tokens.create(...)` 대신 `api_client.do` 를 쓰는 이유**: SDK 의 `tokens.create` 에
> `scopes` 파라미터가 추가된 것은 최근 버전입니다(실측: 0.73.0 **없음** / 0.108.0 **있음**).
> `api_client.do` 는 SDK 버전에 무관하게 동작합니다. CLI `databricks tokens create` 에는
> 아직 `--scopes` 플래그가 없습니다(v1.1.0 실측).

### 발급 방법 B — UI 로 발급 후 노트북에 붙여넣기

1. 우상단 사용자 메뉴 → **설정 → 개발자 → 액세스 토큰 → 새 토큰 생성**
2. 수명을 **30일**로 지정합니다
3. **API Scopes** 에서 **Other APIs** 를 선택하고 **Clusters** 만 체크합니다
   (**BI Tools** 를 고르면 SQL 계열 scope 가 들어가 driver-proxy 는 403 입니다)
4. **Auto-scope tokens** 는 **끕니다** (위 설명 참조)
5. 노트북 `notebook_gateway_register.py` 의 **셀 3** 을 실행하면 `pat` 입력칸이 나타납니다
6. 값을 붙여넣고 셀을 **다시 실행**
7. 저장 확인 후 입력칸을 **비웁니다**

셀 3 은 붙여넣은 값이 노트북 컨텍스트 토큰과 같으면 저장을 거부합니다.

### secret scope 접근 권한을 반드시 확인하십시오

이 scope 를 읽을 수 있는 주체는 driver-proxy 를 직접 호출해 **게이트웨이를 우회**할 수 있습니다
(rate limit 과 usage tracking 도 함께 우회됩니다).

```bash
databricks secrets list-acls vllm-gateway --profile <P>
```

**실측**: 새로 만든 scope 는 CLI · SDK 모두 **생성자에게만** `MANAGE` 를 부여합니다. 다만
**이미 존재하는 scope 를 재사용하면 그 scope 의 기존 권한을 그대로 물려받습니다**
(`create-scope` 는 이미 있으면 아무 일도 하지 않습니다). 검증 환경에서 실제로 `users: MANAGE`
(워크스페이스 전원 접근)인 scope 가 발견되었으므로, 재사용 시에는 반드시 확인하십시오.

```bash
databricks secrets delete-acl vllm-gateway users --profile <P>       # 필요 시 제거
databricks secrets put-acl vllm-gateway <운영그룹> MANAGE --profile <P>
```

**신원을 일치시키십시오.** 위 방법 A 는 **로컬 CLI 프로파일**의 신원으로 scope 를 만들고,
노트북 셀 3 과 엔드포인트는 **노트북을 실행하는 신원**으로 secret 을 읽습니다. 새 scope 의 기본
권한은 **생성자 전용**이므로 두 신원이 다르면 셀 3 이 권한 오류로 실패합니다. 같은 신원을 쓰거나,
`databricks secrets put-acl <scope> <노트북 실행 신원> READ` 로 권한을 부여하십시오.

> **운영 권고**: 사용자 개인 PAT 대신 **서비스 프린시펄** 소유 토큰을 쓰는 편이 안전합니다
> (담당자 변경·퇴사에 영향받지 않음). 단, 서비스 프린시펄 PAT 가 driver-proxy 를 통과하는지는
> **이 프로젝트에서 검증하지 못했습니다.** 사용자 PAT 로만 검증했습니다.

---

## 4. Step 3 · 엔드포인트 생성

### 노트북 가져오기와 클러스터 연결

`notebook_gateway_register.py` 를 워크스페이스에 **노트북으로** 임포트합니다.

```bash
databricks workspace import \
  --language PYTHON --format SOURCE --overwrite \
  /Users/<사용자>/notebook_gateway_register \
  notebook_gateway_register.py --profile <P>
```

UI 로 하려면 워크스페이스에서 **Import → File** 로 이 `.py` 파일을 올리면 됩니다.

> **반드시 §2 에서 vLLM 을 띄운 그 GPU 클러스터에 연결하십시오.** 셀 2 는 드라이버의
> `127.0.0.1:8005` 를 호출하고, 셀 1 은 클러스터 ID 를 읽습니다. 서버리스나 다른 클러스터에
> 연결하면 서로 무관해 보이는 오류가 발생합니다.

그 다음 **셀 1 → 셀 4** 를 순서대로 실행합니다.
셀 1 만 환경에 맞게 고치고, 나머지는 그대로 실행하면 됩니다.

```python
ENDPOINT   = "qwen38-27b-vllm"   # 만들 엔드포인트 이름 (외부 agent 가 model 로 지정)
PORT       = 8005                # vLLM 포트
SCOPE, KEY = "vllm-gateway", "driver_proxy_pat"
RATE_LIMIT = 120                 # 분당 호출 한도 (0 이면 설정하지 않음)
```

**엔드포인트 이름은 `databricks-` 로 시작할 수 없습니다.**

### 손으로 옮겨 적지 않는 값들

노트북이 환경에서 직접 읽으므로 오타로 인한 장애가 발생하지 않습니다.

| 값 | 읽는 방법 | 손으로 적었다면 틀렸을 때 |
|---|---|---|
| 조직 ID | `w.get_workspace_id()` — 모든 클라우드 동작 | 호스트명 파싱은 Azure 밖에서 잘못된 값을 만듭니다 |
| 클러스터 ID | `spark.conf` | 상류 URL 오류 |
| **모델 이름** | vLLM 의 `/v1/models` 에서 직접 읽음 | 상류 **404** `The model ... does not exist` |
| 컨텍스트 상한 | 동일 응답의 `max_model_len` | 장문 시험이 무의미해짐 |
| 드라이버 사설 IP | `spark.conf.get("spark.driver.host")` | 아래 주의 참조 |

> **실측 주의**: `socket.gethostbyname(socket.gethostname())` 은 이 환경에서 **`127.0.1.1`**
> (루프백)을 돌려줍니다. 이 값으로 도달성을 점검하면 `127.0.0.1` 바인드 상태에서도 점검이
> 통과해 버려 **아무것도 검증하지 못합니다.** 반드시 `spark.driver.host` 를 쓰십시오
> (실측값 `10.139.64.4`).

### 만들어지는 설정

```json
{
  "name": "qwen38-27b-vllm",
  "config": {"served_entities": [{"external_model": {
    "name": "qwen38-27b",
    "provider": "custom",
    "task": "llm/v1/chat",
    "custom_provider_config": {
      "custom_provider_url": "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/chat/completions",
      "bearer_token_auth": {"token": "{{secrets/vllm-gateway/driver_proxy_pat}}"}
    }}}]},
  "ai_gateway": {
    "usage_tracking_config": {"enabled": true},
    "rate_limits": [{"calls": 120, "renewal_period": "minute", "key": "endpoint"}]
  }
}
```

네 가지를 눈여겨보십시오.

- `provider` 는 **`custom`** 입니다. 요청 본문을 변형하지 않습니다. (`openai` provider 는
  파라미터 이름을 바꿔 상류에 전달합니다.)
- `custom_provider_url` 은 **`/v1/chat/completions` 까지 전체 경로**를 줍니다.
- `task` 는 **`llm/v1/chat`** 입니다. 보내지 않아도 서버가 같은 값으로 채우지만, 명시하는 편이
  읽기 쉽습니다.
- secret 은 **참조 문자열**로 들어갑니다. 조회하면 `{{secrets/...}}` 문자열이 그대로 돌아오며
  토큰 값은 설정에 남지 않습니다.

### 여러 번 실행해도 안전합니다

셀 4 는 같은 이름이 있으면 갱신하고 없으면 생성합니다. 두 경로 모두 실측했습니다.

| 상황 | 동작 | 소요 |
|---|---|---|
| 신규 생성 | `POST` 한 번으로 `ai_gateway` 까지 함께 설정 | READY 까지 수십 초 |
| 기존 갱신 | `PUT .../config` + `PUT .../ai-gateway` **두 번** (설정 갱신 API 는 `ai_gateway` 를 함께 받지 않습니다) | 즉시 |

**주의 2가지**

- 같은 이름의 무관한 엔드포인트가 이미 있으면 그 설정을 덮어씁니다. 처음 실행하기 전에
  `databricks serving-endpoints list` 로 이름 충돌을 확인하십시오.
- `PUT .../ai-gateway` 는 **ai_gateway 블록 전체를 대체**합니다. 즉 `RATE_LIMIT = 0` 으로 다시
  실행하면 기존 rate limit 이 사라지고, UI 에서 따로 설정한 guardrails·payload 로깅도 함께
  지워집니다. 셀 4 는 `usage_tracking_config` 와 `rate_limits` 만 보냅니다.

---

## 5. Step 4 · 검증

노트북 **셀 5** 가 한 번의 호출로 전체 경로를 확인합니다. 확인 항목은 다음과 같습니다.

| 항목 | 통과 기준 |
|---|---|
| 엔드포인트 호출 | HTTP 200 |
| 응답의 `model` | vLLM 의 `--served-model-name` 과 일치 |
| 추론 내용 전달 | `choices[].message.reasoning` 에 내용, `usage.completion_tokens_details.reasoning_tokens` 에 토큰 수 |
| `<think>` 유출 | `content` 에 태그가 섞이지 않음 |

**셀 6** 이 외부 agent 관점의 사용 예시(OpenAI SDK · 스트리밍 · tool calling)를 함께 확인합니다.

### 운영 투입 전에 추가로 하십시오

아래 두 가지는 **엔드포인트 경유**로 확인해야 하며, 노트북에는 포함하지 않았습니다
(rate limit 시험은 그 1분의 쿼터를 모두 소진해 직후 호출이 429 가 되기 때문입니다).

- **부하 시험** — 실제 예상 동시성으로 엔드포인트를 경유해 호출하십시오. vLLM 자체 동시성과
  게이트웨이 경유 동시성은 다른 시험입니다.
- **rate limit 실효 확인** — 한도를 의도적으로 초과시켜 429 가 나는지 확인하고, 확인 후
  1분 기다린 뒤 정상 사용하십시오.
- **장문·대용량 본문** — 실제 사용할 최대 길이의 프롬프트를 엔드포인트 경유로 보내십시오.
  검증 환경에서는 프롬프트 78,693 토큰(본문 0.47MB)이 정상 통과했고, 본문 1.10MB 요청도
  게이트웨이는 통과시켜 **컨텍스트 상한이 먼저 걸렸습니다**(vLLM 의 400 메시지가 그대로 전달).
  즉 확인된 범위에서 병목은 게이트웨이 본문 크기가 아니라 `--max-model-len` 입니다.
  다만 **첫 호출은 prefix 캐시가 비어 있어 느립니다** — 78K 토큰 기준 약 41초를 예상하십시오.

---

## 6. Step 5 · 사용 방법

### 외부 agent (OpenAI SDK)

`base_url` 은 **엔드포인트 경로가 아니라 `/serving-endpoints` 까지**만 주고, `model` 에
**엔드포인트 이름**을 넣습니다.

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<워크스페이스>/serving-endpoints",
    api_key="<노트북 밖에서 발급한 PAT>",
)

stream = client.chat.completions.create(
    model="qwen38-27b-vllm",                      # 엔드포인트 이름
    messages=[{"role": "user", "content": "안녕하세요"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### 외부 agent (curl)

```bash
curl -N -X POST \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  "https://<워크스페이스>/serving-endpoints/qwen38-27b-vllm/invocations" \
  -d '{"messages":[{"role":"user","content":"안녕하세요"}],"stream":true}'
```

### AI Playground

좌측 메뉴 **AI/ML → Playground** 로 이동한 뒤,

1. 상단의 엔드포인트 선택 버튼 클릭
2. 목록에서 `qwen38-27b-vllm` 선택 — **`External` · `Ready` · `Tools enabled`** 로 표시됩니다
3. **Use endpoint** 클릭 후 대화

### 응답 형식에서 알아둘 점

- 추론 내용은 `content` 가 아니라 **`choices[].message.reasoning`** 필드로 옵니다.
  토큰 수는 `usage.completion_tokens_details.reasoning_tokens` 에 있습니다. 노출하고 싶지
  않으면 클라이언트에서 버리면 됩니다. `content` 에는 `<think>` 태그가 섞이지 않습니다.
- **`max_tokens` 는 추론 토큰이 먼저 소비합니다.** 이 모델은 답을 쓰기 전에 추론을 하고, 그
  추론 토큰이 `max_tokens` 한도에서 **먼저** 차감됩니다. 한도가 작으면 추론만 하다 끝나고
  `content` 가 **빈 문자열**로 돌아옵니다(오류가 아니라 `finish_reason: length`).
  실측: 같은 프롬프트를 `max_tokens=300` 으로 6회 호출했을 때 **5회가 빈 본문**이었고
  추론 토큰은 255~300 이었습니다. `max_tokens=2000` 에서는 3/3 모두 정상 응답했습니다.
  → 외부 agent 를 구현할 때 **1,500 이상**을 주고, `content` 가 비었을 때를 처리하십시오.
- **Databricks CLI 의 `serving-endpoints query` 는 `reasoning` 필드를 표시하지 않습니다.**
  응답 형식을 확인할 때는 직접 HTTP 호출이나 SDK 를 쓰십시오.
- 요청 본문에 `model` 을 넣어도 **게이트웨이가 엔드포인트 설정값으로 덮어씁니다.**
- 컨텍스트 상한을 넘기면 vLLM 의 오류 메시지가 그대로 전달됩니다.

### 다른 사용자에게 호출 권한 주기

엔드포인트 생성자에게는 `CAN MANAGE` 가 자동 부여됩니다. 다른 사용자에게는 조회 권한만
주는 것을 권합니다.

```bash
databricks serving-endpoints get qwen38-27b-vllm --profile <P>      # id 확인
databricks permissions update serving-endpoints <ID> --profile <P> \
  --json '{"access_control_list":[{"group_name":"<그룹>","permission_level":"CAN_QUERY"}]}'
```

---

## 7. 운영 시 주의사항

### 클러스터 자동 종료가 엔드포인트를 죽입니다 — 가장 중요합니다

**실측**: 검증 환경의 클러스터는 게이트웨이 검증을 마친 뒤 **90분 만에 자동 종료**되었고
(`INACTIVITY`, `inactivity_duration_min: 90`), 그 상태에서도 엔드포인트는 계속 `READY` 로
표시되었습니다. 즉 **모든 호출이 실패하는데 엔드포인트 상태만으로는 알 수 없습니다.**

Databricks 문서의 자동 종료 기준은 *"현재 시각과 **마지막 명령 실행** 시각의 차이"* 입니다.
driver-proxy 를 통한 추론 트래픽은 "명령 실행"이 아니므로, **추론 요청이 계속 들어와도
자동 종료가 발동할 것으로 보입니다.** (문서 문구와 위 실측에 근거한 판단이며, 추론 트래픽만으로
자동 종료가 발동하는지는 직접 측정하지 못했습니다.)

| 운영 형태 | 설정 | 감수할 것 |
|---|---|---|
| 상시 서비스 | `autotermination_minutes: 0` | GPU 과금이 계속됩니다 (아래) |
| 시험·데모 | 기본값 유지 | 방치하면 조용히 죽습니다 |

**비용** — `Standard_NC24ads_A100_v4` 는 koreacentral 온디맨드 **시간당 4.959 USD**
(Azure Retail Prices API, 2026-09-09 조회). 상시 운영이면 VM 만 **월 약 3,570 USD** 입니다(30일 = 720시간 기준).
**Databricks DBU 요금은 별도**이며 이 문서에서 확인하지 못했습니다. Azure Pricing Calculator 와
Databricks 가격 페이지로 최신 값을 확인하십시오.

### 엔드포인트가 `READY` 라는 것은 상류가 살아 있다는 뜻이 아닙니다

| 상황 | 엔드포인트 상태 | 조치 |
|---|---|---|
| 클러스터 **자동 종료 · 재시작** | `READY` 인데 호출은 전부 실패 | `/local_disk0` 이 비워집니다. venv·가중치·serve 를 다시 올리십시오(`DEPLOYMENT_GUIDE.md` §1~§5 + 이 문서 §2). **`cluster_id` 는 그대로이므로 엔드포인트 설정은 고칠 필요가 없습니다** — 2026-09-09 재시작 후 설정을 전혀 고치지 않고 호출이 성공함을 실측 |
| 클러스터 **삭제 후 재생성** | `READY` 인데 호출은 전부 실패 | `cluster_id` 가 바뀌므로 노트북 **셀 1 → 2 → 4** 를 다시 실행하십시오 |
| **PAT 만료** | `READY` 인데 호출은 전부 실패 | 만료 전에 새 토큰을 같은 secret 에 저장하십시오. 엔드포인트는 secret 을 참조하므로 재생성이 필요 없습니다 |
| vLLM 프로세스 **종료** | `READY` 인데 호출은 전부 실패 | §2 재실행 |

상시 서비스로 운영한다면 `/health` 를 주기적으로 확인하고 자동 복구를 준비하십시오.

### 엔드포인트 생성자는 변경할 수 없습니다

Databricks 는 엔드포인트 생성 시 **호출한 신원을 생성자로 기록하며, 이후 변경할 수 없습니다.**
그리고 설정 갱신 시 생성자의 워크스페이스 멤버십을 다시 확인하므로, **생성자가 워크스페이스를
떠나면 갱신이 `PERMISSION_DENIED` 로 실패**합니다. 클러스터를 재생성해 셀 4 를 다시 실행해야
하는 상황에서 이 문제를 만나면 삭제 후 재생성밖에 방법이 없습니다.

→ 장기 운영이라면 **팀이 소유한 서비스 프린시펄로 엔드포인트를 생성**하십시오.
노트북에서 실행하면 생성자는 실행한 사람이 됩니다.

### PAT 회전

- 사용자 PAT 는 만료 약 7일 전에 본인에게 메일이 갑니다. 서비스 프린시펄 토큰은 워크스페이스
  관리자에게 통보됩니다.
- **90일 이상 사용되지 않은 PAT 는 자동으로 폐기됩니다.** 예비 토큰을 미리 만들어 두는 방식은
  이 정책에 걸립니다.
- 교체는 **같은 secret 키에 새 값을 저장**하면 됩니다. 엔드포인트는 참조만 하므로 재생성이
  필요 없습니다.

### usage tracking 과 payload 로깅

| 기능 | 요구사항 | 이 프로젝트 실측 |
|---|---|---|
| `rate_limits` | 없음 | metastore 없이 **동작 확인** |
| `usage_tracking_config` | 설정은 metastore 없이 수락되지만, 기록은 Unity Catalog 시스템 테이블에 쌓입니다 | 설정 수락은 확인. **기록 조회는 확인하지 못했습니다** (검증 환경에 metastore 없음). 시스템 테이블 조회는 account 관리자 권한이 필요합니다 |
| `inference_table_config` (payload 로깅) | Unity Catalog metastore + 대상 스키마 `CREATE TABLE` 권한 | **미검증.** metastore 가 없어 설정 자체가 실패했습니다 |

---

## 8. 문제 해결

**먼저 알아둘 것**: 실패 응답의 최상위 `error_code` 는 실제 원인과 다를 수 있습니다.
진짜 원인은 **`external_model_error` 안에** 들어 있습니다.

```json
{"error_code":"INTERNAL_ERROR",
 "message":"{\"external_model_provider\":\"custom\",
             \"external_model_error\":\"<html>...502 Bad Gateway...\"}"}
```

| 증상 | 원인 | 조치 |
|---|---|---|
| **502** · `INTERNAL_ERROR` · 본문에 `502 Bad Gateway` | driver-proxy 가 상류에 접속하지 못함. vLLM 이 `127.0.0.1` 바인드이거나 죽었거나 포트가 다름 | `BIND_HOST=0.0.0.0` 으로 §2 재실행. 확인: 드라이버에서 `curl http://$(hostname -I \| cut -d' ' -f1):8005/health` |
| **404** · `ENDPOINT_NOT_FOUND` · 본문에 `The model ... does not exist` | 엔드포인트의 모델 이름과 vLLM 의 `--served-model-name` 불일치 | 노트북 셀 2 가 vLLM 에서 직접 읽으므로 셀 2→4 를 다시 실행 |
| **403** · `Invalid request` | secret 의 토큰이 노트북에서 얻은 토큰 | 노트북 밖에서 발급한 PAT 로 교체 (§3). 판별: `databricks tokens list` 에 보이는지 |
| **403** · `Invalid access token` | PAT 가 **폐기**됨 (실측) | 새 토큰을 같은 secret 에 저장. **폐기는 즉시 반영되지 않고 수분 지연**이 있습니다 |
| **403** · `does not have required scopes: clusters` | PAT 에 **`clusters` scope 가 없음** (실측). `ai-gateway` scope 를 고른 경우가 대표적입니다 | §3 「API Scope」 참조. `scopes:['clusters']` 로 재발급하십시오. `PATCH /api/2.0/token/<token_id>` 로 scope 만 고치는 경로도 있으나(전파 최대 10분) **이 프로젝트에서 실측하지 않았습니다** |
| **403** 또는 **401** | PAT **만료** | 새 토큰을 같은 secret 에 저장. 만료 시 어느 코드가 오는지는 미검증입니다 |
| 호출이 전부 **401** | vLLM 에 **`--api-key` 를 설정**함 | driver-proxy 가 `Authorization` 을 상류로 전달하지 않으므로 `--api-key` 를 제거하고 §2 로 재기동 |
| **401** (호출자 측) | `Authorization` 헤더 없음 | 호출자 토큰을 확인하십시오 |
| `tools` 요청이 **400** (`requires --enable-auto-tool-choice`) | tool calling 플래그 없이 기동 | `TOOL_CALL_PARSER=qwen3_xml` 로 §2 재실행 |
| 엔드포인트 생성 시 **400** (`please change to https`) | 상류 URL 이 `http://` | driver-proxy URL 사용. 노트북이 자동 구성합니다 |
| 엔드포인트 생성/갱신 **`PERMISSION_DENIED`** | 기록된 생성자가 워크스페이스 멤버가 아님 | §7 「엔드포인트 생성자」 참조 |
| `inference_table_config` 설정 실패 (`METASTORE_DOES_NOT_EXIST`) | Unity Catalog metastore 없음 | metastore 할당 후 설정. `usage_tracking_config` 와 `rate_limits` 는 영향 없음 |
| Playground 목록에 없음 | `task` 가 `llm/v1/chat` 이 아니거나 `READY` 가 아님 | `databricks serving-endpoints get <이름>` 으로 확인 |
| 노트북에서 `Failed to get token for subscription` 경고 | Azure 관리 ID 조회 경고 | **무해합니다.** 동작에 영향 없습니다 (노트북 UI 에서는 나타나지 않습니다) |
| 셀 6 아래에 MLflow Tracing · External Models 안내와 trace 위젯이 표시됨 | DBR ML 이 `from openai import OpenAI` 를 감지해 자동으로 끼워 넣는 안내입니다. MLflow OpenAI autolog 가 기본 활성입니다 | **무해합니다.** 다만 그 안내가 권하는 **`%pip install -U mlflow` 와 `dbutils.library.restartPython()` 은 실행하지 마십시오** — Python REPL 이 재시작되어 Run all 이 중단됩니다 |
| 셀 실행 후 이후 셀이 모두 실패 (`Py4JException`) | `%pip` 이 Python REPL 을 재시작 | 노트북은 `subprocess` 로 설치하므로 발생하지 않습니다. 직접 `%pip` 을 추가하지 마십시오 |

---

## 9. 부록 A · 측정 기준선

2026-09-09 재현 실측 (Azure koreacentral · A100 80GB · DBR 19.5 GPU ML · vLLM 0.28.0 ·
`--max-model-len 131072 --kv-cache-dtype fp8 --max-num-seqs 32`).

| 항목 | 값 |
|---|---|
| serve 기동 (`/health` 200 까지) | 약 5분 |
| GPU KV cache | 1,188,386 토큰 |
| 최대 동시성 (131,072 토큰/요청 기준) | 9.07x |
| Available KV cache memory | 39.06 GiB |
| 엔드포인트 신규 생성 → READY | 수십 초 |
| 엔드포인트 설정 갱신 → READY | 즉시 |
| 기본 호출 지연 | 1~2초 (짧은 프롬프트) |
| 추론 필드 | `reasoning` 263자 · `reasoning_tokens` 121 (산술 프롬프트, `max_tokens=900`) |

> **KV cache 값은 실행마다 조금씩 달라집니다.** 같은 플래그로 다시 띄운 결과가
> 1,188,386 토큰 / 9.07x 였고, 2026-09-07 실행에서는 1,240,814 토큰 / 9.47x 였습니다
> (약 4% 차이. 기동 시점의 `Available KV cache memory` 가 39.06 GiB 對 40.78 GiB 로 달랐습니다.
> 원인은 규명하지 않았습니다.) **정확히 일치하는지로 정상 여부를 판정하지 마십시오.**

vLLM 자체의 장문·동시성 수치는 `DEPLOYMENT_GUIDE.md` §4·§5 를 참조하십시오. 다만 그 수치는
**vLLM 에 직접 호출한 결과**이며, 게이트웨이 경유 수치는 §5 「운영 투입 전에 추가로 하십시오」의
항목으로 직접 측정하십시오.

---

## 10. 부록 B · 검증한 것과 검증하지 못한 것

고객 환경에서 다르게 동작할 수 있는 부분을 정직하게 구분합니다.

### 실측으로 확인한 것

외부 클라이언트 → 엔드포인트 → AI Gateway → driver-proxy → vLLM 전 구간 · `provider: custom`
요청 본문 무변형 · secret 참조 동작 및 조회 시 참조 문자열 그대로 반환 · SSE 스트리밍 ·
OpenAI SDK 호환 · `reasoning` 필드 전달 · `<think>` 미유출 · tool calling · 상류 오류 메시지
전달 · rate limit 429 발동 · AI Playground 노출 및 대화 · **신규 생성과 기존 갱신 두 경로** ·
**502 · 404 의 실제 응답 코드와 본문** · **클러스터 재시작 후 `cluster_id` 불변으로 엔드포인트
무수정 동작** · **자동 종료가 상류를 죽이며 엔드포인트는 `READY` 유지** · 새 secret scope 의
기본 권한이 생성자 한정.

### Databricks 문서에 근거한 것 (이 프로젝트에서 측정하지 않음)

아래는 플랫폼의 문서화된 동작이며, 이 프로젝트가 직접 재현한 것은 아닙니다.

- 엔드포인트 **생성자는 변경할 수 없고**, 생성자가 워크스페이스를 떠나면 설정 갱신이
  `PERMISSION_DENIED` 로 실패한다 (§7)
- 엔드포인트 이름은 `databricks-` 로 시작할 수 없다 (§4)
- **90일 이상 사용되지 않은 PAT 는 자동 폐기**되고, 만료 약 7일 전에 알림이 발송된다 (§7)
- 자동 종료 기준은 "마지막 **명령 실행** 시각" 이다 (§7)
- External models 의 리전별 지원 여부, usage tracking 의 Unity Catalog 요건 (§0.1, §7)

### 검증하지 못한 것 — 고객 환경에서 확인이 필요합니다

| 항목 | 이유 |
|---|---|
| Private Link · public access 차단 워크스페이스에서의 도달성 | 검증 환경은 공개 접근 가능. **§0.2 사전 시험으로 반드시 확인하십시오** |
| serverless egress 제한 정책 하에서의 동작 | 해당 정책이 없는 환경에서 검증 |
| 서비스 프린시펄 PAT 로 driver-proxy 통과 | 사용자 PAT 로만 검증 |
| driver-proxy 에 필요한 최소 클러스터 권한 | 워크스페이스 관리자 권한으로만 검증 |
| Unity Catalog Volume 배포 경로 · payload 로깅 · usage 기록 조회 | 검증 환경에 metastore 없음 |
| Azure Key Vault 백엔드 secret scope | `DATABRICKS` 백엔드로만 검증 |
| PAT 만료 시점의 거동 | 만료시키지 않았음 |
| 추론 트래픽만으로 자동 종료가 발동하는지 | 문서 문구와 종료 실측에 근거한 판단 |
| AWS · GCP | Azure 에서만 검증 |
| 8시간 초과 연속 운전 | 8시간 soak 까지 확인 |

---

## 참고 문서

전과정은 **두 문서로 이어집니다.** 이 문서는 그 두 번째입니다.

```
DEPLOYMENT_GUIDE.md §1~§5          이 문서 (§0~§8)
클러스터 → 배포 → venv·가중치   →   전제 점검 → PAT → 엔드포인트
→ vLLM serve → 검증                → 검증 → 사용 → 운영
   (07_deploy_notebook.py)            (notebook_gateway_register.py)
```

| 문서 | 내용 |
|---|---|
| `DEPLOYMENT_GUIDE.md` §1~§5 | 클러스터 · venv · 가중치 · serve · 성능 검증 (이 문서의 전제) |
| `DEPLOYMENT_GUIDE.md` §4.1 | **`--host` 결정 표** — `0.0.0.0` 을 선택했다면 이 문서 §2 를 건너뜁니다 |
| `DEPLOYMENT_GUIDE.md` §1.3 | **`autotermination_minutes`** — 게이트웨이 운영 시 `0` (생성 전에 결정) |
| `DEPLOYMENT_GUIDE.md` §7 | 배포 단계 문제 해결 |
| `notebook_gateway_register.py` | 이 문서의 실행 노트북 (셀 6개 · 외부 파일 의존 없음) |

