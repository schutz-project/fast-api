#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/schutz/employee-portal-p0
CONTAINER=employee-portal-p0-api-1
TAG='sha-8f97a8d337fb672c8c6e92bfd22ca49c2ff2dde0-39910776450-1'
REGION=ap-northeast-2
cd "$APP_DIR"
test -f compose.yaml && test -f compose.ecr.yaml

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
NEW_IMAGE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/schutz/fast-api:${TAG}"
OLD_IMAGE=$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')
[ "$NEW_IMAGE" != "$OLD_IMAGE" ] || { echo 'ALREADY_DEPLOYED'; exit 0; }
docker image inspect "$NEW_IMAGE" >/dev/null
docker image inspect "$OLD_IMAGE" >/dev/null

# Preserve only API changes. Do not start migration, seed or DB services.
[ "$(grep -Ec '^[[:space:]]*image:[[:space:]]*' compose.ecr.yaml)" -eq 1 ] || {
  echo 'ERROR: expected exactly one image entry in compose.ecr.yaml' >&2
  exit 1
}

check_health() {
  docker exec "$CONTAINER" python -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=4); assert r.status == 200' >/dev/null 2>&1
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
  docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml up -d --no-deps --no-build api
}

rollback() {
  local rc=$?
  trap - ERR
  echo 'RELEASE_FAILED_ROLLBACK_START'
  cp -p "$BACKUP/compose.ecr.yaml" compose.ecr.yaml
  if docker image inspect "$OLD_IMAGE" >/dev/null && deploy_compose; then
    if check_health; then
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

TMP=$(mktemp ./compose.ecr.release.XXXXXX)
trap 'rm -f "$TMP"' EXIT
# Verified above that exactly one image line exists in this override.
sed -E "s|^([[:space:]]*image:[[:space:]]*).*$|\\1${NEW_IMAGE}|" compose.ecr.yaml > "$TMP"
cat "$TMP" > compose.ecr.yaml

docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml config -q

docker compose -p employee-portal-p0 -f compose.yaml -f compose.ecr.yaml config --images | grep -Fx -- "$NEW_IMAGE" >/dev/null

deploy_compose

OK=0
for _ in $(seq 1 18); do
  if check_health; then OK=1; break; fi
  sleep 3
done
[ "$OK" -eq 1 ]
ACTUAL=$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')
[ "$ACTUAL" = "$NEW_IMAGE" ]
echo 'FASTAPI_RELEASE_SUCCESS'
echo "DEPLOYED_TAG=$TAG"
echo "PREVIOUS_TAG=${OLD_IMAGE##*:}"
