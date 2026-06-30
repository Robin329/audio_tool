"""把卡点导出为 Adobe Premiere Pro 可识别的标记文件。

Premiere 能"自动识别"标记的两种方式(都不需要手动「从 CSV 导入标记」):

1. 带 cue 标记的 WAV —— 把卡点作为 cue 点嵌进音频本身,
   导入/拖入 Premiere 后标记自动出现在该音频片段上。  export_wav_with_cues()
2. FCPXML 序列 —— 文件→导入 该 .xml,生成带时间线标记的序列。 export_fcpxml()

另外保留 CSV / TXT / JSON 供需要时使用。
"""
from __future__ import annotations

import csv
import struct
from typing import Iterable, Sequence
from xml.sax.saxutils import escape


def seconds_to_timecode(t: float, fps: float, drop_frame: bool = False) -> str:
    """秒 -> 时间码 HH:MM:SS:FF。

    fps 支持 23.976/24/25/29.97/30/50/59.94/60。
    对非整数帧率,这里按四舍五入到最近整数帧的非丢帧时间码处理
    (Premiere 导入卡点足够准;如需严格丢帧时间码可设 drop_frame)。
    """
    if t < 0:
        t = 0.0
    nominal_fps = int(round(fps))
    total_frames = int(round(t * fps))

    sep = ";" if drop_frame else ":"
    frames = total_frames % nominal_fps
    total_seconds = total_frames // nominal_fps
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{frames:02d}"


# Premiere 标记 CSV 的标准列
_PREMIERE_HEADER = ["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"]


def export_premiere_csv(
    times: Sequence[float],
    path: str,
    fps: float = 30.0,
    *,
    name_prefix: str = "卡点",
    marker_type: str = "Comment",
    drop_frame: bool = False,
) -> int:
    """导出 Premiere Pro 标记 CSV。返回写入的标记数。

    每个卡点写成一个零时长的注释标记。In==Out。
    """
    times = sorted(set(round(float(t), 4) for t in times))
    # utf-8-sig 带 BOM,保证 Premiere 在 Windows 上正确识别中文
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_PREMIERE_HEADER)
        for i, t in enumerate(times, 1):
            tc = seconds_to_timecode(t, fps, drop_frame)
            writer.writerow([f"{name_prefix} {i:03d}", "", tc, tc, "00:00:00:00", marker_type])
    return len(times)


def export_timecode_txt(times: Sequence[float], path: str, fps: float = 30.0) -> int:
    """导出纯时间码清单(每行一个),用于手动核对或其它软件。"""
    times = sorted(set(round(float(t), 4) for t in times))
    with open(path, "w", encoding="utf-8") as f:
        for t in times:
            f.write(f"{seconds_to_timecode(t, fps)}\t{t:.3f}s\n")
    return len(times)


def export_json(times: Iterable[float], path: str, fps: float = 30.0, tempo: float | None = None) -> int:
    """导出 JSON(秒 + 时间码),便于程序化二次处理。"""
    import json

    times = sorted(set(round(float(t), 4) for t in times))
    data = {
        "fps": fps,
        "tempo": tempo,
        "count": len(times),
        "markers": [
            {"index": i, "time": t, "timecode": seconds_to_timecode(t, fps)}
            for i, t in enumerate(times, 1)
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(times)


# --------------------------------------------------------------------------
# 方式一:带 cue 标记的 WAV(Premiere 自动识别为片段标记)
# --------------------------------------------------------------------------
def _build_cue_chunks(sample_positions: Sequence[int], names: Sequence[str]) -> bytes:
    """构造 RIFF 的 'cue ' 与 'LIST/adtl/labl' 两个块(已字对齐)。"""
    n = len(sample_positions)
    cue_body = struct.pack("<I", n)
    for i, pos in enumerate(sample_positions, 1):
        # id, position, fccChunk('data'), chunkStart, blockStart, sampleOffset
        cue_body += struct.pack("<II4sIII", i, pos, b"data", 0, 0, pos)
    cue_chunk = b"cue " + struct.pack("<I", len(cue_body)) + cue_body

    adtl = b"adtl"
    for i, name in enumerate(names, 1):
        text = name.encode("utf-8") + b"\x00"   # 以 NUL 结尾
        size = 4 + len(text)                     # dwName + 文本(不含填充字节)
        chunk = b"labl" + struct.pack("<I", size) + struct.pack("<I", i) + text
        if size % 2:                             # 块体为奇数则补一个填充字节
            chunk += b"\x00"
        adtl += chunk
    list_chunk = b"LIST" + struct.pack("<I", len(adtl)) + adtl
    return cue_chunk + list_chunk


def _xmp_marker_packet(positions: Sequence[int], names: Sequence[str], sample_rate: int) -> bytes:
    """构造 Adobe XMP 动态媒体标记包(Premiere / Audition 真正读取的标记)。

    音频标记:frameRate 用 f<采样率>,startTime 为样本序号 —— 样本级精确。
    """
    items = []
    for pos, name in zip(positions, names):
        items.append(
            '         <rdf:li rdf:parseType="Resource">\n'
            f'          <xmpDM:startTime>{pos}</xmpDM:startTime>\n'
            '          <xmpDM:duration>0</xmpDM:duration>\n'
            f'          <xmpDM:name>{escape(name)}</xmpDM:name>\n'
            '          <xmpDM:type>Cue</xmpDM:type>\n'
            '         </rdf:li>'
        )
    body = "\n".join(items)
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">\n'
        '   <xmpDM:Tracks>\n'
        '    <rdf:Bag>\n'
        '     <rdf:li rdf:parseType="Resource">\n'
        '      <xmpDM:trackName>CuePoint Markers</xmpDM:trackName>\n'
        '      <xmpDM:trackType>Cue</xmpDM:trackType>\n'
        f'      <xmpDM:frameRate>f{sample_rate}</xmpDM:frameRate>\n'
        '      <xmpDM:markers>\n'
        '       <rdf:Seq>\n'
        f'{body}\n'
        '       </rdf:Seq>\n'
        '      </xmpDM:markers>\n'
        '     </rdf:li>\n'
        '    </rdf:Bag>\n'
        '   </xmpDM:Tracks>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def _riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    """打包一个字对齐的 RIFF 子块。"""
    chunk = fourcc + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        chunk += b"\x00"
    return chunk


def export_wav_with_cues(
    src_audio_path: str,
    out_path: str,
    times: Sequence[float],
    *,
    name_prefix: str = "卡点",
) -> int:
    """把卡点嵌入音频另存为 WAV(同时写 Adobe XMP 标记与通用 cue 标记)。返回标记数。

    - `_PMX`(XMP/xmpDM:markers):Premiere、Audition 读取的标记;这是 PR 能识别的关键。
    - `cue `/`LIST`:通用 WAV cue 标记,供其它软件使用。

    Premiere 导入/拖入该 WAV 后,会在该音频片段上显示这些标记。
    会用原始音频的采样率与声道,转成 16bit PCM WAV。
    """
    import soundfile as sf

    data, sr = sf.read(src_audio_path, dtype="float32", always_2d=False)
    sf.write(out_path, data, sr, subtype="PCM_16")

    positions = sorted({int(round(t * sr)) for t in times if t >= 0})
    names = [f"{name_prefix} {i:03d}" for i in range(1, len(positions) + 1)]

    cue_blocks = _build_cue_chunks(positions, names)
    xmp_block = _riff_chunk(b"_PMX", _xmp_marker_packet(positions, names, sr))

    with open(out_path, "rb") as f:
        raw = bytearray(f.read())
    raw += cue_blocks
    raw += xmp_block
    # 修正 RIFF 总块大小 = 文件长度 - 8
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    with open(out_path, "wb") as f:
        f.write(raw)
    return len(positions)


# --------------------------------------------------------------------------
# 方式二:FCPXML(Final Cut Pro 7 xmeml)序列,文件→导入 即得带标记的序列
# --------------------------------------------------------------------------
def export_fcpxml(
    times: Sequence[float],
    out_path: str,
    fps: float = 30.0,
    *,
    duration: float | None = None,
    sequence_name: str = "卡点序列",
    name_prefix: str = "卡点",
) -> int:
    """导出 FCP7 XML(.xml),内含时间线标记。返回标记数。

    Premiere:文件 → 导入 → 选择该 .xml,会生成一条带标记的序列。
    """
    times = sorted({round(float(t), 4) for t in times if t >= 0})
    nominal = int(round(fps))
    ntsc = "TRUE" if abs(fps - nominal) > 1e-3 else "FALSE"
    total_frames = int(round((duration if duration else (times[-1] + 2 if times else 1)) * fps))

    markers = []
    for i, t in enumerate(times, 1):
        frame = int(round(t * fps))
        name = escape(f"{name_prefix} {i:03d}")
        markers.append(
            f"    <marker>\n"
            f"      <name>{name}</name>\n"
            f"      <comment></comment>\n"
            f"      <in>{frame}</in>\n"
            f"      <out>-1</out>\n"
            f"    </marker>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE xmeml>\n"
        '<xmeml version="4">\n'
        "  <sequence>\n"
        f"    <name>{escape(sequence_name)}</name>\n"
        f"    <duration>{total_frames}</duration>\n"
        "    <rate>\n"
        f"      <timebase>{nominal}</timebase>\n"
        f"      <ntsc>{ntsc}</ntsc>\n"
        "    </rate>\n"
        + "\n".join(markers) + ("\n" if markers else "")
        + "  </sequence>\n"
        "</xmeml>\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return len(times)
