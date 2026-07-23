"""FastAPI app: upload PDF, theo dõi tiến độ, tải file Word song ngữ."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import jobs
from .config import (
    DEFAULT_ENGINE,
    MAX_UPLOAD_MB,
    UPLOAD_DIR,
    ensure_dirs,
)

ensure_dirs()

app = FastAPI(title="PDF Bilingual Translator")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), engine: str = Form(DEFAULT_ENGINE)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Vui lòng tải lên file .pdf")

    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File vượt quá {MAX_UPLOAD_MB}MB")

    job = jobs.create_job(file.filename, "", engine)
    pdf_path = UPLOAD_DIR / f"{job.id}.pdf"
    pdf_path.write_bytes(data)
    job.pdf_path = str(pdf_path)

    jobs.start_job(job)
    return {"job_id": job.id}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    return JSONResponse(
        {
            "status": job.status,
            "progress": round(job.progress, 3),
            "message": job.message,
            "error": job.error,
            "ready": job.status == "done",
        }
    )


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    job = jobs.get_job(job_id)
    if not job or job.status != "done" or not job.output_path:
        raise HTTPException(404, "File chưa sẵn sàng")
    filename = f"{Path(job.filename).stem}_song_ngu.docx"
    return FileResponse(
        job.output_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


# Phục vụ frontend tĩnh ở "/"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
