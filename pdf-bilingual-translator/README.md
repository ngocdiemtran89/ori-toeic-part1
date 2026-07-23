# PDF Bilingual Translator (Anh – Việt)

Tool web kéo-thả: đưa file **PDF sách** → tự động dịch **song ngữ Anh–Việt** (1 câu Anh /
1 câu Việt) → xuất ra file **Word (.docx)** trình bày như sách xuất bản.

- Tự phát hiện PDF chữ (đọc trực tiếp) hay PDF scan (chạy OCR).
- Hai bộ dịch: **Google Translate (miễn phí)** và **Claude API (chất lượng cao)**.
- Xử lý nền + thanh tiến độ cho file dày.

## Cài đặt

```bash
cd pdf-bilingual-translator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

OCR (cho PDF scan) cần Tesseract ở hệ thống:

```bash
# Ubuntu/Debian
sudo apt-get install -y tesseract-ocr
# macOS
brew install tesseract
```

## Chạy

```bash
uvicorn backend.main:app --reload --port 8000
```

Mở http://localhost:8000 → kéo-thả PDF → chọn bộ dịch → tải Word về.

## Dùng Claude API (tuỳ chọn, chất lượng cao)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# rồi chọn "Claude API" trên giao diện, hoặc đặt mặc định:
export PBT_ENGINE=claude
```

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PBT_ENGINE` | `google` | Bộ dịch mặc định (`google` / `claude`) |
| `PBT_CLAUDE_MODEL` | `claude-sonnet-5` | Model Claude khi dùng engine claude |
| `PBT_MAX_UPLOAD_MB` | `50` | Giới hạn dung lượng upload |
| `PBT_OCR_THRESHOLD` | `40` | Số ký tự tối thiểu/trang; dưới ngưỡng → OCR |
| `PBT_DATA_DIR` | `./data` | Nơi lưu file tạm |
| `PBT_CLAUDE_WORKERS` | `5` | Số lô Claude chạy song song |
| `PBT_GOOGLE_WORKERS` | `3` | Số lô Google chạy song song (thấp để tránh chặn IP) |
| `PBT_MAX_RETRIES` | `4` | Số lần thử lại khi lô dịch lỗi |
| `PBT_GLOSSARY_CHARS` | `6000` | Lượng text mẫu để rút glossary (Claude) |

## Tối ưu chất lượng & tốc độ

- **Dịch theo ngữ cảnh:** gửi cả cụm đoạn để model hiểu mạch văn, nhưng trả về
  theo từng câu → vừa mượt vừa giữ ánh xạ 1-1.
- **Glossary nhất quán (Claude):** quét mẫu đầu sách rút thuật ngữ chính, nhồi vào
  system prompt (có prompt caching) để dịch thống nhất xuyên suốt.
- **Song song:** các lô dịch chạy đồng thời → nhanh nhiều lần với sách dày.
- **Retry + backoff:** mỗi lô tự thử lại khi lỗi mạng/tạm thời, job không chết giữa chừng.

## Chi phí ước tính (Claude API)

Cho ~1 cuốn 300 trang (~120K token tiếng Anh); ~100 trang ≈ ⅓ số tiền.

| Bộ dịch | Chi phí/cuốn |
|---|---|
| Google Translate | Miễn phí |
| Claude Haiku 4.5 | ~$0.85 |
| Claude Sonnet 5 (khuyến nghị) | ~$1.70–$2.55 |
| Claude Opus 4.8 | ~$4.25 |

## Cấu trúc

```
backend/
  main.py            FastAPI: /api/upload, /api/status, /api/download
  jobs.py            Job nền + tiến độ
  config.py          Cấu hình
  pipeline/
    extract.py       PyMuPDF + OCR fallback
    segment.py       Tách câu (pysbd)
    translate.py     GoogleEngine / ClaudeEngine
    render_docx.py   Layout Word chuẩn sách
frontend/index.html  Giao diện kéo-thả
```
