#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$app_root/venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

base_url="http://127.0.0.1:8000"
once=0
interval=15
max_failures=3
log_file="$app_root/userdata/watchdog.log"
restart_cmd=""
health_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      base_url="${2:-}"
      shift 2
      ;;
    --once)
      once=1
      shift
      ;;
    --interval)
      interval="${2:-15}"
      shift 2
      ;;
    --max-failures)
      max_failures="${2:-3}"
      shift 2
      ;;
    --log)
      log_file="${2:-}"
      shift 2
      ;;
    --restart-cmd)
      restart_cmd="${2:-}"
      shift 2
      ;;
    --health-file)
      health_file="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--url http://127.0.0.1:8000] [--once] [--interval 15] [--max-failures 3] [--log path] [--restart-cmd cmd] [--health-file json]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "$log_file")"

log_msg() {
  local line
  line="[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $1"
  printf '%s\n' "$line" | tee -a "$log_file"
}

check_health() {
  "$python_bin" - "$base_url" "$health_file" <<'PYEOF'
import json
import pathlib
import sys
import urllib.request

base = sys.argv[1].rstrip("/")
health_file = sys.argv[2].strip()
health_url = f"{base}/api/share/health"
capture_url = f"{base}/api/share/capture-state"

if health_file:
    try:
        health = json.loads(pathlib.Path(health_file).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"UNREACHABLE {exc}")
        sys.exit(2)
else:
    try:
        with urllib.request.urlopen(health_url, timeout=4) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"UNREACHABLE {exc}")
        sys.exit(2)

unknown_jobs = int(health.get("unknown_print_jobs") or 0)
active_queue = health.get("active_queue_code")
capture_busy = bool(health.get("capture_busy"))
if not health_file:
    try:
        with urllib.request.urlopen(capture_url, timeout=3) as resp:
            cap = json.loads(resp.read().decode("utf-8"))
            capture_busy = bool(cap.get("busy") or cap.get("shutter_locked"))
    except Exception:
        pass

status = str(health.get("status") or "unknown")
if status == "ok" and unknown_jobs == 0:
    print(f"OK status={status} unknown_print_jobs={unknown_jobs} active_queue={active_queue}")
    sys.exit(0)

if unknown_jobs > 0 or active_queue or capture_busy:
    print(f"UNSAFE_RESTART status={status} unknown_print_jobs={unknown_jobs} active_queue={active_queue} capture_busy={capture_busy}")
    sys.exit(3)

print(f"DEGRADED status={status}")
sys.exit(1)
PYEOF
}

failures=0
while true; do
  set +e
  probe_out="$(check_health)"
  rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    failures=0
    log_msg "$probe_out"
  elif [[ "$rc" -eq 3 ]]; then
    failures=0
    log_msg "SKIP_RESTART ($probe_out)"
  else
    failures=$((failures + 1))
    log_msg "FAIL ($failures/$max_failures): $probe_out"
    if [[ "$failures" -ge "$max_failures" ]]; then
      if [[ -n "$restart_cmd" ]]; then
        log_msg "TRIGGER_RESTART: $restart_cmd"
        sh -c "$restart_cmd" >> "$log_file" 2>&1 || log_msg "RESTART_ERROR"
        failures=0
      else
        log_msg "ALERT: threshold reached without --restart-cmd"
      fi
    fi
  fi

  if [[ "$once" -eq 1 ]]; then
    exit "$rc"
  fi
  sleep "$interval"
done
