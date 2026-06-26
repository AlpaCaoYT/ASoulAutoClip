"""SenseVoiceSmall 语音识别 — 阿里通义千问 ASR 模型，中文准确率优于 Whisper。
完全免费，本地运行。参考 Modelscope_Faster_Whisper_Multi_Subtitle 项目方案。
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
        raise RuntimeError(f"FFmpeg 音频提取失败: {err}")
    return tmp_path


def transcribe_sensevoice(video_path, output_dir=None):
    """使用 SenseVoiceSmall 模型转录音视频为 SRT 字幕。

    首次使用自动下载模型（~200MB），之后离线可用。
    中文识别准确率优于 OpenAI Whisper。

    Args:
        video_path: 视频/音频文件路径
        output_dir: SRT 输出目录

    Returns:
        SRT 文件路径
    """
    video_path = str(video_path)
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"  视频: {video.name} ({size_mb:.1f} MB)")
    print("  使用 SenseVoiceSmall (阿里通义千问) 识别，首次需下载模型 (~200MB)...")

    try:
        import warnings
        warnings.filterwarnings("ignore")
        import sys
        # editdistance 需要 C++ 编译器，用桩模块跳过
        if "editdistance" not in sys.modules:
            class _FakeED:
                def __init__(self, *a, **k): pass
                def eval(self, *a, **k): return 0
                def distance(self, *a, **k): return 0
            sys.modules["editdistance"] = _FakeED()
        from funasr import AutoModel
    except ImportError as e:
        raise RuntimeError(
            f"funasr 依赖缺失 ({e})。需要 VS Build Tools 编译 editdistance。\n"
            "暂时可跳过此方案，使用 faster-whisper GPU。"

    # 提取音频
    print("  提取音频...")
    audio_path = _extract_audio(video_path)

    try:
        # 加载模型（自动从 ModelScope 下载）
        print("  加载 SenseVoiceSmall 模型...")
        model = AutoModel(
            model="iic/SenseVoiceSmall",
            disable_pbar=True,
            device="cpu",
        )

        print("  识别中...")
        result = model.generate(
            input=audio_path,
            language="zh",
            use_itn=True,       # 逆文本正则化（数字/标点规范化）
            batch_size=1,
        )

        if not result or len(result) == 0:
            raise RuntimeError("SenseVoice 返回空结果")

        # 解析结果生成 SRT
        segments = result[0].get("text", "") if isinstance(result[0], dict) else str(result[0])
        timestamp_data = result[0].get("timestamp", []) if isinstance(result[0], dict) else []

        srt_lines = []
        idx = 1

        if timestamp_data:
            # 有时间戳 → 逐句生成
            for ts in timestamp_data:
                start_ms = ts[0] if ts[0] is not None else 0
                end_ms = ts[1] if ts[1] is not None else start_ms + 1000
                text = ts[2] if len(ts) > 2 else ""
                if not text:
                    continue

                t1 = f"{int(start_ms//3600000):02d}:{int((start_ms//60000)%60):02d}:{int((start_ms//1000)%60):02d},{start_ms%1000:03d}"
                t2 = f"{int(end_ms//3600000):02d}:{int((end_ms//60000)%60):02d}:{int((end_ms//1000)%60):02d},{end_ms%1000:03d}"
                srt_lines.append(f"{idx}\n{t1} --> {t2}\n{text}\n")
                idx += 1
        else:
            # 无时间戳 → 整个文本作为一个片段
            srt_lines.append(f"1\n00:00:00,000 --> 99:59:59,000\n{segments}\n")

        srt_text = "\n".join(srt_lines)
        print(f"  SenseVoice 识别完成: {idx - 1} 段")

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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        transcribe_sensevoice(sys.argv[1])
    else:
        print("用法: python sensevoice_asr.py <视频路径>")
        print("依赖: pip install funasr modelscope")
