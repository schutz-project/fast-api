#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/schutz/employee-portal-p0
CONTAINER=employee-portal-p0-api-1
TAG="${1:-sha-8f97a8d337fb672c8c6e92bfd22ca49c2ff2dde0-39910776450-1}"
[[ "$TAG" =~ ^sha-[a-f0-9]{40}-[0-9]+-[0-9]+$ ]] || { echo INVALID_IMAGE_TAG; exit 2; }
REGION=ap-northeast-2
cd "$APP_DIR"
exec 9>.fastapi-release.lock
flock -w 120 9 || { echo FASTAPI_RELEASE_LOCK_TIMEOUT; exit 1; }
test -f compose.yaml && test -f compose.ecr.yaml

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
NEW_IMAGE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/schutz/fast-api:${TAG}"
OLD_IMAGE=$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')
[ "$NEW_IMAGE" != "$OLD_IMAGE" ] || { echo 'ALREADY_DEPLOYED'; exit 0; }
auth_dir=$(mktemp -d)
trap 'rm -rf "$auth_dir"' EXIT
aws ecr get-login-password --region "$REGION" |
  docker --config "$auth_dir" login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
timeout 180 docker --config "$auth_dir" pull "$NEW_IMAGE" >/dev/null
rm -rf "$auth_dir"
trap - EXIT
docker image inspect "$NEW_IMAGE" >/dev/null
EXPECTED_IMAGE_ID=$(docker image inspect "$NEW_IMAGE" --format '{{.Id}}')
OLD_IMAGE_ID=$(docker inspect "$CONTAINER" --format '{{.Image}}')
BASELINE_DB_ID=$(docker inspect employee-portal-p0-db-1 --format '{{.Id}}')
[[ $(docker image inspect "$OLD_IMAGE" --format '{{.Id}}') == "$OLD_IMAGE_ID" ]]
export OLD_IMAGE
docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml config --format json | python3 -c 'import json,os,sys; assert json.load(sys.stdin)["services"]["api"]["image"] == os.environ["OLD_IMAGE"]'
docker image inspect "$OLD_IMAGE" >/dev/null

# Preserve only API changes. Do not start migration, seed or DB services.
[ "$(grep -Ec '^[[:space:]]*image:[[:space:]]*' compose.ecr.yaml)" -eq 1 ] || {
  echo 'ERROR: expected exactly one image entry in compose.ecr.yaml' >&2
  exit 1
}

check_health() {
  [[ $(docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null) == true &&
     $(docker inspect "$CONTAINER" --format '{{.RestartCount}}' 2>/dev/null) == 0 &&
     $(docker inspect employee-portal-p0-db-1 --format '{{.Id}}' 2>/dev/null) == "$BASELINE_DB_ID" &&
     $(docker inspect employee-portal-p0-db-1 --format '{{.State.Health.Status}}' 2>/dev/null) == healthy ]] || return 1
  docker exec "$CONTAINER" python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=4).status == 200' >/dev/null 2>&1 || return 1
  [[ $(curl -s -o /dev/null --max-time 5 -w '%{http_code}' http://127.0.0.1:8080/health) == 200 &&
     $(curl -s -o /dev/null --max-time 5 -w '%{http_code}' http://127.0.0.1:8081/internal/check/employee) == 200 ]]
}
wait_health() {
  local passes=0
  for _ in $(seq 1 30); do
    if check_health; then
      passes=$((passes + 1))
      if (( passes >= 3 )); then return 0; fi
    else
      passes=0
    fi
    sleep 3
  done
  return 1
}

# Refuse deployment when current service is already unhealthy.
check_health || { echo 'ERROR: baseline /health failed; deployment not started' >&2; exit 1; }

umask 077
BACKUP_ROOT="$APP_DIR/.release-backups"
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$BACKUP_ROOT/release-$STAMP-$$"
mkdir -m 700 "$BACKUP"
cp -p compose.ecr.yaml "$BACKUP/compose.ecr.yaml"
printf '%s\n' "$OLD_IMAGE" > "$BACKUP/previous-image.txt"
printf '%s\n' "$NEW_IMAGE" > "$BACKUP/new-image.txt"

deploy_compose() {
  docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml up -d --no-deps --no-build --pull never api || return 1
  docker exec employee-portal-p0-nginx-1 nginx -t || return 1
  docker exec employee-portal-p0-nginx-1 nginx -s reload
}

rollback() {
  local rc="${1:-$?}"
  trap - ERR INT TERM
  echo 'RELEASE_FAILED_ROLLBACK_START'
  cp -p "$BACKUP/compose.ecr.yaml" compose.ecr.yaml
  if docker image inspect "$OLD_IMAGE" >/dev/null && deploy_compose; then
    if wait_health && [[ $(docker inspect "$CONTAINER" --format '{{.Image}}') == "$OLD_IMAGE_ID" ]]; then
      echo 'ROLLBACK_OK'
    else
      echo 'ROLLBACK_HEALTH_FAILED_MANUAL_CHECK_REQUIRED' >&2
    fi
  else
    echo 'ROLLBACK_FAILED_MANUAL_CHECK_REQUIRED' >&2
  fi
  exit "$rc"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

TMP=$(mktemp ./compose.ecr.release.XXXXXX)
trap 'rm -f "$TMP"' EXIT
# Verified above that exactly one image line exists in this override.
sed -E "s|^([[:space:]]*image:[[:space:]]*).*$|\\1${NEW_IMAGE}|" compose.ecr.yaml > "$TMP"
cat "$TMP" > compose.ecr.yaml

docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml config -q

docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml config --images | grep -Fx -- "$NEW_IMAGE" >/dev/null

deploy_compose

wait_health
ACTUAL=$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')
[ "$ACTUAL" = "$NEW_IMAGE" ]
[[ $(docker inspect "$CONTAINER" --format '{{.Image}}') == "$EXPECTED_IMAGE_ID" ]]
trap - ERR INT TERM
echo 'FASTAPI_RELEASE_SUCCESS'
echo "DEPLOYED_TAG=$TAG"
echo "PREVIOUS_TAG=${OLD_IMAGE##*:}"
