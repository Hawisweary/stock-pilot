"""按用户七优先级主题补充股票池：入库、命名、标签、分组。

请使用 venv-quant 运行（Python >= 3.10）:
  bash backend/scripts/run_py.sh scripts/add_theme_watchlist.py
或:
  ../venv-quant/bin/python scripts/add_theme_watchlist.py
"""
from __future__ import annotations

import os
import sys

if sys.version_info < (3, 10):
    print("错误: 需要 Python 3.10+（当前 %d.%d）。" % sys.version_info[:2])
    print("请使用: bash backend/scripts/run_py.sh scripts/add_theme_watchlist.py")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from config import DB_PATH

# code -> {name, market, tags, groups}
NEW_STOCKS: dict[str, dict] = {
    # P1 低空经济 / 商业航天
    "002085": {"name": "万丰奥威", "tags": ["低空经济"], "groups": ["低空经济", "航空航天"]},
    "001696": {"name": "宗申动力", "tags": ["低空经济"], "groups": ["低空经济", "航空航天"]},
    "688631": {"name": "莱斯信息", "tags": ["低空经济"], "groups": ["低空经济", "航空航天"]},
    "000547": {"name": "航天发展", "tags": ["商业航天"], "groups": ["商业航天", "航空航天", "SpaceX"]},
    "688375": {"name": "国博电子", "tags": ["商业航天"], "groups": ["商业航天", "航空航天", "SpaceX"]},
    # P2 人形机器人
    "603667": {"name": "五洲新春", "tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "300953": {"name": "震裕科技", "tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    # P3 AI算力
    "300308": {"name": "中际旭创", "tags": ["AI算力"], "groups": ["AI算力"]},
    "600183": {"name": "生益科技", "tags": ["AI算力"], "groups": ["AI算力"]},
    "688256": {"name": "寒武纪", "tags": ["AI算力"], "groups": ["AI算力"]},
    "000938": {"name": "紫光股份", "tags": ["AI算力"], "groups": ["AI算力"]},
    # P4 半导体自主可控
    "002371": {"name": "北方华创", "tags": ["半导体自主可控"], "groups": ["半导体自主可控"]},
    "603986": {"name": "兆易创新", "tags": ["半导体自主可控", "消费电子"], "groups": ["半导体自主可控", "消费电子"]},
    "688012": {"name": "中微公司", "tags": ["半导体自主可控"], "groups": ["半导体自主可控"]},
    "688347": {"name": "华虹公司", "tags": ["半导体自主可控"], "groups": ["半导体自主可控"]},
    "600363": {"name": "联创光电", "tags": ["半导体自主可控"], "groups": ["半导体自主可控"]},
    # P5 自动驾驶
    "HSAI": {"name": "禾赛科技", "market": "US", "tags": ["自动驾驶"], "groups": ["自动驾驶"]},
    "002920": {"name": "德赛西威", "tags": ["自动驾驶"], "groups": ["自动驾驶"]},
    # P6 信创
    "600536": {"name": "中国软件", "tags": ["信创"], "groups": ["信创"]},
    "688111": {"name": "金山办公", "tags": ["信创"], "groups": ["信创"]},
    "688692": {"name": "达梦数据", "tags": ["信创"], "groups": ["信创"]},
    "688083": {"name": "中望软件", "tags": ["信创"], "groups": ["信创"]},
    # P7 消费电子
    "002475": {"name": "立讯精密", "tags": ["消费电子"], "groups": ["消费电子"]},
    "300433": {"name": "蓝思科技", "tags": ["消费电子", "人形机器人"], "groups": ["消费电子", "人形机器人"]},
}

# 防御/均衡板块 — 银行、消费、ST负样本、公用事业、医药、煤炭
SECTOR_STOCKS: dict[str, dict] = {
    # 银行
    "601398": {"name": "工商银行", "tags": ["银行"], "groups": ["银行"]},
    "601939": {"name": "建设银行", "tags": ["银行"], "groups": ["银行"]},
    "601988": {"name": "中国银行", "tags": ["银行"], "groups": ["银行"]},
    "601328": {"name": "交通银行", "tags": ["银行"], "groups": ["银行"]},
    "601658": {"name": "邮储银行", "tags": ["银行"], "groups": ["银行"]},
    # 消费（五粮液与美的/茅台重叠，暂不纳入）
    "600519": {"name": "贵州茅台", "tags": ["消费"], "groups": ["消费"]},
    "600887": {"name": "伊利股份", "tags": ["消费"], "groups": ["消费"]},
    # ST 负样本（ML 训练用，禁止实盘）
    "600696": {
        "name": "*ST岩石",
        "tags": ["ST负样本"],
        "groups": ["ST负样本"],
        "sector": "ML负样本-禁止实盘",
    },
    "600355": {
        "name": "*ST精伦",
        "tags": ["ST负样本"],
        "groups": ["ST负样本"],
        "sector": "ML负样本-禁止实盘",
    },
    "600421": {
        "name": "*ST华嵘",
        "tags": ["ST负样本"],
        "groups": ["ST负样本"],
        "sector": "ML负样本-禁止实盘",
    },
    # 公用事业
    "600900": {"name": "长江电力", "tags": ["公用事业"], "groups": ["公用事业"]},
    "600025": {"name": "华能水电", "tags": ["公用事业"], "groups": ["公用事业"]},
    "600803": {"name": "新奥股份", "tags": ["公用事业"], "groups": ["公用事业"]},
    # 医药
    "603259": {"name": "药明康德", "tags": ["医药"], "groups": ["医药"]},
    "600276": {"name": "恒瑞医药", "tags": ["医药"], "groups": ["医药"]},
    "300760": {"name": "迈瑞医疗", "tags": ["医药"], "groups": ["医药"]},
    # 煤炭/资源
    "601088": {"name": "中国神华", "tags": ["煤炭资源"], "groups": ["煤炭资源"]},
    "601225": {"name": "陕西煤业", "tags": ["煤炭资源"], "groups": ["煤炭资源"]},
}

# 已在池中 — 补主题标签与分组
EXISTING_TAGS: dict[str, dict] = {
    # P1
    "301005": {"name": "超捷股份", "tags": ["低空经济", "商业航天"], "groups": ["低空经济", "商业航天", "航空航天"]},
    "688297": {"tags": ["低空经济"], "groups": ["低空经济", "航空航天"]},
    "688568": {"tags": ["低空经济"], "groups": ["低空经济", "航空航天"]},
    "600879": {"tags": ["商业航天"], "groups": ["商业航天", "航空航天", "SpaceX"]},
    "600118": {"tags": ["商业航天"], "groups": ["商业航天", "航空航天", "SpaceX"]},
    "600343": {"tags": ["商业航天"], "groups": ["商业航天", "航空航天"]},
    # P2
    "002050": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "601689": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "688322": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "002747": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "300124": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "300607": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "688017": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    "688777": {"tags": ["人形机器人"], "groups": ["人形机器人", "机器人"]},
    # P3
    "300502": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "300476": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "002463": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "300394": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "601138": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "603019": {"tags": ["AI算力"], "groups": ["AI算力"]},
    "688041": {"tags": ["AI算力", "半导体自主可控"], "groups": ["AI算力", "半导体自主可控"]},
    "300474": {"tags": ["AI算力"], "groups": ["AI算力"]},
    # P4
    "600703": {"tags": ["半导体自主可控"], "groups": ["半导体自主可控"]},
    # P5
    "000625": {"tags": ["自动驾驶"], "groups": ["自动驾驶"]},
    # P7
    "000333": {"name": "美的集团", "tags": ["消费", "消费电子"], "groups": ["消费", "消费电子"]},
    "000725": {"tags": ["消费电子"], "groups": ["消费电子"]},
    # 银行（已在池）
    "600036": {"name": "招商银行", "tags": ["银行"], "groups": ["银行"]},
    "601288": {"name": "农业银行", "tags": ["银行"], "groups": ["银行"]},
}


def _ensure_group(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM custom_groups WHERE name=?", (name,)).fetchone()
    if row:
        return int(row[0])
    conn.execute(
        "INSERT INTO custom_groups (name, description, sort_order) VALUES (?, ?, ?)",
        (name, f"主题分组：{name}", 0),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _ensure_tag(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO industry_tags(name) VALUES(?)", (name,))
    row = conn.execute("SELECT id FROM industry_tags WHERE name=?", (name,)).fetchone()
    return int(row[0])


def _link_tag(conn: sqlite3.Connection, stock_id: int, tag: str) -> None:
    tid = _ensure_tag(conn, tag)
    conn.execute(
        "INSERT OR IGNORE INTO stock_industries(stock_id, industry_id) VALUES (?, ?)",
        (stock_id, tid),
    )


def _link_group(conn: sqlite3.Connection, stock_id: int, group: str) -> None:
    gid = _ensure_group(conn, group)
    conn.execute(
        "INSERT OR IGNORE INTO stock_group_members (group_id, stock_id) VALUES (?, ?)",
        (gid, stock_id),
    )


def _register(conn: sqlite3.Connection, code: str, market: str = "A") -> dict:
    row = conn.execute(
        "SELECT id, is_active FROM stocks WHERE code=? AND market=?",
        (code, market),
    ).fetchone()
    if row:
        sid, active = int(row[0]), int(row[1])
        if active:
            return {"code": code, "status": "skipped", "stock_id": sid}
        conn.execute(
            "UPDATE stocks SET is_active=1, updated_at=datetime('now') WHERE id=?",
            (sid,),
        )
        return {"code": code, "status": "reactivated", "stock_id": sid}
    cur = conn.execute(
        "INSERT INTO stocks (code, name, market) VALUES (?, ?, ?)",
        (code, code, market),
    )
    return {"code": code, "status": "added", "stock_id": int(cur.lastrowid)}


def _stock_id(conn: sqlite3.Connection, code: str, market: str = "A") -> int | None:
    row = conn.execute(
        "SELECT id FROM stocks WHERE code=? AND market=?",
        (code, market),
    ).fetchone()
    return int(row[0]) if row else None


def _apply_meta(conn: sqlite3.Connection, stock_id: int, meta: dict) -> None:
    if meta.get("name") or meta.get("sector"):
        conn.execute(
            """UPDATE stocks SET name=COALESCE(?, name), sector=COALESCE(?, sector),
               updated_at=datetime('now') WHERE id=?""",
            (meta.get("name"), meta.get("sector"), stock_id),
        )
    for tag in meta.get("tags") or []:
        _link_tag(conn, stock_id, tag)
    for grp in meta.get("groups") or []:
        _link_group(conn, stock_id, grp)


def main() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")

    registered: list[dict] = []
    new_ids: list[int] = []

    all_new = {**NEW_STOCKS, **SECTOR_STOCKS}
    for code, meta in all_new.items():
        market = meta.get("market", "A")
        reg = _register(conn, code, market)
        registered.append(reg)
        sid = reg.get("stock_id")
        if not sid:
            continue
        if reg.get("status") in ("added", "reactivated"):
            new_ids.append(int(sid))
        _apply_meta(conn, int(sid), meta)

    for code, meta in EXISTING_TAGS.items():
        sid = _stock_id(conn, code, "A")
        if not sid:
            print(f"[warn] 池中未找到 {code}，跳过标签")
            continue
        _apply_meta(conn, sid, meta)

    conn.commit()
    conn.close()

    print(f"注册结果: {len(registered)} 条（含主题股 {len(NEW_STOCKS)} + 板块股 {len(SECTOR_STOCKS)}）")
    added = [r for r in registered if r.get("status") in ("added", "reactivated")]
    skipped = [r for r in registered if r.get("status") == "skipped"]
    print(f"  新增/激活: {len(added)}, 已存在跳过: {len(skipped)}")
    if added:
        print("  新增代码:", ", ".join(r["code"] for r in added))
    print(f"  新股票 ID: {new_ids}")
    print("提示: 数据抓取请在后端服务空闲时执行 POST /api/stocks/onboard 或逐只 fetch")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
