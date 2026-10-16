# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Measure a music track: tempo, first beat, first downbeat and loudness per second.

Depends only on numpy and on ffmpeg in the PATH (no librosa, on purpose: it is a
heavy dependency for five steps of signal processing).

  1. Decode to mono 22 050 Hz with ffmpeg.
  2. Spectral flux (onset envelope): how much NEW energy appears at each instant.
     That is what we hear as a hit.
  3. Autocorrelation of the flux gives the beat period.
  4. Metrical disambiguation: a quarter, a half and a dotted quarter explain the
     same hits, so the candidates are scored by hit strength times a log-normal
     prior centred on 120 BPM (standard practice in tempo tracking).
  5. Phase: the offset that lands the grid on the strongest hits.
  6. Bar phase: of every N beats, the one with most energy is the downbeat.
"""

import subprocess

import numpy as np

SR = 22050
N_FFT = 1024
HOP = 256
FRAME_RATE = SR / HOP
# The flux of analysis frame i peaks before the hit reaches the centre of its window.
# Measured on synthetic clicks at known times (see tests/test_beatgrid.py).
ONSET_LAG = 0.0327
# silence put before the track, so a hit on its very first sample still has a 'before'
PAD_S = 0.1


def decode(path):
    """Any audio (or the first audio stream of a video) -> float32 mono at SR."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
           "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def onset_envelope(y):
    """Log-compressed spectral flux with its local mean removed, normalised to 0-1."""
    win = np.hanning(N_FFT).astype(np.float32)
    n = 1 + (len(y) - N_FFT) // HOP
    if n < 2:
        raise ValueError("audio too short to measure")
    spec = np.empty((n, N_FFT // 2 + 1), dtype=np.float32)
    for a in range(0, n, 4096):                           # in blocks: a long track stays light on memory
        b = min(n, a + 4096)
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(a, b)[:, None]
        spec[a:b] = np.abs(np.fft.rfft(y[idx] * win, axis=1))
    spec = np.log1p(spec * 10.0)
    flux = np.concatenate([[0.0], np.maximum(np.diff(spec, axis=0), 0).sum(axis=1)])
    k = max(1, int(0.4 * FRAME_RATE))                     # ~0.4 s moving mean
    env = np.maximum(flux - np.convolve(flux, np.ones(k) / k, mode="same"), 0)
    return env / env.max() if env.max() > 0 else env


def estimate_period(env, bpm_min=70.0, bpm_max=170.0):
    """Autocorrelation peak inside the tempo window, refined with a parabola."""
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    ac[0] = 0.0
    lo, hi = int(FRAME_RATE * 60.0 / bpm_max), int(FRAME_RATE * 60.0 / bpm_min)
    lag = float(lo + int(np.argmax(ac[lo:hi])))
    i = int(lag)
    if 0 < i < len(ac) - 1:
        a, b, c = ac[i - 1], ac[i], ac[i + 1]
        if a - 2 * b + c:
            lag = i + 0.5 * (a - c) / (a - 2 * b + c)
    return lag / FRAME_RATE


def _grid_strength(env, period, offset):
    n = int((len(env) / FRAME_RATE - offset) / period)
    idx = ((offset + np.arange(max(n, 1)) * period) * FRAME_RATE).astype(int)
    return env[idx[idx < len(env)]]


def disambiguate(env, period):
    """Pick among the metrical multiples of the autocorrelation period."""
    best, best_score = period, -1.0
    for p in sorted({period * r for r in (1 / 3, 1 / 2, 2 / 3, 1.0, 3 / 2, 2.0, 3.0)}):
        bpm = 60.0 / p
        if not 60.0 <= bpm <= 200.0 or len(env) / FRAME_RATE / p < 8:
            continue
        strength = max(float(_grid_strength(env, p, o / FRAME_RATE).mean())
                       for o in range(int(round(p * FRAME_RATE))))
        score = strength * np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        if score > best_score:
            best, best_score = p, score
    return best


def estimate_phase(env, period):
    """Offset (s) of the first beat: the shift whose grid sums the most onset energy."""
    scores = [float(_grid_strength(env, period, o / FRAME_RATE).sum())
              for o in range(int(round(period * FRAME_RATE)))]
    return int(np.argmax(scores)) / FRAME_RATE


def estimate_bar_phase(env, period, offset, beats_per_bar=4):
    """Which beat of every `beats_per_bar` carries the most energy: that is the 'one'.
    `offset` is a hit time, so the envelope is read ONSET_LAG earlier, as the peak
    in a small window (a grid point rarely falls on the exact analysis frame)."""
    half = max(1, int(period * FRAME_RATE / 8))
    scores = [0.0] * beats_per_bar
    b = 0
    while True:
        c = int(round((offset + b * period - ONSET_LAG) * FRAME_RATE))
        if c + half >= len(env):
            break
        scores[b % beats_per_bar] += float(env[max(0, c - half):c + half + 1].max())
        b += 1
    return int(np.argmax(scores)), scores


def rms_profile(y, step=1.0):
    """RMS level per `step` seconds, in dBFS. Useful to find sections and drops."""
    n = int(step * SR)
    return [round(20 * float(np.log10(np.sqrt(np.mean(y[i:i + n].astype(np.float64) ** 2)) + 1e-9)), 2)
            for i in range(0, len(y) - n + 1, n)]


def refine(env, period, offset, threshold=0.25):
    """Sharpen the grid: find the onset peak near every predicted beat (sub-frame, by a
    parabola), then fit time = offset + k * period by least squares over all of them.
    The autocorrelation alone is quantised to the hop (11.6 ms), which over a minute of
    music drifts by a frame or more. Also moves the first beat past any silent lead-in."""
    first_onset = np.flatnonzero(env > threshold)
    if len(first_onset) == 0:
        return period, offset
    first_onset = first_onset[0] / FRAME_RATE
    while offset + period * 0.5 < first_onset:
        offset += period
    half = max(1, int(period * FRAME_RATE / 4))
    ks, ts = [], []
    k = 0
    while True:
        c = int(round((offset + k * period) * FRAME_RATE))
        if c + half >= len(env) - 1:
            break
        lo = max(1, c - half)
        i = lo + int(np.argmax(env[lo:c + half + 1]))
        if env[i] > threshold:
            a, b, cc = env[i - 1], env[i], env[i + 1]
            d = a - 2 * b + cc
            ks.append(k)
            ts.append((i + (0.5 * (a - cc) / d if d else 0.0)) / FRAME_RATE + ONSET_LAG)
        k += 1
    if len(ks) < 4:
        return period, offset
    slope, icpt = np.polyfit(np.array(ks, float), np.array(ts), 1)
    return float(slope), float(icpt)


def analyse(path, beats_per_bar=4, fps=25.0):
    y = decode(path)
    n = len(y)
    env = onset_envelope(np.concatenate([np.zeros(int(PAD_S * SR), np.float32), y]))
    raw = estimate_period(env)
    period = disambiguate(env, raw)
    period, offset = refine(env, period, estimate_phase(env, period))
    k, scores = estimate_bar_phase(env, period, offset, beats_per_bar)
    offset -= int(PAD_S * SR) / SR
    return {
        "audio": path,
        "duration": round(n / SR, 3),
        "bpm": round(60.0 / period, 3),
        "bpm_autocorrelation_raw": round(60.0 / raw, 3),
        "beat_seconds": round(period, 5),
        "beat_frames": round(period * fps, 3),
        "beats_per_bar": beats_per_bar,
        "first_beat": round(offset, 4),
        "first_downbeat": round(offset + k * period, 4),
        "bar_seconds": round(period * beats_per_bar, 5),
        "bar_phase_scores": [round(s, 2) for s in scores],
        "rms_db_per_second": rms_profile(y),
    }
