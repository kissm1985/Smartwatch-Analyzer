"""PDF exports with ReportLab (pure Python, no system libraries) – SPEC 8.1.

* report PDF: cover, table of contents, page numbers ("N / M"), header + footer with the
  period and versions, all sections (facts, findings, tables, charts as 200 dpi PNG),
  methodology, thresholds, disclaimer; A4 portrait; DejaVu Sans embedded (Hungarian accents).
* analysis PDF: Explorer chart + table + parameter summary + optional user note.

Output is deterministic for the same input (``invariant`` mode: fixed document id/dates).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import matplotlib
import pandas as pd
import plotly.graph_objects as go
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from vitalmart import __version__
from vitalmart.export.figures import DPI, figure_png
from vitalmart.fmt import fmt_datetime
from vitalmart.i18n import t

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 18 * mm, 20 * mm, 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN_X

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")
SOFT = colors.HexColor("#f8fafc")
SEVERITY = {"info": "#2563eb", "attention": "#d97706", "consult": "#dc2626",
            "positive": "#16a34a"}

MAX_TABLE_ROWS = 400   # analysis PDF: longer results go to XLSX/CSV (noted in the PDF)


def register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    pdfmetrics.registerFont(TTFont(FONT, str(ttf / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(ttf / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT,
                                  boldItalic=FONT_BOLD)


def _styles() -> dict[str, ParagraphStyle]:
    register_fonts()
    base = ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=13, textColor=INK,
                          spaceAfter=4)
    return {
        "body": base,
        "muted": ParagraphStyle("muted", parent=base, textColor=MUTED, fontSize=8.5, leading=11.5),
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10.5),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8, leading=10, spaceAfter=0),
        "cellh": ParagraphStyle("cellh", parent=base, fontName=FONT_BOLD, fontSize=7.5,
                                leading=9.5, textColor=MUTED, spaceAfter=0),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=15, leading=19,
                             spaceBefore=4, spaceAfter=8),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_BOLD, fontSize=10, leading=13,
                             textColor=MUTED, spaceBefore=8, spaceAfter=4),
        "chart": ParagraphStyle("chart", parent=base, fontName=FONT_BOLD, fontSize=9,
                                leading=12, spaceBefore=6, spaceAfter=2),
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=26,
                                leading=32, spaceAfter=10),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=13, leading=17,
                                   textColor=MUTED, spaceAfter=18),
        "toc0": ParagraphStyle("toc0", parent=base, fontSize=10.5, leading=16),
        "center": ParagraphStyle("center", parent=base, alignment=TA_CENTER, textColor=MUTED,
                                 fontSize=8.5),
    }


def p(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


# ---------------------------------------------------------------- document skeleton

class _NumberedCanvas(Canvas):
    """Defers page output so the footer can show 'page N / total'."""

    numbered_cover = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_page_number(total)
            super().showPage()
        super().save()

    def _draw_page_number(self, total: int) -> None:
        if self._pageNumber == 1 and not self.numbered_cover:
            return
        self.setFont(FONT, 7.5)
        self.setFillColor(MUTED)
        self.drawRightString(PAGE_W - MARGIN_X, 10 * mm,
                             t("export.pdf.page", n=self._pageNumber, total=total))


class _Doc(BaseDocTemplate):
    def __init__(self, buf: io.BytesIO, *, header: str, footer: str, cover: bool,
                 **kw: Any) -> None:
        super().__init__(buf, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
                         topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM, invariant=1,
                         creator=f"VitalMart {__version__}", producer="VitalMart (ReportLab)",
                         **kw)
        self.header, self.footer = header, footer
        frame = Frame(MARGIN_X, MARGIN_BOTTOM, FRAME_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
                      id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        templates = [PageTemplate("cover", [frame], onPage=self._cover),
                     PageTemplate("body", [frame], onPage=self._decorate)]
        self.addPageTemplates(templates if cover else templates[::-1])
        self._heading_seq = 0

    def _cover(self, canv: Canvas, _doc: Any) -> None:
        canv.saveState()
        canv.setFillColor(colors.HexColor("#2563eb"))
        canv.rect(0, PAGE_H - 12 * mm, PAGE_W, 12 * mm, stroke=0, fill=1)
        canv.restoreState()

    def _decorate(self, canv: Canvas, _doc: Any) -> None:
        canv.saveState()
        canv.setFont(FONT, 7.5)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN_X, PAGE_H - 12 * mm, self.header)
        canv.setStrokeColor(LINE)
        canv.line(MARGIN_X, PAGE_H - 13.5 * mm, PAGE_W - MARGIN_X, PAGE_H - 13.5 * mm)
        canv.line(MARGIN_X, 13 * mm, PAGE_W - MARGIN_X, 13 * mm)
        canv.drawString(MARGIN_X, 10 * mm, self.footer)
        canv.restoreState()

    def afterFlowable(self, flowable: Flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name == "h1":
            text = flowable.getPlainText()
            self._heading_seq += 1
            key = f"h{self._heading_seq}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0)
            self.notify("TOCEntry", (0, text, self.page, key))

    def handle_documentBegin(self) -> None:
        self._heading_seq = 0
        super().handle_documentBegin()


def _build(story: list[Flowable], *, title: str, subject: str, author: str, header: str,
           footer: str, toc: bool) -> bytes:
    register_fonts()
    buf = io.BytesIO()
    doc = _Doc(buf, header=header, footer=footer, cover=toc, title=title, subject=subject,
               author=author, keywords="VitalMart")
    canvasmaker = type("_Canvas", (_NumberedCanvas,), {"numbered_cover": not toc})
    if toc:
        doc.multiBuild(story, canvasmaker=canvasmaker)
    else:
        doc.build(story, canvasmaker=canvasmaker)
    return buf.getvalue()


# ---------------------------------------------------------------- building blocks

def kv_table(rows: Sequence[tuple[str, str]], st: dict[str, ParagraphStyle],
             key_width: float = 62 * mm) -> Table:
    data = [[p(k, st["cellh"]), p(v, st["cell"])] for k, v in rows]
    tbl = Table(data, colWidths=[key_width, FRAME_W - key_width], hAlign="LEFT")
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                             ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                             ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    return tbl


def data_table(head: Sequence[str], rows: Sequence[Sequence[object]],
               st: dict[str, ParagraphStyle], col_widths: Sequence[float] | None = None) -> Table:
    data = [[p(h, st["cellh"]) for h in head]] + [[p(c, st["cell"]) for c in r] for r in rows]
    n = max(len(head), 1)
    tbl = Table(data, colWidths=list(col_widths) if col_widths else [FRAME_W / n] * n,
                repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED),
                             ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                             ("TOPPADDING", (0, 0), (-1, -1), 2),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                             ("LEFTPADDING", (0, 0), (-1, -1), 3),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    return tbl


def finding_box(label: str, text: str, meta: str, severity: str,
                st: dict[str, ParagraphStyle]) -> Table:
    hexcolor = SEVERITY.get(severity, SEVERITY["info"])
    color = colors.HexColor(hexcolor)
    body = [Paragraph(f'<font name="{FONT_BOLD}" color="{hexcolor}">'
                      f"{escape(label.upper())}</font>  {escape(text)}", st["body"]),
            p(meta, st["muted"])]
    tbl = Table([[body]], colWidths=[FRAME_W], hAlign="LEFT")
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT),
                             ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, -1), 4),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return tbl


def chart_image(fig: go.Figure, max_height: float = 120 * mm) -> Image:
    png = figure_png(fig)
    img = Image(io.BytesIO(png))
    w, h = img.imageWidth / DPI * inch, img.imageHeight / DPI * inch
    scale = min(1.0, FRAME_W / w, max_height / h)
    img.drawWidth, img.drawHeight = w * scale, h * scale
    img.hAlign = "CENTER"
    return img


def note_box(text: str, st: dict[str, ParagraphStyle], color: str = "#dc2626") -> Table:
    tbl = Table([[p(text, st["body"])]], colWidths=[FRAME_W])
    tbl.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor(color)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return tbl


def _toc(st: dict[str, ParagraphStyle]) -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [st["toc0"]]
    toc.dotsMinLevel = 0
    return toc


# ---------------------------------------------------------------- report PDF

def report_pdf(rep: Any) -> bytes:
    """``rep`` is a :class:`vitalmart.report.engine.Report`."""
    from vitalmart.report.engine import finding_label, period_text

    st = _styles()
    period = period_text(rep)
    meta = lambda f: (t("report.meta", basis=f.basis, confidence=f.confidence, n=f.n)  # noqa: E731
                      + (f" · {t('report.source')}: {f.source}" if f.source else ""))

    def finding(f: Any) -> Table:
        return finding_box(finding_label(f), f.text, meta(f),
                           "positive" if f.positive else f.severity, st)

    story: list[Flowable] = [Spacer(1, 30 * mm), p(t("report.title"), st["title"]),
                             p(f"{rep.person_name} · {period}", st["subtitle"])]
    sources = "; ".join(f"{s['name']} ({s['vendor']}, {s['type']})" for s in rep.sources) or "–"
    story += [kv_table([
        (t("report.cover.compare"), f"{rep.cmp.start:%Y.%m.%d.} – {rep.cmp.end:%Y.%m.%d.}"),
        (t("report.cover.sources"), sources),
        (t("report.cover.coverage"), f"{rep.coverage_pct:.0f} %"),
        (t("export.pdf.generated"), fmt_datetime(rep.generated_at)),
        (t("export.pdf.versions"), t("export.pdf.versions_value", app=__version__,
                                     rules=rep.rules_version, data=rep.data_version)),
    ], st), Spacer(1, 14 * mm), note_box(rep.disclaimer, st)]
    story += [NextPageTemplate("body"), PageBreak(), p(t("report.toc"), st["h3"]), _toc(st),
              PageBreak()]

    story.append(p(t("report.section.summary"), st["h1"]))
    if rep.summary:
        story += [finding(f) for f in rep.summary]
    else:
        story.append(p(t("report.no_findings"), st["muted"]))
    if rep.changes:
        story += [p(t("report.changes"), st["h3"]),
                  data_table([t("report.t.metric"), t("report.t.current"),
                              t("report.t.previous"), t("report.t.change")], rep.changes, st,
                             [FRAME_W * 0.4, FRAME_W * 0.2, FRAME_W * 0.2, FRAME_W * 0.2])]

    for s in rep.sections:
        story += [CondPageBreak(60 * mm), p(s.title, st["h1"])]
        if not s.available:
            story.append(p(s.reason, st["muted"]))
            continue
        if s.facts:
            story.append(kv_table(s.facts, st))
            story.append(Spacer(1, 4))
        story += [finding(f) for f in s.findings]
        story += [p(x, st["muted"]) for x in s.paragraphs]
        if s.table:
            story.append(data_table(s.table_head, s.table, st))
        for c in s.charts:
            story.append(KeepTogether([p(c.title, st["chart"]), chart_image(c.fig)]))

    story += [CondPageBreak(50 * mm), p(t("report.section.focus"), st["h1"])]
    if rep.focus:
        story += [p(f"• {x}", st["body"]) for x in rep.focus]
        story.append(p(t("report.p.focus_note"), st["muted"]))
    else:
        story.append(p(t("report.no_focus"), st["muted"]))

    story += [CondPageBreak(60 * mm), p(t("report.section.methodology"), st["h1"])]
    story += [p(x, st["body"]) for x in rep.methodology]
    story += [Paragraph(f"<b>{escape(rep.review_note)}</b>", st["body"]),
              p(t("report.thresholds"), st["h3"]),
              data_table([t("report.t.parameter"), t("report.t.value")], rep.thresholds, st,
                         [FRAME_W * 0.7, FRAME_W * 0.3])]
    story += [CondPageBreak(40 * mm), p(t("report.section.disclaimer"), st["h1"]),
              note_box(rep.disclaimer, st)]

    return _build(story, title=f"{t('report.title')} – {rep.person_name} – {period}",
                  subject=t("export.pdf.subject_report", period=period), author=rep.person_name,
                  header=f"{t('report.title')} · {rep.person_name} · {period}",
                  footer=t("export.pdf.footer", app=__version__, rules=rep.rules_version,
                           data=rep.data_version),
                  toc=True)


def write_report_pdf(rep: Any, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(report_pdf(rep))
    return out


# ---------------------------------------------------------------- analysis PDF

def _cell(v: object) -> str:
    from vitalmart.fmt import fmt_num

    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, pd.Timestamp | datetime):
        ts = pd.Timestamp(v)
        return ts.strftime("%Y.%m.%d.") if ts == ts.normalize() else ts.strftime("%Y.%m.%d. %H:%M")
    if isinstance(v, bool):
        return t("export.desc.yes") if v else t("export.desc.no")
    if isinstance(v, int | float):
        return fmt_num(v, 0 if float(v).is_integer() else 2)
    return str(v)


def analysis_pdf(*, title: str, description: Sequence[tuple[str, str]], table: pd.DataFrame,
                 fig: go.Figure | None = None, note: str = "", generated_at: datetime | None = None,
                 sql: str | None = None) -> bytes:
    st = _styles()
    generated = generated_at or datetime.now().replace(microsecond=0)
    story: list[Flowable] = [p(title, st["h1"]),
                             p(t("export.pdf.generated") + ": " + fmt_datetime(generated),
                               st["muted"])]
    if note.strip():
        story += [p(t("export.pdf.note"), st["h3"]), note_box(note, st, "#2563eb")]
    if fig is not None:
        story += [Spacer(1, 4), chart_image(fig, max_height=130 * mm)]
    story += [p(t("export.pdf.params"), st["h3"]), kv_table(description, st)]
    story.append(p(t("export.pdf.table"), st["h3"]))
    shown = table.head(MAX_TABLE_ROWS)
    if len(table) > MAX_TABLE_ROWS:
        story.append(p(t("export.pdf.table_cut", n=MAX_TABLE_ROWS, total=len(table)), st["muted"]))
    cols = [str(c) for c in shown.columns]
    story.append(data_table(cols, [[_cell(v) for v in r] for r in shown.itertuples(index=False)],
                            st))
    if sql:
        story += [p(t("explorer.sql"), st["h3"]),
                  Paragraph(escape(sql).replace("\n", "<br/>").replace("  ", "&nbsp;&nbsp;"),
                            ParagraphStyle("sql", parent=st["small"], textColor=MUTED,
                                           fontSize=6.5, leading=8))]
    story += [Spacer(1, 8), note_box(t("app.disclaimer"), st)]
    return _build(story, title=title, subject=t("export.pdf.subject_analysis"),
                 author="VitalMart", header=f"VitalMart · {title}",
                 footer=t("export.pdf.footer_analysis", app=__version__), toc=False)
