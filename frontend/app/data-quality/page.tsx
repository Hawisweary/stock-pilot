"use client";

import { DataQualityDashboard } from "@/components/DataQualityDashboard";
import { DataTabs } from "@/components/DataTabs";

export default function DataQualityPage() {
  return (
    <main className="container mx-auto px-4 py-6 space-y-6">
      <DataTabs active="data-quality" />
      <DataQualityDashboard />
    </main>
  );
}
