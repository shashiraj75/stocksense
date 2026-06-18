"""Generate STOCKSENSE_DOCUMENTATION.pdf from the markdown file."""
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

MD_FILE = "STOCKSENSE_DOCUMENTATION.md"
PDF_FILE = "STOCKSENSE_DOCUMENTATION.pdf"

# ── Colour palette ────────────────────────────────────────────────────────────
PRIMARY   = colors.HexColor("#1e3a5f")   # dark navy
ACCENT    = colors.HexColor("#2563eb")   # blue
GREEN     = colors.HexColor("#16a34a")
RED       = colors.HexColor("#dc2626")
LIGHT_BG  = colors.HexColor("#f0f4ff")
BORDER    = colors.HexColor("#cbd5e1")
HEADER_BG = colors.HexColor("#1e3a5f")
ALT_ROW   = colors.HexColor("#f8fafc")

# ── Page setup ────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGIN = 20 * mm

def build_styles():
    base = getSampleStyleSheet()
    s = {}

    s["cover_title"] = ParagraphStyle("cover_title",
        fontName="Helvetica-Bold", fontSize=32, textColor=colors.white,
        leading=40, alignment=TA_CENTER, spaceAfter=8)

    s["cover_sub"] = ParagraphStyle("cover_sub",
        fontName="Helvetica", fontSize=14, textColor=colors.HexColor("#bfdbfe"),
        leading=20, alignment=TA_CENTER, spaceAfter=6)

    s["cover_date"] = ParagraphStyle("cover_date",
        fontName="Helvetica", fontSize=11, textColor=colors.HexColor("#93c5fd"),
        alignment=TA_CENTER)

    s["h1"] = ParagraphStyle("h1",
        fontName="Helvetica-Bold", fontSize=18, textColor=PRIMARY,
        leading=24, spaceBefore=18, spaceAfter=8,
        borderPad=4)

    s["h2"] = ParagraphStyle("h2",
        fontName="Helvetica-Bold", fontSize=13, textColor=ACCENT,
        leading=18, spaceBefore=14, spaceAfter=6)

    s["h3"] = ParagraphStyle("h3",
        fontName="Helvetica-Bold", fontSize=11, textColor=PRIMARY,
        leading=15, spaceBefore=10, spaceAfter=4)

    s["h4"] = ParagraphStyle("h4",
        fontName="Helvetica-BoldOblique", fontSize=10, textColor=colors.HexColor("#374151"),
        leading=14, spaceBefore=8, spaceAfter=3)

    s["body"] = ParagraphStyle("body",
        fontName="Helvetica", fontSize=9.5, textColor=colors.HexColor("#1f2937"),
        leading=14, spaceAfter=4)

    s["bullet"] = ParagraphStyle("bullet",
        fontName="Helvetica", fontSize=9.5, textColor=colors.HexColor("#1f2937"),
        leading=13, leftIndent=14, firstLineIndent=-10, spaceAfter=2)

    s["code"] = ParagraphStyle("code",
        fontName="Courier", fontSize=8.5, textColor=colors.HexColor("#1e293b"),
        leading=12, leftIndent=10, spaceAfter=2,
        backColor=colors.HexColor("#f1f5f9"), borderPad=4)

    s["toc_entry"] = ParagraphStyle("toc_entry",
        fontName="Helvetica", fontSize=10, textColor=PRIMARY,
        leading=16, leftIndent=12, spaceAfter=2)

    s["toc_title"] = ParagraphStyle("toc_title",
        fontName="Helvetica-Bold", fontSize=14, textColor=PRIMARY,
        leading=20, spaceAfter=10, alignment=TA_CENTER)

    s["footer"] = ParagraphStyle("footer",
        fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#9ca3af"),
        alignment=TA_CENTER)

    return s


def make_table(headers, rows, col_widths=None):
    """Build a styled reportlab Table from headers + rows."""
    data = [headers] + rows
    if col_widths is None:
        n = len(headers)
        avail = PAGE_W - 2 * MARGIN
        col_widths = [avail / n] * n

    style = TableStyle([
        # Header row
        ("BACKGROUND",  (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 8.5),
        ("TOPPADDING",  (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",(0, 0), (-1, -1), 6),
        # Body rows
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 1), (-1, -1), 8.5),
        ("TOPPADDING",  (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("TEXTCOLOR",   (0, 1), (-1, -1), colors.HexColor("#1f2937")),
        # Alternating rows
        *[("BACKGROUND", (0, i), (-1, i), ALT_ROW) for i in range(2, len(data), 2)],
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ALT_ROW]),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
    ])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(style)
    return t


def cover_page(styles):
    """Return flowables for a professional cover page."""
    elements = []

    # Top colour band via a coloured table cell
    banner_data = [[""]]
    banner = Table(banner_data, colWidths=[PAGE_W - 2 * MARGIN], rowHeights=[8])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("LINEABOVE",  (0, 0), (-1, -1), 0, colors.transparent),
    ]))
    elements.append(Spacer(1, 10 * mm))
    elements.append(banner)
    elements.append(Spacer(1, 20 * mm))

    # Title block
    title_data = [[Paragraph("StockSense", styles["cover_title"]),]]
    title_table = Table(title_data, colWidths=[PAGE_W - 2 * MARGIN], rowHeights=[50])
    title_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    elements.append(title_table)

    subtitle_data = [[Paragraph("Complete Product &amp; Technical Documentation", styles["cover_sub"])]]
    sub_table = Table(subtitle_data, colWidths=[PAGE_W - 2 * MARGIN], rowHeights=[36])
    sub_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(sub_table)
    elements.append(Spacer(1, 12 * mm))

    # Tagline
    elements.append(Paragraph(
        "AI-Powered Stock Prediction &amp; Portfolio Intelligence Platform",
        ParagraphStyle("tag", fontName="Helvetica-BoldOblique", fontSize=12,
                       textColor=PRIMARY, alignment=TA_CENTER, spaceAfter=4)))
    elements.append(Paragraph(
        "Indian &amp; US Equity Markets &nbsp;|&nbsp; Nifty 100 &nbsp;|&nbsp; S&amp;P 500 &nbsp;|&nbsp; Crypto",
        ParagraphStyle("markets", fontName="Helvetica", fontSize=10,
                       textColor=colors.HexColor("#6b7280"), alignment=TA_CENTER)))
    elements.append(Spacer(1, 16 * mm))

    # Info box
    info_rows = [
        ["Version", "1.0"],
        ["Last Updated", "June 2026"],
        ["Classification", "Confidential — Investor Use Only"],
        ["Coverage", "22 sections · All signals · Every factor"],
    ]
    info_table = Table(info_rows, colWidths=[55 * mm, 95 * mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), LIGHT_BG),
        ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",      (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR",     (0, 0), (-1, -1), PRIMARY),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    elements.append(info_table)
    elements.append(PageBreak())
    return elements


def parse_markdown(md_text, styles):
    """Convert markdown to reportlab flowables."""
    elements = []
    lines = md_text.split("\n")
    i = 0

    # Track table parsing state
    in_table = False
    table_headers = []
    table_rows = []

    def flush_table():
        nonlocal in_table, table_headers, table_rows
        if table_headers and table_rows:
            avail = PAGE_W - 2 * MARGIN
            n = len(table_headers)
            col_widths = [avail / n] * n
            t = make_table(table_headers, table_rows, col_widths)
            elements.append(Spacer(1, 3))
            elements.append(t)
            elements.append(Spacer(1, 6))
        in_table = False
        table_headers = []
        table_rows = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip horizontal rules and HTML comments
        if stripped.startswith("---") or stripped.startswith("<!--"):
            i += 1
            continue

        # Code blocks
        if stripped.startswith("```"):
            if in_table:
                flush_table()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_text = "\n".join(code_lines)
            # Escape XML chars
            code_text = code_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            code_text = code_text.replace("\n", "<br/>")
            elements.append(Spacer(1, 3))
            elements.append(Paragraph(code_text, styles["code"]))
            elements.append(Spacer(1, 6))
            i += 1
            continue

        # Markdown tables
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Separator row
            if all(re.match(r"[-:]+", c) for c in cells if c):
                i += 1
                continue
            if not in_table:
                in_table = True
                table_headers = cells
            else:
                table_rows.append(cells)
            i += 1
            continue
        else:
            if in_table:
                flush_table()

        # Blank line
        if not stripped:
            elements.append(Spacer(1, 4))
            i += 1
            continue

        # Headings
        if stripped.startswith("#### "):
            elements.append(Paragraph(escape(stripped[5:]), styles["h4"]))
        elif stripped.startswith("### "):
            elements.append(Paragraph(escape(stripped[4:]), styles["h3"]))
        elif stripped.startswith("## "):
            elements.append(Paragraph(escape(stripped[3:]), styles["h2"]))
        elif stripped.startswith("# "):
            text = stripped[2:]
            # Skip the main title (already on cover) and TOC heading
            if "Table of Contents" not in text and "StockSense" not in text:
                elements.append(Spacer(1, 4))
                elements.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=4))
                elements.append(Paragraph(escape(text), styles["h1"]))
        # Blockquote
        elif stripped.startswith("> "):
            text = escape(stripped[2:])
            bq_data = [[Paragraph(text, ParagraphStyle("bq",
                fontName="Helvetica-Oblique", fontSize=9.5,
                textColor=colors.HexColor("#374151"), leading=13))]]
            bq_table = Table(bq_data, colWidths=[PAGE_W - 2 * MARGIN - 14])
            bq_table.setStyle(TableStyle([
                ("LEFTPADDING",   (0, 0), (-1, -1), 10),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND",    (0, 0), (-1, -1), LIGHT_BG),
                ("LINEBEFORE",    (0, 0), (0, -1), 3, ACCENT),
            ]))
            elements.append(bq_table)
            elements.append(Spacer(1, 4))
        # Bullet
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = escape(stripped[2:])
            elements.append(Paragraph(f"• {text}", styles["bullet"]))
        # Numbered list
        elif re.match(r"^\d+\.", stripped):
            text = escape(re.sub(r"^\d+\.\s*", "", stripped))
            num = re.match(r"^(\d+)\.", stripped).group(1)
            elements.append(Paragraph(f"{num}. {text}", styles["bullet"]))
        # Normal paragraph
        else:
            text = escape(stripped)
            elements.append(Paragraph(text, styles["body"]))

        i += 1

    if in_table:
        flush_table()

    return elements


def escape(text):
    """Escape XML special chars and convert basic markdown inline formatting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold+italic ***text***
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    # Bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic *text*
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Inline code `text`
    text = re.sub(r"`([^`]+)`",
        r'<font name="Courier" color="#1e293b" backColor="#f1f5f9"> \1 </font>', text)
    # Strip markdown links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def add_page_numbers(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#9ca3af"))
    # Footer line
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    # Left: product name
    canvas.drawString(MARGIN, 10 * mm, "StockSense — Confidential")
    # Centre: page number
    canvas.drawCentredString(PAGE_W / 2, 10 * mm, f"Page {doc.page}")
    # Right: date
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, "June 2026")
    canvas.restoreState()


def main():
    with open(MD_FILE, "r", encoding="utf-8") as f:
        md_text = f.read()

    styles = build_styles()

    doc = SimpleDocTemplate(
        PDF_FILE,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=18 * mm, bottomMargin=22 * mm,
        title="StockSense — Complete Product & Technical Documentation",
        author="StockSense",
        subject="AI-Powered Stock Prediction Platform",
    )

    story = []

    # Cover page
    story.extend(cover_page(styles))

    # TOC placeholder (manual)
    story.append(Paragraph("Table of Contents", styles["toc_title"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=6))
    toc_items = [
        "1.  Product Overview",
        "2.  Architecture Overview",
        "3.  Data Sources",
        "4.  Core Prediction Engine",
        "5.  Technical Analysis Module",
        "6.  Fundamental Scoring Module",
        "7.  Sentiment Analysis Module",
        "8.  Global Macro Context Module",
        "9.  Quality Factors Module",
        "10. Risk Penalty Framework",
        "11. Confidence Engine",
        "12. Target Price & Trade Levels",
        "13. Daily Picks Engine",
        "14. Backtesting & Validation Engine",
        "15. Crypto Prediction Module",
        "16. Screener & Universe Management",
        "17. API Reference",
        "18. Frontend Pages & Components",
        "19. Infrastructure & Deployment",
        "20. Automation Workflows",
        "21. Factor Weights by Horizon",
        "22. Key Design Principles",
    ]
    for item in toc_items:
        story.append(Paragraph(item, styles["toc_entry"]))
    story.append(PageBreak())

    # Main content from markdown
    story.extend(parse_markdown(md_text, styles))

    doc.build(story, onFirstPage=add_page_numbers, onLaterPages=add_page_numbers)
    print(f"PDF created: {PDF_FILE}")


if __name__ == "__main__":
    main()
