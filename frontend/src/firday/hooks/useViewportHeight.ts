import { useEffect } from "react";

// Keeps --app-h in sync with the visual viewport (mobile browser chrome / keyboard)
export function useViewportHeight() {
  useEffect(() => {
    const vv = window.visualViewport;
    const apply = () => {
      const h = vv ? vv.height : window.innerHeight;
      document.documentElement.style.setProperty("--app-h", `${Math.round(h)}px`);
    };
    apply();
    window.addEventListener("resize", apply);
    vv?.addEventListener("resize", apply);
    return () => {
      window.removeEventListener("resize", apply);
      vv?.removeEventListener("resize", apply);
    };
  }, []);
}
