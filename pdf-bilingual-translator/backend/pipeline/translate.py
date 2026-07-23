"""Dịch khối văn bản Anh -> Việt (tối ưu chất lượng, tốc độ, chi phí, độ bền).

Tối ưu:
- (a) Dịch theo NGỮ CẢNH: mỗi đoạn là một "group" câu, dịch cùng nhau để hiểu
  mạch văn, nhưng trả về theo từng câu (giữ ánh xạ 1-1).
- (b) GLOSSARY: rút bảng thuật ngữ (Claude) rồi nhồi vào system prompt để nhất quán.
- (d) SONG SONG: các lô chạy đồng thời qua thread pool.
- (e) KHỬ TRÙNG: câu trùng lặp y hệt chỉ dịch một lần, còn lại lấy từ cache.
- (f) RETRY: mỗi lô tự thử lại + backoff khi lỗi.

Engine chỉ cần cài `translate_groups(groups)`; hàm `translate_document` lo khử
trùng, gọi engine, rồi gán kết quả về `block.translations`.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Protocol, Tuple

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
Groups = List[List[str]]  # danh sách nhóm-ngữ-cảnh; mỗi nhóm là list câu


class Engine(Protocol):
    def translate_groups(self, groups: Groups, progress: ProgressCb = None) -> Groups:
        ...


# --------------------------------------------------------------------------- #
# Tiện ích: retry + backoff, chạy song song
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


def _parallel(payloads: list, counts: List[int], worker: Callable,
              workers: int, progress: ProgressCb, total: int) -> list:
    results: list = [None] * len(payloads)
    done = 0
    if not payloads:
        return results
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
# Google Translate (miễn phí)
# --------------------------------------------------------------------------- #
class GoogleEngine:
    SEP = "\n@@@\n"

    def _translate_chunk(self, sentences: List[str]) -> List[str]:
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source="en", target="vi")
        joined = self.SEP.join(sentences)
        translated = translator.translate(joined) or ""
        parts = [p.strip() for p in re.split(r"@@@", translated)]
        parts = [p for p in parts if p != ""]
        if len(parts) != len(sentences):
            return [translator.translate(s) or "" for s in sentences]
        return parts

    def translate_groups(self, groups: Groups, progress: ProgressCb = None) -> Groups:
        if not groups:
            return []
        flat: List[str] = []
        boundaries: List[int] = []
        for g in groups:
            boundaries.append(len(g))
            flat.extend(g)

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
        results = _parallel(payloads, counts, self._translate_chunk,
                            GOOGLE_WORKERS, progress, len(flat))
        flat_tr: List[str] = []
        for r in results:
            flat_tr.extend(r)

        out: Groups = []
        pos = 0
        for ln in boundaries:
            out.append(flat_tr[pos : pos + ln])
            pos += ln
        return out


# --------------------------------------------------------------------------- #
# Claude API (ngữ cảnh + glossary + song song + retry)
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

    def _build_glossary(self, groups: Groups) -> str:
        parts: List[str] = []
        chars = 0
        for g in groups:
            text = " ".join(g)
            parts.append(text)
            chars += len(text)
            if chars >= GLOSSARY_SAMPLE_CHARS:
                break
        sample = "\n".join(parts)[:GLOSSARY_SAMPLE_CHARS]
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
            return ""

    def _system_prompt(self) -> list:
        text = self.BASE_SYSTEM
        if self._glossary:
            text += (
                "\n\nBẢNG THUẬT NGỮ BẮT BUỘC DÙNG NHẤT QUÁN "
                "(English = Tiếng Việt):\n" + self._glossary
            )
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

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

    def _translate_batch(self, batch: Groups) -> Groups:
        lines: List[str] = []
        n = 0
        counts: List[int] = []
        for gi, g in enumerate(batch):
            if gi > 0:
                lines.append("")  # ranh giới đoạn -> ngữ cảnh
            for s in g:
                n += 1
                lines.append(f"{n}. {s}")
            counts.append(len(g))
        body = "\n".join(lines)

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": body}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        flat = self._parse_numbered(text, n)

        out: Groups = []
        pos = 0
        for c in counts:
            out.append(flat[pos : pos + c])
            pos += c
        return out

    def translate_groups(self, groups: Groups, progress: ProgressCb = None) -> Groups:
        if not groups:
            return []
        self._glossary = self._build_glossary(groups)

        batches: List[Groups] = []
        cur: Groups = []
        cnt = 0
        for g in groups:
            k = len(g)
            if cur and cnt + k > CLAUDE_BATCH_SENTENCES:
                batches.append(cur)
                cur = []
                cnt = 0
            cur.append(g)
            cnt += k
        if cur:
            batches.append(cur)

        counts = [sum(len(g) for g in b) for b in batches]
        total = sum(counts)
        results = _parallel(batches, counts, self._translate_batch,
                            CLAUDE_WORKERS, progress, total)
        out: Groups = []
        for r in results:
            out.extend(r)
        return out


def get_engine(name: str) -> Engine:
    name = (name or "google").lower()
    if name == "claude":
        return ClaudeEngine()
    if name == "google":
        return GoogleEngine()
    raise ValueError(f"Engine không hỗ trợ: {name!r} (chọn 'google' hoặc 'claude')")


# --------------------------------------------------------------------------- #
# Orchestrator: khử trùng (e) + gọi engine + gán về block
# --------------------------------------------------------------------------- #
def translate_document(blocks: List[Block], engine: Engine, progress: ProgressCb = None) -> None:
    """Dịch toàn tài liệu. Mỗi block là một nhóm-ngữ-cảnh; câu trùng y hệt
    (sau chuẩn hoá khoảng trắng) chỉ dịch một lần."""
    for b in blocks:
        b.translations = [""] * len(b.sentences)

    seen: Dict[str, Tuple[int, int]] = {}
    dups: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    group_texts: Groups = []
    group_positions: List[List[Tuple[int, int]]] = []

    for bi, b in enumerate(blocks):
        texts: List[str] = []
        positions: List[Tuple[int, int]] = []
        for si, s in enumerate(b.sentences):
            key = " ".join(s.split())
            if key in seen:
                dups[key].append((bi, si))
            else:
                seen[key] = (bi, si)
                texts.append(s)
                positions.append((bi, si))
        if texts:  # bỏ qua block toàn câu trùng
            group_texts.append(texts)
            group_positions.append(positions)

    results = engine.translate_groups(group_texts, progress=progress)

    cache: Dict[str, str] = {}
    for texts, positions, tr in zip(group_texts, group_positions, results):
        for s, (bi, si), vi in zip(texts, positions, tr):
            blocks[bi].translations[si] = vi
            cache[" ".join(s.split())] = vi

    # Điền các câu trùng từ cache (không tốn thêm lần dịch nào)
    for key, plist in dups.items():
        vi = cache.get(key, "")
        for bi, si in plist:
            blocks[bi].translations[si] = vi
