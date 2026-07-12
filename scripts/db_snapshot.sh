#!/usr/bin/env bash
# 生成 SQLite 只读副本，供 Polars/对账脚本读取，避免与 API 写锁冲突
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${AFR_DB_PATH:-$ROOT/data/afr.db}"
DST="${AFR_DB_READ_PATH:-$ROOT/data/afr_read.db}"
mkdir -p "$(dirname "$DST")"
if [[ ! -f "$SRC" ]]; then
  echo "Source DB not found: $SRC" >&2
  exit 1
fi
sqlite3 "$SRC" ".backup '$DST'"
echo "Snapshot: $SRC -> $DST ($(date -Iseconds))"
