from pathlib import Path
import re
import html
import json
from collections import Counter

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, PageBreak,
    LongTable, TableStyle, KeepTogether, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / '主题多分辨率适配技术研究与资源申请建议.md'
OUT = ROOT / '主题多分辨率适配技术研究与资源申请建议.pdf'
pdfmetrics.registerFont(TTFont('YaHei', 'C:/Windows/Fonts/msyh.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('YaHei-Bold', 'C:/Windows/Fonts/msyhbd.ttc', subfontIndex=0))
pdfmetrics.registerFontFamily('YaHei', normal='YaHei', bold='YaHei-Bold', italic='YaHei', boldItalic='YaHei-Bold')

W, H = A4
LEFT, RIGHT, TOP, BOTTOM = 53, 53, 51, 48
CW = W - LEFT - RIGHT
NAVY = colors.HexColor('#17344A')
LIGHT = colors.HexColor('#F1F5F8')
GRAY = colors.HexColor('#D9D9D9')
INK = colors.HexColor('#18212A')

body = ParagraphStyle('Body', fontName='YaHei', fontSize=10.1, leading=15.7,
    textColor=INK, wordWrap='CJK', spaceAfter=6.2, allowWidows=0, allowOrphans=0)
h1 = ParagraphStyle('Chapter', parent=body, fontName='YaHei-Bold', fontSize=16.1,
    leading=22, textColor=colors.black, spaceBefore=13, spaceAfter=10, keepWithNext=True)
h2 = ParagraphStyle('Subhead', parent=body, fontName='YaHei-Bold', fontSize=12.1,
    leading=18, textColor=colors.black, spaceBefore=9, spaceAfter=6, keepWithNext=True)
title = ParagraphStyle('Title', parent=body, fontName='YaHei-Bold', fontSize=24,
    leading=32, textColor=colors.black, spaceAfter=13)
subtitle = ParagraphStyle('Subtitle', parent=body, fontSize=12, leading=19,
    textColor=colors.black, spaceAfter=8)
meta = ParagraphStyle('Meta', parent=body, fontSize=8.9, leading=14.5,
    textColor=colors.HexColor('#53616D'), spaceAfter=15)
cell = ParagraphStyle('Cell', parent=body, fontSize=8.6, leading=13.5, spaceAfter=0)
cellhead = ParagraphStyle('CellHead', parent=cell, fontName='YaHei-Bold', textColor=colors.white)
code = ParagraphStyle('Code', parent=body, fontSize=8.6, leading=13,
    backColor=LIGHT, borderPadding=9, spaceBefore=5, spaceAfter=12)
refs = ParagraphStyle('Reference', parent=body, fontSize=9.3, leading=15.1, spaceAfter=8)
refs_compact = ParagraphStyle('ReferenceCompact', parent=refs, fontSize=9.0, leading=13.8, spaceAfter=5.5)
REFERENCE_MAP = dict(re.findall(r'^\[(P\d{2})\]: (https?://\S+)', SOURCE.read_text(encoding='utf-8'), re.M))


def markup(s):
    # Resolve Markdown formatting without ever injecting source text as XML.
    tokens = {}
    def hold(value):
        key = f'ZZFORMAT{len(tokens)}ZZ'
        tokens[key] = value
        return key
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: hold(f'<link href="{html.escape(m.group(2), quote=True)}" color="#1B5678">{html.escape(m.group(1))}</link>'), s)
    s = re.sub(r'`([^`]+)`', lambda m: hold('<font color="#334E63">' + html.escape(m.group(1)) + '</font>'), s)
    s = re.sub(r'\*\*([^*]+)\*\*', lambda m: hold('<b>' + html.escape(m.group(1)) + '</b>'), s)
    s = re.sub(r'\[(P\d{2})\]', lambda m: hold(
        f'<link href="{html.escape(REFERENCE_MAP[m.group(1)], quote=True)}" color="#1B5678">[{m.group(1)}]</link>'
    ) if m.group(1) in REFERENCE_MAP else m.group(0), s)
    s = html.escape(s)
    for key, value in tokens.items():
        s = s.replace(key, value)
    return s


class Architecture(Flowable):
    def __init__(self):
        Flowable.__init__(self)
        self.width = CW
        self.height = 226

    def draw(self):
        c = self.canv
        gap = 22
        bw = (CW - gap * 2) / 3
        bh = 49
        ys = [165, 93, 21]
        positions = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1), (0, 2), (1, 2), (2, 2)]
        labels = [
            ('主题包与素材', 'XML 资源与身份登记'),
            ('事实与交互状态', '规则 变量 路径与时间'),
            ('语义分组与检索', '关系图与已审案例'),
            ('拓扑与布局候选', '模型建议和软目标'),
            ('约束求解及写回', '几何裁决与受限修改'),
            ('多状态场景渲染', '截图 事件与缺陷诊断'),
            ('局部修复或接管', '保留可用候选和证据'),
            ('官方工具与真机', '指定设备和路径验证'),
            ('案例数据与回归', '已审结果和失败修复')]
        for i, ((col, row), (main, sub)) in enumerate(zip(positions, labels)):
            x, y = col * (bw + gap), ys[row]
            c.setStrokeColor(GRAY)
            c.setFillColor(NAVY if i in (0, 7, 8) else LIGHT)
            c.roundRect(x, y, bw, bh, 5, fill=1, stroke=1)
            c.setFillColor(colors.white if i in (0, 7, 8) else INK)
            c.setFont('YaHei-Bold', 10)
            c.drawCentredString(x + bw / 2, y + 29, main)
            c.setFont('YaHei', 7.7)
            c.drawCentredString(x + bw / 2, y + 13, sub)
        c.setStrokeColor(colors.HexColor('#788D9B'))
        c.setLineWidth(0.9)
        for i in range(8):
            a, b = positions[i], positions[i + 1]
            ax, ay = a[0] * (bw + gap), ys[a[1]]
            bx, by = b[0] * (bw + gap), ys[b[1]]
            if a[1] == b[1]:
                direction = 1 if b[0] > a[0] else -1
                x1 = ax + (bw if direction == 1 else 0)
                x2 = bx + (0 if direction == 1 else bw)
                y = ay + bh / 2
                c.line(x1 + 2 * direction, y, x2 - 3 * direction, y)
                c.line(x2 - 3 * direction, y, x2 - 7 * direction, y + 3)
                c.line(x2 - 3 * direction, y, x2 - 7 * direction, y - 3)
            else:
                x, y1, y2 = ax + bw / 2, ay, by + bh
                c.line(x, y1 - 2, x, y2 + 3)
                c.line(x, y2 + 3, x - 3, y2 + 7)
                c.line(x, y2 + 3, x + 3, y2 + 7)


def table_widths(header):
    first = header[0]
    n = len(header)
    if n == 6:
        return [CW * x for x in [.20, .10, .17, .17, .18, .18]]
    if n == 4:
        if first == '角色':
            vals = [.29, .14, .19, .38]
        elif first == '数据或基准':
            vals = [.20, .26, .27, .27]
        elif first == '层级':
            vals = [.16, .24, .28, .32]
        else:
            vals = [.19, .27, .28, .26]
    elif n == 3:
        vals = {
            '事项': [.20, .40, .40],
            '能力': [.19, .37, .44],
            '字段族': [.17, .51, .32],
            '角色': [.22, .32, .46],
            '费用项': [.30, .18, .52],
            '数据工作': [.31, .44, .25],
            '阶段': [.18, .43, .39],
            '实验': [.21, .44, .35],
            '编号与项目': [.31, .37, .32],
            '层级': [.18, .47, .35],
            '接口': [.23, .37, .40],
            '作业顺序': [.18, .43, .39],
        }.get(first, [.23, .36, .41])
    else:
        vals = [1 / n] * n
    return [CW * x for x in vals]


def create_table(rows):
    head = [t.strip() for t in rows[0]]
    data = [[Paragraph(markup(t.strip()), cellhead if i == 0 else cell) for t in row] for i, row in enumerate(rows)]
    t = LongTable(data, colWidths=table_widths(head), repeatRows=1,
        hAlign='LEFT', spaceBefore=3, spaceAfter=11)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F6F8FA')]),
        ('GRID', (0, 0), (-1, -1), .5, GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
    ]))
    if head[0] == '作业顺序':
        t.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 4.5),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5)]))
    return t


class AnnotationExample(Flowable):
    """Code-native illustration; no real theme or verified labels implied."""
    def __init__(self):
        Flowable.__init__(self)
        self.width, self.height = CW, 278

    def draw(self):
        c = self.canv
        teal = colors.HexColor('#257D83')
        amber = colors.HexColor('#A66B16')
        def text(x, y, t, size=9, color=INK):
            c.setFillColor(color)
            c.setFont('YaHei', size)
            c.drawString(x, y, t)
        def box(x, y, w, h, color, label):
            c.setStrokeColor(color)
            c.setLineWidth(1.2)
            c.setFillColor(colors.white)
            c.roundRect(x, y, w, h, 4, fill=1, stroke=1)
            text(x + 6, y + h - 14, label, 8, color)
        c.setFillColor(LIGHT)
        c.roundRect(0, 8, CW, 263, 6, fill=1, stroke=0)
        text(15, 250, '示意：角色、组件组与状态证据', 10)
        text(25, 230, '源竖屏', 9)
        text(221, 230, '目标横屏候选', 9)
        c.setStrokeColor(GRAY)
        c.setFillColor(colors.HexColor('#E8EEF3'))
        c.roundRect(25, 60, 91, 162, 5, fill=1, stroke=1)
        c.roundRect(221, 78, 240, 144, 5, fill=1, stroke=1)
        box(33, 178, 75, 35, NAVY, 'g0 时间日期')
        text(47, 186, '10:08', 11)
        box(33, 100, 75, 70, teal, 'g1 主视觉')
        text(48, 123, '图层组', 10, teal)
        box(33, 67, 75, 25, amber, 'g2 工具条')
        box(233, 158, 104, 52, NAVY, 'g0 时间日期')
        text(245, 172, '10:08', 16)
        box(349, 114, 99, 96, teal, 'g1 主视觉')
        text(371, 151, '图层组', 12, teal)
        box(233, 90, 104, 38, amber, 'g2 工具条')
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.line(134, 155, 202, 155)
        c.line(202, 155, 196, 160)
        c.line(202, 155, 196, 150)
        text(143, 169, '组级重排', 9)
        text(134, 135, '保留组内关系', 8)
        text(233, 62, '面板另采状态，隐藏位置与触发器均保留', 8)
        text(15, 39, '① 元素角色  ② 成员与关系  ③ 目标组框  ④ 回放及审核', 9)
        text(15, 21, '虚构示意；框不是实际求解结果，交互命中区域需单独确认。', 8)


class Report(BaseDocTemplate):
    def __init__(self, filename):
        BaseDocTemplate.__init__(self, filename, pagesize=A4,
            leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP, bottomMargin=BOTTOM,
            title='主题多分辨率适配技术研究与资源申请建议',
            author='主题多分辨率适配技术研究',
            subject='交互理解 场景渲染 布局算法 数据集建设与阶段性资源申请')
        f = Frame(LEFT, BOTTOM, CW, H - TOP - BOTTOM,
            leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0, id='main')
        self.addPageTemplates(PageTemplate(id='main', frames=[f], onPage=self.page_decoration))

    def page_decoration(self, c, doc):
        c.saveState()
        if doc.page > 1:
            c.setFont('YaHei', 8)
            c.setFillColor(colors.black)
            c.drawString(LEFT, H - 30, '主题多分辨率适配技术研究与资源申请建议')
        c.setFont('YaHei', 8)
        c.setFillColor(colors.HexColor('#61707B'))
        c.drawString(LEFT, 27, '2026年10月27日  |  技术研究与资源建议')
        c.drawRightString(W - RIGHT, 27, f'第 {doc.page} 页')
        c.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == 'Chapter':
            text = flowable.getPlainText()
            if text == '阅读索引':
                return
            key = 'sec-' + re.sub(r'[^\w]', '', text)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, 0, False)
            self.notify('TOCEntry', (0, text, self.page, key))
        elif isinstance(flowable, Paragraph) and flowable.style.name == 'Subhead':
            text = flowable.getPlainText()
            if re.match(r'^(6\.(?:[7-9]|1[0-5])|7\.(?:[7-9]|1[0-9]|2[0-5])|10\.5) ', text):
                key = 'step-' + re.sub(r'[^\w]', '', text)
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, 1, False)
                summaries = {
                    '6.7': '6.7至6.15 布局算法操作步骤',
                    '7.7': '7.7至7.16 数据集建设操作步骤',
                    '7.17': '7.17至7.25 标注作业 结构与外部数据借鉴',
                    '10.5': '10.5 前10个工作日任务表',
                }
                number = text.split(' ', 1)[0]
                if number in summaries:
                    self.notify('TOCEntry', (1, summaries[number], self.page, key))


def parse():
    lines = SOURCE.read_text(encoding='utf-8').splitlines()
    story = []
    i = 0
    in_appendix = False
    final_appendix = False
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if re.match(r'^\[[A-Z]\d{2}\]: https?://', line):
            i += 1
            continue
        if line.startswith('# '):
            story.append(Paragraph(markup(line[2:]), title))
            i += 1
            # Subtitle and date are the next two nonempty paragraphs.
            for st in [subtitle, meta]:
                while i < len(lines) and not lines[i].strip():
                    i += 1
                story.append(Paragraph(markup(lines[i]), st))
                i += 1
            continue
        if line.startswith('## '):
            text = line[3:]
            if text.startswith('2 '):
                story.append(PageBreak())
                story.append(Paragraph('阅读索引', h1))
                story.append(Paragraph('管理层可先阅读第1章、第10章和第11章；技术评审重点阅读第4章至第9章。具体实施见第6.7至6.15节、第7.7至7.25节和第10.5节；论文与开源项目入口见附录。', body))
                toc = TableOfContents()
                toc.levelStyles = [ParagraphStyle('TOC0', parent=body, fontSize=11,
                    leading=19, leftIndent=0, firstLineIndent=0, spaceBefore=3, spaceAfter=5),
                    ParagraphStyle('TOC1', parent=body, fontSize=9.6, leading=16,
                        leftIndent=15, firstLineIndent=0, spaceBefore=0, spaceAfter=3)]
                story.append(toc)
                story.append(PageBreak())
            elif text.startswith('附录'):
                story.append(PageBreak())
                in_appendix = True
                final_appendix = text.startswith('附录C')
            # Some major topic changes are given clear starts; shorter sections flow.
            elif text.startswith(('4 ', '5 ', '6 ', '7 ', '8 ', '9 ', '10 ', '11 ', '12 ')):
                story.append(PageBreak())
            story.append(Paragraph(markup(text), h1))
            i += 1
            continue
        if line.startswith('### '):
            if line.startswith(('### A5 ', '### A6 ', '### 7.17 ', '### 7.25 ', '### 10.5 ')):
                story.append(PageBreak())
            story.append(Paragraph(markup(line[4:]), h2))
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                parts = lines[i].strip().strip('|').split('|')
                if not all(re.match(r'^\s*:?-+:?\s*$', x) for x in parts):
                    rows.append(parts)
                i += 1
            story.append(create_table(rows))
            continue
        if line.startswith('```'):
            content = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                content.append(lines[i])
                i += 1
            i += 1
            if content and content[0] == '标注示意流程':
                story.append(AnnotationExample())
            elif content and content[0] == '主题包与素材':
                story.append(Architecture())
            else:
                raw = '<br/>'.join(html.escape(x).replace('  ', '&nbsp;&nbsp;') for x in content)
                story.append(KeepTogether([Paragraph(raw, code)]))
            continue
        paragraph = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#', '|', '```')):
            paragraph.append(lines[i].rstrip())
            i += 1
        # Paper titles and descriptions remain together.
        if in_appendix and paragraph[0].startswith('**P'):
            head = Paragraph(markup(paragraph[0]), ParagraphStyle('PaperTitle', parent=refs,
                fontName='YaHei-Bold', spaceBefore=6, spaceAfter=3, keepWithNext=True))
            desc = Paragraph(markup(' '.join(paragraph[1:])), refs)
            story.append(KeepTogether([head, desc]))
        else:
            story.append(Paragraph(markup(' '.join(paragraph)), refs_compact if final_appendix else refs if in_appendix else body))
    return story


if __name__ == '__main__':
    doc = Report(str(OUT))
    doc.multiBuild(parse())
    reader = PdfReader(str(OUT))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    source = SOURCE.read_text(encoding='utf-8')
    cited = set(re.findall(r'\[(P\d{2})\]', source))
    defined = set(re.findall(r'^\*\*(P\d{2}) ', source, re.M))
    annotations = sum(len(page.get('/Annots', [])) for page in reader.pages)
    result = {
        'pages': len(reader.pages), 'bytes': OUT.stat().st_size,
        'paper_count': len(defined), 'unresolved_paper_ids': sorted(cited - defined),
        'pdf_annotations': annotations, 'extracted_chars': len(text),
        'replacement_characters': text.count('\ufffd'),
        'contains_budget': '35000' in text,
        'contains_executive': '管理层摘要' in text,
    }
    (ROOT / 'qa_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
