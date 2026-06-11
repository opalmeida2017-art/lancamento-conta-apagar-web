import { BASE } from "./api.js";

let ws = null;

export function connectWebSocket(onMessage) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}${BASE}/ws`);
  ws.onmessage = (ev) => {
    try { onMessage(JSON.parse(ev.data)); } catch (_) {}
  };
  ws.onclose = () => setTimeout(() => connectWebSocket(onMessage), 3000);
}

export function appendLogLine(text, nivel = "INFO") {
  const panel = document.getElementById("log-panel");
  if (!panel) return;
  const div = document.createElement("div");
  div.className = "log-line" + (nivel === "ERRO" ? " erro" : nivel === "WARN" ? " warn" : "");
  div.textContent = text;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
  while (panel.children.length > 300) panel.removeChild(panel.firstChild);
}
