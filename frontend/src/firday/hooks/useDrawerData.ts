import { useCallback, useEffect, useState } from "react";
import * as api from "../api";

export interface DrawerRow {
  name: string;
  status: string;
  tone: "ok" | "dim" | "warm" | "error";
  metric: string;
  action?: { label: string; amber?: boolean; disabled?: boolean; onClick: () => void };
}

export interface DrawerSection {
  id: string;
  title: string;
  rows: DrawerRow[];
  count: number | null;
}

const SECTION_ORDER = ["system", "containers", "providers", "adapters", "tools", "automations", "integrations", "activity"];
const SECTION_TITLES: Record<string, string> = {
  system: "System",
  containers: "Containers",
  providers: "Providers",
  adapters: "Adapters",
  tools: "Tools",
  automations: "Automations",
  integrations: "Integrations",
  activity: "Activity",
};

function formatUptime(seconds?: number | null): string {
  if (seconds == null) return "unavailable";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function pctMetric(v?: number | null): string {
  return v == null ? "unavailable" : `${Math.max(0, Math.min(100, Math.round(v)))}%`;
}

// Ported from the previous static dashboard's dashboard.js: same endpoints,
// same grouping and polling cadence, now feeding React state instead of DOM.
export function useDrawerData(open: boolean, notify: (message: string, amber?: boolean) => void) {
  const [sections, setSections] = useState<Record<string, DrawerSection>>({});

  const setSection = useCallback((id: string, rows: DrawerRow[], count: number | null) => {
    setSections((prev) => ({ ...prev, [id]: { id, title: SECTION_TITLES[id], rows, count } }));
  }, []);

  const refreshSystem = useCallback(async () => {
    const [health, status] = await Promise.all([api.get<{ status?: string }>("/health"), api.get<any>("/system/status")]);
    if (!health.ok && !status.ok) {
      setSection("system", [], null);
      return null;
    }
    const s = status.body || {};
    setSection(
      "system",
      [
        { name: "health", tone: health.ok ? "ok" : "error", status: health.ok ? "OK" : "DOWN", metric: health.ok ? health.body?.status || "ok" : "unavailable" },
        { name: "uptime", tone: status.ok ? "ok" : "dim", status: status.ok ? "NOMINAL" : "UNKNOWN", metric: formatUptime(s.uptime_seconds) },
        { name: "tailscale", tone: s.tailscale_connected ? "ok" : "warm", status: s.tailscale_connected ? "CONNECTED" : "UNVERIFIED", metric: s.tailscale_connected ? "connected" : "unverified" },
        { name: "cpu", tone: "dim", status: "LOAD", metric: pctMetric(s.cpu_percent) },
        { name: "memory", tone: "dim", status: "LOAD", metric: pctMetric(s.memory_percent) },
        { name: "vault", tone: "dim", status: "LOAD", metric: pctMetric(s.vault_percent) },
      ],
      null,
    );
    return s;
  }, [setSection]);

  const restartContainer = useCallback(
    async (name: string) => {
      const res = await api.post<any>("/docker/restart", { container: name });
      const ok = res.body?.status === "success";
      const label = ok ? "done" : /confirmation/i.test(res.body?.error || "") ? "requires confirmation (not yet available)" : "blocked";
      notify(`${name}: ${label}`, !ok);
      refreshContainers();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [notify],
  );

  const refreshContainers = useCallback(async () => {
    const res = await api.post<any>("/docker/containers", {});
    if (!res.ok || res.body?.status !== "success") {
      setSection("containers", [], 0);
      return;
    }
    const list: any[] = res.body.output?.containers || [];
    setSection(
      "containers",
      list.map((c) => {
        const running = /running/i.test(c.state || c.status || "");
        return {
          name: c.name,
          tone: running ? "ok" : "dim",
          status: running ? "RUNNING" : "STOPPED",
          metric: c.status || c.state || "",
          action: { label: "restart", amber: true, onClick: () => restartContainer(c.name) },
        };
      }),
      list.length,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSection, restartContainer]);

  const refreshProviders = useCallback(
    (status: any) => {
      if (!status) {
        setSection("providers", [], null);
        return;
      }
      setSection(
        "providers",
        [
          { name: "active planner", tone: "ok", status: "READY", metric: status.planner_mode ?? "unavailable" },
          {
            name: "omniroute (cloud)",
            tone: status.omniroute_configured ? "ok" : "dim",
            status: status.omniroute_configured ? "CONFIGURED" : "STANDBY",
            metric: status.omniroute_configured ? "configured" : "not configured",
          },
          { name: "ollama (local)", tone: "dim", status: "STANDBY", metric: "not monitored" },
        ],
        null,
      );
    },
    [setSection],
  );

  const pollGmail = useCallback(async () => {
    const res = await api.post<any>("/comm/gmail/poll", {});
    if (!res.ok) {
      notify("gmail poll unavailable", true);
      return;
    }
    const count = Array.isArray(res.body) ? res.body.length : 0;
    notify(`gmail: ${count} message${count === 1 ? "" : "s"} processed`);
    refreshSystemGroup();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notify]);

  const refreshAdapters = useCallback(
    (status: any) => {
      if (!status) {
        setSection("adapters", [], null);
        return;
      }
      setSection(
        "adapters",
        [
          {
            name: "gmail",
            tone: status.gmail_configured ? "ok" : "dim",
            status: status.gmail_configured ? "CONNECTED" : "OFFLINE",
            metric: status.gmail_configured ? "configured" : "not configured",
            action: { label: "poll", disabled: !status.gmail_configured, onClick: pollGmail },
          },
        ],
        1,
      );
    },
    [setSection, pollGmail],
  );

  const refreshTools = useCallback(async () => {
    const res = await api.get<any[]>("/tools");
    if (!res.ok || !Array.isArray(res.body)) {
      setSection("tools", [], 0);
      setSection("integrations", [], 0);
      return;
    }
    const tools = res.body;
    const total = tools.length;
    const confirm = tools.filter((t) => t.permissions?.requires_confirmation).length;
    setSection(
      "tools",
      [
        { name: "total", tone: "ok", status: "COUNT", metric: String(total) },
        { name: "allowed by default", tone: "ok", status: "ENABLED", metric: String(total - confirm) },
        { name: "requires confirmation", tone: "warm", status: "GATED", metric: String(confirm) },
      ],
      total,
    );
    const groups = new Map<string, number>();
    for (const t of tools) {
      const ns = t.name.includes(".") ? t.name.split(".")[0] : t.name;
      groups.set(ns, (groups.get(ns) || 0) + 1);
    }
    setSection(
      "integrations",
      [...groups.entries()].sort().map(([ns, count]) => ({
        name: ns,
        tone: "ok" as const,
        status: "SYNCED",
        metric: `${count} tool${count === 1 ? "" : "s"}`,
      })),
      groups.size,
    );
  }, [setSection]);

  const runAutomation = useCallback(
    async (taskId: string, name: string) => {
      const res = await api.post<any>(`/automation/tasks/${taskId}/run`, {});
      if (!res.ok) {
        notify(`${name}: run failed`, true);
        return;
      }
      const decision = res.body?.security_decision;
      const amber = Boolean(decision && decision !== "allow");
      const label =
        decision === "require_confirmation" ? "requires confirmation (not yet available)" : decision === "deny" ? "blocked" : res.body?.outcome;
      notify(`${name}: ${label}`, amber);
      refreshAutomations();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [notify],
  );

  const refreshAutomations = useCallback(async () => {
    const res = await api.get<any[]>("/automation/tasks");
    if (!res.ok || !Array.isArray(res.body)) {
      setSection("automations", [], 0);
      setSection("activity", [], 0);
      return;
    }
    const tasks = res.body;
    setSection(
      "automations",
      tasks.map((t) => ({
        name: t.name,
        tone: t.enabled ? "ok" : "dim",
        status: t.enabled ? "ENABLED" : "DISABLED",
        metric: t.enabled ? "enabled" : "disabled",
        action: { label: "run", onClick: () => runAutomation(t.task_id, t.name) },
      })),
      tasks.length,
    );

    const events: any[] = [];
    for (const t of tasks) for (const h of t.history || []) events.push({ ...h, task_name: t.name });
    events.sort((a, b) => +new Date(b.started_at) - +new Date(a.started_at));
    setSection(
      "activity",
      events.slice(0, 10).map((e) => {
        const decision = e.security_decision || e.outcome;
        const ok = decision === "allow" || e.outcome === "success";
        return {
          name: `${e.task_name} — ${e.tool_name}`,
          tone: ok ? "ok" : decision === "require_confirmation" ? "warm" : "error",
          status: ok ? "OK" : String(decision).toUpperCase(),
          metric: e.outcome,
        };
      }),
      events.length,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSection, runAutomation]);

  const refreshSystemGroup = useCallback(async () => {
    const statusRes = await api.get<any>("/system/status");
    await refreshSystem();
    refreshProviders(statusRes.body);
    refreshAdapters(statusRes.body);
  }, [refreshSystem, refreshProviders, refreshAdapters]);

  useEffect(() => {
    if (!open) return;
    refreshSystemGroup();
    refreshContainers();
    refreshTools();
    refreshAutomations();
    const stops = [
      api.poll(refreshSystemGroup, 10000),
      api.poll(refreshContainers, 20000),
      api.poll(refreshTools, 60000),
      api.poll(refreshAutomations, 15000),
    ];
    return () => stops.forEach((stop) => stop());
  }, [open, refreshSystemGroup, refreshContainers, refreshTools, refreshAutomations]);

  return SECTION_ORDER.map((id) => sections[id]).filter(Boolean) as DrawerSection[];
}
