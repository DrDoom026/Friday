/* PART 11: thin fetch layer. No business logic, no security decisions - it
 * only carries the API key header and turns a failed section into
 * `{ok:false}` instead of an uncaught exception, so one bad endpoint never
 * blanks the rest of the dashboard. */

(function () {
  "use strict";

  const KEY_STORAGE = "friday.apiKey";
  let unauthorizedHandler = null;

  function getApiKey() {
    try { return localStorage.getItem(KEY_STORAGE) || ""; }
    catch { return ""; }
  }

  function setApiKey(key) {
    try { localStorage.setItem(KEY_STORAGE, key); } catch {}
  }

  /** Called whenever a request actually comes back 401 - the only honest
   * signal that this backend has FIRDAY_API_KEYS configured. Nothing here
   * assumes a key is required; it only reacts to what the backend said. */
  function onUnauthorized(fn) { unauthorizedHandler = fn; }

  async function call(path, options = {}) {
    const headers = Object.assign(
      { "Content-Type": "application/json" },
      options.headers || {}
    );
    const key = getApiKey();
    if (key) headers["X-API-Key"] = key;

    try {
      const res = await fetch(path, Object.assign({}, options, { headers }));
      let body = null;
      try { body = await res.json(); } catch {}
      if (res.status === 401 && unauthorizedHandler) unauthorizedHandler();
      return { ok: res.ok, status: res.status, body };
    } catch (err) {
      return { ok: false, status: 0, body: null, networkError: true };
    }
  }

  function get(path) { return call(path, { method: "GET" }); }
  function post(path, payload) {
    return call(path, { method: "POST", body: JSON.stringify(payload || {}) });
  }

  /** Bounded, centralized polling: each section owns its own interval and
   * failure never touches another section's state. */
  function poll(fn, intervalMs) {
    let stopped = false;
    async function tick() {
      if (stopped) return;
      try { await fn(); } catch {}
      if (!stopped) setTimeout(tick, intervalMs);
    }
    tick();
    return () => { stopped = true; };
  }

  window.FridayApi = { getApiKey, setApiKey, get, post, poll, onUnauthorized };
})();
