// Keyboard shortcuts for live driving. Space = start/stop, arrows =
// throttle (or duty in PWM mode), Shift = bigger steps, S = single-step,
// P = pause/resume. Never fires while typing in a form control (the
// Script tab has a textarea).

export function initKeyboard(panels, { onUndo, onRedo } = {}) {
  document.addEventListener("keydown", (ev) => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if ((ev.ctrlKey || ev.metaKey) && ev.code === "KeyZ") {
      ev.preventDefault();
      (ev.shiftKey ? onRedo : onUndo)?.();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.code === "KeyY") {
      ev.preventDefault();
      onRedo?.();
      return;
    }
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const ctl = panels._lastCtl || {};

    switch (ev.code) {
      case "Space":
        ev.preventDefault();
        panels.send({ type: "set_running", on: !ctl.running });
        break;

      case "ArrowLeft":
      case "ArrowRight": {
        ev.preventDefault();
        const dir = ev.code === "ArrowRight" ? 1 : -1;
        if (ctl.pwm?.enabled) {
          const step = ev.shiftKey ? 0.10 : 0.02;
          const duty = Math.min(1, Math.max(0, (ctl.pwm.duty ?? 0.5) + dir * step));
          panels.duty.set(Math.round(duty * 100));
          panels.sendDebounced({ type: "set_pwm", duty }, "pwm_duty");
        } else {
          const max = parseFloat(panels.voltage.input.max) || 24;
          const step = ev.shiftKey ? max / 10 : max / 40;
          const v = Math.min(max, Math.max(0, (ctl.throttle_v ?? 0) + dir * step));
          panels.voltage.set(v);
          panels.sendDebounced({ type: "set_voltage", value: v });
        }
        break;
      }

      case "KeyS":
        panels.send({ type: "time", action: "step", step_s: 0.001 });
        break;

      case "KeyP":
        panels.send({ type: "time", action: ctl.paused ? "play" : "pause" });
        break;
    }
  });
}
