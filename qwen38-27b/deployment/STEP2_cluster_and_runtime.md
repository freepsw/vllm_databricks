# STEP 2 · 클러스터 생성 · 스크립트 배포 · venv 빌드

**이 단계에서 하는 일**: GPU 클러스터를 만들어 기동하고, 스크립트 7개를 드라이버로 옮기고, vLLM 0.28.0 전용 격리 venv 를 빌드합니다.  
**소요 시간**: 클러스터 기동 **약 6분** + 스크립트 스테이징 **즉시** + venv 빌드 **62~77초**  
**끝났는지 판단하는 기준**: 클러스터 `RUNNING` · `/local_disk0/scripts` 에 파일 **7개** · `00_check_prereq.py` 전 항목 PASS · `import vllm; vllm.__version__` 이 **`0.28.0`** 출력.

---

## 🔴 클러스터를 만들기 전에 먼저 정할 것 두 가지

| 항목 | 빠른 검증만 (약 10분) | 8시간 soak · 게이트웨이 · 상시 서비스 |
|---|---|---|
| **`autotermination_minutes`** | **`90`** (`01_cluster.json` 의 기본값 그대로) | **`0`** (필수 · **생성 시점에** 지정) |
| **`--host` (STEP3에서)** | `127.0.0.1` (로컬 테스트) | **`0.0.0.0`** (게이트웨이 연결) |

> ⚠️ **`autotermination_minutes` 을 나중에 바꾸면 클러스터가 재시작되고 `/local_disk0` 이 비워져 처음부터 다시 해야 합니다.**  
> 게이트웨이·상시 서비스·8시간 시험을 할 가능성이 조금이라도 있으면 **처음부터 `0` 으로 만드십시오.**

---

## 1. 클러스터 생성

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

### 1.2 Databricks 런타임 및 GPU 사양

#### 런타임

**`19.x-gpu-ml-scala2.13`**를 선택합니다.

이 런타임은 다음을 제공합니다:
- **드라이버**: 580.x 계열 (NVIDIA 최신 드라이버)
- **CUDA**: 13.0 (cuda-toolkit 13.0.2)
- **GPU**: NVIDIA A100 80GB, compute capability **8.0**

이 조합이 중요합니다. 드라이버 580과 CUDA 13.0은 최신 vLLM 바이너리(cu130 wheel)를 올바르게 지원하므로,  
처음부터 최적화된 설치가 가능합니다.

### 1.3 클러스터 생성 및 설정

#### 클러스터 스펙

다음 명령으로 클러스터를 생성합니다:

```bash
# 패키지 루트(qwen38-27b/)에서 실행합니다
databricks clusters create --json @scripts/01_cluster.json --profile <PROFILE>
```

- `<PROFILE>`: Databricks CLI 프로필 이름 (예: `default`)

명령 실행 후 반환되는 `cluster_id`를 기록해 두십시오. [STEP3](STEP3_vllm_serve.md) 에서 필요합니다.

#### 클러스터 구성 항목

`scripts/01_cluster.json`에는 다음이 설정됩니다:

| 항목 | 값 | 설명 |
|---|---|---|
| `cluster_name` | `qwen38-27b-vllm-test` | 중립적인 이름 |
| `num_workers` | `0` | **Single-node** 클러스터 |
| `spark.master` | `local[*, 4]` | 드라이버만 사용, 스레드 제한 |
| `spark.databricks.cluster.profile` | `singleNode` | 단일 노드 프로파일 |
| `data_security_mode` | `SINGLE_USER` | 사용자별 격리 모드 |
| `autotermination_minutes` | `90` | **생성 전에 위 표를 참고해 `0` 또는 `90` 선택** |

#### `autotermination_minutes` 가 왜 중요합니까

**실측** [실측]: 명령이 없는 상태에서 90분 후 `INACTIVITY` 로 `TERMINATING` 종료되는 것을 확인했습니다 (클러스터 상태:  
`inactivity_duration_min: 90`).

**추론 트래픽이 이 타이머를 갱신하는지는 측정하지 못했습니다** [미측정] — 그 90분 구간에는 추론 트래픽이  
아예 없었으므로, 이 관측은 "명령이 없으면 종료된다" 만 증명합니다. 갱신 여부는 증명하지 않습니다.

→ **갱신되지 않는다고 가정하십시오.** 기본값 90분 유지 시 8시간 부하 시험은 약 90분 만에 클러스터가 죽습니다.

| 계획 | 선택값 |
|---|---|
| §5.1 빠른 검증까지만 (약 10분) | `90` (기본값) |
| **§5.2 8시간 soak · AI Gateway 연결 · 상시 서비스** | **`0`** — 생성 시점에 지정 |

> ⚠️ **`0` 으로 만들었다면 테스트 종료 후 반드시 수동 종료하십시오.**  
> 클러스터가 절대 자동 종료되지 않으므로 과금이 계속됩니다.  
> `databricks clusters delete <CLUSTER_ID> --profile <PROFILE>`  
> (`terminate` 는 없는 명령입니다. 반드시 `delete` 를 사용하십시오.)

#### 클러스터 파일 편집 항목

`scripts/01_cluster.json`을 편집할 때:

- **`single_user_name`**: 고객 이메일 주소로 변경
- **`custom_tags.Owner`**: 고객 이메일 주소로 변경
- **`autotermination_minutes`**: 위 표를 참고해 `0` 또는 `90` 선택 (파일에 이미 `90` 으로 들어 있습니다)

예시:

```json
{
  "autotermination_minutes": 0,
  "single_user_name": "customer@example.com",
  "custom_tags": {
    "Owner": "customer@example.com"
  }
}
```

나머지 필드는 검증된 기본값이므로 변경하지 않는 것을 권장합니다.

#### 클러스터 기동 및 대기

명령 실행 후 **기동은 약 6분**이며 `RUNNING` 이 될 때까지 기다립니다.

> **신규 워크스페이스라면** 첫 기동이 `INVALID_WORKER_ENVIRONMENT: WorkerEnv not found in central`  
> 로 실패할 수 있습니다. 백엔드 초기화가 진행 중이라는 뜻이며, 다음으로 다시 시작하면 통과합니다:  
> `databricks clusters start <CLUSTER_ID> --profile <PROFILE>`

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

### 1.5 네트워크 연결성

드라이버 노드의 외부 egress를 통해 HuggingFace에서 직접 가중치를 다운로드합니다.

```
https://huggingface.co/Qwen/Qwen3.8-27B-FP8
```

**네트워크가 차단된 경우**: Unity Catalog Volume 또는 클라우드 스토리지(Azure Blob Storage 등)에 가중치를  
미리 업로드하고, [STEP1](STEP1_model_weights.md) 에서 로컬 경로로 지정할 수 있습니다.

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

---

## 2. 스크립트 배포 — 3가지 경로

스크립트를 워크스페이스가 읽을 수 있는 위치에 올린 다음, 부트스트랩 셀로 드라이버의  
`/local_disk0/scripts` 로 복사합니다. 환경에 맞는 경로 **하나만** 고르십시오.

**어느 경로를 쓸 수 있는지 먼저 판별하십시오** — 아래 두 명령의 결과로 결정됩니다.

```bash
# ① Unity Catalog metastore 가 붙어 있는가 → 있으면 경로 A
databricks catalogs list --profile <PROFILE>

# ② DBFS root 가 살아 있는가 → 살아 있으면 경로 B
databricks fs ls dbfs:/FileStore/ --profile <PROFILE>
```

| ① 카탈로그 목록 | ② DBFS | 쓸 경로 |
|---|---|---|
| 나온다 | 무관 | **A · UC Volume** (권장 · 단 [미검증]) |
| 비었다 / 오류 | 나온다 | **B · DBFS** |
| 비었다 / 오류 | 오류 | **C · 워크스페이스 파일** |

경로 A 를 고르려면 대상 볼륨에 `READ VOLUME`·`WRITE VOLUME` 권한도 필요합니다. 권한이 없고
볼륨을 만들 수도 없으면 ②의 결과에 따라 B 또는 C 로 가십시오.

| 경로 | 조건 | 드라이버 접근 경로 | 검증 여부 |
|---|---|---|---|
| **A · UC Volume** | UC 사용 환경. 카탈로그·스키마에 `USE CATALOG`·`USE SCHEMA`, 볼륨에 `READ VOLUME`·`WRITE VOLUME` | `/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts` | [미검증] |
| **B · DBFS (레거시)** | DBFS root 가 살아 있는 레거시 워크스페이스에서만. **2025-12-19 이후 생성된 account 는 기본 차단** | `/dbfs/FileStore/qwen38-scripts` | 검증됨 |
| **C · 워크스페이스 파일** | metastore 도 없고 DBFS 도 막혔을 때 | `/Workspace/Users/<사용자>/qwen38-scripts` | 검증됨 |

> ⚠️ **경로 A 는 실행 검증되지 않았습니다** [미검증]. 검증 환경에 Unity Catalog metastore 가 없어  
> `/Volumes` 경로를 실제로 돌려보지 못했습니다. `/Workspace` 와 같은 FUSE 마운트 방식이므로  
> 같은 제약이 적용될 가능성이 높습니다 — 즉 분리된 프로세스에서는 보이지 않을 수 있으므로  
> **복사는 노트북 Python 셀에서 실행하고, 복사 직후 파일 7개가 실제로 왔는지 개수로 확인하십시오** (아래 §2.4).  
> **실행 검증된 스테이징 경로는 B(DBFS)와 C(워크스페이스 파일)입니다.**

### 경로 A · UC Volume (권장 · 미검증)

#### 사전 조건

필요한 권한: 대상 카탈로그·스키마에 `USE CATALOG` · `USE SCHEMA`, 볼륨에 `READ VOLUME`(드라이버에서  
읽기) 과 `WRITE VOLUME`(업로드). 볼륨이 없으면 먼저 만들어야 합니다.

```sql
-- SQL 에디터 또는 노트북에서 (볼륨이 없을 때만)
CREATE VOLUME IF NOT EXISTS <catalog>.<schema>.<volume>;
```

#### 업로드 (로컬 머신에서 · 한 번 실행)

```bash
databricks fs mkdir dbfs:/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts \
  --profile <PROFILE>

databricks fs cp -r --overwrite ./scripts \
  dbfs:/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts/ \
  --profile <PROFILE>
```

### 경로 B · DBFS (레거시)

> ⚠️ **DBFS root 는 폐기 예정이며 최근 워크스페이스에서는 기본적으로 막혀 있습니다.**  
> Databricks 문서 기준으로 **2025-12-19 이후에 생성된 account 는 기본적으로 이 기능에  
> 접근할 수 없습니다.** 먼저 사용 가능한지 확인하십시오.

```bash
# 사용 가능성 확인
databricks fs ls dbfs:/FileStore/ --profile <PROFILE>
```

**실패하면 경로 A(§2·A) 또는 경로 C(§2·C)를 쓰십시오.**

#### 업로드 (로컬 머신에서)

```bash
databricks fs cp -r --overwrite ./scripts \
  dbfs:/FileStore/qwen38-scripts/ \
  --profile <PROFILE>
```

### 경로 C · 워크스페이스 파일

metastore 가 없고 DBFS 도 차단된 환경을 위한 경로입니다. 스크립트 전체가 100KB 미만이므로  
워크스페이스 파일로 충분합니다(파일당 상한 500MB).

> **`--format RAW` 가 중요합니다.** 이 옵션이 없으면 `.py` 가 **노트북으로 임포트되고 확장자가  
> 제거되어** 부트스트랩이 파일을 찾지 못합니다. `import-dir` 도 같은 이유로 쓰지 마십시오.

#### 업로드 (로컬 머신에서)

```bash
databricks workspace mkdirs /Users/<사용자>/qwen38-scripts --profile <PROFILE>

for f in ./scripts/*; do
  databricks workspace import --format RAW --overwrite \
    --file "$f" "/Users/<사용자>/qwen38-scripts/$(basename $f)" --profile <PROFILE>
done
```

#### FILE 로 올라갔는지 확인 (NOTEBOOK 이면 위 `--format RAW` 를 빠뜨린 것입니다)

```bash
databricks workspace list /Users/<사용자>/qwen38-scripts -o json --profile <PROFILE> \
  | grep object_type | sort -u
# 기대: "object_type": "FILE" 만 나와야 합니다
```

### 2.4 부트스트랩 셀 (노트북 첫 셀 · 드라이버에서 실행)

어느 방법을 선택하든, 클러스터 드라이버의 노트북 **첫 셀**에서 다음을 실행하십시오.  
이 셀은 `/local_disk0`(ephemeral 로컬 스토리지)로 스크립트를 복사하고 실행 권한을 부여합니다.

**공통 부트스트랩 코드** — `SRC` 경로만 바꾸면 됩니다:

```python
%python
# 경로 A: SRC = "/Volumes/<catalog>/<schema>/<volume>/qwen38-scripts"
# 경로 B: SRC = "/dbfs/FileStore/qwen38-scripts"
# 경로 C: SRC = "/Workspace/Users/<사용자>/qwen38-scripts"
SRC = "/dbfs/FileStore/qwen38-scripts"  # 선택한 경로로 변경하십시오
DST = "/local_disk0/scripts"
import os, shutil, glob, stat
os.makedirs(DST, exist_ok=True)
for f in glob.glob(f"{SRC}/*"):
    shutil.copy(f, DST)
for f in glob.glob(f"{DST}/*.sh"):
    os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
print(len(os.listdir(DST)), sorted(os.listdir(DST)))
```

### 스테이징 후 확인 — 파일은 정확히 7개입니다

위 셀의 마지막 줄이 **`7`** 과 다음을 출력해야 합니다:

```
['00_check_prereq.py', '01_cluster.json', '02_build_venv.py', '03_stage_weights.py', '04_serve.sh', '05_validate.py', '06_soak.py']
```

**0개이거나 확장자가 없는 이름이 보이면** 업로드 형식이 잘못된 것입니다(경로 C 의 `--format RAW` 확인).  
AI Gateway 연결에는 **추가 스크립트가 필요하지 않습니다.**

**`/local_disk0` 은 클러스터 재시작 시 초기화되므로, 재시작할 때마다 이 부트스트랩 셀을 다시  
실행해야 합니다.**

### ⚠️ `/Workspace` 는 노트북 셀에서만 보입니다

분리된 프로세스(`setsid`·`nohup`·별도 세션)에서는 이 마운트가 **예외 없이 빈 디렉터리처럼 동작**합니다  
(`os.path.isdir()` 이 `False`, `glob()` 이 `[]`). 부트스트랩 셀을 분리 실행하면 **아무것도 복사되지  
않은 채 오류 없이 끝납니다.** 반드시 노트북 셀에서 실행하고 파일 수를 확인하십시오.

같은 이유로 경로 A의 `VOLUME_PATH`(`/Volumes/...`)도 노트북 셀에서 실행하십시오  
(`/Volumes` 는 동일한 FUSE 마운트 방식입니다).

| 마운트 | 노트북 셀 | `setsid` 분리 프로세스 |
|---|---|---|
| `/dbfs` | 정상 | 정상 |
| `/Workspace` | 정상 | **`isdir=False` · 0개** |

---

## 3. venv 빌드

```python
%python
import subprocess, sys
_r = subprocess.run([sys.executable, "/local_disk0/scripts/02_build_venv.py"],
                    capture_output=True, text=True)
print(_r.stdout)
if _r.stderr.strip():
    print("--- stderr ---"); print(_r.stderr[-2000:])
print(f"[종료코드] {_r.returncode}")
```

| 항목 | 기대값 |
|---|---|
| 소요 시간 | **62~77초** (관측 62 · 66 · 76.9초 · 실행마다 변동) |
| 설치 위치 | `/local_disk0/vllm028` |
| 디렉터리 크기 | 7.6 GB |
| vLLM | **0.28.0** (정확히 고정) |
| torch | **2.13.0+cu130** |
| transformers | 5.16.1 |

성공 시 마지막 줄: `[02] SUCCESS: vllm 0.28.0 torch 2.13.0+cu130 13.0 tf 5.16.1`

### 왜 격리 venv 가 필요합니까

DBR 19 의 base Python 환경에는 torch **2.12.0+cu130** 과 transformers **4.57.6** 이 들어 있는데,  
vLLM 0.28.0 은 torch **2.13.0** 을 고정하고 transformers 5.x 를 요구합니다. **어떤 vLLM  
릴리스도 DBR 기본 torch 를 핀하지 않으므로** 런타임 패키지를 보존한 `--no-deps` 설치는 이 조합에  
적용할 수 없고 런타임에 즉시 실패합니다. 그래서 `uv venv` 로 자체 torch 를 가진 독립 venv 를 만들고,  
vLLM 을 **분리된 프로세스**로 띄워 loopback HTTP 로 통신합니다.

---

## 4. 가장 흔한 실패 원인 · 환경변수 오염

**DBR 19 클러스터에는 `OPENSSL_FORCE_FIPS_MODE` 환경변수가 설정되어 있습니다.** 이 값을 그대로 두고  
python 을 실행하면 `crypto/fips/fips.c:154 FATAL FIPS SELFTEST FAILURE` 로 **즉시 실패하고  
프로세스가 rc=134 로 중단됩니다.** 값을 바꾸는 것이 아니라 **환경에서 제거(unset)** 해야 하며,  
**venv 빌드 시점과 serve 실행 시점 모두에서** 먼저 실행해야 합니다.

```bash
unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset $v; done
```

**`02_build_venv.py` · `03_stage_weights.py` · `04_serve.sh` 에는 이 정리가 이미 포함되어  
있습니다.** 스크립트를 쓰는 한 별도로 할 일은 없고, 명령을 직접 조립할 때만 넣으십시오.

---

## 5. 사전 점검 · `00_check_prereq.py`

> **실행 시점 주의**: 이 점검은 `/local_disk0/scripts/00_check_prereq.py` 를 실행하므로  
> 위 §2 의 부트스트랩 셀을 먼저 끝내야 합니다.

§3 의 셀에서 스크립트 이름만 `00_check_prereq.py` 로 바꿔 실행하십시오. 점검 항목은  
NVIDIA 드라이버 ≥ **580** · GPU 메모리 ≥ **80 GB** · compute capability **8.0** · `/local_disk0`  
여유 ≥ **45 GB** · `uv` 존재 · PyPI 및 HuggingFace 도달성 · 환경변수 상태입니다.

**성공 판정**: 마지막 줄이 `[PASS] 모든 필수 조건 확인 완료` 이고 `[종료코드] 0` 입니다.  
`sensitive_env` 가 `[WARN]` 로 나오는 것은 **정상입니다** — `OPENSSL_FORCE_FIPS_MODE` 는 각  
스크립트가 실행 시점에 제거합니다.

> ⚠️ **폐쇄망에서 가중치를 UC Volume 으로 가져오는 경우** (STEP1 경로 B), HuggingFace  
> 도달성 항목이 `[FAIL]` 이 되고 **종료코드가 `1`** 이 됩니다. 이것은 환경 부적격이 아닙니다.  
> **`hf_reachable` 하나만 FAIL 이고 나머지가 PASS 이면 그대로 진행하십시오.** 단  
> **`pypi_reachable` 은 venv 빌드에 필요하므로 반드시 PASS 여야 합니다.**

---

## 6. 검증 체크리스트

| # | 확인 | 명령 · 기대 출력 |
|---|---|---|
| 1 | 클러스터 상태 | `databricks clusters get <CLUSTER_ID> --profile <PROFILE>` → `RUNNING` |
| 2 | `/local_disk0` 여유 | `df -h /local_disk0` → **45 GB 이상** |
| 3 | 스크립트 개수 | `ls /local_disk0/scripts \| wc -l` → **7** |
| 4 | 사전 점검 | `[PASS] 모든 필수 조건 확인 완료` · `[종료코드] 0` |
| 5 | vLLM 버전 | `/local_disk0/vllm028/bin/python -c "import vllm; print(vllm.__version__)"` → **`0.28.0`** |
| 6 | venv 크기 | `du -sh /local_disk0/vllm028` → 7.6 GB |

---

**다음 단계**: 가중치가 아직 없으면 [STEP1_model_weights.md](STEP1_model_weights.md) 의 실행 셀을  
돌리고, 그 다음 [STEP3_vllm_serve.md](STEP3_vllm_serve.md) 로 진행하십시오.
