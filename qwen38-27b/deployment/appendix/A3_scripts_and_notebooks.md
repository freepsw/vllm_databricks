# 부록 A3 · 제공 파일 — 역할 · 실행 위치 · 환경변수

이 패키지의 실행 자산은 **스크립트 7개 + 노트북 2개 + 앱 소스 2개**입니다. 문서를 따라가다 "이 파일이 무엇이었지" 싶을 때 여기를 보십시오.

**실행 위치의 뜻**  
· **드라이버** = vLLM 이 도는 GPU 클러스터의 드라이버 노드. 노트북 셀 또는 셸에서 실행합니다.  
· **로컬 CLI** = 담당자 PC 의 `databricks` CLI.  
· **노트북** = 워크스페이스에 임포트해 GPU 클러스터에 연결한 노트북.  
· **Databricks Apps** = 앱 소스로 업로드해 serverless 컴퓨트에서 도는 프로세스(GPU 클러스터가 아닙니다).

> **스크립트는 `/local_disk0/scripts` 에 두고 실행합니다.** `/local_disk0` 은 클러스터별 ephemeral 스토리지라 재시작 때마다 비워지므로, 부트스트랩 셀로 다시 복사해야 합니다. 복사는 **노트북 셀에서** 하십시오 — `/Workspace` 는 분리 프로세스에서 보이지 않습니다([A2](A2_troubleshooting.md) §8).

---

## 스크립트 (7개)

| 파일 | 역할 | 실행 위치 | 필수 | 대응 단계 |
|---|---|---|---|---|
| `00_check_prereq.py` | 배포 전 환경 점검 (GPU · 디스크 · 도구 · 네트워크 · 환경변수 오염) | 드라이버 | 권장 | [../STEP2_cluster_and_runtime.md](../STEP2_cluster_and_runtime.md) |
| `01_cluster.json` | 클러스터 생성 스펙 | 로컬 CLI | 필수 | [../STEP2_cluster_and_runtime.md](../STEP2_cluster_and_runtime.md) |
| `02_build_venv.py` | vLLM 0.28.0 격리 venv 빌드 | 드라이버 | **필수** | [../STEP2_cluster_and_runtime.md](../STEP2_cluster_and_runtime.md) |
| `03_stage_weights.py` | 가중치 확보 (HuggingFace 또는 UC Volume) | 드라이버 | **필수** | [../STEP1_model_weights.md](../STEP1_model_weights.md) |
| `04_serve.sh` | vLLM serve 기동 + 기동 로그 점검 | 드라이버 | **필수** | [../STEP3_vllm_serve.md](../STEP3_vllm_serve.md) |
| `05_validate.py` | 빠른 검증 8항목 (약 10분) | 드라이버 | **필수** | [../STEP5_test.md](../STEP5_test.md) |
| `06_soak.py` | 장시간 안정성 시험 | 드라이버 | 선택 | [../STEP5_test.md](../STEP5_test.md) |

### 주요 환경변수와 멱등성

| 파일 | 주요 환경변수 (기본값) | 여러 번 실행해도 되는가 |
|---|---|---|
| `00_check_prereq.py` | 없음 (환경을 읽기만 합니다) | **예** — 상태를 바꾸지 않습니다 |
| `01_cluster.json` | 없음 (JSON 스펙). **`autotermination_minutes: 90`** 을 게이트웨이·soak 용이면 **`0`** 으로 바꾸십시오 | 파일이므로 해당 없음. 이미 만든 클러스터의 값을 바꾸면 **재시작되어 `/local_disk0` 이 비워집니다** |
| `02_build_venv.py` | 없음 | **예** — `/local_disk0/vllm028/bin/python` 이 있으면 버전을 확인하고 재빌드를 건너뜁니다 |
| `03_stage_weights.py` | `MODEL_HF_ID`(`Qwen/Qwen3.8-27B-FP8`) · `LOCAL_PATH`(`/local_disk0/models/Qwen3.8-27B-FP8`) · `VOLUME_PATH`(없음 = HF 다운로드) · `ALLOW_HF_DOWNLOAD`(`true`) | **예** — `.stage_complete` 마커가 있으면 건너뜁니다 |
| `04_serve.sh` | **`BIND_HOST`(`127.0.0.1`)** · **`TOOL_CALL_PARSER`(없음)** · `PORT`(`8005`) · `SERVED_MODEL_NAME`(`qwen38-27b`) · `MAX_MODEL_LEN`(`131072`) · `KV_CACHE_DTYPE`(`fp8`) · `GPU_MEM_UTIL`(`0.90`) · `SERVE_LOG`(`/local_disk0/serve.log`) | **예** — 기존 프로세스를 정리하고 VRAM 반환을 확인한 뒤 다시 올립니다(재기동됨) |
| `05_validate.py` | `PORT`(`8005`) · `SERVED`(`qwen38-27b`) · `OUT`(`/local_disk0/validate_result.json`) · `SERVE_LOG`(`/local_disk0/serve.log`) · `SELFTEST`(없음) | **예** — 결과 JSON 을 덮어씁니다 |
| `06_soak.py` | `DURATION_S`(**`28800` = 8시간**) · `CONCURRENCY`(`22`) · `MAX_TOKENS`(`256`) · `PORT`(`8005`) · `SERVED`(`qwen38-27b`) · `OUT`(`/local_disk0/SOAK_result.json`) · `DONE_MARKER`(`/local_disk0/SOAK_DONE`) | **예** — 단, **시작 전에 이전 실행의 `SOAK_DONE` 과 결과 JSON 을 지우십시오.** 남아 있으면 완료 판정을 오독합니다 |

**`04_serve.sh` 의 기본 `BIND_HOST` 는 `127.0.0.1` 입니다.** 게이트웨이로 감쌀 예정이면 반드시 `BIND_HOST=0.0.0.0` 을 지정하십시오 — 지정하지 않으면 엔드포인트 호출이 전부 502 가 됩니다. tool calling 을 쓸 예정이면 `TOOL_CALL_PARSER=qwen3_xml` 도 함께 주십시오(없으면 `tools` 요청이 400).

**`06_soak.py` 의 `verdict` 를 완료 신호로 쓰지 마십시오.** `SOAK_result.json` 의 `totals.verdict` 는 시험이 끝나기 전에도 `completed` 로 채워져 있습니다. 완료 여부는 **`DONE_MARKER` 파일의 존재**로 판단하십시오.

---

## 노트북 (3개)

| 파일 | 역할 | 연결 대상 | 필수 | 특징 |
|---|---|---|---|---|
| `07_deploy_notebook.py` | 스크립트 스테이징 → venv → 가중치 → serve → 검증 → soak 을 위젯으로 실행 | vLLM 을 띄울 **GPU 클러스터** | 권장 | 위젯 `soak_duration_s` 기본값 **`28800`**. 시작 전에 이전 `SOAK_DONE`·결과 JSON 을 자동으로 지웁니다 |
| `notebook_gateway_register.py` | AI Gateway 엔드포인트 생성·갱신 (셀 6개) | **vLLM 이 도는 그 GPU 클러스터** | 게이트웨이를 쓸 때 필수 | **외부 파일 의존이 없습니다.** 셀 1 만 고치고 1 → 2 → 3 → 4 를 실행. 같은 이름이 있으면 갱신하므로 **여러 번 실행해도 안전** |
| `08_step4b_apps_agent.py` | **STEP4B 검증** (셀 6개) — driver-proxy 로 T1~T5(`/health` · `/v1/models` · 단발 대화 · **도구 호출 루프** · **음성 404**)를 직접 실행하고, 앱이 쓴 자기시험 JSON 을 **신선도까지** 교차 검증해 한 줄로 판정 | **vLLM 이 도는 그 GPU 클러스터**(classic) | 앱 경로를 쓸 때 권장 | **PAT 를 만들지 않습니다**(노트북 컨텍스트 토큰 함정 · STEP4B §3). **T0(egress)은 측정하지 않습니다** — 노트북은 classic 이라 serverless 정책 강제를 볼 수 없습니다. 증거를 `/Shared/step4b_evidence_<UTC>.json` 으로 남깁니다 |

`08_step4b_apps_agent.py` 의 위젯 기본값입니다 — **자리표시자 두 개는 반드시 채워야** 실행됩니다.

| 위젯 | 기본값 |
|---|---|
| `cluster_id` | **`<클러스터ID>`** (자리표시자 · 비우거나 그대로 두면 셀 2 에서 멈춥니다) |
| `app_name` | **`<앱이름>`** (같음) |
| `port` · `secret_scope` · `secret_key` | `8005` · `qwen-agent` · `driver_pat` |
| `expect_model` · `expect_ctx` | `qwen38-27b` · `131072`(0 이면 그 검사를 건너뜁니다) |
| `stale_limit_s` | `3600` — 앱 자기시험 파일이 이보다 오래되면 실패로 봅니다 |

`notebook_gateway_register.py` 의 셀 구성입니다 ([../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) 참조).

| 셀 | 하는 일 | 소요 |
|---|---|---|
| 1 | 설정 (**이 셀만 고칩니다**) — `ENDPOINT` · `PORT` · `SCOPE`/`KEY` · `RATE_LIMIT` | — |
| 2 | vLLM 도달성 점검(사설 IP) + 모델 이름·컨텍스트 상한 자동 인식 | 5초 |
| 3 | PAT 를 secret scope 에 저장 (노트북 컨텍스트 토큰이면 거부) | 10초 |
| 4 | 엔드포인트 생성/갱신 + 상류 URL 반영 확인 | 30초 |
| 5 | 스모크 테스트 + 응답 형식 확인 (실패 시 어느 hop 인지 자동 분리) | 10초 |
| 6 | 외부 agent 연결 예시 (OpenAI SDK · 스트리밍 · tool calling) | 1분 |

> 노트북은 `subprocess` 로 패키지를 설치합니다. **`%pip` 을 직접 추가하지 마십시오** — Python REPL 이 재시작되어 "Run all" 이 중단됩니다.

---

## Databricks Apps 앱 소스 (2개 · 선택)

게이트웨이 없이 앱 하나로 쓸 때만 씁니다 — 절차는 [../STEP4B_apps_agent.md](../STEP4B_apps_agent.md).

| 파일 | 역할 | 실행 위치 | 필수 | 특징 |
|---|---|---|---|---|
| `apps/agent_app/app.py` | agent 루프(도구 호출) · 신원 진단 · egress 대조군 · 자기시험 · 최소 화면 | **Databricks Apps**(serverless) | 선택 | **표준 라이브러리만** 씁니다(`requirements.txt` 없음 = 기동 시 pypi egress 불필요). 기동 직후 자기시험을 돌려 결과를 `/Shared/qwen_agent_selftest.json` 에 씁니다 |
| `apps/agent_app/app.yaml` | 런타임 설정(시작 명령 · 환경변수 · secret 연결) | 같음 | 선택 | **`VLLM_CLUSTER_ID` 를 실제 클러스터 ID 로 바꿔야 합니다.** `DRIVER_PAT` 는 `valueFrom: driver_pat` 으로 secret 리소스에서 주입됩니다 |

### 앱의 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VLLM_CLUSTER_ID` | 없음 (**필수**) | vLLM 이 도는 클러스터 ID |
| `VLLM_PORT` | `8005` | vLLM 포트 |
| `VLLM_MODEL` | `qwen38-27b` | `--served-model-name` 과 **같아야** 합니다 |
| `DRIVER_PAT` | 없음 (secret 주입) | driver-proxy 인증용 PAT. 비어 있으면 호출이 **403**([../STEP4B_apps_agent.md](../STEP4B_apps_agent.md) §2) |
| `VLLM_ORG_ID` | 호스트명에서 자동 추출 | `adb-<숫자>.` 형태가 아니면 직접 지정 |
| `RESULT_PATH` | `/Shared/qwen_agent_selftest.json` | 자기시험 결과를 쓸 워크스페이스 경로 |
| `PROBE_ALLOWED_URL` · `PROBE_DENIED_URL` | `https://pypi.org/simple/` · `https://example.com/` | egress 강제 여부 판정용 대조군. **미허용 도메인 쪽을 반드시 남겨두십시오** — 없으면 거짓 통과를 걸러낼 수 없습니다 |

**여러 번 배포해도 됩니다** — `apps deploy` 는 앱을 재시작하며, 자기시험은 결과 파일을 덮어씁니다.
`DRIVER_PAT` 를 갱신했으면 **반드시 재배포**하십시오(기동 시점에 읽습니다).

---

## 클러스터를 재시작한 뒤 다시 해야 하는 것

`/local_disk0` 이 비워지므로 아래를 다시 수행합니다(합계 약 2분 30초 + serve 기동). **`cluster_id` 는 바뀌지 않으므로 엔드포인트 설정은 고치지 않아도 됩니다.**

1. 부트스트랩 셀 — 스크립트 7개를 `/local_disk0/scripts` 로 복사 (복사된 파일 수가 **7개**인지 확인)
2. `02_build_venv.py` — venv 재빌드
3. `03_stage_weights.py` — 가중치 재확보 (UC Volume 복사가 HF 재다운로드보다 빠르고 egress 에 의존하지 않습니다)
4. `04_serve.sh` — `BIND_HOST=0.0.0.0 TOOL_CALL_PARSER=qwen3_xml` 로 기동

클러스터를 **삭제 후 재생성**한 경우에만 `cluster_id` 가 바뀌므로 등록 노트북의 **셀 1 → 2 → 4** 를 다시 실행하십시오.

---

## 이 패키지에 포함되지 않은 것

게이트웨이 연결의 **구버전 자산**은 **혼동을 막기 위해 제외**했습니다. 현행 경로는 [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) + `notebook_gateway_register.py` 하나입니다. 구버전 자산을 함께 전달하지 마십시오 — 두 경로가 섞이면 어느 쪽이 현행인지 판단할 수 없게 됩니다.

**성능 원시 데이터와 측정 하네스**는 **요청하시면 별도로 전달합니다.** 이 패키지에는 포함되지 않습니다. 이 경로는 **MLflow 모델 레지스트리를 쓰지 않습니다**(대안 비교는 [A1_model_registry_options.md](A1_model_registry_options.md)).
