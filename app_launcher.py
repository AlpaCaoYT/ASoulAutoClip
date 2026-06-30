import os
import re
import sys
import io
import json
import time
import shutil
import subprocess
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from datetime import datetime
from tkinterdnd2 import TkinterDnD

# === GPU CUDA DLL 路径注入（必须在 faster-whisper 加载前执行）===
_py_base = Path(sys.executable).parent / "Lib" / "site-packages" / "nvidia"
if _py_base.exists():
    for _d in _py_base.rglob("bin"):
        _dir = str(_d)
        try:
            os.add_dll_directory(_dir)
        except Exception:
            pass
        # 预加载 cublas DLL（确保 ctranslate2 能找到）
        for _dll in _d.glob("cublas64_*.dll"):
            try:
                import ctypes
                ctypes.CDLL(str(_dll))
            except Exception:
                pass
# ================================================================

# Windows GBK 控制台无法输出 emoji，统一用 LogWriter 拦截所有 print
# 原理：替换 sys.stdout 为自定义 writer，输出到日志文件（UTF-8）+ GUI Text 控件
class _LogWriter(io.TextIOBase):
    """拦截所有 print() / sys.stdout.write() 输出，安全写入日志文件和 GUI。"""
    _app = None  # 由 AppLauncher.__init__ 注入

    @classmethod
    def bind(cls, app):
        cls._app = app

    def write(self, text):
        app = self._app
        if app is not None and text:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    try:
                        app._write_to_log(stripped)
                    except Exception:
                        pass
        elif app is None and text:
            # 启动阶段 crash → 直接写到真实 stderr（用户可见）
            try:
                _sys_stderr_backup.write(text)
                _sys_stderr_backup.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"

_sys_stdout_backup = sys.stdout
_sys_stderr_backup = sys.stderr
sys.stdout = _LogWriter()
sys.stderr = _LogWriter()

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "app_config.json"
LOG_DIR = PROJECT_ROOT / "workspace" / "logs"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "素材"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "切片输出"


def _load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class AppLauncher(TkinterDnD.Tk):
    # 发言人类型及配色
    SPEAKER_TYPES = [
        ("未指定", "#888888"),
        ("嘉然",   "#FF69B4"),
        ("贝拉",   "#9B59B6"),
        ("乃琳",   "#3498DB"),
        ("心宜",   "#FF1493"),
        ("思诺",   "#EEA0D7"),
        ("旁白",   "#FFFFFF"),
        ("一起说", "#FF8C00"),
        ("ASOUL",  "#006AFF"),
    ]
    SPEAKER_COLORS = dict(SPEAKER_TYPES)

    def __init__(self):
        super().__init__()
        # 日志文件（必须在 _LogWriter.bind 前创建，防止早期 print 找不到路径）
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_name = datetime.now().strftime("session_%Y%m%d_%H%M%S.log")
        self._log_path = LOG_DIR / log_name
        _LogWriter.bind(self)  # 绑定日志重定向目标

        self.title("ZhiJiangAutoClip 工作台")
        self.geometry("1000x820")
        self.minsize(800, 600)

        # 现代主题（sv_ttk Sun Valley，Windows 11 风格）
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
        except ImportError:
            pass  # 未安装时回退默认主题

        saved = _load_config()

        input_val = saved.get("input_dir", str(DEFAULT_INPUT_DIR))
        if not os.path.isdir(input_val):
            input_val = str(DEFAULT_INPUT_DIR)
        output_val = saved.get("output_dir", str(DEFAULT_OUTPUT_DIR))
        if not os.path.isdir(output_val):
            output_val = str(DEFAULT_OUTPUT_DIR)
        self.input_dir_var = tk.StringVar(value=input_val)
        self.output_dir_var = tk.StringVar(value=output_val)
        self.api_key_var = tk.StringVar(value=os.environ.get("SILICONFLOW_API_KEY", saved.get("api_key", "")))
        self.api_base_var = tk.StringVar(value=os.environ.get("SILICONFLOW_BASE_URL", saved.get("api_base", "https://api.deepseek.com/v1/chat/completions")))
        self.api_model_var = tk.StringVar(value=os.environ.get("SILICONFLOW_MODEL", saved.get("api_model", "deepseek-v4-pro")))
        self.bvid_var = tk.StringVar(value=os.environ.get("BILIBILI_VIDEO_INPUT", saved.get("bvid", "")))
        self.sessdata_var = tk.StringVar(value=os.environ.get("BILIBILI_SESSDATA", saved.get("sessdata", "")))

        self._running = False
        self._stop_requested = False
        self._auto_mode = tk.BooleanVar(value=saved.get("auto_mode", False))
        self._advanced_showing = False
        self._mode = tk.StringVar(value="bv")  # "bv" 或 "video"
        self._step_done = {1: False, 2: False, 3: False, 4: False}
        self._step_widgets = {}
        self._error_count = {}  # 错误分类计数
        self._converted_srts = set()  # SRT 繁简转换缓存，避免重复处理

        # 成员出场标记（默认全部出场）
        saved_members = saved.get("member_status", {})
        self._member_vars = {}
        for name in ["嘉然", "贝拉", "乃琳", "心宜", "思诺"]:
            self._member_vars[name] = tk.BooleanVar(value=saved_members.get(name, True))

        # 封面配置
        self._cover_style_var = tk.StringVar(value=saved.get("cover_style", "style1"))
        self._cover_count_var = tk.StringVar(value=str(saved.get("cover_count", 5)))

        # STT（语音识别）接口配置
        self._stt_key_var = tk.StringVar(value=saved.get("stt_api_key", ""))
        self._stt_url_var = tk.StringVar(value=saved.get("stt_base_url", "https://api.openai.com/v1"))
        self._stt_model_var = tk.StringVar(value=saved.get("stt_model", "whisper-1"))

        # 本地 Whisper 模型选择
        self._whisper_model_var = tk.StringVar(value=saved.get("whisper_model", "small"))

        # 分析后暂停
        self._pause_var = tk.BooleanVar(value=saved.get("pause_after_analysis", False))

        # 分析模式
        self._analysis_mode = tk.StringVar(value=saved.get("analysis_mode", "fuzzy"))

        self._build_ui()
        self._check_environment()

    # ==========================================
    # UI 构建
    # ==========================================

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Step.TButton", font=("Microsoft YaHei UI", 10), padding=8)
        style.configure("All.Bold.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=10)

        # 外层：日志区固定底部，配置区滚动占满剩余空间
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        # 日志区 — 固定高度，优先放底部
        log_box = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_box.pack(fill=tk.X, side=tk.BOTTOM)
        self.log_text = tk.Text(log_box, height=10, wrap=tk.WORD, relief=tk.FLAT,
                                bg="#151515", fg="#eaeaea", insertbackground="#ffffff",
                                font=("Consolas", 11))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        # 右键菜单 + Ctrl+C 复制
        def _copy_log():
            try:
                if self.log_text.tag_ranges("sel"):
                    text = self.log_text.selection_get()
                else:
                    text = self.log_text.get("1.0", "end-1c")
                self.clipboard_clear()
                self.clipboard_append(text)
            except Exception:
                pass
        self._log_menu = tk.Menu(self.log_text, tearoff=0)
        self._log_menu.add_command(label="复制", command=_copy_log)
        self._log_menu.add_command(label="全选", command=lambda: (
            self.log_text.tag_add("sel", "1.0", "end"),
            self.log_text.mark_set("insert", "1.0"),
            self.log_text.see("insert"),
        ))
        self.log_text.bind("<Button-3>", lambda e: self._log_menu.tk_popup(e.x_root, e.y_root))
        self.log_text.bind("<Control-c>", lambda e: _copy_log())
        self.log_text.bind("<Control-C>", lambda e: _copy_log())

        # 配置区 — 滚动，占满剩余空间
        canvas = tk.Canvas(outer, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        self._content_frame = ttk.Frame(canvas, padding=16)
        self._content_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._canvas_win = canvas.create_window((0, 0), window=self._content_frame, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)

        def _set_canvas_width(event):
            canvas.itemconfig(self._canvas_win, width=event.width)
        canvas.bind("<Configure>", _set_canvas_width)

        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        root = self._content_frame

        # 标题
        header = ttk.Frame(root)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="ZhiJiangAutoClip 工作台", style="Title.TLabel").pack(anchor="w")

        # 环境状态条
        self.status_frame = ttk.Frame(root)
        self.status_frame.pack(fill=tk.X, pady=(0, 8))
        self.ffmpeg_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.ffmpeg_label.pack(side=tk.LEFT, padx=(0, 16))
        self.python_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.python_label.pack(side=tk.LEFT, padx=(0, 16))
        self.input_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.input_label.pack(side=tk.LEFT)
        self.diag_btn = ttk.Button(self.status_frame, text="故障检测", width=9,
                                    command=self._show_diagnostics)
        self.diag_btn.pack(side=tk.RIGHT)

        # 素材路径
        paths_box = ttk.LabelFrame(root, text="素材路径", padding=10)
        paths_box.pack(fill=tk.X, pady=(0, 8))
        paths_box.columnconfigure(0, weight=1)

        # BV号（始终显示，便于两种模式共用）
        row_idx = 0
        self._row(paths_box, "BV号/链接", self.bvid_var, row_idx); row_idx += 1
        self._row(paths_box, "SESSDATA", self.sessdata_var, row_idx, show="*"); row_idx += 1
        sess_help = ttk.Frame(paths_box)
        sess_help.grid(row=row_idx, column=0, sticky="ew", pady=(0, 4)); row_idx += 1
        ttk.Label(sess_help, text="SESSDATA 是 B站登录凭证，不填也能下载，填了能下会员视频",
                  foreground="#888", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        ttk.Button(sess_help, text="怎么获取？", width=9,
                   command=lambda: messagebox.showinfo("获取 SESSDATA",
                       "1. 用浏览器登录 Bilibili\n"
                       "2. 按 F12 → 应用/Application\n"
                       "3. 左侧 Cookies → bilibili.com\n"
                       "4. 找到 SESSDATA，复制值\n\n"
                       "不填也能正常下载普通视频。")).pack(side=tk.LEFT, padx=(4, 0))

        # 输入目录
        in_top = ttk.Frame(paths_box)
        in_top.grid(row=row_idx, column=0, sticky="ew", pady=3); row_idx += 1
        ttk.Label(in_top, text="素材目录", width=8).pack(side=tk.LEFT)
        self._input_display_var = tk.StringVar()
        self.input_combo = ttk.Combobox(in_top, textvariable=self._input_display_var, state="readonly")
        self.input_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        in_btns = ttk.Frame(paths_box)
        in_btns.grid(row=row_idx, column=0, sticky="ew", pady=(0, 3)); row_idx += 1
        ttk.Button(in_btns, text="浏览文件夹", command=self.choose_input_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(in_btns, text="刷新", command=self._refresh_input_list).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(in_btns, text="整理文件", command=self._organize_files).pack(side=tk.LEFT, padx=(0, 6))
        self._combo_count_label = ttk.Label(in_btns, text="", foreground="#888")
        self._combo_count_label.pack(side=tk.LEFT)
        self._refresh_input_list()

        # 视频选择
        vid_row = ttk.Frame(paths_box)
        vid_row.grid(row=row_idx, column=0, sticky="ew", pady=3); row_idx += 1
        ttk.Label(vid_row, text="选中视频", width=8).pack(side=tk.LEFT)
        self._video_var = tk.StringVar()
        self._video_combo = ttk.Combobox(vid_row, textvariable=self._video_var, state="readonly")
        self._video_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._video_combo.bind("<<ComboboxSelected>>", self._on_video_select)
        self._video_list = []  # [(path, display_name)]

        # 字幕文件选择
        srt_row = ttk.Frame(paths_box)
        srt_row.grid(row=row_idx, column=0, sticky="ew", pady=3); row_idx += 1
        ttk.Label(srt_row, text="字幕文件", width=8).pack(side=tk.LEFT)
        self._srt_var = tk.StringVar()
        self._srt_combo = ttk.Combobox(srt_row, textvariable=self._srt_var, state="readonly")
        self._srt_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(srt_row, text="浏览", width=4,
                   command=lambda: self._browse_file("字幕", [("SRT 字幕", "*.srt")], self._srt_var, self._srt_combo)
                   ).pack(side=tk.LEFT, padx=(4, 0))
        self._srt_list = []

        # 弹幕文件选择
        ass_row = ttk.Frame(paths_box)
        ass_row.grid(row=row_idx, column=0, sticky="ew", pady=3); row_idx += 1
        ttk.Label(ass_row, text="弹幕文件", width=8).pack(side=tk.LEFT)
        self._ass_var = tk.StringVar()
        self._ass_combo = ttk.Combobox(ass_row, textvariable=self._ass_var, state="readonly")
        self._ass_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(ass_row, text="浏览", width=4,
                   command=lambda: self._browse_file("弹幕", [("ASS 弹幕", "*.ass")], self._ass_var, self._ass_combo)
                   ).pack(side=tk.LEFT, padx=(4, 0))
        self._ass_list = []

        # 素材预览
        self._video_preview_label = ttk.Label(paths_box, text="", foreground="#4a4",
                                               font=("Microsoft YaHei UI", 8))
        self._video_preview_label.grid(row=row_idx, column=0, sticky="ew", pady=(0, 0)); row_idx += 1

        # 输出目录
        self._row_with_browse(paths_box, "输出目录", self.output_dir_var, self.choose_output_dir, row_idx); row_idx += 1

        # 文件拖放区
        self.drop_frame = tk.Frame(paths_box, bg="#1e1e1e", height=52, relief=tk.GROOVE, bd=1)
        self.drop_frame.grid(row=row_idx, column=0, sticky="ew", pady=(6, 0)); row_idx += 1
        self.drop_frame.grid_propagate(False)

        self.drop_label = tk.Label(self.drop_frame,
            text="将视频/字幕/弹幕文件拖放到这里（自动复制到输入目录）",
            bg="#1e1e1e", fg="#777", font=("Microsoft YaHei UI", 8))
        self.drop_label.pack(expand=True)

        self.drop_frame.drop_target_register("DND_Files")
        self.drop_frame.dnd_bind("<<Drop>>", self._on_file_drop)
        self.drop_label.drop_target_register("DND_Files")
        self.drop_label.dnd_bind("<<Drop>>", self._on_file_drop)

        # 操作流程
        actions = ttk.LabelFrame(root, text="操作流程", padding=10)
        actions.pack(fill=tk.X, pady=(0, 8))

        # 路径选择
        mode_frame = ttk.Frame(actions)
        mode_frame.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(mode_frame, text="素材来源：").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(mode_frame, text="BV号下载", variable=self._mode, value="bv",
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Radiobutton(mode_frame, text="已有视频（必剪免费ASR）", variable=self._mode, value="video",
                        command=self._on_mode_change).pack(side=tk.LEFT)

        self.mode_hint = ttk.Label(actions, text="", foreground="#888",
                                    font=("Microsoft YaHei UI", 8))
        self.mode_hint.pack(fill=tk.X, pady=(0, 6))
        self._update_mode_hint()

        # 成员出场标记
        member_frame = ttk.LabelFrame(actions, text="成员出场标记（影响弹幕分析和提示词）", padding=10)
        member_frame.pack(fill=tk.X, pady=(0, 6))

        member_row = ttk.Frame(member_frame)
        member_row.pack(fill=tk.X)
        for i, (name, var) in enumerate(self._member_vars.items()):
            cb = ttk.Checkbutton(member_row, text=name, variable=var)
            cb.pack(side=tk.LEFT, padx=(0, 16))
        # 全选/取消按钮
        def _toggle_all():
            all_on = all(v.get() for v in self._member_vars.values())
            for v in self._member_vars.values():
                v.set(not all_on)
            self._preview_output_folder()
        ttk.Button(member_row, text="全选/取消", width=9,
                   command=_toggle_all).pack(side=tk.LEFT, padx=(8, 0))
        # 成员勾选变化时更新文件夹预览
        for var in self._member_vars.values():
            var.trace_add("write", lambda *_: self._preview_output_folder())

        # API 状态条
        api_bar = ttk.Frame(actions)
        api_bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(api_bar, text="AI 接口：").pack(side=tk.LEFT)
        self.api_status_label = ttk.Label(api_bar, text="未配置", foreground="#e44")
        self.api_status_label.pack(side=tk.LEFT, padx=(4, 12))
        ttk.Button(api_bar, text="配置", command=self._open_api_config).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(api_bar, text="测试连接", command=self._test_api).pack(side=tk.LEFT)
        self._update_api_status()

        # 步骤列表
        steps_header = ttk.Frame(actions)
        steps_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(steps_header, text="步骤", font=("Microsoft YaHei UI", 10, "bold"), width=6, anchor="w").pack(side=tk.LEFT)
        ttk.Label(steps_header, text="状态", font=("Microsoft YaHei UI", 10, "bold"), width=8, anchor="w").pack(side=tk.LEFT, padx=(0, 8))

        self.steps_frame = ttk.Frame(actions)
        self.steps_frame.pack(fill=tk.X)

        self._build_step_rows()

        # 一键全部
        flow_bottom = ttk.Frame(actions)
        flow_bottom.pack(fill=tk.X, pady=(8, 4))
        self.btn_all = ttk.Button(flow_bottom, text="▶ 一键运行全部流程",
                                   style="All.Bold.TButton",
                                   command=self.run_all)
        self.btn_all.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_stop = ttk.Button(flow_bottom, text="■ 终止",
                                    command=self._stop_run, state="disabled")
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 4))
        self._edit_btn = ttk.Button(flow_bottom, text="编辑切片数据",
                                     command=self._edit_data_source)
        self._edit_btn.pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(flow_bottom, text="校核高光",
                   command=self._edit_highlight_review).pack(side=tk.RIGHT, padx=4)
        ttk.Button(flow_bottom, text="完整字幕",
                   command=self._edit_subtitle_speakers).pack(side=tk.RIGHT, padx=4)
        ttk.Button(flow_bottom, text="Aegisub",
                   command=self._open_in_aegisub).pack(side=tk.RIGHT, padx=2)
        ttk.Button(flow_bottom, text="导入ASS",
                   command=self._import_aegisub_styles).pack(side=tk.RIGHT, padx=2)

        # 配置行：切片数量 + 封面风格
        clip_cfg = ttk.Frame(actions)
        clip_cfg.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(clip_cfg, text="切片数", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        clip_spin = ttk.Spinbox(clip_cfg, textvariable=self._cover_count_var,
                                 from_=1, to=20, width=4)
        clip_spin.pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(clip_cfg, text="封面", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        ttk.Combobox(clip_cfg, textvariable=self._cover_style_var,
                     values=["style1", "style2", "style3", "style4"],
                     state="readonly", width=10).pack(side=tk.LEFT)

        # 模式开关行
        auto_row = ttk.Frame(actions)
        auto_row.pack(fill=tk.X, pady=(8, 2))
        self._auto_cb = ttk.Checkbutton(auto_row, text="全自动模式（出错自动跳过，不弹窗询问）",
                                         variable=self._auto_mode)
        self._auto_cb.pack(side=tk.LEFT)
        self._pause_cb = ttk.Checkbutton(auto_row, text="分析后暂停校核（第3步后暂停，校核后再生成切片）",
                                          variable=self._pause_var)
        self._pause_cb.pack(side=tk.LEFT, padx=(16, 0))

        # 分析模式选择（变量在 __init__ 已创建）
        # 注意: self._analysis_mode 已在 __init__ 中定义
        mode_row = ttk.Frame(actions)
        mode_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(mode_row, text="分析模式:", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text="模式1 模糊（默认，先找高光）", variable=self._analysis_mode,
                        value="fuzzy").pack(side=tk.LEFT, padx=(4, 12))
        ttk.Radiobutton(mode_row, text="模式2 精确（配置发言人后重分析）", variable=self._analysis_mode,
                        value="precise",
                        command=self._on_precise_mode).pack(side=tk.LEFT)

        # 快捷工具
        util_row = ttk.Frame(actions)
        util_row.pack(fill=tk.X)
        ttk.Button(util_row, text="打开输入文件夹",
                   command=lambda: self._open_folder(self.input_dir_var.get())).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="打开输出文件夹",
                   command=lambda: self._open_folder(self.output_dir_var.get())).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="用 Kdenlive 打开",
                   command=self._open_kdenlive).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="查看日志文件",
                   command=lambda: self._open_folder(str(LOG_DIR))).pack(side=tk.LEFT)

        # 高级配置（默认折叠）
        self.advanced_toggle = ttk.Button(root, text="▼ 展开高级配置",
                                           command=self._toggle_advanced)
        self.advanced_toggle.pack(fill=tk.X, pady=(0, 2))

        self.advanced_frame = ttk.Frame(root)

        api_box = ttk.LabelFrame(self.advanced_frame, text="AI 接口配置（弹幕分析 + LLM纠错用）", padding=10)
        api_box.pack(fill=tk.X, pady=(0, 8))
        self._row(api_box, "API Key", self.api_key_var, 0, show="*")
        self._row(api_box, "接口地址", self.api_base_var, 1)
        self._row(api_box, "模型名称", self.api_model_var, 2)

        # STT 接口配置
        stt_box = ttk.LabelFrame(self.advanced_frame, text="STT 接口配置（语音识别，必剪失败时回退）", padding=10)
        stt_box.pack(fill=tk.X, pady=(0, 8))
        self._row(stt_box, "STT API Key", self._stt_key_var, 0, show="*")
        self._row(stt_box, "STT 接口地址", self._stt_url_var, 1)
        self._row(stt_box, "STT 模型", self._stt_model_var, 2)
        stt_hint = ttk.Label(stt_box, text="支持 OpenAI Whisper API 及兼容服务。例: https://api.openai.com/v1，模型 whisper-1",
                             foreground="#888", font=("Microsoft YaHei UI", 8))
        stt_hint.grid(row=3, column=0, sticky="ew", pady=(2, 0))

        # 本地 Whisper 模型选择
        wh_row = ttk.Frame(stt_box)
        wh_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(wh_row, text="本地模型", width=12).pack(side=tk.LEFT)
        wh_combo = ttk.Combobox(wh_row, textvariable=self._whisper_model_var,
                                 values=["tiny", "base", "small", "medium", "large-v3"],
                                 state="readonly", width=12)
        wh_combo.pack(side=tk.LEFT)
        ttk.Label(wh_row, text="tiny=最快 | large-v3=最准(GPU推荐)", foreground="#888",
                  font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(8, 0))

        bvid_box = ttk.LabelFrame(self.advanced_frame, text="B站下载配置（下载弹幕用）", padding=10)
        bvid_box.pack(fill=tk.X, pady=(0, 8))
        self._row(bvid_box, "BV号/链接", self.bvid_var, 0)
        self._row(bvid_box, "SESSDATA", self.sessdata_var, 1, show="*")

        # 封面配置
        cover_box = ttk.LabelFrame(self.advanced_frame, text="封面配置", padding=10)
        cover_box.pack(fill=tk.X, pady=(0, 8))
        cover_row0 = ttk.Frame(cover_box)
        cover_row0.grid(row=0, column=0, sticky="ew", pady=3)
        ttk.Label(cover_row0, text="封面风格", width=12).pack(side=tk.LEFT)
        cover_style_combo = ttk.Combobox(cover_row0, textvariable=self._cover_style_var,
                                          values=["style1", "style2", "style3", "style4"],
                                          state="readonly", width=30)
        cover_style_combo.pack(side=tk.LEFT)
        cover_style_combo.bind("<<ComboboxSelected>>", lambda e: self._update_cover_hint())
        cover_row1 = ttk.Frame(cover_box)
        cover_row1.grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Label(cover_row1, text="封面数量", width=12).pack(side=tk.LEFT)
        cover_count_spin = ttk.Spinbox(cover_row1, textvariable=self._cover_count_var,
                                        from_=1, to=10, width=5)
        cover_count_spin.pack(side=tk.LEFT)
        self._cover_hint = ttk.Label(cover_row1, text="", foreground="#888",
                                      font=("Microsoft YaHei UI", 8))
        self._cover_hint.pack(side=tk.LEFT, padx=(12, 0))
        self._update_cover_hint()

        # 配置编辑 & 工具
        edit_box = ttk.LabelFrame(self.advanced_frame, text="配置编辑 & 工具", padding=10)
        edit_box.pack(fill=tk.X, pady=(0, 8))
        edit_row = ttk.Frame(edit_box)
        edit_row.pack(fill=tk.X)
        ttk.Button(edit_row, text="编辑提示词模板", command=self._edit_prompt).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(edit_row, text="编辑纠错字典", command=self._edit_dict).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(edit_row, text="LLM智能纠错字幕", command=self.run_llm_corrector).pack(side=tk.LEFT)

        # 保存按钮
        save_frame = ttk.Frame(self.advanced_frame)
        save_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(save_frame, text="💾 保存当前配置", command=self._save_config).pack(side=tk.LEFT, padx=(0, 8))
        self.save_hint = ttk.Label(save_frame, text="", foreground="#888")
        self.save_hint.pack(side=tk.LEFT)

        ffmpeg_path = shutil.which("ffmpeg") or ""
        self.log("准备就绪。按照 1→2→3→4 的顺序点击即可。\n"
                 "或者直接点「一键运行全部流程」。\n"
                 "\n"
                 "=== 防杀毒误报提示 ===\n"
                 "生成视频时 FFmpeg 可能被杀毒软件拦截（误判为风险程序）。\n"
                 f"FFmpeg 路径: {ffmpeg_path}\n"
                 "解决方法: 以管理员身份运行 PowerShell，执行：\n"
                 f'  Add-MpPreference -ExclusionPath "{PROJECT_ROOT}"\n'
                 f'  Add-MpPreference -ExclusionPath "{Path(ffmpeg_path).parent if ffmpeg_path else "C:\\ffmpeg\\bin"}"\n'
                 "这是 Windows Defender 的标准白名单操作，安全无风险。");

    def _row_with_browse(self, parent, label, variable, command, row):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", pady=3)
        ttk.Label(frame, text=label, width=12).pack(side=tk.LEFT)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(frame, text="浏览", command=command).pack(side=tk.LEFT)

    def _row(self, parent, label, variable, row, show=None):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", pady=3)
        ttk.Label(frame, text=label, width=12).pack(side=tk.LEFT)
        ttk.Entry(frame, textvariable=variable, show=show).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _toggle_advanced(self):
        if self._advanced_showing:
            self.advanced_frame.pack_forget()
            self.advanced_toggle.config(text="▼ 展开高级配置")
        else:
            self.advanced_frame.pack(fill=tk.X, before=self.advanced_toggle, pady=(0, 2))
            self.advanced_toggle.config(text="▲ 收起高级配置")
        self._advanced_showing = not self._advanced_showing

    # ==========================================
    # 环境检测
    # ==========================================

    def _check_environment(self):
        # FFmpeg
        if shutil.which("ffmpeg"):
            self.ffmpeg_label.config(text="FFmpeg: 已找到", foreground="#4a4")
        else:
            self.ffmpeg_label.config(text="FFmpeg: 未找到!", foreground="#e44")

        # Python
        self.python_label.config(text=f"Python: {sys.version_info.major}.{sys.version_info.minor}", foreground="#4a4")

        # 输入目录
        input_path = Path(self.input_dir_var.get())
        if input_path.exists():
            videos = sorted(
                [f for f in input_path.rglob("*") if f.suffix.lower() in {".mp4", ".flv", ".mkv", ".mov", ".ts"} and "__no_danmaku__" not in str(f)],
                key=lambda f: f.stat().st_size, reverse=True
            )
            srts = [f for f in input_path.rglob("*.srt") if "__no_danmaku__" not in str(f)]
            # 自动繁→简（处理已有字幕）
            for srt in srts:
                self._convert_srt_to_simplified(str(srt))
            ass = [f for f in input_path.rglob("*.ass") if "__no_danmaku__" not in str(f)]

            parts = []
            if videos:
                parts.append(f"视频({len(videos)}个)")
            if srts:
                parts.append(f"字幕({len(srts)}个)")
            if ass:
                parts.append(f"弹幕({len(ass)}个)")

            if parts:
                self.input_label.config(text=f"素材目录: {'，'.join(parts)}", foreground="#4a4")
            else:
                self.input_label.config(text="素材目录: 空（请放入素材）", foreground="#c90")

            # 填充视频/字幕/弹幕选择
            is_bv = self._mode.get() == "bv"

            # 视频
            prev_video = self._video_var.get()
            self._video_list = [(str(v), f"{v.name}  ({v.stat().st_size/1048576:.0f} MB)") for v in videos]
            if is_bv and not videos:
                self._video_list = []
                self._video_var.set("(BV下载后自动匹配)")
                self._video_combo["values"] = ["(BV下载后自动匹配)"]
            elif videos:
                self._video_combo["values"] = [l for _, l in self._video_list]
                # 保持用户之前的选择，不强制跳回第一个
                kept = False
                if prev_video:
                    for path, display in self._video_list:
                        if display == prev_video:
                            for i, (_, d) in enumerate(self._video_list):
                                if d == prev_video:
                                    self._video_combo.current(i)
                                    kept = True
                                    break
                            break
                if not kept:
                    self._video_combo.current(0)
                self._on_video_select()
            else:
                self._video_var.set("(未检测到视频 — 请拖入文件)")
                self._video_combo["values"] = ["(未检测到视频 — 请拖入文件)"]

            # 字幕 — BV模式下也尝试匹配已有文件，匹配不到才显示占位符
            video = self._get_selected_video()
            self._srt_list = [(str(f), f.name) for f in sorted(srts)]
            matched_srt = self._smart_match_file(srts, video.stem, ".srt") if video else None
            if matched_srt:
                self._srt_combo["values"] = [l for _, l in self._srt_list]
                for p, d in self._srt_list:
                    if p == str(matched_srt): self._srt_var.set(d); break
            elif is_bv and not srts:
                self._srt_list = []
                self._srt_var.set("(BV下载后自动匹配)")
                self._srt_combo["values"] = ["(BV下载后自动匹配)"]
            elif self._srt_list and video:
                self._srt_var.set("(未匹配 — 请手动选择)")
                self._srt_combo["values"] = ["(未匹配 — 请手动选择)"] + [l for _, l in self._srt_list]
            else:
                self._srt_var.set("(无字幕 — 将自动生成)")
                self._srt_combo["values"] = ["(无字幕 — 将自动生成)"]

            # 弹幕 — 同上，BV模式下也尝试匹配已有文件
            self._ass_list = [(str(f), f.name) for f in sorted(ass)]
            matched_ass = self._smart_match_file(ass, video.stem, ".ass") if video else None
            if matched_ass:
                self._ass_combo["values"] = [l for _, l in self._ass_list]
                for p, d in self._ass_list:
                    if p == str(matched_ass): self._ass_var.set(d); break
            elif is_bv and not ass:
                self._ass_list = []
                self._ass_var.set("(BV下载后自动匹配)")
                self._ass_combo["values"] = ["(BV下载后自动匹配)"]
            elif self._ass_list and video:
                self._ass_var.set("(未匹配 — 请手动选择)")
                self._ass_combo["values"] = ["(未匹配 — 请手动选择)"] + [l for _, l in self._ass_list]
            else:
                self._ass_var.set("(无弹幕 — 将自动跳过)")
                self._ass_combo["values"] = ["(无弹幕 — 将自动跳过)"]
        else:
            self.input_label.config(text="输入目录: 不存在", foreground="#e44")

        # 更新编辑按钮状态
        self._update_edit_btn()

        # 快速诊断（后台线程，不阻塞 UI）
        threading.Thread(target=self._run_quick_diag, daemon=True).start()

    def _update_edit_btn(self):
        """Data_source.txt 存在时高亮编辑按钮"""
        target = self.input_dir_var.get().strip()
        ds = os.path.join(target, "Data_source.txt")
        if os.path.exists(ds):
            self._edit_btn.configure(text="✎ 编辑切片数据（已就绪）")
        else:
            self._edit_btn.configure(text="编辑切片数据")

    def _run_quick_diag(self):
        """后台静默运行诊断（仅本地检查，不联网避免卡顿）"""
        try:
            import shutil
            ok = True
            if not shutil.which("ffmpeg"):
                ok = False
            input_path = Path(self.input_dir_var.get())
            if not input_path.exists():
                ok = False
            if ok:
                self.diag_btn.configure(text="✓ 本地正常")
                self.after(5000, lambda: self.diag_btn.configure(text="故障检测"))
            else:
                self.diag_btn.configure(text="✗ 点击检测")
        except Exception:
            pass

    def _show_diagnostics(self):
        """打开故障检测窗口（后台运行，不阻塞 UI）"""
        win = tk.Toplevel(self)
        win.title("故障检测")
        win.geometry("750x550")
        win.minsize(600, 400)
        win.transient(self)

        loading = ttk.Label(win, text="正在检测中，请稍候...",
                            font=("Microsoft YaHei UI", 11))
        loading.pack(expand=True)
        self.log("正在后台运行故障检测...")

        def _populate(results):
            loading.destroy()
            from utils.diagnostics import diagnostics_summary
            icons = {"ok": "✓", "warn": "⚠", "error": "✗", "info": "ℹ"}
            colors = {"ok": "#4a4", "warn": "#c90", "error": "#e44", "info": "#888"}
            ok, warn, errs, passed = diagnostics_summary(results)
            ttk.Label(win, text=f"检测完毕: {ok} 通过, {warn} 警告, {errs} 错误 — {'✓ 就绪' if passed else '✗ 存在问题'}",
                      font=("Microsoft YaHei UI", 10, "bold"),
                      foreground="#4a4" if passed else "#e44").pack(pady=(12, 8), padx=12)

            canvas = tk.Canvas(win, highlightthickness=0)
            sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
            sf = ttk.Frame(canvas)
            sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=sf, anchor="nw")
            canvas.configure(yscrollcommand=sb.set)

            for r in results:
                row = ttk.Frame(sf); row.pack(fill=tk.X, padx=12, pady=3)
                tk.Label(row, text=icons.get(r["level"], "?"), fg=colors.get(r["level"], "#999"),
                         font=("Consolas", 11), width=2, anchor="w").pack(side=tk.LEFT)
                ttk.Label(row, text=r["name"], font=("Microsoft YaHei UI", 9, "bold"), width=20).pack(side=tk.LEFT)
                ttk.Label(row, text=r["message"], font=("Microsoft YaHei UI", 9), foreground="#aaa").pack(side=tk.LEFT, padx=(8, 0))
                if r.get("fix"):
                    fr = ttk.Frame(sf); fr.pack(fill=tk.X, padx=28, pady=(0, 3))
                    ttk.Label(fr, text=f"→ {r['fix']}", foreground="#888", font=("Microsoft YaHei UI", 8)).pack(anchor="w")

            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=(0, 8))
            sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=(0, 8))
            ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 12))
            for r in results:
                self.log(f"{icons.get(r['level'], '?')} {r['name']}: {r['message']}")
                if r.get("fix"): self.log(f"   → {r['fix']}")

        def _run():
            from utils.diagnostics import run_diagnostics
            results = run_diagnostics(self.input_dir_var.get().strip())
            self.after(0, lambda: _populate(results))

        threading.Thread(target=_run, daemon=True).start()

    def _save_config(self):
        try:
            data = {
                "input_dir": self.input_dir_var.get().strip(),
                "output_dir": self.output_dir_var.get().strip(),
                "api_key": self.api_key_var.get().strip(),
                "api_base": self.api_base_var.get().strip(),
                "api_model": self.api_model_var.get().strip(),
                "bvid": self.bvid_var.get().strip(),
                "sessdata": self.sessdata_var.get().strip(),
                "member_status": {name: var.get() for name, var in self._member_vars.items()},
                "cover_style": self._cover_style_var.get().strip(),
                "cover_count": int(self._cover_count_var.get().strip() or "5"),
                "stt_api_key": self._stt_key_var.get().strip(),
                "stt_base_url": self._stt_url_var.get().strip(),
                "stt_model": self._stt_model_var.get().strip(),
                "whisper_model": self._whisper_model_var.get().strip(),
                "auto_mode": self._auto_mode.get(),
                "pause_after_analysis": self._pause_var.get(),
                "analysis_mode": self._analysis_mode.get(),
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.save_hint.config(text="已保存 ✓", foreground="#4a4")
            self._update_api_status()
            self.after(3000, lambda: self.save_hint.config(text="", foreground="#888"))
            self.log("[配置] 已保存到 app_config.json")
        except Exception as e:
            self.save_hint.config(text=f"保存失败: {e}", foreground="#e44")

    def _edit_prompt(self):
        prompt_dir = PROJECT_ROOT / "prompt_method"
        files = sorted(prompt_dir.glob("*.txt"))
        if not files:
            messagebox.showinfo("提示", "prompt_method 目录下没有 .txt 文件")
            return

        chooser = tk.Toplevel(self)
        chooser.title("选择提示词文件")
        chooser.geometry("400x300")
        chooser.transient(self)
        chooser.grab_set()

        ttk.Label(chooser, text="选择要编辑的提示词模板：", font=("Microsoft YaHei UI", 10)).pack(pady=(12, 8))

        listbox = tk.Listbox(chooser, font=("Consolas", 10), bg="#151515", fg="#eaeaea",
                             selectbackground="#336", selectforeground="#fff")
        listbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        for f in files:
            listbox.insert(tk.END, f.name)
        listbox.select_set(0)

        def on_open():
            sel = listbox.curselection()
            if sel:
                filepath = files[sel[0]]
                chooser.destroy()
                self._open_text_editor(filepath)

        ttk.Button(chooser, text="打开编辑", command=on_open).pack(pady=(0, 12))

    def _edit_dict(self):
        dict_path = PROJECT_ROOT / "utils" / "asr_dict.txt"
        if not dict_path.exists():
            messagebox.showinfo("提示", f"找不到纠错字典: {dict_path}")
            return
        self._open_text_editor(dict_path, label="每行格式：错误词 正确词（中间用空格分隔）\n以 # 开头的行为注释，会被跳过。")

    def _open_text_editor(self, filepath, label=""):
        win = tk.Toplevel(self)
        win.title(f"编辑: {filepath.name}")
        win.geometry("900x650")
        win.minsize(600, 400)
        win.transient(self)

        if label:
            ttk.Label(win, text=label, foreground="#888", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=(10, 0))

        toolbar = ttk.Frame(win)
        toolbar.pack(fill=tk.X, padx=12, pady=(8, 4))

        def save():
            try:
                text = editor.get("1.0", "end-1c")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(text)
                status.config(text="已保存 ✓", foreground="#4a4")
                self.after(3000, lambda: status.config(text="", foreground="#888"))
                self.log(f"[编辑] 已保存: {filepath.name}")
            except Exception as e:
                status.config(text=f"保存失败: {e}", foreground="#e44")

        ttk.Button(toolbar, text="💾 保存", command=save).pack(side=tk.LEFT, padx=(0, 8))
        status = ttk.Label(toolbar, text="", foreground="#888")
        status.pack(side=tk.LEFT)

        editor_frame = ttk.Frame(win)
        editor_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

        editor = tk.Text(editor_frame, wrap=tk.WORD, relief=tk.FLAT,
                         bg="#151515", fg="#eaeaea", insertbackground="#ffffff",
                         font=("Consolas", 11), undo=True)
        editor.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL, command=editor.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        editor.config(yscrollcommand=scrollbar.set)

        # 加载文件内容
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            editor.insert("1.0", content)
        except Exception as e:
            editor.insert("1.0", f"# 读取失败: {e}\n")

        # Ctrl+S 快捷键
        win.bind("<Control-s>", lambda e: save())

    # ==========================================
    # 交互
    # ==========================================

    def _refresh_input_list(self):
        base = Path(self.input_dir_var.get())
        self._input_dirs = {}
        # 预定义的分类目录名，即使为空也展示
        known_categories = {c for c, _ in self.SPEAKER_TYPES if c not in ("未指定", "旁白", "一起说", "ASOUL")}
        known_categories.update({"小心思", "ASOUL团播", "枝江大团播", "闪耀舞台", "未分类",
                                  "嘉然×贝拉", "嘉然×乃琳", "贝拉×乃琳"})
        if base.exists():
            for child in sorted(base.iterdir()):
                if not child.is_dir() or "__no_danmaku__" in child.name:
                    continue
                # 已知分类目录始终展示，其他目录需包含素材才展示
                is_known = child.name in known_categories
                if not is_known:
                    has_media = any(child.rglob(f"*{ext}") for ext in [".mp4", ".flv", ".mkv", ".mov", ".ts", ".srt", ".ass"])
                    if not has_media:
                        continue
                self._input_dirs[child.name] = str(child)
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir() and "__no_danmaku__" not in grandchild.name:
                        self._input_dirs[f"{child.name}/{grandchild.name}"] = str(grandchild)
        if not self._input_dirs:
            self._input_dirs[base.name] = str(base)
        self.input_combo["values"] = list(self._input_dirs.keys())
        self._combo_count_label.config(text=f"共 {len(self._input_dirs)} 个可选目录")
        # 用文件夹名显示，不显示完整路径
        current = self.input_dir_var.get()
        current_name = Path(current).name
        if current_name in self._input_dirs:
            self._input_display_var.set(current_name)
        elif self._input_dirs:
            first_name = list(self._input_dirs.keys())[0]
            self._input_display_var.set(first_name)
            self.input_dir_var.set(self._input_dirs[first_name])

    def _preview_output_folder(self):
        """根据成员选择预生成分类名，显示在视频预览区"""
        active = [n for n, v in self._member_vars.items() if v.get()]
        if not active:
            return
        cat = self._resolve_category()
        out_base = self.output_dir_var.get().rstrip("/")
        inp_base = self.input_dir_var.get().rstrip("/")
        self._video_preview_label.config(
            text=f"分类: {cat}/  →  输入: {inp_base}/{cat}/  →  输出: {out_base}/{cat}/",
            foreground="#4a4")

    def _convert_srt_to_simplified(self, srt_path):
        """将 SRT 字幕从繁体转为简体（缓存已处理文件）"""
        if srt_path in self._converted_srts:
            return False
        self._converted_srts.add(srt_path)
        try:
            import zhconv
            with open(srt_path, "r", encoding="utf-8-sig") as f:
                text = f.read()
            simplified = zhconv.convert(text, "zh-cn")
            if simplified != text:
                with open(srt_path, "w", encoding="utf-8-sig") as f:
                    f.write(simplified)
                return True
        except ImportError:
            pass
        except Exception:
            pass
        return False

    def _resolve_category(self):
        """根据当前成员勾选确定分类名"""
        active = [n for n, v in self._member_vars.items() if v.get()]
        aso_set = {"嘉然", "贝拉", "乃琳"}
        active_aso = [n for n in active if n in aso_set]
        active_ss = [n for n in active if n not in aso_set]

        if len(active) == 1:
            return active[0]
        if set(active) == {"心宜", "思诺"}:
            return "小心思"
        if len(active) == 2 and all(n in aso_set for n in active):
            return "×".join(sorted(active))
        if len(active_aso) >= 2 and not active_ss:
            return "ASOUL团播"
        if active_ss and active_aso:
            return "枝江大团播"
        if active_ss:
            return "闪耀舞台"
        return "未分类"

    def _auto_organize_for_analysis(self):
        """分析前自动归类：仅移动当前选中视频的配套文件到成员子文件夹"""
        base = DEFAULT_INPUT_DIR
        if not base.exists():
            return

        video = self._get_selected_video()
        if not video:
            return
        # 只处理在根目录下的视频（已归类的跳过）
        if video.parent != base:
            return

        category = self._resolve_category()
        from core.file_utils import sanitize_filename

        name = video.stem
        date_match = re.search(r'(\d{4})[年-](\d{1,2})[月-](\d{1,2})日?', name)
        if date_match:
            y, m, d = date_match.groups()
            date_str = f"{m}月{d}日"
        else:
            import datetime
            ts = video.stat().st_mtime
            dt = datetime.datetime.fromtimestamp(ts)
            date_str = f"{dt.month}月{dt.day}日"
        short_name = sanitize_filename(name)
        if len(short_name) > 20:
            short_name = short_name[:20]
        folder_name = f"{date_str} {short_name}"
        target_dir = base / category / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        moved = 0
        # 移动视频
        dest = target_dir / video.name
        if video != dest:
            shutil.move(str(video), str(dest))
            moved += 1
        # 移动配套字幕/弹幕
        for ext in [".srt", ".ass"]:
            src = base / f"{name}{ext}"
            if src.exists():
                shutil.move(str(src), str(target_dir / f"{name}{ext}"))

        if moved > 0:
            self.log(f"  📁 自动归类: {video.name} → {category}/")
            self.input_dir_var.set(str(target_dir))
            os.environ["AUTOCLIP_INPUT_DIR"] = str(target_dir)
            self._check_environment()

    def _check_analysis_cache(self):
        """检查是否已有分析缓存（Data_source.txt 比 SRT 新则可复用）"""
        target = self.input_dir_var.get().strip()
        ds_path = os.path.join(target, "Data_source.txt")
        if not os.path.exists(ds_path):
            ds_path = "Data_source.txt"
        if not os.path.exists(ds_path):
            return False
        srt = self._find_srt_for_check()
        if not srt:
            return os.path.getsize(ds_path) > 10  # 有内容就复用
        try:
            return os.path.getmtime(ds_path) > os.path.getmtime(str(srt))
        except Exception:
            return False

    def _organize_files(self):
        """将素材根目录的文件按成员勾选归类到子文件夹（手动触发）"""
        base = DEFAULT_INPUT_DIR
        if not base.exists():
            return

        video_exts = {".mp4", ".flv", ".mkv", ".mov", ".ts"}
        from core.file_utils import sanitize_filename

        # 仅收集根目录下的视频（已在子文件夹中的不处理）
        all_videos = [f for f in base.iterdir() if f.is_file() and f.suffix.lower() in video_exts]

        if not all_videos:
            self.log("  没有需要整理的文件（视频已在子文件夹中）")
            self._refresh_input_list()
            self._check_environment()
            return

        category = self._resolve_category()

        moved = 0
        for video in all_videos:
            name = video.stem
            # 尝试提取日期 (B站格式: 2025年6月26日 或 2025-06-26 或 6月26日)
            date_match = re.search(r'(\d{4})[年-](\d{1,2})[月-](\d{1,2})日?', name)
            if date_match:
                y, m, d = date_match.groups()
                date_str = f"{m}月{d}日"
            else:
                import datetime
                ts = video.stat().st_mtime
                dt = datetime.datetime.fromtimestamp(ts)
                date_str = f"{dt.month}月{dt.day}日"

            short_name = sanitize_filename(name)
            if len(short_name) > 20:
                short_name = short_name[:20]

            folder_name = f"{date_str} {short_name}"
            target_dir = base / category / folder_name
            target_dir.mkdir(parents=True, exist_ok=True)

            dest = target_dir / video.name
            if video != dest:
                shutil.move(str(video), str(dest))
                moved += 1

            # 移动同名 SRT/ASS
            for ext in [".srt", ".ass"]:
                src = base / f"{name}{ext}"
                if src.exists():
                    dst = target_dir / f"{name}{ext}"
                    shutil.move(str(src), str(dst))

        self.log(f"  文件整理完成: {moved} 个视频归入 {category}/{folder_name}/")
        self._refresh_input_list()
        # 自动选中整理后的子文件夹
        subfolder = str(base / category / folder_name)
        if os.path.isdir(subfolder):
            self.input_dir_var.set(subfolder)
        self._check_environment()

    def _on_combo_select(self, event=None):
        name = self.input_combo.get()
        full = self._input_dirs.get(name)
        if full:
            self.input_dir_var.set(full)
            self._input_display_var.set(name)
            self._check_environment()

    def _on_video_select(self, event=None):
        """用户选择了视频文件 → 同步匹配字幕/弹幕 + 更新输出预览"""
        sel = self._video_var.get()
        video_path = None
        video_stem = None
        for path, display in self._video_list:
            if display == sel:
                video_path = Path(path)
                video_stem = video_path.stem
                from core.file_utils import sanitize_filename
                base_input = Path(self.input_dir_var.get())
                try:
                    rel = video_path.parent.relative_to(base_input)
                except ValueError:
                    rel = Path(".")
                out_base = self.output_dir_var.get().rstrip("/")
                if str(rel) != ".":
                    out_path = f"{out_base}/{rel}/{sanitize_filename(video_stem)}/"
                else:
                    out_path = f"{out_base}/{sanitize_filename(video_stem)}/"
                self._video_preview_label.config(
                    text=f"→ 输出: {out_path}", foreground="#4a4")
                break

        # 同步切换字幕和弹幕选择
        if video_stem:
            # 字幕：查找同名 SRT
            for p, d in self._srt_list:
                if Path(p).stem == video_stem:
                    self._srt_var.set(d)
                    break
            else:
                if self._srt_list:
                    self._srt_var.set("(未匹配 — 请手动选择)")
                    values = ["(未匹配 — 请手动选择)"] + [l for _, l in self._srt_list]
                    if self._srt_combo["values"] != tuple(values):
                        self._srt_combo["values"] = values
            # 弹幕：查找同名 ASS
            for p, d in self._ass_list:
                if Path(p).stem == video_stem:
                    self._ass_var.set(d)
                    break
            else:
                if self._ass_list:
                    self._ass_var.set("(未匹配 — 请手动选择)")
                    values = ["(未匹配 — 请手动选择)"] + [l for _, l in self._ass_list]
                    if self._ass_combo["values"] != tuple(values):
                        self._ass_combo["values"] = values

    def _get_selected_video(self):
        """返回用户选择的视频文件路径（BV模式未下载时返回None）"""
        sel = self._video_var.get()
        if "BV下载" in sel or "未检测到" in sel:
            return None
        for path, display in self._video_list:
            if display == sel:
                return Path(path)
        return Path(self._video_list[0][0]) if self._video_list else None

    def _get_selected_srt(self):
        """返回用户选择的 SRT 文件（无有效匹配或与视频不同名时返回None）"""
        sel = self._srt_var.get()
        if any(x in sel for x in ("未匹配", "无字幕", "自动生成", "BV下载", "未检测到", "请手动")):
            return None
        for path, display in self._srt_list:
            if display == sel and os.path.exists(path):
                # 额外校验：SRT 必须与选中的视频同名
                video = self._get_selected_video()
                if video and Path(path).stem != video.stem:
                    continue
                return Path(path)
        return None

    def _get_selected_ass(self):
        """返回用户选择的 ASS 弹幕文件（无有效匹配或与视频不同名时返回None）"""
        sel = self._ass_var.get()
        if any(x in sel for x in ("未匹配", "无弹幕", "自动跳过", "BV下载", "未检测到", "请手动")):
            return None
        for path, display in self._ass_list:
            if display == sel and os.path.exists(path):
                # 额外校验：ASS 必须与选中的视频同名
                video = self._get_selected_video()
                if video and Path(path).stem != video.stem:
                    continue
                return Path(path)
        return None

    def _smart_match_file(self, files, video_stem, suffix):
        """智能匹配：优先同名文件，否则列表第一个"""
        for f in files:
            if f.stem == video_stem:
                return f
        return files[0] if files else None

    def _browse_file(self, title, filetypes, string_var, combo):
        """自由选择文件（字幕/弹幕），更新 StringVar 并拷贝到素材目录"""
        path = filedialog.askopenfilename(
            title=f"选择{title}文件",
            filetypes=filetypes,
            initialdir=self.input_dir_var.get() or str(DEFAULT_INPUT_DIR),
        )
        if not path:
            return
        fname = os.path.basename(path)
        target_dir = self.input_dir_var.get().strip()
        if not target_dir:
            target_dir = str(DEFAULT_INPUT_DIR)
        try:
            os.makedirs(target_dir, exist_ok=True)
            dst = os.path.join(target_dir, fname)
            if os.path.abspath(path) != os.path.abspath(dst):
                shutil.copy2(path, dst)
                self.log(f"[浏览] 已复制 {title} 文件: {fname}")
            else:
                dst = path
        except Exception as e:
            self.log(f"[浏览] 复制失败: {e}")
            dst = path
        string_var.set(os.path.basename(dst))
        # 更新 combobox 列表
        current_values = list(combo["values"])
        if fname not in current_values:
            combo["values"] = [fname] + [v for v in current_values if v != fname]
        combo.current(0)
        # 仅更新文件列表，不触发完整环境检测（避免 organize 移动文件）
        self._update_edit_btn()

    def _open_folder(self, path_str):
        """安全打开文件夹：不存在则创建，失败则记录日志"""
        p = Path(path_str)
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))
        except Exception as e:
            self.log(f"[打开] 失败: {p} — {e}")

    def choose_input_dir(self):
        path = filedialog.askdirectory(initialdir=self.input_dir_var.get() or str(DEFAULT_INPUT_DIR))
        if path:
            self.input_dir_var.set(path)
            self._refresh_input_list()
            self._check_environment()

    def _on_file_drop(self, event):
        files = self.tk.splitlist(event.data)
        target_dir = self.input_dir_var.get().strip()
        if not os.path.isdir(target_dir):
            try:
                os.makedirs(target_dir)
            except Exception as e:
                self.log(f"[拖放] 无法创建目录: {e}")
                return

        copied = 0
        for f in files:
            src = f.strip("{}")
            fname = os.path.basename(src)
            dst = os.path.join(target_dir, fname)
            try:
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    self.log(f"[拖放] 已复制: {fname}")
                    copied += 1
            except Exception as e:
                self.log(f"[拖放] 失败 {fname}: {e}")

        if copied:
            self.drop_label.config(text=f"已导入 {copied} 个文件 ✓", fg="#4a4")
            self.after(3000, lambda: self.drop_label.config(
                text="将视频/字幕/弹幕文件拖放到这里（自动复制到输入目录）", fg="#777"))
            self._check_environment()

    def choose_output_dir(self):
        path = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(DEFAULT_OUTPUT_DIR))
        if path:
            self.output_dir_var.set(path)

    def log(self, text, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text.rstrip()}"
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.update_idletasks()
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{level}] {line}\n")
        except Exception:
            pass

    def _write_to_log(self, text):
        """供 _LogWriter 调用，直接写入原始文本（无时间戳，用于捕获 print 输出）。
        通过 after_idle 调度到主线程，确保 tkinter 控件访问安全。"""
        stripped = text.rstrip()
        if not stripped:
            return

        def _write():
            try:
                self.log_text.insert(tk.END, stripped + "\n")
                self.log_text.see(tk.END)
            except Exception:
                pass
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(stripped + "\n")
            except Exception:
                pass

        try:
            self.after_idle(_write)
        except Exception:
            pass

    def log_error(self, step_name, exc_info):
        """结构化报错：分类 + 根因 + 修复建议 + 写日志"""
        exc = exc_info[1] if exc_info and exc_info[1] else None
        key = type(exc).__name__ if exc else "UnknownError"
        self._error_count[key] = self._error_count.get(key, 0) + 1

        # 尝试分类错误
        try:
            from utils.diagnostics import classify_error
            diag = classify_error(exc, context=step_name)
            self.log(f"[{step_name}] ❌ 失败原因: {diag['reason']}", level="ERROR")
            self.log(f"   💡 修复建议: {diag['fix']}", level="ERROR")
        except Exception:
            self.log(f"[{step_name}] {key}: {exc}", level="ERROR")

        # 详细 traceback
        tb = traceback.format_exc()
        if tb and tb != "NoneType: None\n":
            self.log(tb.strip()[-500:], level="TRACE")

    def _update_api_status(self):
        key = self.api_key_var.get().strip()
        base = self.api_base_var.get().strip()
        model = self.api_model_var.get().strip()
        if key:
            self.api_status_label.config(text=f"已配置 ({model})", foreground="#4a4")
        else:
            self.api_status_label.config(text="未填写 API Key", foreground="#e44")

    def _update_cover_hint(self):
        hints = {
            "style1": "上白下黄震撼风格 · 双分屏布局",
            "style2": "上黄下白震撼风格 · 双分屏布局",
            "style3": "居中大字醒目风格 · 底部居中",
            "style4": "艺术简洁风格 · 毛玻璃背景",
        }
        self._cover_hint.config(text=hints.get(self._cover_style_var.get(), ""))

    def _open_api_config(self):
        """展开高级配置并聚焦 API Key"""
        if not self._advanced_showing:
            self._toggle_advanced()
        self.log("[提示] 在高级配置里填写 AI 接口信息")
        self.log("  接口地址：https://api.deepseek.com/v1/chat/completions")
        self.log("  模型名称：deepseek-v4-pro 或 deepseek-v4-flash")
        self.log("  API Key：在 platform.deepseek.com 注册后获取")

    def _test_api(self):
        key = self.api_key_var.get().strip()
        base = self.api_base_var.get().strip()
        model = self.api_model_var.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请先填写 API Key（展开高级配置 → AI 接口配置）")
            return
        self.log("正在测试 AI 接口连接...")
        try:
            import requests
            resp = requests.post(
                base,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "回复OK"}], "max_tokens": 10},
                timeout=15,
            )
            if resp.status_code == 200:
                self.log(f"  API 连接成功 ✓ ({model})")
                messagebox.showinfo("测试通过", f"连接成功！\n模型: {model}")
            else:
                self.log(f"  API 返回错误: {resp.status_code} {resp.text[:100]}")
                messagebox.showerror("测试失败", f"HTTP {resp.status_code}\n{resp.text[:200]}")
        except Exception as e:
            self.log(f"  API 连接失败: {e}")
            messagebox.showerror("测试失败", f"无法连接:\n{e}")

    def _open_kdenlive(self):
        """打开输出目录中最新的子文件夹"""
        out_dir = self.output_dir_var.get().strip()
        if not os.path.isdir(out_dir):
            messagebox.showinfo("提示", "输出目录还不存在，请先运行一次切片。")
            return
        try:
            subs = sorted(Path(out_dir).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            latest = next((s for s in subs if s.is_dir()), Path(out_dir))
            self._open_folder(str(latest))
            self.log(f"[快捷] 已打开: {latest.name}")
        except Exception as e:
            self.log(f"[快捷] 打开失败: {e}")
            self._open_folder(out_dir)

    def _edit_subtitle_speakers(self):
        """模式2：字幕发言人编辑器 — 逐条指定发言人 + 编辑文字"""
        # 优先使用已选中的 SRT，避免全目录扫描卡顿
        srt_path = None
        selected = self._get_selected_srt()
        if selected and selected.exists():
            srt_path = str(selected)
        else:
            # 快速层：仅扫描顶层目录
            target = self.input_dir_var.get().strip()
            top_srts = [f for f in Path(target).glob("*.srt") if "__no_danmaku__" not in str(f)]
            if top_srts:
                srt_path = str(top_srts[0])
            else:
                # 回退：递归扫描（仅单层子文件夹，比 rglob 快很多）
                sub_srts = [f for f in Path(target).glob("*/*.srt") if "__no_danmaku__" not in str(f)]
                if sub_srts:
                    srt_path = str(sub_srts[0])
                else:
                    # 最终回退：完整 rglob
                    srts = list(Path(target).rglob("*.srt"))
                    srts = [f for f in srts if "__no_danmaku__" not in str(f)]
                    if not srts:
                        messagebox.showinfo("提示", "未找到 SRT 字幕文件")
                        return
                    srt_path = str(srts[0])

        # 解析 SRT
        with open(srt_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        blocks = content.strip().split("\n\n")
        entries = []
        for blk in blocks:
            lines = blk.strip().split("\n")
            if len(lines) >= 3:
                entries.append({
                    "idx": lines[0], "ts": lines[1],
                    "text": "\n".join(lines[2:])
                })

        # 使用统一的发言人配色
        colors = self.SPEAKER_COLORS

        win = tk.Toplevel(self)
        win.title("字幕发言人编辑（完整字幕）")
        win.geometry("900x650")
        win.transient(self)

        # 顶部按钮
        top = ttk.Frame(win, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text=f"共 {len(entries)} 条字幕 — 指定发言人后自动应用成员配色",
                  font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        batch_var = tk.StringVar()
        batch_list = ["批量设置:"] + [f"全部→{t[0]}" for t in self.SPEAKER_TYPES if t[0] != "未指定"]
        batch_combo = ttk.Combobox(top, textvariable=batch_var,
                                    values=batch_list,
                                    state="readonly", width=15)
        batch_combo.pack(side=tk.RIGHT, padx=4)

        def _batch_set():
            sel = batch_var.get()
            for t in self.SPEAKER_TYPES:
                if f"全部→{t[0]}" == sel:
                    for e in entries:
                        e["speaker"] = t[0]
                    _refresh_list()
                    break

        batch_combo.bind("<<ComboboxSelected>>", lambda e: _batch_set())

        # 主区域：Canvas滚动列表
        canvas = tk.Canvas(win, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=8)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=8)

        # 进度标签 + 分页控件
        progress_frame = ttk.Frame(win)
        progress_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._speaker_progress = ttk.Label(progress_frame, text="正在加载...", foreground="#888",
                                            font=("Microsoft YaHei UI", 9))
        self._speaker_progress.pack(side=tk.LEFT)
        page_frame = ttk.Frame(progress_frame)
        page_frame.pack(side=tk.RIGHT)
        page_label = ttk.Label(page_frame, text="", foreground="#888")
        page_label.pack(side=tk.LEFT, padx=(0, 8))

        speaker_vars = []
        text_vars = []
        BATCH_SIZE = 60  # 每批渲染条目数，避免创建大量 widget 卡 UI

        def _render_batch(start_idx):
            """分批渲染：每次渲染 BATCH_SIZE 条，用 after 调度下一批"""
            end_idx = min(start_idx + BATCH_SIZE, len(entries))
            for i in range(start_idx, end_idx):
                e = entries[i]
                row = ttk.Frame(sf)
                row.pack(fill=tk.X, padx=8, pady=1)

                ts_text = e["ts"].split(" --> ")[0][:8] if "-->" in e["ts"] else e["ts"][:8]
                ttk.Label(row, text=ts_text, font=("Consolas", 8), width=7).pack(side=tk.LEFT)

                sp_var = tk.StringVar(value=e.get("speaker", "未指定"))
                speaker_vars.append(sp_var)
                sp_combo = ttk.Combobox(row, textvariable=sp_var,
                                         values=[t[0] for t in self.SPEAKER_TYPES],
                                         state="readonly", width=6)
                sp_combo.pack(side=tk.LEFT, padx=2)

                color_label = tk.Label(row, text="●", fg=colors.get(sp_var.get(), "#888"),
                                       font=("Consolas", 10), width=2)
                color_label.pack(side=tk.LEFT)
                sp_var.trace_add("write", lambda *a, l=color_label, v=sp_var:
                    l.configure(fg=colors.get(v.get(), "#888")))

                txt_var = tk.StringVar(value=e["text"])
                text_vars.append(txt_var)
                ttk.Entry(row, textvariable=txt_var, font=("Microsoft YaHei UI", 9)
                          ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

            progress_pct = min(100, int(end_idx / len(entries) * 100))
            self._speaker_progress.config(text=f"已加载 {end_idx}/{len(entries)} 条 ({progress_pct}%)")
            sf.update_idletasks()

            if end_idx < len(entries):
                win.after(5, lambda: _render_batch(end_idx))
            else:
                self._speaker_progress.config(text=f"共 {len(entries)} 条字幕 — 指定发言人后自动应用成员配色",
                                              foreground="#4a4")
                page_label.config(text="")

        def _refresh_list():
            for w in sf.winfo_children():
                w.destroy()
            speaker_vars.clear()
            text_vars.clear()
            self._speaker_progress.config(text="正在加载...", foreground="#888")
            win.after(5, lambda: _render_batch(0))

        _refresh_list()

        # 保存按钮
        btn_row = ttk.Frame(win, padding=10)
        btn_row.pack(fill=tk.X)

        def _save():
            # 更新 entries 并写回 SRT
            for i in range(len(entries)):
                entries[i]["speaker"] = speaker_vars[i].get()
                entries[i]["text"] = text_vars[i].get()
            out_lines = []
            for i, e in enumerate(entries, 1):
                out_lines.append(str(i))
                out_lines.append(e["ts"])
                sp = e.get("speaker", "未指定")
                out_lines.append(f"[{sp}] {e['text']}" if sp != "未指定" else e["text"])
                out_lines.append("")
            with open(srt_path, "w", encoding="utf-8-sig") as f:
                f.write("\n".join(out_lines))
            self.log(f"  发言人标记已保存: {srt_path}")
            messagebox.showinfo("已保存", f"已保存 {len(entries)} 条字幕的发言人标记")
            win.destroy()

        ttk.Button(btn_row, text="💾 保存发言人标记", command=_save).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_row, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=4)

    def _edit_highlight_review(self):
        """高光片段校核器 — 逐段审阅 + 预览含字幕视频 + 编辑时间/增删字幕 + 指定发言人"""
        from core.subtitle_utils import SubtitleUtils
        # 1. 读取 Data_source.txt
        target = self.input_dir_var.get().strip()
        ds_path = os.path.join(target, "Data_source.txt")
        if not os.path.exists(ds_path):
            ds_path = "Data_source.txt"
        if not os.path.exists(ds_path):
            messagebox.showinfo("提示", "尚未生成 Data_source.txt，请先运行弹幕分析（第3步）。")
            return
        try:
            with open(ds_path, "r", encoding="utf-8") as f:
                segments = json.load(f)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取 Data_source.txt:\n{e}")
            return
        if not segments:
            messagebox.showinfo("提示", "Data_source.txt 中没有高光片段。")
            return

        # 2. 查找 SRT 和视频
        srt_path = self._find_srt_for_check()
        if not srt_path:
            messagebox.showinfo("提示", "未找到字幕文件，无法校核发言人。")
            return
        video = self._get_selected_video()
        if not video:
            if self._video_list:
                video = Path(self._video_list[0][0])
        srt_entries = SubtitleUtils.parse_srt(str(srt_path))
        if not srt_entries:
            messagebox.showinfo("提示", "字幕文件为空或格式无法解析。")
            return

        # 3. 按片段提取字幕（引用原列表以便增删同步）
        segment_data = []
        for seg in segments:
            ts = seg.get("timestamp", "")
            parts = ts.split("-")
            if len(parts) != 2:
                continue
            try:
                t0 = self._parse_srt_time(parts[0].strip())
                t1 = self._parse_srt_time(parts[1].strip())
            except Exception:
                continue
            idxs = [i for i, s in enumerate(srt_entries) if s["end"] > t0 and s["start"] < t1]
            if idxs:
                pre = max(0, idxs[0] - 2)
                post = min(len(srt_entries), idxs[-1] + 2)
                subs = srt_entries[pre:post]  # 保留引用！
                sub_indices = list(range(pre, post))
            else:
                subs = []
                sub_indices = []
            segment_data.append({
                "seg": seg,
                "subs": subs,
                "sub_indices": sub_indices,  # srt_entries 中的位置
                "t0": t0,
                "t1": t1,
            })

        # 4. 构建 UI — 先底部工具栏再 Canvas，避免遮挡
        win = tk.Toplevel(self)
        win.title("高光片段校核器")
        win.geometry("1050x750")
        win.minsize(900, 550)
        win.transient(self)

        # 底部按钮（先 pack，固定不滚动）
        bottom = ttk.Frame(win, padding=12)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        def _save_all():
            speaker_map = {}
            for key, var in speaker_vars.items():
                sp = var.get()
                if sp == "未指定":
                    continue
                seg_idx, sub_idx = key
                sd = segment_data[seg_idx]
                if sub_idx < len(sd["subs"]):
                    speaker_map[id(sd["subs"][sub_idx])] = sp
            def _fmt_ts(sec):
                h, m, s = int(sec//3600), int((sec%3600)//60), int(sec%60)
                return f"{h:02d}:{m:02d}:{s:02d},{int((sec%1)*1000):03d}"
            out_lines = []
            for i, entry in enumerate(srt_entries, 1):
                out_lines.append(str(i))
                out_lines.append(f"{_fmt_ts(entry['start'])} --> {_fmt_ts(entry['end'])}")
                sp = speaker_map.get(id(entry), "")
                text = entry["text"]
                if sp:
                    text = f"[{sp}] {text}"
                out_lines.append(text)
                out_lines.append("")
            with open(str(srt_path), "w", encoding="utf-8-sig") as f:
                f.write("\n".join(out_lines))
            self.log(f"  发言人标记已保存: {srt_path.name}")
            # 解锁第3步，允许切换到精确模式后重分析
            self._step_done[3] = False
            self._mark_step(3, "wait")
            messagebox.showinfo("已保存", "发言人标记已保存！\n\n"
                                "1. 切换到「模式2 精确」\n"
                                "2. 点击第3步重新分析\n"
                                "3. 分析完后点击第4步生成切片")
            win.destroy()

        ttk.Button(bottom, text="💾 保存并关闭", command=_save_all).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=4)

        # 顶部工具栏
        toolbar = ttk.Frame(win, padding=8)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=f"共 {len(segments)} 个高光片段 | 字幕: {srt_path.name}",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        seg_nav = ttk.Frame(toolbar)
        seg_nav.pack(side=tk.RIGHT)
        ttk.Button(seg_nav, text="◀ 上一段", width=8,
                   command=lambda: _nav(-1)).pack(side=tk.LEFT, padx=2)
        seg_label = ttk.Label(seg_nav, text=f"1/{len(segments)}", width=8,
                               font=("Microsoft YaHei UI", 9))
        seg_label.pack(side=tk.LEFT, padx=4)
        ttk.Button(seg_nav, text="下一段 ▶", width=8,
                   command=lambda: _nav(1)).pack(side=tk.LEFT, padx=2)

        # 主区域：Canvas 滚动
        canvas = tk.Canvas(win, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        main_frame = ttk.Frame(canvas, padding=12)
        main_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=main_frame, anchor="nw", tags="main_win")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        def _set_width(e):
            canvas.itemconfig("main_win", width=e.width)
        canvas.bind("<Configure>", _set_width)

        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        cur_idx = [0]
        speaker_vars = {}
        time_vars = {}  # (seg_idx, sub_idx) → (start_var, end_var)

        def _preview_with_subs(t0, t1, subs_list):
            """用 ffmpeg 生成带字幕的预览片段并播放"""
            if not video or not video.exists():
                messagebox.showinfo("提示", "未找到视频文件")
                return
            import tempfile
            # 生成临时 ASS 字幕
            ass_tmp = tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w", encoding="utf-8")
            ass_tmp.write("""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""")
            for sub in subs_list:
                s = sub["start"] - t0
                e = sub["end"] - t0
                if s < 0:
                    s = 0
                if e > (t1 - t0):
                    e = t1 - t0
                text = sub["text"].replace("\n", "\\N")
                ass_tmp.write(f"Dialogue: 0,{self._ass_time(s)},{self._ass_time(e)},Default,,0,0,0,,{text}\n")
            ass_tmp.close()

            # 生成预览视频
            preview_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            preview_path = preview_tmp.name
            preview_tmp.close()
            cmd = [
                "ffmpeg", "-y", "-ss", str(max(0, t0 - 1)), "-t", str(t1 - t0 + 3),
                "-i", str(video),
                "-vf", f"ass='{ass_tmp.name.replace(chr(92),chr(92)+chr(92)).replace(':',r'\\:')}'",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart", preview_path,
            ]
            self.log(f"  正在生成预览片段...")
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            try:
                os.unlink(ass_tmp.name)
            except Exception:
                pass
            if os.path.exists(preview_path) and os.path.getsize(preview_path) > 0:
                try:
                    subprocess.Popen(["ffplay", "-window_title", "预览 (含字幕)", preview_path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except FileNotFoundError:
                    os.startfile(preview_path)
            else:
                self.log("  预览生成失败，回退到直接播放")
                try:
                    subprocess.Popen(["ffplay", "-ss", str(max(0, t0 - 2)), "-autoexit",
                                       str(video)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except FileNotFoundError:
                    os.startfile(str(video))

        def _delete_sub(seg_idx, glob_idx):
            """从 srt_entries 删除一条字幕"""
            sd = segment_data[seg_idx]
            if glob_idx < len(srt_entries):
                del srt_entries[glob_idx]
                # 重建受影响的 segment_data
                for sd2 in segment_data:
                    sd2["sub_indices"] = [i - 1 if i > glob_idx else i for i in sd2["sub_indices"] if i != glob_idx]
                    sd2["subs"] = [srt_entries[i] for i in sd2["sub_indices"] if i < len(srt_entries)]
            _render_segment(cur_idx[0])

        def _add_sub_after(seg_idx, glob_idx):
            """在指定位置后插入一条新字幕"""
            if glob_idx + 1 < len(srt_entries):
                ref = srt_entries[glob_idx]
                new_entry = {"start": ref["end"], "end": ref["end"] + 2, "text": "(新字幕)"}
            else:
                new_entry = {"start": 0.0, "end": 2.0, "text": "(新字幕)"}
            insert_at = glob_idx + 1
            srt_entries.insert(insert_at, new_entry)
            for sd2 in segment_data:
                sd2["sub_indices"] = [i + 1 if i >= insert_at else i for i in sd2["sub_indices"]]
                sd2["subs"] = [srt_entries[i] for i in sd2["sub_indices"] if i < len(srt_entries)]
            _render_segment(cur_idx[0])

        def _fmt_ts_edit(sec):
            h, m, s = int(sec//3600), int((sec%3600)//60), int(sec%60)
            return f"{h:02d}:{m:02d}:{s:02d}"

        def _parse_ts_edit(text):
            parts = text.strip().split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            return 0

        def _render_segment(idx):
            for w in main_frame.winfo_children():
                w.destroy()
            if idx < 0 or idx >= len(segment_data):
                return
            sd = segment_data[idx]
            seg = sd["seg"]
            subs = sd["subs"]
            sub_indices = sd["sub_indices"]
            t0, t1 = sd["t0"], sd["t1"]
            seg_label.config(text=f"{idx + 1}/{len(segments)}")

            # AI 结果卡片（颜色适配暗色背景）
            ai_card = ttk.LabelFrame(main_frame, text="AI 分析结果", padding=8)
            ai_card.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(ai_card, text=f"标题: {seg.get('title', '(无)')}",
                      font=("Microsoft YaHei UI", 10, "bold"), foreground="#5cf").pack(anchor="w")
            ttk.Label(ai_card, text=f"摘要: {seg.get('summary', '(无)')}",
                      font=("Microsoft YaHei UI", 9), foreground="#bbb").pack(anchor="w", pady=(2, 0))
            cover_row = ttk.Frame(ai_card)
            cover_row.pack(fill=tk.X, pady=(4, 0))
            ttk.Label(cover_row, text=f"封面大字: {seg.get('cover_text_1', '')}",
                      foreground="#E6B800", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(0, 16))
            ttk.Label(cover_row, text=f"封面小字: {seg.get('cover_text_2', '')}",
                      foreground="#E6B800", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
            ttk.Label(ai_card, text=f"高光理由: {seg.get('highlight_reason', '(无)')}",
                      foreground="#999", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))
            ttk.Label(ai_card, text=f"时间: {self._format_seconds(t0)} → {self._format_seconds(t1)}",
                      foreground="#888", font=("Consolas", 9)).pack(anchor="w", pady=(4, 0))
            ttk.Button(ai_card, text="▶ 预览 (含字幕)",
                       command=lambda t0=t0, t1=t1, s=subs: _preview_with_subs(t0, t1, s)
                       ).pack(anchor="w", pady=(8, 0))

            if not subs:
                ttk.Label(main_frame, text="(此片段内无匹配字幕)", foreground="#888").pack(pady=20)
                ttk.Button(main_frame, text="＋ 在头部插入新字幕",
                           command=lambda: _add_sub_after(idx, -1)).pack(pady=4)
                return

            sub_card = ttk.LabelFrame(main_frame,
                                       text=f"字幕 ({len(subs)} 条) — 可编辑时间/增删",
                                       padding=8)
            sub_card.pack(fill=tk.X)

            for si, (sub, glob_idx) in enumerate(zip(subs, sub_indices)):
                row = ttk.Frame(sub_card)
                row.pack(fill=tk.X, pady=1)

                # 可编辑时间戳
                start_var = tk.StringVar(value=_fmt_ts_edit(sub["start"]))
                end_var = tk.StringVar(value=_fmt_ts_edit(sub["end"]))
                time_vars[(idx, si)] = (start_var, end_var)
                ts_frame = ttk.Frame(row)
                ts_frame.pack(side=tk.LEFT, padx=(0, 4))
                ttk.Entry(ts_frame, textvariable=start_var, width=8,
                          font=("Consolas", 8)).pack(side=tk.LEFT)
                ttk.Label(ts_frame, text="→", font=("Consolas", 7),
                          foreground="#666").pack(side=tk.LEFT)

                # 发言人下拉
                sp_var = tk.StringVar(value="未指定")
                speaker_vars[(idx, si)] = sp_var
                sp_combo = ttk.Combobox(row, textvariable=sp_var,
                                         values=[t[0] for t in self.SPEAKER_TYPES],
                                         state="readonly", width=6)
                sp_combo.pack(side=tk.LEFT, padx=2)
                color_lbl = tk.Label(row, text="●", fg=self.SPEAKER_COLORS["未指定"],
                                     font=("Consolas", 10), width=2, bg="#1e1e1e")
                color_lbl.pack(side=tk.LEFT)
                sp_var.trace_add("write", lambda *a, l=color_lbl, v=sp_var:
                    l.configure(fg=self.SPEAKER_COLORS.get(v.get(), "#888")))

                # 字幕文本
                txt_var = tk.StringVar(value=sub["text"])
                ttk.Entry(row, textvariable=txt_var,
                          font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
                # 保存文本修改回原字典
                txt_var.trace_add("write", lambda *a, s=sub, v=txt_var: s.update({"text": v.get()}))

                # 删除/添加按钮
                ttk.Button(row, text="✕", width=2,
                           command=lambda gi=glob_idx: _delete_sub(idx, gi)).pack(side=tk.LEFT, padx=(2, 0))
                ttk.Button(row, text="＋", width=2,
                           command=lambda gi=glob_idx: _add_sub_after(idx, gi)).pack(side=tk.LEFT, padx=1)

            # 保存时间编辑
            def _apply_time_edits():
                for (seg_i, sub_i), (sv, ev) in time_vars.items():
                    sd2 = segment_data[seg_i]
                    if sub_i < len(sd2["subs"]):
                        sd2["subs"][sub_i]["start"] = _parse_ts_edit(sv.get())
                        sd2["subs"][sub_i]["end"] = _parse_ts_edit(ev.get())
                _render_segment(cur_idx[0])

            # 批量设置发言人
            batch_row = ttk.Frame(sub_card)
            batch_row.pack(fill=tk.X, pady=(8, 0))
            ttk.Label(batch_row, text="批量本段:", font=("Microsoft YaHei UI", 8),
                      foreground="#888").pack(side=tk.LEFT)
            for sp_name, sp_color in self.SPEAKER_TYPES:
                if sp_name == "未指定":
                    continue
                tk.Button(batch_row, text=sp_name, font=("Microsoft YaHei UI", 7),
                          fg=sp_color, bg="#2a2a2a", relief=tk.FLAT, padx=4,
                          command=lambda n=sp_name, i=idx: _batch_set_speaker(i, n)
                          ).pack(side=tk.LEFT, padx=1)
            ttk.Button(batch_row, text="应用时间编辑", command=_apply_time_edits
                       ).pack(side=tk.LEFT, padx=(12, 0))

        def _batch_set_speaker(seg_idx, name):
            for si in range(len(segment_data[seg_idx]["subs"])):
                key = (seg_idx, si)
                if key in speaker_vars:
                    speaker_vars[key].set(name)

        def _nav(delta):
            new_idx = cur_idx[0] + delta
            if 0 <= new_idx < len(segment_data):
                cur_idx[0] = new_idx
                _render_segment(new_idx)

        _render_segment(0)

    @staticmethod
    def _ass_time(seconds):
        """秒 → ASS 时间格式 H:MM:SS.cc"""
        if seconds < 0:
            seconds = 0
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        return f"{int(h)}:{int(m):02d}:{int(s):02d}.{int((s%1)*100):02d}"

    def _edit_data_source(self):
        """打开标题编辑器，修改 Data_source.txt 中各片段的标题、摘要、封面文字"""
        target = self.input_dir_var.get().strip()
        ds_path = os.path.join(target, "Data_source.txt")
        if not os.path.exists(ds_path):
            # 回退到 cwd
            ds_path = "Data_source.txt"
        if not os.path.exists(ds_path):
            messagebox.showinfo("提示", "尚未生成 Data_source.txt，请先运行弹幕分析（第3步）或视频模式的第1步。")
            return

        try:
            with open(ds_path, "r", encoding="utf-8") as f:
                clips = json.load(f)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取 Data_source.txt:\n{e}")
            return

        if not clips:
            messagebox.showinfo("提示", "Data_source.txt 中没有切片条目。")
            return

        self._ds_editor_clips = clips
        self._ds_editor_path = ds_path
        self._ds_editor_idx = 0

        win = tk.Toplevel(self)
        win.title("编辑切片数据")
        win.geometry("800x580")
        win.minsize(700, 450)
        win.transient(self)

        # 左侧列表
        left = ttk.Frame(win, padding=(12, 12, 6, 12))
        left.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="片段列表", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        listbox = tk.Listbox(left, font=("Microsoft YaHei UI", 9), bg="#151515", fg="#eaeaea",
                              selectbackground="#336", selectforeground="#fff",
                              width=32, activestyle="none")
        listbox.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        for i, clip in enumerate(clips):
            listbox.insert(tk.END, f"[{i+1}] {clip.get('title', '(无标题)')[:30]}")

        # 右侧编辑区
        right = ttk.Frame(win, padding=(6, 12, 12, 12))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        nav = ttk.Frame(right)
        nav.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(nav, text="编辑第", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        idx_label = ttk.Label(nav, text="1", font=("Microsoft YaHei UI", 9, "bold"), foreground="#4af", width=3)
        idx_label.pack(side=tk.LEFT)
        ttk.Label(nav, text=f"/ {len(clips)} 个片段", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        ttk.Button(nav, text="◀ 上一个", width=8,
                   command=lambda: self._ds_nav(-1, listbox, idx_label, fields)).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(nav, text="下一个 ▶", width=8,
                   command=lambda: self._ds_nav(1, listbox, idx_label, fields)).pack(side=tk.RIGHT)

        ttk.Label(right, text="时间戳（只读）", font=("Microsoft YaHei UI", 8), foreground="#888").pack(anchor="w")
        ts_var = tk.StringVar()
        ts_entry = ttk.Entry(right, textvariable=ts_var, state="readonly", font=("Consolas", 10))
        ts_entry.pack(fill=tk.X, pady=(2, 6))

        fields = {}
        field_configs = [
            ("title", "标题 (B站标题)", 50),
            ("summary", "摘要 (片段概括)", 60),
            ("cover_text_1", "封面大字 (3-10字)", 20),
            ("cover_text_2", "封面小字 (3-10字)", 20),
            ("highlight_reason", "高光理由", 60),
        ]
        for key, label, width in field_configs:
            ttk.Label(right, text=label, font=("Microsoft YaHei UI", 8), foreground="#888").pack(anchor="w")
            var = tk.StringVar()
            entry = ttk.Entry(right, textvariable=var, font=("Microsoft YaHei UI", 10))
            entry.pack(fill=tk.X, pady=(2, 6))
            fields[key] = var

        def _load_clip(idx):
            clip = self._ds_editor_clips[idx]
            self._ds_editor_idx = idx
            ts_var.set(clip.get("timestamp", ""))
            for key, var in fields.items():
                var.set(clip.get(key, ""))
            idx_label.config(text=str(idx + 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(idx)
            listbox.see(idx)

        def _save_current():
            idx = self._ds_editor_idx
            self._ds_editor_clips[idx]["timestamp"] = ts_var.get()
            for key, var in fields.items():
                self._ds_editor_clips[idx][key] = var.get()
            # 更新列表显示
            listbox.delete(idx)
            listbox.insert(idx, f"[{idx+1}] {fields['title'].get()[:30] or '(无标题)'}")
            listbox.selection_set(idx)

        listbox.bind("<<ListboxSelect>>", lambda e: (_save_current(), _load_clip(listbox.curselection()[0]) if listbox.curselection() else None))

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=(12, 0))

        def _save_all():
            _save_current()
            try:
                output_keys = ['timestamp', 'title', 'summary', 'cover_text_1', 'cover_text_2', 'highlight_reason']
                simple_data = [{k: v for k, v in c.items() if k in output_keys} for c in self._ds_editor_clips]
                with open(self._ds_editor_path, "w", encoding="utf-8") as f:
                    json.dump(simple_data, f, ensure_ascii=False, indent=2)
                self.log(f"[编辑] Data_source.txt 已保存 ({len(simple_data)} 个片段)")
                messagebox.showinfo("保存成功", f"已保存 {len(simple_data)} 个片段到 Data_source.txt")
                win.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        ttk.Button(btn_row, text="💾 全部保存并关闭", command=_save_all).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(btn_row, text="取消", command=win.destroy).pack(side=tk.RIGHT)

        _load_clip(0)

    def _ds_nav(self, delta, listbox, idx_label, fields):
        """标题编辑器内的片段导航"""
        clips = self._ds_editor_clips
        # 先保存当前
        idx = self._ds_editor_idx
        clips[idx]["title"] = fields["title"].get()
        clips[idx]["summary"] = fields["summary"].get()
        clips[idx]["cover_text_1"] = fields["cover_text_1"].get()
        clips[idx]["cover_text_2"] = fields["cover_text_2"].get()
        clips[idx]["highlight_reason"] = fields["highlight_reason"].get()
        listbox.delete(idx)
        listbox.insert(idx, f"[{idx+1}] {fields['title'].get()[:30] or '(无标题)'}")

        new_idx = (idx + delta) % len(clips)
        clip = clips[new_idx]
        self._ds_editor_idx = new_idx
        for key, var in fields.items():
            var.set(clip.get(key, ""))
        idx_label.config(text=str(new_idx + 1))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(new_idx)
        listbox.see(new_idx)

    # ==========================================
    # 后台任务调度
    # ==========================================

    def _run_worker(self, title, func):
        if self._running:
            self.log(f"[警告] 上一个任务还在运行，请等它完成。")
            return

        def worker():
            t0 = time.time()
            try:
                self.log(f"\n{'─' * 40}")
                self.log(f"[{title}] 开始...")
                func()
                elapsed = time.time() - t0
                self.log(f"[{title}] 完成 ✓ (用时 {elapsed:.0f} 秒)")
            except Exception:
                elapsed = time.time() - t0
                self.log(f"[{title}] 失败 ✗ (用时 {elapsed:.0f} 秒)")
                self.log_error(title, sys.exc_info())
                exc = sys.exc_info()[1]
                err_msg = str(exc) if exc else "未知错误"
                try:
                    from utils.diagnostics import classify_error
                    diag = classify_error(exc, context=title)
                    err_msg = f"{diag['reason']}\n\n💡 {diag['fix']}"
                except Exception:
                    pass
                messagebox.showerror("运行失败", f"「{title}」失败\n\n{err_msg}")
            finally:
                self._running = False
                self.btn_stop.configure(text="■ 终止")
                self._set_buttons_state(True)

        self._running = True
        self._set_buttons_state(False)
        threading.Thread(target=worker, daemon=True).start()

    def _stop_run(self):
        """用户点击终止按钮"""
        self._stop_requested = True
        os.environ["AUTOCLIP_STOP"] = "1"  # 跨线程传递终止信号
        self.btn_stop.configure(state="disabled", text="终止中...")
        self.log("[用户] 请求终止，当前步骤完成后将停止...")

    def _set_buttons_state(self, enabled):
        s = "normal" if enabled else "disabled"
        self.btn_all.configure(state=s)
        s_stop = "disabled" if enabled else "normal"
        self.btn_stop.configure(state=s_stop)

    def _apply_env(self):
        # 强制 UTF-8 编码，防止 Windows GBK 环境打印 emoji 崩溃
        os.environ["PYTHONIOENCODING"] = "utf-8"
        os.environ["PYTHONUTF8"] = "1"
        os.environ["AUTOCLIP_INPUT_DIR"] = self.input_dir_var.get().strip()
        os.environ["AUTOCLIP_OUTPUT_DIR"] = self.output_dir_var.get().strip()
        # 弹幕/字幕分析目录：直接指向素材目录或视频所在子目录
        target = self.input_dir_var.get().strip()
        ass_file = self._get_selected_ass()
        srt_file = self._get_selected_srt()
        if ass_file and ass_file.exists():
            os.environ["DANMU_INPUT_DIR"] = str(ass_file.parent)
            os.environ["AUTOCLIP_ASS_FILE"] = str(ass_file)
        else:
            os.environ["DANMU_INPUT_DIR"] = target
            os.environ.pop("AUTOCLIP_ASS_FILE", None)
        if srt_file and srt_file.exists():
            os.environ["AUTOCLIP_SRT_FILE"] = str(srt_file)
        os.environ["ASR_TARGET_FOLDER"] = self.input_dir_var.get().strip()
        os.environ["SILICONFLOW_API_KEY"] = self.api_key_var.get().strip()
        os.environ["SILICONFLOW_BASE_URL"] = self.api_base_var.get().strip()
        os.environ["SILICONFLOW_MODEL"] = self.api_model_var.get().strip()
        os.environ["BILIBILI_VIDEO_INPUT"] = self.bvid_var.get().strip()
        os.environ["BILIBILI_SESSDATA"] = self.sessdata_var.get().strip()
        # 成员出场标记 → JSON 编码传给 danmu_method
        member_status = {name: (1 if var.get() else 0) for name, var in self._member_vars.items()}
        os.environ["AUTOCLIP_MEMBER_STATUS"] = json.dumps(member_status, ensure_ascii=False)
        # 封面配置
        os.environ["AUTOCLIP_COVER_STYLE"] = self._cover_style_var.get().strip()
        os.environ["AUTOCLIP_COVER_COUNT"] = self._cover_count_var.get().strip()
        # STT 语音识别接口
        os.environ["STT_API_KEY"] = self._stt_key_var.get().strip()
        os.environ["STT_BASE_URL"] = self._stt_url_var.get().strip()
        os.environ["STT_MODEL"] = self._stt_model_var.get().strip()
        os.environ["WHISPER_MODEL"] = self._whisper_model_var.get().strip()
        os.environ["ANALYSIS_MODE"] = self._analysis_mode.get().strip()
        # 终止标志（供 Auto_clip 等长时间运行的模块检查）
        os.environ["AUTOCLIP_STOP"] = "1" if self._stop_requested else "0"

    def _update_mode_hint(self):
        bv_mode = self._mode.get() == "bv"
        if bv_mode:
            self.mode_hint.config(
                text="在上方填写 BV 号 → 自动下载视频、字幕和弹幕 → 分析弹幕找高光 → 切片。需要 AI API Key。")
        else:
            self.mode_hint.config(
                text="将视频拖入上方拖放区 → 必剪免费生成字幕 → 自动切片。无需 API Key 即可使用。")

    def _on_precise_mode(self):
        """选择精确模式时自动开启暂停；若已有分析结果则直接打开校核器"""
        if not self._pause_var.get():
            self._pause_var.set(True)
            self.log("[分析模式] 已切换到精确模式，自动开启「分析后暂停校核」")
        # 如果分析已完成（Data_source.txt 存在），直接打开校核器
        target = self.input_dir_var.get().strip()
        ds_path = os.path.join(target, "Data_source.txt")
        if os.path.exists(ds_path):
            self.log("  检测到已有分析结果，打开高光校核器...")
            self.after(200, self._edit_highlight_review)
        else:
            self.log("  推荐: 先用模式1运行第3步模糊分析 → 校核高光 → 切回精确重分析")

    def _on_mode_change(self):
        """切换素材来源模式时刷新步骤描述"""
        self._update_mode_hint()
        self._reset_steps()
        self._preview_output_folder()

    def _reset_steps(self):
        self._step_done = {1: False, 2: False, 3: False, 4: False}
        for w in self.steps_frame.winfo_children():
            w.destroy()
        self._build_step_rows()

    def _build_step_rows(self):
        self._step_widgets = {}
        bv_mode = self._mode.get() == "bv"
        steps = [
            (1, "获取素材：下载视频 + 字幕 + 弹幕" if bv_mode else "获取素材：必剪免费ASR生成字幕"),
            (2, "字幕纠错：修正ASR常见的识别错误"),
            (3, "弹幕分析：根据弹幕热度找高光片段" if bv_mode else "弹幕分析：无弹幕文件时自动跳过"),
            (4, "自动切片：根据高光时间轴生成视频片段"),
        ]
        for num, desc in steps:
            row = ttk.Frame(self.steps_frame)
            row.pack(fill=tk.X, pady=2)

            # icon + 描述（左对齐，自适应宽度）
            left = ttk.Frame(row)
            left.pack(side=tk.LEFT, fill=tk.X, expand=True)

            icon = tk.Label(left, text="○", font=("Consolas", 13), fg="#666", width=2, anchor="center")
            icon.pack(side=tk.LEFT)

            ttk.Label(left, text=desc, anchor="w").pack(side=tk.LEFT, padx=(4, 0))

            # 状态 + 按钮（右对齐，固定宽度）
            right = ttk.Frame(row)
            right.pack(side=tk.RIGHT)

            status_label = ttk.Label(right, text="等待中", foreground="#888", width=10, anchor="center")
            status_label.pack(side=tk.LEFT, padx=(0, 8))

            btn = ttk.Button(right, text="运行", width=6,
                           command=lambda n=num: self._run_step(n))
            btn.pack(side=tk.LEFT)

            self._step_widgets[num] = {"icon": icon, "status": status_label, "btn": btn}

    # ==========================================
    # Aegisub 集成
    # ==========================================

    def _find_aegisub(self):
        """查找 Aegisub 安装路径"""
        import shutil as _shutil
        # PATH 中查找
        found = _shutil.which("aegisub")
        if found:
            return found
        # 常见安装位置
        for loc in [
            r"C:\Program Files\Aegisub\aegisub.exe",
            r"C:\Program Files (x86)\Aegisub\aegisub.exe",
            str(PROJECT_ROOT.parent / "Aegisub" / "aegisub.exe"),
            str(PROJECT_ROOT / "aegisub" / "aegisub.exe"),
        ]:
            if os.path.exists(loc):
                return loc
        return None

    def _open_in_aegisub(self):
        """在 Aegisub 中打开当前字幕 + 视频进行专业编辑"""
        aegisub = self._find_aegisub()
        if not aegisub:
            if messagebox.askyesno("Aegisub 未安装",
                "未检测到 Aegisub（专业字幕编辑器）。\n\n"
                "Aegisub 支持: 时间轴拖拽、音频波形、视频预览、分轨样式、多选批量标记。\n\n"
                "下载地址: https://github.com/Aegisub/Aegisub/releases\n"
                "（下载 portable 版解压即可使用，无需安装）\n\n"
                "是否现在打开下载页面？"):
                os.startfile("https://github.com/Aegisub/Aegisub/releases")
            return

        srt = self._find_srt_for_check()
        if not srt:
            messagebox.showinfo("提示", "未找到字幕文件。请先生成/下载字幕。")
            return
        video = self._get_selected_video()
        if not video and self._video_list:
            video = Path(self._video_list[0][0])
        if not video or not video.exists():
            messagebox.showinfo("提示", "未找到匹配的视频文件。")
            return

        # 若 SRT 中有 [发言人] 标签，转为 ASS 样式保留
        srt_text = srt.read_text(encoding="utf-8-sig")
        ass_path = srt.with_suffix(".aegisub.ass")
        speaker_styles = set()
        for m in re.findall(r'\[(嘉然|贝拉|乃琳|心宜|思诺|旁白|一起说|ASOUL)\]', srt_text):
            speaker_styles.add(m)
        # 生成基础 ASS（保留原始字幕）
        from core.subtitle_utils import SubtitleUtils
        entries = SubtitleUtils.parse_srt(str(srt))
        with open(ass_path, "w", encoding="utf-8-sig") as f:
            f.write("""[Script Info]
Title: ZhiJiangAutoClip
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""")
            # 发言人样式（用颜色区分）
            speaker_style_defs = {
                "嘉然": "&H00B469FF", "贝拉": "&H00B6599B", "乃琳": "&H00DB9834",
                "心宜": "&H009314FF", "思诺": "&H00D7A0EE", "旁白": "&H00FFFFFF",
                "一起说": "&H0000A5FF", "ASOUL": "&H00FF6A00",
            }
            f.write("Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,10,10,50,1\n")
            for sp_name in speaker_styles:
                color = speaker_style_defs.get(sp_name, "&H00FFFFFF")
                f.write(f"Style: {sp_name},Microsoft YaHei,36,&H00FFFFFF,&H00000000,{color},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,10,10,50,1\n")
            f.write("""
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""")
            for entry in entries:
                start_s = entry["start"]
                end_s = entry["end"]
                s_h, s_r = divmod(start_s, 3600)
                s_m, s_s = divmod(s_r, 60)
                e_h, e_r = divmod(end_s, 3600)
                e_m, e_s = divmod(e_r, 60)
                start_ass = f"{int(s_h)}:{int(s_m):02d}:{int(s_s):02d}.{int((s_s%1)*100):02d}"
                end_ass = f"{int(e_h)}:{int(e_m):02d}:{int(e_s):02d}.{int((e_s%1)*100):02d}"
                text = entry["text"]
                style = "Default"
                sp_match = re.match(r'\[(嘉然|贝拉|乃琳|心宜|思诺|旁白|一起说|ASOUL)\]\s*', text)
                if sp_match:
                    style = sp_match.group(1)
                    text = text[sp_match.end():]
                text = text.replace("\n", "\\N")
                f.write(f"Dialogue: 0,{start_ass},{end_ass},{style},,0,0,0,,{text}\n")

        try:
            subprocess.Popen([aegisub, str(video), str(ass_path)])
            self.log(f"  已在 Aegisub 中打开: {srt.name} + {video.name}")
            messagebox.showinfo("Aegisub 编辑提示",
                "在 Aegisub 中完成编辑后：\n\n"
                "1. 左上角「样式管理器」→ 创建成员专属样式（如「嘉然」「贝拉」）\n"
                "2. 选中字幕行 → 音频面板选择样式\n"
                "3. 用鼠标拖拽调整时间轴\n"
                "4. Ctrl+S 保存 → 回到本程序点击「导入 Aegisub 样式」\n\n"
                "常见: 将 'Default' 替换为成员样式名即可。")
        except Exception as e:
            self.log(f"  启动 Aegisub 失败: {e}")
            messagebox.showerror("启动失败", f"无法启动 Aegisub:\n{e}\n\n路径: {aegisub}")

    def _import_aegisub_styles(self):
        """从 Aegisub 编辑后的 ASS 文件导入样式作为发言人标记"""
        srt = self._find_srt_for_check()
        if not srt:
            messagebox.showinfo("提示", "未找到字幕文件。")
            return
        ass_path = srt.with_suffix(".aegisub.ass")
        if not ass_path.exists():
            # 也检查不带 aegisub 后缀的
            ass_path = srt.with_suffix(".ass")
        if not ass_path.exists():
            messagebox.showinfo("提示",
                f"未找到 Aegisub 编辑后的 ASS 文件。\n\n"
                f"请先在 Aegisub 中打开字幕编辑并保存。\n"
                f"预期路径: {ass_path.name}")
            return

        # 解析 ASS Dialogue 行
        ass_text = ass_path.read_text(encoding="utf-8-sig")
        style_map = {}  # text → style_name
        for line in ass_text.split("\n"):
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) == 10:
                    style = parts[3].strip()
                    text = parts[9].strip().replace("\\N", "\n")
                    if style and style != "Default":
                        style_map[text] = style

        if not style_map:
            messagebox.showinfo("提示", "ASS 中未找到非 Default 样式。\n\n请在 Aegisub 中为字幕行指定成员样式后保存。")
            return

        # 读 SRT → 匹配 → 写回 [发言人] 标签
        srt_text = srt.read_text(encoding="utf-8-sig")
        from core.subtitle_utils import SubtitleUtils
        entries = SubtitleUtils.parse_srt(str(srt))
        count = 0
        for entry in entries:
            if entry["text"] in style_map:
                sp = style_map[entry["text"]]
                count += 1
            else:
                # 模糊匹配：去掉已有标签后匹配
                clean = re.sub(r'^\[.*?\]\s*', '', entry["text"])
                sp = style_map.get(clean, "")
            if sp:
                entry["text"] = f"[{sp}] {re.sub(r'^\[.*?\]\s*', '', entry['text'])}"

        # 写回 SRT
        def _fmt_ts(sec):
            h, m, s_int = int(sec//3600), int((sec%3600)//60), int(sec%60)
            return f"{h:02d}:{m:02d}:{s_int:02d},{int((sec%1)*1000):03d}"
        out = []
        for i, e in enumerate(entries, 1):
            out.append(str(i))
            out.append(f"{_fmt_ts(e['start'])} --> {_fmt_ts(e['end'])}")
            out.append(e["text"])
            out.append("")
        with open(str(srt), "w", encoding="utf-8-sig") as f:
            f.write("\n".join(out))
        self.log(f"  从 Aegisub 导入 {count} 条发言人标记 → {srt.name}")
        messagebox.showinfo("导入完成",
            f"已从 ASS 样式导入 {count} 条发言人标记。\n\n"
            "可切换到「模式2 精确」重新运行第3步。")

    # ==========================================

    def _mark_step(self, num, state):
        """state: 'wait', 'run', 'done', 'fail'"""
        w = self._step_widgets.get(num)
        if not w:
            return
        mapping = {
            "wait": ("○", "#666", "等待中", "#888"),
            "run":  ("◉", "#4af", "运行中...", "#4af"),
            "done": ("✓", "#4a4", "已完成", "#4a4"),
            "fail": ("✗", "#e44", "失败", "#e44"),
        }
        icon, icon_color, text, text_color = mapping.get(state, mapping["wait"])
        w["icon"].config(text=icon, fg=icon_color)
        w["status"].config(text=text, foreground=text_color)
        w["btn"].state(["!disabled"] if state in ("wait", "fail") else ["disabled"])

    def _run_step(self, num):
        bv_mode = self._mode.get() == "bv"
        if num == 1:
            if bv_mode:
                self.run_download_bv()
            else:
                self.run_generate_subtitles()
        elif num == 2:
            self.run_asr_corrector()
        elif num == 3:
            if not bv_mode and not self._has_ass_files():
                self.log("  视频模式下无弹幕文件(.ass)，弹幕分析不可用。")
                self._mark_step(3, "done")
                messagebox.showinfo("跳过", "当前是视频模式且无弹幕文件，弹幕分析已自动跳过。\n\n如需弹幕分析请切换到 BV号下载模式。")
                return
            self.run_danmaku_meta()
        elif num == 4:
            self.run_auto_clip()

    def _ask_skip(self, step_name):
        """弹窗询问：失败后跳过还是重试。全自动模式下自动跳过。"""
        if self._auto_mode.get():
            self.log(f"  全自动模式: 自动跳过「{step_name}」")
            return "skip"
        result = messagebox.askyesnocancel("步骤失败",
            f"「{step_name}」失败了。\n\n"
            "是 = 跳过，继续下一步\n"
            "否 = 重试此步骤\n"
            "取消 = 停止运行")
        if result is None:
            return "stop"
        elif result:
            return "skip"
        else:
            return "retry"

    # ==========================================
    # 第1步（BV路径）：yutto 下载
    # ==========================================

    def run_download_bv(self):
        def task():
            self._apply_env()
            bvid = self.bvid_var.get().strip()
            if not bvid:
                self.log("  ⚠ 未填写 BV 号，跳过。")
                self._mark_step(1, "done")
                return

            # 始终下载到顶层素材根目录，后续 _organize_files 会归类
            target = str(DEFAULT_INPUT_DIR)
            os.makedirs(target, exist_ok=True)
            self.input_dir_var.set(target)
            os.environ["AUTOCLIP_INPUT_DIR"] = target
            sessdata = self.sessdata_var.get().strip()

            # [1/4] 下载视频（bilix → yt-dlp → yutto 三级回退）
            self.log("  [1/4] 下载视频...")
            downloaded = False

            # 尝试 bilix（B站专用异步下载器，最快）
            try:
                import asyncio
                from bilix import DownloaderBilibili
                async def _bilix_dl():
                    async with DownloaderBilibili(video_concurrency=5) as d:
                        await d.get_video(f"https://www.bilibili.com/video/{bvid}", path=target)
                asyncio.run(_bilix_dl())
                if list(Path(target).rglob("*.mp4")) or list(Path(target).rglob("*.flv")):
                    self.log("  ✓ bilix 下载完成")
                    downloaded = True
            except ImportError:
                self.log("  bilix 未安装 (pip install bilix)，使用 yt-dlp...")
            except Exception as e:
                self.log(f"  bilix 失败: {e}")

            # 回退 yt-dlp
            if not downloaded:
                try:
                    import yt_dlp
                    ydl_opts = {
                        "outtmpl": os.path.join(target, "%(title)s.%(ext)s"),
                        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                        "merge_output_format": "mp4",
                        "quiet": True,
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([f"https://www.bilibili.com/video/{bvid}"])
                    self.log("  ✓ yt-dlp 下载完成")
                    downloaded = True
                except Exception as e:
                    self.log(f"  yt-dlp 失败: {e}")

            # 最后回退 yutto
            if not downloaded:
                self.log("  回退 yutto...")
                subprocess.run([
                    sys.executable, "-m", "yutto", "download",
                    f"https://www.bilibili.com/video/{bvid}",
                    "-d", target, "--danmaku-format", "ass",
                ], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target)
                downloaded = True

            # [2/4] B站官方API下载字幕（比yutto可靠）
            self.log("  [2/4] B站官方API下载字幕...")
            if not self._check_srt_exists():
                try:
                    from utils.get_all import BilibiliDownloader
                    dl = BilibiliDownloader(bvid, sessdata, target, auto_correct=False)
                    dl.run(download_video=False, download_subtitle=True,
                           download_danmaku=False, download_all_parts=True)
                except Exception as e:
                    self.log(f"  B站API字幕下载失败: {e}")

            # 下载完成后刷新文件列表，否则 _get_selected_video 找不到文件
            self._check_environment()

            if self._check_srt_exists():
                self.log("  ✓ B站官方字幕已就绪")
            else:
                self.log("  B站无官方字幕，启动 ASR 生成...")
                self._generate_srt_for_video()

            # [3/4] 弹幕下载
            self.log("  [3/4] 弹幕下载...")
            if not self._has_ass_files():
                # yutto已尝试，再用protobuf备用
                try:
                    from utils.get_danmu import DanmakuDownloader
                    DanmakuDownloader(bvid, sessdata, target).run()
                except Exception:
                    pass
            if self._has_ass_files():
                self.log("  ✓ 弹幕已就绪")
            else:
                self.log("  ⚠ 弹幕下载失败（无弹幕将自动跳过分析）")

            # [4/4] 刷新文件列表
            self.log("  [4/4] 下载完成，文件在素材根目录")
            self.log("   💡 勾选出场成员后点击「整理文件」自动归类")
            self._check_environment()
            self._mark_step(1, "done")

        self._run_worker("BV下载", task)

    # ==========================================
    # 第1步（视频路径）：AI 语音识别生成字幕
    # ==========================================

    def run_generate_subtitles(self):
        def task():
            self._apply_env()
            self._generate_srt_for_video()
            self._mark_step(1, "done")
            self._check_environment()

        self._run_worker("必剪免费ASR", task)

    # ==========================================
    # 第2步：字幕纠错
    # ==========================================

    def run_asr_corrector(self):
        def task():
            self._apply_env()
            from utils.ASRCorrector import FileBasedCorrector
            target = self.input_dir_var.get().strip()
            if not os.path.isdir(target):
                self.log(f"  ⚠ 输入目录不存在: {target}")
                self._mark_step(2, "fail")
                return
            corrector = FileBasedCorrector()
            corrector.process_folder(target)
            self._mark_step(2, "done")

        self._run_worker("字幕纠错", task)

    def run_llm_corrector(self):
        """LLM 智能字幕纠错：用大模型修正 ASR 识别错误（需 API Key）"""
        def task():
            self._apply_env()
            from utils.llm_asr_corrector import llm_correct_folder
            target = self.input_dir_var.get().strip()
            if not os.path.isdir(target):
                self.log(f"  ⚠ 输入目录不存在: {target}")
                return
            api_key = self.api_key_var.get().strip()
            if not api_key:
                self.log("  ⚠ 未配置 API Key，LLM 纠错不可用。请先在高级配置中填写。")
                messagebox.showwarning("缺少 API Key", "LLM 智能纠错需要 DeepSeek API Key。\n请展开高级配置 → AI 接口配置中填写。")
                return
            llm_correct_folder(target)
            self.log("  LLM 智能纠错完成 ✓")

        self._run_worker("LLM智能字幕纠错", task)

    def _setup_auto_clip_config(self):
        """配置 Auto_clip.CONFIG，供 run_auto_clip 和 run_all 共用"""
        import Auto_clip
        target = self.input_dir_var.get().strip()
        # input_dir 保留为父级分类目录，保证输出路径包含分类层级
        # （视频/字幕已通过 source_video/srt_file 显式指定，不依赖 auto_detect）
        # input_dir 设为分类目录层级（保证输出路径含分类），视频/字幕已显式指定
        target_path = Path(target).resolve()
        default_path = DEFAULT_INPUT_DIR.resolve()
        if target_path.parent == default_path:
            # target 已是分类目录（如 素材/ASOUL团播）或根目录 → 直接用
            parent_dir = target
        else:
            # target 更深（如 素材/ASOUL团播/06月26日xxx）→ 取父级分类目录
            parent_dir = str(target_path.parent)
        Auto_clip.CONFIG["input_dir"] = parent_dir
        # 输出路径：镜像分类目录结构
        out_base = self.output_dir_var.get().strip()
        category = self._resolve_category()
        Auto_clip.CONFIG["output_dir"] = os.path.join(out_base, category) if category != "未分类" else out_base
        # 直接传递已选中的视频和字幕
        video = self._get_selected_video()
        if video and video.exists():
            Auto_clip.CONFIG["source_video"] = str(video)
        srt = self._get_selected_srt()
        if srt and srt.exists():
            Auto_clip.CONFIG["srt_file"] = str(srt)
        cover_style = self._cover_style_var.get().strip()
        if cover_style:
            if "cover" not in Auto_clip.CONFIG:
                Auto_clip.CONFIG["cover"] = {}
            Auto_clip.CONFIG["cover"]["active_style"] = cover_style
            try:
                Auto_clip.CONFIG["cover"]["count"] = int(self._cover_count_var.get().strip() or "5")
            except ValueError:
                pass
        ds_path = os.path.join(target, "Data_source.txt")
        if not os.path.exists(ds_path):
            ds_path = "Data_source.txt"
        Auto_clip.CONFIG["data_source"] = ds_path
        return Auto_clip

    def run_danmaku_meta(self):
        def task():
            self._apply_env()
            if self._check_analysis_cache():
                if messagebox.askyesno("分析缓存",
                    "已存在分析结果 (Data_source.txt)。\n\n是 = 跳过  否 = 重新分析"):
                    self.log("  ⚡ 使用分析缓存，跳过重分析")
                    self._mark_step(3, "done")
                    return
            from danmu_method.get_data_by_danmu import DanmakuAnalyzer
            analyzer = DanmakuAnalyzer()
            analyzer.run()
            self._mark_step(3, "done")

        self._run_worker("弹幕分析", task)

    def run_auto_clip(self):
        def task():
            self._apply_env()
            target = self.input_dir_var.get().strip()
            ds_path = os.path.join(target, "Data_source.txt")
            if not os.path.exists(ds_path) and not os.path.exists("Data_source.txt"):
                self.log("  无 Data_source.txt，自动生成默认切片条目...")
                self._generate_default_data_source()
                ds_path = os.path.join(target, "Data_source.txt")
            Auto_clip = self._setup_auto_clip_config()
            Auto_clip.main()
            self._mark_step(4, "done")

        self._run_worker("自动切片", task)

    def _find_srt_for_check(self):
        """查找当前视频对应的 SRT 文件（必须同名匹配）"""
        srt = self._get_selected_srt()
        if srt and srt.exists():
            return srt
        video = self._get_selected_video()
        if not video:
            return None
        target = self.input_dir_var.get().strip()
        for pattern in ["*.srt", "*/*.srt"]:
            for f in Path(target).glob(pattern):
                if "__no_danmaku__" in str(f):
                    continue
                if f.stem == video.stem:
                    return f
        return None

    def _check_srt_exists(self):
        """检查输入目录下是否有 SRT 字幕文件"""
        target = self.input_dir_var.get().strip()
        srts = list(Path(target).rglob("*.srt"))
        return len(srts) > 0

    def _has_ass_files(self):
        """检查输入目录下是否有 ASS 弹幕文件"""
        target = self.input_dir_var.get().strip()
        ass_files = list(Path(target).rglob("*.ass"))
        return len(ass_files) > 0

    def _generate_srt_for_video(self):
        """用 ASR 为选中视频生成 SRT 字幕"""
        if self._stop_requested:
            raise InterruptedError("用户终止")
        # 确保文件列表是最新的
        self._check_environment()
        video = self._get_selected_video()
        if not video:
            raise RuntimeError(
                "未找到视频文件。\n"
                "BV模式: 检查BV号是否正确、网络是否通畅\n"
                "视频模式: 请拖入视频文件"
            )

        output_dir = str(video.parent)
        from utils.local_asr import auto_generate_srt_robust
        srt_path = auto_generate_srt_robust(str(video), output_dir)

        # 繁→简转换
        try:
            import zhconv
            with open(srt_path, "r", encoding="utf-8-sig") as f:
                srt_text = f.read()
            simplified = zhconv.convert(srt_text, "zh-cn")
            with open(srt_path, "w", encoding="utf-8-sig") as f:
                f.write(simplified)
            self.log(f"  繁→简转换完成")
        except ImportError:
            pass  # zhconv 未安装则跳过
        except Exception as e:
            self.log(f"  繁→简转换跳过: {e}")

        self.log(f"  字幕已生成: {Path(srt_path).name}")

        # 快速LLM校核（有Key时自动跑，1次API调用不慢）
        if self.api_key_var.get().strip():
            try:
                self.log("  LLM 快速校核字幕...")
                from utils.llm_asr_corrector import quick_llm_correct
                quick_llm_correct(str(srt_path))
            except Exception as e:
                self.log(f"  LLM 校核跳过: {e}")

        self._check_environment()

    def _step1_func(self):
        if self._mode.get() == "bv":
            # BV 路径：调用 yutto 下载
            bvid = self.bvid_var.get().strip()
            if not bvid:
                raise RuntimeError("未填写 BV 号")
            cmd = [
                sys.executable, "-m", "yutto", "download",
                f"https://www.bilibili.com/video/{bvid}",
                "-d", self.input_dir_var.get().strip(),
                "--danmaku-format", "ass",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    cwd=self.input_dir_var.get().strip())
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "")[-400:]
                raise RuntimeError(err if err else "yutto 失败")
            output = (result.stdout or result.stderr or "")[-300:]
            self.log(output if output else "下载完成")

            self._check_environment()
            if not self._check_srt_exists():
                self.log("  B站无官方字幕，启动 ASR 生成...")
                self._generate_srt_for_video()
            else:
                self.log("  已检测到 SRT 字幕文件 ✓")
        else:
            self._generate_srt_for_video()

    def _find_srt_time_range(self):
        """读取 SRT 文件，返回 (earliest_start, latest_end) 时间范围（秒）"""
        target = self.input_dir_var.get().strip()
        srts = list(Path(target).rglob("*.srt"))
        if not srts:
            return 0, 60  # 默认60秒
        srt_path = srts[0]
        import re
        earliest, latest = None, 0
        try:
            with open(srt_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            for match in re.finditer(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", content):
                start_s = self._parse_srt_time(match.group(1))
                end_s = self._parse_srt_time(match.group(2))
                if earliest is None or start_s < earliest:
                    earliest = start_s
                if end_s > latest:
                    latest = end_s
        except Exception:
            pass
        if earliest is None:
            return 0, 60
        return earliest, latest

    @staticmethod
    def _parse_srt_time(time_str):
        """解析 SRT 时间戳为秒数"""
        from core.subtitle_utils import SubtitleUtils
        return SubtitleUtils.parse_srt_time(time_str)

    def _generate_default_data_source(self):
        """无 Data_source.txt 时，基于 SRT 时间范围自动切片（每3分钟一段）"""
        start_s, end_s = self._find_srt_time_range()
        duration = end_s - start_s
        target = self.input_dir_var.get().strip()
        srts = list(Path(target).rglob("*.srt"))
        video_name = srts[0].stem if srts else "未命名"

        # 每段最多3分钟，自动分段
        segment_sec = 180
        clips = []
        seg_start = start_s
        seg_num = 1
        while seg_start < end_s:
            seg_end = min(seg_start + segment_sec, end_s)
            clips.append({
                "timestamp": f"{self._format_seconds(seg_start)}-{self._format_seconds(seg_end)}",
                "title": f"{video_name}#{seg_num}",
                "summary": f"自动切片第{seg_num}段",
                "cover_text_1": video_name[:10],
                "cover_text_2": f"P{seg_num}",
                "highlight_reason": "自动分段"
            })
            seg_start = seg_end
            seg_num += 1

        ds_path = os.path.join(target, "Data_source.txt")
        with open(ds_path, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)
        self.log(f"  已自动生成 {len(clips)} 个默认片段 ({duration/60:.0f}分钟 → 每{segment_sec}秒一段)")

    @staticmethod
    def _format_seconds(seconds):
        from core.subtitle_utils import SubtitleUtils
        return SubtitleUtils.sec_to_srt_time(seconds)

    def run_all(self):
        def task():
            self._apply_env()
            self._reset_steps()
            self._stop_requested = False
            os.environ["AUTOCLIP_STOP"] = "0"

            is_bv = self._mode.get() == "bv"

            for num in range(1, 5):
                if self._stop_requested:
                    self.log("  用户终止，已停止运行。")
                    self._mark_step(num, "fail")
                    break
                if self._step_done.get(num):
                    continue

                name = {1: "素材获取", 2: "字幕纠错", 3: "弹幕分析", 4: "自动切片"}
                self.log(f"\n{'=' * 50}")
                self.log(f"▶ 第{num}步：{name[num]}")
                self.log(f"{'=' * 50}")

                # 视频模式无弹幕时，自动跳过第3步
                if num == 3 and not is_bv:
                    if not self._has_ass_files():
                        self.log("  视频模式下无弹幕文件(.ass)，自动跳过弹幕分析。")
                        self._mark_step(3, "done")
                        self._step_done[3] = True
                        self.after(0, self._update_edit_btn)
                        continue

                # 第4步前检查 Data_source.txt 是否存在
                if num == 4:
                    target = self.input_dir_var.get().strip()
                    ds_path = os.path.join(target, "Data_source.txt")
                    if not os.path.exists(ds_path):
                        alt_path = "Data_source.txt"  # cwd fallback
                        if not os.path.exists(alt_path):
                            self.log("  无 Data_source.txt，自动基于字幕生成默认切片条目...")
                            try:
                                self._generate_default_data_source()
                            except Exception as e:
                                self.log(f"  生成默认片段失败: {e}")
                                self._mark_step(4, "fail")
                                return

                while True:
                    try:
                        self._mark_step(num, "run")
                        if num == 1:
                            self._step1_func()
                            self._apply_env()
                            # 下载/生成完成后自动按成员归类，归类后刷新环境变量
                            self._auto_organize_for_analysis()
                            self._apply_env()
                        elif num == 2:
                            from utils.ASRCorrector import FileBasedCorrector
                            target = self.input_dir_var.get().strip()
                            # 用实际目录而非 __no_danmaku__（此时文件已到位）
                            FileBasedCorrector().process_folder(target)
                            # LLM 智能纠错改为手动触发（高级配置按钮），自动流程太慢
                        elif num == 3:
                            # 缓存检查：Data_source.txt 比 SRT 新则跳过重分析
                            if self._check_analysis_cache() and self._auto_mode.get():
                                self.log("  ⚡ 检测到有效分析缓存，自动跳过（全自动模式）")
                                self._step_done[3] = True
                                self._mark_step(3, "done")
                                continue
                            if self._check_analysis_cache():
                                if messagebox.askyesno("分析缓存",
                                    "已存在分析结果 (Data_source.txt)。\n\n"
                                    "是 = 跳过，直接使用缓存\n"
                                    "否 = 重新分析（覆盖旧结果）"):
                                    self.log("  ⚡ 使用分析缓存，跳过重分析")
                                    self._step_done[3] = True
                                    self._mark_step(3, "done")
                                    continue
                            from danmu_method.get_data_by_danmu import DanmakuAnalyzer
                            analyzer = DanmakuAnalyzer()
                            analyzer.run()
                            self._step_done[3] = True
                            if self._pause_var.get():
                                self.log("  ⏸ 分析后暂停 — 即将打开高光校核器...")
                                self._mark_step(3, "done")
                                self.after(200, self._edit_highlight_review)
                                return  # 暂停，不继续第4步
                        elif num == 4:
                            self._setup_auto_clip_config()
                            import Auto_clip
                            Auto_clip.main()
                        self._mark_step(num, "done")
                        self._step_done[num] = True
                        break
                    except Exception:
                        self.log_error(name[num], sys.exc_info())
                        self._mark_step(num, "fail")
                        choice = self._ask_skip(name[num])
                        if choice == "stop":
                            self.log("  用户取消，停止运行。")
                            return
                        elif choice == "skip":
                            self.log(f"  跳过第{num}步。")
                            self._mark_step(num, "done")
                            self._step_done[num] = True
                            break

        self._run_worker("一键全部流程", task)


def main():
    app = AppLauncher()
    app.mainloop()


if __name__ == "__main__":
    import builtins
    # 启动前快照：万一 _LogWriter 挂了，用这个救命
    _real_print = builtins.print if hasattr(builtins, 'print') else lambda *a, **k: None
    try:
        _real_print("[启动] 正在初始化...", file=_sys_stderr_backup)
        main()
    except Exception as e:
        import datetime as _dt
        _crash_log = PROJECT_ROOT / f"crash_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        _tb = traceback.format_exc()
        _real_print(_tb, file=_sys_stderr_backup)
        with open(_crash_log, "w", encoding="utf-8") as _f:
            _f.write(_tb)
        _real_print(f"\n崩溃日志已保存: {_crash_log}", file=_sys_stderr_backup)
        try:
            import tkinter.messagebox as _mb
            _mb.showerror("启动失败", f"程序崩溃，详情见:\n{_crash_log}")
        except Exception:
            pass
