"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Building2, Users } from "lucide-react";

interface CompanyInfo {
  com_name: string;
  chairman: string;
  manager: string;
  secretary: string;
  reg_capital: number | null;
  setup_date: string;
  province: string;
  city: string;
  website: string;
  employees: number | null;
  main_business: string;
  introduction: string;
}

interface Manager {
  name: string;
  lev: string;
  title: string;
  gender: string;
  edu: string;
  birthday: string;
  begin_date: string;
}

interface Props { stockId: number }

export function CompanyInfoCard({ stockId }: Props) {
  const [info, setInfo] = useState<CompanyInfo | null>(null);
  const [managers, setManagers] = useState<Manager[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAllManagers, setShowAllManagers] = useState(false);

  useEffect(() => {
    if (!stockId) return;
    setLoading(true);
    fetch(`/api/stocks/${stockId}/company`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setInfo(d?.info ?? null);
        setManagers(d?.managers ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [stockId]);

  if (loading) return null;
  if (!info && managers.length === 0) return null;

  const shownManagers = showAllManagers ? managers : managers.slice(0, 6);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Building2 className="h-4 w-4" /> 公司背景
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {info && (
          <>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              {info.chairman && (
                <div><span className="text-muted-foreground">董事长 </span>{info.chairman}</div>
              )}
              {info.manager && (
                <div><span className="text-muted-foreground">总经理 </span>{info.manager}</div>
              )}
              {info.setup_date && (
                <div><span className="text-muted-foreground">成立日期 </span>{info.setup_date}</div>
              )}
              {(info.province || info.city) && (
                <div><span className="text-muted-foreground">所在地 </span>{info.province}{info.city}</div>
              )}
              {info.employees != null && (
                <div><span className="text-muted-foreground">员工人数 </span>{info.employees.toLocaleString()}</div>
              )}
              {info.reg_capital != null && (
                <div><span className="text-muted-foreground">注册资本 </span>{info.reg_capital.toFixed(0)} 万元</div>
              )}
              {info.website && (
                <div className="col-span-2 truncate"><span className="text-muted-foreground">官网 </span>{info.website}</div>
              )}
            </div>
            {info.main_business && (
              <p className="text-xs text-muted-foreground leading-relaxed">
                <span className="font-medium text-foreground">主营业务：</span>{info.main_business}
              </p>
            )}
          </>
        )}

        {managers.length > 0 && (
          <div className="pt-1 border-t">
            <div className="flex items-center gap-1.5 text-xs font-medium mb-2 mt-2">
              <Users className="h-3.5 w-3.5" /> 现任管理层（{managers.length}）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {shownManagers.map((m, i) => (
                <span
                  key={i}
                  title={`${m.edu || ""} ${m.gender === "M" ? "男" : m.gender === "F" ? "女" : ""}`.trim()}
                  className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700"
                >
                  {m.name} <span className="text-muted-foreground">{m.title}</span>
                </span>
              ))}
            </div>
            {managers.length > 6 && (
              <button
                onClick={() => setShowAllManagers(!showAllManagers)}
                className="text-[11px] text-blue-600 mt-1.5 hover:underline"
              >
                {showAllManagers ? "收起" : `展开全部 ${managers.length} 位`}
              </button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
