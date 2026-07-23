"""Kiểu dữ liệu dùng chung giữa các bước trong pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal

BlockKind = Literal["heading", "paragraph"]


@dataclass
class Block:
    """Một khối văn bản đã trích từ PDF (đầu mục hoặc đoạn văn)."""
    kind: BlockKind
    text: str
    # Danh sách câu tiếng Anh sau khi tách (segment)
    sentences: List[str] = field(default_factory=list)
    # Bản dịch tiếng Việt tương ứng theo từng câu (cùng số phần tử với sentences)
    translations: List[str] = field(default_factory=list)
