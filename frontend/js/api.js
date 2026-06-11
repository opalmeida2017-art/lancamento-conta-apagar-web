import os
import re

export function tenantBasePath() {
  const m = window.location.pathname.match(/^\/t\/([a-z0-9-]+)/i);
  return m ? `/t/${m[1]}` : "";
}

export const BASE = tenantBasePath();

function apiPath(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${BASE}${p}`;
}

export const API = {
  async get(path) {
    const r = await fetch(apiPath(path));
    if (!r.ok) throw new Error(await r.text());
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return r.json();
    return r;
  },
  async send(method, path, body) {
    const r = await fetch(apiPath(path), {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) {
      let msg = await r.text();
      try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  },
  async upload(path, formData) {
    const r = await fetch(apiPath(path), { method: "POST", body: formData });
    if (!r.ok) {
      let msg = await r.text();
      try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  },
  openInNewTab(path) {
    window.open(apiPath(path), "_blank");
  },
  download(path, filename) {
    const a = document.createElement("a");
    a.href = apiPath(path);
    a.download = filename || "";
    a.target = "_blank";
    a.click();
  },
  put(path, body) { return this.send("PUT", path, body); },
  patch(path, body) { return this.send("PATCH", path, body); },
  post(path, body) { return this.send("POST", path, body); },
  delete(path) { return this.send("DELETE", path); },
};
