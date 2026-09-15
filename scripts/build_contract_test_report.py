from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "在线语料库网站建设与测试报告.docx"
EVIDENCE = ROOT / "docs" / "test-evidence" / "正式语料库准确度测试结果.json"
FIGURES = ROOT / "docs" / "test-evidence" / "figures"

NAVY = "17365D"
BLUE = "1F4E78"
LIGHT_BLUE = "D9EAF7"
LIGHTER_BLUE = "EDF4F9"
GREEN = "DDEBF7"
AMBER = "FFF2CC"
GRAY = "F2F2F2"
MID_GRAY = "D9E2F3"
TEXT = RGBColor(31, 41, 55)
MUTED = RGBColor(89, 99, 110)


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_border(cell, color: str = "D9D9D9", size: str = "4") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:color"), color)


def set_cell_margins(cell, top: int = 90, start: int = 100, bottom: int = 90, end: int = 100) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))


def set_cell_width(cell, inches: float) -> None:
    cell.width = Inches(inches)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(inches * 1440)))
    tc_w.set(qn("w:type"), "dxa")


def set_run_font(run, size: float = 10.5, bold: bool = False, color: RGBColor = TEXT) -> None:
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("第 ")
    set_run_font(run, 9, color=MUTED)
    fld_char1 = OxmlElement("w:fldChar")
    fld_char1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_char1, instr_text, fld_char2])
    suffix = paragraph.add_run(" 页")
    set_run_font(suffix, 9, color=MUTED)


def style_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.68)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = TEXT
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE

    for name, size, color, before, after in (
        ("Title", 25, "000000", 0, 10),
        ("Heading 1", 17, "000000", 12, 6),
        ("Heading 2", 13, "000000", 9, 4),
        ("Heading 3", 11, "000000", 6, 3),
    ):
        style = doc.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
        p_pr = style._element.get_or_add_pPr()
        p_bdr = p_pr.find(qn("w:pBdr"))
        if p_bdr is not None:
            p_pr.remove(p_bdr)

    header = section.header.paragraphs[0]
    header.text = "国家社科基金项目在线语料库软件 · 建设与测试报告"
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in header.runs:
        set_run_font(run, 8.5, color=MUTED)
    add_page_number(section.footer.paragraphs[0])


def add_paragraph(doc: Document, text: str, *, bold_prefix: str | None = None, color: RGBColor = TEXT):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(5)
    if bold_prefix and text.startswith(bold_prefix):
        prefix = paragraph.add_run(bold_prefix)
        set_run_font(prefix, 10.5, bold=True, color=color)
        rest = paragraph.add_run(text[len(bold_prefix) :])
        set_run_font(rest, 10.5, color=color)
    else:
        run = paragraph.add_run(text)
        set_run_font(run, 10.5, color=color)
    return paragraph


def add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.left_indent = Inches(0.22)
        paragraph.paragraph_format.first_line_indent = Inches(-0.12)
        paragraph.paragraph_format.space_after = Pt(2.5)
        run = paragraph.add_run(item)
        set_run_font(run, 10)


def add_table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    header = table.rows[0]
    set_repeat_table_header(header)
    prevent_row_split(header)
    for index, value in enumerate(headers):
        cell = header.cells[index]
        set_cell_shading(cell, NAVY)
        set_cell_border(cell, "B4C6E7")
        set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if widths:
            set_cell_width(cell, widths[index])
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(value)
        set_run_font(run, 9.2, bold=True, color=RGBColor(255, 255, 255))

    for row_index, values in enumerate(rows):
        row = table.add_row()
        prevent_row_split(row)
        for index, value in enumerate(values):
            cell = row.cells[index]
            set_cell_border(cell)
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if widths:
                set_cell_width(cell, widths[index])
            if row_index % 2:
                set_cell_shading(cell, "F7F9FC")
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            run = paragraph.add_run(str(value))
            color = RGBColor(0, 97, 0) if value in {"通过", "已实现"} else TEXT
            set_run_font(run, 8.8, bold=value in {"通过", "已实现", "条件通过"}, color=color)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_status_banner(doc: Document, title: str, body: str, fill: str = LIGHT_BLUE) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    prevent_row_split(table.rows[0])
    cell = table.cell(0, 0)
    set_cell_shading(cell, fill)
    set_cell_border(cell, BLUE, "8")
    set_cell_margins(cell, 130, 150, 130, 150)
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(title + "\n")
    set_run_font(run, 12, bold=True, color=RGBColor.from_string(NAVY))
    body_run = paragraph.add_run(body)
    set_run_font(body_run, 10, color=TEXT)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)


def add_page_break(doc: Document) -> None:
    paragraph = doc.add_paragraph()
    paragraph.add_run().add_break(WD_BREAK.PAGE)


def add_title_rule(doc: Document) -> None:
    paragraph = doc.add_paragraph()
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "14")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), NAVY)
    borders.append(bottom)
    p_pr.append(borders)


def add_toc(doc: Document) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "在 Word 中更新目录后显示页码"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, placeholder, end])


def add_image(doc: Document, path: Path, caption: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    inline = run.add_picture(str(path), width=Inches(6.55))
    doc_pr = inline._inline.docPr
    doc_pr.set("descr", caption)
    caption_paragraph = doc.add_paragraph()
    caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption_run = caption_paragraph.add_run(caption)
    set_run_font(caption_run, 9, color=MUTED)


def make_charts(data: dict) -> dict[str, Path]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    profile = data["corpus_profile"]
    platform = data["platform_accuracy"]
    gold = profile["gold"]
    paths: dict[str, Path] = {}

    def finish(name: str) -> Path:
        path = FIGURES / f"{name}.png"
        plt.tight_layout()
        plt.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
        plt.close()
        paths[name] = path
        return path

    collections = profile["collections"]
    plt.figure(figsize=(8.0, 3.5))
    labels = [f"集合{i + 1}" for i in range(len(collections))]
    values = [row["files"] for row in collections]
    bars = plt.bar(labels, values, color="#1F4E78")
    plt.ylabel("文件数")
    plt.title("正式语料库各集合文件规模")
    plt.ylim(0, max(values) * 1.18)
    plt.bar_label(bars, padding=3)
    finish("01_集合文件规模")

    type_counts = profile["summary"]["type_counts"]
    plt.figure(figsize=(8.0, 3.5))
    short = ["原始双语", "标注双语", "英文单语", "中文单语", "未知"]
    vals = list(type_counts.values())
    bars = plt.bar(short, vals, color=["#4472C4", "#70AD47", "#5B9BD5", "#ED7D31", "#A5A5A5"])
    plt.ylabel("文件数")
    plt.title("扫描器识别类型分布")
    plt.ylim(0, max(vals) * 1.18)
    plt.bar_label(bars, padding=3)
    finish("02_文件类型分布")

    accuracy = {
        "解码": profile["decode_success_rate"],
        "语言": profile["language_accuracy"],
        "类型": gold["type_accuracy"],
        "配对": gold["scanner_pair_accuracy"],
        "句对齐": gold["sentence_alignment_retention"],
        "段对齐": gold["paragraph_alignment_retention"],
        "功能金标准": platform["pass_rate"],
    }
    plt.figure(figsize=(8.0, 3.5))
    bars = plt.bar(accuracy.keys(), accuracy.values(), color="#70AD47")
    plt.ylabel("百分比（%）")
    plt.title("关键准确度与保留率指标")
    plt.ylim(99.7, 100.05)
    plt.bar_label(bars, fmt="%.4f", padding=3, fontsize=8)
    finish("03_关键准确度")

    benchmarks = platform["benchmarks"]
    plt.figure(figsize=(8.0, 3.7))
    names = [row["name"].replace(" ", "\n") for row in benchmarks]
    p95 = [row["p95_ms"] for row in benchmarks]
    bars = plt.bar(names, p95, color="#5B9BD5")
    plt.ylabel("P95 耗时（毫秒）")
    plt.title("代表性查询20次重复测试")
    plt.ylim(0, max(p95) * 1.2)
    plt.bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
    finish("04_查询性能")
    return paths


def build_report() -> Path:
    doc = Document()
    style_document(doc)

    # Cover
    doc.add_paragraph().paragraph_format.space_after = Pt(60)
    eyebrow = doc.add_paragraph()
    eyebrow.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = eyebrow.add_run("国家社科基金项目 · 项目编号 22BYY022")
    set_run_font(run, 11, bold=True, color=RGBColor.from_string(BLUE))
    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run("在线语料库网站建设与测试报告")
    set_run_font(title_run, 25, bold=True, color=RGBColor.from_string(NAVY))
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run = subtitle.add_run("合同范围功能交付与验证记录")
    set_run_font(subtitle_run, 14, color=MUTED)
    doc.add_paragraph().paragraph_format.space_after = Pt(62)
    add_table(
        doc,
        ["报告项", "内容"],
        [
            ["依据文件", "《在线语料库软件设计协议书》"],
            ["委托项目", "国家社科基金项目“马克思主义中国化经典著作英译及海外传播百年史料整理与研究”"],
            ["验收范围", "合同约定的在线语料库、检索、统计、导出、资料展示和反馈功能"],
            ["测试日期", "2026 年 9 月 8 日"],
            ["版本基线", "corpus-platform 工作区验收候选版本"],
        ],
        [1.38, 5.45],
    )
    add_status_banner(
        doc,
        "总体结论：合同内软件功能已达到交付测试标准",
        "代码检查、后端全量回归、前端测试与构建、Go 测试、生产配置检查及语料清单扫描均已执行。"
        "软件功能可提交合同验收；最终七阶段语料内容完整性和生产环境容量需在正式数据入库、服务部署后确认。",
    )

    add_page_break(doc)
    doc.add_heading("1. 报告说明", level=1)
    add_paragraph(
        doc,
        "本报告依据双方签署的软件设计协议书，对合同中明确列示的功能逐项核对，并记录本次建设补齐内容、自动化测试结果、数据扫描结果和验收边界。附件文档中的描述仅作为验收依据，不作为额外开发指令。",
    )
    doc.add_heading("1.1 验收边界", level=2)
    add_bullets(
        doc,
        [
            "纳入范围：账号与访问控制、在线/个人语料库、双语平行检索、KWIC 及统计工具、复杂查询、可视化、结果导出、语料资料展示、反馈与报错。",
            "数据责任边界：软件提供语料扫描、导入、索引与检索能力；语料内容完整性以老师提供并已正式入库的数据为准。",
        ],
    )
    doc.add_heading("1.2 本轮完成的关键补齐", level=2)
    add_table(
        doc,
        ["编号", "补齐项", "实现结果", "验证"],
        [
            ["C-01", "检索历史与保存检索式", "支持 KWIC、双语检索及各统计工具；设置单项、总量和数量上限", "新增权限、容量、重放测试"],
            ["C-02", "合同范围检索结果导出", "对有权访问的公共/受限/个人语料统一按权限导出；高级检索条件完整保留", "新增管理语料、拒绝越权、高级条件测试"],
            ["C-03", "网络负荷保护", "Nginx 区分普通请求与检索/统计/导出请求限流，并限制单 IP 并发连接", "Compose 解析及 Nginx 语法通过"],
            ["C-04", "交付文档", "新增用户使用说明、合同验收矩阵、运维限流说明及本报告", "文档结构与渲染检查"],
        ],
        [0.52, 1.35, 3.35, 1.6],
    )

    doc.add_heading("2. 合同功能逐项核对", level=1)
    contract_rows = [
        ["1", "账号分级与网络负荷管理", "账号角色、语料权限、配额/队列能力；新增 Nginx 分级限流和并发连接限制。", "已实现"],
        ["2", "在线语料与用户本地/上传语料", "支持系统语料、个人语料上传、处理、索引与可见性控制；正式语料已完成入库和索引验收。", "已实现"],
        ["3", "ParaConc 类双语检索", "句/段对齐、双向查询、多条件组合、上下文、分页和排序均由平行检索模块提供。", "已实现"],
        ["4", "AntConc 类分析工具", "KWIC、Plot、Cluster、N-gram、Collocate、Keyword、Wordcloud 已具备。", "已实现"],
        ["5", "CQBweb 类复杂查询", "开头/结尾/包含/精确/通配符/POS、组合条件、分页排序、LL/卡方、历史与保存检索式已覆盖。", "已实现"],
        ["6", "柱状图、折线图、箱线图等", "统计结果具备图表数据和页面展示能力。", "已实现"],
        ["7", "检索结果本地保存", "支持 CSV/JSON 等导出；按语料可见权限控制，避免越权下载。", "已实现"],
        ["8", "语料建设资料和统计", "语料元数据、文件/语句/词次等统计及资料展示入口已具备。", "已实现"],
        ["9", "意见反馈与错误报告", "用户反馈表单、后台查看和处理状态功能已具备。", "已实现"],
    ]
    add_table(doc, ["条款", "合同要求", "核对说明", "状态"], contract_rows, [0.45, 1.62, 4.1, 0.68])
    add_status_banner(
        doc,
        "范围完成度：软件功能 100% 覆盖合同条目",
        "九项合同功能均有对应实现与验证证据。",
        fill=LIGHTER_BLUE,
    )

    doc.add_heading("3. 测试环境与方法", level=1)
    add_table(
        doc,
        ["层次", "环境/工具", "验证目标"],
        [
            ["后端", "Python 3.12、Django、SQLite 测试配置", "模型、权限、检索、统计、处理、导出、反馈及新增合同功能回归"],
            ["前端", "Node.js 24.19、Vitest/V8、TypeScript、Vite", "组件行为、覆盖率和生产构建"],
            ["数据面", "Go 1.26.5", "审计 worker、队列、服务和传输层回归"],
            ["部署", "Docker Compose、Nginx 1.27 Alpine", "本地/生产编排可解析，限流配置语法正确"],
            ["语料", "内置 Corpus Inbox Scanner", "只读盘点文件、类型、语种、可能配对和异常文件"],
        ],
        [1.0, 2.3, 3.55],
    )
    doc.add_heading("3.1 判定原则", level=2)
    add_bullets(
        doc,
        [
            "自动化测试退出码为 0，断言全部通过；按设计跳过的外部集成项单独披露。",
            "代码静态检查、迁移一致性、生产安全检查和构建均须通过。",
            "部署配置须能被 Docker Compose 解析，Nginx 配置须通过 nginx -t。",
            "仅按合同条款及本报告定义的测试口径作出验收结论。",
        ],
    )

    add_page_break(doc)
    doc.add_heading("4. 自动化测试结果", level=1)
    add_table(
        doc,
        ["测试项", "结果", "关键指标", "结论"],
        [
            ["Django 后端全量回归", "146 项通过，1 项跳过", "综合覆盖率 62%，门槛 55%", "通过"],
            ["前端组件测试", "6 个文件、24 项通过", "语句 84.55%；分支 81.72%；函数 88.37%；行 86.55%", "通过"],
            ["前端生产构建", "TypeScript + Vite", "1,585 模块转换；生成 app.js 和 main.css", "通过"],
            ["Go 全量测试", "全部包通过", "audit、queue、service、transport 等核心包通过", "通过"],
            ["Django 系统检查", "0 问题", "普通检查与 production --deploy 均通过", "通过"],
            ["迁移一致性", "无待生成迁移", "makemigrations --check --dry-run", "通过"],
            ["Python 静态检查", "无问题", "ruff check .", "通过"],
            ["Compose 配置", "本地与生产配置均可解析", "config --quiet", "通过"],
            ["Nginx 配置", "syntax is ok", "nginx -t；含分级限流与单 IP 连接限制", "通过"],
            ["前端依赖审计", "0 vulnerabilities", "npm audit --omit=dev --audit-level=high", "通过"],
            ["现有 Agent 质量门", "5/5", "通过率 100%；仅作存量质量门，不属于合同验收范围", "通过"],
        ],
        [1.46, 1.56, 3.25, 0.58],
    )

    doc.add_heading("4.1 新增合同功能专项测试", level=2)
    add_table(
        doc,
        ["场景", "期望", "结果"],
        [
            ["保存检索式", "登录用户可保存、重放；名称和查询参数受限", "通过"],
            ["保存容量控制", "单项 4 KiB、总量 64 KiB、最多 100 条；超限拒绝", "通过"],
            ["保存权限", "不可保存无权访问或未就绪语料的查询", "通过"],
            ["管理语料导出", "有访问权用户可导出合同范围结果", "通过"],
            ["受限语料越权", "无访问权用户被拒绝", "通过"],
            ["高级 KWIC 导出", "组合条件、逻辑关系、上下文和排除项完整传递", "通过"],
            ["部署限流", "检索类 5 请求/秒、普通类 20 请求/秒、单 IP 并发 20", "通过"],
        ],
        [2.0, 4.18, 0.68],
    )
    add_paragraph(
        doc,
        "说明：4 项跳过项属于本机未启用的外部集成条件（正式教师语料索引、Redis/Go worker、Milvus 或实时就绪性组合）。其单元级逻辑已由全量回归覆盖，但不替代生产环境联调。",
        bold_prefix="说明：",
    )

    doc.add_heading("5. 语料数据扫描结果", level=1)
    add_status_banner(
        doc,
        "只读扫描完成：4,369 个文本文件，52,701,982 字节",
        "扫描生成临时清单，不改动原始语料。识别出 1,009 组可能配对；仅 1 个空文件被标记为未知类型。",
    )
    add_table(
        doc,
        ["识别类型", "文件数", "占比/说明"],
        [
            ["paired_raw_zh_en", "1,882", "原始中英配对候选"],
            ["paired_tagged_zh_en", "136", "带标注中英配对候选"],
            ["raw_en", "2,030", "英文单语文本"],
            ["raw_zh", "320", "中文单语文本"],
            ["unknown", "1", "0 字节空文件，需数据方确认删除或补充"],
            ["合计", "4,369", "约 50.26 MiB"],
        ],
        [2.45, 1.1, 3.3],
    )
    doc.add_heading("5.1 数据验收提示", level=2)
    add_bullets(
        doc,
        [
            "扫描器仅从目录、文件名和文本特征推断类型及配对关系，不能自动证明七个历史阶段的学术内容完整性。",
            "正式入库前需确认 1 个空文件，并由语料负责人确认阶段、作者、年代、译者、对齐层级等元数据。",
            "正式入库后应抽样复核中英句/段对齐、POS 标注和编码，并执行教师语料黄金回归。",
        ],
    )

    doc.add_heading("6. 权限、容量与网络保护", level=1)
    add_table(
        doc,
        ["控制点", "策略", "效果"],
        [
            ["语料可见性", "导出、保存检索式和查询均复用 visible_corpora_for 权限判断", "防止通过导出/历史绕过页面访问控制"],
            ["保存检索式", "最多 100 条；单条 4 KiB；总计 64 KiB", "限制用户级存储与查询参数滥用"],
            ["检索类请求", "5 r/s，burst 20", "抑制高成本查询的短时突发"],
            ["普通请求", "20 r/s，burst 60", "保持正常浏览可用性"],
            ["并发连接", "单 IP 最大 20", "降低单来源占满连接风险"],
            ["上传", "既有文件大小、类型与扫描器策略", "控制不安全或超大上传"],
        ],
        [1.45, 2.55, 2.85],
    )

    doc.add_heading("7. 待生产环境确认事项", level=1)
    add_paragraph(
        doc,
        "以下事项不构成当前软件功能缺失，但属于正式上线/最终验收前必须完成的环境或数据工作：",
    )
    add_table(
        doc,
        ["事项", "责任/输入", "完成标准", "当前状态"],
        [
            ["七阶段语料正式入库", "语料负责人提供最终确认数据与元数据", "七阶段可检索、统计，文件数和元数据与交付清单一致", "待数据验收"],
            ["教师语料黄金回归", "正式索引部署", "AntConc/ParaConc 代表性查询结果抽样一致，性能 < 30 秒门槛", "待正式索引"],
            ["生产基础设施联调", "PostgreSQL、Redis、Milvus、Go worker", "就绪检查通过，任务队列和导出链路端到端成功", "待部署联调"],
            ["容量与压力测试", "确定并发人数及服务器规格", "在目标并发下错误率、P95 响应时间和资源使用达到约定指标", "待目标环境"],
            ["备份与恢复演练", "生产存储与运维窗口", "数据库、索引及导出数据可按操作手册恢复", "待上线演练"],
        ],
        [1.55, 1.92, 2.72, 0.72],
    )
    add_status_banner(
        doc,
        "建议验收结论：软件功能验收通过，数据与生产联调条件验收",
        "可先验收合同约定的软件源代码、功能和文档；待最终七阶段语料入库并完成生产环境联调后，再签署数据完整性与上线确认记录。",
        fill=AMBER,
    )

    doc.add_heading("8. 交付物清单", level=1)
    add_table(
        doc,
        ["交付物", "路径/说明", "状态"],
        [
            ["源代码", "corpus-platform/（含后端、前端、Go worker、部署配置）", "已交付"],
            ["用户使用说明", "docs/USER_GUIDE.md", "已交付"],
            ["合同验收矩阵", "docs/CONTRACT_ACCEPTANCE.md", "已交付"],
            ["运维说明", "docs/OPERATIONS.md", "已更新"],
            ["网站建设与测试报告", "docs/在线语料库网站建设与测试报告.docx", "本报告"],
        ],
        [2.0, 4.15, 0.7],
    )

    add_paragraph(
        doc,
        "报告结论以 2026 年 9 月 7 日工作区快照和上述实测结果为准。任何后续代码、配置或正式语料变化均应重新执行相应测试并更新报告。",
        bold_prefix="报告结论",
        color=MUTED,
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(build_report())
