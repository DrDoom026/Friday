// Thin fetch layer. No business logic, no security decisions - it only
// carries the API key header and turns a failed call into `{ok:false}`
// instead of a thrown exception, so one bad endpoint never blanks the rest
// of the dashboard. TS port of the previous static dashboard's api.js.

const KEY_STORAGE = "friday.apiKey";
let unauthorizedHandler: (() => void) | null = null;

export function getApiKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key: string) {
  try {
    localStorage.setItem(KEY_STORAGE, key);
  } catch {
    // ignore
  }
}

// Called whenever a request actually comes back 401 - the only honest
// signal that this backend has FIRDAY_API_KEYS configured.
export function onUnauthorized(fn: () => void) {
  unauthorizedHandler = fn;
}

export interface ApiResult<T = any> {
  ok: boolean;
  status: number;
  body: T | null;
  networkError?: boolean;
}

async function call<T = any>(path: string, options: RequestInit = {}): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;

  try {
    const res = await fetch(path, { ...options, headers });
    let body: T | null = null;
    try {
      body = await res.json();
    } catch {
      // no body
    }
    if (res.status === 401 && unauthorizedHandler) unauthorizedHandler();
    return { ok: res.ok, status: res.status, body };
  } catch {
    return { ok: false, status: 0, body: null, networkError: true };
  }
}

export const get = <T = any>(path: string) => call<T>(path, { method: "GET" });
export const post = <T = any>(path: string, payload?: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(payload || {}) });

// Bounded, centralized polling: each caller owns its own interval and
// failure never touches another caller's state.
export function poll(fn: () => Promise<void> | void, intervalMs: number) {
  let stopped = false;
  async function tick() {
    if (stopped) return;
    try {
      await fn();
    } catch {
      // ignore
    }
    if (!stopped) window.setTimeout(tick, intervalMs);
  }
  tick();
  return () => {
    stopped = true;
  };
}
