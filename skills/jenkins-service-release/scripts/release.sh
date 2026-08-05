#!/usr/bin/env bash
# Jenkins 并行发版脚本（兼容 macOS bash 3.2）
# 支持多 serviceName、多环境、按服务不同 collection 同时并行触发
set -euo pipefail

JENKINS_USER=$(cat /Users/jackson/secret/jenkins/username)
JENKINS_TOKEN=$(cat /Users/jackson/secret/jenkins/api_token)
JENKINS_URL="https://jenkins.designorder.cn"
JOB="build_general_service_image"
POLL_INTERVAL=30
TIMEOUT=1800

SERVICE_TYPE=""
ENVS=()

usage() {
  echo "Usage: $0 --service projection|dataproc|drawing2d|cgm --env preprod|refactor|preprod,refactor"
  echo "       $0 --service projection --env preprod --env refactor"
  exit 1
}

add_env() {
  local raw="$1"
  local part
  IFS=',' read -ra PARTS <<< "$raw"
  for part in "${PARTS[@]}"; do
    case "$part" in
      preprod|refactor) ENVS+=("$part") ;;
      *) echo "Unknown env: $part"; exit 1 ;;
    esac
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE_TYPE="$2"; shift 2 ;;
    --env) add_env "$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$SERVICE_TYPE" && ${#ENVS[@]} -gt 0 ]] || usage

# 每项为 collection:serviceName（前端 drawing2d 含不同 collection）
case "$SERVICE_TYPE" in
  projection)
    TARGETS=(
      "python-ai:ai-python-auto-dimension"
      "python-ai:ai-python-auto-dimension-part"
    )
    ;;
  dataproc)
    TARGETS=("python-ai:ai-algorithm-stp-convert")
    ;;
  drawing2d)
    TARGETS=(
      "front-web-apps:do-web-apps-drawing"
      "middleware:middleware-node-queue-server"
    )
    ;;
  cgm)
    TARGETS=("ops:do-cgm")
    ;;
  *) echo "Unknown service: $SERVICE_TYPE"; exit 1 ;;
esac

env_profile() {
  case "$1" in
    preprod)  echo "production" ;;
    refactor) echo "suanfa" ;;
  esac
}

env_branch() {
  case "$1" in
    preprod)  echo "release" ;;
    refactor) echo "dev" ;;
  esac
}

# 展平为任务列表：每个 (环境, collection, serviceName) 一项
TASK_ENVS=()
TASK_PROFILES=()
TASK_BRANCHES=()
TASK_COLLECTIONS=()
TASK_SERVICES=()

for env in "${ENVS[@]}"; do
  profile=$(env_profile "$env")
  branch=$(env_branch "$env")
  for target in "${TARGETS[@]}"; do
    collection="${target%%:*}"
    svc="${target#*:}"
    TASK_ENVS+=("$env")
    TASK_PROFILES+=("$profile")
    TASK_BRANCHES+=("$branch")
    TASK_COLLECTIONS+=("$collection")
    TASK_SERVICES+=("$svc")
  done
done

TASK_COUNT=${#TASK_SERVICES[@]}

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

CRUMB_JSON=$(curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/crumbIssuer/api/json")
CRUMB=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['crumb'])")
CRUMB_FIELD=$(echo "$CRUMB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['crumbRequestField'])")

trigger_one() {
  local idx="$1"
  local svc="${TASK_SERVICES[$idx]}"
  local collection="${TASK_COLLECTIONS[$idx]}"
  local profile="${TASK_PROFILES[$idx]}"
  local branch="${TASK_BRANCHES[$idx]}"
  local env="${TASK_ENVS[$idx]}"
  local hdr
  hdr=$(mktemp)
  local code
  code=$(curl -sS -D "$hdr" -o /dev/null -w '%{http_code}' -X POST \
    -u "$JENKINS_USER:$JENKINS_TOKEN" \
    -H "$CRUMB_FIELD: $CRUMB" \
    "$JENKINS_URL/job/$JOB/buildWithParameters" \
    --data-urlencode "collection=$collection" \
    --data-urlencode "serviceName=$svc" \
    --data-urlencode "profile=$profile" \
    --data-urlencode "branch=$branch" \
    --data-urlencode "CleanWorkSpace=false" \
    --data-urlencode "DeployToK8S=true" \
    --data-urlencode "Rsync=false" \
    --data-urlencode "Reverse=true")
  if [[ "$code" != "201" && "$code" != "302" ]]; then
    echo "ERROR: trigger [$env] $svc failed HTTP $code" >&2
    rm -f "$hdr"
    exit 1
  fi
  local qid
  qid=$(grep -i '^location:' "$hdr" | sed 's|.*/queue/item/||;s|/.*||' | tr -d '\r')
  rm -f "$hdr"
  if [[ -z "$qid" ]]; then
    echo "ERROR: no queue id for [$env] $svc" >&2
    exit 1
  fi
  echo "$qid" > "$WORKDIR/queue_$idx"
  echo "Triggered [$env] $svc (collection=$collection profile=$profile branch=$branch) -> queue $qid"
}

resolve_build_no() {
  local qid="$1"
  local i build_no=""
  for i in $(seq 1 30); do
    build_no=$(curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
      "$JENKINS_URL/queue/item/$qid/api/json" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); e=d.get('executable'); print(e.get('number','') if e else '')" 2>/dev/null || true)
    if [[ -n "$build_no" ]]; then
      echo "$build_no"
      return 0
    fi
    sleep 2
  done
  return 1
}

echo "=== Trigger ${TASK_COUNT} builds in parallel (envs: ${ENVS[*]}) ==="
idx=0
while [[ $idx -lt $TASK_COUNT ]]; do
  trigger_one "$idx" &
  idx=$((idx + 1))
done
wait

echo "=== Resolve build numbers ==="
idx=0
while [[ $idx -lt $TASK_COUNT ]]; do
  env="${TASK_ENVS[$idx]}"
  svc="${TASK_SERVICES[$idx]}"
  qid=$(cat "$WORKDIR/queue_$idx")
  build_no=$(resolve_build_no "$qid") || { echo "ERROR: timeout resolving build for [$env] $svc (queue $qid)"; exit 1; }
  echo "$build_no" > "$WORKDIR/build_$idx"
  echo "[$env] $svc -> build #$build_no"
  idx=$((idx + 1))
done

echo "=== Wait for all builds ==="
ELAPSED=0
PENDING_IDX=()
idx=0
while [[ $idx -lt $TASK_COUNT ]]; do
  PENDING_IDX+=("$idx")
  idx=$((idx + 1))
done

while [[ $ELAPSED -lt $TIMEOUT && ${#PENDING_IDX[@]} -gt 0 ]]; do
  STILL=()
  for i in "${PENDING_IDX[@]}"; do
    env="${TASK_ENVS[$i]}"
    svc="${TASK_SERVICES[$i]}"
    build_no=$(cat "$WORKDIR/build_$i")
    read -r building result display < <(curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
      "$JENKINS_URL/job/$JOB/$build_no/api/json?tree=building,result,displayName" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('building',True), d.get('result') or 'RUNNING', d.get('displayName',''))")
    if [[ "$building" == "False" ]]; then
      echo "$result" > "$WORKDIR/result_$i"
      echo "$display" > "$WORKDIR/display_$i"
      echo "[$ELAPSED s] [$env] $svc #$build_no -> $result ($display)"
    else
      echo "[$ELAPSED s] [$env] $svc #$build_no -> RUNNING ($display)"
      STILL+=("$i")
    fi
  done
  if [[ ${#STILL[@]} -eq 0 ]]; then
    PENDING_IDX=()
  else
    PENDING_IDX=("${STILL[@]}")
  fi
  [[ ${#PENDING_IDX[@]} -eq 0 ]] && break
  sleep "$POLL_INTERVAL"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [[ ${#PENDING_IDX[@]} -gt 0 ]]; then
  echo "ERROR: timeout waiting for builds"
  exit 1
fi

echo ""
echo "=== Summary ==="
FAIL=0
for env in "${ENVS[@]}"; do
  profile=$(env_profile "$env")
  branch=$(env_branch "$env")
  echo ""
  echo "[$env] profile=$profile branch=$branch"
  idx=0
  while [[ $idx -lt $TASK_COUNT ]]; do
    if [[ "${TASK_ENVS[$idx]}" == "$env" ]]; then
      svc="${TASK_SERVICES[$idx]}"
      collection="${TASK_COLLECTIONS[$idx]}"
      build_no=$(cat "$WORKDIR/build_$idx")
      result=$(cat "$WORKDIR/result_$idx")
      display=$(cat "$WORKDIR/display_$idx")
      echo "  - $svc (collection=$collection): build #$build_no | $result | $display"
      echo "    console: $JENKINS_URL/job/$JOB/$build_no/console"
      [[ "$result" != "SUCCESS" ]] && FAIL=1
    fi
    idx=$((idx + 1))
  done
done
exit "$FAIL"
