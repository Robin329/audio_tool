"""卡点标记工具入口。

  python main.py            启动图形界面
  python main.py song.mp3   命令行直接检测并导出 Premiere CSV(无界面)
"""
from __future__ import annotations

import argparse
import sys


_EXT = {"wav": ".wav", "fcpxml": ".xml", "csv": ".csv", "txt": ".txt", "json": ".json"}


def cli(args: argparse.Namespace) -> int:
    from beatmarker import analysis, export

    print(f"分析中: {args.audio}")
    audio = analysis.load_audio(args.audio)
    print(f"  时长 {audio.duration:.1f}s  BPM≈{audio.tempo:.1f}")
    pts = analysis.detect_points(audio, density=args.density)
    times = [t for t, _ in pts]
    if not args.no_snap:
        times = analysis.snap_to_beats(times, audio.beat_times)

    out = args.out or (args.audio.rsplit(".", 1)[0] + "_卡点" + _EXT[args.format])
    fps = args.fps
    if args.format == "wav":
        n = export.export_wav_with_cues(args.audio, out, times)
    elif args.format == "fcpxml":
        n = export.export_fcpxml(times, out, fps=fps, duration=audio.duration)
    elif args.format == "txt":
        n = export.export_timecode_txt(times, out, fps=fps)
    elif args.format == "json":
        n = export.export_json(times, out, fps=fps, tempo=audio.tempo)
    else:  # csv
        n = export.export_premiere_csv(times, out, fps=fps,
                                       drop_frame=str(fps) in ("29.97", "59.94"))
    print(f"  导出 {n} 个卡点 -> {out}  (format={args.format}, fps={fps})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="音乐卡点检测 + Premiere 标记导出")
    parser.add_argument("audio", nargs="?", help="音频文件;省略则启动图形界面")
    parser.add_argument("-o", "--out", help="输出文件路径")
    parser.add_argument("-f", "--format", default="wav",
                        choices=["wav", "fcpxml", "csv", "txt", "json"],
                        help="导出格式(默认 wav:带cue标记的音频,Premiere自动识别)")
    parser.add_argument("--fps", type=float, default=30.0, help="序列帧率(默认30)")
    parser.add_argument("--density", type=float, default=0.5, help="卡点疏密 0~1(默认0.5)")
    parser.add_argument("--no-snap", action="store_true", help="不吸附到节拍")
    args = parser.parse_args()

    if args.audio:
        return cli(args)

    from beatmarker.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
