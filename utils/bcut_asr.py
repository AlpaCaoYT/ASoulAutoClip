"""必剪 (Bcut) ASR 语音识别 — 免费，无需 API Key。
基于 Bilibili 公开接口，VideoCaptioner 同款方案。
"""

import json
import os
import time
import subprocess
import tempfile
from pathlib import Path

import requests

API_BASE = "https://member.bilibili.com/x/bcut/rubick-interface"

HEADERS = {
    "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Referer": "https://www.bilibili.com/",
}


class BcutASR:
    """必剪语音识别，输入视频/音频文件，输出 SRT 字幕。"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._resource_id = None
        self._upload_id = None
        self._upload_urls = []
        self._per_size = None
        self._task_id = None

    def _extract_audio(self) -> bytes:
        """用 FFmpeg 从视频提取音频为 mp3"""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            "ffmpeg", "-y", "-i", self.file_path,
            "-ac", "1", "-ar", "16000", "-b:a", "64k",
            "-f", "mp3", tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-300:] if result.stderr else "未知错误"
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise RuntimeError(f"FFmpeg 音频提取失败: {err}")

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise RuntimeError("FFmpeg 提取的音频文件为空，请检查视频文件是否完整")

        with open(tmp_path, "rb") as f:
            data = f.read()
        os.unlink(tmp_path)
        return data

    def transcribe(self, max_retries=1) -> str:
        """执行语音识别，返回 SRT 格式字幕文本（含重试）"""
        last_error = None
        for attempt in range(max_retries):
            try:
                print(f"  必剪 ASR 识别中... (尝试 {attempt + 1}/{max_retries})")
                audio = self._extract_audio()
                self._request_upload(audio)
                self._upload_parts(audio)
                self._commit_upload()
                self._create_task()
                result = self._query_result()
                return self._build_srt(result)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 3
                    print(f"  暂失败: {e}，{wait}秒后重试...")
                    time.sleep(wait)
        raise RuntimeError(f"必剪 ASR 识别失败（重试{max_retries}次）: {last_error}")

    def _request_upload(self, audio: bytes):
        resp = requests.post(
            f"{API_BASE}/resource/create",
            json={
                "type": 2,
                "name": "audio.mp3",
                "size": len(audio),
                "ResourceFileType": "mp3",
                "model_id": "8",
            },
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        self._resource_id = data["resource_id"]
        self._upload_id = data["upload_id"]
        self._upload_urls = data["upload_urls"]
        self._per_size = data["per_size"]

    def _upload_parts(self, audio: bytes):
        for i, url in enumerate(self._upload_urls):
            start = i * self._per_size
            end = min((i + 1) * self._per_size, len(audio))
            requests.put(url, data=audio[start:end], timeout=60).raise_for_status()

    def _commit_upload(self):
        resp = requests.post(
            f"{API_BASE}/resource/create/complete",
            json={
                "resource_id": self._resource_id,
                "upload_id": self._upload_id,
            },
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()

    def _create_task(self):
        resp = requests.post(
            f"{API_BASE}/task",
            json={"resource_id": self._resource_id},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        self._task_id = resp.json()["data"]["task_id"]

    def _query_result(self, max_retries=60, interval=2) -> dict:
        for _ in range(max_retries):
            resp = requests.post(
                f"{API_BASE}/task/result",
                json={"task_id": self._task_id},
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            if data.get("status") == 4:  # 完成
                return json.loads(data["result"])
            time.sleep(interval)
        raise TimeoutError("必剪 ASR 超时，请重试")

    def _build_srt(self, result: dict) -> str:
        lines = []
        for i, seg in enumerate(result.get("transcript", []), 1):
            start_ms = seg["start_time"]
            end_ms = seg["end_time"]
            text = seg["transcript"].strip()
            if not text:
                continue
            t1 = f"{start_ms//3600000:02d}:{(start_ms//60000)%60:02d}:{(start_ms//1000)%60:02d},{start_ms%1000:03d}"
            t2 = f"{end_ms//3600000:02d}:{(end_ms//60000)%60:02d}:{(end_ms//1000)%60:02d},{end_ms%1000:03d}"
            lines.append(f"{len(lines)+1}\n{t1} --> {t2}\n{text}\n")
        return "\n".join(lines)


def video_to_srt(video_path: str, output_dir: str = None) -> Path:
    """便捷方法：视频 → SRT 字幕文件"""
    video_path = str(video_path)
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"  视频: {video.name} ({size_mb:.1f} MB)")

    asr = BcutASR(video_path)
    srt_text = asr.transcribe()

    if not srt_text or len(srt_text.strip()) < 10:
        raise RuntimeError("必剪 ASR 返回空结果，视频可能无语音或接口限流")

    out = Path(output_dir or os.path.dirname(video_path))
    out.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    srt_path = out / f"{stem}.srt"

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    line_count = srt_text.count("\n\n") + 1
    print(f"  字幕已生成: {srt_path.name} ({line_count} 行)")
    return srt_path
