import sqlite3, sys
sys.path.insert(0, '.')
import config
conn = sqlite3.connect(config.DB_PATH)
total = conn.execute('SELECT COUNT(*) FROM stocks WHERE is_active=1').fetchone()[0]
fi = conn.execute('SELECT COUNT(DISTINCT stock_id) FROM financial_indicators').fetchone()[0]
fs = conn.execute('SELECT COUNT(DISTINCT stock_id) FROM factor_scores').fetchone()[0]
v5 = conn.execute('SELECT COUNT(DISTINCT stock_id) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL').fetchone()[0]
conn.close()
print(f'财务指标: {fi:5d}/{total} ({fi/total*100:.1f}%)')
print(f'因子评分: {fs:5d}/{total} ({fs/total*100:.1f}%)')
print(f'V5综合分: {v5:5d}/{total} ({v5/total*100:.1f}%)')
print(f'待补全:   {total-fi}')
