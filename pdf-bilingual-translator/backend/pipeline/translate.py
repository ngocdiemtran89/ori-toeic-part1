"""Dịch khối văn bản Anh -> Việt (tối ưu chất lượng, tốc độ, độ bền).

Bốn tối ưu:
- (a) Dịch theo NGỮ CẢNH: gửi cả cụm câu/đoạn để model hiểu mạch văn, nhưng vẫn
  trả về theo từng câu để giữ ánh xạ 1-1.
- (b) GLOSSARY: rút bảng thuật ngữ chính (Claude) rồi nhồi vào system prompt để
  dịch nhất quán xuyên suốt sách.
- (d) SONG SONG: các lô dịch chạy đồng thời bằng thread pool.
- (f) RETRY: mỗi lô có thử lại + backoff khi lỗi mạng/tạm thời.

Cả hai engine đều điền `block.translations` (cùng độ dài `block.sentences`).
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional, Protocol

from ..config import (
    CLAUDE_BATCH_SENTENCES,
    CLAUDE_MODEL,
    CLAUDE_WORKERS,
    GLOSSARY_SAMPLE_CHARS,
    GOOGLE_BATCH_CHARS,
    GOOGLE_WORKERS,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
)
from .models import Block

ProgressCb = Optional[Callable[[int, int], None]]  # (đã dịch, tổng)


class Engine(Protocol):
    def translate_blocks(self, blocks: List[Block], progress: ProgressCb = None) -> None:
        ...


# --------------------------------------------------------------------------- #
# Tiện ích dùng chung: retry + backoff, chạy lô song song
# --------------------------------------------------------------------------- #
def _retry(fn: Callable, *args):
    delay = RETRY_BASE_DELAY
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _parallel(
    payloads: list,
    counts: List[int],
    worker: Callable,
    workers: int,
    progress: ProgressCb,
    total: int,
) -> list:
    """Chạy `worker(payload)` cho từng payload song song, giữ đúng thứ tự kết quả.

    `counts[i]` = số câu của payload i, dùng để cập nhật tiến độ.
    """
    results: list = [None] * len(payloads)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        fut_to_i = {ex.submit(_retry, worker, p): i for i, p in enumerate(payloads)}
        for fut in as_completed(fut_to_i):
            i = fut_to_i[fut]
            results[i] = fut.result()
            done += counts[i]
            if progress:
                progress(done, total)
    return results


# --------------------------------------------------------------------------- #
# Google Translate (miễn phí, không cần API key)
# --------------------------------------------------------------------------- #
class GoogleEngine:
    """deep-translator. Gộp câu theo lô ký tự (đã cho cả đoạn nên có ngữ cảnh),
    chạy song song, có retry. Google không dùng glossary."""

    SEP = "\n@@@\n"

    def _translate_chunk(self, sentences: List[str]) -> List[str]:
        # Tạo translator mới mỗi lô để an toàn khi chạy đa luồng
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source="en", target="vi")
        joined = self.SEP.join(sentences)
        translated = translator.translate(joined) or ""
        parts = [p.strip() for p in re.split(r"@@@", translated)]
        parts = [p for p in parts if p != ""]
        if len(parts) != len(sentences):
            # Google làm hỏng separator -> dịch từng câu để giữ đúng số lượng
            return [translator.translate(s) or "" for s in sentences]
        return parts

    def translate_blocks(self, blocks: List[Block], progress: ProgressCb = None) -> None:
        flat: List[str] = []
        index: List[tuple[int, int]] = []
        for bi, b in enumerate(blocks):
            b.translations = [""] * len(b.sentences)
            for si, s in enumerate(b.sentences):
                flat.append(s)
                index.append((bi, si))

        # Chia lô theo số ký tự
        payloads: List[List[str]] = []
        cur: List[str] = []
        chars = 0
        for s in flat:
            if cur and chars + len(s) + len(self.SEP) > GOOGLE_BATCH_CHARS:
                payloads.append(cur)
                cur = []
                chars = 0
            cur.append(s)
            chars += len(s) + len(self.SEP)
        if cur:
            payloads.append(cur)

        counts = [len(p) for p in payloads]
        results = _parallel(
            payloads, counts, self._translate_chunk, GOOGLE_WORKERS, progress, len(flat)
        )

        flat_tr: List[str] = []
        for r in results:
            flat_tr.extend(r)
        for (bi, si), vi in zip(index, flat_tr):
            blocks[bi].translations[si] = vi


# --------------------------------------------------------------------------- #
# Claude API (chất lượng cao): ngữ cảnh + glossary + song song + retry
# --------------------------------------------------------------------------- #
class ClaudeEngine:
    BASE_SYSTEM = (
        "Bạn là dịch giả chuyên nghiệp Anh->Việt cho sách xuất bản. "
        "Dịch tự nhiên, mượt mà, đúng văn phong và mạch văn. "
        "Người dùng gửi các câu tiếng Anh ĐÃ ĐÁNH SỐ; dòng trống ngăn cách các "
        "đoạn để bạn hiểu ngữ cảnh. Hãy dịch dựa trên toàn bộ ngữ cảnh nhưng "
        "TRẢ VỀ ĐÚNG số dòng đó, mỗi dòng dạng '<số>. <bản dịch tiếng Việt>', "
        "không thêm giải thích, không gộp/tách câu, giữ nguyên thứ tự."
    )

    def __init__(self, model: str = CLAUDE_MODEL) -> None:
        import os

        import anthropic

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise RuntimeError("Engine 'claude' cần biến môi trường ANTHROPIC_API_KEY.")
        self._client = anthropic.Anthropic()
        self._model = model
        self._glossary = ""

    # -- (b) Glossary ------------------------------------------------------- #
    def _build_glossary(self, blocks: List[Block]) -> str:
        sample_parts: List[str] = []
        chars = 0
        for b in blocks:
            if b.kind != "paragraph":
                continue
            sample_parts.append(b.text)
            chars += len(b.text)
            if chars >= GLOSSARY_SAMPLE_CHARS:
                break
        sample = "\n".join(sample_parts)[:GLOSSARY_SAMPLE_CHARS]
        if not sample.strip():
            return ""

        def _call() -> str:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                system=(
                    "Bạn là biên tập viên thuật ngữ. Từ đoạn văn tiếng Anh dưới đây, "
                    "liệt kê tối đa 30 thuật ngữ/danh từ riêng lặp lại quan trọng và "
                    "bản dịch tiếng Việt thống nhất. Mỗi dòng dạng 'English = Tiếng Việt'. "
                    "Nếu không có thuật ngữ đặc thù, trả về dòng trống."
                ),
                messages=[{"role": "user", "content": sample}],
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        try:
            return _retry(_call) or ""
        except Exception:  # noqa: BLE001
            return ""  # glossary chỉ là bonus; lỗi thì bỏ qua

    def _system_prompt(self) -> list:
        text = self.BASE_SYSTEM
        if self._glossary:
            text += (
                "\n\nBẢNG THUẬT NGỮ BẮT BUỘC DÙNG NHẤT QUÁN "
                "(English = Tiếng Việt):\n" + self._glossary
            )
        # cache_control: glossary + hướng dẫn ổn định -> cache để rẻ & nhất quán
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    # -- (a) Dịch theo ngữ cảnh + trả về theo câu ---------------------------- #
    @staticmethod
    def _render_batch(blocks: List[Block]) -> tuple[str, int]:
        lines: List[str] = []
        n = 0
        for bi, b in enumerate(blocks):
            if bi > 0:
                lines.append("")  # dòng trống = ranh giới đoạn (ngữ cảnh)
            for s in b.sentences:
                n += 1
                lines.append(f"{n}. {s}")
        return "\n".join(lines), n

    @staticmethod
    def _parse_numbered(text: str, expected: int) -> List[str]:
        result = [""] * expected
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\.\s*(.*)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < expected:
                result[idx] = m.group(2).strip()
        return result

    def _translate_batch(self, blocks: List[Block]) -> List[str]:
        body, n = self._render_batch(blocks)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": body}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return self._parse_numbered(text, n)

    def translate_blocks(self, blocks: List[Block], progress: ProgressCb = None) -> None:
        for b in blocks:
            b.translations = [""] * len(b.sentences)

        self._glossary = self._build_glossary(blocks)

        # Chia lô theo số câu (gom các block liền kề -> giữ ngữ cảnh đoạn)
        payloads: List[List[Block]] = []
        cur: List[Block] = []
        cnt = 0
        for b in blocks:
            k = len(b.sentences)
            if cur and cnt + k > CLAUDE_BATCH_SENTENCES:
                payloads.append(cur)
                cur = []
                cnt = 0
            cur.append(b)
            cnt += k
        if cur:
            payloads.append(cur)

        counts = [sum(len(b.sentences) for b in p) for p in payloads]
        total = sum(counts)
        results = _parallel(
            payloads, counts, self._translate_batch, CLAUDE_WORKERS, progress, total
        )

        for batch, res in zip(payloads, results):
            pos = 0
            for b in batch:
                b.translations = res[pos : pos + len(b.sentences)]
                pos += len(b.sentences)


def get_engine(name: str) -> Engine:
    name = (name or "google").lower()
    if name == "claude":
        return ClaudeEngine()
    if name == "google":
        return GoogleEngine()
    raise ValueError(f"Engine không hỗ trợ: {name!r} (chọn 'google' hoặc 'claude')")
