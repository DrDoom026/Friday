import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import AppShell from "./firday/AppShell";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppShell />
  </StrictMode>,
);
