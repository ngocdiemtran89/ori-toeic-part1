"""Trích xuất văn bản + cấu trúc từ PDF, tự động OCR khi gặp trang scan.

Điểm mấu chốt: PDF/OCR trả về văn bản NGẮT DÒNG theo chiều rộng trang. Nếu coi
mỗi dòng là một câu thì file Word sẽ "xuống dòng lung tung". Vì vậy ta REFLOW:
ghép các dòng bị ngắt lại thành đoạn hoàn chỉnh, nối từ bị gạch nối cuối dòng, và
gộp các đoạn bị chia nhỏ trước khi tách câu.
"""
from __future__ import annotations

import io
import re
from typing import List

import fitz  # PyMuPDF

from ..config import OCR_TEXT_THRESHOLD
from .models import Block

# Ký tự kết thúc câu/đoạn: nếu dòng trước KHÔNG kết bằng các ký tự này thì coi là
# bị ngắt giữa chừng -> ghép tiếp với dòng/đoạn sau.
_SENT_END = ('.', '!', '?', ':', ';', '"', '”', "'", ")", "]")


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _reflow(lines: List[str]) -> str:
    """Ghép nhiều dòng thành MỘT đoạn: nối từ bị gạch nối cuối dòng, còn lại nối
    bằng khoảng trắng."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-"):
            out = out[:-1] + ln  # information bị tách: infor- + mation
        else:
            out = out + " " + ln
    return " ".join(out.split())


def _reflow_paragraphs(text: str) -> List[str]:
    """Tách text (OCR) theo dòng trống thành từng đoạn, mỗi đoạn được reflow."""
    paras: List[str] = []
    for chunk in re.split(r"\n\s*\n", text):
        p = _reflow(chunk.splitlines())
        if p:
            paras.append(p)
    return paras


def _merge_paragraphs(blocks: List[Block]) -> List[Block]:
    """Gộp các đoạn bị chia nhỏ: nếu đoạn trước không kết thúc bằng dấu câu thì
    nối tiếp với đoạn sau (chỉ áp dụng cho paragraph, không đụng heading)."""
    merged: List[Block] = []
    for b in blocks:
        if (
            b.kind == "paragraph"
            and merged
            and merged[-1].kind == "paragraph"
            and not merged[-1].text.rstrip().endswith(_SENT_END)
        ):
            prev = merged[-1]
            prev.text = _reflow([prev.text, b.text])
        else:
            merged.append(b)
    return merged


def _ocr_page(page: "fitz.Page") -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Trang này là ảnh scan, cần cài pytesseract + Pillow + tesseract-ocr để OCR."
        ) from exc

    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="eng")


def extract_blocks(pdf_path: str) -> List[Block]:
    doc = fitz.open(pdf_path)
    blocks: List[Block] = []

    # Ước lượng cỡ chữ median để nhận diện heading
    all_sizes: List[float] = []
    for page in doc:
        data = page.get_text("dict")
        for blk in data.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        all_sizes.append(span["size"])
    median_size = _median(all_sizes) or 12.0
    heading_cut = median_size * 1.25

    for page in doc:
        raw_text = page.get_text("text").strip()

        # Trang scan: quá ít text -> OCR rồi reflow thành đoạn
        if len(raw_text) < OCR_TEXT_THRESHOLD:
            for para in _reflow_paragraphs(_ocr_page(page)):
                blocks.append(Block(kind="paragraph", text=para))
            continue

        # sort=True: đúng thứ tự đọc (quan trọng cho PDF nhiều cột)
        data = page.get_text("dict", sort=True)
        for blk in data.get("blocks", []):
            line_texts: List[str] = []
            sizes: List[float] = []
            for line in blk.get("lines", []):
                parts: List[str] = []
                for span in line.get("spans", []):
                    t = span.get("text", "")
                    parts.append(t)
                    if t.strip():
                        sizes.append(span["size"])
                line_str = "".join(parts).strip()
                if line_str:
                    line_texts.append(line_str)
            if not line_texts:
                continue

            block_text = _reflow(line_texts)  # ghép dòng trong khối thành đoạn
            if not block_text:
                continue
            avg_size = sum(sizes) / len(sizes) if sizes else median_size
            is_heading = avg_size >= heading_cut and len(block_text) < 120
            blocks.append(
                Block(kind="heading" if is_heading else "paragraph", text=block_text)
            )

    doc.close()
    return _merge_paragraphs(blocks)
