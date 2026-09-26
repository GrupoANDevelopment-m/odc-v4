"""Generate PDF audit report from markdown using reportlab.

Reads /workspace/odc-v4/docs/AUDIT_2026-09-25.md and produces
/workspace/odc-v4/docs/AUDIT_2026-09-25.pdf.

The audit document is ~21KB markdown. We parse it into sections and
render them in a clean reportlab layout (A4, monospace for code blocks,
sans-serif body).
"""
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Preformatted,
    Table, TableStyle, PageBreak,
)


SRC = Path("/workspace/odc-v4/docs/AUDIT_2026-09-25.md")
OUT = Path("/workspace/odc-v4/docs/AUDIT_2026-09-25.pdf")


def build_styles():
    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"],
                             fontSize=20, spaceAfter=12, spaceBefore=20,
                             textColor=colors.HexColor("#1a1a2e"),
                             leading=24),
        "h2": ParagraphStyle("h2", parent=base["Heading2"],
                             fontSize=14, spaceAfter=8, spaceBefore=14,
                             textColor=colors.HexColor("#16213e"),
                             leading=18),
        "h3": ParagraphStyle("h3", parent=base["Heading3"],
                             fontSize=11, spaceAfter=6, spaceBefore=10,
                             textColor=colors.HexColor("#0f3460"),
                             leading=14),
        "body": ParagraphStyle("body", parent=base["Normal"],
                                fontSize=9.5, leading=12.5,
                                spaceAfter=4, alignment=TA_LEFT),
        "code": ParagraphStyle("code", parent=base["Code"],
                                fontSize=8, leading=10,
                                leftIndent=12, rightIndent=12,
                                spaceAfter=4, spaceBefore=4,
                                backColor=colors.HexColor("#f4f4f4"),
                                textColor=colors.HexColor("#222"),
                                borderColor=colors.HexColor("#ddd"),
                                borderWidth=0.5, borderPadding=4),
        "caption": ParagraphStyle("caption", parent=base["Italic"],
                                  fontSize=8, leading=10,
                                  textColor=colors.HexColor("#666")),
    }
    return styles


def md_to_flowables(md_text, styles):
    """Convert markdown to reportlab flowables."""
    flowables = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Skip empty lines but preserve spacing
        if not line:
            flowables.append(Spacer(1, 4))
            i += 1
            continue

        # Frontmatter (---\n...\n---) — skip
        if line.strip() == "---":
            j = i + 1
            while j < len(lines) and lines[j].strip() != "---":
                j += 1
            i = j + 1
            continue

        # H1 (# title)
        if line.startswith("# "):
            title = line[2:].strip()
            flowables.append(Paragraph(escape_md(title), styles["h1"]))
            i += 1
            continue

        # H2 (##)
        if line.startswith("## "):
            heading = line[3:].strip()
            flowables.append(Paragraph(escape_md(heading), styles["h2"]))
            i += 1
            continue

        # H3 (###)
        if line.startswith("### "):
            heading = line[4:].strip()
            flowables.append(Paragraph(escape_md(heading), styles["h3"]))
            i += 1
            continue

        # H4 (####)
        if line.startswith("#### "):
            heading = line[5:].strip()
            flowables.append(Paragraph(
                f"<b>{escape_md(heading)}</b>", styles["body"]
            ))
            i += 1
            continue

        # Code block (``` fenced)
        if line.startswith("```"):
            j = i + 1
            code_lines = []
            while j < len(lines) and not lines[j].startswith("```"):
                code_lines.append(lines[j])
                j += 1
            code_text = "\n".join(code_lines)
            try:
                flowables.append(Preformatted(
                    code_text,
                    ParagraphStyle("codeblock",
                                    parent=styles["code"],
                                    leftIndent=4, rightIndent=4),
                ))
            except Exception:
                flowables.append(Paragraph(
                    f"<font face='Courier'>{escape_md(code_text)}</font>",
                    styles["code"],
                ))
            i = j + 1
            continue

        # Table (markdown table — simple parsing)
        if line.startswith("|") and i + 1 < len(lines) and lines[i + 1].startswith("|") and "---" in lines[i + 1]:
            table_lines = [line]
            j = i + 1
            while j < len(lines) and lines[j].startswith("|"):
                table_lines.append(lines[j])
                j += 1
            flowable = make_table(table_lines, styles)
            if flowable:
                flowables.append(flowable)
                flowables.append(Spacer(1, 4))
            i = j
            continue

        # Horizontal rule (---)
        if line.strip() == "---":
            flowables.append(Spacer(1, 8))
            i += 1
            continue

        # Bullet list
        if line.startswith("- "):
            bullet_lines = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  ") and lines[i].strip()):
                bullet_lines.append(lines[i])
                i += 1
            for bl in bullet_lines:
                text = bl.lstrip("- ").strip()
                flowables.append(Paragraph(
                    f"• {escape_md(text)}", styles["body"]
                ))
            continue

        # Regular paragraph
        para_lines = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "- ", "```", "|", "---")):
            para_lines.append(lines[i])
            i += 1
        para_text = " ".join(para_lines)
        flowables.append(Paragraph(escape_md(para_text), styles["body"]))

    return flowables


def make_table(table_lines, styles):
    """Convert markdown table to reportlab Table."""
    rows = []
    for tl in table_lines:
        if "---" in tl and "|" in tl and ":" not in tl and "---" in tl.replace("|", ""):
            continue  # separator row
        cells = [c.strip() for c in tl.strip().strip("|").split("|")]
        rows.append([Paragraph(escape_md(c), styles["body"]) for c in cells])
    if not rows or len(rows) < 1:
        return None
    # Equal-width columns
    n_cols = max(len(r) for r in rows)
    col_width = (A4[0] - 4 * cm) / n_cols
    table = Table(rows, colWidths=[col_width] * n_cols, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#aaa")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def escape_md(text):
    """Minimal markdown escape for reportlab paragraph XML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Restore inline code, bold, italic
    text = re_inline(text)
    return text


import re
def re_inline(text):
    # Bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic *text*
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # Inline code `text`
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    # Links [text](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<u>\1</u>", text)
    return text


def main():
    md = SRC.read_text(encoding="utf-8")
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="ODC v4 Audit Report",
        author="ODC v4 Audit",
    )
    flowables = md_to_flowables(md, styles)
    # Title page header
    title_block = [
        Spacer(1, 80),
        Paragraph(
            "<font size='22' color='#1a1a2e'><b>ODC v4</b></font>",
            ParagraphStyle("title", fontSize=22, alignment=TA_LEFT,
                            spaceAfter=8),
        ),
        Paragraph(
            "<font size='16' color='#16213e'>Audit Report</font>",
            ParagraphStyle("subtitle", fontSize=16, alignment=TA_LEFT,
                            spaceAfter=24),
        ),
        Paragraph(
            "<font size='10' color='#666'>"
            "Security, reliability, and operability audit<br/>"
            "Date: 2026-09-25<br/>"
            "Classification: Production readiness review</font>",
            ParagraphStyle("meta", fontSize=10, alignment=TA_LEFT,
                            leading=14),
        ),
        PageBreak(),
    ]
    doc.build(title_block + flowables)
    print(f"OK: {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
