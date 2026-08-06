// WebSocket client: commands up, telemetry down, auto-reconnect.
// Slider moves are debounced per command type so dragging doesn't flood
// the server (docs/PROTOCOL.md).

export class SimSocket {
  constructor() {
    this.handlers = { telemetry: [], hello: [], event: [], status: [] };
    this.room = null;            // multi-tenant room (null/"main" = default)
    this._debounceTimers = new Map();
    this._ws = null;
    this._retryMs = 500;
    this._closedByUs = false;
  }

  on(kind, fn) { (this.handlers[kind] ||= []).push(fn); return this; }
  _fire(kind, payload) { for (const fn of this.handlers[kind] || []) fn(payload); }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const qs = this.room && this.room !== "main"
      ? `?room=${encodeURIComponent(this.room)}` : "";
    const ws = new WebSocket(`${proto}://${location.host}/ws${qs}`);
    this._ws = ws;
    ws.onopen = () => {
      this._retryMs = 500;
      this._fire("status", { connected: true });
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "telemetry") this._fire("telemetry", msg);
      else if (msg.type === "hello") this._fire("hello", msg);
      else if (msg.type === "event") this._fire("event", msg);
    };
    ws.onclose = () => {
      this._fire("status", { connected: false });
      if (!this._closedByUs) {
        setTimeout(() => this.connect(), this._retryMs);
        this._retryMs = Math.min(this._retryMs * 1.7, 8000);
      }
    };
    ws.onerror = () => ws.close();
  }

  send(msg) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  // Debounced send keyed by message type (+ an optional discriminator), so
  // e.g. dragging the throttle only ships the latest value every 60 ms.
  sendDebounced(msg, key = null, delay = 60) {
    const k = key || msg.type;
    clearTimeout(this._debounceTimers.get(k));
    this._debounceTimers.set(k, setTimeout(() => {
      this._debounceTimers.delete(k);
      this.send(msg);
    }, delay));
  }
}
