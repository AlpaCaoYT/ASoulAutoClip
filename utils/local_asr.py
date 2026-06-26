"""本地 Whisper 语音识别 — 基于 faster-whisper，完全免费无需 API Key。
作为必剪 ASR 失败后的可靠回退方案。
"""

import os
import subprocess
import tempfile
from pathlib import Path


def _extract_audio(video_path, sample_rate=16000):
    """提取音频为 WAV"""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-ac", "1", "-ar", str(sample_rate),
        "-f", "wav", tmp_path,
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:] if result.stderr else ""
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"FFmpeg 提取音频失败: {err}")
    return tmp_path


def transcribe_local(video_path, output_dir=None, model_size="small"):
    """使用本地 faster-whisper 模型转录音视频。

    Args:
        video_path: 视频/音频文件路径
        output_dir: SRT 输出目录
        model_size: 模型大小 (tiny/base/small/medium/large-v3)
                   tiny=最快最不准, large=最慢最准, small=推荐平衡

    Returns:
        SRT 文件路径
    """
    video_path = str(video_path)
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"  视频: {video.name} ({size_mb:.1f} MB)")
    print(f"  使用本地 Whisper ({model_size}) 识别，首次使用需下载模型 (~500MB-3GB)...")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper 未安装。运行: pip install faster-whisper"
        )

    # 提取音频
    print("  提取音频...")
    audio_path = _extract_audio(video_path)

    try:
        # 加载模型（自动下载到缓存目录）
        compute = "int8"  # CPU 优化，GPU 可用 "float16"
        model = WhisperModel(model_size, device="cpu", compute_type=compute)
        print(f"  模型已加载，开始识别...")

        # 识别
        segments, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,  # 过滤静音
        )
        print(f"  检测到语言: {info.language} (概率: {info.language_probability:.2f})")

        # 生成 SRT
        srt_lines = []
        idx = 1
        for seg in segments:
            start = seg.start
            end = seg.end
            text = seg.text.strip()
            if not text:
                continue

            # 格式化时间戳
            t1 = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d},{int((start%1)*1000):03d}"
            t2 = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d},{int((end%1)*1000):03d}"
            srt_lines.append(f"{idx}\n{t1} --> {t2}\n{text}\n")
            idx += 1

            # 进度显示
            if idx % 30 == 0:
                pct = min(99, int(start / (size_mb * 30)))  # 粗略估算
                print(f"    已识别 {idx} 段...", end="\r")

        srt_text = "\n".join(srt_lines)
        print(f"  本地识别完成: {idx - 1} 段字幕")

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)

    # 保存 SRT
    out = Path(output_dir or os.path.dirname(video_path))
    out.mkdir(parents=True, exist_ok=True)
    srt_path = out / f"{video.stem}.srt"

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    print(f"  字幕已保存: {srt_path.name}")
    return srt_path


# ==========================================
# 增强版自动 ASR 链路
# ==========================================

def auto_generate_srt_robust(video_path, output_dir=None):
    """三级回退: 必剪(free) → 本地Whisper(free) → 报错"""
    # 第1优先: 必剪
    try:
        print("  [1/3] 尝试必剪 (Bcut) 免费 ASR...")
        from utils.bcut_asr import video_to_srt
        return video_to_srt(video_path, output_dir)
    except Exception as e:
        print(f"  必剪失败: {e}")

    # 第2优先: 本地 Whisper（完全免费）
    try:
        print("  [2/3] 回退到本地 Whisper (免费，首次需下载模型)...")
        return transcribe_local(video_path, output_dir, model_size="small")
    except Exception as e:
        print(f"  本地 Whisper 失败: {e}")

    # 第3优先: Whisper API（需 Key）
    try:
        print("  [3/3] 回退到 Whisper API...")
        from utils.whisper_asr import video_to_srt_whisper
        return video_to_srt_whisper(video_path, output_dir)
    except Exception as e:
        print(f"  Whisper API 也失败: {e}")

    raise RuntimeError(
        "所有 ASR 方案均失败。\n"
        "  [1] 必剪 (B站接口): 可能限流或需登录\n"
        "  [2] 本地 Whisper: pip install faster-whisper\n"
        "  [3] Whisper API: 在高级配置中设置 STT 接口\n"
        "至少需要其中一种可用。"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        transcribe_local(sys.argv[1])
    else:
        print("用法: python local_asr.py <视频路径>")
