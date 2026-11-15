# 界面更新计划：左侧主菜单新增「死活题」

> 2026-11-14 更新：下文是历史设计，不代表当前实现或已获得题库许可。当前版本扩展已有 `tsumego.py` / `ui/tsumego_page.py`，提供自制 CC0 离线示例、SGF 导入、稳定内容 ID、错题/未完成筛选、局部棋盘、提示及逐步答案。答案明确区分正解、错误和未知，提示后完成与独立完成分开记录。未移植 KaTrain 的代码或题库；未下载 tasuki、sanderland 或现代书籍题集。任何第三方转写数据都必须单独核验授权，不因古籍年代或仓库代码许可直接推断可再分发。无答案或劫相关题不使用 KataGo ownership/胜率阈值认证解出。

## 目标

在左侧主菜单（现有「人机对战」「AI 分析」）下方新增第三个模式 **「死活题」**。用户可以选择题集、逐题做题，落子后由程序判断对错，并让 KataGo 应对、给出提示或演示答案。完成进度保存在本地。

不在本次范围：在线题库、账号同步、题目编辑器、计时比赛模式。

## 题库来源（GitHub 调研结果）

| 仓库 | 内容 | 格式 | 是否带答案 | 许可 / 风险 | 结论 |
|---|---|---|---|---|---|
| [tasuki/tsumego](https://github.com/tasuki/tsumego) `books/problems/*.txt` | 古典题集：玄玄棋经 (xxqj)、官子谱、碁经众妙、死活妙机、发阳论、碁经精妙、忘忧清乐集，另含 cho-1/2/3 | 简单 ASCII 盘面，已统一为**黑先**，19 路，局部放在左上角 | **否**（导入时丢弃了解答节点） | 仓库没有 LICENSE 文件；但古典题集是几百年前的作品，**盘面属于公有领域** | ✅ **推荐作为内置题库**（只取古典题集，不取 cho-*） |
| [benjaminmantle/baduk-study-material](https://github.com/benjaminmantle/baduk-study-material) `02-life-and-death/` | 分级题、特殊形状、古典题集、360/648/408 题、赵治勋等的 SGF 压缩包 | SGF（含变化/答案） | 是 | 自述为「灰色授权」的社区扫描版；只有仓库原创文字是 CC0 | ⚠️ 不打包；作为「用户自行导入 SGF」的推荐来源写进文档 |
| [sanderland/tsumego](https://github.com/sanderland/tsumego) `problems/` | 约 1 万题：赵治勋死活大全、石榑郁郎、山田公夫、桥本宇太郎、手筋大全等 | JSON：`AB`/`AW`/`SZ`/`C`/`SOL` | 是（仅第一手） | MIT 只覆盖代码；题目来自**仍受版权保护**的现代书籍 | ❌ 不打包；可支持导入该 JSON 格式 |

**结论**：安装包只内置 tasuki/tsumego 中的古典公有领域题集（转换成本项目使用的 SGF），在关于/README 中注明来源并致谢。现代题集只通过「导入题集」功能由用户自行加载。
> 待确认：发布前联系 tasuki 确认可以再分发转写数据，或者直接从公有领域的原始 SGF 重新转写。

古典题集的难度偏高（大致业余初段以上）。入门用户需要导入题集，这一点要在界面上写清楚。

## 关键设计

### 1. 判题方式
内置的古典题集**没有答案**，所以判题依靠 KataGo，带答案的 SGF 优先使用自带的答案树：

- **有答案树的题（导入的 SGF 或 JSON）**：用户的着法在答案树里，并且所在分支标记为正解（`C[Correct]`/`RIGHT`、`TE`、sanderland 的 `SOL`），就判为正确；不在答案树里就判错。对手的应着取答案树中的下一手。
- **没有答案的题（内置古典题）**：
  1. 先用**死活题框架（tsumego frame）**把盘面其余区域填满，避免 KataGo 去看全局大场。算法可参考 KaTrain 的 `tsumego_frame.py`（MIT 许可，移植时保留署名）。
  2. 用户落子后，对加框盘面执行 `kata-analyze`，访问数固定（例如 CPU 版 400，GPU 版 1500），并带上 `ownership`。
  3. 正解判断：用户的着法是首选，或者和首选相比目差损失不超过阈值（例如 2 目），**并且**目标棋块的 ownership 往预期方向发展。
  4. 对手应着用 KataGo 的首选着法。所有关键棋块的 ownership 绝对值都超过 0.85，或者手数达到上限时，判定为「已解决」。
  5. 界面注明「AI 判定」，与「题目自带答案」区分开。
- 判题过程中盘面不可操作，状态栏显示「AI 判题中」，沿用现有的 `State.ANALYZING` 和 `AI_THINKING` 流程。

### 2. 数据模型（新增 `src/katago_gui/tsumego/`）
- `model.py`：`Problem`（id、题集、尺寸、`AB`/`AW`、先行方、目标描述「黑先活 / 白先杀 / 劫」、可选答案树、显示区域 bbox）；`Collection`（名称、来源、许可说明、难度、题目列表）。
- `loader.py`：
  - 读取内置题集（打包进 `katago_gui/data/tsumego/*.sgf`，一个题集一个 SGF 集合文件）；
  - 导入 SGF：沿用 `sgf.py` 的解析器，但要**保留变化分支**。当前 `parse_sgf` 只恢复主线，需要新增一个 `parse_sgf_tree()`，不改变原有的 `load_sgf` 行为；
  - 导入 sanderland JSON。
- `frame.py`：死活题框架 + 计算问题区域 bbox。
- `judge.py`：两种判题器（答案树 / KataGo），接口统一为 `judge(problem, moves) -> Verdict(correct|wrong|continue, reply, note)`。
- `progress.py`：进度（每题 未做 / 正确 / 错误、最近一题）存到 `app_home()/tsumego_progress.json`，用 `atomic_write_text` 写入。
- 一次性转换脚本 `tools/import_tasuki.py`：把 tasuki 的 `.txt` 转成 SGF。这个脚本不打包，生成的数据放进仓库。

### 3. 控制器
- `InteractionMode` 新增 `TSUMEGO = "tsumego"`。
- `GameController` 新增 `load_problem(problem)`、`tsumego_play(point)`、`tsumego_reset()`、`tsumego_hint()`、`tsumego_show_solution()`；切换模式沿用 `set_mode` 的现有流程：先停止分析，再同步引擎。
- 加载题目时把盘面同步给引擎：`clear_board`，然后用 `set_position` 摆子；引擎不支持时退回逐子 `play`。这一步不计入棋谱手数，也不把棋谱标记为 dirty。
- 死活题模式下禁用 SGF 导出、停一手、认输和数子。切换到其他模式时恢复切换前的对局，或者按现有规则提示保存。

### 4. 界面（`main_window.py` 及新增的 `ui/tsumego_panel.py`）
- **左侧主菜单**：在 `btn_analysis` 下面加 `self.btn_tsumego = QPushButton(tr('死活题'))`，设为 checkable，连接到 `enter_tsumego_mode`。三个按钮用 `QButtonGroup` 互斥。`_update_controls` 改为按 `c.mode` 设置三个按钮的选中状态，`_retranslate` 也加上这个按钮。
- **菜单栏**：「视图」菜单新增「死活题模式」动作，与 `analysis_mode_action` 并列。
- **右侧面板**：用 `QStackedWidget` 切换「对局控制 + 分析/着手列表」和新的 `TsumegoPanel`：
  - 题集下拉框（内置题集 + 已导入题集）、「导入题集…」按钮；
  - 题号网格或列表，用颜色区分未做、正确和错误，并显示完成比例；
  - 当前题信息：`黑先活` / `白先杀`、来源，以及判题方式（自带答案 / AI 判定）；
  - 按钮：上一题、下一题、随机、重做、提示（在棋盘上标出首选点）、显示答案（逐手演示）；
  - 结果条：「✔ 正解」「✘ 失败，可重做」「继续，对方应于 X」。
- **棋盘**：`BoardWidget` 新增 `set_view_region(bbox)`，只显示问题区域并留 1–2 路边距、坐标保持正确；加载题目时自动裁剪。正解或失败时用不同颜色标记最后一手。
- **标题栏**：`workspace_title` 显示 `死活题 · {题集} · 第 {n}/{N} 题 · 黑先`。
- **国际化**：在 `i18n_ui.py` 中补充所有新文案的英文翻译（死活题 → Life & Death，黑先活 → Black to live，等）。

### 5. 打包与文档
- `pyproject.toml` 增加 package-data，把 `data/tsumego/*.sgf` 打包进去；同时检查安装包构建脚本有没有把数据目录带上。
- 在 [Description.md](Description.md) 和 README 的「功能一览」「界面介绍」「致谢」部分（中英文都要）补充死活题模式、题库来源和许可说明。

## 实施步骤

1. **题库准备**：编写 tasuki → SGF 的转换脚本，生成古典题集，并记录来源和许可。
2. **数据层**：`model` / `loader`（含变化树解析）/ `progress`，加单元测试。
3. **判题层**：答案树判题器，然后是死活题框架和 KataGo 判题器；用 `fake_gtp_engine.py` 模拟 `kata-analyze` 输出来测试。
4. **控制器**：`TSUMEGO` 模式、模式切换、引擎同步和状态流转。
5. **界面**：左侧菜单按钮、`TsumegoPanel`、`QStackedWidget`、棋盘裁剪、国际化。
6. **打包与文档**：package-data、安装包检查、Description/README 更新。
7. **验证**：
   - 单元测试：SGF 变化树解析、tasuki 转换、JSON 导入、答案树判题、进度读写、框架生成；
   - `test_ui.py`：三个模式按钮互斥、切换模式时分析会停止、死活题模式下相关按钮禁用；
   - `test_real_engine.py`（可选）：取几道古典题检查 KataGo 判题结果是否合理；
   - 手动验收：截图存到 `artifacts/acceptance-<日期>/`，与现有验收流程一致。

## 待决定的问题

1. 内置题库只放古典题（难度偏高），还是另找可以授权的入门题？
2. 没有答案的题完全交给 AI 判定，还是只提供「AI 提示」、不给对错结论？
3. 第一版要不要做棋盘局部裁剪显示，还是先显示整个棋盘？
