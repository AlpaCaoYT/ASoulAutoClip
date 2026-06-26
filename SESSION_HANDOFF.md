# 会话交接文件

> 上一轮完成了大量修复和优化，以下是可以直接告诉下一个 AI 的全部信息。

---

## 项目是什么

ZhiJiangAutoClip — A-SOUL/闪耀舞台 直播自动切片工具，带 tkinter GUI。
双击 `run_app.bat` 启动。入口 `app_launcher.py`。

## 两条工作流

| | BV号模式 | 视频模式 |
|---|---|---|
| 第1步 | yutto 下载视频+字幕+弹幕(ASS) | 必剪免费ASR 生成SRT字幕 |
| 第2步 | ASRCorrector 字幕纠错 | 同左 |
| 第3步 | DanmakuAnalyzer 弹幕密度→DeepSeek→Data_source.txt | 无弹幕时自动跳过 |
| 第4步 | Auto_clip 读取Data_source.txt→ffmpeg切片 | 自动基于SRT生成默认全段切片 |

## 关键修复（本轮已完成）

1. **GBK emoji 崩溃** — `app_launcher.py` 顶部 `_LogWriter` 类接管全局 `sys.stdout`，所有 print 输出路由到 tkinter Text 控件 + UTF-8 日志文件
2. **视频模式流程断裂** — 无弹幕时第3步自动跳过，第4步基于 SRT 时间轴自动生成 Data_source.txt
3. **sys.exit() 杀进程** — `get_data_by_danmu.py`、`auto_detect.py` 全部改为 `raise`
4. **GV号输入位移动** — 从「操作流程」移到「素材路径」区域上方
5. **第4步重复纠错移除** — `Auto_clip.main()` 不再调用 `auto_correct_subtitles()`
6. **Data_source.txt 路径** — 从 cwd 改为 input_dir 输出

## 提示词体系（本轮全面重写）

### 核心原则：反标题党，粉丝真诚分享
- 禁用"震惊""绷不住了""全场疯掉""炸裂"等夸张前缀
- 信息量优先 > 情绪渲染
- 粉丝视角 > 营销口吻
- 成员昵称自然使用（然然/拉姐/乃老师/心宜/思诺）

### 文件结构
- `danmu_method/get_data_by_danmu.py` — 默认 `PROMPT_TEMPLATE`（含五人速查表+红线）
- `prompt_method/prompt_A-SOUL团播.txt` — 团播专属
- `prompt_method/prompt_{成员}单播.txt` — 嘉然/贝拉/乃琳/心宜/思诺 各一份
- `成员知识库.md` — 完整五人档案（梗、CP、红线、粉丝文化）

### 加载逻辑
`DanmakuAnalyzer._resolve_prompt_template()` 按 `broadcast_desc` 自动匹配文件，找不到回退到 PROMPT_TEMPLATE。

## 成员速查（用于提示词调优）

| 成员 | 粉丝名 | 昵称 | 核心标签 |
|------|--------|------|---------|
| 嘉然 | 嘉心糖 | 然然/然比 | 可爱担当、身高自嘲、夹子音、毒舌反差、安心梗 |
| 贝拉 | 贝极星 | 拉姐/贝拉拉 | 队长+舞蹈、山药姐、魔性笑声、植物杀手、偏爱嘉然 |
| 乃琳 | 奶淇琳 | 乃老师/乃宝 | MC+gamer、坏女人(爱称)、厨房杀手、考驾照、面试梗 |
| 心宜 | 心球仪 | 小海豹/保洁阿宜 | 元气+跳舞、魔性笑声、动物模仿、游戏下饭 |
| 思诺 | 小海诺 | 铁柱(爱称) | vocal(美声)、高冷崩人设、毒舌吐槽、对心宜嘴硬 |

### CP
- 乃贝（贝拉x乃琳）—甜度极高
- 琳嘉女孩（嘉然x乃琳）—互宠
- 超级嘉贝（贝拉x嘉然）—队长偏爱
- 小心思（心宜x思诺）—元气x高冷，甜度极高

### 绝对红线
- 禁止盒信息（真人身份）
- 禁止恶意人身攻击
- 禁止将玩笑曲解为真正矛盾
- "坏女人""铁柱"是爱称，必须体现宠溺感
- CP提及温馨自然，不强行营业

## 关键文件路径

```
app_launcher.py          — GUI主入口（LogWriter、步骤调度、_generate_default_data_source）
Auto_clip.py             — 切片主逻辑（VideoProcessor、ffmpeg）
danmu_method/get_data_by_danmu.py — 弹幕分析+AI元数据生成
utils/ASRCorrector.py    — 字幕纠错（读asr_dict.txt）
utils/bcut_asr.py        — 必剪免费ASR（B站接口，无需Key）
core/video_processor.py  — 单片段处理（ASS字幕+ffmpeg切片+封面）
core/cover_generator.py  — 封面图生成（PIL+ffmpeg截图）
core/subtitle_utils.py   — ASS字幕生成（含5成员配色）
prompt_method/           — 6个成员专属提示词txt
成员知识库.md            — 完整参考文档
app_config.json          — GUI配置持久化
workspace/logs/          — 会话日志
workspace/video_input/   — 默认输入目录
workspace/clip_output/   — 默认输出目录
```

## 已知待办

1. 窗口布局在小屏幕可能显示不全
2. 必剪ASR偶尔限流报错
3. 缺少视频播放预览
4. 打包成exe（PyInstaller）
5. 弹幕分析Prompt可根据实际效果继续微调

## 用户偏好

- 不喜欢复杂术语，界面要简洁
- 目标是做成可分发的一体化软件
- 已安装Kdenlive用于手动精修

## 本轮新增功能（2026-06-26）

### 1. 成员出场标记配置
- **位置**: GUI "操作流程"区域 → "成员出场标记" LabelFrame
- **功能**: 5个复选框（嘉然/贝拉/乃琳/心宜/思诺），默认全选
- **全选/取消** 按钮一键切换
- **持久化**: 保存到 `app_config.json` → `member_status` 字段
- **传递**: `_apply_env()` → `AUTOCLIP_MEMBER_STATUS` 环境变量（JSON）
- **接收端**: `danmu_method/get_data_by_danmu.py` 每次初始化 `DanmakuAnalyzer` 时重新加载 `MEMBER_STATUS`
- **影响**: 决定弹幕分析的 `broadcast_desc`（单播/团播）和活跃成员列表，进而影响提示词选择

### 2. 标题编辑器
- **位置**: "一键运行全部流程"按钮右侧 → "编辑切片数据"按钮
- **功能**: 打开编辑窗口，左侧片段列表 + 右侧可编辑字段
  - 时间戳（只读）
  - 标题 / 摘要 / 封面大字 / 封面小字 / 高光理由
- **导航**: "上一个"/"下一个"按钮，列表点击
- **保存**: "全部保存并关闭"按钮，写回 `Data_source.txt`
- **方法**: `app_launcher.py` → `_edit_data_source()` + `_ds_nav()`

### 3. 封面配置
- **位置**: 高级配置 → "封面配置" LabelFrame
- **风格选择**: Combobox 下拉（style1-4），含实时提示文字
  - style1: 上白下黄震撼风格（双分屏）
  - style2: 上黄下白震撼风格（双分屏）
  - style3: 居中大字醒目风格
  - style4: 艺术简洁风格（毛玻璃背景）
- **封面数量**: Spinbox (1-10)
- **持久化**: 保存到 `app_config.json` → `cover_style` / `cover_count`
- **传递**: `_apply_env()` → `AUTOCLIP_COVER_STYLE` / `AUTOCLIP_COVER_COUNT`
- **Auto_clip.py**: CONFIG 中已添加完整 cover 样式定义 + 环境变量回退
- **重要变更**: `Auto_clip.main()` 中 `generate_cover` 改为 `True`，主流程现在会生成封面

### 4. 关键代码路径变更

```
app_launcher.py:
  __init__:104-113    → 新增 member_vars + cover_style/count 变量
  _build_ui:257-275   → 新增成员出场标记 UI
  _build_ui:307-330   → 新增封面配置 UI（高级配置内）
  _save_config:445-447 → 保存 member_status + cover 配置
  _apply_env:787-791  → 传递 AUTOCLIP_MEMBER_STATUS + AUTOCLIP_COVER_*
  _edit_data_source   → 新增标题编辑器方法
  _ds_nav             → 新增编辑器导航方法
  _update_cover_hint  → 新增封面风格提示
  run_auto_clip       → 传递封面配置到 Auto_clip.CONFIG
  run_all:1350-1359   → 传递封面配置到 Auto_clip.CONFIG

danmu_method/get_data_by_danmu.py:
  _load_member_status → 新增：从环境变量加载成员出场状态
  DanmakuAnalyzer.__init__ → 每次初始化重新加载 MEMBER_STATUS

Auto_clip.py:
  CONFIG["cover"]     → 新增完整样式定义（style1-4）+ 环境变量回退
  main():generate_cover → False → True（主流程现在生成封面）
```

## 知识库对齐优化（2026-06-26 第二轮）

基于 `成员知识库.md` 全面校核，从粉丝切片UP主视角修复了以下不一致：

### CP名称修正（全项目）
| 旧（错误） | 新（正确） | 说明 |
|-----------|-----------|------|
| 嘉晚饭 | **琳嘉女孩** | 嘉然x乃琳的正确CP名 |
| 贝嘉 | **超级嘉贝** | 贝拉x嘉然的正确CP名 |
| （缺失） | **乃贝** | A-SOUL最热门CP，贝拉x乃琳 |

### 成员代表色修正
`core/subtitle_utils.py`、`Auto_clip.py`、`core/regen_script.py` 三处颜色已统一为知识库官方代表色：
- 嘉然：粉色 #FF69B4（旧色 #9972F0 紫 → 正）
- 贝拉：紫色 #9B59B6（旧色 #747DDB 蓝 → 正）
- 乃琳：蓝色 #3498DB（旧色 #906657 棕 → 正）
- 心宜：金色 #FFD700（旧色 #9555FF 紫粉 → 正）
- 思诺：银色 #C0C0C0（旧色 #C889A8 粉 → 正）

### 提示词内容修正（6个文件全部重写）
- **prompt_嘉然单播.txt**：CP名修正、新增"安心"梗/书法/画画/圣嘉然
- **prompt_贝拉单播.txt**："牛"梗改为"非强关联不要提"、新增山药姐/偏爱嘉然
- **prompt_乃琳单播.txt**：删除虚构的"手滑取关多邻国"细节、CP名修正
- **prompt_A-SOUL团播.txt**：全面重写，新增乃贝CP、闪耀舞台成员、小心思CP、枝江大家庭跨团关系
- **prompt_心宜单播.txt**：足球改为"非强关联不要提"、新增保洁阿宜/口音梗
- **prompt_思诺单播.txt**：王者/考研改为"非强关联不要提"、补充铁柱来源

### 广播类型检测优化
`danmu_method/get_data_by_danmu.py` `generate_summary_with_ai()`：
- 新增 A-SOUL / 闪耀舞台 成员集合判断
- 多人出场时根据实际成员组成生成准确的 `broadcast_type`（A-SOUL团播/闪耀舞台团播/枝江团播）
- 单播逻辑不变

### PROMPT_TEMPLATE 回退模板
`danmu_method/get_data_by_danmu.py` 中的默认 PROMPT_TEMPLATE 同步更新：
- 所有CP名修正
- 新增乃贝CP和跨团关系
- 成员描述与知识库对齐
- 新增引战/拉踩禁止项

