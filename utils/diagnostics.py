"""故障诊断系统 — 检测所有依赖项和环境，给出可操作的修复建议。

用法:
    from utils.diagnostics import run_diagnostics
    results = run_diagnostics()
    for r in results:
        print(f"[{r['level']}] {r['name']}: {r['message']}")
        if r.get('fix'):
            print(f"  修复: {r['fix']}")
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


# ==========================================
# 诊断项
# ==========================================


def _check_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return {
            "name": "FFmpeg",
            "level": "error",
            "message": "未找到 FFmpeg",
            "fix": "下载 FFmpeg (https://ffmpeg.org) → 解压 → bin 目录加入系统 PATH 环境变量",
        }
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            ver = r.stdout.split("\n")[0][:60]
            return {"name": "FFmpeg", "level": "ok", "message": f"已安装: {ver}"}
    except Exception:
        pass
    return {"name": "FFmpeg", "level": "warn", "message": f"找到但无法执行: {path}", "fix": "重新安装 FFmpeg 或检查杀毒软件是否拦截"}


def _check_python():
    v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 8):
        return {
            "name": "Python",
            "level": "error",
            "message": f"Python {v} 版本过低",
            "fix": "安装 Python 3.8+ (https://python.org)",
        }
    return {"name": "Python", "level": "ok", "message": f"Python {v}"}


def _check_deps():
    deps = {
        "requests": "pip install requests",
        "PIL": "pip install Pillow",
        "yt_dlp": "pip install yt-dlp",
    }
    missing = []
    for mod, install in deps.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(f"{mod} ({install})")

    if missing:
        return {
            "name": "Python 依赖",
            "level": "error",
            "message": f"缺少: {', '.join(missing)}",
            "fix": "pip install -r requirements.txt",
        }
    return {"name": "Python 依赖", "level": "ok", "message": "核心依赖已就绪"}


def _check_network(host="bilibili.com", label="B站 (bilibili.com)"):
    try:
        import requests
        resp = requests.get(f"https://{host}", timeout=10,
                           headers={"User-Agent": "Mozilla/5.0"})
        # 200-499 都说明服务器可达（401/403/412 只是权限/条件问题）
        return {"name": f"网络: {label}", "level": "ok", "message": f"可连接 {host}"}
    except requests.exceptions.ConnectionError:
        return {
            "name": f"网络: {label}",
            "level": "error",
            "message": f"无法连接 {host}（网络不通）",
            "fix": "检查网络连接或代理设置",
        }
    except requests.exceptions.Timeout:
        return {
            "name": f"网络: {label}",
            "level": "warn",
            "message": f"连接 {host} 超时",
            "fix": "网络较慢，功能可用但可能耗时较长",
        }
    except Exception as e:
        return {
            "name": f"网络: {label}",
            "level": "ok",
            "message": f"可连接 {host}（{_shorten(str(e), 40)}）",
        }


def _check_dir(path, label):
    p = Path(path)
    if not p.exists():
        return {
            "name": f"目录: {label}",
            "level": "warn",
            "message": f"不存在: {path}",
            "fix": "运行程序时将自动创建",
        }
    if not os.access(str(p), os.W_OK):
        return {
            "name": f"目录: {label}",
            "level": "error",
            "message": f"无写入权限: {path}",
            "fix": "检查文件夹权限或更换目录",
        }
    # 统计文件
    video_exts = {".mp4", ".flv", ".mkv", ".mov", ".ts"}
    files = list(p.rglob("*"))
    videos = [f for f in files if f.suffix.lower() in video_exts]
    srts = [f for f in files if f.suffix.lower() == ".srt"]
    ass = [f for f in files if f.suffix.lower() == ".ass"]
    parts = []
    if videos:
        parts.append(f"视频x{len(videos)}")
    if srts:
        parts.append(f"SRTx{len(srts)}")
    if ass:
        parts.append(f"ASSx{len(ass)}")
    info = ", ".join(parts) if parts else "空"
    return {"name": f"目录: {label}", "level": "ok", "message": f"{path} ({info})"}


def _read_config(key_name, default=""):
    """从 app_config.json 读取配置（env var 优先）"""
    env_val = os.environ.get(key_name, "").strip()
    if env_val:
        return env_val
    try:
        config_path = Path(__file__).resolve().parent.parent / "app_config.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return str(data.get(key_name.lower(), default)).strip()
    except Exception:
        pass
    return default


def _check_api_key():
    key = _read_config("SILICONFLOW_API_KEY") or _read_config("api_key")
    if not key:
        return {
            "name": "AI API Key",
            "level": "warn",
            "message": "未配置 DeepSeek API Key",
            "fix": "在 GUI 高级配置 → AI 接口中填写 Key（弹幕分析和 LLM 纠错需要）",
        }
    # 格式检查
    if not key.startswith("sk-"):
        return {
            "name": "AI API Key",
            "level": "warn",
            "message": "API Key 格式可疑（通常以 sk- 开头）",
            "fix": "确认 Key 是否正确复制",
        }
    return {"name": "AI API Key", "level": "ok", "message": f"已配置 ({key[:8]}...{key[-4:]})"}


def _check_stt_api():
    key = _read_config("STT_API_KEY") or _read_config("stt_api_key")
    url = _read_config("STT_BASE_URL") or _read_config("stt_base_url")
    if not key:
        return {
            "name": "STT 接口",
            "level": "info",
            "message": "未配置 STT API Key（必剪失败时将无法回退到 Whisper）",
            "fix": "在 GUI 高级配置 → STT 接口中填写 OpenAI Whisper API 地址和 Key",
        }
    if not url:
        return {
            "name": "STT 接口",
            "level": "warn",
            "message": "STT Key 已配置但接口地址为空",
            "fix": "填写 STT 接口地址，例如 https://api.openai.com/v1",
        }
    return {"name": "STT 接口", "level": "ok", "message": f"已配置 ({key[:8]}...{key[-4:]}) → {url}"}


def _check_yutto():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yutto", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return {"name": "yutto (B站下载)", "level": "ok", "message": "已安装"}
    except Exception:
        pass
    return {
        "name": "yutto (B站下载)",
        "level": "warn",
        "message": "yutto 未安装或不可用",
        "fix": "pip install yutto",
    }


def _check_bcut_api():
    """快速检测必剪 ASR 接口可用性"""
    try:
        import requests
        resp = requests.get(
            "https://member.bilibili.com/x/bcut/rubick-interface/resource/create",
            headers={"User-Agent": "Bilibili/1.0.0"},
            timeout=10,
        )
        # 这个接口需要 POST，GET 会返回 405/404，但只要不是连接超时就说明可达
        if resp.status_code in (404, 405, 400):
            return {"name": "必剪 ASR 接口", "level": "ok", "message": "B站接口可达"}
        return {"name": "必剪 ASR 接口", "level": "ok", "message": f"可达 (HTTP {resp.status_code})"}
    except Exception as e:
        return {
            "name": "必剪 ASR 接口",
            "level": "warn",
            "message": f"无法连接: {_shorten(str(e), 80)}",
            "fix": "B站接口可能暂时不可用，将自动回退到 Whisper API",
        }


def _check_video_codec(video_path=None):
    """检查视频编码是否兼容"""
    if not video_path:
        return {"name": "视频编码", "level": "info", "message": "未指定视频"}
    if not os.path.exists(video_path):
        return {"name": "视频编码", "level": "error", "message": f"文件不存在: {video_path}"}

    if not shutil.which("ffprobe"):
        return {"name": "视频编码", "level": "info",
                "message": "ffprobe 未安装（通常随 FFmpeg 一起安装）",
                "fix": "重新安装 FFmpeg 完整版 (https://ffmpeg.org)"}

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            info = json.loads(r.stdout)
            fmt = info.get("format", {})
            duration = float(fmt.get("duration", 0))
            size_mb = float(fmt.get("size", 0)) / (1024 * 1024)
            streams = info.get("streams", [])
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            video_streams = [s for s in streams if s.get("codec_type") == "video"]

            issues = []
            if not audio_streams:
                issues.append("无音频流（无法生成字幕）")
            if not video_streams and not audio_streams:
                issues.append("无可播放的流")
            if size_mb < 1:
                issues.append("文件过小，可能不完整")

            if issues:
                return {
                    "name": "视频编码",
                    "level": "error",
                    "message": f"{Path(video_path).name}: {', '.join(issues)}",
                    "fix": "检查视频文件是否完整下载",
                }

            audio_codec = audio_streams[0].get("codec_name", "?") if audio_streams else "?"
            return {
                "name": "视频编码",
                "level": "ok",
                "message": f"{Path(video_path).name}: {duration/60:.0f}分钟, {size_mb:.0f}MB, 音频={audio_codec}",
            }
    except Exception as e:
        pass
    return {"name": "视频编码", "level": "info", "message": "无法检测视频详情（ffprobe 可能未安装）"}


# ==========================================
# 工具函数
# ==========================================

def _shorten(text, max_len):
    return text if len(text) <= max_len else text[:max_len-3] + "..."


def classify_error(exception, context=""):
    """分类错误并给出修复建议"""
    msg = str(exception).lower()
    etype = type(exception).__name__

    # 网络类
    if any(w in msg for w in ["timeout", "timed out", "connection", "connect", "network", "unreachable"]):
        return {
            "category": "网络",
            "reason": f"无法连接服务器 ({_shorten(str(exception), 100)})",
            "fix": "检查网络连接、代理设置，或服务器可能暂时不可用",
        }
    # 认证类
    if any(w in msg for w in ["401", "403", "unauthorized", "forbidden", "permission denied", "invalid key"]):
        return {
            "category": "认证",
            "reason": f"API 认证失败 ({_shorten(str(exception), 100)})",
            "fix": "检查 API Key 是否正确、是否过期、是否有余额",
        }
    # FFmpeg 类
    if "ffmpeg" in msg or "ffprobe" in msg:
        return {
            "category": "FFmpeg",
            "reason": f"FFmpeg 执行失败 ({_shorten(str(exception), 100)})",
            "fix": "确认 FFmpeg 已安装并加入 PATH，视频文件未损坏",
        }
    # 文件类
    if any(w in msg for w in ["not found", "no such file", "filenotfound", "不存在"]):
        return {
            "category": "文件",
            "reason": f"文件未找到 ({_shorten(str(exception), 100)})",
            "fix": f"确认 {context} 路径正确，文件未被移动或删除",
        }
    # JSON 类
    if "json" in msg or etype == "JSONDecodeError":
        return {
            "category": "数据格式",
            "reason": f"AI 返回格式异常 ({_shorten(str(exception), 100)})",
            "fix": "AI 接口可能返回了非 JSON 内容，请查看日志中的原始返回",
        }
    # 通用
    return {
        "category": "其他",
        "reason": f"{etype}: {_shorten(str(exception), 120)}",
        "fix": "查看详细日志确认原因",
    }


# ==========================================
# 主入口
# ==========================================

def run_diagnostics(input_dir=None):
    """运行全部诊断，返回结构化结果列表"""
    results = []

    # 基础环境
    results.append(_check_python())
    results.append(_check_ffmpeg())
    results.append(_check_deps())

    # 网络
    results.append(_check_network("bilibili.com", "B站"))
    # DeepSeek 检查：有 Key 才测连通性，没 Key 不测
    api_key = _read_config("SILICONFLOW_API_KEY") or _read_config("api_key")
    if api_key:
        results.append(_check_network("api.deepseek.com", "DeepSeek API"))
    else:
        results.append({"name": "网络: DeepSeek API", "level": "info",
                         "message": "未配置 Key，跳过连通测试"})

    # 目录
    project_root = Path(__file__).resolve().parent.parent
    default_input = input_dir or str(project_root / "workspace" / "video_input")
    default_output = str(project_root / "workspace" / "clip_output")
    results.append(_check_dir(default_input, "素材输入"))
    results.append(_check_dir(default_output, "切片输出"))

    # API 配置
    results.append(_check_api_key())
    results.append(_check_stt_api())

    # 工具
    results.append(_check_yutto())
    results.append(_check_bcut_api())

    # 视频检测
    if input_dir:
        video_exts = {".mp4", ".flv", ".mkv", ".mov", ".ts"}
        videos = [f for f in Path(input_dir).rglob("*") if f.suffix.lower() in video_exts]
        if videos:
            videos.sort(key=lambda f: f.stat().st_size, reverse=True)
            results.append(_check_video_codec(str(videos[0])))

    return results


def print_diagnostics(results):
    """格式化打印诊断结果"""
    icons = {"ok": "  ✓", "warn": "  ⚠", "error": "  ✗", "info": "  ℹ"}
    for r in results:
        icon = icons.get(r["level"], "  ?")
        print(f"{icon} {r['name']}: {r['message']}")
        if r.get("fix"):
            print(f"     → {r['fix']}")


def diagnostics_summary(results):
    """返回诊断摘要: (ok_count, warn_count, error_count, all_pass)"""
    ok = sum(1 for r in results if r["level"] == "ok")
    warn = sum(1 for r in results if r["level"] == "warn")
    err = sum(1 for r in results if r["level"] == "error")
    return ok, warn, err, (err == 0)


if __name__ == "__main__":
    results = run_diagnostics()
    print_diagnostics(results)
    ok, warn, err, passed = diagnostics_summary(results)
    print(f"\n总计: {ok} 通过, {warn} 警告, {err} 错误")
    print("整体状态:", "✓ 就绪" if passed else "✗ 存在问题需要修复")
