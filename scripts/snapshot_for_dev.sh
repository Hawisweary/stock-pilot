#!/usr/bin/env bash
# 主机一键导出一致性数据快照,供开发机拉取。用 sqlite 在线备份(无需停调度,不会拷到写一半的库)。
# 用法: ./scripts/snapshot_for_dev.sh [输出目录，默认 ./snapshot]
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-snapshot}"
mkdir -p "$OUT"
echo "导出到 $OUT/ ..."

for db in afr us cache; do
  src="data/${db}.db"
  [ -f "$src" ] || { echo "  跳过 $src(不存在)"; continue; }
  echo "  备份 $src ($(du -h "$src" | cut -f1)) ..."
  sqlite3 "$src" ".backup '$OUT/${db}.db'"
done

cat > "$OUT/README.txt" <<EOF
数据快照 $(date '+%Y-%m-%d %H:%M')
=================================
拷到开发机 data/ 下(覆盖同名文件):
  afr.db   → data/afr.db
  us.db    → data/us.db
  cache.db → data/cache.db

⚠️ 密钥不在快照里(安全),需单独手动拷贝(勿走 git / 勿走公开渠道):
  .env
  backend/.env

⚠️ 铁律:开发机的库只用于测试,绝不推回主机(会覆盖真实组合/数据)。
EOF

echo "完成。清单见 $OUT/README.txt"
echo "提醒:密钥 .env / backend/.env 需另行安全拷贝(不在快照内)。"
