#!/bin/bash
#
# 04_serve.sh — vLLM serve 기동 및 정상 상태 확인
#
# 사용법:
#   ./04_serve.sh
#   PORT=9000 MAX_MODEL_LEN=262144 ./04_serve.sh
#   KV_CACHE_DTYPE="" ./04_serve.sh  # kv fp8 비활성화
#
# 환경변수 설정 가능:
#   MAX_MODEL_LEN        : 최대 컨텍스트 토큰 (기본: 131072)
#   KV_CACHE_DTYPE       : kv 캐시 타입 (기본: fp8, 빈 값이면 플래그 생략)
#   PORT                 : 바인드 포트 (기본: 8005)
#   GPU_MEM_UTIL         : GPU 메모리 사용률 (기본: 0.90)
#   LANGUAGE_MODEL_ONLY  : 텍스트 전용 모드 (기본: 1)
#   BIND_HOST            : 바인드 주소 (기본: 127.0.0.1)
#                          AI Gateway 연결(가이드 §6) 시에는 반드시 0.0.0.0 을 지정한다.
#                          driver-proxy 는 드라이버의 사설 IP 로 접속하므로 127.0.0.1 이면 502 가 된다.
#   SERVED_MODEL_NAME    : API 에서 참조할 모델 별칭 (기본: qwen38-27b)
#                          변경하면 가이드 §6 엔드포인트의 모델명도 같게 맞춰야 한다.
#   TOOL_CALL_PARSER     : tool calling 파서 (기본: 미지정 = 비활성)
#                          외부 agent 의 function calling 이 필요하면 qwen3_xml 을 지정한다.
#

set -e

# ============================================================================
# 설정
# ============================================================================

MAX_MODEL_LEN=${MAX_MODEL_LEN:-131072}
KV_CACHE_DTYPE=${KV_CACHE_DTYPE:-fp8}
PORT=${PORT:-8005}
GPU_MEM_UTIL=${GPU_MEM_UTIL:-0.90}
LANGUAGE_MODEL_ONLY=${LANGUAGE_MODEL_ONLY:-1}
BIND_HOST=${BIND_HOST:-127.0.0.1}
TOOL_CALL_PARSER=${TOOL_CALL_PARSER:-}
SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-qwen38-27b}

VENV_PATH="/local_disk0/vllm028"
MODEL_PATH="/local_disk0/models/Qwen3.8-27B-FP8"
SERVE_LOG=${SERVE_LOG:-/local_disk0/serve.log}
LOG_FILE="$SERVE_LOG"

# ============================================================================
# 함수
# ============================================================================

log_info() {
    echo "[INFO] $1"
}

log_error() {
    echo "[ERROR] $1" >&2
}

log_step() {
    echo ""
    echo "=================================="
    echo "$1"
    echo "=================================="
}

# ============================================================================
# 검증
# ============================================================================

log_step "1. 기본 검증"

# venv 존재 확인
if [ ! -f "$VENV_PATH/bin/vllm" ]; then
    log_error "venv를 찾을 수 없습니다: $VENV_PATH"
    exit 1
fi
log_info "✓ venv 확인: $VENV_PATH"

# 모델 경로 확인
if [ ! -f "$MODEL_PATH/config.json" ]; then
    log_error "모델 경로를 찾을 수 없습니다: $MODEL_PATH"
    exit 1
fi
log_info "✓ 모델 확인: $MODEL_PATH"

# GPU_MEM_UTIL 검증 (0.90 초과 금지) — bc 없이 awk로 비교
# 원본 값으로 판정한다. sed로 먼저 걸러내면 "abc"가 빈 값으로 보여 오해를 준다.
check_result=$(awk -v v="$GPU_MEM_UTIL" 'BEGIN{
    if (v == "") print "EMPTY"
    else if (v !~ /^[0-9]*\.?[0-9]+$/) print "INVALID"
    else if (v+0 > 0.90) print "TOO_HIGH"
    else print "OK"
}')

if [ "$check_result" != "OK" ]; then
    case "$check_result" in
        EMPTY)
            log_error "GPU_MEM_UTIL이 설정되지 않았습니다."
            ;;
        INVALID)
            log_error "GPU_MEM_UTIL=$GPU_MEM_UTIL 은 유효한 숫자가 아닙니다."
            ;;
        TOO_HIGH)
            log_error "GPU_MEM_UTIL=$GPU_MEM_UTIL 은 허용 최대값 0.90을 초과합니다. OOM 위험이 있어 중단합니다."
            ;;
    esac
    exit 1
fi
log_info "✓ GPU_MEM_UTIL=$GPU_MEM_UTIL (안전 범위)"

# ============================================================================
# 환경 정리
# ============================================================================

log_step "2. 환경 변수 정리"

unset PYTHONPATH LD_LIBRARY_PATH
for v in $(env | grep -oE '^(OPENSSL|FIPS)[A-Z_]*'); do unset "$v"; done

# 캐시·임시 디렉터리를 /local_disk0으로 보낸다. 기본 위치(루트 볼륨)에 두면
# torch.compile 캐시가 재기동 시 재사용되지 않거나 루트 볼륨을 채울 수 있다.
export TMPDIR=/local_disk0/tmp
export HF_HOME=/local_disk0/hf
export VLLM_CACHE_ROOT=/local_disk0/vllm_cache
export TORCHINDUCTOR_CACHE_DIR=/local_disk0/inductor
mkdir -p "$TMPDIR" "$HF_HOME" "$VLLM_CACHE_ROOT" "$TORCHINDUCTOR_CACHE_DIR"
log_info "✓ PYTHONPATH · LD_LIBRARY_PATH · OPENSSL/FIPS 환경변수 정리 완료"

# ============================================================================
# 기존 프로세스 정리 + 로그 백업
# ============================================================================

log_step "3. 기존 프로세스 정리"

# `vllm serve`만 죽이면 자식 프로세스(VLLM::EngineCore, VllmWorker)가 살아남아
# VRAM 전체를 계속 점유한다. 그 상태로 재기동하면 엔진 초기화가 OOM으로 실패한다.
# 반드시 네 패턴을 모두 정리한다.
for pat in 'vllm.entrypoints.openai.api_server' 'vllm serve' 'EngineCore' 'VllmWorker'; do
    if pgrep -f "$pat" > /dev/null 2>&1; then
        log_info "기존 프로세스 종료: $pat"
        pkill -9 -f "$pat" || true
    fi
done
sleep 5
log_info "✓ 기존 프로세스 정리 완료"

# VRAM이 실제로 반환됐는지 확인한다. 반환 전에 기동하면 OOM으로 실패한다.
VRAM_WAIT=0
while [ $VRAM_WAIT -lt 60 ]; do
    VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1) || VRAM_USED=0
    if [ "${VRAM_USED:-0}" -lt 5000 ]; then
        log_info "✓ VRAM 반환 확인 (사용 중 ${VRAM_USED} MiB)"
        break
    fi
    log_info "VRAM 반환 대기... (사용 중 ${VRAM_USED} MiB, ${VRAM_WAIT}초)"
    sleep 5
    VRAM_WAIT=$((VRAM_WAIT + 5))
done
if [ "${VRAM_USED:-0}" -ge 5000 ]; then
    log_error "VRAM이 반환되지 않았습니다 (${VRAM_USED} MiB 점유 중). 좀비 프로세스를 확인하십시오:"
    log_error "  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv"
    log_error "  이 상태로 기동하면 엔진 초기화가 OOM으로 실패합니다."
    exit 1
fi

# 포트 정리
if lsof -i ":$PORT" > /dev/null 2>&1; then
    log_info "포트 $PORT 가 사용 중입니다. 해제합니다..."
    fuser -k "$PORT/tcp" 2>/dev/null || true
    sleep 2
fi
log_info "✓ 포트 $PORT 해제 완료"

# 로그 백업
if [ -f "$LOG_FILE" ]; then
    log_info "기존 로그를 $LOG_FILE.prev 로 백업합니다"
    mv "$LOG_FILE" "$LOG_FILE.prev" || true
fi

# ============================================================================
# serve 명령 구성
# ============================================================================

log_step "4. serve 명령 구성"

SERVE_CMD="$VENV_PATH/bin/vllm serve $MODEL_PATH"
SERVE_CMD="$SERVE_CMD --served-model-name $SERVED_MODEL_NAME"
SERVE_CMD="$SERVE_CMD --host $BIND_HOST --port $PORT"
SERVE_CMD="$SERVE_CMD --max-model-len $MAX_MODEL_LEN"
SERVE_CMD="$SERVE_CMD --gpu-memory-utilization $GPU_MEM_UTIL"
SERVE_CMD="$SERVE_CMD --max-num-seqs 32"
SERVE_CMD="$SERVE_CMD --max-num-batched-tokens 7840"
SERVE_CMD="$SERVE_CMD --reasoning-parser qwen3"

# tool calling. 플래그를 주지 않으면 tools 를 담은 요청은 무시되지 않고 HTTP 400 으로 거절된다.
if [ -n "$TOOL_CALL_PARSER" ]; then
    SERVE_CMD="$SERVE_CMD --enable-auto-tool-choice --tool-call-parser $TOOL_CALL_PARSER"
fi

if [ "$LANGUAGE_MODEL_ONLY" = "1" ]; then
    SERVE_CMD="$SERVE_CMD --language-model-only"
fi

if [ -n "$KV_CACHE_DTYPE" ]; then
    SERVE_CMD="$SERVE_CMD --kv-cache-dtype $KV_CACHE_DTYPE"
fi

log_info "실행 명령: $SERVE_CMD"

# ============================================================================
# serve 기동
# ============================================================================

log_step "5. serve 기동 (분리 모드)"

setsid nohup $SERVE_CMD > "$LOG_FILE" 2>&1 &
SERVE_PID=$!

log_info "기동 PID: $SERVE_PID"
log_info "로그 파일: $LOG_FILE"
sleep 3

if ! kill -0 $SERVE_PID 2>/dev/null; then
    log_error "프로세스가 즉시 종료되었습니다. 로그를 확인하십시오:"
    tail -40 "$LOG_FILE" >&2
    exit 1
fi
log_info "✓ 프로세스 정상 동작 중"

# ============================================================================
# /health 폴링
# ============================================================================

log_step "6. /health 엔드포인트 대기"

HEALTH_URL="http://127.0.0.1:$PORT/health"
TIMEOUT=1200
POLL_INTERVAL=5
ELAPSED=0

log_info "$HEALTH_URL 폴링 중 (최대 ${TIMEOUT}초)"

while [ $ELAPSED -lt $TIMEOUT ]; do
    # vLLM의 /health는 **본문이 비어 있는 200**을 반환한다. 본문에서 문자열을 찾으면
    # 서버가 정상이어도 절대 매칭되지 않아 타임아웃까지 대기하다 오탐으로 실패한다.
    # 따라서 HTTP 상태코드로 판정한다.
    # `set -e` 하에서 curl 실패(기동 전 connection refused = exit 7)가 스크립트를
    # 죽이지 않도록 반드시 폴백을 둔다. if 조건 밖의 대입문은 set -e의 유예 대상이 아니다.
    HEALTH_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null) || HEALTH_CODE="000"
    if [ "$HEALTH_CODE" = "200" ]; then
        log_info "✓ /health 정상 응답 (${ELAPSED}초)"
        break
    fi

    if [ $((ELAPSED % 30)) -eq 0 ]; then
        log_info "대기 중... (${ELAPSED}초/${TIMEOUT}초)"
    fi

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    # 프로세스가 죽었는지 확인
    if ! kill -0 $SERVE_PID 2>/dev/null; then
        log_error "기동 중 프로세스가 종료되었습니다. 로그 마지막 부분:"
        tail -40 "$LOG_FILE" >&2
        exit 1
    fi
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    log_error "/health 대기 시간을 초과했습니다. 아직 기동 중일 수 있습니다."
    log_error "로그 마지막 40줄:"
    tail -40 "$LOG_FILE" >&2
    exit 1
fi

# ============================================================================
# Oracle 검증
# ============================================================================

log_step "7. 기동 로그 Oracle 검증"

echo ""
echo "=== 커널 · attention 백엔드 ==="
grep -i "MarlinFP8ScaledMMLinearKernel" "$LOG_FILE" | tail -1 || echo "(kernel line not found)"
grep -i "Using.*attention backend" "$LOG_FILE" | tail -1 || echo "(attention backend not found)"

echo ""
echo "=== block size · KV 캐시 ==="
grep -i "Setting attention block size" "$LOG_FILE" | tail -1 || echo "(block size not found)"
grep -i "Padding mamba page" "$LOG_FILE" | tail -1 || echo "(mamba padding not found)"
grep -i "GPU KV cache size" "$LOG_FILE" | tail -1 || echo "(KV cache size not found)"
grep -i "Available KV cache memory" "$LOG_FILE" | tail -1 || echo "(available KV not found)"

echo ""
echo "=== 동시 처리 용량 ==="
grep -i "Maximum concurrency for" "$LOG_FILE" | tail -1 || echo "(max concurrency not found)"

# ============================================================================
# 정상 완료
# ============================================================================

log_step "✅ serve 기동 완료"

echo ""
echo "설정 요약:"
echo "  모델            : $MODEL_PATH
  바인드 주소     : $BIND_HOST"
echo "  최대 컨텍스트   : $MAX_MODEL_LEN"
echo "  KV 캐시 dtype   : ${KV_CACHE_DTYPE:-미지정}"
echo "  GPU 메모리 비율 : $GPU_MEM_UTIL"
echo "  텍스트 전용     : $LANGUAGE_MODEL_ONLY"
echo "  포트            : $PORT"
echo "  로그            : $LOG_FILE"
echo ""
echo "동작 확인:"
echo "  curl http://127.0.0.1:$PORT/health"
echo ""
echo "Python 클라이언트 예시:"
echo "  from openai import OpenAI"
echo "  client = OpenAI(base_url='http://127.0.0.1:$PORT/v1', api_key='not-needed')"
echo ""

exit 0
