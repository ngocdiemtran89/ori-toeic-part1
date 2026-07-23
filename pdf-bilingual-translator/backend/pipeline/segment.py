"""Tách đoạn văn tiếng Anh thành từng câu (giữ ánh xạ 1-1 để dịch song ngữ)."""
from __future__ import annotations

from typing import List

import pysbd

from .models import Block

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def segment_blocks(blocks: List[Block]) -> List[Block]:
    """Điền `sentences` cho mỗi block.

    Heading coi như 1 câu duy nhất (không tách) để giữ nguyên tiêu đề.
    """
    for block in blocks:
        if block.kind == "heading":
            block.sentences = [block.text.strip()]
        else:
            sents = [s.strip() for s in _SEGMENTER.segment(block.text) if s.strip()]
            block.sentences = sents or [block.text.strip()]
    return blocks


def count_sentences(blocks: List[Block]) -> int:
    return sum(len(b.sentences) for b in blocks)
