#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
source_root="$app_root"
output_dir=""
include_media=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      output_dir="${2:-}"
      shift 2
      ;;
    --source)
      source_root="${2:-}"
      shift 2
      ;;
    --include-media)
      include_media=1
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--output <dir>] [--source <dir>] [--include-media]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$output_dir" ]]; then
  output_dir="$app_root/backups/photobooth-backup-$(date +%Y%m%d-%H%M%S)"
fi

python_bin="$app_root/venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python is required to run backup." >&2
  exit 1
fi

"$python_bin" - "$source_root" "$output_dir" "$include_media" <<'PYEOF'
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
output_dir = Path(sys.argv[2]).resolve()
include_media = sys.argv[3] == "1"

if output_dir.exists() and any(output_dir.iterdir()):
    print(f"Backup output directory is not empty: {output_dir}", file=sys.stderr)
    sys.exit(1)

files_root = output_dir / "files"
files_root.mkdir(parents=True, exist_ok=True)
manifest_entries = []

def record_file(rel: Path, dest: Path) -> None:
    hasher = hashlib.sha256()
    with dest.open("rb") as fh:
        while chunk := fh.read(65536):
            hasher.update(chunk)
    manifest_entries.append({
        "path": rel.as_posix(),
        "size": dest.stat().st_size,
        "sha256": hasher.hexdigest(),
    })

def copy_file(rel: Path) -> None:
    src = source_root / rel
    if not src.is_file():
        return
    dest = files_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    record_file(rel, dest)

# 1. Online SQLite backups (Photobooth DB + Platform Store WAL DB)
for db_path_str in ("database/database.sqlite", "userdata/photobooth_platform.sqlite"):
    db_rel = Path(db_path_str)
    db_src = source_root / db_rel
    if db_src.is_file():
        db_dest = files_root / db_rel
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        src_conn = sqlite3.connect(str(db_src), timeout=10)
        dst_conn = sqlite3.connect(str(db_dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        record_file(db_rel, db_dest)

# 2. Config and state JSONs
copy_file(Path("config/config.json"))
userdata_dir = source_root / "userdata"
if userdata_dir.is_dir():
    for item in sorted(userdata_dir.iterdir()):
        if item.is_file() and item.name.startswith("photobooth_") and item.suffix == ".json":
            copy_file(Path("userdata") / item.name)

# 3. Custom templates
templates_dir = source_root / "userdata" / "photobooth_templates"
if templates_dir.is_dir():
    for path in sorted(templates_dir.rglob("*")):
        if path.is_file() and not path.name.endswith(".tmp"):
            copy_file(path.relative_to(source_root))

# 4. Optional media directory
if include_media:
    media_dir = source_root / "media"
    if media_dir.is_dir():
        for path in sorted(media_dir.rglob("*")):
            if path.is_file():
                copy_file(path.relative_to(source_root))

manifest = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "include_media": include_media,
    "file_count": len(manifest_entries),
    "files": manifest_entries,
}
tmp_manifest = output_dir / "manifest.json.tmp"
tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
os.replace(tmp_manifest, output_dir / "manifest.json")

print(f"Backup created at: {output_dir}")
print(f"Files backed up: {len(manifest_entries)} (include_media={include_media})")
PYEOF
