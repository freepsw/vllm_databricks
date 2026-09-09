# Qwen3.8-27B (FP8) × vLLM 0.28.0 × Azure Databricks 배포 가이드

**대상 모델**: `Qwen/Qwen3.8-27B-FP8` (사전양자화 FP8, 약 30 GB)  
**추론 엔진**: vLLM **0.28.0**  
**검증 환경**: Azure Databricks · DBR `19.x-gpu-ml-scala2.13` · `Standard_NC24ads_A100_v4` (A100 80GB ×1, single-node)

이 문서는 위 구성으로 **실제 배포와 8시간 연속 부하 시험을 완료한 결과를 그대로 재현**하기 위한 가이드입니다. 모든 수치는 실측값이며, 고객 환경에서 대조할 수 있도록 기준선으로 함께 제공합니다.

---

## 목차

**시작하기 전에**: [제공 파일](#제공-파일) · [결과 기준선 요약](#결과-기준선-요약) · [전체 흐름 요약](#전체-흐름-요약) · [반드시 지켜야 할 3가지](#반드시-지켜야-할-3가지)

1. [사전 요건 및 클러스터 생성](#1-사전-요건-및-클러스터-생성)
2. [스크립트 배포](#2-스크립트-배포)
3. [환경 구성](#3-환경-구성-venv--가중치)
4. [vLLM serve 실행](#4-vllm-serve-실행)
5. [검증](#5-검증-빠른-검증--장시간-안정성)
6. [AI Gateway 연결 및 테스트](#6-ai-gateway-연결-및-테스트)
7. [배포 단계 문제 해결 요약](#7-배포-단계-문제-해결-요약)

---

## 제공 파일

### 문서 — 이 두 개로 전과정을 따라갑니다

| 문서 | 범위 |
|---|---|
| **`DEPLOYMENT_GUIDE.md`** (이 문서) | §1 클러스터 → §2 배포 → §3 환경 → §4 serve → §5 검증 |
| **`AI_GATEWAY_REGISTRATION_GUIDE.md`** | AI Gateway 연결 → 엔드포인트 생성 → 검증 → 사용 → 운영 (이 문서 §6 에서 이어짐) |

### 실행 노트북

| 파일 | 대응 절 | 비고 |
|---|---|---|
| **`07_deploy_notebook.py`** | 이 문서 §2.4 ~ §5.2 | Workspace → Import → File 로 가져오기 |
| **`notebook_gateway_register.py`** | 게이트웨이 가이드 전체 | 셀 6개 · 실행 코드 140줄 · **외부 파일 의존 없음** |

### 스크립트

| 파일 | 실행 위치 | 용도 |
|---|---|---|
| `scripts/00_check_prereq.py` | 드라이버 | 배포 전 환경 점검 (GPU·디스크·도구·네트워크) |
| `scripts/01_cluster.json` | 로컬 CLI | 클러스터 생성 스펙 (**8시간 soak·게이트웨이 예정이면 `autotermination_minutes: 0` — §1.3**) |
| `scripts/02_build_venv.py` | 드라이버 | vLLM 0.28.0 격리 venv 빌드 |
| `scripts/03_stage_weights.py` | 드라이버 | 가중치 확보 (HuggingFace 또는 UC Volume) |
| `scripts/04_serve.sh` | 드라이버 | vLLM serve 기동 + 기동 로그 점검 |
| `scripts/05_validate.py` | 드라이버 | 빠른 검증 (품질·동시성·장문) |
| `scripts/06_soak.py` | 드라이버 | 장시간 안정성 시험 (선택) |

### 이 패키지에 포함되지 않은 것

게이트웨이 연결의 **구버전 자산**(구 §6 원문, `08_gateway_notebook.py`,
`scripts/08_gateway_deploy.py`, `scripts/09_gateway_validate.py`)은 혼동을 막기 위해
**의도적으로 제외**했습니다. 위의 `AI_GATEWAY_REGISTRATION_GUIDE.md` +
`notebook_gateway_register.py` 가 같은 일을 하며, 3개 파일 ~598줄 + `exec(open(...))` 간접
호출이 노트북 1개 140줄로 단순화되었습니다.

## 결과 기준선 요약

고객 환경에서 아래 값과 크게 다르면 해당 장의 확인 절차를 참조하십시오.

| 항목 | 기준선 |
|---|---|
| KV 캐시 용량 (`--kv-cache-dtype fp8`) | 약 1,188,000 토큰 · 131K 컨텍스트 동시성 약 9.1x (**실행마다 ±5% 변동 — §4.4**) |
| VRAM (131K 프로필) | idle 약 69 GiB · 부하 peak 약 71 GiB (총 80 GiB) |
| 최대 실증 컨텍스트 (262K 프로필) | **249,123 토큰** 처리 성공 (약 151초) |
| 한국어 품질 | 5/5 · `<think>` 유출 0건 |
| 동시성 16 p95 | 약 7.0초 |
| 동시성 22 정상상태 | 약 2.85 rps · p50 약 8.7초 · p95 약 8.9초 |
| 8시간 연속 부하 | 82,129 요청 · 오류 **0** · VRAM drift 0.122% (측정 창 7.998시간) |

## 전체 흐름 요약

| 단계 | 소요 시간 | 주요 작업 | 담당 파일/스크립트 |
|---|---|---|---|
| **1. 사전 요건 및 클러스터** | 5~10분 | Azure 쿼터 확인 · 클러스터 생성 · 기동 | `01_cluster.json` |
| **2. 스크립트 배포** | 5분 | UC Volume/DBFS 업로드 · 부트스트랩 실행 | CLI `databricks fs cp` |
| **3. 환경 구성** | ~2분 30초 | venv 빌드 (66초) · 가중치 준비 (72초) | `02_build_venv.py` · `03_stage_weights.py` |
| **4. vLLM serve 실행** | 280~320초 | serve 기동 · `/health` 확인 · 로그 검증 | `04_serve.sh` 또는 직접 실행 |
| **5. 검증 (필수)** | ~10분 | 8개 항목 점검 (한국어·동시성·장문·기동·VRAM) | `05_validate.py` |
| **6. AI Gateway 연결·테스트** | ~10분 | 엔드포인트 생성 · 종단 검증 · 외부 agent 연결 | `notebook_gateway_register.py` (별도 가이드) |
| **5. 검증 (선택)** | 8시간 | 장시간 안정성 테스트 (VRAM drift·지연·오류) | `06_soak.py` |
| **7. 문제 해결** | 필요시 | 문제 진단 및 조치 | §7 · 각 가이드의 문제 해결 절 |

**vLLM 배포까지 약 25~30분** (§1~§5)  
**AI Gateway 연결까지 약 40분** (§1~§6)  
**프로덕션 검증까지 약 9시간** (8시간 soak 포함)

> **시작 전에 두 가지를 먼저 정하십시오.** 나중에 바꾸면 클러스터·serve 를 다시 올려야 합니다.
>
> | 결정 | 위치 | 게이트웨이까지 갈 예정이면 |
> |---|---|---|
> | `autotermination_minutes` | `01_cluster.json` (**생성 전**) | **`0`** — §1.3 |
> | `--host` | §4.1 결정 표 | **`0.0.0.0`** — 그러면 게이트웨이 가이드 §2 를 건너뜁니다 |
>
> 그리고 **게이트웨이 가이드 §0.2 「도달성 사전 시험」을 이 문서 §1 보다 먼저** 수행하는 편이
> 좋습니다. Private Link·egress 제한으로 막히는 환경이면 30GB 가중치 투입 전에 5분 만에 판별됩니다.

---

## 반드시 지켜야 할 3가지

1. **격리된 venv를 사용합니다.** DBR 19의 torch(2.12.0)와 vLLM 0.28.0이 요구하는 torch(2.13.0)가 다르므로, 런타임 패키지를 보존한 채 설치하는 방식은 동작하지 않습니다.  
   상세는 §3.1에 있습니다.

2. **실행 전 환경변수를 정리합니다.** `OPENSSL_FORCE_FIPS_MODE`가 설정된 상태로 실행하면 프로세스가 즉시 종료됩니다.  
   정리 방법은 §3.2에 있습니다.

3. **지정된 플래그 외에는 추가하지 않습니다.** 특히 `--gpu-memory-utilization`은 **0.90을 초과하지 마십시오.** 금지 플래그 목록은 §4.3에 있습니다.

---

## 참고: A100에서의 FP8

A100(compute capability 8.0)은 **native FP8 연산을 지원하지 않습니다.** 따라서 FP8 가중치는  
`MarlinFP8ScaledMMLinearKernel`(W8A16 weight-only)로 처리되며, 얻는 이득은  
**메모리 절감**(BF16 55.56 GB → FP8 약 30 GB)이고 **연산 가속은 없습니다.**  
연산 가속까지 필요하면 H100 계열(compute capability 9.0 이상)을 검토하십시오.

---

## 1. 사전 요건 및 클러스터 생성

**전제 조건**: Azure 구독 · Databricks CLI 설치 · 적절한 권한

**소요 시간**: 5~10분 (클러스터 기동)

### 1.1 Azure 리소스 요구사항

#### 인스턴스 타입

**`Standard_NC24ads_A100_v4`**를 사용합니다.
이는 NVIDIA A100 80GB GPU 1개와 24개 vCPU를 제공합니다.

#### 쿼터 확인

Azure 구독에서 해당 인스턴스 타입에 대한 쿼터를 확인하십시오.

```bash
# 1. 현재 사용 현황 확인
az vm list-usage -l <REGION> --subscription <SUBSCRIPTION_ID> -o table | \
  grep -i "standard.*a100.*family"

# 2. SKU 가용성 및 지역 제약 확인
az vm list-skus -l <REGION> --size Standard_NC24ads --subscription <SUBSCRIPTION_ID> -o table
```

- `<REGION>`: `koreacentral` 등 배포 예정 지역
- `<SUBSCRIPTION_ID>`: Azure 구독 ID

**쿼터 부족 시** Azure Support를 통해 `StandardNCADSA100v4Family` 쿼터 증설을 요청하십시오.
요청 시 "Standard_NC24ads_A100_v4 × 1 (24 vCPU)"을 필요로 한다고 명시하면 됩니다.

---

### 1.2 Databricks 런타임 및 GPU 사양

#### 런타임

**`19.x-gpu-ml-scala2.13`**를 선택합니다.

이 런타임은 다음을 제공합니다:
- **드라이버**: 580.x 계열 (NVIDIA 최신 드라이버)
- **CUDA**: 13.0 (cuda-toolkit 13.0.2)
- **GPU**: NVIDIA A100 80GB, compute capability **8.0**

이 조합이 중요합니다. 드라이버 580과 CUDA 13.0은 최신 vLLM 바이너리(cu130 wheel)를 올바르게 지원하므로,
처음부터 최적화된 설치가 가능합니다.

---

### 1.3 클러스터 생성 및 설정

#### 클러스터 스펙

다음 명령으로 클러스터를 생성합니다:

```bash
databricks clusters create --json @01_cluster.json --profile <PROFILE>
```

- `<PROFILE>`: Databricks CLI 프로필 이름 (예: `default`)

명령 실행 후 반환되는 `cluster_id`를 기록해 두십시오. 후속 단계에서 필요합니다.

#### 클러스터 구성 항목

`01_cluster.json`에는 다음이 설정됩니다:

| 항목 | 값 | 설명 |
|---|---|---|
| `cluster_name` | `qwen38-27b-vllm-test` | 중립적인 이름 |
| `num_workers` | `0` | **Single-node** 클러스터 |
| `spark.master` | `local[*, 4]` | 드라이버만 사용, 스레드 제한 |
| `spark.databricks.cluster.profile` | `singleNode` | 단일 노드 프로파일 |
| `data_security_mode` | `SINGLE_USER` | 사용자별 격리 모드 |
| `autotermination_minutes` | `90` | 90분 후 자동 종료 (아래 참고) |

#### 자동 종료 정책 — **클러스터를 만들기 전에 결정하십시오**

| 계획 | `autotermination_minutes` |
|---|---|
| §5.1 빠른 검증까지만 (약 10분) | `90` (기본값) |
| **§5.2 8시간 soak 을 할 예정** | **`0`** — 생성 시점에 지정 |
| 상시 서비스 · §6 AI Gateway 연결 | **`0`** — 생성 시점에 지정 |

```json
"autotermination_minutes": 0
```

> ⚠️ **나중에 바꾸려면 클러스터가 재시작되고, 그러면 처음부터 다시 해야 합니다.**
> `databricks clusters edit` 는 *"If a cluster is updated while in a RUNNING state,
> it will be restarted"* 입니다. 재시작되면 `/local_disk0` 이 비워지므로(§3.5)
> venv·가중치·serve 를 모두 다시 올려야 합니다. **8시간 soak 이나 게이트웨이 연결을 할
> 가능성이 있으면 처음부터 `0` 으로 만드십시오.**

> ⚠️ **자동 종료는 추론 트래픽으로 갱신되지 않습니다.** Databricks 의 판정 기준은
> *"마지막 **명령** 실행"* 시각입니다. vLLM 이 요청을 처리하는 중이어도, 노트북 셀이
> 돌지 않으면 유휴로 간주되어 종료됩니다. `06_soak.py` 는 분리 실행되므로(§5.2)
> **기본값 90분으로는 8시간 시험이 약 90분 만에 죽습니다.**
> 실측: 이 프로젝트의 검증 클러스터가 `TERMINATING · INACTIVITY ·
> inactivity_duration_min: 90` 으로 종료된 기록이 있습니다.

#### ⚠️ 중요: `autotermination: 0`일 때는 반드시 수동 종료하십시오

`autotermination_minutes: 0`이면 클러스터가 절대 자동 종료되지 않습니다.
테스트 종료 후 **반드시 수동으로 클러스터를 종료**해야 과금이 멈춥니다.

```bash
databricks clusters delete <CLUSTER_ID> --profile <PROFILE>
```

- `<CLUSTER_ID>`: 클러스터 생성 시 반환된 ID (형식: `MMDD-HHMMSS-xxxxxxxx`)

**주의**: `terminate` 서브커맨드는 존재하지 않습니다. 반드시 `delete` 명령을 사용하십시오.

---

### 1.4 스토리지 요구사항

#### `/local_disk0` 여유 공간

최소 **45 GB 이상**의 여유 공간이 필요합니다.

- vLLM 가상환경: **7.6 GB**
- Qwen3.8-27B-FP8 사전양자화 가중치: **약 30 GB**
- 임시 파일 및 로그: **~ 2–3 GB**

클러스터 시작 후 다음 명령으로 확인할 수 있습니다:

```bash
df -h /local_disk0
```

---

### 1.5 네트워크 연결성

#### HuggingFace 모델 가중치 다운로드

기본적으로 드라이버 노드의 외부 egress를 통해 HuggingFace에서 직접 가중치를 다운로드합니다.

```
https://huggingface.co/Qwen/Qwen3.8-27B-FP8
```

**네트워크가 차단된 경우**: Unity Catalog Volume 또는 클라우드 스토리지(Azure Blob Storage 등)에 가중치를 미리 업로드하고,
§3 (환경 구성) 단계에서 로컬 경로로 지정할 수 있습니다.

---

### 1.6 신규 워크스페이스 시작 시 주의사항

Databricks 워크스페이스를 생성한 지 얼마 되지 않은 경우, 클러스터 시작 시 다음 오류가 나타날 수 있습니다:

```
INVALID_WORKER_ENVIRONMENT: WorkerEnv not found in central
```

이는 백엔드 초기화가 아직 진행 중이라는 의미입니다.

**해결 방법**: 동일한 클러스터를 다시 시작하면 통과합니다:

```bash
databricks clusters start <CLUSTER_ID> --profile <PROFILE>
```

약 **5~10분** 후 클러스터가 `RUNNING` 상태로 전환됩니다.

이미 운영 중인 워크스페이스라면 이 현상은 발생하지 않습니다.

---

### 1.7 클러스터 설정 파일

`01_cluster.json`을 편집할 때:

- **`single_user_name`**: 고객 이메일 주소로 변경
- **`custom_tags.Owner`**: 고객 이메일 주소로 변경

예시:

```json
{
  "single_user_name": "customer@example.com",
  "custom_tags": {
    "Owner": "customer@example.com"
  }
}
```

나머지 필드는 테스트된 기본값입니다. 변경하지 않는 것을 권장합니다.

---

### 1.8 사전 점검 스크립트 — **§2 배포를 마친 뒤에 실행합니다**

> **실행 순서 주의**: 이 점검은 `/local_disk0/scripts/00_check_prereq.py` 를 실행하므로
> **§2(스크립트 배포)의 부트스트랩 셀을 먼저 실행해야 합니다.** 지금은 내용만 확인하고,
> §2.4 를 마친 다음 이 절로 돌아오십시오.

클러스터가 RUNNING 이고 부트스트랩이 끝난 뒤, 드라이버 노드에서 다음을 실행하십시오:

```python
%python
import os, glob, stat
# 스크립트 파일들이 /local_disk0/scripts에 있다고 가정
# (§2 부트스트랩 셀 실행 후)
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/00_check_prereq.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

이 스크립트는 다음을 검증합니다:

- NVIDIA 드라이버 버전 ≥ 580
- GPU 메모리 ≥ 80 GB
- compute capability 8.0
- `/local_disk0` 여유 ≥ 45 GB
- 필수 도구(`uv`) 존재
- PyPI 및 HuggingFace 도달성
- 환경 변수 상태

**모두 PASS**이면 §3(환경 구성)으로 진행합니다.

---

### 1.9 다음 단계

클러스터가 RUNNING 이면 스크립트를 클러스터로 옮깁니다 (§2). 그 다음 §1.8 의 사전 점검을
실행하고, 통과하면 §3 으로 진행합니다.

---

## 2. 스크립트 배포

**전제 조건**: 클러스터가 RUNNING 상태 · 로컬 머신에 제공된 `scripts/` 폴더 준비

**소요 시간**: 약 5분 (업로드 + 부트스트랩)

제공된 배포 스크립트들(`02_build_venv.py`, `03_stage_weights.py`, `04_serve.sh`, `05_validate.py`, `06_soak.py`)은
클러스터에 먼저 올린 다음, 드라이버의 `/local_disk0/scripts/` 경로에서 실행합니다.

> **노트북을 쓰면 아래 §2.4부터 §5.2까지를 셀 단위로 그대로 실행할 수 있습니다.**
> `07_deploy_notebook.py`를 워크스페이스에 가져오고(Workspace → Import → File),
> 클러스터에 연결한 뒤 위에서 아래로 실행하십시오. 각 셀에 소요 시간과 성공 판정 기준이
> 함께 적혀 있습니다. 아래 본문은 그 셀들의 상세 근거입니다.

### 2.1 방법 A: Unity Catalog Volume (권장, UC 사용 환경)

이 방법은 클라우드 스토리지를 거쳐서 올리므로, 드라이버 로컬 egress가 제한된 환경에서도 안정적입니다.

**필요 권한**: 대상 카탈로그·스키마에 `USE CATALOG` · `USE SCHEMA`, 볼륨에 `READ VOLUME`(드라이버에서
읽기) 과 `WRITE VOLUME`(업로드). 볼륨이 없으면 먼저 만들어야 합니다.

```sql
-- SQL 에디터 또는 노트북에서 (볼륨이 없을 때만)
CREATE VOLUME IF NOT EXISTS <catalog>.<schema>.<volume>;
```

**로컬 머신에서 (한 번 실행)**:

```bash
databricks fs mkdir dbfs:/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts \
  --profile <PROFILE>

databricks fs cp -r --overwrite ./scripts \
  dbfs:/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts/ \
  --profile <PROFILE>
```

드라이버에서 접근 경로: `/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts/`

### 2.2 방법 B: DBFS (레거시 워크스페이스에서만)

> ⚠️ **DBFS root 는 폐기 예정이며 최근 워크스페이스에서는 기본적으로 막혀 있습니다.**
> Databricks 문서 기준으로 **2025-12-19 이후에 생성된 account 는 기본적으로 이 기능에
> 접근할 수 없습니다.** 관리자가 "Disable DBFS root and mounts" 를 켠 경우에도 마찬가지이며,
> 그때는 UI·API·CLI·FUSE 전부에서 차단되고 `Public DBFS root is disabled` 가 반환됩니다.
>
> 먼저 사용 가능한지 확인하십시오. 실패하면 **방법 A(§2.1) 또는 방법 C(§2.3)** 를 쓰십시오.
>
> ```bash
> databricks fs ls dbfs:/FileStore/ --profile <PROFILE>
> ```

**로컬 머신에서**:

```bash
databricks fs cp -r --overwrite ./scripts \
  dbfs:/FileStore/qwen38-scripts/ \
  --profile <PROFILE>
```

드라이버에서 접근 경로: `/dbfs/FileStore/qwen38-scripts/`

---

### 2.3 방법 C: 워크스페이스 파일 (UC Volume 도 DBFS 도 없을 때)

metastore 가 없고 DBFS 도 차단된 환경을 위한 경로입니다. 스크립트 전체가 100KB 미만이므로
워크스페이스 파일로 충분합니다(파일당 상한 500MB).

> **`--format RAW` 가 중요합니다.** 이 옵션이 없으면 `.py` 가 **노트북으로 임포트되고 확장자가
> 제거되어** 부트스트랩이 파일을 찾지 못합니다. `import-dir` 도 같은 이유로 쓰지 마십시오.

**로컬 머신에서**:

```bash
databricks workspace mkdirs /Users/<사용자>/qwen38-scripts --profile <PROFILE>

for f in ./scripts/*; do
  databricks workspace import --format RAW --overwrite \
    --file "$f" "/Users/<사용자>/qwen38-scripts/$(basename $f)" \
    --profile <PROFILE>
done
```

**FILE 로 올라갔는지 확인하십시오** (`NOTEBOOK` 이면 위 `--format RAW` 를 빠뜨린 것입니다):

```bash
databricks workspace list /Users/<사용자>/qwen38-scripts -o json --profile <PROFILE> \
  | grep object_type | sort -u
# 기대: "object_type": "FILE" 만 나와야 합니다
```

드라이버에서 접근 경로: `/Workspace/Users/<사용자>/qwen38-scripts/`

> ⚠️ **`/Workspace` 는 노트북 셀에서만 보입니다.** 분리된 프로세스(`setsid`·`nohup`·별도 세션)에서는
> 이 마운트가 보이지 않으며, **예외가 아니라 빈 디렉터리처럼 동작**합니다
> (`os.path.isdir()` 이 `False`, `glob()` 이 `[]`).
> 따라서 §2.4 부트스트랩을 분리 실행하면 **아무것도 복사되지 않은 채 오류 없이 끝납니다.**
> 반드시 **노트북 셀에서** 실행하고, 복사된 파일 수를 확인하십시오.
>
> 실측 대조 (같은 클러스터, 같은 경로):
>
> | 마운트 | 노트북 셀 | `setsid` 분리 프로세스 |
> |---|---|---|
> | `/dbfs` | 정상 | 정상 |
> | `/Workspace` | 정상 | **`isdir=False` · 0개** |
>
> 같은 이유로 §3.4 방법 (B) 의 `VOLUME_PATH`(`/Volumes/...`)도 노트북 셀에서 실행하십시오
> (`/Volumes` 는 이 프로젝트에서 검증하지 못했지만 동일한 FUSE 마운트 방식입니다).

---

### 2.4 부트스트랩 셀 (클러스터 드라이버에서 실행)

어느 방법을 선택하든, 클러스터 드라이버의 노트북 **첫 셀**에서 다음을 실행하십시오.
이 셀은 `/local_disk0`(ephemeral 로컬 스토리지)로 스크립트를 복사하고 실행 권한을 부여합니다.

**방법 A를 사용할 경우**:

```python
%python
SRC = "/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts"
DST = "/local_disk0/scripts"
import os, shutil, glob, stat
os.makedirs(DST, exist_ok=True)
for f in glob.glob(f"{SRC}/*"):
    shutil.copy(f, DST)
for f in glob.glob(f"{DST}/*.sh"):
    os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
print(f"Scripts copied to {DST}. Files: {sorted(os.listdir(DST))}")
```

**방법 B 또는 방법 C를 사용할 경우** — `SRC` 만 바꾸면 나머지는 동일합니다.

```python
%python
# 방법 B (DBFS):            SRC = "/dbfs/FileStore/qwen38-scripts"
# 방법 C (워크스페이스 파일):  SRC = "/Workspace/Users/<사용자>/qwen38-scripts"
SRC = "/dbfs/FileStore/qwen38-scripts"
DST = "/local_disk0/scripts"
import os, shutil, glob, stat
os.makedirs(DST, exist_ok=True)
for f in glob.glob(f"{SRC}/*"):
    shutil.copy(f, DST)
for f in glob.glob(f"{DST}/*.sh"):
    os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
print(f"Scripts copied to {DST}. Files: {sorted(os.listdir(DST))}")
```

**복사된 파일 수를 확인하십시오.** **7개**(`00_check_prereq.py` · `01_cluster.json` ·
`02_build_venv.py` · `03_stage_weights.py` · `04_serve.sh` · `05_validate.py` · `06_soak.py`)가
나와야 합니다. 게이트웨이 연결에는 **추가 스크립트가 필요하지 않습니다** — 노트북
`notebook_gateway_register.py` 가 외부 파일 의존 없이 단독으로 수행합니다(§「이 패키지에 포함되지
않은 것」 참조).
0개이거나 확장자가 없는 이름이 보이면 업로드 형식이 잘못된 것입니다(§2.3 의 `--format RAW` 확인).

**중요**: `/local_disk0`는 클러스터 **재시작 시 초기화**되므로, 클러스터를 재시작할 때마다
이 부트스트랩 셀을 다시 실행해야 합니다.

**이제 §1.8 의 사전 점검 스크립트를 실행하십시오.** 스크립트가 배포된 지금이 실행 시점입니다.
모두 PASS 이면 §3 으로 진행합니다.

---

### 2.5 노트북에서 스크립트 실행하기

이후 각 단계에서는 다음 두 형태 중 하나로 스크립트를 실행합니다.

**노트북 셀에서 Python 스크립트 실행** (권장):

```python
import os
os.environ["예시변수"] = "값"  # 필요시 환경변수 지정
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/05_validate.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

**쉘 명령으로 실행**:

```bash
bash /local_disk0/scripts/04_serve.sh
```

각 단계에서 구체적인 실행 방법을 제시합니다.

---

### 2.6 다음 단계

부트스트랩 셀 실행 후 §3 (환경 구성)으로 진행합니다.

---

## 3. 환경 구성: venv 빌드 및 가중치 준비

**전제 조건**: 부트스트랩 셀 실행 완료 · `/local_disk0`에 약 8 GB 여유

**소요 시간**: venv 약 66초 + 가중치 약 72초 (HuggingFace 직접 다운로드 시)

### 3.1 설계 근거 — 왜 격리된 venv가 필요한가

DBR 19 클러스터의 base Python 환경에는 torch **2.12.0+cu130**과 transformers **4.57.6**이 포함되어 있습니다. 그런데 Qwen3.8-27B-FP8과 vLLM 0.28.0을 함께 띄우려면 다음 조건을 만족해야 합니다.

- **vLLM 0.28.0은 torch 2.13.0을 고정**합니다. 이전 버전(0.20~0.26)은 2.11.0을 고정했고, 더 이전은 2.10.0/2.9.1을 사용했습니다.
- **어떤 vLLM 릴리스도 DBR 기본 torch를 핀하지 않습니다** (vLLM은 2.11.0 → 2.13.0으로 건너뜁니다).
- **transformers 5.x도 필요**한데, DBR 19에는 4.57.6만 있습니다.

따라서 "DBR torch를 보존하고 `--no-deps`로 설치하는" 방식은 이 모델에 **절대 적용할 수 없습니다.** 필수 의존성 충돌을 무시하면 런타임에 즉시 실패합니다.

**대신 다음 전략을 사용합니다:**
- `uv venv`로 **자체 torch(2.13.0)을 가진 독립 venv**를 만듭니다.
- vLLM을 **분리된 프로세스로 띄워** loopback HTTP(127.0.0.1:8005)로 통신합니다.
- 이렇게 하면 **노트북 커널의 base torch(2.12.0)와 venv torch(2.13.0)가 절대 섞이지 않습니다.**

실제로 한 마이너 버전 차이(2.12.0 vs 2.13.0)이면서도 같은 cu130 계열이므로, 커널 ABI 충돌 없이 정상 동작합니다.

---

### 3.2 주의: 환경변수 정리 (가장 흔한 실패 원인)

DBR 19 클러스터에는 **`OPENSSL_FORCE_FIPS_MODE` 환경변수가 설정되어 있습니다.** 이 값을 그대로 두고 python을 실행하면 다음과 같이 **즉시 실패합니다:**

```
crypto/fips/fips.c:154 FATAL FIPS SELFTEST FAILURE
```

프로세스가 rc=134로 중단됩니다. 이는 설정값을 바꾸는 게 아니라 **환경에서 제거(unset)** 해야 합니다.

**venv 빌드 시점과 serve 실행 시점 모두에서** 다음을 먼저 실행하십시오:

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done
```

스크립트 `02_build_venv.py`와 `03_stage_weights.py`에는 이 정리가 이미 포함되어 있습니다.

---

### 3.3 venv 빌드

드라이버 노트북 셀에서 다음을 실행하십시오:

```python
%python
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/02_build_venv.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

#### 기대 결과

| 항목 | 예상값 |
|---|---|
| **소요 시간** | 약 66초 |
| **디렉터리 크기** | 7.6 GB |
| **vllm 버전** | 0.28.0 (정확히 고정) |
| **torch 버전** | 2.13.0+cu130 |
| **transformers 버전** | 5.16.1 |

스크립트 끝에서 다음과 같이 출력되면 성공입니다:

```
[02] SUCCESS: vllm 0.28.0 torch 2.13.0+cu130 13.0 tf 5.16.1
```

---

### 3.4 가중치 확보 — 두 가지 경로

Qwen3.8-27B-FP8 모델 가중치(~30 GB)는 두 가지 방법으로 준비할 수 있습니다.

#### (A) HuggingFace 직접 다운로드 (권장: 폐쇄망이 아닐 때)

**조건:**
- 클러스터에서 HuggingFace Hub로의 egress(아웃바운드)가 열려 있어야 함
- HF token이 있으면 더 좋지만 public 모델이므로 불필요

**실행:**

```python
%python
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/03_stage_weights.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

**기대 결과:**

| 항목 | 예상값 |
|---|---|
| **소요 시간** | 약 72초 — **네트워크에 따라 크게 다릅니다** (아래 참고) |
| **다운로드 크기** | 약 30 GB |
| **safetensors 파일 수** | 66개 |
| **저장 위치** | `/local_disk0/models/Qwen3.8-27B-FP8` |

> **소요 시간은 이 값에 맞추려 하지 마십시오.** 72초는 약 415 MB/s 에 해당하는 값이며,
> Azure koreacentral 에서 `hf_transfer` 로 측정한 결과입니다(2026-09-09 재현 시 75초).
> 리전이 다르거나 egress 대역폭이 제한된 환경에서는 **수십 분이 걸릴 수 있고 그것도 정상입니다.**
> 30 GB 를 100 Mbps 로 받으면 약 40분입니다. 진행률이 올라가고 있으면 기다리십시오.
> 폐쇄망이거나 반복 배포가 예상되면 방법 (B) 의 UC Volume 경로가 훨씬 빠르고 안정적입니다.

**원리:**
- `HF_HUB_ENABLE_HF_TRANSFER=1`로 병렬 스레드 다운로드 활성화
- `max_workers=8`로 다운로드 속도 향상
- `ignore_patterns=['*.pt','original/*']`로 불필요한 파일 제외

#### (B) Unity Catalog Volume 경로 (폐쇄망/에어갭 환경)

**전제조건:**
- 사전에 가중치가 UC Volume에 올려져 있어야 함
- 경로 예: `/Volumes/my_catalog/my_schema/model_weights/Qwen3.8-27B-FP8`

**실행:**

```python
%python
import os
os.environ["VOLUME_PATH"] = "/Volumes/my_catalog/my_schema/model_weights/Qwen3.8-27B-FP8"
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/03_stage_weights.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

**특징:**
- Volume에서 `/local_disk0`으로 복사
- `/local_disk0` 경로를 실제 serve 시 사용
- marker 파일로 멱등성 처리 (이미 복사됐으면 skip)

**폐쇄망에서 HuggingFace 접근을 원천 차단하려면** `ALLOW_HF_DOWNLOAD=false`를 함께 지정하십시오.
이 경우 `VOLUME_PATH` 복사가 실패하면 HF로 우회하지 않고 즉시 오류로 끝냅니다.

```python
os.environ["VOLUME_PATH"] = "/Volumes/my_catalog/my_schema/model_weights/Qwen3.8-27B-FP8"
os.environ["ALLOW_HF_DOWNLOAD"] = "false"
```

#### `03_stage_weights.py` 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `MODEL_HF_ID` | `Qwen/Qwen3.8-27B-FP8` | 다운로드할 HuggingFace 모델 ID |
| `LOCAL_PATH` | `/local_disk0/models/Qwen3.8-27B-FP8` | 가중치를 둘 로컬 경로 (변경 시 §4의 serve 경로도 함께 변경) |
| `VOLUME_PATH` | (없음) | 지정하면 HF 대신 이 경로에서 복사 |
| `ALLOW_HF_DOWNLOAD` | `true` | `false`면 HF 다운로드를 금지 (`VOLUME_PATH` 필수) |

**왜 직접 Volume을 읽지 않는가?**
UC Volume은 cloud blob 기반 storage이므로, vLLM이 매번 cloud API를 통해 가중치를 읽으면 cold-start가 느립니다. 따라서 한 번만 `/local_disk0`(NVMe SSD)으로 복사한 뒤, vLLM이 로컬 경로를 참조하게 하는 것이 최적입니다.

---

### 3.5 중요: /local_disk0 소멸

**`/local_disk0`은 클러스터별 ephemeral storage입니다.** 클러스터를 재시작하거나 terminate 후 새 클러스터를 만들면 `/local_disk0` 데이터는 모두 사라집니다.

따라서 **클러스터를 재기동할 때마다:**
- 부트스트랩 셀: 약 1분 (스크립트 복사 + 권한)
- venv 재빌드: 약 66초
- 가중치 재다운로드 또는 복사: 약 72초
- **합계: 약 2분 30초**

이는 **정상이며 오류가 아닙니다.** 스크립트의 marker 파일 처리로 인해 **같은 클러스터가 계속 떠 있는 동안은 한 번만 빌드/다운로드합니다.**

반복 배포가 예상되면 가중치를 **Unity Catalog Volume** 에 영구 보관하고 방법 (B) 로 복사하십시오.
재다운로드 없이 로컬 복사만 하므로 더 빠르고 외부 egress에 의존하지 않습니다.

---

### 3.6 모델 사양

#### 아키텍처

| 항목 | 값 |
|---|---|
| **모델 클래스** | `Qwen3_5ForConditionalGeneration` |
| **매개변수** | 27B(비양자화) |
| **레이어** | 64층 = 48×선형어텐션 + 16×풀어텐션 |
| **어텐션** | head_dim 256, kv_heads 4 |
| **최대 컨텍스트** | 262,144 토큰 (~103K 영어 단어) |
| **멀티모달** | 비전 블록 포함. 단 **권장 구성은 `--language-model-only` 로 텍스트 전용**입니다. 이미지를 쓰려면 §4.2 참조 |

#### 양자화 및 가중치

| 항목 | 값 |
|---|---|
| **양자화 방식** | FP8 (pre-quantized) |
| **양자화 형식** | e4m3 |
| **활성 스킴** | dynamic |
| **FP8 가중치 크기** | ~30 GB (66개 safetensors shard) |
| **BF16 원본 크기** | ~55.56 GB (비교 참고) |

#### GPU 연산

**중요:** NVIDIA A100(SM80)은 **native FP8 연산을 지원하지 않습니다.**

따라서 vLLM이 선택하는 커널은 **`MarlinFP8ScaledMMLinearKernel`**(W8A16 weight-only)이며, 다음과 같은 특징이 있습니다:

- **메모리 절감**: FP8 양자화로 모델 가중치가 약 50% 줄어듦
- **연산 가속 불가**: VRAM만 절감되고 계산 속도는 BF16과 동일
- **효율 평가**: 대역폭 제한 환경(메모리 접근이 병목)에서만 실질 가속

이는 의도된 설계이며, 양자화의 주 목표는 대기열 길이 단축(더 많은 시퀀스 동시 처리)입니다.

---

### 3.7 검증 체크리스트

#### venv 빌드 확인

```bash
source /local_disk0/vllm028/bin/activate
python -c "import vllm; print(vllm.__version__)"
# 출력: 0.28.0
```

#### 가중치 확인

```bash
ls -lh /local_disk0/models/Qwen3.8-27B-FP8/
# 다음 파일들이 존재하는지 확인:
# - config.json
# - tokenizer.json
# - model.safetensors.index.json
# - layers-0.safetensors ~ layers-63.safetensors (64개)
# - mtp.safetensors, outside.safetensors (2개)

# safetensors 총 개수 확인 (합계 66개)
ls /local_disk0/models/Qwen3.8-27B-FP8/*.safetensors | wc -l
# 출력: 66

du -sh /local_disk0/models/Qwen3.8-27B-FP8/
# 출력: 29G (약 30G)
```

> 이 모델의 shard 파일명은 `layers-N.safetensors` 형식입니다.
> `model-0000N-of-*.safetensors` 형식으로 세면 **0개가 나오며**, 정상 다운로드를
> 실패로 오판하게 됩니다. 반드시 위의 `*.safetensors` 패턴으로 확인하십시오.

#### 가중치 설정 검증

```python
import json
cfg = json.load(open("/local_disk0/models/Qwen3.8-27B-FP8/config.json"))
qc = cfg.get("quantization_config", {})
print(qc.get("quant_method"))  # 출력: fp8
print(qc.get("fmt"))           # 출력: e4m3
print(cfg.get("architectures")) # 출력: ['Qwen3_5ForConditionalGeneration']
print(cfg.get("text_config", {}).get("max_position_embeddings"))  # 출력: 262144
```

> 이 모델은 멀티모달 config 구조이므로 `max_position_embeddings`는 **최상위가 아니라
> `text_config` 아래에** 있습니다. `cfg.get("max_position_embeddings")`는 `None`을
> 반환하므로 컨텍스트 길이 확인에 사용할 수 없습니다.

모두 통과하면 §4 (vLLM serve 실행)로 진행할 준비가 됩니다.

---

### 3.8 다음 단계

가중치 검증 완료 후 §4로 진행하여 vLLM serve를 기동합니다.

---

## 4. vLLM serve 실행

**전제 조건**: 환경 구성(§3) 완료 · `/local_disk0/serve.log`에 쓰기 권한

**소요 시간**: 첫 기동 약 280~320초 · 캐시 후 재기동 약 190초

### 4.1 권장 설정 명령

> **먼저 결정하십시오 — §6 AI Gateway 까지 갈 예정입니까?**
>
> | 계획 | `--host` | 이유 |
> |---|---|---|
> | §5 검증까지만 (드라이버 내부에서만 호출) | `127.0.0.1` | 외부 노출 없음 |
> | **§6 AI Gateway 연결 · 외부 agent 호출** | **`0.0.0.0`** | driver-proxy 가 드라이버 **사설 IP** 로 접속하므로 loopback 이면 엔드포인트 호출이 전부 **502** |
>
> 게이트웨이를 쓸 가능성이 있으면 **처음부터 `0.0.0.0` 으로 띄우십시오.** 나중에 바꾸려면
> serve 를 다시 올려야 합니다(약 5분). 보안 영향은 §6.1 을 참조하십시오
> (`0.0.0.0` 은 VNet 에 8005 를 노출하며, 이 포트에는 인증이 없습니다).

> **아래 명령은 포그라운드에서 실행되어 종료되지 않습니다.** 노트북 셀에 그대로 붙이면 셀이
> 계속 실행 중 상태로 멈춰 있고, 셀을 중단하면 serve 도 함께 죽습니다. 노트북에서는
> **`04_serve.sh`(§4.10) 또는 §4.9 의 분리 실행**을 쓰십시오. 아래는 플래그의 정본입니다.

**환경 정리 후 다음을 실행하십시오:**

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done
# 캐시·임시 경로를 /local_disk0으로 고정 (검증된 구성)
export TMPDIR=/local_disk0/tmp
export HF_HOME=/local_disk0/hf
export VLLM_CACHE_ROOT=/local_disk0/vllm_cache
export TORCHINDUCTOR_CACHE_DIR=/local_disk0/inductor

/local_disk0/vllm028/bin/vllm serve /local_disk0/models/Qwen3.8-27B-FP8 \
  --served-model-name qwen38-27b --host 127.0.0.1 --port 8005 \
  --language-model-only --max-model-len 131072 \
  --gpu-memory-utilization 0.90 --max-num-seqs 32 \
  --max-num-batched-tokens 7840 --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  > /local_disk0/serve.log 2>&1
```

---

### 4.2 플래그별 근거

각 플래그가 이 값으로 설정되는 이유를 설명합니다.

| 플래그 | 설정값 | 근거 |
|---|---|---|
| `--served-model-name` | `qwen38-27b` | API 요청 시 참조할 모델 별칭. 바꾸려면 `04_serve.sh` 에 `SERVED_MODEL_NAME=<이름>` 을 넘기십시오(스크립트 기본값도 `qwen38-27b`). §6 엔드포인트의 모델명과 **반드시 일치**해야 합니다 |
| `--host` | `127.0.0.1` 또는 **`0.0.0.0`** | `127.0.0.1` 은 loopback 전용이라 드라이버 내부에서만 접근됩니다. **§6 AI Gateway 를 쓰려면 `0.0.0.0` 이 필수**입니다 — driver-proxy 는 드라이버 사설 IP 로 접속하므로 loopback 이면 전부 502 입니다. §4.1 상단의 결정 표를 참조하십시오 |
| `--port` | `8005` | 클러스터 기본 충돌 회피. 기본 8000 대신 별도 포트 사용 |
| `--language-model-only` | (플래그) | 텍스트 전용 서빙 시 VRAM 절감. 이미지 입력이 필요하면 이 플래그를 **제거**하고 `--limit-mm-per-prompt image=1` 추가 (실측: 이미지 처리 약 4.4초, VRAM +0.38 GiB) |
| `--max-model-len` | `131072` | 기본값 131K 토큰 = 약 250,000자(한국어). 프로덕션 권장. 262K도 가능하나 아래 별도 절 참조 |
| `--gpu-memory-utilization` | `0.90` | **0.90을 초과하지 마십시오** — A100 80GB SKU에서 상위 값으로 OOM 실패 전례. 0.90은 안전한 마진 |
| `--max-num-seqs` | `32` | 동시 시퀀스 수. 8로 설정 시 N=16 동시성에서 p95 지연이 13.19초인데, 32로 올리면 **7.03초**로 단축. 원인은 모델이 아니라 스케줄러 큐잉 최적화 |
| `--max-num-batched-tokens` | `7840` | 반드시 784의 배수(784 × 10). align 모드 스케줄러가 784 경계로 분할하므로 정렬 필요 |
| `--kv-cache-dtype` | `fp8` | KV 캐시를 FP8로 압축. 용량 **1.9배**(626,735 → 1,188,386 토큰), 131K 컨텍스트 동시성 **4.78x → 9.07x**. 지연은 동일(p95 7.08초 → 7.00초). 백엔드가 FLASH_ATTN에서 FLASHINFER로 변경. KV 캐시는 캘리브레이션 없으므로 프로덕션 전 정확도 검증 권장 |
| `--reasoning-parser` | `qwen3` | Qwen 응답에 포함된 `<think>` 블록이 클라이언트로 새지 않도록 처리(한국어 품질 5/5 확인) |

---

### 4.3 절대 넘기지 말 플래그

다음 플래그들은 이 설정에서 사용하거나 값을 변경하면 안 됩니다.

| 플래그 | 이유 |
|---|---|
| `--quantization <method>` | on-the-fly 양자화 변환은 OOM 유발. 모델이 이미 FP8 pre-quantized이므로 불필요 |
| `--block-size <N>` | 784(또는 kv fp8 ON 시 1568)가 자동 도출됨. 수동 지정 시 메모리 프로파일링 오류 |
| `--mamba-cache-mode` | 자동 해석됨. 수동 지정 시 recurrent state 누수 위험 |
| `--enforce-eager` | vLLM 0.21.0부터 graph 컴파일 메모리가 gmu 예산 내에서 자동 관리됨. 강제 활성화 시 불필요한 계산 반복 |
| `--trust-remote-code` | Qwen3.8 코드가 registry에 vendored되어 있으므로 불필요 |
| `--speculative-config` | speculative decoding(MTP)은 GDN recurrent state를 증대시키고, 실제로 N=8에서 8/16 요청이 실패. mamba padding도 0.13% → 1.52%로 증가 |

---

### 4.4 기동 로그 확인: Oracle 표

기동이 완료되면 다음 항목들을 로그에서 grep으로 확인할 수 있습니다.
**앞 4개 항목은 결정론적이므로 판정 기준이고, 뒤 3개는 참고 범위입니다**(아래 주의 참조).

**주의:** `block_size`는 `create_engine_config()` 시점에는 16으로 보이고, 디바이스 KV 프로파일링 단계에서 최종 결정되므로 **기동 로그로만 확인 가능**합니다. config 조회 API로는 보이지 않습니다.

| 확인 항목 | 로그 검색 문자열 | 기대값 (kv-cache-dtype fp8 ON) | 기대값 (kv-cache-dtype 미지정) |
|---|---|---|---|
| 가중치 커널 | `MarlinFP8ScaledMMLinearKernel` | 동일 | 동일 |
| attention 백엔드 | `Using ... attention backend` | `FLASHINFER` | `FLASH_ATTN` |
| block size | `Setting attention block size to N tokens` | `1568` | `784` |
| mamba padding | `Padding mamba page size by N%` | `0.13%` | `0.13%` |
| KV 캐시 용량 | `GPU KV cache size: N tokens` | 약 `1,188,000` (**±5%**) | 약 `627,000` (**±5%**) |
| 사용 가능 KV | `Available KV cache memory: N GiB` | 약 `39~41` | 약 `39~41` |
| 최대 동시성 | `Maximum concurrency for ...` (131K 컨텍스트) | 약 `9.1x` (**±5%**) | 약 `4.8x` (**±5%**) |

> ⚠️ **표의 마지막 세 항목(KV 캐시 용량·사용 가능 KV·최대 동시성)은 정확한 값 일치로
> 판정하지 마십시오.** 같은 플래그로 다시 띄워도
> 기동 시점의 여유 VRAM 에 따라 약 4% 변동합니다. 실측 2회 대조:
>
> | 실행 | Available KV | KV 캐시 용량 | 최대 동시성 |
> |---|---|---|---|
> | 2026-09-09 | `39.06 GiB` | `1,188,386` | `9.07x` |
> | 2026-09-07 (동일 플래그) | `40.78 GiB` | `1,240,814` | `9.47x` |
>
> **판정 기준은 앞 4개 항목**(커널·attention 백엔드·block size·mamba padding)입니다.
> 이들은 결정론적이며 값이 다르면 실제로 설정이 잘못된 것입니다.

**Grep 명령 예시:**

```bash
grep "Using.*attention backend" /local_disk0/serve.log
grep "Setting attention block size" /local_disk0/serve.log
grep "GPU KV cache size" /local_disk0/serve.log
grep "Available KV cache memory" /local_disk0/serve.log
```

---

### 4.5 기동 소요 시간

serve 프로세스가 `/health` 엔드포인트에서 200 상태를 반환할 때까지 대기해야 합니다.

| 상황 | 예상 시간 |
|---|---|
| **첫 기동** (torch.compile 캐시 비어있음, --max-model-len 131072) | 약 **280초** |
| **첫 기동** (torch.compile 캐시 비어있음, --max-model-len 262144) | 약 **320초** |
| **재기동** (torch.compile 캐시 있음) | 약 **190초** |

**클라이언트 대기 시간 설정:** `/health`가 200이 될 때까지 기다려야 하며, 최악의 경우를 고려해 timeout을 **20분(1200초)** 이상으로 설정하십시오. 첫 기동 시 torch 컴파일이 예상보다 길어질 수 있습니다.

---

### 4.6 VRAM 예산

A100 80GB 클러스터에서 예상되는 메모리 사용 패턴입니다.

#### 131K 컨텍스트 설정 (권장)

```
--max-model-len 131072 --gpu-memory-utilization 0.90 --kv-cache-dtype fp8
```

| 상태 | 사용량 | 비고 |
|---|---|---|
| idle | 약 69 GiB | 기동 직후 |
| 부하 peak | 약 71 GiB | 동시성 22, 최대 토큰 배치 시 |
| **여유 공간** | **약 9 GiB** | 80 GiB 중 |

#### 262K 컨텍스트 설정

262,144 토큰(최대 약 503,000자 한국어)을 지원하려면:

```
--max-model-len 262144 --gpu-memory-utilization 0.90 --kv-cache-dtype fp8
```

| 항목 | 값 | 비고 |
|---|---|---|
| `/health` 도달 시간 | 약 **320초** | 캐시 콜드 상태 |
| 사용 가능 KV 캐시 | 약 39.5~40.9 GiB | 실행마다 변동 (§4.4 참고) |
| KV 캐시 수용 토큰 | 약 **1,240,000개** | 262K 컨텍스트 약 **4.7x 동시 처리** |
| 모델 가중치 | 약 28.33 GiB | |
| idle VRAM | 약 69.3 GiB | |
| 부하 peak VRAM | **약 71.3 GiB** | **여유 8.7 GiB** |

**262K에서 실증된 최대 처리:**
- 120,000 토큰: 68초
- 200,000 토큰: 119초
- **249,123 토큰**: 151초 (바늘 정확도 100%)

**한국어 변환:** 토큰 비율이 약 0.52 tok/char이므로, 262K 컨텍스트는 약 503,000자에 해당합니다. 25만 토큰은 약 480,000자입니다.

---

### 4.7 선택 기준: 131K vs 262K

- **131K (기본 권장)**: 일반적인 업무용 대부분을 커버합니다(한국어 약 250,000자).
- **262K (장문 처리)**: 입력이 13만 토큰을 넘는 문서 처리가 필요할 때만 선택하십시오.
  - 기동 시간 추가: 약 40초 (280s → 320s)
  - 클라이언트 timeout 증가: 25만 토큰 요청 시 약 150초 필요
  - VRAM 여유 감소: 9 GiB → 8.7 GiB (마진 얇음)

---

### 4.8 262K 장문 프로필

131K 대신 262K를 사용하려면 권장 명령에서 `--max-model-len` 값만 변경하면 됩니다:

가장 간단한 방법은 `04_serve.sh`에 환경변수로 넘기는 것입니다 (환경 정리·캐시 경로·기동 확인이
스크립트에 이미 포함되어 있습니다).

```bash
MAX_MODEL_LEN=262144 bash /local_disk0/scripts/04_serve.sh
```

직접 실행하려면 §4.1과 동일하게 **환경 정리와 캐시 경로 설정을 반드시 포함**하고
`--max-model-len` 값만 바꿉니다.

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done
# 캐시·임시 경로를 /local_disk0으로 고정 (검증된 구성)
export TMPDIR=/local_disk0/tmp
export HF_HOME=/local_disk0/hf
export VLLM_CACHE_ROOT=/local_disk0/vllm_cache
export TORCHINDUCTOR_CACHE_DIR=/local_disk0/inductor

# 131K 대신 262K로 변경
/local_disk0/vllm028/bin/vllm serve /local_disk0/models/Qwen3.8-27B-FP8 \
  --served-model-name qwen38-27b --host 127.0.0.1 --port 8005 \
  --language-model-only --max-model-len 262144 \
  --gpu-memory-utilization 0.90 --max-num-seqs 32 \
  --max-num-batched-tokens 7840 --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  > /local_disk0/serve.log 2>&1
```

다른 설정은 모두 동일하며 gmu도 0.90 유지합니다.

**장문 요청 시 주의:**
- 컨텍스트 크기가 커질수록 첫 처리 지연이 증가합니다 (위 표 참조)
- 클라이언트의 HTTP timeout을 충분히 길게 설정하십시오 (25만 토큰에는 약 150초 필요)
- KV 캐시 메모리 프로파일은 안정적입니다 (여유 8.7 GiB)

---

### 4.9 분리 실행(Detached Serve)

노트북 셀이나 실행 컨텍스트가 끝나도 vLLM 서비스가 계속 떠 있어야 합니다. 다음과 같이 분리된 프로세스로 띄우십시오.

#### 기동

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done
# 캐시·임시 경로를 /local_disk0으로 고정 (검증된 구성)
export TMPDIR=/local_disk0/tmp
export HF_HOME=/local_disk0/hf
export VLLM_CACHE_ROOT=/local_disk0/vllm_cache
export TORCHINDUCTOR_CACHE_DIR=/local_disk0/inductor

setsid nohup /local_disk0/vllm028/bin/vllm serve \
  /local_disk0/models/Qwen3.8-27B-FP8 \
  --served-model-name qwen38-27b --host 127.0.0.1 --port 8005 \
  --language-model-only --max-model-len 131072 \
  --gpu-memory-utilization 0.90 --max-num-seqs 32 \
  --max-num-batched-tokens 7840 --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  > /local_disk0/serve.log 2>&1 &

echo "vLLM serve started. PID: $!"
```

**주요 요소:**
- `setsid`: 새 세션으로 프로세스 분리
- `nohup`: 터미널 닫힘 신호(SIGHUP) 무시
- `> /local_disk0/serve.log 2>&1`: 표준출력과 에러를 파일로 리다이렉트
- `&`: 백그라운드 실행

#### 분리 상태 확인

**정말 분리되었는지 확인하십시오.** 이 확인 없이 컨텍스트가 끝나면 서비스가 함께 죽을 수 있습니다.

```bash
# 프로세스 찾기 (argv[1]이 vllm 실행파일인 프로세스만 선택)
PID=$(ps -eo pid,args | awk '$3 ~ /bin\/vllm$/ {print $1; exit}')
echo "Found PID: $PID"

# 세션 분리 확인
ps -o pid,ppid,sid -p $PID

# 정상이면 다음과 같이 보입니다:
# PID    PPID   SID
# 5412   1      5412    <- SID가 자신의 PID와 같으면 분리됨 ✅
```

**sid == pid인지 확인하십시오.** 다르면 아직 부모 세션에 종속되어 있습니다.

> `pgrep -f 'vllm serve'`를 쓰면 **그 명령을 실행하는 셸 자신이 함께 매칭되어**
> PID가 여러 개 반환되고, `ps -p $PID`가 `list of process IDs must follow -p`로
> 실패합니다. serve 프로세스는 `python <venv>/bin/vllm serve …` 형태이므로
> argv의 두 번째 토큰(`awk`의 `$3`)으로 판별하는 위 방식을 사용하십시오.

#### 로그 확인

```bash
tail -f /local_disk0/serve.log

# 또는 /health 대기 (상태코드로 판정)
timeout 1200 bash -c 'until [ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8005/health)" = "200" ]; do sleep 2; done' && echo "Ready" || echo "Timeout"
```

> vLLM의 `/health`는 **본문이 비어 있는 HTTP 200**을 반환합니다(본문 0바이트).
> 따라서 `curl … | grep -q ok`처럼 본문에서 문자열을 찾는 방식은 서버가 완전히
> 정상이어도 **절대 매칭되지 않아** 20분을 기다린 뒤 `Timeout`을 출력합니다.
> 반드시 위와 같이 상태코드로 판정하십시오.

---

### 4.10 정지 및 정리

재기동 전에 기존 프로세스와 포트를 정리해야 합니다. 그렇지 않으면 다음 기동이
`Engine core initialization failed`로 실패합니다.

#### 가장 간단한 방법

`04_serve.sh`는 기동 전에 정리를 스스로 수행합니다. 재기동만 하려면 별도 정리 없이
그대로 다시 실행하십시오.

> **스크립트 안의 `pkill -9 -f 'vllm serve'` 는 아래 경고와 모순되지 않습니다.**
> 아래에서 금지하는 것은 **노트북 `%sh` 셀에 그 명령을 직접 쓰는 것**입니다. 그 경우 셀의 셸이
> 명령 문자열을 자기 argv 에 갖고 있어 자기 자신이 매칭됩니다. `04_serve.sh` 를 실행할 때
> 셸의 argv 는 `bash …/04_serve.sh` 이므로 패턴에 걸리지 않습니다.
> **스크립트를 "고치지" 마십시오.**

```bash
bash /local_disk0/scripts/04_serve.sh
```

#### 수동 정리 (노트북 Python 셀)

수동으로 정리해야 한다면 아래를 사용하십시오. VRAM 반환까지 확인합니다.

```python
import os, subprocess, time

PATTERNS = ("/vllm028/bin/vllm", "VLLM::EngineCore", "VllmWorker", "resource_tracker")
ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
killed = []
for line in ps.splitlines()[1:]:
    pid, _, args = line.strip().partition(" ")
    if pid == str(os.getpid()):
        continue
    # 괄호가 없으면 `and` 가 먼저 묶여 PATTERNS 중 일부가 무효가 되고,
    # 무관한 프로세스가 "VLLM::" 만으로 죽습니다. 괄호를 빼지 마십시오.
    if any(x in args for x in PATTERNS) and ("vllm028" in args or "VLLM::" in args):
        try:
            os.kill(int(pid), 9); killed.append((pid, args[:40]))
        except Exception as e:
            print("skip", pid, e)
print("killed:", killed)

for i in range(24):
    used = int(subprocess.run(
        "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits",
        shell=True, capture_output=True, text=True).stdout.strip().split("\n")[0])
    if used < 5000:
        print(f"VRAM 반환 확인: {used} MiB ({i*2}초)"); break
    time.sleep(2)
else:
    print(f"VRAM 미반환: {used} MiB — 클러스터 재시작이 필요할 수 있습니다")
```

정상 출력 예:

```
killed: [('5814', '/local_disk0/vllm028/bin/python -c from '), ('5815', 'VLLM::EngineCore')]
VRAM 반환 확인: 0 MiB (2초)
```

#### ⚠️ `pkill -9 -f 'vllm serve'`를 노트북 `%sh` 셀에서 쓰지 마십시오

두 가지 이유로 위험합니다.

1. **셀 자신이 죽습니다.** `%sh` 셀의 셸은 명령 문자열을 자기 argv에 담고 있으므로
   `vllm serve` 패턴에 **자기 자신이 매칭**됩니다. 셀은 출력 한 줄 없이 즉시 끝나고
   Databricks는 이를 **성공으로 표시**하므로, 실패했다는 신호조차 남지 않습니다.
2. **EngineCore가 살아남습니다.** 실제 VRAM을 쥐고 있는 자식 프로세스의 이름은
   `VLLM::EngineCore`로, `vllm serve` 패턴에 걸리지 않습니다. 실측 결과 API 서버만
   죽고 포트는 해제되었지만 **VRAM 72,310 MiB / 81,920 MiB가 그대로 점유**된 상태가
   남았습니다. 이 상태에서 재기동하면 반드시 실패합니다.

#### 포트만 확인할 때

```bash
ss -ltnp | grep 8005 || echo "(listen 없음)"
```

#### GPU VRAM 반환 확인

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
# 정상: 0 MiB, 81920 MiB

nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
# 정상: 출력 없음 (GPU 점유 프로세스 없음)
```

VRAM이 완전히 반환되지 않으면 드라이버 재시작이 필요할 수 있습니다(클러스터 재시작).

---

### 4.11 성능 기준선

정상 기동 후 예상되는 성능 지표입니다. 자신의 환경에서 측정한 값과 비교하십시오.

**측정 조건:** 동시성 22, `max_tokens` 256, `--kv-cache-dtype fp8` ON, `--max-model-len 131072`, `--max-num-seqs 32`

| 지표 | 기대값 | 비고 |
|---|---|---|
| 처리량 (rps) | 약 **2.85** | 초당 요청 수 |
| p50 지연 | 약 **8.7초** | 중앙값 |
| p95 지연 | 약 **8.9초** | 95 백분위 |
| GPU 사용률 | 약 **100%** | 정상 포화 |
| prompt 생성 속도 | 약 **90–104 tok/s** | 입력 처리 속도 |
| generation 속도 | 약 **650–720 tok/s** | 출력 생성 속도 |

**N=16(동시성 16) 참고값:**
- p95 지연: 약 **7.0초** (N=22보다 낮음)

**8시간 연속 운영:** 4시간 이후 지연이 수렴하고 열화가 없습니다.
- 0~3시간: p95가 +0.23초(+2.7%) 상승 (열과 스케줄러 워밍업)
- 3~8시간: p95 8.94초에서 평탄 유지 (누수 없음)
- VRAM: 초기 +88 MB 계단 후 완전히 평탄 (mamba state 누수 0)

---

### 4.12 다음 단계

serve가 정상 기동되면 클라이언트 코드에서 OpenAI-compatible API로 접근할 수 있습니다:

> **반드시 `/v1/chat/completions` 를 쓰십시오.** 레거시 `/v1/completions`(`prompt` 방식)는
> 채팅 템플릿을 적용하지 않으므로 `--reasoning-parser qwen3` 경로를 우회합니다.
> §5 의 검증(`05_validate.py`)도 전부 채팅 엔드포인트로 수행합니다.
>
> 같은 프롬프트로 두 엔드포인트를 실측 대조한 결과입니다.
>
> | 항목 | `/v1/chat/completions` | `/v1/completions` (레거시) |
> |---|---|---|
> | `prompt_tokens` (동일 입력) | 80 | **28** ← 채팅 템플릿이 적용되지 않음 |
> | `message.reasoning` | 있음 (325자) | **없음** |
> | `reasoning_tokens` | 127 | `completion_tokens_details: null` |
> | 출력 형태 | 정상 답변 | 프롬프트를 되풀이하고 `답변1` 같은 머리말을 스스로 만들어 붙임 |
>
> 두 경우 모두 답(345)은 맞았고 `<think>` 유출도 없었지만, 레거시 경로는 **추론 내용을 분리해
> 주지 않고** 출력 형태도 어시스턴트 응답이 아닙니다.

```bash
curl http://127.0.0.1:8005/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen38-27b",
    "messages": [{"role": "user", "content": "안녕하세요. 오늘의 날씨는?"}],
    "max_tokens": 1500
  }'
```

Python 클라이언트 예시:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8005/v1",
    api_key="not-needed"       # loopback 직접 호출이므로 인증 없음
)

resp = client.chat.completions.create(
    model="qwen38-27b",
    messages=[{"role": "user", "content": "안녕하세요."}],
    max_tokens=1500,           # 추론 토큰이 먼저 소비되므로 넉넉히 (아래 참고)
)
print(resp.choices[0].message.content)
```

> **`max_tokens` 는 넉넉히 주십시오.** 이 모델은 답을 쓰기 전에 추론하고, 추론 토큰이
> `max_tokens` 에서 **먼저** 차감됩니다. 한도가 작으면 추론만 하다 끝나고 `content` 가
> **빈 문자열**로 돌아옵니다(오류가 아니라 `finish_reason: length`).
> 실측: 같은 프롬프트를 `300` 으로 6회 호출했을 때 5회가 빈 본문이었고, `1500` 에서는
> 5/5 정상이었습니다. 추론 내용은 `message.reasoning` 필드에 따로 담깁니다.

§5 (검증)로 진행하여 배포가 정상인지 확인하십시오.

---

## 5. 검증: 빠른 검증 및 장시간 안정성

**전제 조건**: vLLM serve가 떠 있는 상태 · §4 기동 완료

**실행 위치**: **serve가 떠 있는 같은 클러스터의 드라이버에서 실행해야 합니다** (loopback `127.0.0.1:8005`로 접속하므로). 다른 곳에서 실행하면 연결 실패합니다.

**소요 시간**: (1) 빠른 검증 약 **10분** / (2) 장시간 안정성 **8시간** (선택)

---

### 5.1 1단계: 빠른 검증 (~10분)

`05_validate.py`로 다음 8개 항목을 점검합니다. 모두 통과해야 배포가 정상입니다.

#### 실행 방법

Notebook에서 다음을 실행하십시오 (드라이버 노드에서 실행되어야 함):

```python
%python
import os
os.environ["PORT"] = "8005"
os.environ["SERVED"] = "qwen38-27b"
os.environ["OUT"] = "/local_disk0/validate_result.json"
os.environ["SERVE_LOG"] = "/local_disk0/serve.log"

import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/05_validate.py"], capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

또는 쉘에서:

```bash
PORT=8005 SERVED=qwen38-27b OUT=/local_disk0/validate_result.json \
  SERVE_LOG=/local_disk0/serve.log python3 /local_disk0/scripts/05_validate.py
```

#### 검증 항목 설명

| # | 항목 | 기대값 | 실패 시 의미 |
|---|---|---|---|
| 1 | `/health` 도달 | 200 | serve가 준비 안 됨 |
| 2 | `/v1/models` 확인 | served name 있음 | 모델 로드 실패 |
| 3 | 한국어 품질 5건 | 5/5 통과 | 한국어 응답 부정상 |
| 4 | `<think>` 미유출 | 0건 | 추론 캐시가 응답에 노출 |
| 5 | 동시성 N=16 | 오류 0 (p95 지연은 **참고값**이며 판정 기준이 아닙니다 — 프롬프트·응답 길이에 따라 달라집니다) | 높은 동시 부하에서 에러 |
| 6 | 장문 처리 ~32K | 바늘 회수 | 컨텍스트 이해 불가 |
| 7 | 기동 oracle | 주요 항목 감지 | 커널/attention 설정 확인용 |
| 8 | VRAM | 정보 수집 | 메모리 리소스 확인용 |

#### 검증 결과 기준선

**기대 결과**:

| 항목 | 기준선 |
|---|---|
| `/health` 도달 | 초회 약 **280초** / 캐시 후 약 **190초** |
| 한국어 품질 | **5/5 통과**, `<think>` 유출 **0** |
| N=16 p95 지연 | 약 **1.3초** (이 검증은 짧은 인사 프롬프트를 사용) |
| N=16 p95 지연 (상담 시나리오 프롬프트, `max_tokens` 256) | 약 **7.0초** |
| VRAM (131K 프로필) | idle 약 **69 GiB** / 부하 피크 약 **71 GiB** |

**실행 예시 (정상)**:

```
검증 시작 (서버: qwen38-27b @ 8005)

PASS | 1. /health 도달: 200
PASS | 2. /v1/models 서빙 확인: qwen38-27b
PASS | 3. 한국어 품질 5건: 5/5 (모두 한국어 응답 받음)
PASS | 4. <think> 미유출: 정상
PASS | 5. 동시성 N=16: 0 오류, p95=1.29초
PASS | 6. 장문 처리 ~32K: 약 31932토큰, 13.5초, 바늘 회수
PASS | 7. 기동 oracle: attention=yes, block_size=1568, kernel=yes, kv_cache=1,188,386, padding=0.13
PASS | 8. VRAM: 72310 / 81920 MB (88.3%), 여유 9610 MB

======================================================================
| 항목 | 결과 | 상세 |
|---|---|---|
| 1. /health 도달 | PASS | 200 |
| 2. /v1/models 서빙 확인 | PASS | qwen38-27b |
| 3. 한국어 품질 5건 | PASS | 5/5 (모두 한국어 응답 받음) |
| 4. <think> 미유출 | PASS | 정상 |
| 5. 동시성 N=16 | PASS | 0 오류, p95=7.02초 |
| 6. 장문 처리 ~32K | PASS | 약 31824토큰, 10.2초, 바늘 회수 |
| 7. 기동 oracle | PASS | attention=FLASHINFER, block_size=1568, kv_cache=1188386 |
| 8. VRAM | PASS | 70412 / 81920 MB (85.9%), 여유 11508 MB |
======================================================================

결과 저장: /local_disk0/validate_result.json
모든 검증 통과 (8/8)
```

#### 실패 시 확인 순서

**`/health` 실패 (1번)**
- serve가 띄어져 있는지 확인: `curl -v http://127.0.0.1:8005/health`
- 280초 이상 기다렸는지 확인 (초회 기동 시간)
- `/local_disk0/serve.log` 마지막 100줄 확인: `tail -100 /local_disk0/serve.log`

**한국어 품질 실패 (3번) 또는 `<think>` 유출 (4번)**
- 모델의 reasoning_parser 설정 확인: serve 로그에 `--reasoning-parser qwen3` 있는지 확인
- 개별 요청으로 테스트: `curl -X POST http://127.0.0.1:8005/v1/chat/completions ...`

**동시성 오류 (5번)**
- 동시 부하 항목만 다시 확인: `PORT=8005 SERVED=qwen38-27b python3 /local_disk0/scripts/05_validate.py` 재실행 (동시성 16은 스크립트에 고정되어 있으며 환경변수로 바꿀 수 없습니다)
- serve의 `/metrics` 확인: `curl http://127.0.0.1:8005/metrics | grep "num_requests"`

**장문 처리 실패 (6번)**
- 프롬프트 토큰 수 부족 확인: 로그의 달성 토큰이 목표(~32,000)에 도달했는지
- VRAM 여유 확인 (8번 항목): 부하 시 최대 메모리 사용량 확인
- 컨텍스트 길이 제한 확인: `--max-model-len 131072` 설정 여부

---

### 5.2 2단계: 장시간 안정성 테스트 (8시간 - 선택)

`06_soak.py`로 8시간 지속 부하에서 다음을 확인합니다:

- VRAM 누수 없음
- 지연 시간 안정적 (열화 없음)
- 요청 오류 0
- KV 캐시 누수 없음 (Mamba 모델의 고유 위험)

#### 사전 조건

- `05_validate.py` 8/8 통과 완료
- 충분한 시간 (약 8.5시간 = 배포 시간 포함)
- serve 실행 중 상태 유지 가능 (클러스터 restart 불가)
- **클러스터의 `autotermination_minutes` 가 `0` 이어야 합니다** — 아래 확인 필수

> ⚠️ **기본값(90분)으로는 이 시험이 약 90분 만에 죽습니다.**
> 이 시험은 분리 실행되므로 노트북 셀이 돌지 않고, Databricks 자동 종료는 *"마지막 **명령**
> 실행"* 기준이라 추론 트래픽으로는 갱신되지 않습니다.
>
> ```bash
> databricks clusters get <CLUSTER_ID> --profile <PROFILE> | grep autotermination_minutes
> # 0 이 아니면 이 시험을 시작하지 마십시오
> ```
>
> **`0` 이 아니라면**: 실행 중 클러스터를 고치면 **재시작되어 `/local_disk0` 이 비워지므로**
> venv·가중치·serve 를 처음부터 다시 올려야 합니다(§3.5). 이 경우 `01_cluster.json` 에
> `"autotermination_minutes": 0` 을 넣어 **클러스터를 새로 만들고** §2 부터 다시 진행하는 편이
> 확실합니다. 자세한 근거는 §1.3 을 참조하십시오.

#### 실행 방법

이 테스트는 **이미 떠 있는 serve**에만 부하를 줍니다 (serve를 직접 띄우지 않습니다).
드라이버 노트북에서 실행하십시오:

```python
%python
import os
os.environ["DURATION_S"] = "28800"  # 8시간
os.environ["CONCURRENCY"] = "22"
os.environ["MAX_TOKENS"] = "256"
os.environ["PORT"] = "8005"
os.environ["SERVED"] = "qwen38-27b"
os.environ["OUT"] = "/local_disk0/SOAK_result.json"

import subprocess
# 8시간 실행이므로 노트북 셀이 붙잡고 있지 않도록 분리 실행한다.
# 셀이 끊겨도 시험은 계속되며, 진행 상황은 아래 '진행 상황 확인 셀'로 확인한다.
_p = subprocess.Popen(["setsid", "nohup", "python3", "/local_disk0/scripts/06_soak.py"],
                      stdout=open("/local_disk0/soak_stdout.log", "w"),
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      start_new_session=True)
print(f"분리 실행 시작 pid={_p.pid}  로그: /local_disk0/soak_stdout.log")
```

또는 쉘에서 (백그라운드 실행 권장):

```bash
nohup python3 /local_disk0/scripts/06_soak.py > /local_disk0/soak.log 2>&1 &
```

#### 진행 상황 확인 셀

분리 실행했으므로 진행 상황은 아래 셀로 확인합니다. **몇 번이든 반복 실행**할 수 있습니다.
결과 JSON은 60초마다 갱신됩니다.

```python
import json, os

OUT = "/local_disk0/SOAK_result.json"
DONE = "/local_disk0/SOAK_DONE"
LOG = "/local_disk0/soak_stdout.log"

done = os.path.exists(DONE)
print("완료 마커:", "있음 (시험 종료)" if done else "없음 (진행 중)")

if os.path.exists(OUT):
    with open(OUT) as f:
        R = json.load(f)
    t = R.get("totals", {})
    cfg = R.get("config", {})
    el = t.get("elapsed_s") or 0
    dur = cfg.get("duration_s") or 0
    ratio = (el / dur * 100) if dur else 0
    # verdict는 시험이 끝나기 전에도 "completed"로 채워져 있으므로
    # 완료 마커가 없으면 진행 중으로 표시한다.
    state = t.get("verdict") if done else "진행 중"
    print(f"경과 {el:.0f}초 / {dur}초 ({ratio:.1f}%)   상태: {state}")
    print(f"요청 성공 {t.get('ok')} · 오류 {t.get('err')} · 처리량 {t.get('rps')} rps")
    p50, p95 = t.get("recent_p50"), t.get("recent_p95")
    print(f"지연 p50 {p50}초 · p95 {p95}초" if p50 is not None else "지연: 집계 전 (첫 응답 대기 중)")
    print(f"VRAM {t.get('vram_first_mb')} → {t.get('vram_last_mb')} MB "
          f"(peak {t.get('vram_peak_mb')} · drift {t.get('vram_drift_pct')}% · "
          f"측정 창 {t.get('vram_window_h')}시간 · 샘플 {t.get('vram_samples')})")
    hc = R.get("health_checks", [])
    print(f"/health 점검 {sum(1 for h in hc if h.get('status') == 200)}/{len(hc)} 200")
    if t.get("recent_errors"):
        print("최근 오류:", t["recent_errors"])
    if t.get("early_stop_reason"):
        print("조기 중단 사유:", t["early_stop_reason"])
else:
    print(f"{OUT} 아직 없습니다 (최초 스냅샷은 시작 후 약 60초)")

if os.path.exists(LOG):
    with open(LOG) as f:
        lines = f.readlines()
    print(f"\n--- soak_stdout.log 마지막 12줄 (총 {len(lines)}줄) ---")
    print("".join(lines[-12:]))
```

진행 중 출력 예:

```
완료 마커: 없음 (진행 중)
경과 124초 / 600초 (20.6%)   상태: 진행 중
요청 성공 355 · 오류 0 · 처리량 2.87 rps
지연 p50 8.221초 · p95 8.386초
VRAM 72310 → 72314 MB (peak 72314 · drift 0.006% · 측정 창 0.034시간 · 샘플 13)
/health 점검 1/1 200
```

> `SOAK_result.json`의 `verdict`는 시험이 끝나기 전에도 `completed`로 채워져 있습니다.
> 완료 여부는 **`verdict`가 아니라 `/local_disk0/SOAK_DONE` 파일의 존재**로 판단하십시오.

#### `06_soak.py` 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `DURATION_S` | `28800` (8시간) | 시험 시간(초) |
| `CONCURRENCY` | `22` | 동시 요청 수 |
| `MAX_TOKENS` | `256` | 응답 최대 토큰 |
| `PORT` | `8005` | serve 포트 |
| `SERVED` | `qwen38-27b` | 모델 별칭 |
| `OUT` | `/local_disk0/SOAK_result.json` | 최종 결과 JSON |
| `DONE_MARKER` | `/local_disk0/SOAK_DONE` | 완료 표시 파일 (진행 중 존재 여부로 완료 판단) |
| `METRICS_LOG` | (없음) | 지정하면 60초 간격 메트릭을 이 파일에 기록 |

시험 시간을 줄여 계측만 확인하려면 `DURATION_S`를 짧게 지정하십시오
(예: `os.environ["DURATION_S"] = "600"` → 10분).

#### 검증 내용

| 지표 | 기대값 | 기준선 |
|---|---|---|
| 요청 총수 | 많을수록 좋음 | ~82,000 (8시간, 동시 22) |
| 오류율 | **0%** | **0/82,129** |
| `/health` 점검 | 모두 200 | 16/16 (30분 간격) |
| 대기열 최대 | 0 (미포화) | waiting max 0 |
| VRAM drift | ±0.3% | **0.122%** (측정 창 7.998시간) |
| p95 지연 변화 | ±5% | 0h: 8.71s → 3h: 8.94s → 7h: 8.92s (**3h까지 +2.7% 상승 후 평탄**) |

#### 결과 기준선

**8시간 완주 결과**:

실측 기준선 요약:
- **시작/종료**: 약 8.00시간 완주
- **동시성**: 22
- **요청**: 성공 82,129 · 오류 0 · 처리량 2.85 rps
- **지연**: 0시간 p95=8.71s → 3시간 8.94s (peak, +2.7%) → 7시간 8.92s (평탄 유지)
- **VRAM**: 초기 후 +88 MB (1회 계단) → 2h 이후 완전히 평탄, drift 0.122%
- **/health**: 16회 점검 중 16회 200 (모두 성공)
- **대기열**: running 0~22 (정상) · waiting 최대 0 (미포화)

#### 판정 기준

**당신의 결과 해석**:

- **VRAM drift < 0.2%**: 정상
- **p95 3시간 이후 안정**: 정상
- **오류 0**: 정상
- **대기열 0**: 정상

#### ⚠️ VRAM 누수 판정 방법 주의

`kv_cache_usage_perc`의 "처음 vs 마지막" 단순 비교는 **워밍업 구간이 앞 평균을 낮춰서** 증가처럼 보입니다 (오판).

**올바른 판정**:

1. **워밍업 구간 제외** (처음 30분): 초기 캐시 채움 단계는 무시
2. **정상상태에서만 판정** (2시간 이후): 충분히 주행한 후 기울기 계산
3. **max값 확인**: 전구간 최대값이 정상 범위를 넘지 않으면 정상

#### 결과 저장 위치

```
/local_disk0/SOAK_result.json
```

구조:

```json
{
  "config": {
    "duration_s": 28800,
    "concurrency": 22,
    "max_tokens": 256
  },
  "totals": {
    "elapsed_s": 28800.0,
    "ok": 82129,
    "err": 0,
    "rps": 2.85,
    "vram_drift_pct": 0.122,
    "vram_window_h": 7.998,
    "recent_p50": 8.712,
    "recent_p95": 8.917
  }
}
```

---

### 5.3 참고: 셀프테스트 모드

`05_validate.py`에는 **오프라인 자기검증 모드**가 있습니다. 서버 없이 크기 산술, 질문 누수, 채점 로직을 검증합니다:

```bash
SELFTEST=1 python3 /local_disk0/scripts/05_validate.py
```

이 모드는 배포 전 스크립트 자체가 정상인지 빠르게 확인할 때 유용합니다.

---

### 5.4 다음 단계

검증 완료 후 문제가 발생하면 §7 (배포 단계 문제 해결)을 참조하십시오.
이어서 **§6 (AI Gateway 연결)** 으로 진행하면 외부 agent 와 AI Playground 에서 이 모델을 쓸 수 있습니다.

---

## 6. AI Gateway 연결 및 테스트

> **이 장은 별도 문서로 분리되었습니다.** 아래 두 자산으로 진행하십시오.

| 자산 | 내용 |
|---|---|
| **`AI_GATEWAY_REGISTRATION_GUIDE.md`** | 워크스페이스 전제조건 점검 → 엔드포인트 생성 → 검증 → 사용 → 운영·문제 해결 |
| **`notebook_gateway_register.py`** | 실행 노트북 (셀 6개 · 실행 코드 140줄 · **외부 파일 의존 없음**) |

그 문서로 넘어가면 vLLM 이 표준 **OpenAI 호환 서빙 엔드포인트**로 노출되고, AI Playground 에서
바로 대화할 수 있으며, rate limit 과 usage tracking 이 적용됩니다.

### 넘어가기 전에 확인하십시오

| # | 확인 | 아니라면 |
|---|---|---|
| 1 | vLLM 이 **`--host 0.0.0.0`** 으로 떠 있음 | 게이트웨이 가이드 §2 에서 재기동합니다. §4.1 의 결정 표에서 `0.0.0.0` 을 골랐다면 **§2 를 건너뜁니다** |
| 2 | 클러스터 `autotermination_minutes` 가 **`0`** | 자동 종료되면 엔드포인트는 `READY` 인데 호출이 전부 실패합니다. 근거와 조치는 §1.3 |
| 3 | 워크스페이스에서 **PAT 발급이 가능**함 | 상류 인증 수단이 없어 이 방식이 성립하지 않습니다. 게이트웨이 가이드 §0.1 |

**특히 게이트웨이 가이드 §0.2 「도달성 사전 시험」은 이 문서 §1 보다 먼저 하는 편이 좋습니다.**
Private Link·egress 제한으로 막히는 환경이라면 30GB 가중치를 투입하기 전에 5분 만에 판별됩니다.

> 이 장의 구버전 원문(2026-09-07 검증)은 내부 아카이브에 보존돼 있으며 이 패키지에는
> 포함하지 않았습니다. 현행 문서로 진행하십시오.

---

## 7. 배포 단계 문제 해결 요약

배포 단계에서 발생 가능한 문제들을 증상별로 정리했습니다. 아래 표에서 해당하는 문제를 찾아 조치하십시오.

| 증상 | 원인 | 조치 |
|---|---|---|
| 클러스터 기동 직후 즉시 종료, 에러 `INVALID_WORKER_ENVIRONMENT` | 신규 워크스페이스 백엔드 초기화 미완 | `databricks clusters start <CLUSTER_ID> --profile <PROFILE>` 로 재시도 (약 5~10분 소요) |
| python 프로세스 즉시 종료, `FATAL FIPS SELFTEST FAILURE` (rc=134) | `OPENSSL_FORCE_FIPS_MODE` 환경변수 설정되어 있음 | 실행 전 `unset PYTHONPATH LD_LIBRARY_PATH` 와 OPENSSL/FIPS 변수 제거. 스크립트에는 이미 포함됨 |
| `/health` 계속 503 또는 응답 없음, 20분 이상 대기 | serve 컴파일 중이거나 OOM으로 기동 실패 | `/local_disk0/serve.log` 마지막 40줄 확인: `tail -40 /local_disk0/serve.log`. OOM 메시지 확인: `grep -i "out of memory"` |
| OOM으로 serve 기동 실패 · `Engine core initialization failed` | `--gpu-memory-utilization` 0.90 초과 또는 이전 `VLLM::EngineCore`가 VRAM 점유 | ① 명령에서 `--gpu-memory-utilization 0.90`으로 설정 확인 ② **§4.10의 수동 정리 셀**로 좀비 프로세스 정리 (`pkill -f 'vllm serve'`는 EngineCore를 남깁니다) ③ `nvidia-smi`로 VRAM 0 MiB 반환 확인 |
| 포트 8005 점유, `Address already in use` | 이전 serve가 아직 떠 있음 | `fuser -k 8005/tcp` 또는 `lsof -ti :8005 \| xargs kill -9` (포트만 해제되고 VRAM은 남을 수 있으므로 §4.10 정리도 함께 수행) |
| 검증 스크립트 `/health` 실패 (연결 거부) | serve가 다른 호스트나 포트에서 실행 중, 또는 검증이 다른 노드에서 실행됨 | 검증 스크립트를 serve가 떠 있는 같은 드라이버에서 실행. loopback `127.0.0.1:8005` 접속이므로 원격 실행 불가 |
| 검증 `SERVE_LOG` 지정 누락으로 기동 oracle SKIP | 로그 경로 불일치 | 환경변수 `SERVE_LOG=/local_disk0/serve.log` 지정 후 재실행 |
| 가중치 다운로드 실패, HuggingFace 연결 불가 | 드라이버 egress 차단 (폐쇄망/에어갭) | UC Volume 경로로 전환. §3.4 방법 B 참조. 또는 Azure Blob Storage 등에 사전 업로드 |
| 클러스터가 계속 과금됨, 비용 누적 | 장시간 시험용으로 `autotermination_minutes: 0` 설정 후 종료 불완료 | `databricks clusters delete <CLUSTER_ID> --profile <PROFILE>` 로 반드시 종료. `databricks clusters list` 로 상태 확인 |
| 검증 중 한국어 품질 실패 (3번) 또는 `<think>` 유출 (4번) | `--reasoning-parser qwen3` 설정 누락 | serve 로그에 이 플래그가 있는지 확인. 없으면 명령 수정 후 재기동 |
| 검증 중 동시성 오류 (5번) — 높은 동시 부하에서 요청 실패 | 동시 시퀀스 수 부족 또는 배치 토큰 설정 오류 | `--max-num-seqs 32` 확인. 필요시 로그 확인: `curl http://127.0.0.1:8005/metrics` |
| 검증 중 장문 처리 실패 (6번) — 바늘 회수 불가 | 컨텍스트 이해 부정상 또는 토큰 부족 | 프롬프트 토큰 수 확인 (목표 ~32,000). VRAM 여유 확인 (아이템 8). 필요시 컨텍스트 길이 재설정 |
| serve 응답에 `<think>` 블록 노출 | reasoning_parser 미작동 | serve 명령에 `--reasoning-parser qwen3` 포함 확인. 로그 검색: `grep "reasoning_parser" /local_disk0/serve.log` |
