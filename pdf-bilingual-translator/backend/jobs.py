"""Quản lý job dịch chạy nền + theo dõi tiến độ."""
from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .config import OUTPUT_DIR
from .pipeline.extract import extract_blocks
from .pipeline.render_docx import render_docx
from .pipeline.segment import count_sentences, segment_blocks
from .pipeline.translate import get_engine


@dataclass
class Job:
    id: str
    filename: str
    pdf_path: str
    engine: str
    status: str = "queued"  # queued | extracting | translating | rendering | done | error
    progress: float = 0.0    # 0..1
    message: str = "Đang chờ xử lý…"
    output_path: Optional[str] = None
    error: Optional[str] = None


_jobs: Dict[str, Job] = {}
_lock = threading.Lock()


def create_job(filename: str, pdf_path: str, engine: str) -> Job:
    job = Job(id=uuid.uuid4().hex, filename=filename, pdf_path=pdf_path, engine=engine)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def _run(job: Job) -> None:
    try:
        job.status = "extracting"
        job.message = "Đang đọc PDF (và OCR nếu là bản scan)…"
        job.progress = 0.05
        blocks = extract_blocks(job.pdf_path)
        segment_blocks(blocks)
        total = count_sentences(blocks)
        if total == 0:
            raise RuntimeError("Không trích được văn bản nào từ PDF.")

        # Gom toàn bộ câu (giữ ánh xạ vị trí để trả lại đúng block)
        flat: list[str] = []
        index: list[tuple[int, int]] = []  # (block_i, sentence_i)
        for bi, b in enumerate(blocks):
            for si, s in enumerate(b.sentences):
                flat.append(s)
                index.append((bi, si))
            b.translations = [""] * len(b.sentences)

        job.status = "translating"
        job.message = f"Đang dịch {total} câu bằng engine '{job.engine}'…"

        def on_progress(done: int, tot: int) -> None:
            job.progress = 0.1 + 0.8 * (done / tot if tot else 1)
            job.message = f"Đang dịch… {done}/{tot} câu"

        engine = get_engine(job.engine)
        translations = engine.translate_all(flat, progress=on_progress)

        for (bi, si), vi in zip(index, translations):
            blocks[bi].translations[si] = vi

        job.status = "rendering"
        job.message = "Đang tạo file Word…"
        job.progress = 0.92
        title = Path(job.filename).stem
        out_path = str(OUTPUT_DIR / f"{job.id}.docx")
        render_docx(blocks, out_path, title=title)

        job.output_path = out_path
        job.status = "done"
        job.progress = 1.0
        job.message = "Hoàn tất! Tải file Word về."
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)
        job.message = f"Lỗi: {exc}"
        traceback.print_exc()


def start_job(job: Job) -> None:
    threading.Thread(target=_run, args=(job,), daemon=True).start()
