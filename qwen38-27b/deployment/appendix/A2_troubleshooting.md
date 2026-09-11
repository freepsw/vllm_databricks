# 부록 A2 · 문제 해결 (증상 기준)

**쓰는 법**: 화면에 보이는 것으로 아래 색인에서 절을 찾으십시오. 각 항목은 **증상 / 원인 / 확인 방법 / 조치** 로 되어 있습니다.

**먼저 알아둘 것** — AI Gateway 엔드포인트의 실패 응답에서 최상위 `error_code` 는 실제 원인과 다를 수 있습니다. **진짜 원인은 `external_model_error` 안에** 들어 있습니다.

```json
{"error_code":"INTERNAL_ERROR",
 "message":"{\"external_model_provider\":\"custom\",
             \"external_model_error\":\"<html>...502 Bad Gateway...\"}"}
```

---

## 증상 → 절 색인

| 화면에 보이는 것 | 절 |
|---|---|
| **502** · `INTERNAL_ERROR` · `502 Bad Gateway` | [§1](#1-502--상류에-닿지-못함) |
| **403** · **401** | [§2](#2-403--401--인증과-권한) |
| **`CUSTOMER_UNAUTHORIZED`** · `serverless network policy` | [§3](#3-customer_unauthorized--serverless-egress-차단) |
| **400** · `BAD_REQUEST` · `INVALID_STATE` · `requires --enable-auto-tool-choice` | [§4](#4-400--요청이-거절됨) |
| **429** · `REQUEST_LIMIT_EXCEEDED` | [§5](#5-429--호출-한도-초과) |
| **HTTP 200 인데 본문이 비어 있음** | [§6](#6-http-200-인데-본문이-비어-있음--가장-헷갈리는-증상) |
| 재기동 중 실패가 **빠르거나 느림** · 다운타임을 알고 싶을 때 | [§7](#7-재기동-다운타임과-502-의-두-얼굴) |
| 배포·기동 단계 실패 (FIPS · `/local_disk0` · `/Workspace` · OOM · 포트) | [§8](#8-배포기동-단계) |
| 무해한 경고 · Playground 목록에 없음 | [§9](#9-무해한-것과-그-밖의-것) |

---

## 1. 502 — 상류에 닿지 못함

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| 502 · `502 Bad Gateway` · 엔드포인트 상태는 `READY` | vLLM 이 **`--host 127.0.0.1`** 로 바인드됨. driver-proxy 는 드라이버 **사설 IP** 로 접속하므로 닿지 못합니다 | 드라이버에서 `ps -eo args \| grep 'vllm serve'` 에 `--host 0.0.0.0` 이 있는지. 그리고 `curl http://$(hostname -I \| cut -d' ' -f1):8005/health` | `BIND_HOST=0.0.0.0` 으로 재기동 → [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) |
| 502 · 어제까지 되던 것이 오늘 전부 실패 | **클러스터 자동 종료**로 상류가 죽음. 엔드포인트는 계속 `READY` 입니다 | `databricks clusters get <CLUSTER_ID>` 의 상태와 `autotermination_minutes` | 클러스터 시작 → `/local_disk0` 이 비워졌으므로 venv·가중치·serve 를 다시 올림 → §8. `autotermination_minutes: 0` 으로 재발 방지 |
| 502 · vLLM 프로세스가 없음 | serve 프로세스 종료(OOM·수동 정리·좀비 정리 실패) | `nvidia-smi` 로 VRAM 점유 · `tail -40 /local_disk0/serve.log` | serve 재기동. VRAM 이 반환되지 않았으면 §8 의 좀비 정리 |
| 502 가 **아주 빠르게** 떨어짐 (0.037~0.314초) | 재기동 중 (모델 로딩 미완) 또는 상류 바인드 오류 | `curl http://127.0.0.1:8005/health` 가 아직 503/무응답 | 기다리십시오. 재기동 **45~190초** · 콜드 **280~320초** → §7 |

---

## 2. 403 · 401 — 인증과 권한

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| 403 · `Invalid request` | secret 에 담긴 값이 **노트북 컨텍스트 토큰**입니다. 워크스페이스 안에서는 200, 밖에서는 403 이며 게이트웨이는 밖에서 호출합니다 | `databricks tokens list` 에 그 토큰이 보이는지 (보이지 않으면 노트북 토큰) | 노트북 밖에서 발급한 PAT 로 교체 → [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) |
| 403 · `does not have required scopes: clusters` | PAT 에 `clusters` scope 가 없음. **`ai-gateway` scope 를 고른 경우가 대표적입니다** | 오류 메시지가 필요한 scope 이름을 그대로 알려줍니다 | `scopes: ['clusters']` 로 재발급해 같은 secret 에 저장 |
| 403 · `Invalid access token` | PAT 가 **폐기**됨 (폐기는 즉시 반영되지 않고 수분 지연이 있습니다) | `databricks tokens list` 에서 해당 `token_id` 확인 | 새 토큰을 **같은 secret 키**에 저장. 엔드포인트는 참조만 하므로 재생성 불필요 |
| 403 또는 401 · 갑자기 전부 실패 | PAT **만료**. 사용자 PAT 는 만료 7일 전 통보, **90일 미사용 시 자동 폐기** | 발급 시 지정한 수명과 경과일 | 같은 secret 키에 새 값 저장 |
| 호출이 전부 **401** | vLLM 에 **`--api-key` 를 설정**함. driver-proxy 가 `Authorization` 을 상류로 전달하지 않습니다 | serve cmdline 에 `--api-key` 가 있는지 | `--api-key` 를 제거하고 재기동 |
| 401 (호출자 측) | 요청에 `Authorization` 헤더가 없음 | 호출 코드의 `api_key` · 환경변수 | 호출자 토큰을 채우십시오 |
| 403 · 특정 사용자만 실패 | 엔드포인트 호출 권한이 없음 | `databricks permissions get serving-endpoints <ID>` | `CAN_QUERY` 부여 → [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) |

---

## 3. `CUSTOMER_UNAUTHORIZED` — serverless egress 차단

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| `CUSTOMER_UNAUTHORIZED: Access to <워크스페이스> is denied because of serverless network policy` | 서빙 엔드포인트(serverless 컴퓨트)가 워크스페이스 FQDN 으로 나갈 수 없음. **vLLM·PAT·바인드와 무관합니다** | 노트북(classic 클러스터)에서 driver-proxy 를 직접 호출해 보십시오. **200 이면 상류는 정상이므로 막힌 것은 게이트웨이 hop 입니다.** 2026-09-10 에 Databricks 측 워크스페이스에서 **정책만 바꿔 양방향으로 재현**해 이 귀속을 확정했습니다([A4 §10.1](A4_serverless_egress_allowlist.md)) | **워크스페이스 FQDN 을 허용 목록에 추가하는 방법은 쓸 수 없습니다** — 백엔드 검증이 거부합니다(2026-09-10 실측). 남은 선택지는 **① agent 를 Databricks Apps 에서 실행하고 driver-proxy 직접 호출**([STEP4B](../STEP4B_apps_agent.md) · **정책 변경 불필요**. 단 앱 SP 토큰·사용자 OBO 토큰은 **403**(`Single-user check failed` · `required scopes: clusters`)이고, **클러스터 single user 의 PAT 를 secret 앱 리소스로 주입하면 통과** — 도구 호출 agent 루프까지 실측, [A4 §10.2](A4_serverless_egress_allowlist.md)) **② 정책 enforcement 를 `Dry run mode for all products` 로 전환**(실측 통과 · 모든 제품의 egress 강제 해제라는 대가) **③ 게이트웨이 없이 driver-proxy 직접 호출**(실측 · 호출 측에 PAT 배포) **④ 이 워크스페이스에만 Full access 정책 부착 ⑤ 관리형 서빙 전환**(미검증)입니다. `allowed_databricks_destinations` 자기참조는 **불가**(필드 자체가 없음). 상세는 [A4_serverless_egress_allowlist.md](A4_serverless_egress_allowlist.md) ⛔ 블록 |

정책 변경이 승인되지 않으면 게이트웨이를 쓰지 않고 driver-proxy 를 직접 호출하는 선택지가 있습니다(호출 한도·사용량 추적·Playground·엔드포인트 단위 권한을 잃습니다). **agent 를 Databricks Apps 에서 실행하는 경로는 egress 정책을 건드리지 않고 성립**합니다. 단 driver-proxy 인증층(전용 클러스터의 단일 사용자 검사 · `clusters` scope 미지원)때문에 **앱 SP 토큰만으로는 안 되고, single user 의 PAT 를 secret 으로 주입해야** 합니다 — [A4 §10.2](A4_serverless_egress_allowlist.md).

---

## 4. 400 — 요청이 거절됨

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| 400 · `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser` | tool calling 플래그 없이 vLLM 을 띄운 상태에서 `tools` 를 보냄. **무시가 아니라 거절입니다** | serve cmdline 에 `--enable-auto-tool-choice --tool-call-parser qwen3_xml` 유무 | `TOOL_CALL_PARSER=qwen3_xml` 로 재기동 → [../STEP3_vllm_serve.md](../STEP3_vllm_serve.md) |
| 400 · `BAD_REQUEST` · **0.38~0.45초에 즉시** | 컨텍스트 초과. 사용 가능한 프롬프트는 **`131072 − max_tokens`** 입니다 | 프롬프트 토큰 수 + `max_tokens` 합계. 한국어는 장문 구간 실측 **0.543 tok/자** | `max_tokens` 를 줄이거나 프롬프트를 분할하십시오 |
| 400 · `INVALID_STATE: Cluster … is in Terminated state` | **클러스터가 종료됨.** driver-proxy 는 이때 502 가 아니라 **400** 을 돌려줍니다 | 경로가 무엇이든 같은 응답이라 `/health` 로는 구분되지 않습니다. 클러스터 상태를 직접 보십시오 | 클러스터 시작 후 serve 재기동 → §1 |
| 엔드포인트 생성 시 400 · `please change to https` | 상류 URL 이 `http://` | 등록 노트북은 driver-proxy URL 을 자동 구성하므로 손으로 넣지 마십시오 | 노트북 셀 1 → 4 재실행 |
| 404 · `The model ... does not exist` | 엔드포인트의 모델 이름과 vLLM 의 `--served-model-name` 불일치 | 노트북 셀 2 의 출력(모델 이름은 `/v1/models` 에서 직접 읽습니다) | 셀 2 → 4 재실행 |
| 오타·이상한 필드를 보냈는데 **200** | 게이트웨이는 **알 수 없는 필드를 조용히 무시**하고, 요청의 `model` 도 **엔드포인트 설정값으로 덮어씁니다** | 응답 `model` 로는 검증할 수 없습니다 | 클라이언트 쪽에서 요청 스키마를 검증하십시오 |

---

## 5. 429 — 호출 한도 초과

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| 429 · `error_code: REQUEST_LIMIT_EXCEEDED` | AI Gateway rate limit(기본 **120 calls/분** · key=endpoint) 초과 | `databricks serving-endpoints get <이름>` 의 `ai_gateway.rate_limits` | 클라이언트에 **자체 지수 백오프**를 두십시오. **`Retry-After` 헤더가 없습니다** |

실측: 400건을 5.2초에 발사(=4,573 req/분)했을 때 200 **133건** · 429 **267건** 이었고, **+10초 이후에는 다시 200** 이었습니다. 한도를 올릴 때는 서버의 실측 처리량(**196.4 req/분** · N=32 · 출력 256토큰)을 함께 고려하십시오 — 한도만 올려도 GPU 능력 이상은 나오지 않습니다 → [../../performance/P3_capacity_and_cost.md](../../performance/P3_capacity_and_cost.md)

---

## 6. HTTP 200 인데 본문이 비어 있음 — 가장 헷갈리는 증상

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| 200 · `choices[0].message.content` 가 **빈 문자열** · 오류도 예외도 없음 | **추론 폭주.** 이 모델은 답을 쓰기 전에 추론하고 그 토큰이 `max_tokens` 에서 **먼저** 차감됩니다. 한도를 다 쓰면 본문 없이 끝납니다 | **`finish_reason == "length"`** 인지 보십시오. 이것이 **유일한 감지 조건**입니다. `usage.completion_tokens_details.reasoning_tokens` 도 함께 확인 | `max_tokens` 를 **1,500 이상**으로 올리고, `finish_reason == "length"` 를 **실패로 처리**하는 코드를 넣으십시오 |

실측 근거입니다.

- 같은 어려운 질문 7건 중 **6건이 본문 0자** (`finish_reason=length`)
- `max_tokens=6000` 에서 **130.3초를 소모한 뒤 본문 0자**
- 산수 질문 · 게이트웨이 경유 경계: `max_tokens` **100 → 6/6 정상** · **200 → 4/6** · **300 → 0/6**
- 사소한 질문(`1+1은?`)에서도 출력 82토큰 중 **78토큰(95.1 %)** 이 추론이었습니다

`cached_tokens` 는 **항상 `None`** 이므로 캐시 적중 여부로는 아무것도 판단할 수 없습니다. 이것은 정상 동작입니다.

---

## 7. 재기동 다운타임과 502 의 두 얼굴

**"빠른 실패"와 "느린 실패"를 구분하면 원인이 갈립니다.** 상류를 재기동하는 동안의 실측입니다.

| 관측 | 값 | 뜻 |
|---|---|---|
| 재기동 다운타임(컴파일 캐시 있음) | **45초 · 190초** — 각 1회 관측 · 같은 조건에서도 갈림 | 이 시간 동안의 호출은 전부 실패합니다. 계획은 **190초** 로 잡으십시오 · 45초는 SLA 근거로 부적절 |
| 콜드 기동(컴파일 캐시 없음) | **280~320초** (관측 280 · 319 · 320초) | 클러스터 재시작 후·첫 기동. 계획은 **320초** 로 |
| 재기동 중 502 | 총 **132건** | 그중 **대부분은 프롬프트 크기와 무관하게 0.037~0.314초에 즉시** 502 |
| 이미 생성 중이던 요청 | **1건이 28.47초** 를 기다린 뒤 502 | 연결이 이미 서 있던 요청만 늦게 실패합니다 |

→ **즉시 떨어지는 502** 는 상류가 아예 없다는 뜻입니다(재기동·자동 종료·바인드 오류).  
→ **수십 초 매달린 뒤의 502** 는 처리 중이던 요청이 상류 종료로 끊긴 것입니다. 클라이언트 타임아웃을 그보다 짧게 잡으면 이 구분을 볼 수 없게 됩니다.

재기동 중 실패한 요청은 재시도로 회복됩니다. 무중단이 필요하면 클러스터 재기동 자체를 계획된 창(window)으로 다루십시오.

---

## 8. 배포·기동 단계

| 증상 | 원인 | 확인 방법 | 조치 |
|---|---|---|---|
| python 즉시 종료 · `FATAL FIPS SELFTEST FAILURE` (rc=134) | DBR 19 에 **`OPENSSL_FORCE_FIPS_MODE` 환경변수**가 설정되어 있음. venv 환경 오염 | `env \| grep -E 'OPENSSL\|FIPS'` | 값을 바꾸는 것이 아니라 **제거**해야 합니다 — `unset PYTHONPATH LD_LIBRARY_PATH` 와 OPENSSL/FIPS 변수 unset. `02_build_venv.py` · `03_stage_weights.py` 에는 이미 포함되어 있습니다 |
| 재시작 후 venv·가중치·스크립트가 모두 사라짐 | **`/local_disk0` 은 클러스터별 ephemeral 스토리지**입니다. 재시작·재생성하면 비워집니다 | `ls /local_disk0` | 정상이며 오류가 아닙니다. 부트스트랩 셀 → venv 재빌드 → 가중치 확보를 다시 수행하십시오(합계 약 2분 30초). 반복 배포가 예상되면 가중치를 UC Volume 에 영구 보관하십시오 |
| 부트스트랩이 **오류 없이 0개**를 복사함 | **`/Workspace` 는 노트북 셀에서만 보입니다.** 분리 프로세스(`setsid`·`nohup`·별도 세션)에서는 예외가 아니라 **빈 디렉터리처럼 동작**합니다(`isdir()` = `False`, `glob()` = `[]`) | 복사된 파일 수를 출력하십시오. **7개**가 나와야 합니다 | 부트스트랩을 **노트북 셀에서** 실행하십시오. `/dbfs` 는 분리 프로세스에서도 정상입니다 |
| 워크스페이스 파일이 확장자 없이 올라감 | `--format RAW` 누락 → `.py` 가 노트북으로 임포트되고 확장자가 제거됨 | `databricks workspace list <경로> -o json \| grep object_type` 이 `"FILE"` 만 나와야 합니다 | `--format RAW` 로 다시 업로드(`import-dir` 은 쓰지 마십시오) |
| serve 기동 실패 · `Engine core initialization failed` (OOM) | 이전 `VLLM::EngineCore` 가 VRAM 점유 · `--gpu-memory-utilization` 과다 | `nvidia-smi` 로 VRAM 이 0 MiB 로 반환됐는지 | 좀비 정리 후 재기동. **`pkill -f 'vllm serve'` 는 EngineCore 를 남깁니다** → [../STEP3_vllm_serve.md](../STEP3_vllm_serve.md) 의 정리 절차 |
| `Address already in use` (8005) | 이전 serve 가 아직 포트를 점유 | `lsof -ti :8005` | `fuser -k 8005/tcp`. 포트만 풀리고 VRAM 은 남을 수 있으므로 위 정리도 함께 |
| `/health` 가 계속 503 · 20분 이상 | 컴파일 중이거나 기동 실패 | `tail -40 /local_disk0/serve.log` · `grep -i "out of memory"` | 콜드 기동은 **280~320초** 가 정상 범위입니다. 그 이상이면 로그로 판단 |
| 검증 스크립트가 `/health` 연결 실패 | `127.0.0.1:8005` 로 접속하므로 **serve 가 떠 있는 그 드라이버**에서 실행해야 합니다 | 실행 위치 | 같은 드라이버에서 재실행 |

---

## 9. 무해한 것과 그 밖의 것

| 증상 | 판정 |
|---|---|
| 노트북에서 `Failed to get token for subscription` 경고 | **무해합니다.** Azure 관리 ID 조회 경고이며 동작에 영향 없습니다(노트북 UI 에서는 나타나지 않습니다) |
| 셀 아래에 MLflow Tracing · External Models 안내와 trace 위젯이 표시됨 | **무해합니다.** DBR ML 이 `from openai import OpenAI` 를 감지해 끼워 넣는 안내입니다. 다만 그 안내가 권하는 **`%pip install -U mlflow` 와 `dbutils.library.restartPython()` 은 실행하지 마십시오** — Python REPL 이 재시작되어 Run all 이 중단됩니다 |
| 이후 셀이 모두 `Py4JException` 으로 실패 | `%pip` 이 Python REPL 을 재시작한 경우입니다. 등록 노트북은 `subprocess` 로 설치하므로 발생하지 않습니다 — 직접 `%pip` 을 추가하지 마십시오 |
| Playground 목록에 엔드포인트가 없음 | `task` 가 `llm/v1/chat` 이 아니거나 `READY` 가 아닙니다. `databricks serving-endpoints get <이름>` 으로 확인하십시오 |
| `inference_table_config` 설정 실패 (`METASTORE_DOES_NOT_EXIST`) | Unity Catalog metastore 가 없습니다. `usage_tracking_config` 와 `rate_limits` 는 영향받지 않습니다 |
| 엔드포인트 생성·갱신이 `PERMISSION_DENIED` | 기록된 **생성자가 워크스페이스 멤버가 아닙니다.** 생성자는 변경할 수 없으므로 삭제 후 재생성해야 합니다 → [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) |
| KV cache 토큰 수가 기준선과 약간 다름 | **정상입니다.** 같은 플래그로도 기동마다 달라집니다(관측 1,188,386 대 1,240,814 토큰 = 약 4.4 % 차이). 정확히 일치하는지로 판정하지 마십시오 |

---

**여기서 해결되지 않으면**: [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) 의 안내와 [../STEP3_vllm_serve.md](../STEP3_vllm_serve.md) 의 기동 문제 해결 절을 보십시오.
