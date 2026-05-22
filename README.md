# 日报 & 周报自动化 Agent

两个基于 OpenAI Agents SDK 的专家 Agent，用于自动整理每日工作进展和生成周报。

- **日报素材整理专家** (`daily_progress_agent.py`)：扫描指定目录的近期改动文件，提取内容摘要，整理成规范的《每日进展》笔记
- **周报撰写专家** (`weekly_report_agent.py`)：读取本周日报，自动汇总成周报

## 安装

需要 Python 3.11+。

```bash
git clone <repo-url>
cd code
make install
```

## 配置

复制 `.env.example` 为 `.env`，填入必填项：

```bash
cp .env.example .env
```

**必填**：
- `DEEPSEEK_API_KEY`（或 `OPENAI_API_KEY`）
- `DAILY_PROGRESS_DIR`：日报输出目录，例如 `~/work/日志/每日进展`
- `WEEKLY_REPORT_DIR`：周报输出目录，例如 `~/work/日志/周报`
- `SCAN_DIR`：日报自动扫描的工作目录，例如 `~/work`

**可选**：
- `AGENT_MODEL`：模型名称，默认 `deepseek-chat`
- `REFERENCE_WEEKLY_REPORT`：用作风格参考的已有周报路径

## 使用

### 日报

自动扫描当天改动文件：

```bash
make run-daily
```

或手动传入素材：

```bash
.venv/bin/python daily_progress_agent.py \
  --date 2026-05-22 \
  --raw-notes "完成了 XXX 功能，遇到 YYY 问题"
```

自动扫描 + 手工补充一起用：

```bash
.venv/bin/python daily_progress_agent.py \
  --date 2026-05-22 \
  --auto-scan \
  --raw-notes "今天还开了一个同步会，决定先做 A 再做 B"
```

扫描最近 2 天（而不只是当天）：

```bash
.venv/bin/python daily_progress_agent.py \
  --date 2026-05-22 \
  --auto-scan \
  --scan-scope recent \
  --scan-days 2
```

### 周报

```bash
make run-weekly
```

自动检测最近 7 天窗口，读取该窗口内的所有日报生成周报。

## 日报输出格式

每篇日报包含：

- 今日 Todo / 明日 Todo
- 按项目分块的今日进展，每块包含：
  - **背景**：项目背景
  - **主要内容**：今日具体做了什么
  - **结论（置信度：高/中/低）**：结论或待人工补充

置信度由内容自动推断：有明确指标/数字为「高」，有判断但证据较弱为「中」，信息不足为「低」。

## 日报模板

`DAILY_PROGRESS_DIR` 下需有一个 `_模板.md` 文件作为日报结构参考。Agent 会在生成前读取该模板。如果目录不存在，首次运行会自动创建目录，但模板需手动放入。

## Provider 切换

`.env` 中修改 `AGENT_PROVIDER`：

```
# 使用 DeepSeek（默认）
AGENT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
AGENT_MODEL=deepseek-chat

# 使用 OpenAI
AGENT_PROVIDER=openai
OPENAI_API_KEY=sk-...
AGENT_MODEL=gpt-4o
```
