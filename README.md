# LabCompass HUST AI Research Finder

这个项目用于整理华中科技大学电子信息与通信学院公开教师主页信息，辅助本科生申请进实验室前做人工智能相关方向匹配分析。

项目只处理公开网页，不登录任何网站，不访问私人账号，不绕过访问限制。脚本会从学院官网师资队伍栏目自动发现教师列表页和教师主页，并重点筛选人工智能、机器学习、智能感知、图像/视觉、多模态、智能信息处理等相关老师。

## 文件结构

```text
LabCompass_Hust/
  config/
    seed_pages.json          # 学院公开师资列表页入口
  data/
    raw_html/                # 抓取到的原始 HTML
    raw_text/                # 从 HTML 提取的纯文本，便于人工检查
  output/
    all_teachers_raw.json    # 全院教师原始抓取结果
    all_teachers_profiles.md # 全院教师粗画像
    ai_teachers.md           # 人工智能相关老师重点清单
    ai_cv_teachers.md        # 兼容旧文件名，内容与 ai_teachers.md 同步
    scrape_report.md         # 抓取统计报告
    teachers.csv             # 完整自动提取结果，字段较长，适合留档
    teachers_summary.csv     # 短字段摘要，适合快速浏览
    teachers_readable.md     # GitHub 预览友好的导师卡片
    teachers.xlsx            # Excel 表格
  src/
    scrape_teachers.py       # 主脚本
  analysis_template.md       # 人工匹配度分析模板
  requirements.txt
```

## 安装依赖

建议在项目目录中创建虚拟环境：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果你暂时不想创建虚拟环境，也可以直接运行：

```bash
pip install -r requirements.txt
```

## 运行全院采集

默认尽量覆盖学院官网师资栏目发现到的全部教师主页，访问间隔 1.5 秒：

```bash
python src/scrape_teachers.py
```

调试时可以限制人数，避免反复访问太多页面：

```bash
python src/scrape_teachers.py --limit 10 --delay 2
```

脚本默认优先复用 `data/raw_html/` 中已有缓存，减少重复请求。需要重新抓取教师主页时使用：

```bash
python src/scrape_teachers.py --refresh --delay 2
```

运行完成后查看：

- `output/ai_teachers.md`：人工智能相关老师重点清单，按三维判断排序
- `output/ai_cv_teachers.md`：兼容旧文件名，内容与 `ai_teachers.md` 同步
- `output/all_teachers_profiles.md`：全院教师粗画像
- `output/all_teachers_raw.json`：结构化原始结果，便于后续分析
- `output/scrape_report.md`：抓取统计和低置信度提示
- `output/teachers_readable.md`：最适合在 GitHub 上直接阅读
- `output/teachers_summary.csv`：短字段摘要 CSV
- `output/teachers.csv`：完整字段 CSV，保留较长原文片段
- `output/teachers.xlsx`
- `data/raw_html/`
- `data/raw_text/`

## 更新数据

1. 打开 `config/seed_pages.json`，增删学院公开师资列表页。
2. 运行 `python src/scrape_teachers.py --refresh --delay 2` 重新抓取，或不加 `--refresh` 复用缓存快速重建报告。
3. 检查 `output/teachers.csv` 中 `抓取状态`、`备注`、`来源URL`。
4. 对字段为空、低置信度或标注“待人工核验”的老师，打开 `data/raw_text/` 或原网页人工核验。

## 手动补充无法自动提取的信息

网页结构可能不统一，尤其是外部教师主页、旧版个人页、图片化内容。建议：

1. 先打开 `来源URL` 对应网页。
2. 再打开 `data/raw_text/` 中同名文本，搜索“研究方向”“科研项目”“论文”“团队”“邮箱”等词。
3. 如果仍无法确认，字段保持“未找到”，不要凭印象补。
4. 可以另存一份人工修订版，例如 `output/teachers_manual.xlsx`，避免覆盖自动抓取结果。

## 访问礼仪和边界

- 脚本会读取 `robots.txt`；如果网站没有提供 robots 文件，则只访问配置中的公开页面和页面中明确出现的个人主页链接。
- 默认访问间隔为 1.5 秒，可以用 `--delay` 调大。
- 请求失败、结构复杂或字段无法确定时，脚本保存原始 HTML/文本并在表格中留空或标注“未找到”。
- 不伪造导师数据，不尝试登录，不使用私人账号。
- 论文库、学院新闻、实验室主页等外部证据已预留接口，但默认不抓取，避免扩大访问范围。

## 后续 TODO

- [ ] 增加人工修订表与自动抓取表的合并脚本。
- [ ] 根据你的个人期待方向，把 `interest_match` 从通用 AI 匹配改成个人匹配。
- [ ] 为不同研究方向建立更细的本科生任务类型标签和权重。
- [ ] 增加重复老师合并和旧页面识别。
- [ ] 为每位导师生成单独 Markdown 分析卡片。
- [ ] 接入学院新闻、实验室主页、Semantic Scholar、IEEE、DBLP 等外部证据源。
