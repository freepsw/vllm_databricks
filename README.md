# Qwen3.8-27B (FP8) × vLLM 0.28.0 × Azure Databricks

Deploy **Qwen/Qwen3.8-27B-FP8** with vLLM on a Databricks all-purpose GPU cluster (single A100 80GB),
then expose it through **Databricks Model Serving / AI Gateway** as an OpenAI-compatible endpoint.
All numbers in these documents are measured, not estimated. Documentation is in Korean.

---

## 이 저장소는 무엇인가

단일 A100 80GB 클러스터에 Qwen3.8-27B(FP8)을 vLLM 으로 올리고, 이를 Databricks 서빙
엔드포인트로 감싸 **표준 OpenAI 호환 API · AI Playground · rate limit · usage tracking** 까지
연결하는 전과정 가이드입니다.

**검증 환경**: Azure Databricks · DBR `19.x-gpu-ml-scala2.13` · `Standard_NC24ads_A100_v4`
(A100 80GB ×1, single-node) · vLLM 0.28.0 / torch 2.13.0+cu130 · koreacentral

---

## 어디서 시작하나

### **[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) 하나만 열면 됩니다.**

위에서부터 읽어 내려가면 §6 에서 게이트웨이 문서로 넘기고, 그 문서가 끝까지 데려갑니다.

| 순서 | 문서 | 내용 |
|---|---|---|
| 1 | `DEPLOYMENT_GUIDE.md` 상단 「전체 흐름 요약」 | 전체 지도 + **시작 전 결정 2가지** |
| 2 | 같은 문서 §1 → §5 | 클러스터 생성 → 스크립트 배포 → venv·가중치 → vLLM serve → 검증 |
| 3 | 같은 문서 §6 | 게이트웨이 문서로 인계 |
| 4 | [`AI_GATEWAY_REGISTRATION_GUIDE.md`](AI_GATEWAY_REGISTRATION_GUIDE.md) §0 → §8 | 전제 점검 → PAT → 엔드포인트 생성 → 검증 → 사용 → 운영 |

> ⚠️ **단, §1 을 시작하기 전에 딱 한 곳을 먼저 보십시오** —
> `AI_GATEWAY_REGISTRATION_GUIDE.md` **§0.2 「도달성 사전 시험」**.
> 5분이면 끝나고, Private Link · egress 정책으로 막히는 환경인지 판별합니다.
> 여기서 막히면 30GB 가중치와 A100 시간을 투입한 뒤에 되돌아와야 하므로,
> 순서를 거스를 가치가 있는 유일한 예외입니다.

---

## 파일 구성

```
DEPLOYMENT_GUIDE.md                  ← 입구 문서 (§1~§5 배포, §6 에서 인계)
AI_GATEWAY_REGISTRATION_GUIDE.md     ← AI Gateway 연결·테스트·운영

07_deploy_notebook.py                ← 실행 노트북 ①  배포 (가이드 §2.4~§5.2)
notebook_gateway_register.py         ← 실행 노트북 ②  게이트웨이 (셀 6개, 외부 파일 의존 없음)

scripts/
  00_check_prereq.py                 드라이버 환경 점검 (GPU·디스크·도구·네트워크)
  01_cluster.json                    클러스터 생성 스펙
  02_build_venv.py                   vLLM 0.28.0 격리 venv 빌드
  03_stage_weights.py                가중치 확보 (HuggingFace 또는 UC Volume)
  04_serve.sh                        vLLM serve 기동 + 기동 로그 점검
  05_validate.py                     빠른 검증 8항목 (품질·동시성·장문)
  06_soak.py                         장시간 안정성 시험 (선택)
```

노트북은 Databricks 워크스페이스에서 **Workspace → Import → File** 로 가져옵니다.

---

## 시작 전에 정해야 하는 2가지

나중에 바꾸면 클러스터나 serve 를 다시 올려야 합니다.

| 결정 | 위치 | 게이트웨이까지 갈 예정이면 |
|---|---|---|
| `autotermination_minutes` | `scripts/01_cluster.json` (**클러스터 생성 전**) | **`0`** — 자동 종료는 *마지막 명령 실행* 기준이라 추론 트래픽으로 갱신되지 않습니다. 종료되면 엔드포인트는 `READY` 인데 호출이 전부 실패합니다 |
| `--host` | `DEPLOYMENT_GUIDE.md` §4.1 결정 표 | **`0.0.0.0`** — driver-proxy 는 드라이버 사설 IP 로 접속하므로 loopback 이면 전부 502. 처음부터 지정하면 게이트웨이 가이드 §2(재기동)를 건너뜁니다 |

---

## 소요 시간 (실측)

| 구간 | 시간 |
|---|---|
| 클러스터 생성·기동 | 약 6분 |
| 스크립트 배포 | 약 5분 |
| venv 빌드 | 73~76초 |
| 가중치 스테이징 (30GB) | 72~75초 — **네트워크에 따라 크게 다릅니다** |
| vLLM serve (`/health` 200) | 약 319초 |
| 빠른 검증 8항목 | 약 10분 |
| AI Gateway 연결·테스트 | 약 10분 |
| **vLLM 배포까지** | **약 25~30분** |
| **AI Gateway 연결까지** | **약 40분** |
| (선택) 8시간 안정성 시험 | 8시간 |

---

## 성능 기준선 (실측)

| 항목 | 값 |
|---|---|
| KV 캐시 용량 (`--kv-cache-dtype fp8`) | 약 1,188,000 토큰 (**실행마다 ±5% 변동**) |
| 131K 컨텍스트 동시성 | 약 9.1x |
| VRAM (131K 프로필) | idle 약 69 GiB · 부하 peak 약 71 GiB (총 80 GiB) |
| 최대 실증 컨텍스트 (262K 프로필) | **249,123 토큰** 처리 성공 (약 151초) |
| 동시성 22 정상상태 | 약 2.85 rps · p50 약 8.7초 · p95 약 8.9초 |
| 8시간 연속 부하 | **82,129 요청 · 오류 0** · VRAM drift 0.122% |

> KV 캐시·동시성 값은 **정확한 일치로 정상 여부를 판정하지 마십시오.** 같은 플래그로 다시 띄워도
> 기동 시점의 여유 VRAM 에 따라 약 4% 변동합니다. 판정 기준이 되는 결정론적 항목 4개는
> `DEPLOYMENT_GUIDE.md` §4.4 에 있습니다.

**참고: A100 에서의 FP8** — A100(compute capability 8.0)은 native FP8 연산을 지원하지 않습니다.
FP8 가중치는 `MarlinFP8ScaledMMLinearKernel`(W8A16 weight-only)로 처리되며, 얻는 이득은
**메모리 절감**(BF16 약 55.6 GB → FP8 약 30 GB)이고 **연산 가속은 없습니다.**

---

## 검증 상태

| 구간 | 검증 |
|---|---|
| 배포 (§1~§5) | 최초 배포 + **처음부터 전체 재현 2회** |
| 8시간 안정성 | 완주 1회 (82,129 요청 · 오류 0) |
| AI Gateway 연결 | 종단 12항목 PASS + **Databricks 노트북 UI `Run all` 2회 + 멱등성 재실행** |
| 스크립트 배포 3경로 | UC Volume · DBFS · 워크스페이스 파일 |

### 고객 환경에서 직접 확인이 필요한 것

아래는 검증 환경에서 확인할 수 없었던 항목입니다. 각 가이드의 부록에 명시되어 있습니다.

- Private Link · public access 차단 워크스페이스에서의 도달성 → **`AI_GATEWAY_REGISTRATION_GUIDE.md` §0.2 로 먼저 판별하십시오**
- serverless egress 제한 정책 하에서의 동작
- 서비스 프린시펄 PAT 로 driver-proxy 통과
- PAT **auto-scoping**(30일 관찰 후 scope 자동 축소)이 상류 인증을 실제로 깨뜨리는지
  → 가이드 §3 은 발급 시 끄도록 안내합니다
- Unity Catalog Volume 배포 경로 · payload 로깅 · usage 기록 조회 (검증 환경에 metastore 없음)
- DBFS 가 차단된 워크스페이스의 오류 문구
- AWS · GCP (Azure 에서만 검증)

---

## 비용 주의

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
