# Qwen3.8-27B (FP8) 배포 트랙

**최종 결과물**: OpenAI 호환 엔드포인트 **1개** — `https://<workspace>/serving-endpoints/<엔드포인트 이름>/invocations`  
**모델 · 엔진**: `Qwen/Qwen3.8-27B-FP8` (사전양자화 FP8 · 약 30 GB) × vLLM **0.28.0**  
**노드**: `Standard_NC24ads_A100_v4` (A100 80GB × 1) · DBR 19.5 GPU ML · driver 580.159.03  
**vLLM 이 응답하기까지 실측 소요**: 약 **14분** (계산식은 아래 「5단계 지도」에 노출했습니다)

> **배포를 시작하기 전에 [성능 보고서](../performance/README.md) §1(한 장 요약)만은 읽으시기를 권합니다.**  
> 이 모델에는 클라이언트 코드를 바꿔야 하는 특성이 세 가지 있고, 모르고 배포하면 운영에서 발견하게 됩니다.

---

## 0. 받으신 파일과 명령을 실행하는 위치

이 문서 묶음은 아래 구조로 전달됩니다. **로컬 CLI 명령은 모두 `qwen38-27b/`(패키지 루트)에서
실행하는 것을 전제로 적혀 있습니다.** 다른 위치에서 실행하면 파일을 찾지 못합니다.

```
qwen38-27b/                     ← 여기서 databricks CLI 명령을 실행하십시오
├── README.md
├── deployment/                 배포 문서 (이 폴더)
│   └── images/                 STEP4B UI 배포 화면 캡처 31장
├── performance/                성능 보고서
├── scripts/                    드라이버에서 돌 스크립트 7개
│   ├── 00_check_prereq.py  01_cluster.json  02_build_venv.py
│   ├── 03_stage_weights.py 04_serve.sh      05_validate.py
│   └── 06_soak.py
├── notebooks/                  워크스페이스에 임포트할 노트북 3개
│   ├── 07_deploy_notebook.py
│   ├── notebook_gateway_register.py
│   └── 08_step4b_apps_agent.py     STEP4B 검증
└── apps/                       Databricks Apps 앱 소스 (STEP4B · 선택)
    └── agent_app/
        ├── app.py
        └── app.yaml
```

```bash
# 실행 위치 확인 — scripts · notebooks · apps 가 함께 보여야 합니다
ls scripts notebooks apps
```

### 두 가지 실행 방법 — 아무거나 고르십시오

STEP1~STEP5 는 **필요한 노트북 셀을 문서 안에 그대로 실어 두었습니다.** 복사해 붙여 넣으면 됩니다.
같은 절차를 **위젯으로 실행하는 노트북**도 함께 드립니다 — 셀을 옮기는 것이 번거로우면 이것을
임포트하십시오.

```bash
# 배포 작업 노트북 (STEP1~STEP3·STEP5 를 셀 단위로 실행)
databricks workspace import --language PYTHON --format SOURCE --overwrite \
  /Users/<사용자>/07_deploy_notebook \
  notebooks/07_deploy_notebook.py --profile <PROFILE>
```

어느 쪽을 골라도 **결과는 같습니다.** 문서에는 두 경로의 판정 기준을 같이 적어 두었습니다.

---

## 1. 이 트랙이 만드는 것

이 트랙을 끝까지 따라가면 **표준 OpenAI 호환 엔드포인트 1개**가 남습니다. 외부 agent 는 OpenAI SDK 의
`base_url` 만 바꿔 붙일 수 있고, Databricks AI Playground 에서도 바로 대화할 수 있습니다.

엔드포인트는 Databricks **서빙 엔드포인트(external model · `provider: custom`)** 로 만들어지며,
AI Gateway 계층이 **rate limit** 과 **usage tracking** 을 담당합니다. 모델 자체는 GPU 클러스터
**드라이버 프로세스**에서 vLLM 으로 떠 있습니다.

## 2. 아키텍처

```
[게이트웨이 경유 — 이 트랙의 최종 형태]

  외부 agent · AI Playground
        │  Authorization: Bearer <호출자 토큰>
        ▼
  https://<workspace>/serving-endpoints/<엔드포인트 이름>/invocations
        │  ← Databricks 서빙 엔드포인트 (external model · provider: custom)
        │    AI Gateway: rate limit · usage tracking
        │  Authorization: Bearer <secret scope 에 저장한 PAT>
        ▼
  https://<workspace>/driver-proxy-api/o/<조직ID>/<클러스터ID>/8005/v1/chat/completions
        │  ← Databricks driver-proxy (드라이버 사설 IP 로 접속)
        ▼
  vLLM (드라이버 프로세스 · 0.0.0.0:8005)
```

### driver-proxy 를 거치는 이유

서빙 엔드포인트의 상류 URL 은 **HTTPS 만** 허용됩니다(`http://` 로 만들면 거부됩니다).
드라이버에 직접 붙는 경로는 사설 IP·자체 인증서 문제로 이 조건을 만족시킬 수 없습니다.
driver-proxy URL 은 워크스페이스 도메인을 그대로 쓰므로 **공인 인증서·공개 DNS·Bearer 인증이 한 번에 해결**됩니다.

**게이트웨이를 쓰지 않는 「직접 호출」 경로도 driver-proxy 를 지납니다.** 두 경로의 차이는
**AI Gateway 계층만큼**입니다. 측정값은 [성능 보고서 P1](../performance/P1_direct_vs_gateway.md) 에 있습니다.

---

## 3. 6단계 지도 (STEP0 은 조건부)

| 단계 | 문서 | 하는 일 | 소요 시간 |
|---|---|---|---|
| **STEP 0** (조건부) | [STEP0_gateway_precheck.md](STEP0_gateway_precheck.md) | **AI Gateway 를 붙일 계획**이면: 도달성 사전 시험 (네트워크 차단 사전 판별) | [추정] 약 **5분** · 가중치 투입 전에 차단 여부 판별 |
| **STEP 1** | [STEP1_model_weights.md](STEP1_model_weights.md) | 가중치 경로 결정(HF 직접 / UC Volume) · 드라이버 `/local_disk0` 스테이징 · 66개 shard 검증 | 결정 **즉시** / 실행 **약 72.6초** (HF · 약 415 MB/s 조건. 대역폭에 따라 수십 분도 정상) — **실행은 STEP2 이후** |
| **STEP 2** | [STEP2_cluster_and_runtime.md](STEP2_cluster_and_runtime.md) | 클러스터 생성·기동 · 스크립트 7개 스테이징 · 격리 venv 빌드 · 사전 점검 | 클러스터 **약 6분** + 스테이징 **즉시** + venv **62~77초** |
| **STEP 3** | [STEP3_vllm_serve.md](STEP3_vllm_serve.md) | vLLM serve 기동 · 기동 로그 Oracle 판정 · 분리 실행 | 콜드 **280~320초**(131K) / 약 320초(262K) / 재기동 **45~190초** |
| **STEP 4** | [STEP4_ai_gateway.md](STEP4_ai_gateway.md) | PAT·secret 준비 · 서빙 엔드포인트 생성 · 종단 검증 | [추정] 약 **15분** (STEP3 를 `0.0.0.0` 으로 띄운 경우) |
| **STEP 4B** (선택 · STEP4 대안) | [STEP4B_apps_agent.md](STEP4B_apps_agent.md) | **Databricks Apps 에 agent 앱 배포** — 게이트웨이 없이 driver-proxy 직접 호출 · 도구 호출 루프 · 자기시험. **UI 로만 하는 길(화면 캡처 31장)** 과 CLI 길을 모두 실측 | **약 12분** (실측) |
| **STEP 5** | [STEP5_test.md](STEP5_test.md) | 품질·동시성·장문 8항목 검증 | [추정] 약 **10분** |

### 합계 (계산식 노출)

**AI Gateway 없이 vLLM 이 응답할 때까지 (STEP 0 생략, STEP 1~3, 콜드 기동 기준)**

```
  360초 (클러스터 기동 · 약 6분)
+   0초 (스크립트 스테이징 = 즉시)
+  77초 (venv 빌드 · 보수값 62~77초 범위)
+  72.6초 (가중치 확보 · HuggingFace)
+ 320초 (vLLM 콜드 기동 · 보수값 280~320초 범위)
= 829.6초 ≈ 약 14분
```

**AI Gateway 를 붙일 계획 시: STEP0 을 먼저 (합계 = 위 값 + 5분)**

**엔드포인트 종단까지 (STEP 1~5, STEP0 제외)**

```
  829.6초 (위 합계)
+ 900초 (STEP 4 · [추정] 약 15분)
+ 600초 (STEP 5 · [추정] 약 10분)
= 2,329.6초 ≈ 약 39분
```

> **STEP 4·5 의 값은 추정치입니다** — 실측되지 않았습니다. **STEP 1~3 의 값만 실측입니다**(STEP 0 의 5분도 추정입니다).  
> STEP 3 을 `--host 127.0.0.1` 로 띄웠다면 STEP 4 에서 vLLM 을 다시 올려야 하므로 약 5분이  
> 더 듭니다(합계 약 44분). **처음부터 `0.0.0.0` 으로 띄우십시오.**

같은 클러스터에서 serve 만 다시 올리는 경우는 **45 ~ 190초** 로 관측되었습니다(각 1회).
계획은 보수값 **190초** 로 잡으십시오. 클러스터를 **재기동**하면 `/local_disk0` 이 비워지므로
STEP 1·2 를 다시 수행해야 합니다 (STEP2 참조).

**8시간 연속 부하 시험은 선택입니다.** 실측 기준선: 82,129 요청 · 오류 **0** · VRAM drift **0.122 %**
(측정 창 7.998시간). 단, **이 기준선은 `06_soak.py` 의 기본값(플래그 미포함·host 127.0.0.1)과는
다른 조합에서 얻은 것**입니다(다른 검증 클러스터 · 다른 플래그 조합). 진행 방법은
[STEP5_test.md](STEP5_test.md) 를 보십시오.

---

## 4. 사전 요건 체크리스트

시작 전에 아래를 모두 확인하십시오. 하나라도 비어 있으면 뒤 단계에서 반드시 막힙니다.

| # | 항목 | 요구값 | 확인 방법 |
|---|---|---|---|
| 1 | Azure GPU 쿼터 | `Standard_NC24ads_A100_v4` × 1 (A100 80GB × 1 · 24 vCPU) | `az vm list-usage -l <REGION> -o table \| grep -i "standard.*a100.*family"` |
| 2 | SKU 지역 가용성 | 배포 지역에 해당 SKU 존재 | `az vm list-skus -l <REGION> --size Standard_NC24ads -o table` |
| 3 | Databricks 런타임 | `19.x-gpu-ml-scala2.13` (driver 580.x · CUDA 13.0 · compute capability 8.0) | 클러스터 생성 시 지정 |
| 4 | `/local_disk0` 여유 | **45 GB 이상** (venv 7.6 GB + 가중치 약 30 GB + 임시 2~3 GB) | 드라이버에서 `df -h /local_disk0` |
| 5 | 네트워크 egress | `https://huggingface.co` 도달 (막혀 있으면 UC Volume 경로) | STEP1 경로 A/B 선택 |
| 6 | 스크립트 배포 경로 | UC Volume(권장) · DBFS(레거시) · 워크스페이스 파일 중 하나 | STEP2 참조 |
| 7 | PAT 발급 가능 | 워크스페이스에서 개인 액세스 토큰 발급 허용 | STEP4 의 상류 인증 수단 |

**쿼터가 부족하면** Azure Support 로 `StandardNCADSA100v4Family` 증설을 요청하십시오.
요청 시 "Standard_NC24ads_A100_v4 × 1 (24 vCPU)" 이 필요하다고 명시하면 됩니다.

**A100 에서의 FP8 을 오해하지 마십시오.** A100(compute capability 8.0)은 native FP8 연산을
지원하지 않습니다. FP8 가중치는 `MarlinFP8ScaledMMLinearKernel`(W8A16 weight-only)로 처리되므로
얻는 이득은 **메모리 절감**(BF16 약 55.56 GB → FP8 약 30 GB)이고 **연산 가속은 없습니다.**

---

## 5. 반드시 지켜야 할 5가지

지키지 않으면 각각 아래와 같이 **실측으로 확인된 방식으로** 실패합니다.

| # | 지켜야 할 것 | 지키지 않으면 | 근거 |
|---|---|---|---|
| 1 | **격리된 venv 를 씁니다** | DBR 19 의 torch(2.12.0)와 vLLM 0.28.0 이 요구하는 torch(2.13.0)가 다릅니다. 런타임 패키지를 보존한 설치는 동작하지 않습니다 | STEP2 §3 |
| 2 | **실행 전 환경변수를 정리합니다** | `OPENSSL_FORCE_FIPS_MODE` 가 남아 있으면 프로세스가 `FATAL FIPS SELFTEST FAILURE` 로 즉시 종료(rc=134) | STEP2 §4 |
| 3 | **지정된 플래그 외에는 추가하지 않습니다** | 특히 `--gpu-memory-utilization` 은 **0.90 을 초과하지 마십시오.** 상위 값에서 OOM 전례 | STEP3 §4 |
| 4 | **`--host 0.0.0.0` 으로 기동합니다** | driver-proxy 는 드라이버 **사설 IP** 로 접속합니다. `127.0.0.1` 이면 엔드포인트 호출이 전부 **502** | STEP3 §1 · STEP4 |
| 5 | **클러스터 `autotermination_minutes: 0`** | 종료되면 엔드포인트는 계속 `READY` 인데 호출은 전부 실패합니다. 실측: 명령이 없는 상태에서 90분 후 `INACTIVITY` 종료. **추론 트래픽이 타이머를 갱신하는지는 미측정**이므로 갱신되지 않는다고 가정하십시오 | STEP2 §1 · STEP4 |

> **4번과 5번은 클러스터·serve 를 만들기 *전에* 정하십시오.** 나중에 바꾸면  
> `databricks clusters edit` 가 클러스터를 재시작하고, 그러면 `/local_disk0` 이 비워져  
> venv·가중치·serve 를 처음부터 다시 올려야 합니다.

> **`autotermination_minutes: 0` 이면 반드시 수동으로 종료하십시오.** 그렇지 않으면 과금이  
> 계속됩니다. `databricks clusters delete <CLUSTER_ID> --profile <PROFILE>` 을 사용하십시오  
> (`terminate` 서브커맨드는 존재하지 않습니다).

---

## 6. 따라가는 순서

**AI Gateway 를 붙이지 않으려면** STEP0 · STEP4 를 건너뜁니다. 그 경우 배포는 STEP1~3·STEP5 만 거칩니다.

**앱 하나로 쓰려면**(외부 시스템에 URL 을 열지 않고 사내 사용자가 화면으로 쓰는 경우) STEP4 대신
**[STEP4B](STEP4B_apps_agent.md)** 를 하십시오. 게이트웨이를 만들지 않으므로 STEP0 도 필요 없습니다 —
**serverless egress 가 제한된 워크스페이스에서 실증된 유일한 경로**입니다(STEP4B §10).

**AI Gateway 를 붙이려면** 아래 순서를 따릅니다. **STEP1 은 두 번 봅니다.** 가중치 확보 셀은 드라이버에서 돌아야 하므로
클러스터와 스크립트 스테이징(STEP2)이 먼저 끝나야 합니다.

```
① 게이트웨이 예정 시 : STEP0
   (작은 클러스터·5분·네트워크 차단 여부 판별·가중치 투입 전 결판)
      ↓
② STEP1 을 읽고 경로만 결정한다
   (A: HuggingFace 직접 / B: UC Volume)
      ↓
③ STEP2  클러스터 생성 · 스크립트 스테이징 · venv 빌드 · 사전 점검
      ↓
④ STEP1 로 돌아가 가중치 확보 셀을 실행한다
      ↓
⑤ STEP3  vLLM serve 기동 (반드시 --host 0.0.0.0)
      ↓
⑥ STEP4  AI Gateway 엔드포인트 등록
      ↓
⑦ STEP5  검증
```

**시간을 아끼는 요령**: STEP0 의 **도달성 사전 시험**은 가중치·venv 없이 임시 HTTP 서버 하나로
됩니다. ② 직후에 먼저 돌려 보십시오 — 네트워크가 막혀 있으면 ③④⑤⑥ 의 시간 전부가 헛수고입니다.

---

## 7. 문서 지도

**부록 (필요할 때만)**

| 문서 | 언제 봅니까 |
|---|---|
| [A1 모델 레지스트리 선택지](appendix/A1_model_registry_options.md) | "왜 Unity Catalog 등록 모델을 쓰지 않는가" 를 확인할 때 |
| [A2 문제 해결](appendix/A2_troubleshooting.md) | 증상이 났을 때 (증상 → 원인 → 조치) |
| [A3 스크립트·노트북 목록](appendix/A3_scripts_and_notebooks.md) | 어떤 파일이 무엇을 하는지 확인할 때 |
| [A4 serverless egress — 원인과 선택지 (조건부)](appendix/A4_serverless_egress_allowlist.md) | 엔드포인트 호출이 `CUSTOMER_UNAUTHORIZED … serverless network policy` 로 거부될 때 · **계정 관리자에게 전달** · 워크스페이스 FQDN 을 허용 목록에 넣는 방법은 **불가**하므로 선택지 표에서 고릅니다 |

**대안 경로**

| 문서 | 언제 봅니까 |
|---|---|
| [STEP4B Databricks Apps agent 앱](STEP4B_apps_agent.md) | 게이트웨이 없이 앱 하나로 쓸 때 · 게이트웨이가 egress 정책에 막혔을 때 |

**성능 보고서**

| 문서 | 내용 |
|---|---|
| [성능 보고서 개요](../performance/README.md) | 이 모델의 성능 특성과 한계 요약 |
| [P1 직접 대 게이트웨이](../performance/P1_direct_vs_gateway.md) | AI Gateway 계층이 더하는 비용 |
| [P2 agent 워크로드](../performance/P2_agent_workload.md) | 도구 호출·추론·장문·멀티턴 실측 |
| [P3 용량과 비용](../performance/P3_capacity_and_cost.md) | 포화점·처리량·백만 토큰 비용 |

---

준비가 되었으면 **AI Gateway 를 붙이지 않으려면** [STEP1_model_weights.md](STEP1_model_weights.md) 로,
**게이트웨이 예정이면** [STEP0_gateway_precheck.md](STEP0_gateway_precheck.md) 로 시작하십시오.
