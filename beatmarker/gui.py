"""卡点标记工具的 Tkinter 图形界面。

工作流:打开音频 → 自动检测卡点(可调疏密 / 检测器)→ 波形上查看与手动增删
→ 导出 Premiere Pro 标记 CSV。

交互:
  - 单击波形       : 移动播放头(并可从该处播放)
  - 双击波形       : 在该处增/删卡点(就近切换)
  - 空格           : 播放 / 停止
  - a 键           : 在播放头处加卡点
  - Delete / d 键  : 删除离播放头最近的卡点
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from . import analysis, export  # noqa: E402


def _setup_cjk_font():
    """让 matplotlib 画布里的中文正常显示(否则是方块)。"""
    from matplotlib import font_manager, rcParams

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "SimSun", "Microsoft JhengHei",
                 "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
        if name in available:
            rcParams["font.sans-serif"] = [name]
            break
    rcParams["axes.unicode_minus"] = False  # 负号正常显示


_setup_cjk_font()

FPS_CHOICES = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]
SNAP_DELETE_TOL = 0.15  # 双击/删除时认为"命中"卡点的时间容差(秒)


class BeatMarkerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("卡点标记工具 — Premiere 导出")
        self.root.geometry("1180x680")

        self.audio: analysis.AudioData | None = None
        self.audio_path: str | None = None
        self.markers: list[float] = []          # 卡点时间(秒)
        self.playhead: float = 0.0

        # 播放状态
        self._playing = False
        self._play_wall_start = 0.0
        self._play_audio_start = 0.0

        self._build_widgets()
        self._bind_keys()
        self._tick()  # 启动播放头刷新循环

    # ------------------------------------------------------------------ UI
    def _build_widgets(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="打开音频…", command=self.open_audio).pack(side=tk.LEFT)
        ttk.Button(top, text="重新检测", command=self.run_detect).pack(side=tk.LEFT, padx=4)

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.play_btn = ttk.Button(top, text="▶ 播放", command=self.toggle_play)
        self.play_btn.pack(side=tk.LEFT)
        ttk.Button(top, text="＋卡点(a)", command=self.add_marker_at_playhead).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="－卡点(d)", command=self.delete_marker_near_playhead).pack(side=tk.LEFT)
        ttk.Button(top, text="清空卡点", command=self.clear_markers).pack(side=tk.LEFT, padx=4)

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(top, text="帧率 fps:").pack(side=tk.LEFT)
        self.fps_var = tk.StringVar(value="30")
        ttk.Combobox(top, textvariable=self.fps_var, values=FPS_CHOICES, width=7,
                     state="readonly").pack(side=tk.LEFT, padx=(2, 8))
        ttk.Button(top, text="导出带标记 WAV…", command=self.export_wav).pack(side=tk.LEFT)
        ttk.Button(top, text="导出 FCPXML…", command=self.export_fcpxml).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="其他(CSV/TXT/JSON)…", command=self.export_other).pack(side=tk.LEFT)

        # --- 检测参数行 ---
        opt = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        opt.pack(side=tk.TOP, fill=tk.X)

        self.use_beats = tk.BooleanVar(value=True)
        self.use_onsets = tk.BooleanVar(value=True)
        self.use_low = tk.BooleanVar(value=True)
        self.snap = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="节拍", variable=self.use_beats, command=self.run_detect).pack(side=tk.LEFT)
        ttk.Checkbutton(opt, text="起音", variable=self.use_onsets, command=self.run_detect).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(opt, text="低频冲击", variable=self.use_low, command=self.run_detect).pack(side=tk.LEFT)
        ttk.Checkbutton(opt, text="吸附到节拍", variable=self.snap, command=self.run_detect).pack(side=tk.LEFT, padx=8)

        ttk.Label(opt, text="疏密:").pack(side=tk.LEFT, padx=(8, 2))
        self.density = tk.DoubleVar(value=0.5)
        scale = ttk.Scale(opt, from_=0.05, to=1.0, variable=self.density,
                          command=lambda _e: self._density_changed(), length=180)
        scale.pack(side=tk.LEFT)

        self.status = tk.StringVar(value="请打开一个音频文件。")
        ttk.Label(opt, textvariable=self.status, foreground="#0a6").pack(side=tk.RIGHT)

        # --- 波形画布 ---
        self.fig = Figure(figsize=(11, 4.2), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#111418")
        self.fig.patch.set_facecolor("#1b1f24")
        self.ax.tick_params(colors="#aaa")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_click)

        self._marker_artists: list = []
        self._playhead_artist = None
        self._draw_waveform()

    def _bind_keys(self):
        self.root.bind("<space>", lambda e: self.toggle_play())
        self.root.bind("a", lambda e: self.add_marker_at_playhead())
        self.root.bind("d", lambda e: self.delete_marker_near_playhead())
        self.root.bind("<Delete>", lambda e: self.delete_marker_near_playhead())

    # ------------------------------------------------------------- 文件 / 检测
    def open_audio(self):
        path = filedialog.askopenfilename(
            title="选择音频",
            filetypes=[("音频文件", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.status.set("正在分析音频…")
        self.root.update_idletasks()
        try:
            self.audio = analysis.load_audio(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("加载失败", f"无法分析该音频:\n{e}")
            self.status.set("加载失败。")
            return
        self.audio_path = path
        self.stop_play()
        self.playhead = 0.0
        self._draw_waveform()
        self.run_detect()
        self.status.set(
            f"{os.path.basename(path)} | {self.audio.duration:.1f}s | "
            f"BPM≈{self.audio.tempo:.1f}"
        )

    def run_detect(self):
        if not self.audio:
            return
        pts = analysis.detect_points(
            self.audio,
            use_beats=self.use_beats.get(),
            use_onsets=self.use_onsets.get(),
            use_low_energy=self.use_low.get(),
            density=float(self.density.get()),
        )
        times = [t for t, _s in pts]
        if self.snap.get():
            times = analysis.snap_to_beats(times, self.audio.beat_times)
        self.markers = sorted(times)
        self._redraw_markers()
        self.status.set(f"检测到 {len(self.markers)} 个卡点 | BPM≈{self.audio.tempo:.1f}")

    def _density_changed(self):
        # 拖动滑块时实时重算
        self.run_detect()

    # ------------------------------------------------------------- 标记编辑
    def add_marker(self, t: float):
        t = round(max(0.0, min(t, self.audio.duration if self.audio else t)), 3)
        if all(abs(t - m) > 1e-3 for m in self.markers):
            self.markers.append(t)
            self.markers.sort()
            self._redraw_markers()

    def add_marker_at_playhead(self):
        if self.audio:
            self.add_marker(self.playhead)

    def delete_marker_near(self, t: float, tol: float = SNAP_DELETE_TOL) -> bool:
        if not self.markers:
            return False
        idx = min(range(len(self.markers)), key=lambda i: abs(self.markers[i] - t))
        if abs(self.markers[idx] - t) <= tol:
            del self.markers[idx]
            self._redraw_markers()
            return True
        return False

    def delete_marker_near_playhead(self):
        self.delete_marker_near(self.playhead)

    def clear_markers(self):
        self.markers = []
        self._redraw_markers()

    # ------------------------------------------------------------- 绘图
    def _draw_waveform(self):
        self.ax.clear()
        self.ax.set_facecolor("#111418")
        self._marker_artists = []
        self._playhead_artist = None
        if not self.audio:
            self.ax.text(0.5, 0.5, "打开音频文件以开始", color="#777",
                         ha="center", va="center", transform=self.ax.transAxes)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.canvas.draw_idle()
            return

        y, sr = self.audio.y, self.audio.sr
        n_pix = 4000
        if len(y) > n_pix:
            step = len(y) // n_pix
            trimmed = y[: step * n_pix].reshape(n_pix, step)
            env = np.abs(trimmed).max(axis=1)
            t = np.linspace(0, self.audio.duration, n_pix)
        else:
            env = np.abs(y)
            t = np.linspace(0, self.audio.duration, len(y))
        self.ax.fill_between(t, -env, env, color="#3da9fc", linewidth=0)
        self.ax.set_xlim(0, self.audio.duration)
        self.ax.set_ylim(-1.05, 1.05)
        self.ax.set_yticks([])
        self.ax.set_xlabel("时间 (秒)", color="#aaa")
        self.ax.tick_params(colors="#aaa")
        self._redraw_markers()

    def _redraw_markers(self):
        for art in self._marker_artists:
            art.remove()
        self._marker_artists = []
        if self.audio:
            for m in self.markers:
                line = self.ax.axvline(m, color="#ff5277", linewidth=1.2, alpha=0.9)
                self._marker_artists.append(line)
        self._draw_playhead()

    def _draw_playhead(self):
        if self._playhead_artist is not None:
            try:
                self._playhead_artist.remove()
            except ValueError:
                pass
        if self.audio:
            self._playhead_artist = self.ax.axvline(
                self.playhead, color="#ffd23f", linewidth=1.6
            )
        self.canvas.draw_idle()

    def _on_click(self, event):
        if not self.audio or event.inaxes != self.ax or event.xdata is None:
            return
        t = float(event.xdata)
        if event.dblclick:
            # 双击:就近删除,否则新增
            if not self.delete_marker_near(t):
                self.add_marker(t)
        else:
            # 单击:移动播放头(若在播放则从该处续播)
            self.playhead = max(0.0, min(t, self.audio.duration))
            if self._playing:
                self.stop_play()
                self.start_play()
            else:
                self._draw_playhead()

    # ------------------------------------------------------------- 播放
    def toggle_play(self):
        if self._playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if not self.audio:
            return
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            messagebox.showwarning("无法播放", f"sounddevice 不可用:\n{e}")
            return
        start = self.playhead if self.playhead < self.audio.duration - 0.05 else 0.0
        self.playhead = start
        start_frame = int(start * self.audio.sr)
        sd.stop()
        sd.play(self.audio.y[start_frame:], self.audio.sr)
        self._playing = True
        self._play_wall_start = time.monotonic()
        self._play_audio_start = start
        self.play_btn.config(text="■ 停止")

    def stop_play(self):
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:  # noqa: BLE001
            pass
        self._playing = False
        self.play_btn.config(text="▶ 播放")

    def _tick(self):
        # 约 30fps 刷新播放头
        if self._playing and self.audio:
            elapsed = time.monotonic() - self._play_wall_start
            self.playhead = self._play_audio_start + elapsed
            if self.playhead >= self.audio.duration:
                self.playhead = self.audio.duration
                self.stop_play()
            self._draw_playhead()
        self.root.after(33, self._tick)

    # ------------------------------------------------------------- 导出
    def _fps(self) -> float:
        return float(self.fps_var.get())

    def export_wav(self):
        """方式一(推荐):把卡点作为 cue 标记嵌入 WAV,Premiere 自动识别。"""
        if not self._check_markers() or not self.audio_path:
            return
        path = filedialog.asksaveasfilename(
            title="导出带标记的 WAV", defaultextension=".wav",
            initialfile=self._default_name(".wav"), filetypes=[("WAV 音频", "*.wav")],
        )
        if not path:
            return
        try:
            n = export.export_wav_with_cues(self.audio_path, path, self.markers)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("导出失败", str(e))
            return
        messagebox.showinfo(
            "导出成功",
            f"已写入 {n} 个标记(Adobe XMP + 通用 cue)到 WAV:\n{path}\n\n"
            f"用法:把这个 WAV 导入 Premiere。\n"
            f"在『项目』面板双击该音频在『源监视器』打开,或打开『标记』面板,"
            f"即可看到这些卡点标记。\n\n"
            f"若仍看不到,请改用『导出 FCPXML…』(文件→导入)。",
        )

    def export_fcpxml(self):
        """方式二:导出 FCPXML 序列,文件→导入 即得带时间线标记的序列。"""
        if not self._check_markers():
            return
        path = filedialog.asksaveasfilename(
            title="导出 FCPXML", defaultextension=".xml",
            initialfile=self._default_name(".xml"), filetypes=[("FCPXML", "*.xml")],
        )
        if not path:
            return
        n = export.export_fcpxml(
            self.markers, path, fps=self._fps(),
            duration=self.audio.duration if self.audio else None,
        )
        messagebox.showinfo(
            "导出成功",
            f"已写入 {n} 个标记到 FCPXML:\n{path}\n\n"
            f"用法:Premiere → 文件 → 导入 → 选择该 .xml,会生成一条带标记的序列。\n"
            f"序列帧率请设为 {self._fps()}。",
        )

    def export_other(self):
        if not self._check_markers():
            return
        path = filedialog.asksaveasfilename(
            title="导出 CSV / TXT / JSON", defaultextension=".csv",
            initialfile=self._default_name(".csv"),
            filetypes=[("Premiere 标记 CSV", "*.csv"), ("时间码 TXT", "*.txt"), ("JSON", "*.json")],
        )
        if not path:
            return
        fps = self._fps()
        if path.lower().endswith(".csv"):
            n = export.export_premiere_csv(self.markers, path, fps=fps,
                                           drop_frame=self.fps_var.get() in ("29.97", "59.94"))
        elif path.lower().endswith(".json"):
            n = export.export_json(self.markers, path, fps=fps,
                                   tempo=self.audio.tempo if self.audio else None)
        else:
            n = export.export_timecode_txt(self.markers, path, fps=fps)
        messagebox.showinfo("导出成功", f"已写入 {n} 个标记到:\n{path}")

    def _check_markers(self) -> bool:
        if not self.markers:
            messagebox.showwarning("没有卡点", "当前没有任何卡点可导出。")
            return False
        return True

    def _default_name(self, ext: str) -> str:
        base = os.path.splitext(os.path.basename(self.audio_path))[0] if self.audio_path else "markers"
        return f"{base}_卡点{ext}"


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    BeatMarkerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
