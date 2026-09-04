import { useRef } from "react";
import { useFrame } from "@react-three/fiber";

// Watches frame rate after warm-up and asks for a tier downgrade when it sags
export const PerfMonitor = ({ onDowngrade, enabled }: { onDowngrade: () => void; enabled: boolean }) => {
  const acc = useRef({ frames: 0, elapsed: 0, warm: 0, cooldown: 0 });
  useFrame((_, dt) => {
    if (!enabled) return;
    const a = acc.current;
    if (a.warm < 3) {
      a.warm += dt;
      return;
    }
    if (a.cooldown > 0) {
      a.cooldown -= dt;
      return;
    }
    a.frames += 1;
    a.elapsed += dt;
    if (a.elapsed >= 2) {
      const fps = a.frames / a.elapsed;
      a.frames = 0;
      a.elapsed = 0;
      if (fps < 28) {
        a.cooldown = 5;
        onDowngrade();
      }
    }
  });
  return null;
};
