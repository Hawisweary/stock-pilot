from config import DB_PATH
"""
自定义分组管理 API
- GET  /api/groups              列出所有分组及成员
- POST /api/groups              创建分组
- PUT  /api/groups/{id}         编辑分组
- DELETE /api/groups/{id}       删除分组
- POST /api/groups/{id}/add     添加股票到分组
- POST /api/groups/{id}/remove  从分组移除股票
"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql, execute_insert, execute_update
from services.score_sql import per_stock_latest_v5_join

router = APIRouter(prefix="/api", tags=["groups"])


@router.get("/groups")
async def list_groups():
    """列出所有分组及其成员（含评分）"""
    groups = execute_sql("SELECT * FROM custom_groups ORDER BY sort_order, id")
    result = []
    for g in groups:
        g = dict(g)
        join_v5 = per_stock_latest_v5_join("cs")
        members = execute_sql(
            f"""
            SELECT s.id, s.code, s.name, s.industry,
                   cs.composite_v5, cs.veto_status
            FROM stock_group_members sgm
            JOIN stocks s ON sgm.stock_id = s.id
            {join_v5}
            WHERE sgm.group_id=? AND s.is_active=1
            ORDER BY sgm.sort_order, s.code
            """,
            (g["id"],),
        )
        g["stocks"] = [dict(m) for m in members]
        g["stock_count"] = len(members)
        result.append(g)
    return result


@router.post("/groups")
async def create_group(body: dict):
    """创建新分组"""
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名称不能为空")
    desc = body.get("description", "").strip()
    gid = execute_insert(
        "INSERT INTO custom_groups (name, description, sort_order) VALUES (?, ?, ?)",
        (name, desc, body.get("sort_order", 0))
    )
    row = execute_sql("SELECT * FROM custom_groups WHERE id=?", (gid,))
    return dict(row[0]) if row else {}


@router.put("/groups/{group_id}")
async def update_group(group_id: int, body: dict):
    """编辑分组名称/描述"""
    existing = execute_sql("SELECT id FROM custom_groups WHERE id=?", (group_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="分组不存在")
    updates = []
    values = []
    for f in ["name", "description", "sort_order"]:
        if f in body:
            updates.append(f"{f}=?")
            values.append(body[f])
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    execute_update(f"UPDATE custom_groups SET {', '.join(updates)} WHERE id=?", values + [group_id])
    row = execute_sql("SELECT * FROM custom_groups WHERE id=?", (group_id,))
    return dict(row[0]) if row else {}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int):
    """删除分组（级联删除成员关系）"""
    affected = execute_update("DELETE FROM custom_groups WHERE id=?", (group_id,))
    if not affected:
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"ok": True}


@router.post("/groups/{group_id}/add")
async def add_stock_to_group(group_id: int, body: dict):
    """添加股票到分组"""
    stock_id = body.get("stock_id")
    if not stock_id:
        raise HTTPException(status_code=400, detail="需要 stock_id")
    existing = execute_sql(
        "SELECT id FROM stock_group_members WHERE group_id=? AND stock_id=?",
        (group_id, stock_id)
    )
    if existing:
        return {"ok": True, "message": "已在分组中"}
    execute_insert(
        "INSERT INTO stock_group_members (group_id, stock_id) VALUES (?, ?)",
        (group_id, stock_id)
    )
    return {"ok": True}


@router.post("/groups/{group_id}/remove")
async def remove_stock_from_group(group_id: int, body: dict):
    """从分组移除股票"""
    stock_id = body.get("stock_id")
    if not stock_id:
        raise HTTPException(status_code=400, detail="需要 stock_id")
    execute_update(
        "DELETE FROM stock_group_members WHERE group_id=? AND stock_id=?",
        (group_id, stock_id)
    )
    return {"ok": True}
