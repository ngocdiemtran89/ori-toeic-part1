"""Dịch danh sách câu tiếng Anh -> tiếng Việt.

Thiết kế pluggable: interface `Engine` + hai hiện thực GoogleEngine (free) và
ClaudeEngine (chất lượng cao). Cả hai đều nhận một list câu và trả về list bản
dịch CÙNG ĐỘ DÀI, giữ ánh xạ 1-1 để trình bày song ngữ.
"""
from __future__ import annotations

import os
import re
from typing import Callable, List, Optional, Protocol

from ..config import (
    CLAUDE_BATCH_SENTENCES,
    CLAUDE_MODEL,
    GOOGLE_BATCH_CHARS,
)

ProgressCb = Optional[Callable[[int, int], None]]  # (đã dịch, tổng)


class Engine(Protocol):
    def translate_all(self, sentences: List[str], progress: ProgressCb = None) -> List[str]:
        ...


# --------------------------------------------------------------------------- #
# Google Translate (miễn phí, không cần API key)
# --------------------------------------------------------------------------- #
class GoogleEngine:
    """Dùng deep-translator. Gộp nhiều câu vào 1 lần gọi bằng ký tự phân tách
    hiếm gặp để giảm số request, rồi tách lại theo đúng số câu."""

    SEP = "\n@@@\n"

    def __init__(self) -> None:
        from deep_translator import GoogleTranslator

        self._translator = GoogleTranslator(source="en", target="vi")

    def _translate_chunk(self, sentences: List[str]) -> List[str]:
        joined = self.SEP.join(sentences)
        translated = self._translator.translate(joined)
        parts = [p.strip() for p in re.split(r"@@@", translated)]
        parts = [p for p in (x.strip() for x in parts) if p != ""]
        # Nếu số phần không khớp (Google đôi khi làm hỏng separator) -> dịch từng câu
        if len(parts) != len(sentences):
            return [self._translator.translate(s) or "" for s in sentences]
        return parts

    def translate_all(self, sentences: List[str], progress: ProgressCb = None) -> List[str]:
        out: List[str] = []
        batch: List[str] = []
        chars = 0
        done = 0
        total = len(sentences)

        def flush() -> None:
            nonlocal batch, chars, done
            if not batch:
                return
            out.extend(self._translate_chunk(batch))
            done += len(batch)
            if progress:
                progress(done, total)
            batch = []
            chars = 0

        for s in sentences:
            # +len(SEP) để chừa chỗ cho separator
            if chars + len(s) + len(self.SEP) > GOOGLE_BATCH_CHARS and batch:
                flush()
            batch.append(s)
            chars += len(s) + len(self.SEP)
        flush()
        return out


# --------------------------------------------------------------------------- #
# Claude API (chất lượng cao, cần ANTHROPIC_API_KEY)
# --------------------------------------------------------------------------- #
class ClaudeEngine:
    """Gửi danh sách câu đánh số, nhận bản dịch đánh số, validate đúng số câu.

    Chỉ yêu cầu model trả TIẾNG VIỆT (không lặp tiếng Anh) để tiết kiệm token.
    """

    SYSTEM = (
        "Bạn là dịch giả chuyên nghiệp Anh->Việt cho sách xuất bản. "
        "Dịch tự nhiên, mượt mà, giữ đúng thuật ngữ và văn phong. "
        "Bạn nhận một danh sách câu tiếng Anh đánh số. "
        "Trả về ĐÚNG số dòng đó, mỗi dòng dạng '<số>. <bản dịch tiếng Việt>', "
        "không thêm giải thích, không gộp hay tách câu."
    )

    def __init__(self, model: str = CLAUDE_MODEL) -> None:
        import anthropic

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise RuntimeError(
                "Engine 'claude' cần biến môi trường ANTHROPIC_API_KEY."
            )
        self._client = anthropic.Anthropic()
        self._model = model

    def _translate_batch(self, sentences: List[str]) -> List[str]:
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            system=[{"type": "text", "text": self.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": numbered}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        parsed = self._parse_numbered(text, len(sentences))
        return parsed

    @staticmethod
    def _parse_numbered(text: str, expected: int) -> List[str]:
        result: List[str] = [""] * expected
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\.\s*(.*)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < expected:
                result[idx] = m.group(2).strip()
        return result

    def translate_all(self, sentences: List[str], progress: ProgressCb = None) -> List[str]:
        out: List[str] = []
        total = len(sentences)
        for i in range(0, total, CLAUDE_BATCH_SENTENCES):
            batch = sentences[i : i + CLAUDE_BATCH_SENTENCES]
            out.extend(self._translate_batch(batch))
            if progress:
                progress(min(i + len(batch), total), total)
        return out


def get_engine(name: str) -> Engine:
    name = (name or "google").lower()
    if name == "claude":
        return ClaudeEngine()
    if name == "google":
        return GoogleEngine()
    raise ValueError(f"Engine không hỗ trợ: {name!r} (chọn 'google' hoặc 'claude')")
