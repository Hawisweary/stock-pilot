"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 因子 IC 已合并至 /factors?tab=ic */
export default function IcRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/factors?tab=ic");
  }, [router]);
  return <div className="p-8 text-sm text-muted-foreground">跳转至因子实验室…</div>;
}
