#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openai import BadRequestError
from pydantic import BaseModel, Field

from agents import RunContextWrapper

from agent_provider import configure_model_provider, load_local_env


def _resolve_daily_dir() -> Path:
    env = os.environ.get("DAILY_PROGRESS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[5] / "work" / "日志" / "每日进展"


DAILY_PROGRESS_DIR = _resolve_daily_dir()
_template_env = os.environ.get("DAILY_TEMPLATE_PATH", "").strip()
DAILY_TEMPLATE_PATH = (
    Path(_template_env).expanduser().resolve()
    if _template_env
    else Path(__file__).parent / "templates" / "daily_progress_template.md"
)


@dataclass
class DailyProgressContext:
    target_date: date
    output_dir: Path
    template_path: Path


class DailyProgressDraft(BaseModel):
    note_title: str = Field(description="Daily progress note title in Chinese.")
    output_filename: str = Field(description="Markdown filename, for example 2026-05-21_工作进展.md.")
    note_markdown: str = Field(description="Full markdown content for the daily progress note.")


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".sql",
    ".csv",
    ".log",
    ".toml",
    ".ini",
    ".xlsx",
}
IGNORE_DIRS = {".git", ".obsidian", ".venv", "__pycache__", "node_modules", ".cache", ".trash"}
BINARY_SUMMARY_EXTENSIONS = {".xlsx"}
KEY_HINT_WORDS = ("背景", "目标", "方案", "实验", "评估", "结果", "结论", "风险", "问题", "建议")


def extract_json_object(text: str) -> str:
    fenced = re.search(r"```json\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output.")
    return text[start : end + 1]


def write_daily_progress(ctx: DailyProgressContext, draft: DailyProgressDraft) -> Path:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    normalized_filename = f"{ctx.target_date.isoformat()}_工作进展.md"
    normalized_title = f"{ctx.target_date.isoformat()} 工作进展"
    markdown = draft.note_markdown.strip()
    markdown = re.sub(
        r"(?m)^title:\s*.*$",
        f"title: {normalized_title}",
        markdown,
        count=1,
    )
    markdown = re.sub(
        r"(?m)^#\s+.*$",
        f"# {normalized_title}",
        markdown,
        count=1,
    )
    markdown = normalize_project_section_labels(markdown)
    output_path = ctx.output_dir / normalized_filename
    output_path.write_text(markdown + "\n", encoding="utf-8")
    return output_path


def normalize_project_section_labels(markdown: str) -> str:
    # Enforce stable field names in project blocks.
    markdown = markdown.replace("**进展**", "**主要内容**")
    markdown = markdown.replace("**待推进**", "**结论**")
    markdown = markdown.replace("**状态**", "**结论**")
    markdown = attach_confidence_to_conclusion(markdown)
    return markdown


def attach_confidence_to_conclusion(markdown: str) -> str:
    def level_for(content: str) -> str:
        content = content.strip()
        if "待人工补充" in content or content in {"", "待补充"}:
            return "低"
        if any(k in content for k in ("提升", "下降", "AUC", "增益", "pct", "%", "结论", "发现")):
            return "高"
        return "中"

    def repl_bold(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        level = level_for(content)
        return f"- **结论（置信度：{level}）**：{content}"

    def repl_plain(match: re.Match[str]) -> str:
        indent = match.group(1)
        content = match.group(2).strip()
        level = level_for(content)
        return f"{indent}- 结论（置信度：{level}）：{content}"

    markdown = re.sub(r"- \*\*结论\*\*：([^\n]*)", repl_bold, markdown)
    markdown = re.sub(r"(?m)^(\s*)-\s*结论：([^\n]*)$", repl_plain, markdown)
    return markdown


def collect_recent_work_materials(
    scan_dirs: list[Path],
    target_date: date,
    scope: str,
    days: int,
    limit: int,
    include_patterns: list[str] | None = None,
    max_bytes_per_file: int = 3000,
) -> str:
    cutoff_ts = datetime.now().timestamp() - days * 24 * 60 * 60
    include_patterns = include_patterns or []
    matches: list[Path] = []

    for root in scan_dirs:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            # Avoid feeding previous daily notes back into today's extraction.
            try:
                if path.resolve().is_relative_to(DAILY_PROGRESS_DIR.resolve()):
                    continue
            except Exception:
                pass
            if include_patterns and not any(fnmatch.fnmatch(path.name, p) for p in include_patterns):
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if scope == "today":
                    if mtime.date() != target_date:
                        continue
                elif path.stat().st_mtime < cutoff_ts:
                    continue
            except OSError:
                continue
            matches.append(path)

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    picked = matches[:limit]

    window_desc = (
        f"目标日期 {target_date.isoformat()} 当天"
        if scope == "today"
        else f"最近 {days} 天"
    )
    lines = [
        f"自动采样范围：{window_desc}，最多 {limit} 个文件",
        "变更判定：基于文件最近修改时间（不依赖 git）",
        f"扫描目录：{', '.join(str(p) for p in scan_dirs)}",
        "",
    ]
    if not picked:
        lines.append("未发现最近改动的文本文件。")
        return "\n".join(lines)

    for idx, path in enumerate(picked, start=1):
        stat = path.stat()
        rel = to_vault_rel(path)
        project = detect_project_from_path(rel)
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        lines.append(f"[{idx}] {rel} (project={project}, modified={mtime}, size={stat.st_size}B)")
        try:
            if path.suffix.lower() in BINARY_SUMMARY_EXTENSIONS:
                snippet = summarize_binary_file(path)
            else:
                snippet = summarize_text_file(path, max_bytes=max_bytes_per_file)
        except OSError as exc:
            snippet = f"<读取失败: {exc}>"
        if snippet:
            lines.append(snippet)
        lines.append("")
    return "\n".join(lines).strip()


def to_vault_rel(path: Path) -> Path:
    try:
        return path.relative_to(VAULT_ROOT)
    except ValueError:
        return path


def summarize_binary_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return summarize_xlsx(path)
    return "<二进制文件：仅记录文件元数据>"


def summarize_xlsx(path: Path) -> str:
    # Optional rich preview when openpyxl is available; fallback to metadata summary.
    try:
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet_names = wb.sheetnames[:8]
        content_lines: list[str] = []
        hint_lines: list[str] = []
        if wb.sheetnames:
            for sheet in wb.sheetnames[:3]:
                ws = wb[sheet]
                content_lines.append(f"- 工作表 `{sheet}`")
                for row in ws.iter_rows(min_row=1, max_row=8, values_only=True):
                    cells = ["" if cell is None else str(cell).strip() for cell in row]
                    if not any(cells):
                        continue
                    row_text = " | ".join(cells[:8]).strip()
                    if row_text:
                        content_lines.append(f"  {row_text}")
                    if any(k in row_text for k in KEY_HINT_WORDS):
                        hint_lines.append(row_text)
        preview = "\n".join(content_lines) if content_lines else "<前3个工作表前8行无可读内容>"
        hint_preview = "\n".join(f"- {line}" for line in hint_lines[:8]) if hint_lines else "- 未命中显式关键词（背景/结论/风险等）"
        return (
            f"<Excel文件摘要>\n"
            f"工作表: {', '.join(sheet_names) if sheet_names else '<无工作表>'}\n"
            f"内容预览:\n{preview}\n"
            f"可提炼线索:\n{hint_preview}"
        )
    except Exception:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = [n for n in zf.namelist() if n.startswith("xl/worksheets/")]
                modified, revision = read_xlsx_core_metadata(zf)
                meta = []
                if modified:
                    meta.append(f"内部修改时间: {modified}")
                if revision:
                    meta.append(f"内部修订号: {revision}")
                meta_line = ("\n" + "\n".join(meta)) if meta else ""
                return (
                    f"<Excel文件摘要>\n检测到工作表文件数: {len(names)}（未安装 openpyxl，未读取单元格内容）"
                    f"{meta_line}"
                )
        except Exception:
            return "<Excel文件：无法解析内容，仅记录文件元数据>"


def read_xlsx_core_metadata(zf: zipfile.ZipFile) -> tuple[str | None, str | None]:
    try:
        xml_bytes = zf.read("docProps/core.xml")
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None, None
    ns = {
        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "dcterms": "http://purl.org/dc/terms/",
    }
    modified = root.findtext("dcterms:modified", namespaces=ns)
    revision = root.findtext("cp:revision", namespaces=ns)
    return modified, revision


def detect_project_from_path(rel_path: Path) -> str:
    parts = rel_path.parts
    # Expected: work/项目/<project-name>/...
    if len(parts) >= 3 and parts[0] == "work" and parts[1] == "项目":
        return parts[2]
    if len(parts) >= 2 and parts[0] == "work":
        return parts[1]
    return "未分类"


def summarize_text_file(path: Path, max_bytes: int) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    head = text[:max_bytes]
    lines = [line.strip() for line in head.splitlines() if line.strip()]
    if not lines:
        return "<文本文件为空或无可读内容>"

    heading_lines = [ln for ln in lines if ln.startswith("#")][:8]
    hint_lines = [ln for ln in lines if any(k in ln for k in KEY_HINT_WORDS)][:10]
    fallback_lines = lines[:20]

    selected = hint_lines if hint_lines else fallback_lines
    selected = selected[:12]
    bullets = "\n".join(f"- {ln}" for ln in selected)

    sections: list[str] = ["<文本文件摘要>"]
    if heading_lines:
        sections.append("标题线索:\n" + "\n".join(f"- {ln}" for ln in heading_lines))
    sections.append("内容线索:\n" + bullets)
    return "\n".join(sections)


async def build_daily_progress_agent(
    target_date: date, typed_output: bool = True
) -> tuple[Any, DailyProgressContext]:
    load_local_env()
    model = configure_model_provider()

    from agents import Agent, function_tool

    daily_context = DailyProgressContext(
        target_date=target_date,
        output_dir=DAILY_PROGRESS_DIR,
        template_path=DAILY_TEMPLATE_PATH,
    )

    async def daily_progress_instructions(
        run_ctx: RunContextWrapper[DailyProgressContext], agent: Agent[DailyProgressContext]
    ) -> str:
        ctx = run_ctx.context
        output_contract = (
            "输出必须符合结构化 schema。"
            if typed_output
            else (
                "请只输出一个 JSON 对象，不要输出额外解释。"
                " JSON 字段必须包含 note_title、output_filename、note_markdown。"
            )
        )
        return (
            "你是日报素材整理专家。你的任务是把原始工作素材整理成规范的《每日进展》笔记。"
            f" 当前目标日期是 {ctx.target_date.isoformat()}。"
            " 必须贴合日报模板的结构，信息不全时保留空白或待办，不要编造结论。"
            " 重点整理：所属项目、今日 Todo、今日进展、关键产出、问题与风险、明日/下次 Todo。"
            " 项目归属必须以扫描条目里的 project 字段为准，禁止根据正文语义跨项目合并归类。"
            " 当同一条素材涉及多个项目时，拆成多条；不要把 A 项目的结论写进 B 项目标题下。"
            " 在“今日进展”中，每个小节标题必须严格使用原始 project 名称，不允许附加括号解释或映射关系。"
            " 每个项目必须且只需三行核心信息：`背景`、`主要内容`、`结论`。"
            " 若结论信息不足，必须写“结论：待人工补充”，不能用“待推进/状态”等替代结论字段。"
            " 禁止只写“更新了某文件”，必须结合摘要内容写出具体做了什么、结果是什么。"
            " 结论尽量引用具体数字、指标或明确判断，便于后续自动标记置信度。"
            f" {output_contract}"
        )

    @function_tool
    def read_daily_progress_template(run_ctx: RunContextWrapper[DailyProgressContext]) -> str:
        """Read the daily progress note template."""
        return run_ctx.context.template_path.read_text(encoding="utf-8")

    agent = Agent[DailyProgressContext](
        name="日报素材整理专家",
        handoff_description="Format raw work notes into one daily progress entry.",
        instructions=daily_progress_instructions,
        model=model,
        tools=[read_daily_progress_template],
        output_type=DailyProgressDraft if typed_output else None,
    )
    return agent, daily_context


async def run_daily_progress_agent(raw_notes: str, target_date: date) -> Path:
    from agents import Runner

    prompt = (
        "请把下面的原始工作素材整理成一篇《每日进展》笔记。\n\n"
        f"原始素材：\n{raw_notes}\n"
    )

    try:
        agent, daily_context = await build_daily_progress_agent(target_date=target_date, typed_output=True)
        result = await Runner.run(
            agent,
            prompt,
            context=daily_context,
            max_turns=8,
        )
        draft = result.final_output
        if not isinstance(draft, DailyProgressDraft):
            raise RuntimeError(f"Unexpected output type: {type(draft)!r}")
    except BadRequestError as exc:
        if "response_format" not in str(exc):
            raise
        agent, daily_context = await build_daily_progress_agent(target_date=target_date, typed_output=False)
        result = await Runner.run(
            agent,
            prompt,
            context=daily_context,
            max_turns=8,
        )
        raw_output = result.final_output
        if not isinstance(raw_output, str):
            raw_output = str(raw_output)
        draft = DailyProgressDraft.model_validate_json(extract_json_object(raw_output))

    return write_daily_progress(daily_context, draft)


def main() -> None:
    parser = argparse.ArgumentParser(description="Format raw notes into a daily progress note.")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Target note date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--raw-notes",
        default="",
        help="Raw work notes to format into a daily progress entry.",
    )
    parser.add_argument(
        "--auto-scan",
        action="store_true",
        help="Auto-collect recent modified files and convert them into raw notes.",
    )
    parser.add_argument(
        "--scan-dir",
        action="append",
        default=[],
        help="Directory to scan for recent files. Can be repeated. Default: VULT/work",
    )
    parser.add_argument(
        "--scan-days",
        type=int,
        default=2,
        help="Recent days window for auto scan when --scan-scope=recent. Default: 2",
    )
    parser.add_argument(
        "--scan-scope",
        choices=["today", "recent"],
        default="today",
        help="Auto scan time scope. 'today' scans only --date day; 'recent' scans --scan-days window.",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=20,
        help="Max files to include from auto scan. Default: 20",
    )
    parser.add_argument(
        "--scan-include",
        action="append",
        default=[],
        help="Optional filename glob, e.g. '*.md'. Can be repeated.",
    )
    args = parser.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    raw_notes = args.raw_notes.strip()
    if args.auto_scan:
        default_scan = os.environ.get("SCAN_DIR", "").strip()
        scan_dirs = (
            [Path(p).expanduser().resolve() for p in args.scan_dir]
            if args.scan_dir
            else [Path(default_scan).expanduser().resolve()]
            if default_scan
            else [DAILY_PROGRESS_DIR.parents[2]]
        )
        auto_notes = collect_recent_work_materials(
            scan_dirs=scan_dirs,
            target_date=target_date,
            scope=args.scan_scope,
            days=max(1, args.scan_days),
            limit=max(1, args.scan_limit),
            include_patterns=args.scan_include or None,
        )
        raw_notes = f"{raw_notes}\n\n{auto_notes}".strip()
    if not raw_notes:
        raise SystemExit("No input notes. Provide --raw-notes or enable --auto-scan.")

    output_path = asyncio.run(run_daily_progress_agent(raw_notes, target_date))
    print(output_path)


if __name__ == "__main__":
    main()
