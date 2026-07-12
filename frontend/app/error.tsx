"use client";

import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[ErrorBoundary]", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 px-4">
      <div className="text-6xl">⚠️</div>
      <h2 className="text-xl font-semibold text-gray-200">页面加载出错</h2>
      <p className="text-sm text-gray-400 max-w-md text-center">
        {error.message || "发生未知错误，请重试"}
      </p>
      <div className="flex gap-3">
        <button
          onClick={reset}
          className="px-4 py-2 bg-teal-600 text-white rounded-lg text-sm hover:bg-teal-500 transition"
        >
          重试
        </button>
        <button
          onClick={() => (window.location.href = "/")}
          className="px-4 py-2 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600 transition"
        >
          回到首页
        </button>
      </div>
    </div>
  );
}
