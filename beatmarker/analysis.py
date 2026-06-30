"""卡点检测核心算法。

提供从音频中检测候选卡点的函数,每个卡点带一个 0~1 的强度分,
方便上层按疏密阈值过滤、排序或吸附到节拍网格。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np


@dataclass
class AudioData:
    """加载后的音频及其分析所需的中间量。"""

    y: np.ndarray            # 单声道波形
    sr: int                  # 采样率
    duration: float          # 时长(秒)
    tempo: float             # 估计 BPM
    beat_times: np.ndarray   # 节拍时间(秒)
    onset_times: np.ndarray  # 起音点时间(秒)
    onset_env: np.ndarray    # 起音强度包络
    onset_env_t: np.ndarray  # 包络对应的时间轴
    low_energy: np.ndarray   # 低频能量曲线(检测 bass/kick 冲击)
    low_energy_t: np.ndarray


def load_audio(path: str, target_sr: int | None = 22050) -> AudioData:
    """加载音频并完成一次性的节拍 / 起音 / 低频能量分析。

    target_sr 降采样到 22050 可以显著加速分析,卡点精度足够。
    """
    import librosa

    y, sr = librosa.load(path, sr=target_sr, mono=True)
    duration = float(len(y) / sr)

    # --- 节拍 ---
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, units="frames"
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    onset_env_t = librosa.times_like(onset_env, sr=sr)

    # --- 起音点(回退到能量真正起跳处,卡点更准) ---
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, backtrack=True
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)

    # --- 低频能量(< 200Hz),用于检测鼓点 / bass drop 冲击 ---
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    low = S[freqs < 200].sum(axis=0)
    low_t = librosa.times_like(low, sr=sr)

    return AudioData(
        y=y,
        sr=sr,
        duration=duration,
        tempo=float(np.atleast_1d(tempo)[0]),
        beat_times=beat_times,
        onset_times=onset_times,
        onset_env=onset_env,
        onset_env_t=onset_env_t,
        low_energy=low,
        low_energy_t=low_t,
    )


def _strength_lookup(times: np.ndarray, values: np.ndarray):
    """返回一个把任意时间映射到 values 上最近采样值的函数。"""
    if len(times) == 0:
        return lambda t: 0.0

    def fn(t: float) -> float:
        idx = int(np.searchsorted(times, t))
        idx = min(max(idx, 0), len(times) - 1)
        # 比较相邻两个采样,取更近的
        if idx > 0 and abs(times[idx - 1] - t) < abs(times[idx] - t):
            idx -= 1
        return float(values[idx])

    return fn


def detect_points(
    audio: AudioData,
    *,
    use_beats: bool = True,
    use_onsets: bool = True,
    use_low_energy: bool = True,
    density: float = 0.5,
    min_gap: float = 0.08,
) -> List[tuple]:
    """综合多种检测器,返回 [(time, strength)] 列表(按时间升序)。

    density: 0~1,越大保留越多卡点。
    min_gap: 两个卡点最小间隔(秒),避免过密。
    """
    strength_fn = _strength_lookup(audio.onset_env_t, audio.onset_env)

    candidates: dict[float, float] = {}

    def add(t: float, base: float):
        t = round(float(t), 3)
        if t < 0 or t > audio.duration:
            return
        candidates[t] = max(candidates.get(t, 0.0), base)

    onset_max = float(audio.onset_env.max()) or 1.0

    if use_beats:
        for t in audio.beat_times:
            # 节拍点给一个稳定的中等强度 + 实际起音能量
            add(t, 0.5 + 0.5 * strength_fn(t) / onset_max)

    if use_onsets:
        for t in audio.onset_times:
            add(t, strength_fn(t) / onset_max)

    if use_low_energy:
        # 低频能量一阶差分的峰值 = 突然变重的瞬间(强卡点)
        diff = np.diff(audio.low_energy, prepend=audio.low_energy[:1])
        diff = np.clip(diff, 0, None)
        peak_thresh = diff.mean() + diff.std()
        for t, d in zip(audio.low_energy_t, diff):
            if d > peak_thresh:
                add(t, 1.0)  # 低频冲击直接给满分,优先保留

    if not candidates:
        return []

    points = sorted(candidates.items())  # [(time, strength)]

    # 按强度阈值过滤(density 越大阈值越低)
    strengths = np.array([s for _, s in points])
    # density=1 -> 阈值0(全保留);density=0 -> 阈值取较高分位
    thresh = np.quantile(strengths, 1.0 - density) if len(strengths) > 1 else 0.0
    filtered = [(t, s) for t, s in points if s >= thresh]

    # 强制最小间隔:相邻太近时保留强度更高者
    return _enforce_min_gap(filtered, min_gap)


def _enforce_min_gap(points: Sequence[tuple], min_gap: float) -> List[tuple]:
    result: List[tuple] = []
    for t, s in points:
        if result and t - result[-1][0] < min_gap:
            # 与上一个太近,保留更强的那个
            if s > result[-1][1]:
                result[-1] = (t, s)
        else:
            result.append((t, s))
    return result


def snap_to_beats(
    times: Sequence[float], beat_times: np.ndarray, tolerance: float = 0.12
) -> List[float]:
    """把卡点吸附到最近的节拍上(在容差范围内),让卡点更整齐。"""
    if len(beat_times) == 0:
        return list(times)
    beats = np.asarray(beat_times)
    out = []
    for t in times:
        idx = int(np.argmin(np.abs(beats - t)))
        out.append(float(beats[idx]) if abs(beats[idx] - t) <= tolerance else float(t))
    # 去重并排序
    return sorted(set(round(x, 3) for x in out))
