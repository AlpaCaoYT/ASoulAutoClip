"""LLM 字幕纠错器 — 使用大模型智能修正 ASR 识别错误。
基于 VideoCaptioner 的 LLM 优化思路，利用成员知识库提升字幕准确率。
"""

import json
import os
import re
from pathlib import Path

import requests


def _load_api_config():
    return {
        "api_key": os.environ.get("SILICONFLOW_API_KEY", "").strip(),
        "base_url": os.environ.get("SILICONFLOW_BASE_URL", "https://api.deepseek.com/v1/chat/completions"),
        "model": os.environ.get("SILICONFLOW_MODEL", "deepseek-v4-pro"),
    }


CORRECTION_PROMPT = """你是一个专业的字幕校对员，熟悉枝江娱乐（A-SOUL + 闪耀舞台）的成员和粉丝文化。请修正以下 ASR 语音识别产生的错误。

## 成员及常见术语
- 嘉然（然然/然比）：A-SOUL 可爱担当，粉丝名"嘉心糖"
- 贝拉（拉姐/贝拉拉/队长）：A-SOUL 队长+舞蹈担当，粉丝名"贝极星"
- 乃琳（乃老师/乃宝）：A-SOUL MC+声乐，粉丝名"奶淇琳"
- 心宜（小海豹/保洁阿宜）：闪耀舞台元气担当，粉丝名"心球仪"
- 思诺（铁柱 爱称）：闪耀舞台 vocal 担当，粉丝名"小海诺"
- CP 名：乃贝、琳嘉女孩、超级嘉贝、小心思
- 常见词：切片、弹幕、高光、录播、团播、单播、嘉心糖、贝极星、奶淇琳、心球仪、小海诺
- 常见梗：夹子音、身高梗、安心、山药、铁柱、坏女人（爱称）、厨房杀手、植物杀手

## 纠错原则
1. 只修正明显的 ASR 识别错误（同音字/近音字误识别）
2. 成员名、粉丝名、CP 名、圈内梗必须准确
3. 保持原有的语气、口癖和说话风格
4. 不要润色或改变句子的表达方式
5. 不要改变说话人的意思
6. 如果原文正确，就原样保留

## 待校对文本
{raw_text}

## 输出格式
只输出校对后的纯文本，不要加任何解释或标记。每行对应输入的一行。"""


def correct_srt_with_llm(srt_path, output_path=None):
    """使用 LLM 智能修正 SRT 字幕文件中的识别错误。

    Args:
        srt_path: 输入 SRT 文件路径
        output_path: 输出路径（默认覆盖原文件）

    Returns:
        修正后的 SRT 文件路径
    """
    api = _load_api_config()
    if not api["api_key"]:
        raise RuntimeError("未配置 API Key，无法使用 LLM 字幕纠错。请在 GUI 高级配置中填写。")

    # 读取 SRT
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # 解析 SRT 块
    blocks = re.split(r"\n\n+", content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            time_match = re.match(
                r"(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)", lines[1]
            )
            if time_match:
                entries.append({
                    "index": lines[0].strip(),
                    "start": time_match.group(1),
                    "end": time_match.group(2),
                    "text": "\n".join(lines[2:]).strip(),
                })

    if not entries:
        print("  SRT 文件为空或格式无法识别，跳过 LLM 纠错。")
        return srt_path

    print(f"  已解析 {len(entries)} 条字幕，开始 LLM 智能纠错...")

    # 分批次处理（每批最多 30 条，避免 token 超限）
    BATCH_SIZE = 30
    corrected_entries = []

    for batch_start in range(0, len(entries), BATCH_SIZE):
        batch = entries[batch_start : batch_start + BATCH_SIZE]
        batch_end = min(batch_start + BATCH_SIZE, len(entries))

        # 构建输入文本（只提交文字部分）
        text_lines = [f"{e['text']}" for e in batch]
        raw_text = "\n".join(text_lines)

        prompt = CORRECTION_PROMPT.format(raw_text=raw_text)

        try:
            resp = requests.post(
                api["base_url"],
                headers={
                    "Authorization": f"Bearer {api['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": api["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
                timeout=120,
            )

            if resp.status_code == 200:
                corrected_text = (
                    resp.json()["choices"][0]["message"]["content"].strip()
                )
                corrected_lines = corrected_text.split("\n")
                # 确保行数匹配
                for i, entry in enumerate(batch):
                    if i < len(corrected_lines) and corrected_lines[i].strip():
                        entry["text"] = corrected_lines[i].strip()
                print(f"    批次 {batch_start // BATCH_SIZE + 1}: 已修正 {len(batch)} 条")
            else:
                print(f"    批次 {batch_start // BATCH_SIZE + 1}: API 错误 {resp.status_code}，保留原文")
        except Exception as e:
            print(f"    批次 {batch_start // BATCH_SIZE + 1}: 请求失败 ({e})，保留原文")

        corrected_entries.extend(batch)

    # 重建 SRT 文件
    output_lines = []
    for i, entry in enumerate(corrected_entries, 1):
        output_lines.append(str(i))
        output_lines.append(f"{entry['start']} --> {entry['end']}")
        output_lines.append(entry["text"])
        output_lines.append("")

    srt_output = "\n".join(output_lines)

    if output_path is None:
        output_path = srt_path

    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write(srt_output)

    print(f"  LLM 纠错完成，已保存到: {output_path}")
    return output_path


def llm_correct_folder(folder_path):
    """对文件夹内所有 SRT 文件执行 LLM 智能纠错。

    Args:
        folder_path: 目标文件夹路径
    """
    target = Path(folder_path)
    if not target.exists():
        print(f"  目录不存在: {folder_path}")
        return

    srts = list(target.rglob("*.srt"))
    if not srts:
        print(f"  目录中无 SRT 文件，跳过 LLM 纠错。")
        return

    print(f"\n{'=' * 50}")
    print(f"LLM 智能字幕纠错 — 共 {len(srts)} 个 SRT 文件")
    print(f"{'=' * 50}")

    for srt_path in srts:
        print(f"\n处理: {srt_path.name}")
        try:
            correct_srt_with_llm(str(srt_path))
        except Exception as e:
            print(f"  失败: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        llm_correct_folder(sys.argv[1])
    else:
        target = os.environ.get("ASR_TARGET_FOLDER", "workspace/video_input")
        llm_correct_folder(target)
