# Claude Code 监控页运行说明

这个仓库的四主题监控页，不是直接把 `last30days` 的原始输出公开出去，而是走一条固定流水线：

1. `scripts/daily-research.sh`
   - 负责按时段触发抓取
   - 当前固定主题：`Claude Code`、`Codex`、`大模型`、`Obsidian`
   - 当前默认深度：`deep`
   - 当前 cron：`10:00` 与 `20:00`，时区 `Asia/Shanghai`

2. `last30days`
   - 负责对每个主题跑多组 focused queries
   - 输出 compact/raw 结果到 `artifacts/raw-research/<slot>/<date>/`

3. `scripts/merge_compact_reports.py`
   - 合并同一主题的多组 query 结果

4. `scripts/synthesize_monitor_page.py`
   - 读取四个主题的 compact raw
   - 选候选条目
   - 合并相似事件
   - 调用 `scripts/monitor_link_enrichment.py` 对 `web / hn` 正文做 enrich
   - 优先使用 AI 对候选内容和网页正文生成中文总结
   - 再用 AI 生成每个主题的趋势总结
   - 只有在 AI 不可用或返回空结果时，才退回本地 heuristic
   - 生成最终 monitor 页面

5. `_research/<slot>/01-monitor.md`
   - 这是 GitHub Pages 实际展示的 monitor 页面

## 当前页面约定

- 页面上显示的是：
  - `候选条目`
  - `最终展示`
  - `本轮模型`
- “原始”不再用于页面统计文案
- 卡片里的字段名是 `总结`，不是 `重点结论`
- 总结要尽量具体，优先写清：
  - 发生了什么
  - 涉及哪些工具/模型/插件/厂商
  - 风险点或价值点在哪里
- 当前策略是：`AI-first, heuristic-fallback`

## 模型展示规则

- 不要硬编码模型名
- 要从 compact/raw 里的 `**OpenAI Model:** ...` 解析
- monitor 页顶部必须写出本轮实际模型
- 如果多个主题模型不同，就把多个模型都写出来

## Claude Code 手动运行流程

如果要完整跑一轮并推送：

1. 先看工作区状态
   - `git status --short`
   - 注意不要误覆盖用户自己的未提交改动

2. 运行抓取
   - `bash scripts/daily-research.sh`

3. 检查输出
   - 看 `_research/morning/01-monitor.md` 或 `_research/evening/01-monitor.md`
   - 验收不是只看 `HTTP 200`
   - 要确认：
     - 页面有内容
     - 摘要不是模板腔
     - 摘要明显是在理解内容，而不是只做关键词映射
     - 趋势卡要说明“这一轮主题往哪里发展”，而不是重复单条卡片
     - 没有明显跑题的 web 卡片
     - 页面上写了本轮模型

4. 提交
   - 只提交本轮确实需要的文件
   - 不要把无关本地改动一起提交

5. 推送
   - `git push origin main`

6. 线上验证
   - `https://zimengyuan.github.io/Report/research/morning/monitor/`
   - `https://zimengyuan.github.io/Report/research/evening/monitor/`
   - 必须检查最终 HTML，不要只看状态码

## 如果只想用现有 raw 重新生成页面

不重新抓取时，也可以直接用现有 raw 重生 monitor 页面。
适用于：
- 只改了展示逻辑
- 只改了摘要器
- 只改了页面文案

做法：
- 用 `scripts/synthesize_monitor_page.py` 读取 `artifacts/raw-research/...` 下的四个 raw 文件
- 重新写回 `_research/<slot>/01-monitor.md`
- 然后提交并 push

## 当前已知要求

- 主题固定为四个：
  - Claude Code
  - Codex
  - 大模型
  - Obsidian
- 页面要全部中文
- 总结尽量具体，不要泛泛地写“内容聚焦”“内容关注”
- 总结优先由 AI 基于正文和上下文理解后生成，不要把关键词规则当主流程
- 页面 UI 走卡片化
- 页面必须写明本轮实际模型
