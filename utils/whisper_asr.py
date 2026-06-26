"""Whisper API 语音识别 — 使用 OpenAI 兼容接口，可靠性高于必剪。
参考 VideoCaptioner 的多 ASR 后端设计，作为 bcut_asr 的可靠替代方案。

支持任何兼容 OpenAI Whisper API 的服务商：
- OpenAI: https://api.openai.com/v1
- DeepSeek 不提供 Whisper，需单独配置 STT 接口
- 也可使用本地 Whisper 服务（如 whisper.cpp server）
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests


def _load_stt_config():
    return {
        "api_key": os.environ.get("STT_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")).strip(),
        "base_url": os.environ.get("STT_BASE_URL", "").strip(),
        "model": os.environ.get("STT_MODEL", "whisper-1").strip(),
    }


def _extract_audio_chunk(video_path, start_sec=0, duration_sec=None, sample_rate=16000):
    """用 FFmpeg 提取音频片段为 WAV（Whisper API 要求）"""
    suffix = ".wav"
    delete = True
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.close()

    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec),
        "-i", str(video_path),
        "-ac", "1", "-ar", str(sample_rate),
        "-f", "wav",
    ]
    if duration_sec is not None:
        cmd.extend(["-t", str(duration_sec)])
    cmd.append(tmp_path)

    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:] if result.stderr else "未知错误"
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"FFmpeg 音频提取失败: {err}")

    return tmp_path


def transcribe_with_whisper(
    video_path,
    api_key=None,
    base_url=None,
    model=None,
    language="zh",
    max_retries=3,
):
    """使用 Whisper API 转录音视频文件为 SRT 字幕。

    Args:
        video_path: 视频/音频文件路径
        api_key: API Key（默认从环境变量读取）
        base_url: API 基础地址（如 https://api.openai.com/v1）
        model: 模型名（默认 whisper-1）
        language: 语言代码（zh=中文）
        max_retries: 最大重试次数

    Returns:
        SRT 格式字幕文本
    """
    config = _load_stt_config()
    api_key = api_key or config["api_key"]
    base_url = base_url or config["base_url"]
    model = model or config["model"]

    if not api_key:
        raise RuntimeError(
            "未配置 STT API Key。请在 GUI 高级配置 → STT 接口中填写，"
            "或设置环境变量 STT_API_KEY。"
        )
    if not base_url:
        raise RuntimeError(
            "未配置 STT 接口地址。请在 GUI 高级配置 → STT 接口中填写，"
            "例如 https://api.openai.com/v1"
        )

    # 确保端点格式正确
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        # 兼容用户填了 /v1/audio/transcriptions 的情况
        if "/audio/transcriptions" in base_url:
            base_url = base_url.split("/audio/transcriptions")[0]
        if not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/")

    endpoint = f"{base_url}/audio/transcriptions"

    print(f"  Whisper API: {endpoint}")
    print(f"  模型: {model}")

    # 提取音频（WAV 格式，Whisper 推荐 16kHz）
    print("  正在提取音频...")
    audio_path = _extract_audio_chunk(video_path, sample_rate=16000)

    last_error = None
    for attempt in range(max_retries):
        try:
            print(f"  Whisper 识别中... (尝试 {attempt + 1}/{max_retries})")

            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

            # Whisper API 通常有 25MB 文件限制，大文件需要分片
            if file_size_mb > 20:
                os.unlink(audio_path)
                print(f"  音频较大 ({file_size_mb:.0f} MB)，取前 4 小时...")
                audio_path = _extract_audio_chunk(video_path, duration_sec=14400, sample_rate=16000)

            with open(audio_path, "rb") as f:
                resp = requests.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": model, "language": language, "response_format": "srt"},
                    timeout=300,
                )

            if resp.status_code == 200:
                srt_text = resp.text.strip()
                if not srt_text:
                    raise RuntimeError("Whisper API 返回空结果")
                print(f"  Whisper 识别成功 ({srt_text.count(chr(10) + chr(10)) or 1} 段)")
                return srt_text
            elif resp.status_code == 429:
                raise RuntimeError("API 频率限制 (429)，请稍后重试")
            elif resp.status_code == 401:
                raise RuntimeError("API Key 无效 (401)，请检查 STT 接口配置")
            elif resp.status_code == 404:
                raise RuntimeError(
                    f"端点不存在 (404): {endpoint}。"
                    "请确认 STT 接口地址格式，例如 https://api.openai.com/v1"
                )
            else:
                detail = resp.text[:200]
                raise RuntimeError(f"API 错误 ({resp.status_code}): {detail}")

        except RuntimeError:
            raise  # 配置/权限错误直接抛出，不重试
        except requests.exceptions.ConnectionError:
            last_error = f"无法连接 STT 服务器: {base_url}"
        except requests.exceptions.Timeout:
            last_error = "STT 请求超时 (5分钟)"
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries - 1:
            wait = (attempt + 1) * 5
            print(f"  暂失败: {last_error}，{wait}秒后重试...")
            time.sleep(wait)

    # 清理
    if os.path.exists(audio_path):
        os.unlink(audio_path)

    raise RuntimeError(f"Whisper 识别失败（重试{max_retries}次）: {last_error}")


def video_to_srt_whisper(video_path, output_dir=None, **kwargs):
    """便捷方法：视频 → SRT 字幕文件（Whisper API）"""
    video_path = str(video_path)
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"  视频: {video.name} ({size_mb:.1f} MB)")

    srt_text = transcribe_with_whisper(video_path, **kwargs)

    out = Path(output_dir or os.path.dirname(video_path))
    out.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    srt_path = out / f"{stem}.srt"

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    print(f"  字幕已生成: {srt_path.name}")
    return srt_path


# ==========================================
# 自动 ASR 链路（bcut → whisper → 报错）
# ==========================================

def auto_generate_srt(video_path, output_dir=None):
    """自动选择 ASR 后端：优先必剪免费，失败回退 Whisper API"""
    # 第 1 优先：必剪免费 ASR
    try:
        print("  尝试必剪 (Bcut) 免费 ASR...")
        from utils.bcut_asr import video_to_srt as bcut_video_to_srt

        return bcut_video_to_srt(video_path, output_dir)
    except Exception as e:
        print(f"  必剪 ASR 失败: {e}")
        print("  回退到 Whisper API...")

    # 第 2 优先：Whisper API
    config = _load_stt_config()
    if config["api_key"] and config["base_url"]:
        try:
            return video_to_srt_whisper(video_path, output_dir)
        except Exception as e:
            print(f"  Whisper API 也失败: {e}")

    raise RuntimeError(
        "所有 ASR 后端均失败。\n"
        "必剪 (免费): 失败\n"
        f"Whisper API: {'未配置' if not config['base_url'] else '失败'}\n"
        "请在高级配置中设置 STT 接口（如 OpenAI Whisper API）作为备选。"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        video_to_srt_whisper(sys.argv[1])
    else:
        print("用法: python whisper_asr.py <视频文件路径>")
        print("环境变量: STT_API_KEY, STT_BASE_URL (如 https://api.openai.com/v1)")
