import json
import os
import re
from pathlib import Path

from core.auto_detect import auto_detect_files
from core.metadata import load_source_meta, write_source_meta
from core.video_processor import VideoProcessor

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_ROOT = PROJECT_ROOT / "workspace" / "video_input"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "workspace" / "clip_output"
DEFAULT_FONT_PATH = PROJECT_ROOT / "assets" / "font" / "WenYue-XinQingNianTi-W8-J-2.otf"


def _env_path(name, default_path):
    return os.environ.get(name, str(default_path))


def _contains_media_files(folder_path):
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return False

    video_exts = {".mp4", ".flv", ".mkv", ".mov", ".ts"}
    has_video = any(child.is_file() and child.suffix.lower() in video_exts for child in folder.iterdir())
    has_srt = any(child.is_file() and child.suffix.lower() == ".srt" for child in folder.iterdir())
    return has_video and has_srt


def _resolve_input_dir(input_dir):
    candidate = Path(input_dir)
    if not candidate.exists():
        return str(candidate)

    if candidate.is_file():
        return str(candidate.parent)

    if _contains_media_files(candidate):
        return str(candidate)

    session_dirs = [path for path in candidate.iterdir() if path.is_dir() and _contains_media_files(path)]
    if not session_dirs:
        return str(candidate)

    session_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    selected_dir = session_dirs[0]
    print(f"📁 检测到输入根目录，自动选择最近的素材目录: {selected_dir.name}")
    return str(selected_dir)

# ==========================================
# 1. 用户配置区域
# ==========================================

CONFIG = {
    # 路径配置集中管理，路径不要更改
    # 数据源文件路径 (里面含有JSON数组，用于确定剪辑片段)
    "data_source": "Data_source.txt",

    # 输入文件夹路径, 程序会自动在此文件夹下查找：
    # 1. 唯一的视频文件 (.mp4, .flv, .mkv, .mov, .ts)
    # 2. 唯一的字幕文件 (.srt)
    # 3. 输出时会自动提取这个文件夹的名字，在 output_dir 下创建同名文件夹
    "input_dir": _env_path("AUTOCLIP_INPUT_DIR", DEFAULT_INPUT_ROOT),

    "output_dir": _env_path("AUTOCLIP_OUTPUT_DIR", DEFAULT_OUTPUT_ROOT),

    # --- 字幕缓冲配置 (按句子数量) ---
    "padding": {
        "pre_sentences": 8,   # 片段向前延伸的句子数量
        "post_sentences": 5   # 片段向后延伸的句子数量
    },

    # --- [封面字体配置] ---
    "font_path": str(DEFAULT_FONT_PATH),

    # --- [封面样式配置] (可由 GUI 封面配置或环境变量覆盖) ---
    "cover": {
        "count": int(os.environ.get("AUTOCLIP_COVER_COUNT", "5")),
        "active_style": os.environ.get("AUTOCLIP_COVER_STYLE", "style1"),
        "images": [
            # {"path": r"image.png", "x": 0.5, "y": 0.5, "anchor": "center", "size": (2000, 1200), "opacity": 1}
        ],
        "style1": {
            "name": "上白下黄震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 255),
            "title_bottom_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style2": {
            "name": "上黄下白震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 0),
            "title_bottom_color": (255, 225, 255),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style3": {
            "name": "居中大字醒目风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.7,
            "title_size": 180,
            "title_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 10,
            "show_summary": False
        },
        "style4": {
            "name": "艺术简洁风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.5,
            "title_size": 180,
            "title_color": (255, 255, 255),
            "title_stroke_color": (50, 50, 50),
            "title_stroke_width": 8,
            "gradient_start_y": 0.0,
            "gradient_opacity": 150,
            "show_summary": False,
            "blur_background": True,
            "blur_radius": 3
        }
    },

    # --- [视频字幕样式 (ASS)] ---
    "subtitle": {
        # 视频方向设置，填写：
        # "horizontal" = 横屏 (1920x1080)（B站经典风格）
        # "vertical"   = 竖屏 (1080x1920)（类似于抖音）
        # 如果是横屏直播就填"horizontal"，竖屏直播就填"vertical"。如果这里设置错了，那么字幕会变得异常大或者异常小
        "orientation": "horizontal",

        # 视频字幕字体（使用前要在自己系统里安装字体，否则系统会使用默认字体）
        "font_family": "WenYue XinQingNianTi (Authorization Required) W8-J", # 新青年体（推荐）
        # "font_family": "084-SSZhuangYuanTi",  # 上首状元体
        # "font_family": "Jiyucho",  # 自由体

        "font_size": 120,          # 字体大小  (推荐为120)
        "outline_width": 7,        # 描边宽度 （推荐为7）
        "shadow_depth": 2,         # 阴影深度  (推荐为2)
        "margin_v": 50,            # 字幕和画面底部的距离（推荐为50）

        # 字幕样式：
        # 黄字黑色描边（通用）
        "primary_color": "&H0000E1FF",
        "outline_color": "&H00000000",

        # 白字黑色描边（通用）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00000000",

        # 嘉然专属（粉色 #FF69B4）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00B469FF",

        # "primary_color": "&H00B469FF",
        # "outline_color": "&H00FFFFFF",

        # 贝拉专属（紫色 #9B59B6）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00B6599B",

        # "primary_color": "&H00B6599B",
        # "outline_color": "&H00FFFFFF",

        # 乃琳专属（蓝色 #3498DB）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00DB9834",

        # "primary_color": "&H00DB9834",
        # "outline_color": "&H00FFFFFF",

        # A-SOUL团体（深蓝 #006AFF）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00FF6A00",

        # "primary_color": "&H00FF6A00",
        # "outline_color": "&H00FFFFFF",

        # 心宜专属（金色 #FFD700）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H0000D7FF",

        # "primary_color": "&H0000D7FF",
        # "outline_color": "&H00FFFFFF",

        # 思诺专属（银色 #C0C0C0）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00C0C0C0",

        # "primary_color": "&H00C0C0C0",
        # "outline_color": "&H00FFFFFF",
    },
}

# ==========================================
# helpers
# ==========================================

def apply_config_overrides(overrides):
    if not overrides:
        return
    def deep_update(target, updates):
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                deep_update(target[key], value)
            else:
                target[key] = value
    deep_update(CONFIG, overrides)

# ==========================================
# 2. 主程序入口
# ==========================================

def run_single_clip(
    clip_data,
    output_dir=None,
    source_video=None,
    srt_file=None,
    input_dir=None,
    config_overrides=None,
    force_regen_ass=False
):
    if config_overrides:
        apply_config_overrides(config_overrides)

    if source_video:
        CONFIG['source_video'] = source_video
    if srt_file is not None:
        CONFIG['srt_file'] = srt_file
    if input_dir:
        CONFIG['input_dir'] = _resolve_input_dir(input_dir)

    if not CONFIG.get('source_video'):
        if output_dir:
            meta = load_source_meta(output_dir)
            if meta:
                CONFIG['source_video'] = meta.get('source_video')
                CONFIG['srt_file'] = meta.get('srt_file')

    if not CONFIG.get('source_video'):
        if input_dir:
            video_file, detected_srt = auto_detect_files(input_dir)
            CONFIG['source_video'] = video_file
            CONFIG['srt_file'] = detected_srt
        else:
            print("❌ 未找到视频路径，请先运行 Auto_clip.py")
            return

    if output_dir:
        CONFIG['output_dir'] = output_dir

    processor = VideoProcessor(CONFIG, input_dir=CONFIG.get('input_dir'))
    processor.process_clip(
        1,
        clip_data,
        output_dir_override=output_dir,
        generate_cover=True,
        force_regen_ass=force_regen_ass
    )


def main():
    # 1. 检测文件（字幕纠错已由 app_launcher 步骤完成，此处不再重复）
    input_dir = _resolve_input_dir(CONFIG['input_dir'])
    CONFIG['input_dir'] = input_dir
    video_file, srt_file = auto_detect_files(input_dir)

    CONFIG['source_video'] = video_file
    CONFIG['srt_file'] = srt_file

    # ================= 自动更新输出路径 =================
    # 镜像输入目录的子文件夹结构（如 心宜/6月26日xxx/ → clip_output/心宜/6月26日xxx/）
    from core.file_utils import sanitize_filename
    video_path = Path(video_file)
    input_base = Path(input_dir)
    try:
        rel_dir = video_path.parent.relative_to(input_base)
    except ValueError:
        rel_dir = Path(".")
    video_stem = video_path.stem
    folder_name = sanitize_filename(video_stem)
    if str(rel_dir) != ".":
        CONFIG['output_dir'] = os.path.join(CONFIG['output_dir'], str(rel_dir), folder_name)
    else:
        CONFIG['output_dir'] = os.path.join(CONFIG['output_dir'], folder_name)

    # ----------------- 清理逻辑 -----------------
    output_path_obj = Path(CONFIG['output_dir'])
    if output_path_obj.exists():
        print("🧹 检测到输出目录已存在，正在清理视频和封面 (保留 .ass 字幕)...")
        for file_path in output_path_obj.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in ['.mp4', '.mkv', '.flv', '.jpg', '.png', '.jpeg']:
                try:
                    file_path.unlink()
                except Exception as e:
                    print(f"⚠️ 无法删除文件 {file_path.name}: {e}")
    else:
        output_path_obj.mkdir(parents=True, exist_ok=True)
        print("✅ 输出目录已创建")

    write_source_meta(output_path_obj, CONFIG.get('source_video'), CONFIG.get('srt_file'))

    if not os.path.exists(CONFIG['source_video']):
        print(f"❌ 未找到视频文件: {CONFIG['source_video']}")
        return

    data_source_path = CONFIG['data_source']
    if not os.path.exists(data_source_path):
        # 回退到 input_dir 下查找
        alt_path = os.path.join(input_dir, os.path.basename(data_source_path) or "Data_source.txt")
        if os.path.exists(alt_path):
            data_source_path = alt_path
        else:
            print(f"未找到数据源文件: {data_source_path}")
            print(f"也未找到: {alt_path}")
            print("请先运行弹幕分析(第3步)或在 input_dir 中放入 Data_source.txt")
            return

    try:
        with open(data_source_path, 'r', encoding='utf-8') as f:
            raw_text = f.read()
        json_match = re.search(r'\[.*?\]', raw_text, re.S) if not raw_text.strip().startswith('[') else re.search(r'\[.*\]', raw_text, re.S)
        if not json_match:
            print("❌ 数据源文件中未找到 JSON 数组格式 (以 '[' 开头，以 ']' 结尾)")
            return
        clips = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"❌ JSON 格式错误: {e}")
        print("💡 请检查 Data_source.txt 里的逗号、引号是否正确。")
        return
    except Exception as e:
        print(f"❌ 读取数据源时发生未知错误: {e}")
        return

    print("=" * 60)
    print("🎨 视频剪辑与封面生成工具 (自动检测 + 自动归档模式)")
    print("=" * 60)
    print(f"数据来源: {data_source_path}")
    print(f"视频来源: {video_file}")
    print(f"输出目录: {CONFIG['output_dir']}")
    print(f"待处理片段数: {len(clips)}")
    print("=" * 60)

    index_width = max(2, len(str(len(clips))))
    processor = VideoProcessor(CONFIG, input_dir=CONFIG.get('input_dir'))
    for i, clip in enumerate(clips, 1):
        # 检查终止标志（由 GUI 设置）
        if os.environ.get("AUTOCLIP_STOP") == "1":
            print(f"⏹ 用户终止，已处理 {i-1}/{len(clips)} 个片段")
            break
        try:
            processor.process_clip(
                i,
                clip,
                generate_cover=True,
                index_width=index_width,
                force_regen_ass=False
            )
        except Exception as e:
            print(f"❌ 处理片段 {i} 时出错: {e}")

    print("\n" + "=" * 60)
    print(f"✅ 所有片段处理完毕! 文件保存在: {CONFIG['output_dir']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
