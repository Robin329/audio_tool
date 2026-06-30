# 卡点标记工具 (BeatMarker)

从音乐中自动检测**卡点位置**,在波形上可视化、手动微调,并导出 **Adobe Premiere Pro** 可导入的标记文件,方便导进去预览验证卡点。

## 功能

- **自动检测**:综合三类音频事件并按强度排序
  - 节拍 (beat) —— 规律节奏脉冲
  - 起音点 (onset) —— 每个音符/打击的起跳,适合快剪
  - 低频冲击 (kick / bass drop) —— 最"炸"的重点强卡点
- **疏密可调**:一个滑块控制卡点密度,实时重算
- **吸附到节拍**:把卡点对齐到最近拍点,更整齐
- **手动编辑**:波形上双击增/删、单击定位、空格播放试听
- **导出**:Premiere 自动识别的两种标记格式 + CSV / TXT / JSON

## 安装

```bash
pip install -r requirements.txt
```

依赖:librosa、soundfile、sounddevice、numpy、matplotlib(tkinter 随 Python 自带)。

## 使用

### 图形界面

```bash
python main.py
```

1. 「打开音频…」选择 mp3/wav/flac 等
2. 自动检测并在波形上以红线标出卡点;拖动「疏密」滑块调整数量
3. 试听微调:
   - 单击波形 = 移动播放头,空格 = 播放/停止
   - 双击波形 = 在该处增/删卡点
   - `a` = 在播放头加卡点,`d` / `Delete` = 删除最近卡点
4. 选好序列**帧率 fps**(要和 Premiere 序列一致),按需点导出按钮

### 命令行(批量/无界面)

```bash
# 默认导出带 cue 标记的 WAV(Premiere 自动识别)
python main.py song.mp3 --density 0.6

# 指定格式:wav / fcpxml / csv / txt / json
python main.py song.mp3 -f fcpxml --fps 30 -o song_卡点.xml
```

## 让 Premiere 识别卡点(无需「从 CSV 导入」)

部分 Premiere 版本没有「从 CSV 导入标记」入口,所以提供两种**自动识别**的方式:

### 方式一 ⭐ 带标记的 WAV(最省事)

「导出带标记 WAV…」会把卡点作为 **cue 标记嵌进音频文件本身**。

1. 把导出的 `*_卡点.wav` 像普通素材一样**导入 / 拖进 Premiere**
2. 标记会**自动出现在该音频片段上**(片段标记),无需任何手动导入步骤
3. 想把它们变成时间线标记:选中片段右键 → 复制标记,或直接对齐参考

> 该方式与帧率无关,标记按采样精确位置嵌入,跟着音频走,最不容易出错。

### 方式二 FCPXML 序列

「导出 FCPXML…」生成一个 `.xml`:

1. Premiere → **文件 → 导入** → 选择该 `.xml`
2. 会生成一条带**时间线标记**的序列
3. 序列帧率请与导出时的 **fps 一致**(29.97 / 59.94 已按 NTSC 处理),否则卡点整体偏移

> 仍保留 CSV(支持「从 CSV 导入标记」的版本)、时间码 TXT、JSON,在「其他…」按钮里。

## 目录结构

```
audio_tool/
├── main.py              入口:GUI 或 CLI
├── beatmarker/
│   ├── analysis.py      卡点检测算法
│   ├── export.py        Premiere CSV / TXT / JSON 导出
│   └── gui.py           Tkinter 界面
├── requirements.txt
└── README.md
```
