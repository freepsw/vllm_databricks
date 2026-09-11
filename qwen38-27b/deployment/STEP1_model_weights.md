# STEP 1 · 모델 가중치 확보와 스테이징

**이 단계에서 하는 일**: `Qwen/Qwen3.8-27B-FP8` 사전양자화 가중치를 확보해 드라이버의 `/local_disk0/models/Qwen3.8-27B-FP8` 에 둡니다.  
**소요 시간**: **72~76초** (HuggingFace 직접 다운로드 · 약 415 MB/s 조건, n=2). **대역폭이 낮으면 수십 분도 정상입니다.**  
**끝났는지 판단하는 기준**: `*.safetensors` 파일이 **66개** · 디렉터리 용량 **약 30 GB** · `config.json` · `tokenizer.json` · `model.safetensors.index.json` 존재.

> **실행 순서 주의** — 이 문서의 다운로드·복사 셀은 **드라이버에서** 실행됩니다. 따라서  
> [STEP2](STEP2_cluster_and_runtime.md) 의 클러스터 생성과 스크립트 스테이징을 먼저 마쳐야 합니다.  
> 지금 결정할 것은 **경로 A 인가 경로 B 인가** 이며, 경로 B 라면 클러스터를 만들기 전에 Unity Catalog  
> Volume 업로드를 미리 끝내 두는 편이 좋습니다.

---

## 1. 무엇을 받습니까

| 항목 | 값 |
|---|---|
| HuggingFace 모델 ID | `Qwen/Qwen3.8-27B-FP8` |
| 양자화 | 사전양자화 **FP8** (형식 e4m3 · 활성 스킴 dynamic) |
| 용량 | **약 30 GB** (BF16 원본은 약 55.56 GB · 비율 1.85배) |
| shard 파일 | **safetensors 66개** (`layers-0.safetensors` ~ `layers-63.safetensors` 64개 + `mtp.safetensors` + `outside.safetensors`) |
| 구조 | 하이브리드 어텐션 **64층** · 컨텍스트 262K토큰 |
| 최종 위치 | `/local_disk0/models/Qwen3.8-27B-FP8` |

**HF token 은 필요하지 않습니다.** public 모델입니다.

---

## 2. 경로 A · HuggingFace 직접 다운로드

**조건**  
· 드라이버에서 `https://huggingface.co` 로의 egress(아웃바운드)가 열려 있어야 합니다.  
· 폐쇄망이 아니고, 이 클러스터에 한 번만 배포하는 경우에 적합합니다.

**실행 (노트북 Python 셀)**

```python
%python
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/03_stage_weights.py"],
                    capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

**기대 결과**

| 항목 | 값 |
|---|---|
| 종료 코드 | `0` |
| 출력 | `[03] Shard 개수: 66 (예상: 66)` · `[03] 검증 성공` |
| 다운로드 용량 | 약 30 GB |
| 소요 시간 | **72~76초** (약 415 MB/s · 재현 시 75초 실측, n=2) |

> ⚠️ **소요 시간을 이 값에 맞추려 하지 마십시오.** 72~76초는 **약 415 MB/s 에 해당하는 값**이며,  
> Azure koreacentral 에서 `hf_transfer` 로 측정한 결과입니다.  
> 리전이 다르거나 egress 대역폭이 제한된 환경에서는 **수십 분이 걸릴 수 있고 그것도 정상입니다.**  
> 30 GB 를 100 Mbps 로 받으면 약 40분입니다. **진행률이 올라가고 있으면 기다리십시오.**  
> 폐쇄망이거나 반복 배포가 예상되면 아래 **경로 B** 가 훨씬 빠르고 안정적입니다.

**예상 시간 = 30,000 MB ÷ 실제 대역폭(MB/s)** — 예: 100 MB/s 면 약 5분, 50 MB/s 면 약 10분,
12 MB/s(100 Mbps)면 약 40분.

**진행률 확인** — 다운로드 셀이 도는 동안 **별도 노트북 셀**에서 아래를 반복 실행하십시오.
용량이 늘고 있으면 정상입니다(멈춰 있으면 네트워크를 확인하십시오).

```python
import subprocess
print(subprocess.run(["du","-sh","/local_disk0/models/Qwen3.8-27B-FP8"],
                     capture_output=True, text=True).stdout)
```

**스크립트 내부 설정**: `HF_HUB_ENABLE_HF_TRANSFER=1`(병렬 스레드 다운로드) ·  
`max_workers=8` · `ignore_patterns=['*.pt','original/*']`(불필요한 파일 제외).

---

## 3. 경로 B · Unity Catalog Volume

**조건**  
· 가중치가 UC Volume 에 미리 올려져 있어야 합니다. 예: `/Volumes/<catalog>/<schema>/<volume>/Qwen3.8-27B-FP8`  
· 폐쇄망·에어갭 환경, 또는 **반복 배포·다중 클러스터** 환경에 적합합니다.

**반복 배포라면 경로 B 가 구조적으로 유리합니다.** 클러스터를 재기동할 때마다 30 GB 를 외부에서  
다시 받는 대신 같은 리전의 스토리지에서 로컬 복사만 하므로 더 빠르고, 외부 egress 에 의존하지  
않습니다.

> ⚠️ **경로 B 는 실행 검증되지 않았습니다** [미검증]. 검증 환경에 Unity Catalog metastore 가 없어  
> `/Volumes` 에서의 복사를 실제로 돌려보지 못했습니다. **실행 검증된 가중치 확보 경로는 A(HuggingFace 직접  
> 다운로드) 하나입니다.** 경로 B 를 쓰신다면 아래 검증 절차(safetensors 66개 · 약 30 GB)를  
> **반드시** 수행해 복사가 실제로 완료됐는지 확인하고, 실패 시 경로 A 로 되돌릴 수 있게  
> egress 차단 여부를 미리 확인하십시오.

**실행 (노트북 Python 셀)**

```python
%python
import os
os.environ["VOLUME_PATH"] = "/Volumes/<catalog>/<schema>/<volume>/Qwen3.8-27B-FP8"
os.environ["ALLOW_HF_DOWNLOAD"] = "false"   # Volume 복사 실패 시 HF 로 우회하지 않고 즉시 중단
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/03_stage_weights.py"],
                    capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

`ALLOW_HF_DOWNLOAD=false` 를 함께 주면 HuggingFace 접근이 원천 차단됩니다. 폐쇄망에서 "조용히  
외부로 나가는" 동작을 막아야 한다면 반드시 지정하십시오.

### 왜 Volume 을 직접 읽지 않고 `/local_disk0` 으로 복사합니까

UC Volume 은 **cloud blob 기반 스토리지**입니다. vLLM 이 기동할 때마다 cloud API 를 통해 약 30 GB 를  
읽으면 cold start 가 느려집니다. 따라서 **한 번만** `/local_disk0`(NVMe SSD)으로 복사한 뒤 vLLM 이  
로컬 경로를 참조하게 하는 것이 최적입니다. 스크립트는 marker 파일로 **멱등**하게 처리하므로, 같은  
클러스터가 떠 있는 동안 다시 실행해도 복사를 건너뜁니다.

### ⚠️ `/Volumes` 경로를 쓰는 셀은 노트북에서 실행하십시오

`/Volumes` 는 FUSE 마운트이므로 분리된 프로세스(`setsid`·`nohup`·별도 세션)에서는 보이지 않을 수 있습니다.  
같은 이유로 확인된 `/Workspace` 마운트는 분리 프로세스에서 **예외 없이 빈 디렉터리처럼 동작**합니다.

---

## 4. `03_stage_weights.py` 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `MODEL_HF_ID` | `Qwen/Qwen3.8-27B-FP8` | 다운로드할 HuggingFace 모델 ID |
| `LOCAL_PATH` | `/local_disk0/models/Qwen3.8-27B-FP8` | 가중치를 둘 로컬 경로. **변경하면 [STEP3](STEP3_vllm_serve.md) 의 serve 경로도 함께 변경해야 합니다** |
| `VOLUME_PATH` | (없음) | 지정하면 HF 대신 이 경로에서 복사 |
| `ALLOW_HF_DOWNLOAD` | `true` | `false` 면 HF 다운로드를 금지 (`VOLUME_PATH` 필수) |

---

## 5. 검증

아래 세 가지를 모두 확인한 뒤 다음 단계로 넘어가십시오.

### 필수 확인 (bash)

```bash
# ① safetensors 개수 — 반드시 66
ls /local_disk0/models/Qwen3.8-27B-FP8/*.safetensors | wc -l
# 출력: 66

# ② 디렉터리 용량 — 약 30 GB
du -sh /local_disk0/models/Qwen3.8-27B-FP8/
# 출력: 30G

# ③ 필수 파일 존재
ls -lh /local_disk0/models/Qwen3.8-27B-FP8/{config.json,tokenizer.json,model.safetensors.index.json}
```

> ⚠️ **shard 파일명이 `layers-N.safetensors` 형식입니다.** 흔한  
> `model-0000N-of-*.safetensors` 패턴으로 세면 **0개가 나오며**, 정상 다운로드를 실패로  
> 오판하게 됩니다. 반드시 위의 `*.safetensors` 패턴으로 세십시오.

### 설정 검증 (선택사항 · Python)

```python
import json
cfg = json.load(open("/local_disk0/models/Qwen3.8-27B-FP8/config.json"))
qc = cfg.get("quantization_config", {})
print(qc.get("quant_method"))   # 출력: fp8
print(qc.get("fmt"))            # 출력: e4m3
print(cfg.get("architectures")) # 출력: ['Qwen3_5ForConditionalGeneration']
print(cfg.get("text_config", {}).get("max_position_embeddings"))  # 출력: 262144
```

> 이 모델은 멀티모달 config 구조이므로 `max_position_embeddings` 가 **최상위가 아니라  
> `text_config` 아래에** 있습니다. `cfg.get("max_position_embeddings")` 는 `None` 을 반환하므로  
> 컨텍스트 길이 확인에 쓸 수 없습니다.

---

## 6. 중요 · `/local_disk0` 은 클러스터 ephemeral 입니다

**`/local_disk0` 은 클러스터별 ephemeral 스토리지입니다.** 클러스터를 재시작하거나 삭제 후 새로  
만들면 이 경로의 데이터는 **모두 사라집니다.** 따라서 **클러스터를 재기동할 때마다 다시 스테이징해야  
합니다.**

재기동 시 다시 해야 하는 것과 실측 소요 시간:

| 다시 할 일 | 소요 |
|---|---|
| 부트스트랩 셀 (스크립트 7개 복사 + 권한) | 즉시 |
| venv 재빌드 | **62~77초** (관측 62 · 66 · 76.9초 · 실행마다 변동) |
| 가중치 재확보 (HF 직접) 또는 Volume 복사 | **72~76초** (HF · 약 415 MB/s 조건) |

**이는 정상이며 오류가 아닙니다.** 같은 클러스터가 계속 떠 있는 동안에는 marker 파일 처리로 한 번만  
빌드·다운로드합니다. 반복 배포가 예상되면 가중치를 **Unity Catalog Volume** 에 영구 보관하고  
경로 B 로 복사하십시오. 그러면 클러스터 재기동 시 30 GB 를 외부에서 다시 받지 않고도  
UC Volume 에서 로컬 복사만 하므로 (매우 빠름) 배포 시간을 크게 단축할 수 있습니다.

---

**다음 단계**: **이 문서를 끝까지 실행하지 마십시오.** 지금은 경로 A/B 결정만 하고
[STEP2_cluster_and_runtime.md](STEP2_cluster_and_runtime.md) 로 갑니다. STEP2 에서 클러스터와
스크립트 스테이징이 끝나면 **이 문서로 돌아와** §2(경로 A) 또는 §3(경로 B)의 셀을 드라이버에서
실행하십시오. 그 다음이 [STEP3_vllm_serve.md](STEP3_vllm_serve.md) 입니다.

**이 경로는 Databricks 의 「모델 레지스트리」(Unity Catalog registered model) 를 쓰는 경로가  
아닙니다. 왜 그런지와 대안은 [부록 A1](appendix/A1_model_registry_options.md) 을 보십시오.**
