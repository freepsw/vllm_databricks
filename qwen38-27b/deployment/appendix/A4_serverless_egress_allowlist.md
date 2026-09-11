# 부록 A4 · serverless network policy 로 막힌 AI Gateway 구성 — 원인과 선택지

> ## ⛔ 먼저 읽으십시오 — 2026-09-10 확정 사실
>
> **워크스페이스 자신의 FQDN 은 `allowed_internet_destinations` 에 등록되지 않습니다.**
> 계정 콘솔·API 어느 쪽으로 시도해도 백엔드 검증이 아래 문구로 거부합니다.
>
> ```
> There was an error updating the network policy
> Workspace URL '<워크스페이스 FQDN>' not allowed as
> internet destination: `allowed_internet_destinations`.
> ```
>
> 오류가 **API 필드명을 그대로 노출**하므로 콘솔 UI 가드가 아니라 백엔드 validator 입니다
> — CLI·REST·Terraform 으로 우회할 수 없다고 판단합니다(우회 시도 자체는 미실시).
> **이 제약은 공식 문서에 없습니다.** 오히려 공식 문서는 "외부 모델 provider 의 DNS 를 허용
> 목적지로 추가하라" 고 안내하는데, **provider 가 자기 워크스페이스일 때 그 안내는 실행 불가**입니다.
> [등급: 실측 · 계정 콘솔 시도 + 다른 호스트에서 동일 문구 확인 다수]
>
> **따라서 아래 §4·§5 의 「워크스페이스 FQDN 을 허용 목록에 추가」 절차는 이 구성에서는
> 성공하지 않을 것으로 보십시오.** 아래 절차는 **다른 목적지(외부 provider 도메인)를 허용할 때의
> 참고 절차**로만 유효합니다.
>
> **그러면 무엇을 해야 합니까**
>
> | 방안 | 상태 | 대가 |
> |---|---|---|
> | **agent 를 Databricks Apps 에서 실행하고 driver-proxy 를 직접 호출** ([STEP4B](../STEP4B_apps_agent.md) 에 절차·앱 코드가 있습니다) | **실측(2026-09-10, 고객과 동일한 정책 구성)**. **egress 정책 단계는 통과했으나, driver-proxy 인증에서 거부됨**(dedicated cluster에서 두 자격 모두): ① 앱 SP OAuth 토큰 — HTTP 403 `PERMISSION_DENIED: Single-user check failed: user '<앱 SP>' attempted to run a command on single-user cluster <클러스터ID>, but the single user of this cluster is '<사용자>'` ② OBO 토큰(앱 런타임 전달) — HTTP 403 `{"error_code":403,"message":"Provided OAuth token does not have required scopes: clusters"}` · 이 scope 는 Databricks Apps 의 `user_api_scopes` 허용 목록에 없음(§4 근거) | **해법이 있습니다** — 클러스터 single user 의 **PAT 를 secret 앱 리소스로 주입**하면 통과합니다(**실측 · §10.2**: `/health` 200 · 실제 도구 호출 agent 루프 성공). **고객 정책이 강제된 상태에서 같은 창에 게이트웨이는 차단 · Apps 는 동작**을 나란히 확인했습니다(§10.2 결합 시험). 즉 이 경로는 **성립하지만 PAT 배포는 피할 수 없습니다.** 그 밖에 게이트웨이 기능(rate limit · usage tracking · Playground) 상실 · agent 를 Apps 에서 실행되는 형태로 옮기는 작업 |
> | **정책 enforcement 를 `Dry run mode for all products` 로 전환** | **실측으로 통과 확인**(2026-09-10, 3회 재현). 라디오 버튼 하나이며 **엔드포인트 재배포 없이 1분 내** 반영됐습니다 | 그 정책이 적용된 워크스페이스에서 **모든 제품의 egress 강제가 해제**됩니다(위반은 기록만 됨). 보안 검토·승인 필요 |
> | **게이트웨이를 쓰지 않고 driver-proxy 를 직접 호출**(호출 측이 classic 컴퓨트·외부 시스템) | **실측**. 이 경로에는 정책이 통제하는 serverless 구간이 없습니다 | rate limit · usage tracking · Playground · 엔드포인트 단위 권한 관리 상실. **PAT 를 호출 시스템에 배포**해야 합니다(위 Apps 방안은 앱의 서비스 프린시펄 토큰으로 되므로 PAT 가 필요 없습니다) |
> | 이 워크스페이스에만 **`Full access` 정책**을 따로 붙이기 | 문서 근거(미시험). 정책은 **워크스페이스 단위로 바인딩**되므로 다른 워크스페이스에 영향이 없습니다 | 그 워크스페이스의 egress 통제 포기 |
> | 관리형 Model Serving(custom model)으로 전환 | **미검증**. 자기 워크스페이스로 되돌아오는 hop 자체를 없앱니다 | 아키텍처·비용·기간 재산정. 컨테이너 빌드 단계 egress(`ML Build`)·빌드 시간 제약 확인 필요 |
> | vLLM 을 워크스페이스 밖(내부 LB + Private Link Service)으로 이설 | 문서 근거 — Private Link 로 등록된 도메인은 **암묵 허용**됩니다 | 인프라·운영 부담(LB·헬스프로브·private endpoint 과금) |
>
> **하지 말 것 — 이미 부정된 방안**
>
> | 방안 | 왜 안 되는가 |
> |---|---|
> | 자기 워크스페이스 ID 를 `allowed_databricks_destinations` 에 넣기 | **그 필드가 없습니다.** `egress.network_access` 의 필드는 `restriction_mode` · `allowed_internet_destinations` · `allowed_storage_destinations` · `blocked_internet_destinations` 네 개뿐이고, 계정 콘솔의 Egress 탭에도 그런 섹션이 없습니다(`Add destination` 대화상자 제목이 **"Add allowed internet destination"**). `cross_workspace_access` 는 **ingress** 블록 소속입니다 |
> | 제품별 dry-run(`Databricks SQL` · `AI model serving`)으로 우회 | **통과하지 않습니다**(실측 3회). external model 호출은 **`All products` 를 선택할 때만** dry-run 대상이 됩니다 |
> | AI Gateway V2 로 전환 | V2 도메인도 같은 검증에 거부됩니다 |
>
> **근거**: 위 판정은 Databricks 측 워크스페이스에서 정책만 바꿔 A/B/A 로 측정한 결과입니다 —
> `Full access` → 도달 · `Restricted+Enforced` → 거부 · `Restricted+전 제품 dry-run` → **통과** ·
> `Enforced` 복귀 → 거부 · `Restricted+제품별 dry-run` → 거부(상세 **§10.1**).
> **Apps 방안의 근거는 별개 실험입니다** — 고객과 동일한 정책 구성에서 앱이 driver-proxy 에 도달했고,
> 같은 측정 주기에 **미허용 도메인이 DNS 해석 실패**로 막히는 것을 대조군으로 함께 확인했습니다(상세 **§10.2**).

**이 문서를 여는 조건**: 서빙 엔드포인트를 호출했을 때
`CUSTOMER_UNAUTHORIZED … serverless network policy` 로 거부될 때만 필요합니다. 그 외의 경우에는
읽지 않아도 됩니다.

**대상 독자**: Azure Databricks **계정 관리자**(account admin) — 워크스페이스 관리자 권한으로는 할 수 없습니다  
**소요 시간**: 설정 약 5분 + 전파 약 10분(공식 문서 기준)  
**전제 조건**: 워크스페이스가 **Premium tier** ([요건][seg-req])  
**최종 확인일**: 2026-09-10

> ## 요약 — 허용 도메인 추가로는 해결되지 않습니다
>
> | 순서 | 누가 | 무엇을 | 시간 |
> |---|---|---|---|
> | 1 | **계정 관리자** | 이 워크스페이스에 적용된 정책과 `restriction_mode` 를 확인(**§3**) | 5분 |
> | 2 | **양측 합의** | 위 ⛔ 블록의 선택지 중 하나를 고른다(보안 검토가 필요한 항목이 있습니다) | — |
> | 3 | **계정 관리자** | 고른 방안을 적용(정책 편집 절차는 **§5**, 전파는 **§6**) | 5분 + 전파 |
> | 4 | **엔드포인트 담당자** | 엔드포인트를 다시 호출해 확인(**§6 검증 1**) | 1분 |
>
> **§4·§5 의 「Allowed domains 에 워크스페이스 FQDN 추가」 절차는 이 구성에서 실행할 수 없습니다**
> (백엔드 검증이 거부 — 위 ⛔ 블록). 두 절은 **다른 목적지(외부 provider 도메인·스토리지)를 허용할 때의
> 참고 절차**로만 유효하며, 정책 JSON 을 안전하게 편집하는 방법(전체 교체 함정·`jq` 레시피)은
> 선택지 적용에도 그대로 쓰입니다.
>
> **§0·§6 은 엔드포인트 담당자, §3~§5 는 계정 관리자**의 몫입니다. 계정 관리자에게는
> 워크스페이스 노트북·PAT·SQL 웨어하우스 권한이 없을 수 있습니다.

---

## 0. 이 문서를 여는 상황

**배경 한 문장**: 이 워크스페이스의 GPU 클러스터에서 도는 LLM 서버를 Databricks 서빙
엔드포인트로 감싸 쓰는 구성입니다. 그 엔드포인트가 **같은 워크스페이스의 주소로 다시 접속**해야
하는데, 그 접속이 serverless 방화벽 정책에 막힌 상태입니다.

AI Gateway 엔드포인트를 만들어 호출했을 때 아래 오류가 나오는 경우입니다.

```json
{"error_code":"CUSTOMER_UNAUTHORIZED",
 "message":"CUSTOMER_UNAUTHORIZED: Access to <워크스페이스 FQDN> is denied
             because of serverless network policy."}
```

vLLM · 토큰 · 클러스터 설정과는 **무관합니다.** 서빙 엔드포인트는 **serverless 컴퓨트**에서 동작하고,
그 컴퓨트의 외부 연결은 계정 수준의 **network policy**(serverless egress control)가 통제합니다.
정책이 **restricted access** 모드일 때 허용 목록에 없는 도메인으로는 나갈 수 없습니다.

**판별법** — 같은 노트북에서 아래 두 호출의 결과가 갈리면 이 문서의 상황입니다.

| 호출 | 결과 | 뜻 |
|---|---|---|
| 노트북(classic 클러스터)에서 `…/driver-proxy-api/…/health` | **200** | 워크스페이스 도메인 자체는 정상 |
| 엔드포인트 `…/serving-endpoints/<이름>/invocations` | **`CUSTOMER_UNAUTHORIZED`** | serverless 쪽 나가는 길만 막힘 |

Unity Catalog 시스템 테이블을 쓸 수 있으면 **한 줄로 확인**됩니다(§6 검증 2 에 전제·해석 있음).

```sql
SELECT * FROM system.access.outbound_network
WHERE network_source_type = 'external_model'
  AND event_time >= CURRENT_TIMESTAMP() - INTERVAL 24 HOUR
ORDER BY event_time DESC;
```

---

## 1. 먼저 채워 넣을 값

| 항목 | 어디서 확인 | 이번 값 |
|---|---|---|
| **워크스페이스 FQDN** | 워크스페이스 URL 의 호스트명 (`https://` 와 `/` 제외) | `<예: adb-0000000000000000.0.azuredatabricks.net>` |
| 계정 ID | 계정 콘솔 우상단 사용자 메뉴 | `<ACCOUNT_ID>` |
| 워크스페이스 ID | 호스트명의 **`adb-` 와 그 다음 `.` 사이 숫자** (예: `adb-1234567890123456.7…` → `1234567890123456`. 뒤의 `.7` 은 포함하지 않습니다) | `<WORKSPACE_ID>` |
| 적용 중인 정책 이름 | 아래 §3 에서 확인 | `<POLICY_NAME 또는 default-policy>` |

> 허용할 대상은 **고객사 자신의 워크스페이스 도메인**입니다. 외부 업체 도메인을 여는 것이 아닙니다.
>
> 이 문서를 전달하는 담당자는 **「이번 값」 열을 채워서** 보내십시오. 계정 관리자가 직접 찾지
> 않아도 되게 하면 왕복이 한 번 줄어듭니다.

**CLI 프로파일 표기** — 이 문서에는 두 종류가 나옵니다. 섞이면 인증 오류가 납니다.

| 표기 | 무엇 | 만드는 법 |
|---|---|---|
| `-p ACCOUNT` | **계정** 수준(정책 조회·변경) | §3 의 `databricks auth login --host https://accounts.azuredatabricks.net --account-id <ACCOUNT_ID> -p ACCOUNT` |
| `-p WS` | **워크스페이스** 수준(metastore·시스템 테이블 확인) | `databricks auth login --host https://<워크스페이스 FQDN> -p WS` |

---

## 2. 무엇이 막혀 있는가

```
외부 agent ─①→ 서빙 엔드포인트 (AI Gateway) ─②→ https://<워크스페이스 FQDN>/driver-proxy-api/... → vLLM
                        ↑ serverless 컴퓨트          ↑ 여기가 막혀 있습니다
```

(등록 가이드 §0.2 의 hop ①·② 와 같은 번호입니다.) **hop ②** 구간은 **serverless 컴퓨트가 워크스페이스 도메인으로 나가는 연결**이므로 network policy 의 통제를
받습니다. Databricks 공식 문서도 이 경우를 명시합니다([serverless egress control 개요][seg-overview]).

> Model Serving endpoints that call external models are also subject to serverless egress control.
> When a workspace uses restricted access, **add the external model provider's DNS names as allowed
> destinations in the network policy** so that the endpoint can reach the provider.

⚠️ **"같은 워크스페이스의 API 는 암묵적으로 허용된다"는 서술에 기대지 마십시오.** 문서에는 restricted
모드에서도 *workspace APIs of the same workspace* 는 접근 가능하다고 되어 있으나([보안 posture][seg-posture]), **external model 엔드포인트의 프록시
경로는 그 암묵 허용을 받지 못합니다**(2026-09-09 고객 환경 · 2026-09-10 Databricks 환경 실측 — 엔드포인트와 상류가
같은 워크스페이스인데도 거부됨). 그런데 **도메인을 명시적으로 추가하는 조치도 불가능**합니다(⛔ 블록).

> **정정(2026-09-10)** — egress 정책의 암묵 허용 차이는 **어느 제품이 호출하는가**로 갈리지만, **driver-proxy 인증 거부도 제품별로 다릅니다.** Apps 는 **네트워크 정책 단계는 통과** (HTTP 401 from `/api/2.0/clusters/list` = 워크스페이스 도달 확인)했으나 **driver-proxy 인증에서 거부**됐고, **external model 엔드포인트의 호출은 네트워크 정책 단계에서 거부**됐습니다(§10.2).
> 따라서 이 구성의 판단 기준은 "serverless 냐 classic 이냐" 가 **아니라** "어느 제품·어느 계층에서 막히는가" 입니다.

---

## 3. 현재 정책 확인 (변경 전)

### 계정 콘솔에서

공식 절차: [Accessing network policies][seg-access]

1. [계정 콘솔](https://accounts.azuredatabricks.net) 접속
2. **Security** 클릭
3. **Networking** 탭 클릭
4. **Policies** 아래 **Context-based ingress & egress control** 클릭
5. 목록에서 이 워크스페이스에 적용된 정책을 확인합니다. 워크스페이스에 별도 지정이 없으면
   **`default-policy`** 가 적용됩니다

### CLI 로 확인 (선택)

계정 수준 API 는 **OAuth 인증만 지원합니다. PAT 는 쓸 수 없습니다.**

```bash
databricks auth login --host https://accounts.azuredatabricks.net \
  --account-id <ACCOUNT_ID> -p ACCOUNT

# ① 이 워크스페이스에 어떤 정책이 붙어 있는지
#    (WORKSPACE_ID 는 위치 인자입니다 — --workspace-id 플래그는 없습니다)
databricks account workspace-network-configuration get-workspace-network-option-rpc \
  <WORKSPACE_ID> -p ACCOUNT

# ② 그 정책의 현재 내용
databricks account network-policies list-network-policies-rpc -p ACCOUNT -o json
```

①을 먼저 하십시오. ②만 보면 계정의 정책 목록만 나오고 **어느 것이 이 워크스페이스에
적용되는지는 알 수 없습니다.**

`restriction_mode` 가 `RESTRICTED_ACCESS` 면 이 문서의 조치가 필요하고, `FULL_ACCESS` 면
막힌 원인이 다른 곳입니다(§8 참조).

---

## 4. (참고) 계정 콘솔 UI 로 목적지를 추가하는 절차

> ⚠️ **이 절차로 워크스페이스 FQDN 을 추가할 수는 없습니다**(⛔ 블록). 외부 provider 도메인·스토리지 등
> **다른 목적지**를 허용할 때의 참고 절차입니다.

공식 절차: [정책 만들기][seg-create] · [egress 규칙 설정][seg-egress-rules] · [정책 수정][seg-update]

> **§4 와 §5 는 같은 일을 하는 두 가지 방법입니다. 하나만 하십시오.** UI 로 하실 수 있으면 §4 만
> 보고 §5 는 건너뛰십시오.

1. §3 의 경로로 **Context-based ingress & egress control** 화면을 엽니다
2. 이 워크스페이스에 적용된 정책 이름을 클릭합니다
3. **Egress** 탭을 선택합니다
4. 네트워크 접근 모드가 **Restricted access to specific destinations** 인 것을 확인합니다
   (**Allow access to all destinations** 이면 이 문서의 원인이 아닙니다)
5. **Allowed domains** 목록 위의 **Add destination** 을 클릭합니다
6. **워크스페이스 FQDN** 을 입력합니다 — `https://` 도, 끝의 `/` 도, 경로도 넣지 않습니다

   ```
   adb-0000000000000000.0.azuredatabricks.net
   ```

7. **Update** 를 클릭해 저장합니다

### 정책이 이 워크스페이스에 붙어 있는지 확인

공식 절차: [Associate a network policy to workspaces][seg-associate]

`default-policy` 를 고쳤다면 별도 지정이 없는 모든 워크스페이스에 자동 적용됩니다. 다른 정책을
쓰고 있다면 워크스페이스에 그 정책이 연결되어 있어야 합니다.

1. 계정 콘솔에서 해당 **워크스페이스**를 선택합니다
2. **Network Policy** 에서 **Update network policy** 클릭
3. 정책을 선택하고 **Apply policy** 클릭

---

## 5. (참고) CLI 또는 REST API 로 정책을 편집하는 절차

> 선택지 적용(전 제품 dry-run 전환 · 전용 Full access 정책 부착)에도 이 절의 **전체 교체 함정·`jq` 레시피**가
> 그대로 쓰입니다.

UI 를 쓸 수 없거나 변경 이력을 코드로 관리하는 경우입니다.

> ⚠️ **`PUT` 본문에는 정책 전체가 들어가야 합니다. 빠뜨린 필드는 삭제됩니다**
> (공식 문서: *The `PUT` body must contain the full network policy. Any fields you omit are
> cleared.* — [REST 절차][seg-block] · [스키마][seg-api])**.**
> 반드시 **① 현재 정책을 받아서 ② 그 위에 항목만 추가하고 ③ 되돌려 보내는** 순서로 하십시오.

### ① 현재 정책 내려받기

```bash
databricks account network-policies get-network-policy-rpc <POLICY_ID> \
  -p ACCOUNT -o json > policy.json
```

REST API 로도 같습니다.

```bash
curl -X GET \
  "https://accounts.azuredatabricks.net/api/2.0/accounts/<ACCOUNT_ID>/network-policies/<POLICY_ID>" \
  -H "Authorization: Bearer <OAUTH_TOKEN>" > policy.json
```

### ② `policy.json` 편집 — 항목 **하나만 추가**합니다

> ⚠️ **예시 JSON 을 통째로 붙여넣지 마십시오.** ①에서 내려받은 파일을 고치는 작업입니다.
> 통째로 바꾸면 두 가지 사고가 납니다.
> 1. **기존 허용 목록이 전부 사라집니다**(`PUT` 은 빠뜨린 필드를 삭제합니다)
> 2. `restriction_mode` 가 예시값으로 덮어써집니다 — `FULL_ACCESS` 였던 정책이
>    `RESTRICTED_ACCESS` 로 바뀌면 **그 정책을 쓰는 모든 워크스페이스의 serverless 워크로드가
>    차단**됩니다

추가할 것은 배열 항목 이 하나뿐입니다.

```json
{ "destination": "adb-0000000000000000.0.azuredatabricks.net",
  "internet_destination_type": "DNS_NAME" }
```

**`jq` 를 쓸 수 있으면 이 방법이 가장 안전합니다.** 다른 필드를 건드리지 않고 배열에만 덧붙이며,
배열이 아직 없는 정책에서도 새로 만들어 줍니다(두 경우 모두 실행 확인했습니다).

```bash
jq --arg d "워크스페이스FQDN" \
  '.egress.network_access.allowed_internet_destinations += [{"destination":$d,"internet_destination_type":"DNS_NAME"}]' \
  policy.json > policy_new.json

# 무엇이 바뀌었는지 눈으로 확인 (bash·zsh)
diff <(jq -S . policy.json) <(jq -S . policy_new.json)
```

`diff` 결과에 **추가한 항목만** 보여야 정상입니다. `restriction_mode` 나 다른 필드가 함께
바뀌었다면 그 파일을 보내지 마십시오.

손으로 편집하는 경우 **필드 위치는 ①에서 받은 JSON 을 그대로 따르십시오** — 계정·버전에 따라
`policy_enforcement` 가 `network_access` 안에 있는 형태와 `egress` 바로 아래에 있는 형태가
모두 관측됩니다. `internet_destination_type` 은 현재 **`DNS_NAME`** 만 지원합니다(IP 대역은 불가 — [스키마][seg-api]).

### ③ 되돌려 보내기

```bash
databricks account network-policies update-network-policy-rpc <POLICY_ID> \
  --json @policy_new.json -p ACCOUNT
```

손으로 편집했다면 파일 이름을 그에 맞게 바꾸십시오. 보내기 전 `diff` 로 한 번 더 확인하십시오.

---

## 6. 적용 확인

### 전파 시간

| 변경 내용 | 반영 |
|---|---|
| **허용 도메인 추가·삭제** | 자동으로 **약 10분** 내 전파 ([Apply network policy changes][seg-apply]) |
| 접근 모드(full ↔ restricted) 변경 · dry-run 모드 변경 | 공식 문서는 **엔드포인트 재배포 필요** ([Restart or redeploy][seg-restart]). **단 실측에서는 재배포 없이 1분 내 반영**됐습니다(2026-09-10) |
| 정책 **바인딩 교체**(워크스페이스에 다른 정책 붙이기) | **실측 24초** 내 반영(2026-09-10) |

문서의 "약 10분"은 **상한**으로 이해하십시오 — 실측에서는 바인딩 교체가 24초, dry-run 전환이 1분 내에
반영됐습니다. 다만 노트북·Spark 경로는 문서상 **최대 24시간** 지연 가능이라고 되어 있으므로,
결과가 음성이면 시간을 두고 한 번 더 확인하십시오.

⚠️ **dry-run 에 대한 정정(2026-09-10 실측)** — 제품별 dry-run(`Databricks SQL` · `AI model serving`)은
external model 호출을 **풀어주지 않습니다**(거부 유지). 반면 **`Dry run mode for all products` 를 선택하면
external model 호출이 통과합니다**(3회 재현). 공식 문서에는 상반된 두 서술이 함께 있는데
(§Policy enforcement 는 "All products 옵션만 external model 을 dry-run 으로 만든다",
§Check denial logs 는 "external model 은 dry-run 에서도 enforcement 가 계속된다"), **실측은 전자와 일치**합니다.

### 검증 1 — 엔드포인트 호출 (**엔드포인트 담당자**가 수행)

계정 관리자에게는 엔드포인트 PAT 가 없을 수 있으므로, 이 확인은 엔드포인트를 만든 담당자가
합니다. 등록 가이드의 **스모크 테스트 셀**을 다시 실행하거나 아래를 호출합니다.
**200** 이면 완료입니다.

```bash
curl -X POST "https://워크스페이스FQDN/serving-endpoints/엔드포인트이름/invocations" \
  -H "Authorization: Bearer dapi여기에실제PAT" -H 'Content-Type: application/json' \
  -d '{"max_tokens":1500,"messages":[{"role":"user","content":"1+1?"}]}'
```

> 자리표시자를 **그대로 실행하지 마십시오.** 토큰 자리에 `<PAT>` 같은 문자열이 그대로 들어가면
> `401 Credential was not sent or was of an unsupported type` 이 나옵니다(실제로 겪은 함정).
> Windows 에서 실행하실 경우의 주의사항은 [STEP0](../STEP0_gateway_precheck.md) 의
> 「Windows 에서 실행하실 경우」를 보십시오.

### 검증 2 — 거부 로그 (선택 · **워크스페이스 쪽 권한이 있는 담당자**가 수행)

거부는 Unity Catalog 시스템 테이블 **`system.access.outbound_network`** 에 기록됩니다
([Check denial logs][seg-logs]).
조치 전에는 이 워크스페이스의 거부 기록이 보이고, 조치 후에는 새 기록이 생기지 않아야 합니다.

**조회 전에 세 가지를 확인하십시오.** 하나라도 빠지면 결과가 비거나 테이블을 찾을 수 없습니다.

| # | 확인할 것 | 방법 |
|---|---|---|
| 1 | 워크스페이스가 **Unity Catalog metastore 에 연결**돼 있는가 | `databricks metastores current -p WS` — 연결이 없으면 시스템 테이블 자체가 없어 이 검증은 쓸 수 없습니다(검증 1 로 대체) |
| 2 | **`access` 시스템 스키마가 활성화**돼 있는가 | `databricks system-schemas list <METASTORE_ID> -p WS` 로 `access` 항목의 `state` 를 확인. 활성화되어 있지 않으면 **metastore 관리자**가 `databricks system-schemas enable <METASTORE_ID> access -p WS` ([시스템 테이블 활성화][dbx-systables]) |
| 3 | **조회 권한**이 있는가 | `system` 카탈로그의 `USE CATALOG` + `system.access` 의 `SELECT`. 보통 metastore 관리자가 부여합니다 |

쿼리는 SQL 편집기나 노트북에서 실행합니다(SQL 웨어하우스 필요). **가장 빠른 판별은 이 한 줄**
입니다 — 외부 모델 경로에서 막힌 기록만 뽑습니다.

```sql
SELECT * FROM system.access.outbound_network
WHERE network_source_type = 'external_model'
  AND event_time >= CURRENT_TIMESTAMP() - INTERVAL 24 HOUR
ORDER BY event_time DESC;
```

행이 나오면 그 행의 **목적지가 허용 목록에 넣을 도메인**이고, 그 기록이 이 엔드포인트의 것이라는
귀속까지 함께 확정됩니다(아래 판정 표).

- ⚠️ **`access_type` 을 `DROP` 하나로 걸러 보지 마십시오.** 관측된 값이 최소 세 가지입니다 —
  **`DENIED`** · **`DROP`** · **`DRY_RUN_DENIAL`**(dry-run 기록). 공식 문서는 `DROP` 과
  `DRY_RUN_DENIAL` 만 언급하지만, **실측에서 엔드포인트가 워크스페이스 FQDN 으로 나가지 못한
  기록은 `DENIED` 로 남았고 `DROP` 행에는 그 목적지가 없었습니다**(2026-09-09). `DROP` 만
  조회하면 정작 찾는 기록을 놓쳐 "차단이 없다"고 오판하게 됩니다
- **목적지로 거르고 `access_type` 은 결과에서 읽으십시오** (아래 쿼리)
- 빌드 단계에서 막힌 것은 `network_source_type` 이 **`ML Build`** 로 남습니다
- 기록이 나타나기까지 **지연이 있습니다.** 호출 직후 비어 있어도 잠시 후 다시 조회하십시오
- 컬럼 구성은 먼저 `SELECT *` 로 확인하십시오(계정·롤아웃에 따라 다를 수 있습니다)

#### 거부 목록으로 허용 목록을 확정하기 (권장)

한 번에 끝내려면 **막힌 목적지를 전부 뽑아** 계정 관리자에게 함께 전달하십시오. 컬럼은
`event_time` · `access_type` · `network_source_type` · `destination` · `destination_type` 이
실측으로 확인되었습니다(다르게 보이면 `DESCRIBE TABLE system.access.outbound_network;` 로 확인).

```sql
-- ① 우리 워크스페이스 FQDN 이 막힌 기록이 있는지 (access_type 으로 거르지 않습니다)
SELECT * FROM system.access.outbound_network
WHERE event_time >= CURRENT_TIMESTAMP() - INTERVAL 24 HOUR
  AND destination ILIKE '%<워크스페이스 FQDN>%'
ORDER BY event_time DESC;

-- ② 막힌 목적지 전체 목록 (허용 목록을 한 번에 확정하기 위해)
SELECT destination, access_type, network_source_type,
       COUNT(*) AS n, MIN(event_time) AS first_seen, MAX(event_time) AS last_seen
FROM system.access.outbound_network
WHERE event_time >= CURRENT_TIMESTAMP() - INTERVAL 24 HOUR
GROUP BY ALL
ORDER BY n DESC;
```

| 결과 | 뜻 |
|---|---|
| **워크스페이스 FQDN 하나만** | 원인이 이 구성 하나로 좁혀졌다는 뜻입니다. **그 FQDN 을 허용 목록에 넣는 것은 불가**하므로(⛔ 블록) 선택지 표로 가십시오 |
| 다른 도메인도 함께 | 그 도메인들도 필요한지 판단해 **한 번에** 허용 목록에 넣으십시오(정책당 FQDN 100개 한도) |
| `access_type` 에 `DRY_RUN_DENIAL` 이 섞임 | 그 제품이 dry-run 으로 설정돼 있다는 뜻이고, 그 항목은 **차단되지 않고 기록만** 된 것입니다. 단 **제품별 dry-run 은 external model 호출을 풀어주지 않으므로**(2026-09-10 실측) 이 구성의 거부는 그대로 남습니다 — `All products` 로 설정된 경우에만 external model 이 dry-run 대상이 됩니다 |
| `network_source_type` 이 `ML Build` | 모델 서빙 **컨테이너 빌드** 단계의 거부입니다. 이 구성(external model)은 빌드가 없으므로, 다른 엔드포인트의 기록입니다 |
| **`network_source_type` 이 `external_model`** (실측) | **이 구성의 거부가 바로 이것입니다.** 값 자체가 외부 모델 서빙 경로를 가리키므로 **다른 확인 없이 귀속이 확정**됩니다. 그 행의 목적지가 허용 목록에 넣을 도메인입니다(실측: `access_type=DENIED` · `destination_type=DNS` · 목적지 = 워크스페이스 FQDN) |
| `network_source_type` 이 `General Compute` (실측) | 노트북·잡 등 serverless 일반 컴퓨트의 거부입니다. **이 구성과는 별개 사안**이므로, 목적지 목록을 확인해 함께 처리할지 판단하십시오 |

> **`external_model` 행이 보이지 않을 때의 대체 확인법**: 현재 시각을 적어 두고 **엔드포인트를 한 번 호출**한 뒤
> (거부되는 것을 확인) 몇 분 기다려 그 시각 이후의 기록을 조회하십시오. 목적지가 워크스페이스
> FQDN 인 행이 새로 생기면 그 기록은 이 엔드포인트의 것입니다.
>
> ```sql
> SELECT * FROM system.access.outbound_network
> WHERE event_time >= TIMESTAMP '<호출 직전 시각 UTC>'
>   AND destination ILIKE '%<워크스페이스 FQDN>%'
> ORDER BY event_time DESC;
> ```
>
> `access_type` 을 조건에 넣지 마십시오 — 그 값이 `DENIED` 인지 `DROP` 인지는 결과에서 확인합니다.
>
> 참고: 로그의 `destination_type` 은 **`DNS`**, 정책에 넣을 때의 필드값은
> **`DNS_NAME`**(`internet_destination_type`)입니다. 같은 것을 다르게 표기합니다.

**결과가 비어 있을 때** — 아래를 순서대로 확인하면 "차단이 없었다"와 "로그를 못 보고 있다"를
구분할 수 있습니다.

| 원인 | 확인 |
|---|---|
| **`access_type` 을 `DROP` 으로만 걸렀다** (실측된 함정) | 조건에서 `access_type` 을 빼고 **목적지로** 거르십시오. 이 케이스의 기록은 `DENIED` 였습니다 |
| 로그 지연 | 몇 분 뒤 다시 조회 |
| 조회 구간이 짧음 | `INTERVAL 24 HOUR` → `INTERVAL 7 DAY` 로 넓히기 |
| `access` 스키마 미활성 · 권한 없음 | 위 전제 표의 2·3번 |
| metastore 미연결 | 위 전제 표의 1번 — 이 경우 검증 1(엔드포인트 호출)만으로 판단 |
| 테이블이 아직 제공되지 않음 | `TABLE_OR_VIEW_NOT_FOUND` 가 나면 Databricks 담당자에게 확인 |
| 정말로 차단이 없었음 | 검증 1 이 200 이면 이미 해결된 상태입니다 |

---

## 7. 보안 검토에 필요한 사실

승인 절차에서 물어볼 항목들을 미리 정리한 것입니다.

| 항목 | 사실 |
|---|---|
| 무엇을 여는가 | ⚠️ **이 행과 다음 행은 「FQDN 1개 추가」가 가능하다는 전제로 작성됐습니다 — 그 조치는 불가합니다.** 실제 보안 검토 대상은 ⛔ 블록의 선택지(전 제품 dry-run · driver-proxy 직접 호출 · 전용 Full access 정책 등)이며, 각 항목의 대가는 그 표에 있습니다 |
| 트래픽 방향 | serverless 컴퓨트 → 자사 워크스페이스(HTTPS). 인터넷 전체 개방이 아닙니다 |
| FQDN 필터의 성질 | 공식 문서: *The FQDN filter allows access to all domains that share the same IP address.* — **같은 IP 를 공유하는 도메인까지 함께 허용**됩니다 ([출처][seg-egress-rules]) |
| 한도 | 정책당 허용 FQDN **100개**, 전체 목적지 **2,500개** ([출처][seg-egress-rules]) |
| dry-run 으로 먼저 시험 | **제품별 dry-run 은 도움이 되지 않습니다**(external model 은 계속 차단 — 2026-09-10 실측). **`All products` dry-run 은 통과시킵니다**(실측 3회). 즉 dry-run 은 "시험"이 아니라 **강제 해제 스위치**로 작동하므로 보안 승인이 필요합니다 ([참고][seg-enforcement]) |
| 감사 추적 | 거부가 `system.access.outbound_network` 에 기록됩니다. 단 **적재에 지연**이 있고, 공식 문서 기준 **120초 미만으로 끝나는 단기 워크로드의 로그는 유실될 수 있습니다**(차단 자체는 그대로 적용됩니다 — [제약][seg-limits]) |
| 되돌리기 | 허용 목록에서 항목을 삭제하면 원상복구됩니다(약 10분 전파) |

---

## 8. 그래도 안 될 때

| 확인할 것 | 방법 | 조치 |
|---|---|---|
| 정책이 이 워크스페이스에 적용됐나 | 계정 콘솔에서 워크스페이스의 **Network Policy** 확인 | §4 의 「워크스페이스에 붙어 있는지 확인」 |
| 아직 전파 중인가 | 변경 후 10분 경과 여부 | 기다리십시오(재배포가 전파를 앞당기는지는 문서에 명시 없음) |
| 워크스페이스가 Premium 인가 | 계정 콘솔의 워크스페이스 정보 | Premium 이 아니면 이 기능을 쓸 수 없습니다 |
| 엔드포인트 종류 | 서빙 엔드포인트 상세 | **provisioned throughput 엔드포인트는 FQDN 단위 필터를 지원하지 않습니다** — restricted 모드에서 인터넷 접근이 전부 차단됩니다([출처][seg-egress-rules]). 이 구성은 **external model** 엔드포인트여야 합니다 |
| 도메인 표기 | 허용 목록의 값 | `https://`·경로·끝 슬래시가 없어야 합니다 |
| 다른 원인인가 | 오류 코드 확인 | `CUSTOMER_UNAUTHORIZED` 가 아니라 **502**·**404**·**403**·**400** 이면 네트워크 정책 문제가 아닙니다. 등록 가이드의 문제 해결 절을 보십시오 |

---

## 9. 선택지 상세 — 게이트웨이를 쓰지 않는 방안

**선택지 전체 목록과 실측 등급은 최상단 ⛔ 블록에 있습니다**(전 제품 dry-run 전환 · driver-proxy 직접 호출 ·
전용 Full access 정책 · 관리형 서빙 · 워크스페이스 밖 이설). 이 절은 그중 **정책을 바꾸지 않는 두 방안**의
상세입니다.

| 대안 | 내용 | 잃는 것 |
|---|---|---|
| **게이트웨이를 쓰지 않음** | 고객 agent 가 `…/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/…` 를 **직접** 호출합니다. 이 경로에는 정책이 통제하는 serverless 구간이 없습니다 | rate limit · usage tracking · AI Playground · 엔드포인트 단위 권한 관리. PAT 를 agent 쪽에 배포해야 합니다 |
| **agent 를 Databricks Apps 에서 실행**(위 방안의 변형 · **실측**) | 같은 driver-proxy URL 을 호출하되 호출 주체가 Apps 입니다. **egress 정책은 통과하지만 driver-proxy 인증은 신원을 가립니다** — 앱 SP 토큰과 사용자 OBO 토큰은 **둘 다 403**, **클러스터 single user 의 PAT 를 secret 앱 리소스로 주입한 경우에만 통과**했습니다(§10.2) | 게이트웨이 기능은 동일하게 잃습니다. agent 를 Apps 로 옮기는 작업. **PAT 를 앱에 배포**해야 하며(secret 리소스 사용), 앱 URL 로의 사용자 접속은 실측 확인됨(SSO) |
| **관리형 서빙으로 전환** | 가중치를 Model Serving 의 custom model 로 올려 자기 워크스페이스로 되돌아오는 구간 자체를 없앱니다 | 아키텍처·비용·기간 재산정 |

어느 대안이든 가능 여부는 **agent 가 실제로 실행되는 위치에서 driver-proxy 를 호출해** 판단합니다
([STEP0 §2 시험 A](../STEP0_gateway_precheck.md)). 호출 위치가 바뀌면 판정도 바뀌므로, **옮길 위치에서 다시 측정**하십시오.

---

## 10. 실측 근거 (2026-09-10)

이 문서의 판정은 **Databricks 측 워크스페이스에서 정책만 바꿔 측정**한 결과입니다. 고객 환경을
건드리지 않았고, 모든 변경은 측정 후 원복했습니다. 상류 vLLM 은 **종료 상태로 두었습니다** — 아래 두 응답이
서로 배타적이어서 "정책에 막혔는가"와 "도달했는가"의 판별이 가능합니다.

### 10.1 게이트웨이 경로(external model 엔드포인트) — 정책 A/B/A

| 응답 | 뜻 |
|---|---|
| `CUSTOMER_UNAUTHORIZED … denied because of serverless network policy` | **정책이 차단** — 워크스페이스에 도달하지 못함 |
| `{"external_model_provider":"custom","external_model_error":"INVALID_STATE: Cluster … Terminated"}` | **도달함** — driver-proxy 가 응답한 애플리케이션 오류(정책은 통과) |

| 정책 상태 | 결과 | 횟수 |
|---|---|---|
| `Full access`(기준선) | 도달 | 1 |
| `Restricted` + `Enforced` | **거부** | 2 |
| `Restricted` + **`Dry run mode for all products`** | **통과** | 3 |
| `Enforced` 복귀(대조군) | 거부 | 1 |
| `Restricted` + 제품별 dry-run(`Databricks SQL`·`AI model serving`) | **거부** | 3 |
| `Full access` 원복(대조군) | 도달 | 1 |

부수적으로 확인된 사실.

- **FQDN 등록 거부는 백엔드 검증**입니다 — CLI/REST 로 시도해도 계정 콘솔과 **같은 문자열**로 거부됩니다
- **`policy_enforcement` 의 위치는 `egress.network_access` 안**입니다(`egress` 바로 아래에 두면 무시됩니다)
- 정책 **바인딩은 워크스페이스 단위**이므로, 한 워크스페이스에만 다른 정책을 붙여도 나머지는 영향이 없습니다
- 이 실험 환경에는 Unity Catalog metastore 가 없어 **거부 로그(`system.access.outbound_network`) 재현은 하지 않았습니다** —
  §6 검증 2 의 값(`access_type=DENIED` · `network_source_type=external_model`)은 **고객 환경에서 한 번 관측된 값**입니다

**이 실험이 답하지 못한 것**: vLLM 이 살아 있는 상태의 200 종단 확인(정책 판별에는 불필요했습니다) ·
Databricks 내부 플래그로 우회하는 방법의 적용 가능성 · AWS/GCP·다른 계정에서의 동일성.

### 10.2 Databricks Apps 경로 — **네트워크 단계 통과 · driver-proxy 인증 거부**

같은 driver-proxy URL 을 **Databricks Apps 에서 실행되는 앱**이 호출하도록 바꿔 측정했습니다. 정책은
고객 설정을 그대로 재현했습니다 — `Restricted` + 제품별 dry-run `Databricks SQL`·`AI model serving`.
**Apps 는 그 필터에 없으므로 `All other products` = `Enforced`** 입니다. 즉 **Apps 에는 강제가 걸린 상태**였습니다.
**클러스터는 기동 상태(vLLM serving 실행 중)였습니다.**

| 목적지 | 결과 | 이 측정에서의 역할 |
|---|---|---|
| `example.com`(허용 목록에 없음) | **DNS 이름 해석 실패**(`Temporary failure in name resolution`) | **음성 대조군 — 정책이 실제로 강제되고 있음을 증명** |
| `pypi.org`(허용 목록에 있음) | HTTP 200 | 양성 대조군 — 허용 목록이 동작함 |
| `/api/2.0/clusters/list`(같은 워크스페이스) | HTTP 401 | 워크스페이스에 **도달**(인증 헤더 없이 호출) |
| **`…/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/health`** (앱 SP OAuth `client_credentials`) | **HTTP 403** · 본문 `{"error_code":"PERMISSION_DENIED","message":"Single-user check failed: user '<앱 SP>' attempted to run a command on single-user cluster <클러스터ID>, but the single user of this cluster is '<사용자>'"}` | **driver-proxy 인증 거부** — dedicated cluster 단일 사용자 정책 |
| **`…/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/health`** (OBO 토큰 · `x-forwarded-access-token` 헤더) | **HTTP 403** · 본문 `{"error_code":403,"message":"Provided OAuth token does not have required scopes: clusters"}` | **driver-proxy 인증 거부** — `clusters` scope 허용되지 않음(`user_api_scopes` 에 미지원) |

⚠️ **함정 — 같은 날 오전 측정의 오독**: 클러스터가 **Terminated** 상태였을 때 HTTP 400 `INVALID_STATE: Cluster … is in Terminated state` 이 나왔고, 이를 "경로가 열려 있다"고 오판했습니다. 실제로는 **클러스터 상태 검사가 인증 검사보다 먼저 실행되므로 400 은 진짜 인증 문제(403)를 가리고 있었습니다.** 클러스터를 기동한 후(vLLM serving) 같은 호출을 반복하면 **진짜 인증 오류 403** 이 나옵니다.

**네트워크 정책과 driver-proxy 인증은 분리된 계층입니다:**
- **네트워크 정책 단계** (egress): Apps 는 통과 (DNS 해석 성공 · 워크스페이스 도달 확인)
- **driver-proxy 인증 단계**: Apps 도 거부됨 (dedicated cluster 단일 사용자 + `clusters` scope 불가)

#### 통과한 신원 — 같은 URL 을 세 신원으로 호출한 결과

| 신원 | driver-proxy `/health` | 판정 |
|---|---|---|
| 앱 SP OAuth (`client_credentials`, `all-apis`) | **403** `Single-user check failed …` | 전용 클러스터의 **신원 검사**에서 거부 |
| 사용자 OBO (`x-forwarded-access-token`) — SCIM `Me` 로 **클러스터 single user 본인임을 확인** | **403** `… does not have required scopes: clusters` | **스코프 검사**에서 거부. `clusters` 는 Apps `user_api_scopes` 에 **없는 값**이며(API 가 `not a valid scope` 로 거부) 워크스페이스 허용목록은 `["*"]` 였습니다 — 즉 관리자 설정으로 열 수 없습니다 |
| **클러스터 single user 의 PAT**(Databricks secret → 앱 `secret` 리소스로 주입) | **200** (45 ms) | **통과 — 실측으로 동작한 유일한 신원** |

그 PAT 신원으로 **agent 종단까지 실측**했습니다(vLLM 기동 · `qwen38-27b` · 131,072).

| 시험 | 결과 |
|---|---|
| `/health` · `/v1/models` | 200 · 200 (모델·컨텍스트 한도 반환) |
| 단발 대화(`/v1/chat/completions`) | **200 · 3.9초** · 한국어 정답 반환 |
| **도구 호출 agent 루프** | **성공 · 2 스텝 · 8.8초** — 1스텝에서 도구 2개(`get_cluster_state`·`get_gpu_memory`)를 호출하고, 도구 결과를 받아 2스텝(`finish_reason: stop`)에서 최종 답변 생성 |
| 음성 대조군(`/nonexistent-xyz`) | **404** `{"detail":"Not Found"}` — 앞단이 무조건 200 을 주는 것이 아니라 **요청이 vLLM 까지 전달**됨을 확인 |
| 사용자 브라우저 접속 | 앱 URL 을 브라우저로 열어 SSO 통과 후 화면에서 agent 실행 성공 |

**정리** — Apps 경로는 **성립합니다.** 단 두 가지를 받아들여야 합니다.

1. **PAT 배포가 필요합니다**(앱 SP 토큰으로는 전용 클러스터의 driver-proxy 를 호출할 수 없습니다). secret 으로 주입하고 앱 SP 에 `READ` 만 부여하십시오  
2. 도구 호출용 워크스페이스 API 는 **앱 SP 신원**으로 분리할 수 있습니다(실측 구성이 그렇습니다) — driver-proxy 만 PAT 를 씁니다

#### 결합 시험 — **고객 정책이 강제된 상태에서 그대로 동작했습니다** (14:33~14:44Z)

정책(`RESTRICTED_ACCESS` + dry-run `["DBSQL","ML_SERVING"]`)을 이 워크스페이스에만 붙이고
(`default-policy` 는 건드리지 않음) 앱을 재시작한 뒤, **강제가 실제로 걸린 것을 음성 대조군으로 확인한 다음**
같은 창에서 두 경로를 나란히 측정했습니다.

| 시각(Z) | 사건 |
|---|---|
| 14:33:17 | 정책 부착 |
| 14:33:44 | 앱 재시작 |
| **14:36:46** | **게이트웨이(external model) 경로 차단** — `CUSTOMER_UNAUTHORIZED` (부착 후 **3분 29초**) |
| **14:40:57** | **Apps 계층 강제 확인** — 앱에서 미허용 도메인 DNS 해석 실패(부착 후 **7분 40초**) |
| 14:43 | 게이트웨이 차단 재확인(강제 확인 창 안) |
| 14:44 | 브라우저에서 앱 agent 실행 |

| 경로 | 결과(강제 상태) |
|---|---|
| **AI Gateway external model → driver-proxy** | **차단** `CUSTOMER_UNAUTHORIZED … serverless network policy` (2회) |
| 앱에서 허용 도메인(`pypi.org`) | 200 (양성 대조군) |
| 앱에서 미허용 도메인(`example.com`) | **DNS 해석 실패**(음성 대조군 = 강제 증명) |
| **앱 → driver-proxy(single user PAT)** `/health` · `/v1/models` | **200** (94ms) · **200** |
| 앱 단발 대화 | **200 · 3.8초** · 한국어 정답 |
| **앱 도구 호출 agent 루프** | **성공 · 2스텝 · 7.1초** (도구 2개 호출 → `finish_reason: stop`) |
| 앱 음성 대조군 `/nonexistent-xyz` | **404** `{"detail":"Not Found"}` |
| 브라우저(사용자 신원 SSO) 화면 실행 | **성공 · 2스텝 · 6.7초** · 같은 호출에서 대조군 차단 동시 확인 |

즉 **고객 정책을 하나도 바꾸지 않은 상태에서 게이트웨이는 막히고 Apps 경로는 동작합니다.**
측정 후 정책 바인딩은 `default-policy` 로 원복하고 시험 정책은 삭제했습니다(조회 시 `not found` 확인).

**미검증**: `single_user_name` 을 앱 SP 로 지정하면 PAT 없이 통과하는지(클러스터 재시작 필요) ·
공유·NO_ISOLATION 접근 모드에서의 통과 여부(보안 통제 변경 필요) ·
restricted 정책에서 `*.databricksapps.com` 을 허용목록에 넣을 수 없는 문제의 장기 영향(이번 시험 중 앱 접속은 정상이었습니다).

부수적으로 확인된 사실.

- **PAT 가 필요 없습니다** — 앱의 **서비스 프린시펄 OAuth 토큰**(`client_credentials`)이 driver-proxy 에서 그대로 수용됐습니다.
  그 서비스 프린시펄에 클러스터 조회 권한을 부여하십시오
- **전파에 시간이 걸립니다** — 정책 부착 후 **앱 재시작 + 약 15분** 뒤에야 강제가 관측됐습니다.
  부착 3분 뒤 측정에서는 `example.com` 이 **200 으로 통과**해 **거짓 통과**가 나왔습니다.  
  ⚠️ **허용되지 않은 도메인을 대조군으로 함께 호출하지 않으면 오판합니다**
- **문서 결함** — 공식 Apps 네트워킹 문서는 restricted 정책에서 `*.databricksapps.com` 허용을 요구하지만,
  **API 는 와일드카드를 `not a valid hostname` 으로 거부**합니다. 문서대로 구성할 수 없습니다

**이 실험이 답하지 못한 것**: vLLM 기동 상태의 200 종단 확인 · **앱 URL 로의 사용자 브라우저 접속**
(앱은 restricted 상태에서 배포·기동·정상 동작했으나, 이 측정은 앱 URL 접속 없이 워크스페이스 파일로 결과를
회수했습니다. 바로 위 문서 결함 때문에 이 관문은 별도 확인이 필요합니다) · 고객 계정·리전에서의 동일성.

---

## 참고 문서

- serverless egress control 개요 —
  https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies
- network policy 관리(UI 절차·API·전파·검증·거부 로그) —
  https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies
- API 스키마 —
  https://docs.databricks.com/api/azure/account/networkpolicies/updatenetworkpolicyrpc
- 이 구성의 사전 시험과 선택지 — [STEP0 게이트웨이 도달성 사전 시험](../STEP0_gateway_precheck.md)
- 증상별 문제 해결 — [부록 A2 문제 해결](A2_troubleshooting.md)

**다음 단계**: 서빙 엔드포인트 호출이 200 을 반환하면 [STEP5 검증](../STEP5_test.md) 으로
넘어가십시오.

<!-- 본문에서 참조하는 공식 문서 링크 (앵커 존재 확인일 2026-09-09) -->

[seg-overview]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies
[seg-posture]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies#security-posture
[seg-req]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#requirements
[seg-access]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#accessing-network-policies
[seg-create]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#create-a-network-policy
[seg-egress-rules]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#set-egress-rules
[seg-enforcement]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#policy-enforcement
[seg-block]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#block-internet-destinations
[seg-update]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#update-a-network-policy
[seg-associate]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#associate-a-network-policy-to-workspaces
[seg-apply]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#apply-network-policy-changes
[seg-restart]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#restart-or-redeploy-serverless-workloads
[seg-logs]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#check-denial-logs
[seg-limits]: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies#limitations
[seg-api]: https://docs.databricks.com/api/azure/account/networkpolicies/updatenetworkpolicyrpc
[dbx-systables]: https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/#enable
