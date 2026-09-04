// Preset diff: pick two presets, see exactly which parameters differ.
// Pure front-end - works off the preset list main.js already fetches.

const SECTIONS = ["params", "load", "limits", "thermal", "drive", "battery"];

function flatten(obj, prefix = "", out = {}) {
  for (const [key, val] of Object.entries(obj || {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (val !== null && typeof val === "object" && !Array.isArray(val)) {
      flatten(val, path, out);
    } else {
      out[path] = val;
    }
  }
  return out;
}

function rowsFor(a, b) {
  const fa = { motor_type: a.motor_type }, fb = { motor_type: b.motor_type };
  for (const sec of SECTIONS) {
    flatten(a[sec], sec, fa);
    flatten(b[sec], sec, fb);
  }
  const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb)])].sort();
  return keys.map(k => ({ key: k, a: fa[k], b: fb[k], differs: fa[k] !== fb[k] }));
}

const show = v => v === undefined ? "-" : String(v);

export class PresetDiff {
  constructor(button, getPresets) {
    this.getPresets = getPresets;
    this.overlay = null;
    button.addEventListener("click", () => this.open());
  }

  open() {
    const presets = this.getPresets();
    if (presets.length < 2) return;
    this.close();

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.addEventListener("click", ev => { if (ev.target === overlay) this.close(); });
    const modal = document.createElement("div");
    modal.className = "modal";

    const head = document.createElement("div");
    head.className = "modal-head";
    const title = document.createElement("span");
    title.textContent = "Compare presets";
    const close = document.createElement("button");
    close.textContent = "×";
    close.addEventListener("click", () => this.close());
    head.append(title, close);

    const selRow = document.createElement("div");
    selRow.className = "ctl-grid";
    const mkSelect = (idx) => {
      const field = document.createElement("div");
      field.className = "ctl-field";
      const sel = document.createElement("select");
      presets.forEach((p, i) => {
        const opt = document.createElement("option");
        opt.value = i;
        opt.textContent = p.name || p.id;
        sel.appendChild(opt);
      });
      sel.value = idx;
      sel.addEventListener("change", () => this._render());
      field.appendChild(sel);
      selRow.appendChild(field);
      return sel;
    };
    this.selA = mkSelect(0);
    this.selB = mkSelect(1);

    this.table = document.createElement("div");
    this.table.className = "diff-table";

    modal.append(head, selRow, this.table);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    this.overlay = overlay;
    this.presets = presets;
    this._render();
  }

  _render() {
    const a = this.presets[+this.selA.value];
    const b = this.presets[+this.selB.value];
    this.table.innerHTML = "";
    const header = document.createElement("div");
    header.className = "diff-row diff-header";
    for (const txt of ["parameter", a.name || a.id, b.name || b.id]) {
      const cell = document.createElement("span");
      cell.textContent = txt;
      header.appendChild(cell);
    }
    this.table.appendChild(header);
    let differences = 0;
    for (const row of rowsFor(a, b)) {
      const el = document.createElement("div");
      el.className = "diff-row" + (row.differs ? " differs" : "");
      if (row.differs) differences++;
      for (const txt of [row.key, show(row.a), show(row.b)]) {
        const cell = document.createElement("span");
        cell.textContent = txt;
        el.appendChild(cell);
      }
      this.table.appendChild(el);
    }
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = differences
      ? `${differences} parameter${differences > 1 ? "s" : ""} differ.`
      : "These presets are identical.";
    this.table.appendChild(note);
  }

  close() {
    if (this.overlay) { this.overlay.remove(); this.overlay = null; }
  }
}
