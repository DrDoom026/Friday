import { useCallback, useState } from "react";
import { PerformanceTier } from "../types";

const LOW_GPU = /mali-4|mali-t|videocore|adreno [1-5]\d\d|intel.*hd graphics [3-5]\d\d|swiftshader|llvmpipe/i;

function detectTier(): PerformanceTier {
  if (typeof window === "undefined") return "mid";
  const nav = navigator as Navigator & { deviceMemory?: number };
  const cores = nav.hardwareConcurrency ?? 4;
  const memory = nav.deviceMemory ?? 4;
  const mobile = /android|iphone|ipad|mobile/i.test(nav.userAgent);
  let renderer = "";
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    const info = gl?.getExtension("WEBGL_debug_renderer_info");
    if (gl && info) renderer = String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
  } catch {
    renderer = "";
  }
  if (LOW_GPU.test(renderer) || cores <= 2 || memory <= 2) return "low";
  if (mobile || cores <= 4 || memory <= 4) return "mid";
  return "high";
}

export const TIER_CONFIG: Record<
  PerformanceTier,
  { particleScale: number; dpr: number; antialias: boolean; outerShell: boolean; foreground: boolean }
> = {
  high: { particleScale: 1, dpr: 2, antialias: true, outerShell: true, foreground: true },
  mid: { particleScale: 0.5, dpr: 1.25, antialias: false, outerShell: true, foreground: true },
  low: { particleScale: 0.22, dpr: 1, antialias: false, outerShell: false, foreground: false },
};

export function usePerformanceTier() {
  const [tier, setTier] = useState<PerformanceTier>(detectTier);
  const downgrade = useCallback(() => {
    setTier((t) => (t === "high" ? "mid" : "low"));
  }, []);
  return { tier, downgrade, config: TIER_CONFIG[tier] };
}
