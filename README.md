# ASoulAutoClip — A-SOUL / 闪耀舞台 直播自动切片工具

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![FFmpeg](https://img.shields.io/badge/Dependency-FFmpeg-green)
![License](https://img.shields.io/badge/license-MIT-orange)

> 专为枝江娱乐（A-SOUL + 闪耀舞台）粉丝切片UP主设计的自动化工具。填入 BV 号或拖入视频，一键生成带字幕和封面的切片。

## 启动

```
双击 run_app.bat
```

入口 `app_launcher.py`，基于 tkinter + tkinterdnd2 构建的图形工作台。

## 两种工作流

| | BV号模式 | 视频模式 |
|---|---|---|
| 第1步 | yutto 下载视频 + 字幕 + 弹幕(ASS) | 必剪免费 ASR 生成 SRT 字幕 |
| 第2步 | ASRCorrector 字幕纠错 | 同左 |
| 第3步 | DanmakuAnalyzer 弹幕密度 → DeepSeek → Data_source.txt | 无弹幕时自动跳过 |
| 第4步 | Auto_clip 读取 Data_source.txt → ffmpeg 切片 + 封面 | 自动基于 SRT 生成默认切片 |

- **BV号模式**：填 BV 号 → 自动下载 → 弹幕分析高光 → AI 生成标题文案 → 切片
- **视频模式**：拖入视频 → 必剪免费语音识别 → 自动切片。无需 API Key。

## 关键功能

- **GUI 工作台**：步骤状态跟踪、失败弹窗（跳过/重试/停止）、文件拖放、日志记录
- **成员出场标记**：勾选本次直播出场的成员，自动匹配专属提示词
- **标题编辑器**：切片前可预览和修改所有片段的标题/摘要/封面文字
- **封面配置**：4种预设封面风格可选，自动生成缩略图
- **5位成员专属提示词**：嘉然/贝拉/乃琳/心宜/思诺，严格对齐成员知识库
- **6种 ASS 字幕配色**：成员代表色精准匹配（粉/紫/蓝/金/银/深蓝）
- **必剪免费 ASR**：B站公开接口，无字幕视频自动生成，无需 Key
- **Whisper API 回退**：必剪失败时自动切换 OpenAI Whisper API（需配置 STT 接口），可靠性更高
- **LLM 智能字幕纠错**：大模型 + 成员知识库修正 ASR 识别错误（需 API Key），比词典纠错更智能

## 依赖

### 系统
- **FFmpeg** — 必须安装并加入 PATH

### Python
```bash
pip install -r requirements.txt
pip install yutto pydub tkinterdnd2
```

### AI 接口（可选）
- **DeepSeek API** — 弹幕分析 + LLM 字幕纠错。不填也能用视频模式
- **STT 接口**（可选）— 必剪 ASR 的可靠回退。支持 OpenAI Whisper API 及兼容服务

## 项目结构

```
ASoulAutoClip/
├── app_launcher.py              # GUI 工作台主入口
├── run_app.bat                  # Windows 双击启动
├── Auto_clip.py                 # 自动切片核心引擎
├── requirements.txt
├── core/
│   ├── video_processor.py       # 单片段处理（ffmpeg + ASS）
│   ├── subtitle_utils.py        # SRT/ASS 字幕生成（6成员配色）
│   ├── cover_generator.py       # 封面生成（PIL + ffmpeg截图）
│   ├── regen_script.py          # 独立重新生成脚本
│   └── ...
├── danmu_method/
│   └── get_data_by_danmu.py     # 弹幕密度分析 + DeepSeek AI 元数据
├── prompt_method/               # 6个成员专属提示词模板
│   ├── prompt_A-SOUL团播.txt
│   ├── prompt_嘉然单播.txt
│   ├── prompt_贝拉单播.txt
│   ├── prompt_乃琳单播.txt
│   ├── prompt_心宜单播.txt
│   └── prompt_思诺单播.txt
├── utils/
│   ├── bcut_asr.py              # 必剪免费语音识别
│   ├── whisper_asr.py           # Whisper API 语音识别（回退方案）
│   ├── llm_asr_corrector.py     # LLM 智能字幕纠错
│   ├── ASRCorrector.py          # 字典字幕纠错
│   ├── asr_dict.txt             # 纠错词典（~600条）
│   └── ...
├── assets/font/                 # 字体文件
├── 成员知识库.md                 # 成员完整档案（梗/CP/红线）
└── workspace/
    ├── video_input/             # 素材输入目录
    ├── clip_output/             # 切片输出目录
    └── logs/                    # 运行日志
```

## 成员代表色

| 成员 | 代表色 | 粉丝名 | CP |
|------|--------|--------|-----|
| 嘉然 | 粉色 #FF69B4 | 嘉心糖 | 琳嘉女孩、超级嘉贝 |
| 贝拉 | 紫色 #9B59B6 | 贝极星 | 乃贝、超级嘉贝 |
| 乃琳 | 蓝色 #3498DB | 奶淇琳 | 乃贝、琳嘉女孩 |
| 心宜 | 金色 #FFD700 | 心球仪 | 小心思 |
| 思诺 | 银色 #C0C0C0 | 小海诺 | 小心思 |

## B站成员录播链接

- [嘉然](https://space.bilibili.com/672328094) · [贝拉](https://space.bilibili.com/672353429) · [乃琳](https://space.bilibili.com/672342685)
- [心宜](https://space.bilibili.com/3537115310721181) · [思诺](https://space.bilibili.com/3537115310721781)

## 致谢

- [VideoCaptioner](https://github.com/weifang1314/VideoCaptioner) — 必剪 ASR 模块（`utils/bcut_asr.py`）提取自此项目，感谢其探索的 B站公开接口方案
- [dayanggo/ASoulAutoClip](https://github.com/dayanggo/ASoulAutoClip) — 项目原始基础，本仓库在其之上进行了 GUI 化改造、功能扩展和知识库对齐

## 免责声明

- 本项目为粉丝自制开源工具，非 A-SOUL / 闪耀舞台 官方项目
- 生成的视频素材版权归枝江娱乐所有
- 请遵守 B站社区规范及二创公约
- 禁止用于商业盈利目的

