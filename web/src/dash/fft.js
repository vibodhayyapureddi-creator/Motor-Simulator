// Compact radix-2 FFT (no dependencies, plan phase 8).
//
// Operates on the 60 Hz downsampled telemetry buffers, so in real time it
// resolves ripple up to ~30 Hz (once-per-rev asymmetries, slow commutation
// beats). Because samples are stamped in SIM time, slow motion raises the
// effective sample rate: at 0.02x the same buffers resolve up to ~1.5 kHz
// of sim-frequency content - enough to see PWM switching. (True full-rate
// capture would need a server-side sub-step buffer; deliberately out of
// scope for this pass.)

// In-place iterative Cooley-Tukey on interleaved re/im arrays.
export function fftMag(samples) {
  const n = samples.length;             // must be a power of two
  const re = Float64Array.from(samples);
  const im = new Float64Array(n);

  // bit-reversal permutation
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  // butterflies
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = i + k + len / 2;
        const tr = re[b] * cr - im[b] * ci;
        const ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr;        im[a] += ti;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
  // one-sided magnitude spectrum, normalized
  const half = n >> 1;
  const mags = new Array(half);
  for (let i = 0; i < half; i++)
    mags[i] = 2 * Math.hypot(re[i], im[i]) / n;
  return mags;
}

// Detrend + Hann window + FFT over the newest power-of-two chunk of a
// time series. Returns { freqs, mags } or null if there isn't enough data.
export function spectrum(ts, values, maxN = 512) {
  let n = 1;
  while (n * 2 <= Math.min(values.length, maxN)) n *= 2;
  if (n < 64) return null;
  const t = ts.slice(-n);
  const v = values.slice(-n);
  const dur = t[n - 1] - t[0];
  if (!(dur > 0)) return null;
  const fs = (n - 1) / dur;             // sample rate in SIM Hz

  const mean = v.reduce((a, x) => a + (x ?? 0), 0) / n;
  const windowed = new Array(n);
  for (let i = 0; i < n; i++) {
    const hann = 0.5 * (1 - Math.cos(2 * Math.PI * i / (n - 1)));
    windowed[i] = ((v[i] ?? mean) - mean) * hann;
  }
  const mags = fftMag(windowed);
  const freqs = mags.map((_, i) => i * fs / n);
  return { freqs, mags, fs };
}
