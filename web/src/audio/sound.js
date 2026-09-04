// Motor sound: a live engine whine whose pitch tracks
// RPM plus a load-dependent hum, synthesized with Web Audio. Muted by
// default (browsers require a user gesture to start audio anyway); the
// header button toggles it.
//
// Voices:
//  - whine: sawtooth through a low-pass, pitch ~ rotation frequency × a
//    "blade pass" multiple, loudness rises with speed
//  - hum:   50 to 120 Hz triangle, loudness follows load torque + current
//  - grind: filtered noise burst while stalled/jammed

export class MotorSound {
  constructor(button) {
    this.button = button;
    this.enabled = false;
    this.ctx = null;
    button.addEventListener("click", () => this.toggle());
  }

  toggle() {
    this.enabled = !this.enabled;
    this.button.textContent = this.enabled ? "Sound on" : "Sound off";
    this.button.classList.toggle("on", this.enabled);
    if (this.enabled) this._ensureGraph();
    else if (this.master) this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05);
  }

  _ensureGraph() {
    if (!this.ctx) {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.ctx = ctx;

      this.master = ctx.createGain();
      this.master.gain.value = 0;
      this.master.connect(ctx.destination);

      // whine
      this.whine = ctx.createOscillator();
      this.whine.type = "sawtooth";
      this.whine.frequency.value = 40;
      this.whineFilter = ctx.createBiquadFilter();
      this.whineFilter.type = "lowpass";
      this.whineFilter.frequency.value = 1200;
      this.whineFilter.Q.value = 2.5;
      this.whineGain = ctx.createGain();
      this.whineGain.gain.value = 0;
      this.whine.connect(this.whineFilter).connect(this.whineGain).connect(this.master);
      this.whine.start();

      // hum
      this.hum = ctx.createOscillator();
      this.hum.type = "triangle";
      this.hum.frequency.value = 60;
      this.humGain = ctx.createGain();
      this.humGain.gain.value = 0;
      this.hum.connect(this.humGain).connect(this.master);
      this.hum.start();

      // stall grind: looping noise buffer, normally silent
      const len = ctx.sampleRate;
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
      this.noise = ctx.createBufferSource();
      this.noise.buffer = buf;
      this.noise.loop = true;
      this.noiseFilter = ctx.createBiquadFilter();
      this.noiseFilter.type = "bandpass";
      this.noiseFilter.frequency.value = 140;
      this.noiseFilter.Q.value = 1.2;
      this.noiseGain = ctx.createGain();
      this.noiseGain.gain.value = 0;
      this.noise.connect(this.noiseFilter).connect(this.noiseGain).connect(this.master);
      this.noise.start();
    }
    this.ctx.resume();
    this.master.gain.setTargetAtTime(0.7, this.ctx.currentTime, 0.1);
  }

  // called with every telemetry frame
  update(frame) {
    if (!this.enabled || !this.ctx || this.ctx.state !== "running") return;
    const t = this.ctx.currentTime;
    const ctl = frame.ctl;
    const paused = ctl.paused ? 0 : 1;

    // pitch from rotation frequency; ×8 ≈ slot/commutation passing tone,
    // and slow-motion lowers it with the time scale so it stays honest
    const revHz = Math.abs(frame.rpm) / 60 * ctl.time_scale * paused;
    const pitch = Math.min(2400, 30 + revHz * 8);
    this.whine.frequency.setTargetAtTime(pitch, t, 0.06);
    this.whineFilter.frequency.setTargetAtTime(400 + pitch * 1.6, t, 0.08);
    const speedNorm = Math.min(1, revHz / 120);
    this.whineGain.gain.setTargetAtTime(0.16 * Math.sqrt(speedNorm), t, 0.08);

    // hum follows electrical effort: load torque + current
    const iNorm = Math.min(1, Math.abs(frame.current) / Math.max(1, ctl.limit_a || 10));
    const loadNorm = Math.min(1, Math.abs(frame.load_torque) /
      Math.max(1e-3, Math.abs(frame.torque) + 1e-3));
    const effort = Math.max(iNorm * 0.8, loadNorm * 0.5) * paused;
    this.hum.frequency.setTargetAtTime(50 + iNorm * 70, t, 0.1);
    this.humGain.gain.setTargetAtTime(0.10 * effort, t, 0.12);

    // grind while stalled/jammed with drive applied
    const grinding = frame.flags.stall && Math.abs(frame.voltage) > 0.5 && paused;
    this.noiseGain.gain.setTargetAtTime(grinding ? 0.12 : 0, t, 0.1);
  }
}
