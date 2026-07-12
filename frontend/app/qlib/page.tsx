"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function QlibRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/factors?tab=ml");
  }, [router]);
  return <p className="p-4 text-sm text-muted-foreground">跳转到因子实验室 ML 标签…</p>;
}
