# 부록 B4 · 증거 색인 — 원시 데이터와 재현 절차

**이 부록이 답하는 질문**: "이 보고서의 숫자를 제가 직접 확인할 수 있습니까?"

**할 수 있습니다.** 모든 요청이 타임스탬프와 함께 append-only JSONL 로 남아 있고, 집계는
스크립트 하나로 재현됩니다. 이 부록은 어느 수치가 어느 파일에서 나왔는지의 색인입니다.

---

## 1. 원시 데이터 위치

```
perf_20260909/                 ← 원시 데이터 세트 (문서와 별도로 전달됩니다)
├── README.md          측정 환경 · 무효 구간 · 배제 규칙
├── *.jsonl            원시 요청 기록 11개 (요청 1건 = 1줄)
├── summary.json       집계 결과
├── design/            영역별 측정 설계 5개
└── analysis/          영역별 상세 분석 2개
```

**immutable** — 측정 당시 기록된 원본이며 수정되지 않았습니다.

> **원시 데이터와 집계 스크립트는 이 패키지에 포함되지 않습니다.** 용량이 크기 때문입니다.
> 수치를 직접 확인하실 필요가 있으면 제공자에게 **원시 데이터 세트 `perf_20260909/` 와
> 집계 스크립트 2개(`perf_analyze.py` · `perf_agent_campaign.py`)** 를 요청하십시오. 아래 절의
> 코드는 그 세트를 현재 디렉터리에 풀어 놓은 상태를 전제로 합니다.

---

## 2. 파일별 색인

| 파일 | 요청 | HTTP 200 | 의도된 실패 | 무효 | 어느 결과의 근거인가 |
|---|---|---|---|---|---|
| `probe.jsonl` | 8 | 8 | — | — | 응답 필드명 · 부가 파라미터 통과 확인 ([B1 §6](B1_method_and_environment.md)) |
| `latency.jsonl` | 117 | 117 | — | — | 체감 지연 3분할 · `max_tokens` 계단 · **게이트웨이 오버헤드**([P1](../P1_direct_vs_gateway.md)) |
| `latency2.jsonl` | 114 | **13** | — | **101** | 난이도별 추론 길이. 재기동으로 대부분 무효 |
| `latency3.jsonl` | 32 | 32 | — | — | **추론 폭주**([P2](../P2_agent_workload.md)) · 출력 고정 오버헤드([P1](../P1_direct_vs_gateway.md)) |
| `agent.jsonl` | 84 | 84 | — | — | 도구 호출 · 멀티턴 · 구조화 출력([P2](../P2_agent_workload.md)) |
| `tools2.jsonl` | 31 | 31 | — | — | `tool_choice` 원인 규명 7조건([P1](../P1_direct_vs_gateway.md)) |
| `longctx.jsonl` | 64 | 28 | **4** (400) | **32** | 장문 곡선 · prefix 캐시 · 바늘 · 상한([B2](B2_long_context_rag.md)) |
| `throughput.jsonl` | 585 | 585 | — | — | 동시성 스윕 + `/metrics` 98 스냅샷([P3](../P3_capacity_and_cost.md)) |
| `reliability.jsonl` | 241 | 233 | **8** (400) | — | 입력 거절 · 조용한 실패 경계([B3](B3_failure_modes.md)) |
| `ratelimit.jsonl` | 410 | 142 | **268** (429) | — | 429 시험 + 회복 관측([P3](../P3_capacity_and_cost.md) · [B3](B3_failure_modes.md)) |
| `preflight.jsonl` | — | — | — | — | 모듈별 상류 생존 확인 기록 |
| **합계** | **1,686** | **1,273** | **280** | **133** | |

**「의도된 실패」는 유효한 측정입니다.** 실패 자체가 측정 목적인 요청(컨텍스트 초과 400, rate limit
429)이므로 집계에 포함됩니다. **「무효」만 배제**합니다.

- 유효 요청 = 1,686 − 133 = **1,553건**
- 무효 133건 = `latency2` 101건 + `longctx` 32건 — 전부 서버 재기동 구간의 502 입니다
  ([B1 §5](B1_method_and_environment.md))

---

## 3. 레코드 구조

한 줄이 요청 하나입니다. 주요 필드:

| 필드 | 의미 |
|---|---|
| `label` | 측정 항목 이름 (예: `L21_gateway_fixed`) — 이 값으로 필터해 결과를 찾습니다 |
| `ts` | 요청 시각 (ISO 8601 · UTC) — 무효 구간 배제에 쓰입니다 |
| `url_kind` | `gateway` 또는 직접 경로 — 게이트웨이 비교의 기준 |
| `latency_s` | 총 지연 (초) |
| `http_status` | HTTP 상태 코드 |
| `ttft_s` · `ttfrt_s` · `ttfct_s` | 첫 chunk · 첫 추론 델타 · **첫 본문 델타**까지의 시간 (스트리밍) |
| `prompt_tokens` · `completion_tokens` · `reasoning_tokens` | 토큰 수 (`completion_tokens` 는 추론 포함) |
| `finish_reason` | `stop` · `length` · `tool_calls` — **`length` 가 추론 폭주 신호** |
| `content` · `reasoning` | 응답 본문과 추론 내용 |
| `resp_headers` | 응답 헤더 (인증 헤더는 기록되지 않았습니다) |

**토큰·인증 정보는 기록되지 않았습니다.** `resp_headers` 에는 `content-type` · `server` ·
`x-databricks-org-id` · `x-request-id` 만 들어 있습니다.

---

## 4. 주요 수치를 원시에서 확인하는 방법

### 4.1 게이트웨이 오버헤드 (출력 200토큰 고정)

```bash
python3 - <<'PY'
import json, math, statistics
def pct(v, q):
    v = sorted(v); return v[max(0, math.ceil(q*len(v))-1)]
rows = [json.loads(l) for l in open('perf_20260909/latency3.jsonl') if l.strip()]
g = [r['latency_s'] for r in rows if r.get('label')=='L21_gateway_fixed' and r.get('http_status')==200]
d = [r['latency_s'] for r in rows if r.get('label')=='L21_direct_fixed'  and r.get('http_status')==200]
de = [g[i]-d[i] for i in range(min(len(g), len(d)))]
print('gateway p50 %.4f  direct p50 %.4f' % (pct(g,.5), pct(d,.5)))
print('pair delta p50 %.4f  p95 %.4f  positives %d/%d' % (pct(de,.5), pct(de,.95),
      sum(1 for x in de if x>0), len(de)))
PY
```

기대 출력: `gateway p50 4.5510  direct p50 4.4805` · `pair delta p50 0.0526  p95 0.2211  positives 12/12`

### 4.2 추론 폭주

```bash
python3 - <<'PY'
import json
FILES = ['latency3.jsonl', 'latency2.jsonl']
LABELS = {'L20_runaway_reasoning', 'L10_reasoning_by_tier'}
for f in FILES:
    for line in open('perf_20260909/' + f):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get('label') not in LABELS or r.get('http_status') != 200:
            continue
        mt = (r.get('request') or {}).get('max_tokens')
        if not mt or mt < 800:
            continue
        print('%-14s mt=%-5s rtok=%-5s content=%-4s finish=%-7s %6.1fs' % (
            f, mt, r.get('reasoning_tokens'), len(r.get('content') or ''),
            r.get('finish_reason'), r.get('latency_s')))
PY
```

`content=0` 이면서 `finish=length` 인 줄이 **본문 없이 예산을 전량 소비한 요청**입니다.
`http_status == 200` 필터가 무효 구간(502)을 제외합니다. 기대 출력에 다음 두 줄이 있습니다.

```
latency3.jsonl mt=4000  rtok=4000  content=0    finish=length    86.9s
latency2.jsonl mt=6000  rtok=6000  content=0    finish=length   130.3s
```

**두 번째 줄이 이 보고서의 최악 사례입니다** — 예산 6,000토큰을 전량 추론에 쓰고 130.3초 뒤
본문 0자로 끝났으며, HTTP 상태는 **200** 입니다.

### 4.3 병목이 KV 가 아니라 배치 슬롯임을 확인

```bash
python3 - <<'PY'
import json
KEYS = ['vllm:num_requests_running', 'vllm:num_requests_waiting',
        'vllm:kv_cache_usage_perc', 'vllm:num_preemptions_total']
peak = {k: 0.0 for k in KEYS}
n = 0
for line in open('perf_20260909/throughput.jsonl'):
    if not line.strip():
        continue
    r = json.loads(line)
    m = r.get('metrics')
    if not m:
        continue
    n += 1
    for k in KEYS:
        if m.get(k) is not None:
            peak[k] = max(peak[k], float(m[k]))
print('스냅샷 %d개' % n)
for k in KEYS:
    print('  %-32s 최댓값 %s' % (k, peak[k]))
PY
```

기대 출력:

```
스냅샷 98개
  vllm:num_requests_running        최댓값 32.0
  vllm:num_requests_waiting        최댓값 16.0
  vllm:kv_cache_usage_perc         최댓값 0.15041...
  vllm:num_preemptions_total       최댓값 0.0
```

`running` 최댓값이 **32.0**(= `--max-num-seqs`)이고 KV 사용률 최댓값이 **15.041 %** 이면
배치 슬롯이 먼저 찬 것입니다. `running` 이 32 를 넘으면 외부 트래픽이 섞였다는 뜻입니다.

---

## 5. 전체 집계 재현

```bash
python3 perf_analyze.py     # 원시 JSONL → summary.json
```

집계 규칙(백분위 계산 · 무효 구간 배제 · 이상치 처리)이 **전부 이 파일 안에 코드로** 있습니다.
문서의 수치와 `summary.json` 이 다르면 `summary.json` 이 기준입니다.

## 6. 측정 재실행 (고객 환경)

⚠️ **GPU 클러스터 비용이 발생합니다.** 그리고 하네스는 인증 토큰을 파일에서 읽으므로
고객 환경에 맞게 수정이 필요합니다.

```bash
python3 perf_agent_campaign.py selftest    # 계측 코드 자기 시험 (네트워크 불필요 · 24항목)
python3 perf_agent_campaign.py probe       # 사전 점검
python3 perf_agent_campaign.py latency     # 이하 모듈별
python3 perf_analyze.py                    # 원시 → 집계
```

**`selftest` 는 네트워크가 필요 없으므로 먼저 돌려 보시기를 권합니다.** 계측 코드가 무엇을
검사하는지 확인할 수 있습니다([B1 §4](B1_method_and_environment.md)).

---

**관련**: [부록 B1 측정 방법](B1_method_and_environment.md) · [부록 B5 측정하지 않은 것](B5_limitations.md) · [성능 보고서 본문](../README.md)
