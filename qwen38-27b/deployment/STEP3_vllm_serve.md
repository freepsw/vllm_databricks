# STEP 3 · vLLM serve 기동

**이 단계에서 하는 일**: 드라이버에서 vLLM 0.28.0 을 기동해 `/v1/chat/completions` 호환 API 를 띄웁니다.  
**소요 시간**: 콜드 기동 **280~320초**(131K) / 약 **320초**(262K) · 재기동 **45~190초**(`/health` HTTP 200 까지)  
**끝났는지 판단 기준**: `/health` 가 **HTTP 200** · 기동 로그의 결정론적 지표 **4개** 일치 · `ps -o sid` 에서 SID 가 자기 PID 와 같음(분리 확인).

---

## 1. 기동 전 결정 — 바인드 주소

| 상황 | `--host` 설정 | 사유 |
|---|---|---|
| **AI Gateway 연결 예정** | **`0.0.0.0`** (필수) | driver-proxy 는 드라이버의 **사설 IP** 로 접속. 127.0.0.1 이면 엔드포인트 호출이 모두 502 |
| **로컬 검증만** (STEP4 안 함) | **`127.0.0.1`** | 루프백으로 충분. 나중에 0.0.0.0 으로 바꾸려면 serve 재기동 필요 |

**한 번 선택하면 나중에 바꾸려면 serve 를 다시 올려야 합니다.**

> **아직 게이트웨이를 붙일지 정하지 못했다면 `0.0.0.0` 으로 띄우십시오.** 잃는 것이 없습니다 —
> 드라이버는 워크스페이스 네트워크 안에 있고 driver-proxy 는 Bearer 인증을 요구하므로,
> `0.0.0.0` 바인드가 외부에 그대로 노출되는 것이 아닙니다. 반대로 `127.0.0.1` 로 띄웠다가
> 게이트웨이를 붙이기로 하면 serve 재기동(45~190초)을 한 번 더 하게 됩니다.

---

## 2. 권장 기동 명령

### `04_serve.sh` 사용 (권장)

스크립트가 환경 정리·기존 프로세스·VRAM·포트 정리·분리 실행·`/health` 대기·기동 로그 확인까지 모두 수행합니다.

```bash
cd /local_disk0/scripts && \
  BIND_HOST=0.0.0.0 \
  TOOL_CALL_PARSER=qwen3_xml \
  PORT=8005 \
  ./04_serve.sh
```

### 환경변수 설명

| 변수 | 기본값 | 설명 | 게이트웨이용 |
|---|---|---|---|
| `BIND_HOST` | `127.0.0.1` | 바인드 주소. 게이트웨이 연결 시 반드시 `0.0.0.0` | `0.0.0.0` |
| `TOOL_CALL_PARSER` | (미지정) | Qwen 도구 호출 형식 파싱. agent 워크로드 시 `qwen3_xml` 지정 | `qwen3_xml` |
| `PORT` | `8005` | 바인드 포트 | `8005` |
| `MAX_MODEL_LEN` | `131072` | 최대 컨텍스트 토큰. 장문(262K)은 §7 참조 | `131072` |
| `KV_CACHE_DTYPE` | `fp8` | KV 캐시 압축. 비활성화는 `""` 지정 | `fp8` |
| `GPU_MEM_UTIL` | `0.90` | GPU 메모리 사용률. **0.90 초과 금지**(OOM 위험) | `0.90` |
| `SERVED_MODEL_NAME` | `qwen38-27b` | API 에서 참조할 모델 별칭. STEP4 엔드포인트와 **반드시 일치** | `qwen38-27b` |

**이미 vLLM 이 떠 있어도 그대로 실행하면 됩니다. 스크립트가 기존 프로세스를 정리하고 VRAM 반환을 확인한 뒤 다시 올립니다.**

### 직접 실행할 때

아래를 먼저 실행하여 환경을 정리한 뒤:

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset "$v"; done
export TMPDIR=/local_disk0/tmp
export HF_HOME=/local_disk0/hf
export VLLM_CACHE_ROOT=/local_disk0/vllm_cache
export TORCHINDUCTOR_CACHE_DIR=/local_disk0/inductor
mkdir -p "$TMPDIR" "$HF_HOME" "$VLLM_CACHE_ROOT" "$TORCHINDUCTOR_CACHE_DIR"
```

다음 명령으로 띄우십시오:

```bash
setsid nohup /local_disk0/vllm028/bin/vllm serve /local_disk0/models/Qwen3.8-27B-FP8 \
  --served-model-name qwen38-27b \
  --host 0.0.0.0 --port 8005 --max-model-len 131072 \
  --gpu-memory-utilization 0.90 --max-num-seqs 32 --max-num-batched-tokens 7840 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --language-model-only --kv-cache-dtype fp8 \
  > /local_disk0/serve.log 2>&1 &
```

> ⚠️ **노트북 셀에서 직접 실행하면 안 됩니다.** `%sh` 셀에서 위 명령을 실행하면 셀이 계속 실행 중 상태로 멈춰 있고, 셀을 중단하면 serve 도 함께 죽습니다. 반드시 `04_serve.sh` 또는 위의 분리 실행(setsid nohup ... &) 방식을 쓰십시오.

---

## 3. 플래그별 근거

| 플래그 | 값 | 근거 |
|---|---|---|
| `--served-model-name` | `qwen38-27b` | API 요청에서 참조할 모델 별칭. STEP4 엔드포인트의 모델명과 **반드시 일치** |
| `--host` | `0.0.0.0` | driver-proxy 가 드라이버 **사설 IP** 로 접속. 127.0.0.1 이면 호출이 모두 **502** |
| `--port` | `8005` | 클러스터 기본 포트 충돌 회피 |
| `--max-model-len` | `131072` | 권장 기본값. 한국어 약 238,000자(`max_tokens=1500` 기준 · 실측 0.543 tok/자) · 장문은 §6 |
| `--gpu-memory-utilization` | `0.90` | **0.90 을 초과하지 마십시오.** A100 80GB SKU 에서 상위 값이 OOM 실패 |
| `--max-num-seqs` | `32` | 동시 시퀀스 수. 동시성 지연을 단축(N=8 p95 13.19s → N=32 p95 7.03s) |
| `--max-num-batched-tokens` | `7840` | **반드시 784의 배수**(784 × 10). 스케줄러 정렬 필요 |
| `--kv-cache-dtype` | `fp8` | KV 캐시를 FP8 압축. 용량 **1.9배** · 동시성 **4.78x → 9.07x** · 정확도는 검증 필수 |
| `--reasoning-parser` | `qwen3` | 응답의 `<think>` 블록이 클라이언트로 새지 않음. 추론은 `message.reasoning` 필드 |
| `--enable-auto-tool-choice` | (플래그) | agent 워크로드 필수. **없으면 `tools` 요청이 HTTP 400 거절** |
| `--tool-call-parser` | `qwen3_xml` | Qwen 도구 호출 XML 형식 파싱. `--enable-auto-tool-choice` 와 함께 필수 |
| `--language-model-only` | (플래그) | 텍스트 전용 서빙으로 VRAM 절감. 이미지 입력이 필요하면 이 플래그를 **제거**하고 `--limit-mm-per-prompt image=1` 추가(실측: +4.4초 · +0.38 GiB) |

---

## 4. 절대 넘기지 말 플래그

| 플래그 | 이유 |
|---|---|
| `--api-key` | **주지 마십시오.** 호출이 전부 401 이 됩니다. driver-proxy 가 `Authorization` 헤더를 자신의 인증에 쓰고 상류로 전달하지 않기 때문 |
| `--quantization <method>` | on-the-fly 양자화는 OOM 유발. 모델이 이미 FP8 사전양자화이므로 불필요 |
| `--block-size <N>` | 784(또는 fp8 KV ON 시 1568) 자동 도출. 수동 지정 시 메모리 프로파일링 오류 |
| `--mamba-cache-mode` | 자동 해석. 수동 지정 시 recurrent state 누수 위험 |
| `--enforce-eager` | vLLM 0.21.0+ 에서 graph 컴파일 메모리 자동 관리. 강제 시 불필요한 계산 반복 |
| `--trust-remote-code` | Qwen3.8 코드가 registry 에 vendored 됨. 불필요 |
| `--speculative-config` | speculative decoding 이 recurrent state 증대로 요청 16건 중 **8건 실패** · mamba padding 0.13% → 1.52% |

---

## 5. 기동 정상 판정 — Oracle 표

`/health` 가 HTTP 200 을 반환한 뒤, 기동 로그에서 아래 **결정론적 항목 4개** 를 확인하십시오. 이 값들은 같은 플래그로 기동할 때마다 동일했으므로 **다르면 실제로 설정이 잘못된 것입니다.**

### 결정론적 판정 항목 (반드시 일치)

| 항목 | grep 명령 | 정상값 (`--kv-cache-dtype fp8` 기준) |
|---|---|---|
| 가중치 커널 | `grep -i "MarlinFP8ScaledMMLinearKernel" /local_disk0/serve.log \| tail -1` | 문자열이 존재함 |
| attention 백엔드 | `grep -i "Using.*attention backend" /local_disk0/serve.log \| tail -1` | **`FLASHINFER`** |
| attention block size | `grep -i "Setting attention block size" /local_disk0/serve.log \| tail -1` | **`1568`** 토큰 |
| mamba padding | `grep -i "Padding mamba page" /local_disk0/serve.log \| tail -1` | **`0.13%`** |

### 참고 항목 (변동 범위 ±5% · 정확히 일치할 필요 없음)

| 항목 | grep 명령 | 참고 범위(131K 프로필) | 비고 |
|---|---|---|---|
| KV 캐시 용량 | `grep -i "GPU KV cache size" /local_disk0/serve.log` | 약 **1,188,000 토큰** (±5%) | 기동 시점의 여유 VRAM 에 따라 변동 |
| 사용 가능 KV | `grep -i "Available KV cache memory" /local_disk0/serve.log` | 약 **39 ~ 41 GiB** | 위와 함께 변동 |
| 최대 동시성 | `grep -i "Maximum concurrency for" /local_disk0/serve.log` | 약 **9.1x** (±5%) | 131K 컨텍스트 기준 배수 |

**예시 — 두 번의 정상 기동(동일 플래그):**

| 실행 | 사용 가능 KV | KV 캐시 용량 | 최대 동시성 |
|---|---|---|---|
| 1회차 | `39.06 GiB` | `1,188,386` | `9.07x` |
| 2회차 | `40.78 GiB` | `1,240,814` | `9.47x` |

두 값의 차이는 약 4.4 %이며, 이 정도 변동은 정상입니다. **이 세 항목이 범위 안에 있으면
통과입니다** — 위의 결정론적 4개만 정확히 일치해야 합니다.

> **`--kv-cache-dtype fp8` 을 지정하지 않으면 정상값이 달라집니다** — attention 백엔드는
> `FLASH_ATTN`, block size 는 `784`, KV 캐시 용량은 약 `627,000` 토큰, 최대 동시성은 약 `4.8x`
> 입니다. 이 문서의 권장 구성은 `fp8` 이므로 위 표는 `fp8` 기준입니다.

> **`04_serve.sh` 로 기동했다면 이 grep 을 직접 돌리지 않아도 됩니다** — 스크립트가 기동 직후
> 「7. 기동 로그 Oracle 검증」 단계에서 위 항목들을 순서대로 출력합니다. grep 은 나중에 로그를
> 다시 확인할 때 쓰십시오.

> **`block_size` 는 기동 로그로만 확인할 수 있습니다.** `create_engine_config()` 시점에는 `16`
> 으로 보이고 디바이스 KV 프로파일링 단계에서 최종 결정되므로, config 조회 API 로는 보이지 않습니다.

---

## 6. 기동 소요 시간

| 상황 | 소요시간 | 비고 |
|---|---|---|
| **콜드 기동** (torch.compile 캐시 비어 있음 · 131K) | **280 ~ 320초** | 3회 관측 범위 |
| **콜드 기동** (262K 프로필) | 약 **320초** | 1회 관측 |
| **재기동** (캐시 있음) | **45 ~ 190초** | 원인 미규명 · 계획은 190초 |

**클라이언트 대기 timeout 은 20분(1200초) 이상으로 설정하십시오.** 첫 기동의 torch 컴파일이 예상보다 길어질 수 있습니다. `04_serve.sh` 는 이미 1200초로 설정되어 있습니다.

---

## 7. VRAM 예산과 프로필 선택

### 메모리 사용량(131K 프로필, FP8 KV)

| 상태 | VRAM 점유 |
|---|---|
| 유휴(idle) | 약 69 GiB |
| 부하 중 피크 | 약 71 GiB |
| **GPU 총량** | **80 GiB** |

### 프로필 선택

| 프로필 | 언제 쓰나 | 주의사항 |
|---|---|---|
| **131K (기본 권장)** | 대부분 업무용 | 한국어 약 238,000자(`max_tokens=1500` 기준) |
| **262K (장문)** | 13만 토큰 초과 입력 필요 시만 | 기동 ~320초 · VRAM 여유 감소(8.7 GiB) · 클라이언트 timeout 확대 필수 |

### 262K 로 바꿀 때

```bash
MAX_MODEL_LEN=262144 BIND_HOST=0.0.0.0 TOOL_CALL_PARSER=qwen3_xml bash /local_disk0/scripts/04_serve.sh
```

**실증된 최대 처리**: 249,123 토큰을 약 151초에 처리. 클라이언트 HTTP timeout 을 그에 맞게 설정하십시오.

**컨텍스트 상한**: `max_model_len − max_tokens` (131K 프로필). 초과 시 HTTP 400 `BAD_REQUEST` 가 0.38~0.45초에 즉시 돌아옵니다.

---

## 8. 분리 실행

노트북 셀이 끊겨도 serve 가 살아 있도록 하려면 분리 실행이 필수입니다. `04_serve.sh` 는 이미 분리 실행합니다.

직접 기동한다면 `vllm serve` 앞에 **`setsid nohup`** 을 붙이고 뒤에 **`&`** 를 붙이십시오(`setsid` = 새 세션 · `nohup` = SIGHUP 무시 · `&` = 백그라운드).

### 정말 분리되었는지 확인

```bash
# vllm 실행파일을 argv[3]으로 갖는 프로세스 선택 (잘못된 pkill 패턴 회피)
PID=$(ps -eo pid,args | awk '$3 ~ /bin\/vllm$/ {print $1; exit}')
ps -o pid,ppid,sid -p $PID
# 정상: SID 가 자기 PID 와 같으면 분리됨
```

### `/health` 대기

```bash
timeout 1200 bash -c 'until [ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8005/health)" = "200" ]; do sleep 2; done' \
  && echo "Ready" || echo "Timeout"
```

> ⚠️ vLLM 의 `/health` 는 **본문이 비어 있는 HTTP 200** 을 반환합니다. `curl … | grep -q ok` 처럼 본문을 검사하면 서버가 정상이어도 매칭되지 않아 20분을 무의미하게 기다린 뒤 실패합니다. **반드시 상태코드로만 판정하십시오.**

---

## 9. 정지 및 정리

### 간단한 방법 — `04_serve.sh` 재실행

**재기동만 하려면 별도 정리 없이 `04_serve.sh` 를 그대로 다시 실행하십시오.** 스크립트가 모든 정리를 담당합니다.

### 수동 정리

GPU 점유가 완전히 해제되었는지 확인:

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
# 정상: 0 MiB, 81920 MiB

nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
# 정상: 출력 없음 (GPU 점유 프로세스 없음)

ss -ltnp | grep 8005 || echo "(listen 없음)"
```

> ⚠️ **노트북 `%sh` 셀에서 `pkill -9 -f 'vllm serve'` 를 쓰지 마십시오.**  
> · 셸이 자신의 argv 를 포함하여 매칭되어 **자기 자신이 죽습니다.** 셀은 출력 없이 끝나고 Databricks 는 이를 성공으로 표시하므로 실패 신호도 남지 않습니다.  
> · 실제 VRAM 을 점유하는 자식 프로세스(`VLLM::EngineCore`)는 이 패턴에 걸리지 않으므로 **VRAM 이 72,310 MiB 그대로 점유**되어 재기동이 반드시 실패합니다.  
> **`04_serve.sh` 안의 같은 명령은 문제가 없습니다. 스크립트를 고치지 마십시오.**

---

## 10. 기동 직후 첫 호출 — OpenAI 호환 API 사용

엔드포인트는 `/v1/chat/completions` 를 쓰십시오. `max_tokens` 를 넉넉히 주세요 — 추론이 우선 소비됩니다.

### Python 클라이언트 예시

```python
from openai import OpenAI

# 직접 호출(STEP4 안 하는 경우)
client = OpenAI(base_url="http://127.0.0.1:8005/v1", api_key="dummy")

# 게이트웨이 경유(STEP4 후)
# client = OpenAI(base_url="https://<워크스페이스>/serving-endpoints", api_key="<PAT>")

response = client.chat.completions.create(
    model="qwen38-27b",
    max_tokens=1500,  # 추론 토큰이 먼저 소비됨
    messages=[
        {"role": "user", "content": "안녕하세요. 간단히 자기소개해 주세요"}
    ]
)

print(response.choices[0].message.content)
if hasattr(response.choices[0].message, 'reasoning'):
    print("추론:", response.choices[0].message.reasoning)
```

**주의사항:**  
· 응답의 추론 내용은 `message.reasoning` 필드로 분리되어 옵니다(`<think>` 는 유출되지 않음).  
· `max_tokens` 를 작게 주면 추론 토큰이 먼저 소비되어 본문이 거의 나오지 않을 수 있습니다.  
· `choices[].message.reasoning` 이 공백일 수 있습니다(쉬운 질문의 경우).

---

## 성능 기준선

동시성 N=22 에서 약 2.85 rps 처리. 자세한 성능 수치(처리량·지연·비용·용량)는 성능 추적 문서를 참조하십시오.

---

**다음 단계**: [STEP4_ai_gateway.md](STEP4_ai_gateway.md) 로 이 vLLM 을 OpenAI 호환 서빙 엔드포인트로 등록합니다. 진행 전 반드시 **`--host 0.0.0.0` 으로 떠 있는지**, **클러스터 `autotermination_minutes` 가 `0` 인지** 확인하십시오.
