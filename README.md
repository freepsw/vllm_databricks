# Qwen3.8-27B (FP8) × vLLM 0.28.0 × Azure Databricks

Deploy **Qwen/Qwen3.8-27B-FP8** with vLLM on a Databricks all-purpose GPU cluster (single A100 80GB),
then expose it either through **Databricks Model Serving / AI Gateway** or through a **Databricks App**
that calls the driver directly. All numbers in these documents are measured, not estimated.
Documentation is in Korean.

---

## 어디서 시작하나

### **[`qwen38-27b/README.md`](qwen38-27b/README.md) 하나만 열면 됩니다.**

그 문서가 **배포 트랙**과 **성능 트랙** 두 입구로 갈라 안내합니다.

| 트랙 | 무엇 | 누가 읽는가 | 진입점 |
|---|---|---|---|
| **① 배포** | 모델을 받아서 호출 가능한 엔드포인트까지 만드는 절차 | 인프라·플랫폼 담당자 | [`qwen38-27b/deployment/README.md`](qwen38-27b/deployment/README.md) |
| **② 성능** | 이 구성의 실측 성능·한계·실패 모드 | 아키텍트 · agent 개발자 · 용량 산정 | [`qwen38-27b/performance/README.md`](qwen38-27b/performance/README.md) |

배포를 준비 중이더라도 **성능 트랙 §1(한 장 요약)은** 먼저 읽으시기를 권합니다. 이 모델에는
클라이언트 코드를 바꿔야 하는 특성이 세 가지 있고, 모르고 배포하면 운영에서 발견하게 됩니다.

---

## 구성

```
qwen38-27b/
├── README.md              패키지 입구 (2트랙 안내 · 30초 요약)
├── deployment/            배포 트랙
│   ├── README.md          단계 지도 · 소요 시간
│   ├── STEP0 … STEP5      사전점검 → 가중치 → 클러스터 → serve → 게이트웨이 → 검증
│   ├── STEP4B             (STEP4 대안) Databricks Apps 에 agent 앱 배포 · 화면 캡처 31장
│   ├── appendix/          A1 모델 레지스트리 · A2 문제 해결 · A3 파일 목록 · A4 egress 정책
│   └── images/            STEP4B UI 화면 캡처
├── performance/           성능 트랙
│   ├── README.md          한 장 요약
│   ├── P1 · P2 · P3       직접호출 vs 게이트웨이 · agent 워크로드 · 용량과 비용
│   └── appendix/          B1 측정 방법 · B2 장문 · B3 실패 모드 · B4 증거 · B5 한계
├── notebooks/             워크스페이스에서 실행할 노트북 3개
├── scripts/               드라이버에서 실행할 스크립트 00~06
└── apps/agent_app/        STEP4B 앱 소스 (app.py · app.yaml)
```

노트북과 앱 소스는 Databricks 워크스페이스에서 **Workspace → Import → File** 로 가져옵니다.

> **구조가 바뀌었습니다.** 이전 버전의 `DEPLOYMENT_GUIDE.md` · `AI_GATEWAY_REGISTRATION_GUIDE.md`
> 는 `qwen38-27b/deployment/` 의 STEP0~STEP5 와 부록으로 나뉘어 대체되었습니다. 루트에 있던
> `scripts/` 와 노트북 2개도 `qwen38-27b/` 아래로 옮겼습니다.

---

## 검증 환경

Azure Databricks · DBR `19.x-gpu-ml-scala2.13` · `Standard_NC24ads_A100_v4`
(A100 80GB ×1, single-node) · vLLM 0.28.0 / torch 2.13.0+cu130 · koreacentral

**참고 — A100 에서의 FP8**: A100(compute capability 8.0)은 native FP8 연산을 지원하지 않습니다.
FP8 가중치는 `MarlinFP8ScaledMMLinearKernel`(W8A16 weight-only)로 처리되며, 얻는 이득은
**메모리 절감**(BF16 약 55.6 GB → FP8 약 30 GB)이고 **연산 가속은 없습니다.**

---

## 시작 전에 정해야 하는 2가지

나중에 바꾸면 클러스터나 serve 를 다시 올려야 합니다.

| 결정 | 위치 | 권장 |
|---|---|---|
| `autotermination_minutes` | `qwen38-27b/scripts/01_cluster.json` (**클러스터 생성 전**) | **`0`** — 자동 종료는 *마지막 명령 실행* 기준이라 추론 트래픽으로 갱신되지 않습니다. 종료되면 엔드포인트는 `READY` 인데 호출이 전부 실패합니다 |
| `--host` | `deployment/STEP3_vllm_serve.md` §1 | **`0.0.0.0`** — driver-proxy 는 드라이버 사설 IP 로 접속하므로 loopback 이면 전부 502. 처음부터 지정하면 재기동을 건너뜁니다 |

---

## ⚠️ 비용 주의

`Standard_NC24ads_A100_v4` 는 koreacentral 온디맨드 **시간당 약 4.96 USD** 입니다(Azure Retail
Prices API 조회값, VM 요금만 · **Databricks DBU 요금 별도**). `autotermination_minutes: 0` 으로
상시 운영하면 VM 만 월 약 3,570 USD(30일=720시간 기준)입니다.

**시험이 끝나면 반드시 클러스터를 종료하십시오.**

```bash
databricks clusters delete <CLUSTER_ID> --profile <PROFILE>
```

> `terminate` 서브커맨드는 없습니다. `delete` 가 종료(terminate)이고,
> 완전 삭제는 `permanent-delete` 입니다.

---

## 라이선스·면책

이 저장소의 문서와 스크립트는 특정 환경(위 「검증 환경」)에서 측정한 결과를 재현하기 위한
참고 자료입니다. 모델 가중치의 라이선스는 [Qwen/Qwen3.8-27B-FP8](https://huggingface.co/Qwen/Qwen3.8-27B-FP8)
의 조건을 따릅니다. 프로덕션 투입 전 자신의 환경에서 검증하십시오.
