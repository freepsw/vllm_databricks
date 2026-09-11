# 부록 A1 · 모델 레지스트리 — 이 경로가 쓰는 것과 쓰지 않는 것

**이 부록이 답하는 질문**: "Databricks 에 모델을 올린다면 Unity Catalog 모델 레지스트리에 등록하는 것 아닌가?"

결론부터 말씀드리면 **이 배포 경로는 Unity Catalog 의 registered model(MLflow 모델 레지스트리)을
사용하지 않습니다.** 대신 두 가지가 그 역할을 나눠 맡습니다. 아래에서 무엇이 무엇을 대신하는지,
그리고 고객 조직의 거버넌스 요구가 registered model 을 요구할 경우 무엇을 확인해야 하는지 정리합니다.

---

> **serverless egress 가 제한된 환경에서 registered model + custom model serving 으로 전환할 경우**:
> 그 경로는 컨테이너 빌드 단계가 network policy 의 통제를 받습니다(거부는 `network_source_type = ML Build` 로
> 기록됩니다). 빌드가 PyPI·HuggingFace 등 외부 의존성을 받아야 하면 해당 도메인이 허용 목록에 있어야 하고,
> 없으면 엔드포인트가 READY 에 도달하지 못합니다. 이 전환은 **미검증**이며 판단 근거는
> [부록 A4](A4_serverless_egress_allowlist.md) ⛔ 블록에 있습니다.

## 1. 이 경로에서 "등록" 에 해당하는 것 두 가지

| 등록 대상 | 어디에 등록되는가 | 무엇을 통제하는가 | 해당 단계 |
|---|---|---|---|
| **모델 가중치** (약 30 GB · safetensors 66개) | **Unity Catalog Volume** — 예: `/Volumes/<catalog>/<schema>/<volume>/Qwen3.8-27B-FP8` | UC 권한으로 누가 가중치를 읽을 수 있는지, 어느 버전이 배포에 쓰이는지 | [../STEP1_model_weights.md](../STEP1_model_weights.md) 경로 B |
| **호출 가능한 모델** | **AI Gateway serving endpoint** — 예: `qwen38-27b-vllm` | 엔드포인트 이름·호출 권한·rate limit·사용량 추적 | [../STEP4_ai_gateway.md](../STEP4_ai_gateway.md) |

> ⚠️ **UC Volume 경로 자체는 실행 검증되지 않았습니다.** 검증 환경에 metastore 가 없어
> `/Volumes` 에서의 스테이징을 돌려보지 못했습니다.
> 아래 표는 **이 경로를 쓸 경우 무엇이 무엇을 대신하는가**를 설명하는 것이며, 경로의 동작 확인은
> 고객 환경에서 [../STEP1_model_weights.md](../STEP1_model_weights.md) 의 검증 절차로 하셔야 합니다.
> 실행 검증된 가중치 확보 경로는 HuggingFace 직접 다운로드 하나입니다.

고객이 최종적으로 받는 것은 **엔드포인트 이름 하나**입니다. 클라이언트는
`https://<workspace>/serving-endpoints/<엔드포인트 이름>/invocations` 만 알면 되고,
가중치가 어디 있는지도 어느 클러스터가 서빙하는지도 알 필요가 없습니다.

```
[가중치 등록]                        [모델 등록]
UC Volume                            AI Gateway endpoint
/Volumes/cat/sch/vol/Qwen3.8-27B-FP8 ──▶ qwen38-27b-vllm
  · UC 권한으로 읽기 통제                · 호출 권한을 사용자·그룹별로 부여
  · 배포마다 같은 경로를 참조             · rate limit · 사용량 추적
  · 폐쇄망에서도 재현 가능                · cluster_id 를 클라이언트에 노출하지 않음
```

## 2. MLflow / registered model 을 쓰지 않는 이유

**AI Gateway 의 external model(`provider: custom`)은 MLflow 모델을 입력으로 받지 않습니다.**
이 방식이 필요로 하는 것은 **HTTP 로 접근 가능한 OpenAI 호환 엔드포인트 URL 과 인증 토큰**뿐입니다.
이 배포에서 그 URL 은 클러스터 드라이버에서 도는 vLLM 프로세스를 가리키는 driver-proxy 주소입니다.
따라서 "모델을 로깅하고 레지스트리에 등록한 뒤 서빙" 이라는 단계가 경로 안에 존재하지 않습니다.

**vLLM 프로세스를 직접 통제해야 했기 때문입니다.** 이 배포는 다음 플래그 조합에 의존합니다.

| 플래그 | 없으면 |
|---|---|
| `--reasoning-parser qwen3` | `<think>` 가 본문에 그대로 섞여 나옵니다 |
| `--enable-auto-tool-choice --tool-call-parser qwen3_xml` | `tools` 를 담은 요청이 400 으로 거절됩니다 |
| `--kv-cache-dtype fp8` | KV 캐시 용량이 줄어 동시성 여유가 작아집니다 |
| `--max-model-len 131072` · `--max-num-seqs 32` · `--max-num-batched-tokens 7840` | 컨텍스트 상한과 포화점이 달라집니다 |
| `--language-model-only` | 이 배포가 검증한 구성과 달라집니다 |

이 플래그들은 **vLLM 명령줄에서만 지정할 수 있습니다.** 서버 프로세스를 직접 띄우는 구성이라야
지정이 가능하고, 그래서 all-purpose 클러스터에서 vLLM 을 실행하는 형태가 되었습니다.

## 3. 그러면 무엇을 포기하는가 — 정직한 비교

registered model 을 쓰지 않으므로 다음은 이 경로에서 **자동으로 얻어지지 않습니다.**

| registered model 이 주는 것 | 이 경로에서의 상태 | 대체 수단 |
|---|---|---|
| 모델 버전 관리 (`version 1`, `2`, …) | 없음 | UC Volume 안에서 **디렉터리 이름으로 버전을 구분**하십시오. 예: `Qwen3.8-27B-FP8-20260910/` |
| 모델 계보(lineage) 추적 | 없음 | UC Volume 접근 감사 로그 + 엔드포인트 사용량 추적 |
| 별칭(alias) 로 배포 승격 (`@champion`) | 없음 | 엔드포인트를 두 개 만들고(예: `-staging` / `-prod`) 상류 클러스터를 바꿔 전환 |
| 모델 서명(signature)·입출력 스키마 검증 | 없음 | 클라이언트가 자체 검증을 해야 합니다 — 게이트웨이는 **알 수 없는 필드를 조용히 무시**하고 200 을 반환합니다 |
| 서버 라이프사이클 관리 | 없음 — **클러스터가 죽으면 엔드포인트가 죽습니다** | `autotermination_minutes: 0` 필수 · 헬스체크는 상태가 아니라 실제 호출로 |

**마지막 항목이 운영상 가장 중요합니다.** 이 경로에서 엔드포인트는 상류 클러스터에 종속됩니다.
엔드포인트 상태가 `READY` 라는 것이 상류 vLLM 이 살아 있다는 뜻이 아니며, 실측에서 상류가 죽은
45초 동안에도 엔드포인트는 계속 `READY` 였습니다. 자세한 내용은
[부록 A2 문제 해결](A2_troubleshooting.md) 과 [성능 부록](../../performance/appendix/B3_failure_modes.md) 를 보십시오.

## 4. 조직 거버넌스가 registered model 등록을 요구한다면

이 프로젝트는 **registered model 기반 경로를 검증하지 않았습니다.** 따라서 그 경로가 이 모델·이 구성에서 동작하는지에 대해 이 문서는
어떤 주장도 하지 않습니다. 검토가 필요하면 아래 항목을 Databricks 담당자와 확인하십시오.

- 이 모델(27B · 사전양자화 FP8 · 하이브리드 어텐션 64층)이 대상 서빙 방식에서 지원되는지
- 위 §2 표의 vLLM 플래그 5종에 해당하는 동작(추론 파서 · 도구 파서 · FP8 KV 캐시 · 131K 컨텍스트)을
  그 방식에서 어떻게 얻을 수 있는지 — **이것이 핵심 확인 항목입니다.** 얻을 수 없다면 응답 형식과
  성능 특성이 이 문서의 실측값과 달라지므로 성능 보고서를 그대로 적용할 수 없습니다
- 리전(예: Azure koreacentral)에서 필요한 GPU 서빙 용량이 제공되는지
- 비용 구조가 all-purpose 클러스터 방식과 어떻게 다른지

**중간 대안**: 거버넌스 요구가 "UC 안에 자산이 등록되어 있어야 한다" 는 수준이라면
§1 의 UC Volume 등록으로 충족되는 경우가 많습니다. "registered model 객체가 있어야 한다" 는
수준이라면 위 확인이 선행되어야 합니다. 어느 쪽인지 먼저 확정하는 것이 시간을 아낍니다.

## 5. 이 부록의 요약

- 이 경로는 **UC Volume(가중치) + AI Gateway 엔드포인트(호출 지점)** 로 등록을 구성합니다
- MLflow registered model 은 **경로에 없으며**, 그 이유는 external model 방식이 MLflow 모델이 아니라
  **HTTP 엔드포인트를 입력으로 받기** 때문입니다
- 버전 관리·별칭 승격은 **디렉터리 이름과 엔드포인트 분리**로 직접 운영해야 합니다
- **엔드포인트는 상류 클러스터에 종속**입니다 — 이것이 이 경로의 가장 큰 운영 부담입니다
- registered model 경로는 **이 프로젝트에서 검증하지 않았습니다**

---

**다음**: [../STEP1_model_weights.md](../STEP1_model_weights.md) · [부록 A2 문제 해결](A2_troubleshooting.md)
