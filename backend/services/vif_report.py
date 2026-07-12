"""V2: 因子VIF共线性检查"""
import sqlite3, numpy as np
from config import DB_PATH

def compute_vif():
    """计算7因子VIF并输出报告"""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT fundamental_score, technical_score, sentiment_score,
               capital_score, policy_score, mood_score, val_score
        FROM comprehensive_scores WHERE calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
        AND fundamental_score IS NOT NULL AND technical_score IS NOT NULL
        AND capital_score IS NOT NULL AND policy_score IS NOT NULL
        AND mood_score IS NOT NULL AND val_score IS NOT NULL
    """).fetchall()
    conn.close()

    names = ["基本面", "技术面", "新闻面", "资金面", "政策面", "情绪面", "估值面"]
    features = np.column_stack([np.array([r[i] for r in rows]) for i in range(7)])
    n = features.shape[0]

    vif_results = []
    for i in range(7):
        y = features[:, i]
        X = np.delete(features, i, axis=1)
        X = np.column_stack([np.ones(n), X])
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            y_pred = X @ beta
            r2 = 1 - np.sum((y - y_pred)**2) / np.sum((y - np.mean(y))**2)
            vif = 1 / max(1 - r2, 0.01)
        except Exception:
            vif = 999  # 矩阵奇异/数值异常时设为上限值
        status = "✅ OK" if vif < 5 else "⚠️ 偏高" if vif < 10 else "🔴 严重"
        vif_results.append((names[i], vif, status))

    # 排序输出
    vif_results.sort(key=lambda x: x[1], reverse=True)
    lines = [f"===== VIF 共线性检查报告 ({n}样本) ====="]
    for name, vif, status in vif_results:
        lines.append(f"  {name}: VIF={vif:.1f} {status}")
    lines.append(f"  阈值: VIF<5 可接受, 5-10 偏高, >10 严重共线性")
    
    # 计算相关性矩阵
    corr = np.corrcoef(features.T)
    lines.append("\n--- 相关系数矩阵 (|r|>0.5标出) ---")
    for i in range(7):
        row = []
        for j in range(i):
            if abs(corr[i,j]) > 0.5:
                row.append(f"{names[i]}↔{names[j]}: r={corr[i,j]:.3f}")
        if row: lines.extend(row)
    
    report = "\n".join(lines)
    return report

if __name__ == "__main__":
    print(compute_vif())
