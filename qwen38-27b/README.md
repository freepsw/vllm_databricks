# Qwen3.8-27B × Databricks AI Gateway — 고객 전달 패키지

**모델**: `Qwen/Qwen3.8-27B` 사전양자화 FP8 (약 30 GB)  
**구성**: vLLM 0.28.0 (all-purpose 클러스터 드라이버) + AI Gateway external model  
**검증 환경**: Azure Databricks · `Standard_NC24ads_A100_v4` (A100 80GB × 1) · DBR 19.5 GPU ML  
**성능 측정**: 2026-09-09 · 유효 요청 1,553건

---

## 이 패키지는 두 개의 독립된 트랙입니다

| 트랙 | 무엇 | 누가 읽는가 | 진입점 |
|---|---|---|---|
| **① 배포** | 모델을 받아서 호출 가능한 엔드포인트까지 만드는 절차 | 인프라·플랫폼 담당자 | **[deployment/README.md](deployment/README.md)** |
| **② 성능** | 이 구성의 실측 성능·한계·실패 모드 | 아키텍트 · agent 개발자 · 용량 산정 담당자 | **[performance/README.md](performance/README.md)** |

**진입점은 독립이지만**, 배포를 준비 중인 담당자도 **성능 트랙 §1(한 장 요약)은** 반드시 읽으시기를 권합니다.
이 모델에는 클라이언트 코드를 바꿔야 하는 특성이 세 가지 있고, 그것을 모르고 배포하면 운영에서 발견하게 됩니다.

---

## 30초 요약

**이 구성이 하는 일**: A100 80GB 한 장에 27B 모델을 FP8 으로 올려 **OpenAI 호환 엔드포인트 하나**로  
제공합니다. 컨텍스트 131K · 도구 호출 · 구조화 출력 · 스트리밍을 지원하고, AI Gateway 가 인증 ·  
사용량 추적 · rate limit 을 담당합니다.

**감당하는 부하**: 권장 운영점 **동시성 32 · 출력 256토큰 고정** 조건에서 **3.274 req/s**  
**(196.4 req/분)** · **838 출력토큰/초** · 백만 출력토큰당 **1.64 USD**. 그 이상 동시성에서는  
처리량이 정체하는데 p95 가 2배가 됩니다.

**AI Gateway 를 붙이는 비용**: 요청당 **약 +0.05초 (상대 1.2 %)** · **처리량 손실 없음**.

**배포 소요**: **vLLM 응답까지 약 14분** (가중치 다운로드 대역폭에 따라 더 걸릴 수 있음) ·  
**엔드포인트 종단까지 약 39분** (계산식은 아래 「배포 트랙」에 노출했습니다).

**반드시 알아야 할 세 가지**  

1. **추론 폭주** — 이 모델은 추론 모델이고, 어려운 질문에서 `max_tokens` 를 전량 추론에 쓰고
   **본문 0자 · HTTP 200** 으로 끝납니다. 클라이언트가 `finish_reason == "length"` 를 실패로
   처리해야 합니다.
2. **TTFT ≠ 체감 지연** — 첫 chunk 는 0.271초에 오지만 사용자가 읽을 첫 글자는 **3.549초**입니다.
3. **도구 호출 강제 불가** — `tool_choice` 로 특정 함수 지정과 `"required"` 는 무시됩니다.
   `"required"` 는 `finish_reason` 까지 오염시킵니다.

셋 다 [performance/README.md §1.3](performance/README.md)에 근거와 함께 있습니다.

---

## 패키지 구성

```
qwen38-27b/
├── README.md                                 ← 진입점 (지금 보는 파일)
│
├── deployment/
│   ├── README.md                             ← 배포 트랙 진입점
│   ├── STEP0_gateway_precheck.md             게이트웨이 예정 시 5분 도달성 사전 시험
│   ├── STEP1_model_weights.md                가중치 확보 (HuggingFace 또는 UC Volume)
│   ├── STEP2_cluster_and_runtime.md          클러스터 생성 · 스크립트 스테이징 · venv 빌드
│   ├── STEP3_vllm_serve.md                   vLLM 기동 · 기동 정상 판정 · 분리 실행
│   ├── STEP4_ai_gateway.md                   PAT · secret · 엔드포인트 생성
│   ├── STEP4B_apps_agent.md                  (STEP4 대안) Databricks Apps 에 agent 앱 — UI 배포 화면 캡처 포함
│   ├── STEP5_test.md                         3단계 검증 사다리 (스모크 → 기능 → 부하)
│   ├── appendix/
│   │   ├── A1_model_registry_options.md      ⚠️ 모델 레지스트리를 쓰지 않는 이유와 대안
│   │   ├── A2_troubleshooting.md             증상 기준 문제 해결
│   │   ├── A3_scripts_and_notebooks.md       제공 파일 목록과 역할
│   │   └── A4_serverless_egress_allowlist.md (조건부) 계정 관리자 전달용 — 원인·선택지
│   └── images/                               STEP4B UI 배포 화면 캡처 31장 (문서에서 참조)
│
├── performance/
│   ├── README.md                             ← 성능 트랙 진입점
│   ├── P1_direct_vs_gateway.md               vLLM 직접 대 AI Gateway 경유 비교
│   ├── P2_agent_workload.md                  agent 구현자 필독 (추론 예산 · 도구 · 멀티턴)
│   ├── P3_capacity_and_cost.md               용량 산정 절차 · 비용 모델 · rate limit
│   └── appendix/
│       ├── B1_method_and_environment.md      측정 방법 · 통제한 오염 요인
│       ├── B2_long_context_rag.md            장문 컨텍스트 · prefix 캐시 · RAG 설계
│       ├── B3_failure_modes.md               실패 모드 카탈로그 · 운영 대응
│       ├── B4_evidence_index.md              원시 데이터 색인 · 재현 코드
│       └── B5_limitations.md                 ⚠️ 측정하지 않은 것 · 재측정 권장 항목
│
├── scripts/
│   ├── 00_check_prereq.py                    배포 전 환경 점검
│   ├── 01_cluster.json                       클러스터 생성 스펙
│   ├── 02_build_venv.py                      vLLM 0.28.0 격리 venv 빌드
│   ├── 03_stage_weights.py                   가중치 확보 (HuggingFace 또는 UC Volume)
│   ├── 04_serve.sh                           vLLM serve 기동 + 기동 로그 점검
│   ├── 05_validate.py                        빠른 검증 (품질·동시성·장문)
│   └── 06_soak.py                            장시간 안정성 시험 (선택)
│
├── notebooks/
│   ├── 07_deploy_notebook.py                 배포 작업 (셀 단위 실행)
│   ├── notebook_gateway_register.py          AI Gateway 엔드포인트 등록 (셀 6개)
│   └── 08_step4b_apps_agent.py               STEP4B 검증 (T1~T5 + 앱 결과 교차 검증)
│
└── apps/
    └── agent_app/                            Databricks Apps agent 앱 (STEP4B)
        ├── app.py                            agent 루프 · 도구 2개 · 자기시험
        └── app.yaml                          런타임 설정 (클러스터ID 를 바꿔 쓰십시오)
```

---

## 역할별 읽는 순서

| 역할 | 순서 |
|---|---|
| **의사결정자** | [performance/README.md §1](performance/README.md) → [P3 §비용](performance/P3_capacity_and_cost.md) → 끝 (10분) |
| **인프라 담당자** | [deployment/README.md](deployment/README.md) → STEP1~5 순서대로 · 게이트웨이 예정 시 STEP0 먼저 → [A2 문제 해결](deployment/appendix/A2_troubleshooting.md) |
| **agent 개발자** | [P2](performance/P2_agent_workload.md) 전체 → [B3 실패 모드](performance/appendix/B3_failure_modes.md) → [STEP5 검증](deployment/STEP5_test.md) |
| **RAG 설계자** | [B2 장문·RAG](performance/appendix/B2_long_context_rag.md) → [P3 §용량 산정](performance/P3_capacity_and_cost.md) |
| **아키텍트** | [P1 직접 대 게이트웨이](performance/P1_direct_vs_gateway.md) → [A1 레지스트리 선택지](deployment/appendix/A1_model_registry_options.md) → [B5 한계](performance/appendix/B5_limitations.md) |
| **운영팀** | [B3 실패 모드](performance/appendix/B3_failure_modes.md) → [A2 문제 해결](deployment/appendix/A2_troubleshooting.md) |

---

## 전제 조건 요약

배포를 시작하기 전에 아래가 충족되는지 확인하십시오. 상세는
[deployment/README.md](deployment/README.md)에 있습니다.

- [ ] Azure GPU 쿼터 — `Standard_NC24ads_A100_v4` 1대 분량
- [ ] DBR 19.5 GPU ML 을 쓸 수 있는 워크스페이스
- [ ] 클러스터에서 HuggingFace 로의 egress **또는** 가중치를 담은 UC Volume
- [ ] **serverless egress 가 제한된 워크스페이스가 아닌지** — 제한되어 있으면 게이트웨이 구성
      자체가 불가능하며 계정 관리자 조치가 선행되어야 합니다
      ([deployment/STEP0_gateway_precheck.md](deployment/STEP0_gateway_precheck.md))
- [ ] 클러스터 `autotermination_minutes: 0` 으로 설정할 수 있는 권한 — **자동 종료가 엔드포인트를
      죽입니다**

---

## 신뢰 범위

이 패키지의 성능 수치는 **단일 리전 · 단일 노드 · 약 1시간 36분 측정**의 결과입니다.
GPU 여러 장으로의 확장 선형성, 출력 길이별 비용 일반화, 장문 정확성의 한계는 측정되지 않았습니다.
**측정하지 않은 항목의 전체 목록과 고객 환경에서 재측정을 권장하는 8개 항목**은
[performance/appendix/B5_limitations.md](performance/appendix/B5_limitations.md)에 있습니다.

배포 절차는 실제 실행으로 검증되었습니다. 다만:

- **UC Volume 경로는 미실행 검증**입니다(검증 환경에 Unity Catalog metastore 미구성).
  DBFS 레거시 또는 워크스페이스 파일로 권장합니다. UC 경로는 고객 환경에서 first-run 검증 필요.
- 고객 워크스페이스의 네트워크 정책 · UC 구성 · 권한 모델은 환경마다 달라
  [A2 문제 해결](deployment/appendix/A2_troubleshooting.md)의 증상별 항목을 함께 참고하시기 바랍니다.

---

준비가 되었으면 **배포 경로**는 [deployment/README.md](deployment/README.md) 로,
**성능만 검토**하려면 [performance/README.md](performance/README.md) 로 시작하십시오.
