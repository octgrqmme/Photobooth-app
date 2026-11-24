#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
backup_dir=""
target_root="$app_root"
dry_run=0
safety_backup=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      target_root="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --no-safety-backup)
      safety_backup=0
      shift
      ;;
    -h|--help)
      echo "Usage: $0 <backup_dir> [--target <dir>] [--dry-run] [--no-safety-backup]"
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$backup_dir" ]]; then
        backup_dir="$1"
        shift
      else
        echo "Unexpected positional argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$backup_dir" ]]; then
  echo "Usage: $0 <backup_dir> [--target <dir>] [--dry-run] [--no-safety-backup]" >&2
  exit 1
fi

python_bin="$app_root/venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python is required to run restore." >&2
  exit 1
fi

# Step 1: Verify manifest, checksums, and SQLite integrity before mutating anything
"$python_bin" - "$backup_dir" <<'PYEOF'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

backup_dir = Path(sys.argv[1]).resolve()
manifest_path = backup_dir / "manifest.json"
files_root = backup_dir / "files"

if not manifest_path.is_file() or not files_root.is_dir():
    print(f"Invalid backup directory: {backup_dir}", file=sys.stderr)
    sys.exit(1)

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
    print("Unsupported backup manifest format.", file=sys.stderr)
    sys.exit(1)

for entry in manifest["files"]:
    rel = Path(entry["path"])
    if rel.is_absolute() or ".." in rel.parts:
        print(f"Unsafe path in manifest: {rel}", file=sys.stderr)
        sys.exit(1)
    src = files_root / rel
    if not src.is_file():
        print(f"Missing backed-up file: {rel}", file=sys.stderr)
        sys.exit(1)
    hasher = hashlib.sha256()
    with src.open("rb") as fh:
        while chunk := fh.read(65536):
            hasher.update(chunk)
    if src.stat().st_size != entry["size"] or hasher.hexdigest() != entry["sha256"]:
        print(f"Checksum mismatch for: {rel}", file=sys.stderr)
        sys.exit(1)
    if rel.as_posix() in {"database/database.sqlite", "userdata/photobooth_platform.sqlite"}:
        conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                print(f"Backed-up SQLite failed integrity_check: {row}", file=sys.stderr)
                sys.exit(1)
        finally:
            conn.close()

print(f"Verified {len(manifest['files'])} file(s) from {backup_dir} (created {manifest.get('created_at')})")
PYEOF

if [[ "$dry_run" -eq 1 ]]; then
  echo "Dry-run complete: no files were modified in $target_root."
  exit 0
fi

# Step 2: Create pre-restore safety backup unless disabled
if [[ "$safety_backup" -eq 1 ]]; then
  safety_dir="$target_root/backups/pre-restore-$(date +%Y%m%d-%H%M%S)"
  bash "$app_root/scripts/backup.sh" --source "$target_root" --output "$safety_dir"
fi

# Step 3: Restore files atomically into target_root
"$python_bin" - "$backup_dir" "$target_root" <<'PYEOF'
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

backup_dir = Path(sys.argv[1]).resolve()
target_root = Path(sys.argv[2]).resolve()
manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
files_root = backup_dir / "files"

restored = 0
for entry in manifest["files"]:
    rel = Path(entry["path"])
    src = files_root / rel
    dest = target_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid4().hex}.restore.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        if dest.suffix == ".sqlite":
            Path(str(dest) + "-wal").unlink(missing_ok=True)
            Path(str(dest) + "-shm").unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    restored += 1

print(f"Restored {restored} file(s) into {target_root}")
PYEOF
