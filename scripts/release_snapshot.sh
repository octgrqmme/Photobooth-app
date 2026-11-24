#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
source_root="$app_root"
label="snapshot"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label)
      label="${2:-snapshot}"
      shift 2
      ;;
    --source)
      source_root="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--label name] [--source dir]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

safe_label="$(printf '%s' "$label" | tr -cd 'A-Za-z0-9._-')"
if [[ -z "$safe_label" ]]; then
  safe_label="snapshot"
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
output_dir="$source_root/backups/release-${safe_label}-${timestamp}"

bash "$app_root/scripts/backup.sh" --source "$source_root" --output "$output_dir"

echo "Release snapshot created at: $output_dir"
echo "Rollback command:"
echo "  bash \"$app_root/scripts/restore.sh\" \"$output_dir\" --target \"$source_root\""
