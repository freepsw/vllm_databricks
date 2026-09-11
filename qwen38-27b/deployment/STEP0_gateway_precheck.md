# STEP 0 · 게이트웨이 도달성 사전 시험 (조건부 · 5분)

## 도입부

이 단계에서 하는 일: 게이트웨이 엔드포인트를 만들기 전에, 워크스페이스 네트워크가 그 구성을 허용하는지 판별합니다.  
**언제 합니까**: AI Gateway 엔드포인트로 외부에 노출할 계획일 때만. driver-proxy 를 직접 호출하거나 로컬 데모만 할 것이면 이 단계를 건너뜁니다.  
**왜 지금 합니까**: 여기서 막히면 뒷단계(가중치 다운로드 약 30 GB · venv 빌드 · vLLM 기동)가 전부 헛수고입니다.  
**필요한 것**: 아무 클러스터(GPU 아니어도 됩니다) · 노트북 밖에서 발급한 PAT.  
**끝났는지 판단 기준**: 시험 A 에서 404 응답 확인 + 시험 B 에서 200 또는 `CUSTOMER_UNAUTHORIZED` 중 하나 확인.

> **신규 워크스페이스라면** 첫 클러스터 생성이 `INVALID_WORKER_ENVIRONMENT` 로 실패할 수 있습니다.
> 워크스페이스 초기화가 아직 끝나지 않아서이며, **같은 클러스터를 `databricks clusters start
> <CLUSTER_ID>` 로 다시 시도하면 통과**합니다. 새로 만들지 마십시오.

---

## 0.1 이 경로에는 성격이 다른 두 hop 이 있습니다

AI Gateway 가 드라이버의 vLLM 을 호출할 때 거치는 **두 단계의 네트워크 정책**이 있고, **막히는 것은 두 번째**입니다.

| hop | 누가 호출하나 | 지배하는 정책 |
|---|---|---|
| ① 클라이언트 → driver-proxy | 고객 PC · classic 노트북 | 워크스페이스 public network access · IP access list |
| ② **엔드포인트 → driver-proxy** | **AI Gateway = serverless 컴퓨트** | **계정 콘솔의 serverless network policy** |

⚠️ **①만 시험하면 거짓 통과가 됩니다.** 2026-09-09 고객 워크스페이스 실측: classic 노트북에서 driver-proxy 를 직접 호출하면 200, 그런데 같은 호스트를 향한 엔드포인트 호출은 아래로 거부됐습니다.

```json
{"error_code":"CUSTOMER_UNAUTHORIZED",
 "message":"CUSTOMER_UNAUTHORIZED: Access to <워크스페이스>.azuredatabricks.net is denied
           because of serverless network policy."}
```

이 오류는 vLLM · PAT · 바인드 주소와 **무관합니다.** 막혀 있으면 **§4 의 선택지**로 갑니다.

> **serverless 라고 다 막히는 것은 아닙니다(2026-09-10 실측).** hop ② 가 막히는 이유는 "serverless 이기 때문" 이
> 아니라 **어느 계층에서 막히는가에 따라 다릅니다.** external model 엔드포인트는 **egress 정책 단계에서 거부**되고,
> **Databricks Apps 에서의 호출은 egress 정책은 통과했으나(네트워크 도달 확인) driver-proxy 인증 단계에서 거부**됩니다
> ([부록 A4 §10.2](appendix/A4_serverless_egress_allowlist.md)) — dedicated cluster 단일 사용자 검사 · `clusters` scope 불지원.
> **다만 막힌 채 끝나지 않습니다** — 클러스터 single user 의 **PAT 를 secret 앱 리소스로 주입**하면 통과하고,
> 그 신원으로 **도구 호출 agent 루프까지 실측 성공**했습니다(부록 A4 §10.2).
> 판단 기준은 **어느 제품이 호출하는가에서 나아가 어느 계층에서 거부되는가** 입니다.

---

## 1. 워크스페이스 전제조건 5가지

로컬 터미널에서 실행합니다(`<PROFILE>` 은 CLI 프로파일 이름).

| # | 확인 명령 | 통과 기준 | 실패 시 |
|---|---|---|---|
| 1 | `databricks api get "/api/2.0/workspace-conf?keys=enableTokensConfig" --profile <PROFILE>` | `"true"` | **작업 불가.** PAT 가 비활성이면 상류 인증 수단이 없습니다 |
| 2 | `databricks api get "/api/2.0/workspace-conf?keys=maxTokenLifetimeDays" --profile <PROFILE>` | 값 확인 | 이 값보다 짧은 수명으로 PAT 를 발급하십시오 |
| 3 | `databricks current-user me --profile <PROFILE>` | 자기 정보가 정상 반환 | 인증·프로파일을 먼저 해결하십시오 |
| 4 | Azure 포털에서 워크스페이스 리소스의 **위치(Location)** 확인 | External models 지원 리전 | 미지원 리전이면 엔드포인트 생성 자체가 불가 (koreacentral · japaneast 등 일부 리전만 지원) |
| 5 | `databricks secrets list-scopes --profile <PROFILE>` | `backend_type` 확인 | Azure Key Vault 백엔드 scope 는 Databricks 쪽에서 값을 쓸 수 없을 가능성이 높습니다(미검증). `DATABRICKS` 백엔드로 새로 만드십시오 |

**1번이 가장 중요합니다.** 이 설계는 상류 인증에 **정적 PAT** 를 씁니다. 엔드포인트가 받아들이는 인증은 `bearer_token_auth` 와 `api_key_auth` 뿐이며, 둘 다 정적 값입니다. 워크스페이스가 PAT 를 전면 비활성화했다면 **이 방식은 성립하지 않습니다.**

---

## 2. 시험 A · 클라이언트 → driver-proxy (필요조건)

**이것만으로는 부족합니다.** 이것은 hop ①만 봅니다.

### 2.1 임시 HTTP 서버 띄우기

아무 클러스터의 노트북 셀에서 실행합니다.

```python
import subprocess
subprocess.Popen(["python3", "-m", "http.server", "8005"], start_new_session=True)
print("org  :", spark.conf.get("spark.databricks.clusterUsageTags.clusterOwnerOrgId"))
print("clu  :", spark.conf.get("spark.databricks.clusterUsageTags.clusterId"))
print("host :", spark.conf.get("spark.databricks.workspaceUrl"))
```

> ⚠️ 이 서버는 **인증이 없습니다.** 시험이 끝나면 반드시 정리하십시오.  
> 정리: `import subprocess; subprocess.run(["pkill","-f","http.server"])`  
> **포트가 이미 사용 중이면 다른 프로세스가 응답해 거짓 통과가 될 수 있습니다.** 응답 본문이 파일 목록인지 확인하십시오.

### 2.2 워크스페이스 밖에서 호출

고객 PC(노트북이 아닌 환경)에서 노트북 밖에서 발급한 PAT 로 호출합니다. **기대: 200.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/"
```

### 2.3 ⚠️ Windows 에서 실행하실 경우

- **`curl` 이 아니라 `curl.exe`** — PowerShell 에서 `curl` 은 `Invoke-WebRequest` 별칭이라 Unix 플래그가 매개변수로 해석되며 실행조차 되지 않습니다
- **`--ssl-revoke-best-effort` 필요** — 사내 TLS 검사 프록시 환경에서 Windows curl(schannel)이 `(35) CRYPT_E_NO_REVOCATION_CHECK` 로 실패합니다. 인증서가 잘못된 것이 아니라 파기 목록 서버에 닿지 못하는 것입니다. 이 옵션은 **파기 확인이 불가능할 때만** 그 검사를 건너뛰며 체인 검증은 유지합니다
- **`-s` 를 쓰지 마십시오** — 오류 메시지까지 숨겨서 원인을 알 수 없습니다. `-sS` 를 쓰거나 `-i` 로 상태줄을 보십시오
- `/dev/null` → `NUL`, `%{http_code}` 는 `%%{http_code}`(cmd 배치)
- 자리표시자를 그대로 실행하면 `401 Credential was not sent or was of an unsupported type` 이 됩니다. 꺾쇠는 cmd 리다이렉션 기호입니다

사내 루트 CA 를 런타임 신뢰 저장소에 등록하십시오 (`REQUESTS_CA_BUNDLE` · `SSL_CERT_FILE`).

### 2.4 음성 시험 — 없는 경로가 404 를 돌려주는지

**200 만 보고 통과로 판정하지 마십시오.** 앞단이 상류를 보지 않고 200 을 돌려주는 것이라면 그 200 은 아무것도 증명하지 않습니다.

```bash
curl -i -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/nonexistent-xyz"
```

| 없는 경로의 응답 | 판정 |
|---|---|
| **404** (임시 HTTP 서버면 HTML, vLLM 이면 `{"detail":"Not Found"}` = 22바이트) | 앞단이 상류로 실제 전달함 → 위의 200 은 진짜입니다 |
| 200 | 앞단이 무조건 200 을 줍니다 → `/v1/models` 의 **본문**으로 판정하십시오 |

---

## 3. 시험 B · serverless → driver-proxy (실제로 막히는 hop)

**serverless 컴퓨트에 붙인 별도 노트북**에서 임시 서버를 그대로 둔 채 실행합니다.

```python
import requests
PAT = "<노트북 밖에서 발급한 PAT>"          # API scope: clusters
URL = "https://<워크스페이스>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/"
r = requests.get(URL, headers={"Authorization": f"Bearer {PAT}"}, timeout=30)
print(r.status_code, r.text[:200])
```

| 결과 | 해석 |
|---|---|
| **200** (파일 목록) | serverless egress 가 이 FQDN 으로 열려 있습니다 → 진행하십시오 |
| `CUSTOMER_UNAUTHORIZED` · `serverless network policy` | **차단.** §4 로 갑니다 |
| 403 · `required scopes` | 네트워크가 아니라 PAT scope 문제입니다 |

### 3.1 ⚠️ dry-run 함정 — 외부 모델은 `All products` 일 때만 dry-run

egress 정책의 dry-run 은 제품별로 켤 수 있고 **외부 모델 호출은 `All products` 를 선택할 때만 dry-run 이 적용**됩니다. 노트북만 dry-run 이면 이 시험은 통과하고 엔드포인트는 계속 차단될 수 있습니다.

**2026-09-10 실측** — 제품별 dry-run(`Databricks SQL`+`AI model serving`)에서는 external model 호출이 **계속 거부**되고, `Dry run mode for all products` 로 바꾸면 **통과**합니다(각 3회 재현). 참고로 `All other products` 행은 **표시 전용**이며 dry run 으로 바꿀 수 없습니다 — 필터 enum 에 `DBSQL`·`ML_SERVING` 두 값만 있고 전체 dry run 은 상단 라디오로만 지정됩니다.

### 3.2 확정 시험 — 임시 엔드포인트로 오류의 종류를 봅니다

시험 B 가 200 이어도 확정은 아닙니다. **임시 엔드포인트를 만들어** 실제로 호출해야 합니다.

1. AI Gateway 엔드포인트 생성: `task: llm/v1/chat`, `custom_provider_url: <위 URL>/v1/chat/completions`
2. 한 번 호출
3. 임시 HTTP 서버는 POST 를 모르므로:
   - **egress 가 열려 있으면** `501 Unsupported method` 가 상류 오류로 되돌아옵니다
   - **막혀 있으면** `CUSTOMER_UNAUTHORIZED` 가 옵니다
4. **응답 코드가 아니라 오류의 종류로 갈립니다.**
5. 시험 후 임시 엔드포인트와 HTTP 서버를 지우십시오

---

## 4. 막혀 있을 때의 선택지

| 선택지 | 내용 | 대가 |
|---|---|---|
| **agent 를 Databricks Apps 에서 실행하고 driver-proxy 를 직접 호출** ([STEP4B](STEP4B_apps_agent.md) · 절차·코드 제공) | **실측(2026-09-10)**: egress 정책 단계는 통과하나, driver-proxy 인증에서 거부됨. 고객과 동일한 정책 구성에서 두 자격 모두 HTTP 403 확인 — [부록 A4 §10.2](appendix/A4_serverless_egress_allowlist.md). 정책 변경은 불필요하고, **클러스터 single user 의 PAT 를 secret 앱 리소스로 주입하면 통과**합니다 — 그 신원으로 `/health` 200 · 단발 대화 200 · **도구 호출 agent 루프 성공**까지 실측 | 게이트웨이 기능(rate limit · usage tracking · Playground) 상실 · **PAT 를 앱에 배포**(secret 리소스 · 앱 SP 에 `READ` 만) · agent 를 Apps 로 옮기는 작업 |
| ~~정책에 FQDN 허용 추가~~ | **이 방법은 쓸 수 없습니다.** 워크스페이스 자신의 FQDN 은 `allowed_internet_destinations` 에 등록되지 않고 백엔드 검증이 거부합니다(2026-09-10 실측 — 계정 콘솔·CLI 모두 동일 문구). `allowed_databricks_destinations` 자기참조도 **불가**입니다 — egress 스키마에 그런 필드가 없고 콘솔 Egress 탭에도 해당 섹션이 없습니다 | — |
| **정책 enforcement 를 `Dry run mode for all products` 로 전환** | **실측으로 통과 확인**(2026-09-10, 3회 재현 · 재배포 없이 1분 내 반영). 단 그 정책이 적용된 워크스페이스에서 **모든 제품의 egress 강제가 해제**되므로 보안 승인이 필요합니다 — [부록 A4](appendix/A4_serverless_egress_allowlist.md) ⛔ 블록 | 계정 관리자 |
| **게이트웨이를 쓰지 않음** (호출 측이 classic 컴퓨트·외부 시스템 · 실측) | 고객 agent 가 driver-proxy 를 **직접** 호출(`…/driver-proxy-api/o/…/8005/v1/…`). hop ② 가 없어집니다 | rate limit · usage tracking · Playground · 엔드포인트 단위 권한 관리를 모두 잃고, **PAT 를 호출 측 시스템에 배포**(첫 행의 Apps 방안은 PAT 가 필요 없습니다) |
| **관리형 서빙으로 전환** | 가중치를 Model Serving 의 custom model 로 올려 자기 워크스페이스로 되돌아오는 hop 자체를 없앱니다 | 아키텍처·비용·기간 재산정 |

**[부록 A4](appendix/A4_serverless_egress_allowlist.md) 는 계정 관리자에게 전달하는 문서입니다.** 최상단 ⛔ 블록에 위 제약(워크스페이스 FQDN 등록 거부)과 **선택지별 실측 등급·대가**가 정리되어 있고, 그 뒤에 정책 조회·편집·전파 확인·거부 로그 조회 절차가 있습니다. 어떤 선택지를 고르든 정책 상태 확인과 안전한 편집 절차는 그대로 쓰입니다.

---

## 5. 이 시험에서 검증된 것과 검증되지 않은 것

### 검증된 항목

- **시험 A** (hop ① 클라이언트 → driver-proxy): 음성 시험(없는 경로 → 404)까지 포함해 **실측 실행됨**
- 2026-09-09 고객 워크스페이스: `/health` → 200 · `/nonexistent-xyz` → 404 · `{"detail":"Not Found"}`
- Windows curl 주의 사항 (schannel · TLS revocation check)
- **확정 시험**(§3.2 · 임시 엔드포인트 호출)과 **§4 선택지의 등급**: 2026-09-10 Databricks 측 워크스페이스에서 정책만 바꿔 **양방향으로 실측**했습니다(차단 → 통과 → 차단 재현). 근거는 [부록 A4 §10.1](appendix/A4_serverless_egress_allowlist.md)
- **Databricks Apps 에서의 driver-proxy 호출**: 신원별로 갈립니다 — 앱 SP·사용자 OBO 는 **403**, **single user 의 PAT(secret 주입)는 200** 이고 그 신원으로 **단발 대화·도구 호출 agent 루프·음성 대조군(404) 까지 실측 성공**([부록 A4 §10.2](appendix/A4_serverless_egress_allowlist.md)). egress 정책 단계 통과는 음성 대조군(미허용 도메인 DNS 실패)과 함께 확인. **클러스터 기동 상태에서 측정**했습니다(종료 상태 측정은 false positive 였습니다)
- **앱 URL 로의 사용자 접속**: 브라우저에서 SSO 통과 후 화면 동작 확인
- **결합 시험(고객 정책 강제 + PAT 인증)**: **실측 완료** — 강제를 음성 대조군으로 확인한 창에서 **게이트웨이는 차단 · Apps agent 루프는 성공**(부록 A4 §10.2 결합 시험). Apps 계층 강제 반영은 부착 후 **7분 40초**, 게이트웨이 차단은 **3분 29초**

### 미실행 설계 항목

- **시험 B** (hop ② **serverless 노트북** → driver-proxy): **미실행 설계입니다** — 절차는 설계했으나 실행해 확인한 적이 없습니다. 실측된 것은 위의 확정 시험(external model 엔드포인트)과 Apps 이며, **제품이 다르면 결과가 다를 수 있습니다**
- restricted 정책에서 `*.databricksapps.com` 을 허용 목록에 넣을 수 없는 문제(부록 A4 §10.2) — 이번 시험 중 앱 접속은 정상이었으나 장기 영향은 미확인
- UC Volume · Azure Key Vault 백엔드 secret scope

---

## 다음 단계

**시험 A·B 모두 200 또는 `CUSTOMER_UNAUTHORIZED` 로 판정되었다면** [STEP1_model_weights.md](STEP1_model_weights.md) 로 넘어가 가중치 준비를 시작하십시오.  
**`CUSTOMER_UNAUTHORIZED` 가 나왔다면** 계정 관리자에게 [appendix/A4_serverless_egress_allowlist.md](appendix/A4_serverless_egress_allowlist.md) 를 전달하거나 위 선택지를 검토하십시오. **정책을 바꾸지 않고 진행할 수 있는 유일한 실측 경로는 §4 첫 행(Databricks Apps)** 입니다.
