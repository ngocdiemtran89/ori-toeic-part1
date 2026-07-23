"""Cấu hình toàn cục cho tool dịch PDF song ngữ."""
from __future__ import annotations

import os
from pathlib import Path

# Thư mục lưu file tạm (upload + kết quả .docx)
DATA_DIR = Path(os.environ.get("PBT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"

# Engine dịch mặc định: "google" (free) hoặc "claude"
DEFAULT_ENGINE = os.environ.get("PBT_ENGINE", "google")

# Model dùng khi engine = claude
CLAUDE_MODEL = os.environ.get("PBT_CLAUDE_MODEL", "claude-sonnet-5")

# Giới hạn kích thước file upload (MB)
MAX_UPLOAD_MB = int(os.environ.get("PBT_MAX_UPLOAD_MB", "50"))

# Ngưỡng ký tự tối thiểu trên 1 trang để coi là "PDF chữ"; dưới ngưỡng -> chạy OCR
OCR_TEXT_THRESHOLD = int(os.environ.get("PBT_OCR_THRESHOLD", "40"))

# Số câu gửi mỗi lô khi dịch bằng Claude
CLAUDE_BATCH_SENTENCES = int(os.environ.get("PBT_CLAUDE_BATCH", "40"))

# Giới hạn ký tự mỗi lần gọi Google Translate (deep-translator giới hạn ~5000)
GOOGLE_BATCH_CHARS = 4500


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
