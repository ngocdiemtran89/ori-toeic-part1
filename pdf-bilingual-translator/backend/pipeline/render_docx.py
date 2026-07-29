"""Sinh file Word (.docx) song ngữ, trình bày như sách xuất bản chuyên nghiệp."""
from __future__ import annotations

from typing import List

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .models import Block

BODY_FONT = "Georgia"
EN_SIZE = Pt(11)
VI_SIZE = Pt(11)
VI_COLOR = RGBColor(0x55, 0x55, 0x55)  # xám nhạt để phân biệt bản dịch


def _add_page_number_footer(section) -> None:
    """Thêm số trang vào footer (canh giữa) bằng field PAGE."""
    footer = section.footer
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = para.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def _setup_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = EN_SIZE
    # Áp font cho cả ký tự Đông Á/Unicode (tiếng Việt)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), BODY_FONT)
    rfonts.set(qn("w:hAnsi"), BODY_FONT)
    rfonts.set(qn("w:cs"), BODY_FONT)


def _add_title_page(doc: Document, title: str) -> None:
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(title)
    run.bold = True
    run.font.size = Pt(26)
    run.font.name = BODY_FONT

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    srun = sub.add_run("Bản dịch song ngữ Anh – Việt")
    srun.italic = True
    srun.font.size = Pt(14)
    srun.font.color.rgb = VI_COLOR

    doc.add_page_break()


def _add_toc(doc: Document, blocks: List[Block]) -> None:
    headings = [b for b in blocks if b.kind == "heading"]
    if not headings:
        return
    h = doc.add_paragraph()
    hr = h.add_run("Mục lục")
    hr.bold = True
    hr.font.size = Pt(18)
    for b in headings:
        line = doc.add_paragraph(style="List Bullet")
        line.add_run(b.text)
    doc.add_page_break()


def _add_bilingual_paragraph(doc: Document, block: Block, layout: str) -> None:
    """Trình bày song ngữ cho một đoạn văn.

    layout="sentence": xen kẽ 1 câu Anh / 1 câu Việt.
    layout="paragraph": cả đoạn Anh rồi cả đoạn Việt.
    """
    if layout == "paragraph":
        en = " ".join(block.sentences)
        vi = " ".join(t for t in block.translations if t)

        p_en = doc.add_paragraph()
        p_en.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_en.paragraph_format.space_after = Pt(2)
        p_en.add_run(en).font.size = EN_SIZE

        p_vi = doc.add_paragraph()
        p_vi.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_vi.paragraph_format.space_after = Pt(10)
        r_vi = p_vi.add_run(vi)
        r_vi.italic = True
        r_vi.font.size = VI_SIZE
        r_vi.font.color.rgb = VI_COLOR
        return

    for i in range(len(block.sentences)):
        en = block.sentences[i]
        vi = block.translations[i] if i < len(block.translations) else ""

        p_en = doc.add_paragraph()
        p_en.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_en.paragraph_format.space_after = Pt(0)
        r_en = p_en.add_run(en)
        r_en.font.size = EN_SIZE

        p_vi = doc.add_paragraph()
        p_vi.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_vi.paragraph_format.space_after = Pt(8)
        r_vi = p_vi.add_run(vi)
        r_vi.italic = True
        r_vi.font.size = VI_SIZE
        r_vi.font.color.rgb = VI_COLOR


def _add_bilingual_heading(doc: Document, block: Block) -> None:
    en = block.sentences[0] if block.sentences else block.text
    vi = block.translations[0] if block.translations else ""
    h = doc.add_heading(level=1)
    hr = h.add_run(en)
    hr.font.name = BODY_FONT
    if vi:
        p_vi = doc.add_paragraph()
        r_vi = p_vi.add_run(vi)
        r_vi.italic = True
        r_vi.bold = True
        r_vi.font.color.rgb = VI_COLOR


def render_docx(
    blocks: List[Block],
    output_path: str,
    title: str = "Tài liệu",
    layout: str = "sentence",
) -> str:
    doc = Document()
    _setup_styles(doc)

    section = doc.sections[0]
    section.top_margin = Pt(72)
    section.bottom_margin = Pt(72)
    section.left_margin = Pt(72)
    section.right_margin = Pt(72)
    _add_page_number_footer(section)

    _add_title_page(doc, title)
    _add_toc(doc, blocks)

    for block in blocks:
        if block.kind == "heading":
            _add_bilingual_heading(doc, block)
        else:
            _add_bilingual_paragraph(doc, block, layout)

    doc.save(output_path)
    return output_path
