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
NEEDS_CHECK = "待人工核验"
SUMMARY_CHAR_LIMIT = 120
DEFAULT_DELAY_SECONDS = 1.5
DEFAULT_MAX_LIST_PAGES = 30

TEACHER_URL_PATTERNS = ("professor/", "aprofessor/", "faculty.hust.edu.cn")
LIST_PAGE_MARKERS = ("/xygk/szdw/",)
LIST_PAGE_EXTENSIONS = (".htm", ".html")

AI_RELATED_KEYWORDS = [
    "计算机视觉",
    "图像处理",
    "视频处理",
    "模式识别",
    "机器学习",
    "深度学习",
    "人工智能",
    "智能感知",
    "多媒体信息处理",
    "遥感图像处理",
    "医学图像",
    "目标检测",
    "图像识别",
    "图像分割",
    "三维视觉",
    "点云",
    "图像压缩",
    "多模态",
    "大模型",
    "视觉语言模型",
    "智能信息处理",
    "detection",
    "segmentation",
    "recognition",
    "image",
    "video",
    "visual",
    "vision",
    "multimodal",
    "deep learning",
    "machine learning",
    "智能系统",
    "智能计算",
    "数据挖掘",
    "知识图谱",
    "强化学习",
    "自然语言处理",
    "智能优化",
]

DIRECTION_RULES = [
    ("CV/图像处理", r"计算机视觉|图像|视频|模式识别|目标检测|图像识别|图像分割|三维视觉|点云|多媒体|医学图像|遥感图像|image|video|visual|vision|detection|segmentation|recognition"),
    ("AI/机器学习", r"机器学习|深度学习|人工智能|智能感知|智能信息处理|多模态|大模型|视觉语言模型|神经网络|deep learning|machine learning|AI|multimodal|large model"),
    ("通信/信号处理", r"通信|无线|移动通信|信号处理|信号检测|软件无线电|信道|频谱|编码|调制"),
    ("电磁/微波", r"电磁|微波|毫米波|天线|射频|电波|电路与系统"),
    ("遥感/雷达", r"遥感|雷达|SAR|合成孔径|探测|成像|散射|海洋"),
    ("光电/激光", r"光电|激光|红外|光谱|光学|激光雷达|LiDAR"),
    ("硬件/嵌入式", r"芯片|电路|FPGA|嵌入式|硬件|集成电路|传感器|板卡"),
    ("网络/物联网", r"网络|物联网|自组网|边缘计算|互联网|协议|路由"),
    ("信息安全", r"安全|隐私|密码|攻防|加密|可信|漏洞"),
]

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
    "direction_tags",
    "relevance_score",
    "ai_cv_keywords",
    "research_plain_explanation",
    "recommendation_priority",
    "confidence",
    "undergrad_openness",
    "publication_potential",
    "interest_match",
    "fit_summary",
    "来源URL",
    "发现来源URL",
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
    "方向标签",
    "AI方向原始线索分",
    "AI关键词",
    "推荐优先级",
    "置信度",
    "本科生接纳程度",
    "论文产出潜力",
    "个人方向匹配",
    "抓取状态",
    "人工核验提示",
    "来源URL",
]


@dataclass
class ProfileLink:
    name_hint: str
    url: str
    department: str
    title_hint: str = ""
    source_url: str = ""


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []
        self._attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key: value or "" for key, value in attrs}
        self._href = attrs_dict.get("href")
        self._text_parts = []
        self._attrs = attrs_dict

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append(
                {
                    "href": self._href,
                    "text": clean_text(" ".join(self._text_parts)),
                    "title": clean_text(self._attrs.get("title", "")),
                    "textvalue": clean_text(self._attrs.get("textvalue", "")),
                }
            )
            self._href = None
            self._text_parts = []
            self._attrs = {}


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
        raw = resp.read()
        charset = resp.headers.get_content_charset() or detect_charset(raw) or "utf-8"
        return raw.decode(charset, errors="replace")


def detect_charset(raw: bytes) -> str | None:
    head = raw[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset=[\"']?([A-Za-z0-9_-]+)", head, flags=re.I)
    if match:
        return match.group(1)
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value.strip("：: -\u3000")


def page_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.get_text()


def text_lines(text: str) -> list[str]:
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def is_teacher_url(url: str) -> bool:
    return any(pattern in url for pattern in TEACHER_URL_PATTERNS)


def is_staff_list_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.netloc.endswith("eic.hust.edu.cn")
        and any(marker in parsed.path for marker in LIST_PAGE_MARKERS)
        and parsed.path.endswith(LIST_PAGE_EXTENSIONS)
    )


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def infer_department_from_url_or_text(url: str, text: str = "") -> str:
    mapping = {
        "dzgcx": "电子工程系",
        "txgcx": "通信工程系",
        "xxgcx": "信息工程系",
        "jxsyzx": "教学实验中心",
    }
    for marker, department in mapping.items():
        if marker in url:
            return department
    for department in mapping.values():
        if department in text:
            return department
    return ""


def discover_staff_list_links(html: str, seed_url: str) -> list[str]:
    parser = LinkExtractor()
    parser.feed(html)
    discovered: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        href = link.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = normalize_url(urljoin(seed_url, href))
        if absolute in seen or not is_staff_list_url(absolute):
            continue
        seen.add(absolute)
        discovered.append(absolute)
    return discovered


def discover_profile_links(html: str, seed_url: str, department: str) -> list[ProfileLink]:
    parser = LinkExtractor()
    parser.feed(html)
    links: list[ProfileLink] = []
    seen: set[str] = set()

    for link in parser.links:
        href = link.get("href", "").strip()
        absolute = normalize_url(urljoin(seed_url, href))
        if not is_teacher_url(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        name_hint = link.get("textvalue") or link.get("text") or infer_name_from_url(absolute)
        title_hint = link.get("title", "")
        links.append(
            ProfileLink(
                name_hint=name_hint,
                url=absolute,
                department=department,
                title_hint=title_hint,
                source_url=seed_url,
            )
        )

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


def collect_matching_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    found: list[str] = []
    lower_text = text.lower()
    for keyword in keywords:
        if keyword.lower() in lower_text and keyword not in found:
            found.append(keyword)
    return found


def collect_direction_tags(*parts: str) -> list[str]:
    source = "\n".join(part for part in parts if part and part != NOT_FOUND)
    tags: list[str] = []
    for tag, pattern in DIRECTION_RULES:
        if re.search(pattern, source, flags=re.I):
            tags.append(tag)
    if not tags:
        tags.append("其他/待核验")
    return tags


def normalize_title(title: str, title_hint: str) -> str:
    valid_pattern = r"教授|副教授|讲师|研究员|副研究员|高级工程师|工程师|实验师"
    if title and title != NOT_FOUND and re.search(valid_pattern, title):
        return title
    if title_hint and re.search(valid_pattern, title_hint):
        return title_hint.replace("教授研究员", "教授/研究员")
    return title if title and title != NOT_FOUND else NOT_FOUND


def score_ai_relevance(research: str, project_keywords: str, intro: str, full_text: str) -> tuple[int, list[str]]:
    research_text = "\n".join(value for value in [research, project_keywords, intro] if value and value != NOT_FOUND)
    all_text = "\n".join(value for value in [research_text, full_text] if value)
    matched = collect_matching_keywords(all_text, AI_RELATED_KEYWORDS)
    score = 0

    strong_keywords = [
        "计算机视觉",
        "图像处理",
        "视频处理",
        "模式识别",
        "机器学习",
        "深度学习",
        "人工智能",
        "目标检测",
        "图像识别",
        "图像分割",
        "三维视觉",
        "点云",
        "多模态",
        "视觉语言模型",
        "detection",
        "segmentation",
        "recognition",
        "image",
        "video",
        "visual",
        "deep learning",
        "machine learning",
    ]
    medium_keywords = ["智能", "感知", "数据分析", "信号处理", "遥感", "成像", "信息处理", "算法"]

    research_lower = research_text.lower()
    for keyword in strong_keywords:
        count = research_lower.count(keyword.lower())
        if count:
            score += 22 + min(count - 1, 3) * 6
    for keyword in medium_keywords:
        if keyword.lower() in research_lower:
            score += 8

    full_lower = full_text.lower()
    paper_like_hits = 0
    for keyword in ["detection", "segmentation", "recognition", "image", "video", "visual", "multimodal", "deep learning"]:
        paper_like_hits += min(full_lower.count(keyword), 5)
    score += min(paper_like_hits * 4, 28)

    low_only_patterns = r"通信|电磁|微波|天线|射频|硬件|电路"
    if score < 20 and re.search(low_only_patterns, research_text):
        score = max(score, 8)

    return min(score, 100), matched


def qualitative_from_score(score: int, high_at: int, medium_at: int) -> str:
    if score >= high_at:
        return "high"
    if score >= medium_at:
        return "medium"
    return "low"


def assess_undergrad_openness(row: dict[str, str], full_text: str) -> str:
    text = "\n".join(
        value
        for value in [
            row.get("个人简介", ""),
            row.get("研究方向", ""),
            row.get("团队介绍关键词", ""),
            row.get("代表性项目或论文关键词", ""),
            full_text,
        ]
        if value and value != NOT_FOUND
    )
    if re.search(r"本科生|大创|创新创业|实习|招收.*本科|欢迎.*本科|竞赛|开放课题", text):
        return "high"
    if re.search(r"团队|课题组|招生|硕士|博士|项目|实验平台|软件|系统|数据|算法", text):
        return "medium"
    return "low"


def assess_publication_potential(row: dict[str, str], full_text: str) -> str:
    text = "\n".join(
        value
        for value in [
            row.get("个人简介", ""),
            row.get("研究方向", ""),
            row.get("代表性项目或论文关键词", ""),
            full_text,
        ]
        if value and value != NOT_FOUND
    )
    score = 0
    score += len(re.findall(r"论文|SCI|SSCI|EI|IEEE|ACM|期刊|会议|Pattern Recognition|CVPR|ICCV|ECCV|NeurIPS|AAAI|ICASSP", text, flags=re.I)) * 2
    score += len(re.findall(r"国家自然科学基金|重点研发|863|973|项目负责人|主持|课题|基金|横向项目", text)) * 2
    score += len(re.findall(r"detection|segmentation|recognition|image|video|learning|visual", text, flags=re.I))
    return qualitative_from_score(score, high_at=16, medium_at=6)


def assess_interest_match(row: dict[str, str]) -> str:
    score = int(row.get("relevance_score", 0) or 0)
    tags = row.get("direction_tags", "")
    if score >= 70 or ("CV/图像处理" in tags and "AI/机器学习" in tags):
        return "high"
    if score >= 30 or "AI/机器学习" in tags or "CV/图像处理" in tags:
        return "medium"
    return "low"


def build_fit_summary(row: dict[str, str]) -> str:
    parts = []
    if row.get("undergrad_openness") == "high":
        parts.append("公开文本中出现本科生/竞赛/实习等友好信号")
    elif row.get("undergrad_openness") == "medium":
        parts.append("有团队/项目/招生线索，但本科生接纳需核验")
    else:
        parts.append("本科生接纳信息不足")

    if row.get("publication_potential") == "high":
        parts.append("论文或项目产出线索较多")
    elif row.get("publication_potential") == "medium":
        parts.append("有一定论文/项目线索")
    else:
        parts.append("公开页面产出线索较少")

    if row.get("interest_match") == "high":
        parts.append("与 AI/智能方向明显相关")
    elif row.get("interest_match") == "medium":
        parts.append("与 AI/智能方向可能相关")
    else:
        parts.append("与 AI/智能方向相关性较弱或待核验")
    return "；".join(parts)


def confidence_for_profile(row: dict[str, str], text_length: int) -> str:
    score = int(row.get("relevance_score", 0) or 0)
    if row.get("抓取状态") != "成功" or text_length < 300:
        return "low"
    if score >= 65 and row.get("ai_cv_keywords"):
        return "high"
    if score >= 30 or row.get("direction_tags") != "其他/待核验":
        return "medium"
    return "low"


def priority_for_profile(row: dict[str, str]) -> str:
    levels = [row.get("undergrad_openness"), row.get("publication_potential"), row.get("interest_match")]
    high_count = levels.count("high")
    medium_or_high = sum(level in {"high", "medium"} for level in levels)
    if high_count >= 2 and row.get("confidence") in {"high", "medium"}:
        return "A"
    if medium_or_high >= 2:
        return "B"
    return "C"


def explain_research(row: dict[str, str]) -> str:
    tags = row.get("direction_tags", "")
    research = row.get("研究方向", "")
    if "CV/图像处理" in tags and "AI/机器学习" in tags:
        return "偏视觉/图像与智能算法交叉，可重点看是否有检测、识别、分割、多模态或深度学习课题。"
    if "CV/图像处理" in tags:
        return "偏图像、视频、视觉感知或成像处理方向，适合从论文复现和数据处理切入。"
    if "AI/机器学习" in tags:
        return "偏智能算法或机器学习应用方向，需要人工核验是否是真正的 AI/CV 课题。"
    if "遥感/雷达" in tags and re.search(r"图像|成像|识别|检测|深度学习", research):
        return "偏遥感/雷达成像与智能处理，可能存在 CV 方法和信号处理结合点。"
    if "通信/信号处理" in tags:
        return "主要是通信或信号处理，若只出现泛化“智能处理”，AI/CV 相关性需人工核验。"
    return "当前公开文本不足以判断，建议打开主页和原始文本人工核验。"


def infer_undergrad_tasks(research: str, project_keywords: str, team_keywords: str) -> str:
    source = " ".join(v for v in [research, project_keywords, team_keywords] if v and v != NOT_FOUND)
    if not source:
        return NOT_FOUND
    tasks: list[str] = []
    rules = [
        (r"目标检测|图像检测|视觉检测|图像识别|模式识别|图像分割|计算机视觉|图像|视频", "围绕检测/识别/分割论文做复现，对公开图像或视频数据集做标注、训练和误差分析"),
        (r"医学图像|医疗|影像", "整理医学影像数据处理流程，复现分割/诊断模型并做可视化评估"),
        (r"遥感|雷达|SAR|成像|海洋", "处理遥感/雷达成像数据，复现实验中的滤波、检测或目标识别模块"),
        (r"多模态|大模型|视觉语言|自然语言", "整理多模态数据和提示词实验，复现视觉语言模型基线并做案例分析"),
        (r"机器学习|深度学习|人工智能|智能|算法", "复现机器学习/深度学习基线，做消融实验、参数对比和结果可视化"),
        (r"通信|网络|无线|频谱|信号|信号检测", "搭建通信或信号处理仿真脚本，分析频谱/信道/传输实验数据"),
        (r"芯片|电路|硬件|FPGA|嵌入式|天线", "做硬件平台资料整理、测试脚本、FPGA/嵌入式小模块或实验记录自动化"),
        (r"安全|隐私|攻防|密码", "复现安全论文实验，整理数据集、攻击/防御流程和评测指标"),
        (r"系统|平台|软件|工程", "开发小型实验工具、数据处理脚本、Web 可视化或原型系统模块"),
    ]
    for pattern, task in rules:
        if re.search(pattern, source, flags=re.I) and task not in tasks:
            tasks.append(task)
    if not tasks:
        tasks.append("先做 3-5 篇代表性论文精读，整理术语表、数据集和可复现实验清单")
    return "基于公开方向推测：" + "；".join(tasks[:3])


def collect_external_evidence(row: dict[str, str]) -> list[dict[str, str]]:
    """Reserved extension point for lab/news/paper-index evidence.

    Future sources can include college news, lab pages, Semantic Scholar, IEEE,
    DBLP, or Google Scholar. Keep this empty by default to avoid extra external
    crawling in the first full-coverage pass.
    """
    return []


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
    title = normalize_title(title, link.title_hint)
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
    direction_tags = collect_direction_tags(research, project_keywords, team_keywords, intro)
    relevance_score, ai_cv_keywords = score_ai_relevance(research, project_keywords, intro, text)

    note = ""
    if len(text) < 300:
        note = "页面可读取文本较少，建议人工打开原始HTML或来源URL核验。"
    row = {
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
        "direction_tags": "；".join(direction_tags),
        "relevance_score": str(relevance_score),
        "ai_cv_keywords": "；".join(ai_cv_keywords),
        "research_plain_explanation": "",
        "recommendation_priority": "",
        "confidence": "",
        "undergrad_openness": "",
        "publication_potential": "",
        "interest_match": "",
        "fit_summary": "",
        "来源URL": link.url,
        "发现来源URL": link.source_url,
        "原始HTML路径": str(html_path.relative_to(ROOT)),
        "原始文本路径": str(text_path.relative_to(ROOT)),
        "抓取状态": "成功",
        "备注": note,
    }
    row["confidence"] = confidence_for_profile(row, len(text))
    row["undergrad_openness"] = assess_undergrad_openness(row, text)
    row["publication_potential"] = assess_publication_potential(row, text)
    row["interest_match"] = assess_interest_match(row)
    row["recommendation_priority"] = priority_for_profile(row)
    row["fit_summary"] = build_fit_summary(row)
    row["research_plain_explanation"] = explain_research(row)
    row["external_evidence"] = collect_external_evidence(row)
    return row


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
        "方向标签": row.get("direction_tags", ""),
        "AI方向原始线索分": row.get("relevance_score", ""),
        "AI关键词": compact_cell(row.get("ai_cv_keywords", ""), 90),
        "推荐优先级": row.get("recommendation_priority", ""),
        "置信度": row.get("confidence", ""),
        "本科生接纳程度": row.get("undergrad_openness", ""),
        "论文产出潜力": row.get("publication_potential", ""),
        "个人方向匹配": row.get("interest_match", ""),
        "抓取状态": row.get("抓取状态", ""),
        "人工核验提示": compact_cell(row.get("备注", ""), 90),
        "来源URL": row.get("来源URL", ""),
    }


def markdown_escape(value: str) -> str:
    return (value or "").replace("|", "\\|")


def display_value(value: str, fallback: str = NEEDS_CHECK) -> str:
    value = clean_text(str(value or ""))
    if not value or value == NOT_FOUND:
        return fallback
    return value


def sorted_ai_rows(rows: list[dict[str, str]], min_score: int = 25) -> list[dict[str, str]]:
    candidates = []
    for row in rows:
        try:
            score = int(row.get("relevance_score", 0) or 0)
        except ValueError:
            score = 0
        keywords = row.get("ai_cv_keywords", "")
        if score >= min_score or row.get("interest_match") in {"high", "medium"} or (keywords and score >= 20):
            candidates.append(row)
    priority_rank = {"A": 3, "B": 2, "C": 1}
    level_rank = {"high": 3, "medium": 2, "low": 1}
    return sorted(
        candidates,
        key=lambda item: (
            priority_rank.get(item.get("recommendation_priority", "C"), 0),
            level_rank.get(item.get("interest_match", "low"), 0),
            level_rank.get(item.get("publication_potential", "low"), 0),
            level_rank.get(item.get("undergrad_openness", "low"), 0),
            int(item.get("relevance_score", 0) or 0),
        ),
        reverse=True,
    )


def sorted_ai_cv_rows(rows: list[dict[str, str]], min_score: int = 25) -> list[dict[str, str]]:
    return sorted_ai_rows(rows, min_score)


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


def write_all_profiles_markdown(rows: list[dict[str, str]], path: Path) -> None:
    lines = [
        "# 华中科技大学电子信息与通信学院教师粗画像",
        "",
        "本文件由公开网页自动整理。方向标签和本科生切入点是基于公开文本的保守推断，不代表导师本人招生要求。",
        "",
    ]
    for row in sorted(rows, key=lambda item: (item.get("所属专业/院系", ""), item.get("姓名", ""))):
        score = display_value(row.get("relevance_score", "0"), "0")
        lines.extend(
            [
                f"## {display_value(row.get('姓名'))}",
                "",
                f"- 职称：{display_value(row.get('职称'))}",
                f"- 院系/团队：{display_value(row.get('所属专业/院系'))}",
                f"- 导师类别：{display_value(row.get('导师类别'))}",
                f"- 方向标签：{display_value(row.get('direction_tags'))}",
                f"- AI/智能方向匹配：{display_value(row.get('interest_match'))}",
                f"- 本科生接纳程度：{display_value(row.get('undergrad_openness'))}",
                f"- 论文产出潜力：{display_value(row.get('publication_potential'))}",
                f"- AI/智能关键词：{display_value(row.get('ai_cv_keywords'))}",
                f"- 研究方向摘要：{display_value(compact_cell(row.get('研究方向', ''), 220))}",
                f"- 本科生切入点：{display_value(row.get('可能适合本科生参与的任务类型'))}",
                f"- 置信度：{display_value(row.get('confidence'))}",
                f"- 人工核验：{display_value(row.get('备注'))}",
                f"- 主页：{display_value(row.get('来源URL'))}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ai_markdown(rows: list[dict[str, str]], path: Path) -> None:
    candidates = sorted_ai_rows(rows)
    lines = [
        "# 人工智能相关老师重点清单",
        "",
        "排序依据：本科生接纳程度、论文/项目产出潜力、与 AI/智能方向的匹配程度。请把本清单当作初筛，不要当作最终结论。",
        "",
        "说明：`interest_match` 目前只代表与“AI/智能相关方向”的通用匹配。等你提供个人期待方向后，可以进一步细化为你的个人匹配度。",
        "",
    ]
    if not candidates:
        lines.append("本轮公开主页中没有筛出明显 AI 候选，建议扩展学院新闻、实验室主页和论文库后复查。")
    for row in candidates:
        lines.extend(
            [
                f"## {display_value(row.get('姓名'))} - 优先级 {display_value(row.get('recommendation_priority'), 'C')}",
                "",
                f"- 姓名：{display_value(row.get('姓名'))}",
                f"- 职称：{display_value(row.get('职称'))}",
                f"- 院系/团队：{display_value(row.get('所属专业/院系'))}",
                f"- 主页 URL：{display_value(row.get('来源URL'))}",
                f"- 本科生接纳程度：{display_value(row.get('undergrad_openness'))}",
                f"- 论文/项目产出潜力：{display_value(row.get('publication_potential'))}",
                f"- AI/智能方向匹配：{display_value(row.get('interest_match'))}",
                f"- 综合判断：{display_value(row.get('fit_summary'))}",
                f"- AI/智能相关关键词：{display_value(row.get('ai_cv_keywords'))}",
                f"- 研究方向人话解释：{display_value(row.get('research_plain_explanation'))}",
                f"- 最近论文/项目关键词：{display_value(row.get('代表性项目或论文关键词'))}",
                f"- 本科生可切入方向：{display_value(row.get('可能适合本科生参与的任务类型'))}",
                f"- 推荐优先级：{display_value(row.get('recommendation_priority'), 'C')}",
                f"- 置信度：{display_value(row.get('confidence'), 'low')}",
                f"- 需要人工核验：{display_value(row.get('备注'), '无特别提示')}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ai_cv_markdown(rows: list[dict[str, str]], path: Path) -> None:
    write_ai_markdown(rows, path)


def write_scrape_report(rows: list[dict[str, str]], discovered_count: int, list_pages: list[str], path: Path) -> None:
    success = sum(1 for row in rows if row.get("抓取状态") == "成功")
    failed = len(rows) - success
    low_confidence = sum(1 for row in rows if row.get("confidence") == "low")
    ai_cv_count = len(sorted_ai_rows(rows))
    tag_counts: dict[str, int] = {}
    for row in rows:
        for tag in (row.get("direction_tags") or "其他/待核验").split("；"):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    lines = [
        "# 抓取统计报告",
        "",
        f"- 发现教师主页链接数：{discovered_count}",
        f"- 输出教师记录数：{len(rows)}",
        f"- 成功抓取人数：{success}",
        f"- 失败人数：{failed}",
        f"- 低置信度人数：{low_confidence}",
        f"- 疑似 AI/智能方向老师人数：{ai_cv_count}",
        "",
        "## 已访问的师资列表页",
        "",
    ]
    for url in list_pages:
        lines.append(f"- {url}")
    lines.extend(["", "## 方向标签统计", ""])
    for tag, count in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- {tag}：{count}")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 本轮只基于公开网页和脚本可读取文本，不登录任何网站。",
            "- `collect_external_evidence()` 已预留学院新闻、实验室主页、Semantic Scholar、IEEE、DBLP 等外部证据接口，但默认不抓取外部论文库。",
            "- 页面超时、文本过短、字段缺失的记录均应人工核验。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def save_outputs(rows: list[dict[str, str]], discovered_count: int, list_pages: list[str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "teachers.csv"
    summary_csv_path = OUTPUT_DIR / "teachers_summary.csv"
    xlsx_path = OUTPUT_DIR / "teachers.xlsx"
    readable_md_path = OUTPUT_DIR / "teachers_readable.md"
    raw_json_path = OUTPUT_DIR / "all_teachers_raw.json"
    all_profiles_path = OUTPUT_DIR / "all_teachers_profiles.md"
    ai_path = OUTPUT_DIR / "ai_teachers.md"
    ai_cv_path = OUTPUT_DIR / "ai_cv_teachers.md"
    report_path = OUTPUT_DIR / "scrape_report.md"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    with summary_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(summary_row(row) for row in rows)

    write_minimal_xlsx(rows, xlsx_path)
    write_readable_markdown(rows, readable_md_path)
    raw_json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_all_profiles_markdown(rows, all_profiles_path)
    write_ai_markdown(rows, ai_path)
    write_ai_cv_markdown(rows, ai_cv_path)
    write_scrape_report(rows, discovered_count, list_pages, report_path)


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


def discover_all_profile_links(
    seed_pages: list[dict[str, str]],
    delay: float,
    robots_cache: dict[str, RobotFileParser],
    max_list_pages: int,
) -> tuple[list[ProfileLink], list[str]]:
    pending: list[tuple[str, str]] = [(seed["url"], seed.get("department", "")) for seed in seed_pages]
    visited: set[str] = set()
    list_pages: list[str] = []
    profiles: dict[str, ProfileLink] = {}

    while pending and len(visited) < max_list_pages:
        list_url, department_hint = pending.pop(0)
        list_url = normalize_url(list_url)
        if list_url in visited:
            continue
        visited.add(list_url)
        try:
            html = fetch(list_url, delay, robots_cache)
        except Exception as exc:
            print(f"[WARN] Could not read staff list {list_url}: {exc}")
            continue

        list_pages.append(list_url)
        text = page_text(html)
        department = department_hint or infer_department_from_url_or_text(list_url, text)

        for next_url in discover_staff_list_links(html, list_url):
            if next_url not in visited and all(next_url != item[0] for item in pending):
                next_department = infer_department_from_url_or_text(next_url)
                pending.append((next_url, next_department))

        for profile in discover_profile_links(html, list_url, department):
            if profile.url not in profiles:
                profiles[profile.url] = profile
            else:
                existing = profiles[profile.url]
                if not existing.department and profile.department:
                    existing.department = profile.department
                if not existing.title_hint and profile.title_hint:
                    existing.title_hint = profile.title_hint

    return list(profiles.values()), list_pages


def failure_row(link: ProfileLink, html_path: Path, text_path: Path, exc: Exception) -> dict[str, str]:
    return {
        "姓名": link.name_hint or NEEDS_CHECK,
        "职称": link.title_hint or NEEDS_CHECK,
        "导师类别": NEEDS_CHECK,
        "所属专业/院系": link.department or NEEDS_CHECK,
        "邮箱": NEEDS_CHECK,
        "个人简介": NEEDS_CHECK,
        "研究方向": NEEDS_CHECK,
        "代表性项目或论文关键词": NEEDS_CHECK,
        "团队介绍关键词": NEEDS_CHECK,
        "可能适合本科生参与的任务类型": NEEDS_CHECK,
        "direction_tags": "其他/待核验",
        "relevance_score": "0",
        "ai_cv_keywords": "",
        "research_plain_explanation": "当前主页抓取失败，无法判断方向。",
        "recommendation_priority": "C",
        "confidence": "low",
        "undergrad_openness": "low",
        "publication_potential": "low",
        "interest_match": "low",
        "fit_summary": "主页抓取失败，三个维度均需人工核验。",
        "来源URL": link.url,
        "发现来源URL": link.source_url,
        "原始HTML路径": str(html_path.relative_to(ROOT)),
        "原始文本路径": str(text_path.relative_to(ROOT)),
        "抓取状态": "失败",
        "备注": f"请求或解析失败：{exc}",
        "external_evidence": [],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Collect public HUST EIC teacher profiles and rank AI-related fit.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of teacher profiles to fetch; 0 means no limit.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay in seconds between requests.")
    parser.add_argument("--max-list-pages", type=int, default=DEFAULT_MAX_LIST_PAGES, help="Maximum staff-list pages to discover.")
    parser.add_argument("--refresh", action="store_true", help="Fetch teacher pages even when cached raw HTML exists.")
    args = parser.parse_args()

    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    RAW_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    robots_cache: dict[str, RobotFileParser] = {}
    profile_links, list_pages = discover_all_profile_links(load_seed_pages(), args.delay, robots_cache, args.max_list_pages)
    print(f"[INFO] Discovered {len(profile_links)} teacher profile links from {len(list_pages)} staff list pages.")

    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in profile_links:
        if args.limit and len(rows) >= args.limit:
            break
        if link.url in seen_urls:
            continue
        seen_urls.add(link.url)

        stem = safe_filename(link.url, link.name_hint)
        html_path = RAW_HTML_DIR / f"{stem}.html"
        text_path = RAW_TEXT_DIR / f"{stem}.txt"

        try:
            if html_path.exists() and not args.refresh:
                html = html_path.read_text(encoding="utf-8")
                row = parse_profile(html, link, html_path, text_path)
                rows.append(row)
                print(f"[CACHE] {rows[-1]['姓名']} score={rows[-1]['relevance_score']} tags={rows[-1]['direction_tags']} {link.url}")
            else:
                html = fetch(link.url, args.delay, robots_cache)
                html_path.write_text(html, encoding="utf-8")
                rows.append(parse_profile(html, link, html_path, text_path))
                print(f"[OK] {rows[-1]['姓名']} score={rows[-1]['relevance_score']} tags={rows[-1]['direction_tags']} {link.url}")
        except Exception as exc:
            if html_path.exists():
                html = html_path.read_text(encoding="utf-8")
                row = parse_profile(html, link, html_path, text_path)
                row["备注"] = f"本次请求失败，已使用缓存HTML；失败原因：{exc}"
                rows.append(row)
                print(f"[CACHE-WARN] {rows[-1]['姓名']} score={rows[-1]['relevance_score']} tags={rows[-1]['direction_tags']} {link.url}")
            else:
                rows.append(failure_row(link, html_path, text_path, exc))
                print(f"[WARN] {link.url}: {exc}")

    save_outputs(rows, len(profile_links), list_pages)
    print(f"Saved {len(rows)} rows and reports to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
