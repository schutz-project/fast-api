#!/bin/sh
# Run inside Nginx's PID namespace; reopen after rename, never copytruncate.
log=${NGINX_LOG_FILE:-/var/log/nginx/portal-access.jsonl}
limit=${NGINX_LOG_MAX_BYTES:-10485760}
rotated_at=$(date +%s)
if [ -f "$log" ]; then rotated_at=$(stat -c %Y "$log"); fi
rotate() {
  if [ -f "$log" ]; then
    size=$(wc -c < "$log")
    current=$(date +%s)
    if [ "$size" -ge "$limit" ] || [ "$((current-rotated_at))" -ge 86400 ]; then
      rm -f "$log.5"
      for n in 4 3 2 1; do
        if [ -f "$log.$n" ]; then mv "$log.$n" "$log.$((n+1))"; fi
      done
      mv "$log" "$log.1"
      rotated_at=$current
      kill -USR1 "${NGINX_PID:-1}" 2>/dev/null || true
    fi
  fi
  for n in 1 2 3 4 5; do
    if [ -f "$log.$n" ]; then find "$log.$n" -mtime +6 -exec rm -f {} \;; fi
  done
}
while :; do
  rotate
  if [ "${ROTATE_ONCE:-false}" = true ]; then break; fi
  sleep 120
done
