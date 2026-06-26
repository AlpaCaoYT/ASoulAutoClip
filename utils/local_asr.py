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
    自动使用 HuggingFace 国内镜像下载模型。
    """
    # 设置 HF 国内镜像（解决 SSL/网络问题）
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    video_path = str(video_path)
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"  视频: {video.name} ({size_mb:.1f} MB)")
    print(f"  使用本地 Whisper ({model_size}) 识别，首次需下载模型 (~500MB-3GB)...")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("faster-whisper 未安装。运行: pip install faster-whisper")

    # 提取音频
    print("  提取音频...")
    audio_path = _extract_audio(video_path)

    try:
        # 加载模型（自动下载到缓存目录）
        # 自动检测 GPU/CUDA，失败回退 CPU
        device = "cpu"
        compute = "int8"
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                device = "cuda"
                compute = "float16"
                print(f"  检测到 GPU，尝试 CUDA 加速...")
        except Exception:
            pass

        model_dir = str(Path(__file__).resolve().parent.parent / "models")
        try:
            model = WhisperModel(model_size, device=device, compute_type=compute,
                                 download_root=model_dir)
        except Exception as e:
            if device == "cuda":
                print(f"  GPU 不可用 ({e})，回退 CPU...")
                device = "cpu"
                compute = "int8"
                model = WhisperModel(model_size, device=device, compute_type=compute,
                                     download_root=model_dir)
            else:
                raise
        print(f"  模型已加载，开始识别...")

        # 识别（beam_size=1 速度最快，vad_filter 跳过静音）
        segments, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
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
    """四级回退: 必剪 → SenseVoice(中文最优) → 本地Whisper → WhisperAPI"""
    # [1] 必剪 (B站免费接口)
    try:
        print("  [1/4] 尝试必剪 (Bcut) 免费 ASR...")
        from utils.bcut_asr import video_to_srt
        return video_to_srt(video_path, output_dir)
    except Exception as e:
        print(f"  必剪失败: {e}")

    # [2] SenseVoiceSmall (阿里通义千问 — 中文准确率优于 Whisper)
    try:
        print("  [2/4] 尝试 SenseVoiceSmall (阿里通义千问, 中文最优)...")
        from utils.sensevoice_asr import transcribe_sensevoice
        return transcribe_sensevoice(video_path, output_dir)
    except Exception as e:
        print(f"  SenseVoice 失败: {e}")

    # [3] 本地 faster-whisper (通用方案)
    try:
        print("  [3/4] 回退到本地 faster-whisper...")
        return transcribe_local(video_path, output_dir, model_size="large-v3")
    except Exception as e:
        print(f"  faster-whisper 失败: {e}")

    # [4] Whisper API (需 Key)
    try:
        print("  [4/4] 回退到 Whisper API...")
        from utils.whisper_asr import video_to_srt_whisper
        return video_to_srt_whisper(video_path, output_dir)
    except Exception as e:
        print(f"  Whisper API 也失败: {e}")

    raise RuntimeError(
        "所有 ASR 方案均失败。至少需要一种可用:\n"
        "  pip install funasr modelscope  (SenseVoice, 中文最优)\n"
        "  pip install faster-whisper     (本地 Whisper)\n"
        "  或配置 STT 接口 (Whisper API)"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        transcribe_local(sys.argv[1])
    else:
        print("用法: python local_asr.py <视频路径>")
