import re
import os
import json
import requests
from collections import defaultdict
from pathlib import Path

# ==============================================================================
# [配置区域]
# ==============================================================================

class FileConfig:

    # 输入文件夹路径 (修改为你的文件夹路径)
    # 程序会自动在此文件夹下寻找唯一的 .ass 和 .srt 文件
    # .srt代表字幕文件，其中包含字幕信息
    # .ass代表弹幕文件，其中包含弹幕信息
    # 一定要确定该输入文件夹下有且仅有一个 .ass 和 .srt 文件，如果有多个字幕文件或者弹幕文件，程序会报错
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    INPUT_DIR = os.environ.get("DANMU_INPUT_DIR", str(PROJECT_ROOT / "workspace" / "video_input"))
    
    # 输出文件
    OUTPUT_FILE = 'Data_source.txt'

class ApiConfig:
    # LLM API 设置
    API_KEY = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
    MODEL_NAME = os.environ.get("SILICONFLOW_MODEL", "deepseek-v4-pro")
    TIMEOUT = 60    # 请求超时时间

# 成员出场状态（默认全部出场，可通过 GUI 或环境变量覆盖）
_DEFAULT_MEMBER_STATUS = {
    "嘉然": 1,
    "贝拉": 1,
    "乃琳": 1,
    "心宜": 1,
    "思诺": 1,
}

def _load_member_status():
    env_val = os.environ.get("AUTOCLIP_MEMBER_STATUS", "")
    if env_val:
        try:
            return json.loads(env_val)
        except Exception:
            pass
    return dict(_DEFAULT_MEMBER_STATUS)

MEMBER_STATUS = _load_member_status()

class AnalyzeConfig:
    # 算法参数
    TOP_N = 25              # 初始筛选出多少个高光片段（数量越大，最终筛选出的高光片段也越多，推荐填写20-30）
    WINDOW_SIZE = 10        # 密度计算的时间窗口(秒)，表示计算每多少秒内的热度（推荐填写10）
    MIN_DENSITY = 5         # 最小热度阈值（推荐填写5）
    MERGE_THRESHOLD = 10    # 合并间隔
    MIN_DURATION = 10       # 最小保留时长
    MAX_DURATION = 140       # 最大保留时长

# 弹幕权重
DANMAKU_WEIGHTS = [
    (r'警告|绷|笑死|名场面|锐评|蚌埠住了|甜甜甜|小情侣', 2.0),
    (r'[?\uff1f]', 1.8),
    (r'(哈{2,}|h{3,}|[啊]{2,})', 1.5),
    (r'可爱捏|急了|牛', 1.2)
]
LONG_TEXT_BONUS = (15, 1.2) 

# Prompt 模板
PROMPT_TEMPLATE = """
你是一位熟悉枝江娱乐（A-SOUL + 闪耀舞台）的粉丝剪辑UP主，需要为{broadcast_type}的一个高能片段生成元数据。

你的风格是真诚分享而非营销号。观众点进视频是为了重温片段，不要让他们觉得"又被标题骗了"。

### 片段信息
- 类型: {broadcast_type}
- 出场: {active_members}
- 文件: {filename}

### 字幕内容
{subtitle_text}

### 观众弹幕反应
{danmaku_text}

### 成员速查
- 嘉然（然然/然比）：A-SOUL可爱担当，甜美温柔+偶尔毒舌反差。身高自嘲坦然正面，夹子音撒娇（弹幕刷"夹起来了"），突发恶疾，"安心"梗。嘉心糖宠她她也宠嘉心糖。
- 贝拉（拉姐/贝拉拉/队长）：A-SOUL队长+舞蹈担当，帅气与憨憨并存。魔性笑声、植物杀手、和贝极星拉扯对线是经典。对嘉然有特别的偏爱。"牛"梗非强关联不要提。
- 乃琳（乃老师/乃宝）：A-SOUL MC+声乐，温柔御姐+"坏女人"小腹黑（粉丝宠溺爱称）。厨房杀手、考驾照漫长历程、面试以为来做配音。控场救场担当。
- 心宜（小海豹/保洁阿宜）：闪耀舞台元气担当，魔性笑声感染力强、动物模仿有趣、游戏下饭但嘴硬、口音梗。新衣服少（保洁阿宜）。和思诺的"小心思"CP自然温暖。
- 思诺（铁柱 粉丝宠溺爱称）：闪耀舞台vocal担当（美声高音极强），高冷人设经常崩塌、毒舌吐槽但嘴硬心软、被问到心宜容易脸红。对A-SOUL师姐很崇拜。王者/考研非强关联不要提。
- CP：乃贝（贝拉x乃琳，队长xMC，损友互怼甜度极高）、琳嘉女孩（嘉然x乃琳，互宠甜蜜）、超级嘉贝（贝拉x嘉然，队长偏爱守护）、小心思（心宜x思诺，元气x高冷，甜度极高）。
- 跨团：枝江大家庭——闪耀舞台是A-SOUL后辈，对师姐有敬仰，师姐对后辈温暖关照。

### 生成要求
1. **title（B站标题）**：用一句完整的话概括片段内容，信息量优先。使用成员昵称（然然/拉姐/乃老师/心宜/思诺）。禁止使用"震惊""绷不住了""全场疯掉""炸裂"等夸张前缀。

2. **summary（片段概括）**：粉丝视角客观描述，1-2句。写清楚谁说了什么、做了什么。

3. **cover_text_1（封面大字，3-10字）**：提炼核心事件，清晰直接。

4. **cover_text_2（封面小字，3-10字）**：补充细节或弹幕反应，与大字互补。

5. **highlight_reason（高光理由）**：解释为什么粉丝喜欢这段，标注涉及的梗、CP或成员关系。

### 禁止事项（红线）
- 禁止任何盒信息（真人身份）相关内容
- 禁止恶意调侃身体特征（身高梗可以提但必须正面/中性）
- 禁止将成员玩笑曲解为真正矛盾
- "坏女人""铁柱"是粉丝爱称，必须体现宠溺感
- CP提及应温馨自然，不强行营业，不过度解读
- 禁止震惊体标题、制造对立感的表述
- 禁止引战、拉踩、对比其他V/团

### JSON 输出格式
只输出纯净 JSON 对象：
{{
  "title": "一句话完整概括，信息量充足",
  "summary": "粉丝视角客观描述",
  "cover_text_1": "封面大字（核心事件）",
  "cover_text_2": "封面小字（细节/反应）",
  "highlight_reason": "为什么粉丝喜欢 + 涉及什么梗/关系"
}}
"""
# ==============================================================================
# [核心逻辑]
# ==============================================================================

class DanmakuAnalyzer:
    def __init__(self):
        # 每次初始化时重新加载成员出场状态（支持 GUI 动态修改）
        global MEMBER_STATUS
        MEMBER_STATUS = _load_member_status()

        # 初始化时执行自动文件检测
        self.input_dir = self._resolve_input_dir(FileConfig.INPUT_DIR)
        self.ass_file = None
        self.srt_file = None

        # 数据容器
        self.danmaku_data = []
        self.subtitle_data = []

        # 执行检测
        self._auto_detect_files()

    def _contains_media_files(self, folder_path):
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            return False

        has_ass = any(child.is_file() and child.suffix.lower() == '.ass' for child in folder.iterdir())
        has_srt = any(child.is_file() and child.suffix.lower() == '.srt' for child in folder.iterdir())
        return has_ass and has_srt

    def _resolve_input_dir(self, input_dir):
        candidate = Path(input_dir)
        if not candidate.exists():
            return str(candidate)

        if candidate.is_file():
            return str(candidate.parent)

        if self._contains_media_files(candidate):
            return str(candidate)

        session_dirs = [path for path in candidate.iterdir() if path.is_dir() and self._contains_media_files(path)]
        if not session_dirs:
            return str(candidate)

        session_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        selected_dir = session_dirs[0]
        print(f"📁 检测到输入根目录，自动选择最近的素材目录: {selected_dir.name}")
        return str(selected_dir)

    def _auto_detect_files(self):
        """检测 ASS 和 SRT 文件（优先使用环境变量指定的文件）"""
        env_ass = os.environ.get("AUTOCLIP_ASS_FILE", "").strip()
        env_srt = os.environ.get("AUTOCLIP_SRT_FILE", "").strip()

        # 使用 GUI 指定的文件
        if env_ass and os.path.exists(env_ass):
            self.ass_file = env_ass
            print(f"✅ 使用指定弹幕: {os.path.basename(env_ass)}")
        if env_srt and os.path.exists(env_srt):
            self.srt_file = env_srt
            print(f"✅ 使用指定字幕: {os.path.basename(env_srt)}")

        # 未指定则自动检测
        if not self.ass_file or not self.srt_file:
            print(f"正在扫描文件夹: {self.input_dir}")
            if not os.path.exists(self.input_dir):
                raise FileNotFoundError(f"文件夹不存在 -> {self.input_dir}")

            files = os.listdir(self.input_dir)
            ass_files = [f for f in files if f.lower().endswith('.ass')]
            srt_files = [f for f in files if f.lower().endswith('.srt')]

            if not self.ass_file:
                if len(ass_files) == 0:
                    raise FileNotFoundError("在文件夹中未找到 .ass 弹幕文件")
                self.ass_file = os.path.join(self.input_dir, ass_files[0])
                print(f"✅ 已锁定弹幕文件: {ass_files[0]}")

            if not self.srt_file:
                if len(srt_files) == 0:
                    raise FileNotFoundError("在文件夹中未找到 .srt 字幕文件")
                self.srt_file = os.path.join(self.input_dir, srt_files[0])
                print(f"✅ 已锁定字幕文件: {srt_files[0]}")

        print("-" * 50)

    def parse_ass_time(self, time_str):
        try:
            parts = time_str.split(':')
            if len(parts) != 3:
                raise ValueError("格式不是 H:M:S")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            return 0
    
    def parse_srt_time(self, time_str):
        try:
            time_str = time_str.replace(',', '.')
            parts = time_str.split(':')
            if len(parts) != 3:
                raise ValueError("格式不是 H:M:S")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            return 0
    
    def load_danmaku(self):
        if not os.path.exists(self.ass_file):
            print(f"错误: 找不到文件 {self.ass_file}")
            return []
        try:
            with open(self.ass_file, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return []
        
        pattern = r'Dialogue:\s*(\d+),(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),(.*)'
        matches = re.findall(pattern, content)
        count = 0
        
        for match in matches:
            try:
                start_time = self.parse_ass_time(match[1])
                raw_text = match[9]
                clean_text = re.sub(r'\{[^}]*\}', '', raw_text).strip()
                clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_text)
                if clean_text and start_time > 0:
                    self.danmaku_data.append({'time': start_time, 'text': clean_text})
                    count += 1
            except Exception:
                continue
        self.danmaku_data.sort(key=lambda x: x['time'])
        print(f"✓ 成功加载 {count} 条弹幕")
        return self.danmaku_data
    
    def load_subtitles(self):
        if not os.path.exists(self.srt_file):
            print(f"错误: 找不到文件 {self.srt_file}")
            return []
        try:
            with open(self.srt_file, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return []
        
        blocks = content.strip().split('\n\n')
        count = 0
        for block in blocks:
            try:
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    time_match = re.match(r'(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)', lines[1])
                    if time_match:
                        self.subtitle_data.append({
                            'start': self.parse_srt_time(time_match.group(1)),
                            'end': self.parse_srt_time(time_match.group(2)),
                            'text': ' '.join(lines[2:]).strip()
                        })
                        count += 1
            except Exception:
                continue
        print(f"✓ 成功加载 {count} 条字幕")
        return self.subtitle_data
    
    def get_danmaku_weight(self, text):
        weight = 1.0
        for pattern, score in DANMAKU_WEIGHTS:
            if re.search(pattern, text, re.IGNORECASE):
                weight = max(weight, score)
        limit, bonus = LONG_TEXT_BONUS
        if len(text) >= limit:
            weight = max(weight, bonus)
        return weight

    def calculate_density(self):
        if not self.danmaku_data: return {}, {}
        raw_score = defaultdict(float)
        raw_count = defaultdict(int)
        for danmaku in self.danmaku_data:
            t = int(danmaku['time'])
            weight = self.get_danmaku_weight(danmaku['text'])
            raw_score[t] += weight
            raw_count[t] += 1

        if not raw_score: return {}, {}
        max_time = max(raw_score.keys())
        min_time = min(raw_score.keys())
        window_size = AnalyzeConfig.WINDOW_SIZE
        score_map = {}
        count_map = {}

        # 滑动窗口 O(n)：初始化第一个窗口
        curr_score = sum(raw_score.get(min_time + i, 0) for i in range(window_size))
        curr_count = sum(raw_count.get(min_time + i, 0) for i in range(window_size))

        for t in range(min_time, max_time + 1):
            if curr_score > 0:
                score_map[t] = curr_score
                count_map[t] = curr_count
            # 滑动：减去离开窗口的秒，加上进入窗口的秒
            out_t = t
            in_t = t + window_size
            curr_score -= raw_score.get(out_t, 0)
            curr_count -= raw_count.get(out_t, 0)
            curr_score += raw_score.get(in_t, 0)
            curr_count += raw_count.get(in_t, 0)

        print(f"✓ 计算密度: 窗口大小={window_size}秒, 有效时间点={len(score_map)}")
        return score_map, count_map

    def find_highlights(self):
        score_map, count_map = self.calculate_density()
        if not score_map: return []
            
        all_scores = sorted(score_map.values(), reverse=True)
        if not all_scores: return []

        p90 = all_scores[int(len(all_scores) * 0.1)]
        target_min = AnalyzeConfig.MIN_DENSITY
        adaptive_min = p90 if target_min > p90 else target_min
        
        # === 详细输出：高分窗口 ===
        high_score_windows = [(t, s) for t, s in score_map.items() if s >= adaptive_min]
        high_score_windows.sort(key=lambda x: x[1], reverse=True)
        
        print(f"\n找到 {len(high_score_windows)} 个高分时间窗口 (阈值: {adaptive_min:.1f})")
        print("前20个高分窗口:")
        for i, (t, s) in enumerate(high_score_windows[:20], 1):
            print(f"  {i}. {self.format_time(t)} - 热度:{s:.1f} - 弹幕数:{count_map.get(t,0)}")

        if not high_score_windows: return []

        # 合并逻辑
        raw_highlights = []
        top_limit = AnalyzeConfig.TOP_N * 5
        lookback = 5
        
        for start_time, score in high_score_windows[:top_limit]:
            raw_highlights.append({
                'start': max(0, start_time - lookback),
                'end': start_time + AnalyzeConfig.WINDOW_SIZE + lookback,
                'score': score,
                'count': count_map.get(start_time, 0)
            })
        raw_highlights.sort(key=lambda x: x['start'])
        
        merged = []
        for h in raw_highlights:
            if not merged:
                merged.append(h)
                continue
            last = merged[-1]
            gap = h['start'] - last['end']
            if gap <= AnalyzeConfig.MERGE_THRESHOLD:
                last['end'] = max(last['end'], h['end'])
                last['score'] = max(last['score'], h['score'])
                last['count'] += h['count']
                if last['end'] - last['start'] > AnalyzeConfig.MAX_DURATION:
                    last['end'] = last['start'] + AnalyzeConfig.MAX_DURATION
            else:
                merged.append(h)
        
        final_highlights = []
        for h in merged:
            duration = h['end'] - h['start']
            if AnalyzeConfig.MIN_DURATION <= duration <= AnalyzeConfig.MAX_DURATION:
                final_highlights.append(h)
        
        final_highlights.sort(key=lambda x: x['score'], reverse=True)
        final_highlights = final_highlights[:AnalyzeConfig.TOP_N]
        final_highlights.sort(key=lambda x: x['start'])
        
        # === 详细输出：最终选中片段 ===
        print(f"\n✓ 最终选中 {len(final_highlights)} 个高光片段:")
        for i, h in enumerate(final_highlights, 1):
            duration = h['end'] - h['start']
            print(f"  片段{i}: {self.format_time(h['start'])} - {self.format_time(h['end'])} "
                  f"(高光时长:{duration:.0f}秒)"
                  f"(弹幕:{h['count']}, 热度:{h['score']:.1f})")
        
        return final_highlights

    def _resolve_prompt_template(self, broadcast_desc):
        """根据广播类型加载对应的提示词文件，失败则回退到默认 PROMPT_TEMPLATE"""
        # 映射广播类型到文件名
        prompt_dir = Path(__file__).resolve().parent.parent / "prompt_method"
        member_name = broadcast_desc.replace("单播", "").replace("团播", "")
        # 单播 → 成员专属提示词
        if "单播" in broadcast_desc:
            candidate = prompt_dir / f"prompt_{member_name}单播.txt"
        else:
            candidate = prompt_dir / f"prompt_{broadcast_desc}.txt"
        # 尝试加载
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
        # 回退
        return PROMPT_TEMPLATE

    def generate_summary_with_ai(self, highlight):
        start, end = highlight['start'], highlight['end']
        if not ApiConfig.API_KEY:
            print("未配置 API Key，无法调用 LLM 生成摘要。请在高级配置中填写 API Key。")
            return {"title": "未配置 API Key", "summary": "", "cover_text_1": "", "cover_text_2": "", "highlight_reason": "请先在设置中填写 API Key"}
        # 弹幕往前回溯5秒，往后延迟5秒，最多保留40条不重复弹幕（控制 prompt 长度）
        danmaku_context = [d['text'] for d in self.danmaku_data if start-5 <= d['time'] <= end+5]
        danmaku_text = '\n'.join([f"- {t}" for t in list(set(danmaku_context))[:40]]) or "(无弹幕)"
        # 字幕往前回溯25秒，往后延迟5秒，最多保留30条（控制 prompt 长度）
        sub_context = [s for s in self.subtitle_data if s['start'] <= end+5 and s['end'] >= start-25]
        sub_text = '\n'.join([f"{self.format_time(s['start'])}: {s['text']}" for s in sub_context[:30]]) or "(无字幕)"

        active_members = [k for k, v in MEMBER_STATUS.items() if v == 1]

        aso_set = {"嘉然", "贝拉", "乃琳"}
        ss_set = {"心宜", "思诺"}
        active_set = set(active_members)
        has_aso = bool(active_set & aso_set)
        has_ss = bool(active_set & ss_set)

        if len(active_members) == 1:
            broadcast_desc = f"{active_members[0]}单播"
            broadcast_type = broadcast_desc
        elif len(active_members) == 2:
            # 双播
            if active_set == {"心宜", "思诺"}:
                broadcast_desc = "小心思双播"
                broadcast_type = "小心思双播（心宜 × 思诺）"
            elif active_set.issubset(aso_set):
                broadcast_desc = "A-SOUL双播"
                names = " × ".join(sorted(active_members))
                broadcast_type = f"A-SOUL双播（{names}）"
            elif active_set.issubset(ss_set):
                broadcast_desc = "闪耀舞台双播"
                broadcast_type = "闪耀舞台双播"
            else:
                broadcast_desc = "A-SOUL双播"
                broadcast_type = "双播"
        elif len(active_members) > 2:
            # 团播
            broadcast_desc = "A-SOUL团播"
            if has_aso and has_ss:
                broadcast_type = "枝江团播（A-SOUL + 闪耀舞台）"
            elif has_aso:
                broadcast_type = "A-SOUL团播"
            else:
                broadcast_type = "闪耀舞台团播"
        else:
            broadcast_desc = "A-SOUL直播"
            broadcast_type = "A-SOUL直播"

        # 尝试加载成员专属提示词，失败则用默认模板
        prompt_template = self._resolve_prompt_template(broadcast_desc)
        prompt = prompt_template.format(
            broadcast_type=broadcast_type,
            active_members=', '.join(active_members),
            filename=os.path.basename(self.srt_file),
            subtitle_text=sub_text,
            danmaku_text=danmaku_text
        )

        # 根据在场人员给出简短提示，不硬性约束
        all_members = {"嘉然", "贝拉", "乃琳", "心宜", "思诺"}
        absent = all_members - active_set
        if absent:
            who_absent = "、".join(sorted(absent))
            prompt += (
                f"\n### 提示\n"
                f"当前片段出场成员: {', '.join(active_members)}（{who_absent} 不在场）。\n"
                f"根据字幕实际内容自然生成标题——字幕说了什么就写什么。\n"
                f"字幕里真的涉及不在场成员或CP互动时才提，没提到就别编。\n"
            )

        try:
            response = requests.post(
                ApiConfig.BASE_URL,
                headers={'Authorization': f'Bearer {ApiConfig.API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': ApiConfig.MODEL_NAME,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.7,
                    'max_tokens': 1200
                },
                timeout=ApiConfig.TIMEOUT
            )

            if response.status_code == 200:
                ai_content = response.json()['choices'][0]['message']['content']
                # 鲁棒 JSON 提取：尝试多种格式
                result_json = self._parse_ai_json(ai_content)
                if result_json is None:
                    print(f"  ⚠ AI 返回无法解析为 JSON，原文: {ai_content[:200]}...")
                    return {
                        "title": ai_content[:80] or "AI解析失败",
                        "summary": "",
                        "cover_text_1": "",
                        "cover_text_2": "",
                        "highlight_reason": ""
                    }
                time_str = f"{self.format_time(start)}-{self.format_time(end)}"
                result_json = {
                    "timestamp": time_str,
                    **result_json,
                }
                return result_json
            else:
                print(f"  ⚠ AI API 返回错误: {response.status_code} {response.text[:200]}")
                return {"title": f"AI错误 HTTP{response.status_code}"}
        except Exception as e:
            print(f"  ⚠ AI 生成失败: {e}")
            return {"title": f"AI失败: {str(e)[:60]}"}

    def _parse_ai_json(self, ai_content):
        """从 AI 响应中提取 JSON，支持多种格式"""
        # 尝试1: 直接解析
        try:
            return json.loads(ai_content.strip())
        except json.JSONDecodeError:
            pass

        # 尝试2: 去除 markdown 代码块
        cleaned = re.sub(r'```(?:json)?\s*\n?', '', ai_content)
        cleaned = re.sub(r'\n?\s*```', '', cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 尝试3: 提取第一个 { 到最后一个 } 之间的内容
        match = re.search(r'\{.*\}', ai_content, re.S)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # 尝试4: 修复常见错误后重试
        try:
            fixed = cleaned.replace('：', ':').replace('，', ',').replace('""', '"')
            fixed = re.sub(r',\s*}', '}', fixed)
            fixed = re.sub(r',\s*]', ']', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        return None

    def format_time(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def run(self):
        print("=" * 70)
        print("A-SOUL 弹幕高光片段分析器 v3.1 (自动文件扫描版)")
        print("=" * 70)
        print("成员出场配置:")
        for k, v in MEMBER_STATUS.items():
            status = "✓ 出场" if v == 1 else "✗ 未出场"
            print(f"  {k}: {status}")
        print()
        
        self.load_danmaku()
        self.load_subtitles()
        
        if not self.danmaku_data: return

        highlights = self.find_highlights()
        if not highlights:
            print("[提示] 未找到高光片段")
            return

        print("\n" + "=" * 70)
        print("生成AI总结...")
        print("=" * 70)
        
        results = []
        for i, h in enumerate(highlights, 1):
            duration = h['end'] - h['start']

            print(f"\n[{i}/{len(highlights)}] 分析片段中...")
            ai_info = self.generate_summary_with_ai(h)
            
            # 默认值填充，防止KeyError
            title = ai_info.get('title', 'AI生成失败')
            summary = ai_info.get('summary', '无')
            reason = ai_info.get('highlight_reason', '无')
            
            print(f"  高光时段: {self.format_time(h['start'])} - {self.format_time(h['end'])} ({duration:.0f}秒)")
            print(f"  标题: {title}")
            print(f"  概括: {summary}")
            print(f"  原因: {reason}")
            
            result_item = {
                'index': i,
                'raw_start': h['start'],
                'raw_end': h['end'],
                'duration': duration,
                'score': h['score'],
                'danmaku_count': h['count'],
                **ai_info
            }
            results.append(result_item)
            
        # === 最终汇总 ===
        print("\n" + "=" * 70)
        print("最终汇总 (按热度排序)")
        print("=" * 70)
        
        sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
        for res in sorted_results:
            print(f"\n【片段 {res['index']}】(热度排名: 第{sorted_results.index(res) + 1})")
            print(f"高光时段: {self.format_time(res['raw_start'])} - {self.format_time(res['raw_end'])}")
            print(f"时长: {res['duration']:.0f}秒")
            print(f"弹幕: {res['danmaku_count']}条 | 热度: {res['score']:.1f}")
            print(f"标题: {res.get('title', '')}")
            print(f"概括: {res.get('summary', '')}")
            print(f"原因: {res.get('highlight_reason', '')}")
        
        self.export(results) 

    def export(self, results):
        try:
            output_keys = ['timestamp', 'title', 'summary', 'cover_text_1', 'cover_text_2', 'highlight_reason']
            simple_data = [{k: v for k, v in r.items() if k in output_keys} for r in results]

            # 输出到 input_dir 而非 cwd，确保后续步骤能找到
            output_path = os.path.join(self.input_dir, FileConfig.OUTPUT_FILE)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(simple_data, f, ensure_ascii=False, indent=2)

            print(f"标准格式结果已保存到: {output_path}")

        except Exception as e:
            print(f"导出失败: {e}")

if __name__ == "__main__":
    try:
        app = DanmakuAnalyzer()
        app.run()
    except SystemExit:
        pass # 允许正常退出
    except Exception as e:
        print(f"程序运行出错: {e}")