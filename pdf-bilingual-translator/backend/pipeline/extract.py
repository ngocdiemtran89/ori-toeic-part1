"""Trích xuất văn bản + cấu trúc từ PDF, tự động OCR khi gặp trang scan."""
from __future__ import annotations

import io
from typing import List

import fitz  # PyMuPDF

from ..config import OCR_TEXT_THRESHOLD
from .models import Block


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _ocr_page(page: "fitz.Page") -> str:
    """OCR một trang scan bằng Tesseract (chỉ gọi khi trang gần như không có text)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Trang này là ảnh scan, cần cài pytesseract + Pillow + tesseract-ocr để OCR."
        ) from exc

    # Render trang ở độ phân giải cao (~200 DPI) để OCR chính xác hơn
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="eng")


def extract_blocks(pdf_path: str) -> List[Block]:
    """Đọc PDF -> danh sách Block (heading/paragraph) theo thứ tự đọc.

    Nhận diện heading dựa trên cỡ chữ lớn hơn median rõ rệt.
    Trang không có text (scan) sẽ được OCR và coi toàn bộ là paragraph.
    """
    doc = fitz.open(pdf_path)
    blocks: List[Block] = []

    # Ước lượng cỡ chữ median toàn tài liệu để so sánh heading
    all_sizes: List[float] = []
    for page in doc:
        data = page.get_text("dict")
        for blk in data.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        all_sizes.append(span["size"])
    median_size = _median(all_sizes) or 12.0
    heading_cut = median_size * 1.25  # lớn hơn 25% coi là heading

    for page in doc:
        raw_text = page.get_text("text").strip()

        # Trang scan: quá ít text -> OCR
        if len(raw_text) < OCR_TEXT_THRESHOLD:
            ocr_text = _ocr_page(page).strip()
            for para in _split_paragraphs(ocr_text):
                blocks.append(Block(kind="paragraph", text=para))
            continue

        data = page.get_text("dict")
        for blk in data.get("blocks", []):
            lines = blk.get("lines", [])
            if not lines:
                continue
            texts: List[str] = []
            sizes: List[float] = []
            for line in lines:
                for span in line.get("spans", []):
                    t = span.get("text", "")
                    if t.strip():
                        texts.append(t)
                        sizes.append(span["size"])
            block_text = " ".join(texts).strip()
            block_text = " ".join(block_text.split())  # gộp khoảng trắng thừa
            if not block_text:
                continue
            avg_size = sum(sizes) / len(sizes) if sizes else median_size
            is_heading = avg_size >= heading_cut and len(block_text) < 120
            blocks.append(
                Block(kind="heading" if is_heading else "paragraph", text=block_text)
            )

    doc.close()
    return blocks


def _split_paragraphs(text: str) -> List[str]:
    """Tách text OCR thành các đoạn theo dòng trống."""
    paras: List[str] = []
    for chunk in text.split("\n\n"):
        cleaned = " ".join(chunk.split())
        if cleaned:
            paras.append(cleaned)
    return paras
