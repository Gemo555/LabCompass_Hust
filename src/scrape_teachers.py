from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_lib
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "seed_pages.json"
RAW_HTML_DIR = ROOT / "data" / "raw_html"
RAW_TEXT_DIR = ROOT / "data" / "raw_text"
OUTPUT_DIR = ROOT / "output"

USER_AGENT = "LabCompass-HUST-EIC/0.1 public academic profile collection; no login"
NOT_FOUND = "未找到"
SUMMARY_CHAR_LIMIT = 120

COLUMNS = [
    "姓名",
    "职称",
    "导师类别",
    "所属专业/院系",
    "邮箱",
    "个人简介",
    "研究方向",
    "代表性项目或论文关键词",
    "团队介绍关键词",
    "可能适合本科生参与的任务类型",
    "来源URL",
    "原始HTML路径",
    "原始文本路径",
    "抓取状态",
    "备注",
]

SUMMARY_COLUMNS = [
    "姓名",
    "职称",
    "导师类别",
    "所属专业/院系",
    "邮箱",
    "研究方向摘要",
    "项目/论文关键词摘要",
    "本科生切入点",
    "抓取状态",
    "人工核验提示",
    "来源URL",
]


@dataclass
class ProfileLink:
    name_hint: str
    url: str
    department: str


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = dict(attrs)
        self._href = attrs_dict.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, clean_text(" ".join(self._text_parts))))
            self._href = None
            self._text_parts = []


class TextExtractor(HTMLParser):
    block_tags = {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def get_text(self) -> str:
        text = html_lib.unescape("".join(self.parts))
        lines = [clean_text(line) for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


def load_seed_pages() -> list[dict[str, str]]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_robot_parser(base_url: str) -> RobotFileParser:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        req = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=15) as resp:
            content = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        rp.parse(content.splitlines())
    except Exception:
        # Missing or unreachable robots.txt is treated as "no explicit rule";
        # the caller still rate-limits and only visits explicit public links.
        rp.parse("")
    return rp


def can_fetch(url: str, robots_cache: dict[str, RobotFileParser]) -> bool:
    parsed = urlparse(url)
    key = f"{parsed.scheme}://{parsed.netloc}"
    if key not in robots_cache:
        robots_cache[key] = get_robot_parser(url)
    return robots_cache[key].can_fetch(USER_AGENT, url)


def fetch(url: str, delay: float, robots_cache: dict[str, RobotFileParser]) -> str:
    if not can_fetch(url, robots_cache):
        raise RuntimeError(f"robots.txt disallows fetching {url}")
    time.sleep(delay)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=25) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value.strip("：: -\u3000")


def page_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.get_text()


def text_lines(text: str) -> list[str]:
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def discover_profile_links(html: str, seed_url: str, department: str) -> list[ProfileLink]:
    parser = LinkExtractor()
    parser.feed(html)
    links: list[ProfileLink] = []
    seen: set[str] = set()
    allow_patterns = ("professor/", "aprofessor/", "faculty.hust.edu.cn")

    for href, anchor_text in parser.links:
        href = href.strip()
        absolute = urljoin(seed_url, href)
        if not any(pattern in absolute for pattern in allow_patterns):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        name_hint = anchor_text or infer_name_from_url(absolute)
        links.append(ProfileLink(name_hint=name_hint, url=absolute, department=department))

    return links


def infer_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.strip("/").split("/")[0]
    return slug or ""


def first_match(patterns: Iterable[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return clean_text(match.group(1))
    return NOT_FOUND


def section_after(headings: Iterable[str], text: str, max_chars: int = 420) -> str:
    lines = text_lines(text)
    heading_re = re.compile("|".join(re.escape(h) for h in headings))
    stop_re = re.compile("个人信息|教育经历|工作经历|研究方向|科研项目|研究成果|招生信息|团队展示|团队介绍|论文|专利|获奖|联系方式|社会兼职")
    for i, line in enumerate(lines):
        if not heading_re.search(line):
            continue
        chunks: list[str] = []
        remainder = heading_re.sub("", line).strip("：: ")
        if remainder:
            chunks.append(remainder)
        for next_line in lines[i + 1 :]:
            if stop_re.search(next_line) and not heading_re.search(next_line):
                break
            chunks.append(next_line)
            if len(" ".join(chunks)) >= max_chars:
                break
        result = clean_text("；".join(chunks))[:max_chars]
        if result:
            return result
    return NOT_FOUND


def infer_name(text: str, name_hint: str) -> str:
    if name_hint and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name_hint):
        return name_hint
    title = first_match([r"姓名[：:\s]+([\u4e00-\u9fa5]{2,4})"], text)
    if title != NOT_FOUND:
        return title
    for line in text_lines(text)[:20]:
        match = re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line)
        if match:
            return match.group(0)
    return name_hint or NOT_FOUND


def extract_keywords(text: str, headings: Iterable[str]) -> str:
    section = section_after(headings, text, max_chars=650)
    if section == NOT_FOUND:
        return NOT_FOUND
    candidates = re.findall(
        r"[\u4e00-\u9fa5A-Za-z0-9]+(?:网络|通信|雷达|天线|感知|智能|算法|芯片|信号|图像|视频|系统|安全|定位|毫米波|集成|电路|论文|项目|基金)[\u4e00-\u9fa5A-Za-z0-9]*",
        section,
    )
    unique: list[str] = []
    for item in candidates:
        item = clean_text(item)
        if 2 <= len(item) <= 24 and item not in unique:
            unique.append(item)
        if len(unique) >= 10:
            break
    return "；".join(unique) if unique else section[:160]


def infer_undergrad_tasks(research: str, project_keywords: str, team_keywords: str) -> str:
    source = " ".join(v for v in [research, project_keywords, team_keywords] if v and v != NOT_FOUND)
    if not source:
        return NOT_FOUND
    tasks: list[str] = ["文献阅读与论文精读"]
    rules = [
        (r"算法|智能|机器学习|深度学习|图像|视频|感知|识别", "算法复现、数据处理、模型评测"),
        (r"通信|网络|无线|毫米波|信号|雷达|定位", "通信/信号处理仿真、实验数据分析"),
        (r"芯片|电路|硬件|FPGA|集成|天线", "硬件测试、仿真建模、实验平台辅助"),
        (r"安全|隐私|攻防|密码", "安全论文阅读、实验复现、工具链整理"),
        (r"系统|平台|软件|工程", "原型系统开发、脚本工具与工程实现"),
    ]
    for pattern, task in rules:
        if re.search(pattern, source, flags=re.I) and task not in tasks:
            tasks.append(task)
    return "推测：" + "；".join(tasks[:4])


def parse_profile(html: str, link: ProfileLink, html_path: Path, text_path: Path) -> dict[str, str]:
    text = page_text(html)
    text_path.write_text(text, encoding="utf-8")

    name = infer_name(text, link.name_hint)
    title = first_match(
        [
            r"职称[：:\s]+([^\n；。|,，]{2,30})",
            r"(教授|副教授|讲师|研究员|副研究员|高级工程师)",
        ],
        text,
    )
    mentor_type = first_match([r"导师类别[：:\s]+([^\n；。|,，]{2,40})", r"(博士生导师|硕士生导师|博导|硕导)"], text)
    affiliation = first_match([r"所属院系[：:\s]+([^\n；。|,，]{2,60})", r"所在单位[：:\s]+([^\n；。|,，]{2,60})"], text)
    if affiliation == NOT_FOUND and link.department:
        affiliation = link.department

    email = first_match([r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"], text)
    intro = section_after(["个人简介", "个人介绍", "基本信息"], text)
    research = section_after(["研究方向", "主要研究方向"], text)
    project_keywords = extract_keywords(text, ["科研项目", "研究成果", "论文", "代表性成果"])
    team_keywords = extract_keywords(text, ["团队展示", "团队介绍", "课题组"])
    undergrad_tasks = infer_undergrad_tasks(research, project_keywords, team_keywords)

    note = ""
    if len(text) < 300:
        note = "页面可读取文本较少，建议人工打开原始HTML或来源URL核验。"

    return {
        "姓名": name,
        "职称": title,
        "导师类别": mentor_type,
        "所属专业/院系": affiliation,
        "邮箱": email,
        "个人简介": intro,
        "研究方向": research,
        "代表性项目或论文关键词": project_keywords,
        "团队介绍关键词": team_keywords,
        "可能适合本科生参与的任务类型": undergrad_tasks,
        "来源URL": link.url,
        "原始HTML路径": str(html_path.relative_to(ROOT)),
        "原始文本路径": str(text_path.relative_to(ROOT)),
        "抓取状态": "成功",
        "备注": note,
    }


def safe_filename(url: str, name_hint: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    readable = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", name_hint or "teacher").strip("_")
    return f"{readable}_{digest}"


def compact_cell(value: str, limit: int = SUMMARY_CHAR_LIMIT) -> str:
    value = clean_text(value)
    if not value or value == NOT_FOUND:
        return NOT_FOUND
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip("，,；;。 ")
    return f"{cut}..."


def summary_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "姓名": row.get("姓名", ""),
        "职称": row.get("职称", ""),
        "导师类别": row.get("导师类别", ""),
        "所属专业/院系": row.get("所属专业/院系", ""),
        "邮箱": row.get("邮箱", ""),
        "研究方向摘要": compact_cell(row.get("研究方向", "")),
        "项目/论文关键词摘要": compact_cell(row.get("代表性项目或论文关键词", "")),
        "本科生切入点": compact_cell(row.get("可能适合本科生参与的任务类型", ""), 90),
        "抓取状态": row.get("抓取状态", ""),
        "人工核验提示": compact_cell(row.get("备注", ""), 90),
        "来源URL": row.get("来源URL", ""),
    }


def markdown_escape(value: str) -> str:
    return (value or "").replace("|", "\\|")


def write_readable_markdown(rows: list[dict[str, str]], path: Path) -> None:
    lines = [
        "# HUST EIC Teacher Research Samples",
        "",
        "本文件由 `src/scrape_teachers.py` 自动生成，面向 GitHub 预览阅读。字段为空或无法确认时保留为“未找到”，请以导师公开主页和人工核验为准。",
        "",
        "## 摘要表",
        "",
        "| 姓名 | 职称 | 导师类别 | 院系 | 研究方向摘要 | 本科生切入点 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in rows:
        summary = summary_row(row)
        lines.append(
            "| "
            + " | ".join(
                markdown_escape(summary[column])
                for column in ["姓名", "职称", "导师类别", "所属专业/院系", "研究方向摘要", "本科生切入点", "抓取状态"]
            )
            + " |"
        )

    lines.extend(["", "## 逐位导师卡片", ""])
    for row in rows:
        summary = summary_row(row)
        name = summary["姓名"] or NOT_FOUND
        lines.extend(
            [
                f"### {name}",
                "",
                f"- 职称：{summary['职称']}",
                f"- 导师类别：{summary['导师类别']}",
                f"- 所属专业/院系：{summary['所属专业/院系']}",
                f"- 邮箱：{summary['邮箱']}",
                f"- 研究方向摘要：{summary['研究方向摘要']}",
                f"- 项目/论文关键词摘要：{summary['项目/论文关键词摘要']}",
                f"- 本科生切入点：{summary['本科生切入点']}",
                f"- 抓取状态：{summary['抓取状态']}",
                f"- 人工核验提示：{summary['人工核验提示']}",
                f"- 来源URL：{summary['来源URL']}",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def save_outputs(rows: list[dict[str, str]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "teachers.csv"
    summary_csv_path = OUTPUT_DIR / "teachers_summary.csv"
    xlsx_path = OUTPUT_DIR / "teachers.xlsx"
    readable_md_path = OUTPUT_DIR / "teachers_readable.md"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with summary_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(summary_row(row) for row in rows)

    write_minimal_xlsx(rows, xlsx_path)
    write_readable_markdown(rows, readable_md_path)


def excel_col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xml_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_minimal_xlsx(rows: list[dict[str, str]], path: Path) -> None:
    sheet_rows = [COLUMNS] + [[row.get(column, "") for column in COLUMNS] for row in rows]
    row_xml: list[str] = []
    for row_idx, values in enumerate(sheet_rows, start=1):
        cells = []
        for col_idx, value in enumerate(values, start=1):
            cell_ref = f"{excel_col_name(col_idx)}{row_idx}"
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>')
        row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {''.join(row_xml)}
  </sheetData>
</worksheet>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="teachers" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", root_rels)
        xlsx.writestr("xl/workbook.xml", workbook)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        xlsx.writestr("xl/worksheets/sheet1.xml", worksheet)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Collect public HUST EIC teacher profile samples.")
    parser.add_argument("--limit", type=int, default=8, help="Maximum number of teacher profiles to fetch.")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay in seconds between requests.")
    args = parser.parse_args()

    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    RAW_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    robots_cache: dict[str, RobotFileParser] = {}
    profile_links: list[ProfileLink] = []

    for seed in load_seed_pages():
        try:
            html = fetch(seed["url"], args.delay, robots_cache)
            profile_links.extend(discover_profile_links(html, seed["url"], seed.get("department", "")))
        except Exception as exc:
            print(f"[WARN] Could not read seed page {seed['url']}: {exc}")

    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in profile_links:
        if len(rows) >= args.limit:
            break
        if link.url in seen_urls:
            continue
        seen_urls.add(link.url)

        stem = safe_filename(link.url, link.name_hint)
        html_path = RAW_HTML_DIR / f"{stem}.html"
        text_path = RAW_TEXT_DIR / f"{stem}.txt"

        try:
            html = fetch(link.url, args.delay, robots_cache)
            html_path.write_text(html, encoding="utf-8")
            rows.append(parse_profile(html, link, html_path, text_path))
            print(f"[OK] {rows[-1]['姓名']} {link.url}")
        except Exception as exc:
            rows.append(
                {
                    "姓名": link.name_hint or NOT_FOUND,
                    "职称": NOT_FOUND,
                    "导师类别": NOT_FOUND,
                    "所属专业/院系": link.department or NOT_FOUND,
                    "邮箱": NOT_FOUND,
                    "个人简介": NOT_FOUND,
                    "研究方向": NOT_FOUND,
                    "代表性项目或论文关键词": NOT_FOUND,
                    "团队介绍关键词": NOT_FOUND,
                    "可能适合本科生参与的任务类型": NOT_FOUND,
                    "来源URL": link.url,
                    "原始HTML路径": str(html_path.relative_to(ROOT)),
                    "原始文本路径": str(text_path.relative_to(ROOT)),
                    "抓取状态": "失败",
                    "备注": f"请求或解析失败：{exc}",
                }
            )
            print(f"[WARN] {link.url}: {exc}")

    save_outputs(rows)
    print(f"Saved {len(rows)} rows to {OUTPUT_DIR / 'teachers.csv'} and {OUTPUT_DIR / 'teachers.xlsx'}")


if __name__ == "__main__":
    main()
