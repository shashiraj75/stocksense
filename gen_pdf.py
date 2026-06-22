"""Generate STOCKSENSE_DOCUMENTATION.pdf from the markdown file."""
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib import colors

MD_FILE = "STOCKSENSE_DOCUMENTATION.md"
PDF_FILE = "STOCKSENSE_DOCUMENTATION.pdf"

W, H = A4
styles = getSampleStyleSheet()

def mks(name, parent="Normal", **kw):
    s = ParagraphStyle(name, parent=styles[parent], **kw)
    styles.add(s)
    return s

H1 = mks("H1", "Heading1", fontSize=18, spaceAfter=8, textColor=colors.HexColor("#5b8af0"))
H2 = mks("H2", "Heading2", fontSize=13, spaceAfter=6, textColor=colors.HexColor("#7aa3f5"))
H3 = mks("H3", "Heading3", fontSize=11, spaceAfter=4, textColor=colors.HexColor("#9fbcf8"))
BODY = mks("BODY", "Normal", fontSize=9, leading=13, spaceAfter=4)
CODE = mks("CODE", "Normal", fontSize=8, leading=11, fontName="Courier",
           backColor=colors.HexColor("#1e1e2e"), textColor=colors.HexColor("#e0e0e0"),
           leftIndent=8, spaceAfter=4)
BULLET = mks("BULLET", "Normal", fontSize=9, leading=12, leftIndent=14, spaceAfter=2,
             bulletIndent=6)
QUOTE = mks("QUOTE", "Normal", fontSize=8, leading=11, leftIndent=12, spaceAfter=4,
            textColor=colors.HexColor("#aaaaaa"))

def escape(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # bold
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    # inline code
    t = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8">\1</font>', t)
    return t

def build():
    doc = SimpleDocTemplate(PDF_FILE, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    story = []
    in_code = False
    code_buf = []

    def flush_code():
        nonlocal in_code, code_buf
        if code_buf:
            for line in code_buf:
                story.append(Paragraph(escape(line) or " ", CODE))
        code_buf = []
        in_code = False

    with open(MD_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")

            if line.startswith("```"):
                if in_code:
                    flush_code()
                else:
                    in_code = True
                continue

            if in_code:
                code_buf.append(line)
                continue

            if line.startswith("# "):
                story.append(Paragraph(escape(line[2:]), H1))
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#5b8af0")))
            elif line.startswith("## "):
                story.append(Spacer(1, 4))
                story.append(Paragraph(escape(line[3:]), H2))
            elif line.startswith("### "):
                story.append(Paragraph(escape(line[4:]), H3))
            elif line.startswith("#### "):
                story.append(Paragraph(f"<b>{escape(line[5:])}</b>", BODY))
            elif line.startswith("> "):
                story.append(Paragraph(escape(line[2:]), QUOTE))
            elif re.match(r"^[-*] ", line):
                story.append(Paragraph(f"• {escape(line[2:])}", BULLET))
            elif re.match(r"^\d+\. ", line):
                m = re.match(r"^(\d+)\. (.*)", line)
                if m:
                    story.append(Paragraph(f"{m.group(1)}. {escape(m.group(2))}", BULLET))
            elif line.strip() == "" or line.strip() == "---":
                story.append(Spacer(1, 3))
            else:
                story.append(Paragraph(escape(line), BODY))

    if in_code:
        flush_code()

    doc.build(story)
    print(f"PDF written: {PDF_FILE}")

build()
