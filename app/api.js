// SPDX-License-Identifier: AGPL-3.0-or-later
// The one way the page talks to the server: JSON in and out, a timeout on every request, and for unsafe methods the
// per-run token the server put in the page (X-Viewer-Token) with a JSON content type. When the server was restarted the
// token in the page is stale: a 403 makes the page read the new one from `/` and send the request once more.

export const TIMEOUT_MS = 8000;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const meta = () => document.querySelector('meta[name="viewer-token"]');
const token = () => meta()?.content || "";
const unsafe = (method) => !/^(GET|HEAD|OPTIONS)$/i.test(method);
const TOKEN_META = /<meta\s+name="viewer-token"\s+content="([^"]*)"/i;

// Headers for a request of this method: the token and content type on unsafe ones.
export function headers(method, extra) {
  return unsafe(method) ? { "Content-Type": "application/json", "X-Viewer-Token": token(), ...extra } : { ...extra };
}

// fetch with a timeout; `signal` aborts too.
export async function timedFetch(path, init = {}, timeout = TIMEOUT_MS) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new Error("timeout")), timeout);
  init.signal?.addEventListener("abort", () => ctl.abort(init.signal.reason));
  try {
    return await fetch(path, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

// The token of the running server, read from its page; true when it differs from the one in this page (and is now used).
async function refreshToken() {
  try {
    const r = await timedFetch("/", { cache: "no-store" });
    const fresh = TOKEN_META.exec(await r.text())?.[1];
    if (!fresh || fresh === token()) return false;
    const m = meta();
    if (m) m.content = fresh;
    return !!m;
  } catch { return false; }
}

// timedFetch with the headers of `method`; an unsafe request answered 403 is sent once more under a fresh token.
export async function request(path, { method = "GET", headers: extra, timeout = TIMEOUT_MS, ...init } = {}) {
  const send = () => timedFetch(path, { ...init, method, headers: headers(method, extra) }, timeout);
  let r = await send();
  if (r.status === 403 && unsafe(method) && (await refreshToken())) r = await send();
  return r;
}

// -> { status, data, etag, notModified }. A non-2xx answer throws an ApiError carrying the server's { error } text.
export async function api(path, { method = "GET", body, etag, timeout = TIMEOUT_MS, keepalive = false, signal } = {}) {
  const r = await request(path, {
    method,
    headers: etag ? { "If-None-Match": etag } : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
    keepalive,
    signal,
    timeout,
  });
  if (r.status === 304) return { status: 304, notModified: true, etag, data: null };
  let data = null;
  try { data = await r.json(); } catch { /* an empty or non-JSON body */ }
  if (!r.ok) throw new ApiError((data && data.error) || `${r.status} ${r.statusText}`, r.status);
  return { status: r.status, notModified: false, etag: r.headers.get("ETag") || undefined, data };
}
