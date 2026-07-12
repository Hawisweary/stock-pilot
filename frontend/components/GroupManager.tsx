"use client";

import { useEffect, useState } from "react";
import { Pencil, Trash2, Plus, X, GripVertical, UserPlus } from "lucide-react";
import { useToast } from "@/lib/useToast";

interface Group {
  id: number; name: string; description: string;
  stocks: { id: number; code: string; name: string; score?: number | null; composite_v5?: number | null; veto_status?: string }[];
  stock_count: number;
}

export function GroupManager({ onSelectGroup }: { onSelectGroup?: (g: Group) => void }) {
  const toast = useToast();
  const [groups, setGroups] = useState<Group[]>([]);
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<Group | null>(null);
  const [loading, setLoading] = useState(true);
  const [addingToGroup, setAddingToGroup] = useState<number | null>(null);
  const [allStocks, setAllStocks] = useState<any[]>([]);
  const [search, setSearch] = useState("");

  const load = async () => {
    try {
      const res = await fetch("/api/groups");
      setGroups(await res.json());
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  useEffect(() => { load(); loadAllStocks(); }, []);

  const loadAllStocks = async () => {
    try {
      const res = await fetch("/api/stocks");
      setAllStocks(await res.json());
    } catch (e) { console.error(e); }
  };

  const create = async () => {
    if (!newName.trim()) return;
    try {
      await fetch("/api/groups", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newName.trim() }) });
      setNewName("");
      await load();
      toast.success(`分组「${newName.trim()}」已创建`);
    } catch { toast.error("创建分组失败"); }
  };

  const del = async (id: number) => {
    if (!confirm("删除分组？股票不会删除")) return;
    try {
      await fetch(`/api/groups/${id}`, { method: "DELETE" });
      await load();
      toast.success("分组已删除");
    } catch { toast.error("删除分组失败"); }
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      await fetch(`/api/groups/${editing.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: editing.name, description: editing.description }) });
      setEditing(null);
      await load();
      toast.success("分组已保存");
    } catch { toast.error("保存失败"); }
  };

  const removeStock = async (gid: number, sid: number) => {
    try {
      await fetch(`/api/groups/${gid}/remove`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stock_id: sid }) });
      await load();
    } catch { toast.error("移除失败"); }
  };

  const addStock = async (gid: number, sid: number, sname?: string) => {
    try {
      await fetch(`/api/groups/${gid}/add`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stock_id: sid }) });
      await load();
      if (sname) toast.success(`${sname} 已加入分组`);
    } catch { toast.error("加入分组失败"); }
  };

  const getScoreColor = (s: number) => s >= 70 ? "text-green-600" : s >= 40 ? "text-yellow-600" : "text-red-600";

  return (
    <div className="space-y-4">
      {/* 创建分组 */}
      <div className="flex gap-2">
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="新分组名称..." className="flex-1 px-3 py-1.5 border rounded-md text-sm" onKeyDown={(e) => e.key === "Enter" && create()} />
        <button onClick={create} disabled={!newName.trim()} className="px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"><Plus className="h-4 w-4" /></button>
      </div>

      {loading ? <div className="animate-pulse h-20 bg-muted rounded" /> : groups.length === 0 ? <p className="text-sm text-muted-foreground text-center py-4">暂无分组，上方创建</p> : (
        <div className="space-y-3">
          {groups.map((g) => (
            <div key={g.id} className="border rounded-lg">
              {/* 分组头 */}
              <div className="px-3 py-2 bg-muted/30 rounded-t-lg flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <GripVertical className="h-3.5 w-3.5 text-muted-foreground" />
                  {editing?.id === g.id ? (
                    <div className="flex gap-1">
                      <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} className="px-2 py-0.5 border rounded text-sm w-32" />
                      <input value={editing.description || ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} placeholder="备注" className="px-2 py-0.5 border rounded text-sm w-24" />
                      <button onClick={saveEdit} className="px-2 py-0.5 bg-primary text-primary-foreground rounded text-xs">保存</button>
                      <button onClick={() => setEditing(null)} className="px-2 py-0.5 border rounded text-xs"><X className="h-3 w-3" /></button>
                    </div>
                  ) : (
                    <span className="font-semibold text-sm cursor-pointer hover:underline" onClick={() => onSelectGroup?.(g)}>{g.name} ({g.stock_count})</span>
                  )}
                  {g.description && !editing && <span className="text-xs text-muted-foreground">— {g.description}</span>}
                </div>
                <div className="flex gap-1">
                  <button onClick={() => { setAddingToGroup(g.id); setSearch(""); }} className="p-1 hover:bg-blue-50 rounded" title="加入股票"><UserPlus className="h-3 w-3 text-blue-500" /></button>
                  <button onClick={() => setEditing({ ...g })} className="p-1 hover:bg-muted rounded"><Pencil className="h-3 w-3" /></button>
                  <button onClick={() => del(g.id)} className="p-1 hover:bg-red-100 rounded"><Trash2 className="h-3 w-3 text-red-500" /></button>
                </div>
              </div>
              {/* 加入股票面板 */}
              {addingToGroup === g.id && (
                <div className="px-3 py-2 border-t bg-blue-50/50">
                  <div className="flex items-center gap-2 mb-2">
                    <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索代码或名称..." className="flex-1 px-2 py-1 border rounded text-xs" />
                    <button onClick={() => setAddingToGroup(null)} className="text-xs text-muted-foreground hover:text-foreground">关闭</button>
                  </div>
                  <div className="max-h-32 overflow-y-auto space-y-0.5">
                    {allStocks
                      .filter(s => !g.stocks.some(gs => gs.id === s.id) && (!search || s.code.includes(search) || s.name.includes(search)))
                      .slice(0, 20)
                      .map(s => (
                        <div key={s.id} className="flex items-center justify-between py-0.5 px-1 rounded hover:bg-blue-100 text-xs cursor-pointer"
                             onClick={() => addStock(g.id, s.id, s.name)}>
                          <span><span className="font-mono">{s.code}</span> <span className="text-muted-foreground">{s.name}</span></span>
                          <Plus className="h-3 w-3 text-blue-500" />
                        </div>
                      ))}
                  </div>
                </div>
              )}
              {/* 成员 */}
              {g.stocks.length > 0 && (
                <div className="px-3 py-1.5 divide-y">
                  {g.stocks.map((s) => (
                    <div key={s.id} className="py-1.5 flex items-center justify-between text-sm">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{s.name}</span>
                        <span className="text-muted-foreground font-mono text-xs">{s.code}</span>
                        {(s.score ?? s.composite_v5) != null && <span className={`font-bold text-xs ${getScoreColor(s.score ?? s.composite_v5 ?? 0)}`}>{(s.score ?? s.composite_v5)!.toFixed(1)}</span>}
                      </div>
                      <button onClick={() => removeStock(g.id, s.id)} className="p-0.5 hover:bg-red-50 rounded"><X className="h-3 w-3 text-muted-foreground hover:text-red-500" /></button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
