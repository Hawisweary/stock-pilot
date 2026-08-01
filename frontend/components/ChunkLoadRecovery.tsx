"use client";

import { useEffect } from "react";

const RELOAD_KEY = "afr-chunk-reload-ts";

/** Tauri WebKit 缓存旧 HTML 时，chunk 404 后自动刷新一次。 */
export function ChunkLoadRecovery() {
  useEffect(() => {
    const tryReload = (reason: string) => {
      const last = Number(sessionStorage.getItem(RELOAD_KEY) || "0");
      if (Date.now() - last < 15_000) return;
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
      console.warn("[ChunkLoadRecovery]", reason);
      window.location.reload();
    };

    const onError = (event: ErrorEvent) => {
      const msg = String(event.message || "");
      if (msg.includes("Failed to load chunk") || msg.includes("Loading chunk")) {
        tryReload(msg);
      }
    };

    const onRejection = (event: PromiseRejectionEvent) => {
      const msg = String(event.reason?.message || event.reason || "");
      if (msg.includes("Failed to load chunk") || msg.includes("Loading chunk")) {
        tryReload(msg);
      }
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}
