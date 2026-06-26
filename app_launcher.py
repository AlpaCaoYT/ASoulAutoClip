import os
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
DEFAULT_INPUT_DIR = PROJECT_ROOT / "workspace" / "video_input"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "workspace" / "clip_output"


def _load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class AppLauncher(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        # 日志文件（必须在 _LogWriter.bind 前创建，防止早期 print 找不到路径）
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_name = datetime.now().strftime("session_%Y%m%d_%H%M%S.log")
        self._log_path = LOG_DIR / log_name
        _LogWriter.bind(self)  # 绑定日志重定向目标

        self.title("ASoulAutoClip 工作台")
        self.geometry("1000x820")
        self.minsize(800, 600)

        saved = _load_config()

        self.input_dir_var = tk.StringVar(value=saved.get("input_dir", str(DEFAULT_INPUT_DIR)))
        self.output_dir_var = tk.StringVar(value=saved.get("output_dir", str(DEFAULT_OUTPUT_DIR)))
        self.api_key_var = tk.StringVar(value=os.environ.get("SILICONFLOW_API_KEY", saved.get("api_key", "")))
        self.api_base_var = tk.StringVar(value=os.environ.get("SILICONFLOW_BASE_URL", saved.get("api_base", "https://api.deepseek.com/chat/completions")))
        self.api_model_var = tk.StringVar(value=os.environ.get("SILICONFLOW_MODEL", saved.get("api_model", "deepseek-v4-pro")))
        self.bvid_var = tk.StringVar(value=os.environ.get("BILIBILI_VIDEO_INPUT", saved.get("bvid", "")))
        self.sessdata_var = tk.StringVar(value=os.environ.get("BILIBILI_SESSDATA", saved.get("sessdata", "")))

        self._running = False
        self._advanced_showing = False
        self._mode = tk.StringVar(value="bv")  # "bv" 或 "video"
        self._step_done = {1: False, 2: False, 3: False, 4: False}
        self._step_widgets = {}
        self._error_count = {}  # 错误分类计数

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
        self.log_text = tk.Text(log_box, height=8, wrap=tk.WORD, relief=tk.FLAT,
                                bg="#151515", fg="#eaeaea", insertbackground="#ffffff",
                                font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

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
        ttk.Label(header, text="ASoulAutoClip 工作台", style="Title.TLabel").pack(anchor="w")

        # 环境状态条
        self.status_frame = ttk.Frame(root)
        self.status_frame.pack(fill=tk.X, pady=(0, 8))
        self.ffmpeg_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.ffmpeg_label.pack(side=tk.LEFT, padx=(0, 16))
        self.python_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.python_label.pack(side=tk.LEFT, padx=(0, 16))
        self.input_label = ttk.Label(self.status_frame, text="检测中...", foreground="#999")
        self.input_label.pack(side=tk.LEFT)

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

        # 输入目录 — 下拉框独占一行
        in_top = ttk.Frame(paths_box)
        in_top.grid(row=row_idx, column=0, sticky="ew", pady=3); row_idx += 1
        ttk.Label(in_top, text="输入目录", width=8).pack(side=tk.LEFT)
        self._input_display_var = tk.StringVar()
        self.input_combo = ttk.Combobox(in_top, textvariable=self._input_display_var, state="readonly")
        self.input_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        # 按钮单独一行
        in_btns = ttk.Frame(paths_box)
        in_btns.grid(row=row_idx, column=0, sticky="ew", pady=(0, 3)); row_idx += 1
        ttk.Button(in_btns, text="浏览文件夹", command=self.choose_input_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(in_btns, text="刷新列表", command=self._refresh_input_list).pack(side=tk.LEFT, padx=(0, 6))
        self._combo_count_label = ttk.Label(in_btns, text="", foreground="#888")
        self._combo_count_label.pack(side=tk.LEFT)
        self._refresh_input_list()

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
        ttk.Button(member_row, text="全选/取消", width=9,
                   command=_toggle_all).pack(side=tk.LEFT, padx=(8, 0))

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
        ttk.Button(flow_bottom, text="编辑切片数据",
                   command=self._edit_data_source).pack(side=tk.RIGHT, padx=(4, 0))

        # 快捷工具
        util_row = ttk.Frame(actions)
        util_row.pack(fill=tk.X)
        ttk.Button(util_row, text="打开输入文件夹",
                   command=lambda: os.startfile(self.input_dir_var.get())).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="打开输出文件夹",
                   command=lambda: os.startfile(self.output_dir_var.get())).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="用 Kdenlive 打开",
                   command=self._open_kdenlive).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(util_row, text="查看日志文件",
                   command=lambda: os.startfile(str(LOG_DIR))).pack(side=tk.LEFT)

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

        self.log("准备就绪。按照 1→2→3→4 的顺序点击即可。\n"
                 "或者直接点「一键运行全部流程」.");

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
            media = list(input_path.glob("*"))
            videos = [f for f in media if f.suffix.lower() in {".mp4", ".flv", ".mkv", ".mov", ".ts"}]
            srts = [f for f in media if f.suffix.lower() == ".srt"]
            ass = [f for f in media if f.suffix.lower() == ".ass"]

            parts = []
            if videos:
                parts.append(f"视频({len(videos)}个)")
            if srts:
                parts.append(f"字幕({len(srts)}个)")
            if ass:
                parts.append(f"弹幕({len(ass)}个)")

            if parts:
                self.input_label.config(text=f"输入目录: {'，'.join(parts)}", foreground="#4a4")
            else:
                self.input_label.config(text="输入目录: 空（请放入素材）", foreground="#c90")
        else:
            self.input_label.config(text="输入目录: 不存在", foreground="#e44")

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
        if base.exists():
            for child in sorted(base.iterdir()):
                if child.is_dir():
                    self._input_dirs[child.name] = str(child)
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

    def _on_combo_select(self, event=None):
        name = self.input_combo.get()
        full = self._input_dirs.get(name)
        if full:
            self.input_dir_var.set(full)
            self._input_display_var.set(name)
            self._check_environment()

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
        """结构化报错：分类 + 计数 + 写日志"""
        key = type(exc_info[1]).__name__ if exc_info and exc_info[1] else "UnknownError"
        self._error_count[key] = self._error_count.get(key, 0) + 1
        msg = f"[{step_name}] {key}: {exc_info[1]}"
        self.log(msg, level="ERROR")
        self.log(traceback.format_exc(), level="TRACE")

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
        self.log("  接口地址：https://api.deepseek.com/chat/completions")
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
        """尝试用 Kdenlive 打开输出目录"""
        out_dir = self.output_dir_var.get().strip()
        if not os.path.isdir(out_dir):
            messagebox.showinfo("提示", "输出目录还不存在，请先运行一次切片。")
            return
        # 找出最新的子目录（最近生成的片段）
        try:
            subs = sorted(Path(out_dir).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            latest = next((s for s in subs if s.is_dir()), Path(out_dir))
            os.startfile(str(latest))
            self.log(f"[快捷] 已打开: {latest.name}")
        except Exception as e:
            self.log(f"[快捷] 打开失败: {e}")
            os.startfile(out_dir)

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
                messagebox.showerror("运行失败", f"{title} 失败，详情看日志。")
            finally:
                self._running = False
                self._set_buttons_state(True)

        self._running = True
        self._set_buttons_state(False)
        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons_state(self, enabled):
        try:
            self.btn_all.state(["!disabled"] if enabled else ["disabled"])
        except Exception:
            pass

    def _apply_env(self):
        # 强制 UTF-8 编码，防止 Windows GBK 环境打印 emoji 崩溃
        os.environ["PYTHONIOENCODING"] = "utf-8"
        os.environ["PYTHONUTF8"] = "1"
        os.environ["AUTOCLIP_INPUT_DIR"] = self.input_dir_var.get().strip()
        os.environ["AUTOCLIP_OUTPUT_DIR"] = self.output_dir_var.get().strip()
        os.environ["DANMU_INPUT_DIR"] = self.input_dir_var.get().strip()
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

    def _update_mode_hint(self):
        bv_mode = self._mode.get() == "bv"
        if bv_mode:
            self.mode_hint.config(
                text="在上方填写 BV 号 → 自动下载视频、字幕和弹幕 → 分析弹幕找高光 → 切片。需要 AI API Key。")
        else:
            self.mode_hint.config(
                text="将视频拖入上方拖放区 → 必剪免费生成字幕 → 自动切片。无需 API Key 即可使用。")

    def _on_mode_change(self):
        """切换素材来源模式时刷新步骤描述"""
        self._update_mode_hint()
        self._reset_steps()

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
        """弹窗询问：失败后跳过还是重试"""
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
            self.log("  使用 yutto 下载视频+字幕+弹幕...")
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
                raise RuntimeError(err if err else "yutto 下载失败")
            self.log("下载完成，检测字幕文件...")
            if not self._check_srt_exists():
                self.log("  ⚠ B站无官方字幕，自动用必剪免费 ASR 生成...")
                self._generate_srt_for_video()
            else:
                self.log("  已检测到 SRT 字幕 ✓")
            self._mark_step(1, "done")

        self._run_worker("BV下载 (yutto)", task)

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
    # 第1步（旧）：下载弹幕和字幕
    # ==========================================

    def run_download_danmaku(self):
        def task():
            self._apply_env()
            bvid = self.bvid_var.get().strip()
            if not bvid:
                self.log("  ⚠ 未填写 BV 号，跳过下载。")
                self.log("  如果你已有弹幕和字幕文件，直接放在输入目录即可。")
                return
            from utils.get_all import BilibiliDownloader
            downloader = BilibiliDownloader(
                bvid,
                self.sessdata_var.get().strip(),
                self.input_dir_var.get().strip(),
                auto_correct=True,
            )
            downloader.run(
                download_video=False,
                download_subtitle=True,
                download_danmaku=True,
                download_all_parts=True,
            )

        self._run_worker("下载弹幕和字幕", task)

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

    def run_danmaku_meta(self):
        def task():
            self._apply_env()
            from danmu_method.get_data_by_danmu import DanmakuAnalyzer
            analyzer = DanmakuAnalyzer()
            analyzer.run()
            self._mark_step(3, "done")

        self._run_worker("弹幕分析", task)

    def run_auto_clip(self):
        def task():
            self._apply_env()
            target = self.input_dir_var.get().strip()
            import Auto_clip
            Auto_clip.CONFIG["input_dir"] = target
            Auto_clip.CONFIG["output_dir"] = self.output_dir_var.get().strip()
            # 封面配置
            cover_style = self._cover_style_var.get().strip()
            if cover_style:
                if "cover" not in Auto_clip.CONFIG:
                    Auto_clip.CONFIG["cover"] = {}
                Auto_clip.CONFIG["cover"]["active_style"] = cover_style
                try:
                    Auto_clip.CONFIG["cover"]["count"] = int(self._cover_count_var.get().strip() or "5")
                except ValueError:
                    pass
            # 确保 data_source 指向正确路径
            ds_path = os.path.join(target, "Data_source.txt")
            if not os.path.exists(ds_path):
                ds_path = "Data_source.txt"
            if not os.path.exists(ds_path):
                self.log("  无 Data_source.txt，自动生成默认切片条目...")
                self._generate_default_data_source()
                ds_path = os.path.join(target, "Data_source.txt")
            Auto_clip.CONFIG["data_source"] = ds_path
            Auto_clip.main()
            self._mark_step(4, "done")

        self._run_worker("自动切片", task)

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
        """用 ASR 为视频生成 SRT 字幕：优先必剪免费，失败回退 Whisper API"""
        target = self.input_dir_var.get().strip()
        video_exts = {".mp4", ".flv", ".mkv", ".mov", ".ts"}
        videos = [f for f in Path(target).rglob("*") if f.suffix.lower() in video_exts]
        if not videos:
            raise RuntimeError(f"在 {target} 中未找到视频文件（支持: {', '.join(sorted(video_exts))}）")
        # 优先选最大的视频文件（通常是最完整的录播）
        videos.sort(key=lambda f: f.stat().st_size, reverse=True)
        video = videos[0]
        size_mb = video.stat().st_size / (1024 * 1024)
        self.log(f"  找到 {len(videos)} 个视频，选用: {video.name} ({size_mb:.0f} MB)")

        # 自动 ASR 链路：必剪 → Whisper API → 报错
        from utils.whisper_asr import auto_generate_srt
        srt_path = auto_generate_srt(str(video), target)
        self.log(f"  字幕已生成: {Path(srt_path).name}")

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

            # 检测 SRT 字幕是否存在
            if not self._check_srt_exists():
                self.log("  ⚠ B站无官方字幕，自动用必剪免费 ASR 生成...")
                self._generate_srt_for_video()
            else:
                self.log("  已检测到 SRT 字幕文件 ✓")
        else:
            # 视频路径：必剪免费 ASR 生成字幕
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
        time_str = time_str.replace(",", ".")
        h, m, s = time_str.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _generate_default_data_source(self):
        """无弹幕时，基于 SRT 时间范围生成一个默认全段切片条目"""
        start_s, end_s = self._find_srt_time_range()
        start_str = self._format_seconds(start_s)
        end_str = self._format_seconds(end_s)
        # 从 SRT 文件名推断标题
        target = self.input_dir_var.get().strip()
        srts = list(Path(target).rglob("*.srt"))
        video_name = srts[0].stem if srts else "未命名"
        clip = [{
            "timestamp": f"{start_str}-{end_str}",
            "title": f"{video_name}（字幕版）",
            "summary": "基于必剪ASR字幕生成的完整片段",
            "cover_text_1": video_name[:10],
            "cover_text_2": "字幕版",
            "highlight_reason": "字幕识别完成"
        }]
        ds_path = os.path.join(target, "Data_source.txt")
        with open(ds_path, "w", encoding="utf-8") as f:
            json.dump(clip, f, ensure_ascii=False, indent=2)
        self.log(f"  已生成默认片段: {ds_path}")

    @staticmethod
    def _format_seconds(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def run_all(self):
        def task():
            self._apply_env()
            self._reset_steps()

            is_bv = self._mode.get() == "bv"

            for num in range(1, 5):
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
                        elif num == 2:
                            from utils.ASRCorrector import FileBasedCorrector
                            FileBasedCorrector().process_folder(self.input_dir_var.get().strip())
                            # 字典纠错后自动跑 LLM 智能纠错（有 API Key 时）
                            if self.api_key_var.get().strip():
                                try:
                                    from utils.llm_asr_corrector import llm_correct_folder
                                    self.log("  自动运行 LLM 智能纠错...")
                                    llm_correct_folder(self.input_dir_var.get().strip())
                                except Exception as e:
                                    self.log(f"  LLM 纠错跳过: {e}")
                        elif num == 3:
                            from danmu_method.get_data_by_danmu import DanmakuAnalyzer
                            analyzer = DanmakuAnalyzer()
                            analyzer.run()
                            self._step_done[3] = True
                        elif num == 4:
                            import Auto_clip
                            target = self.input_dir_var.get().strip()
                            Auto_clip.CONFIG["input_dir"] = target
                            Auto_clip.CONFIG["output_dir"] = self.output_dir_var.get().strip()
                            # 封面配置
                            cover_style = self._cover_style_var.get().strip()
                            if cover_style:
                                if "cover" not in Auto_clip.CONFIG:
                                    Auto_clip.CONFIG["cover"] = {}
                                Auto_clip.CONFIG["cover"]["active_style"] = cover_style
                                try:
                                    Auto_clip.CONFIG["cover"]["count"] = int(self._cover_count_var.get().strip() or "5")
                                except ValueError:
                                    pass
                            # 确保 data_source 指向正确路径
                            ds_path = os.path.join(target, "Data_source.txt")
                            if not os.path.exists(ds_path):
                                ds_path = "Data_source.txt"
                            Auto_clip.CONFIG["data_source"] = ds_path
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
                            self._mark_step(num, "done")
                            self._step_done[num] = True
                            break

        self._run_worker("一键全部流程", task)


def main():
    app = AppLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()
