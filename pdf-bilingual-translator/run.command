#!/bin/bash
# Bấm đúp file này (trên Mac) để chạy tool dịch PDF song ngữ.
# Lần đầu sẽ tự cài thư viện (chờ 1-2 phút), các lần sau chạy ngay.

cd "$(dirname "$0")" || exit 1

# Kiểm tra Python 3
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ Chưa có Python 3. Cài tại https://python.org rồi chạy lại."
  read -r -p "Nhấn Enter để đóng..."
  exit 1
fi

# Lần đầu: tạo môi trường ảo + cài thư viện
if [ ! -d ".venv" ]; then
  echo "⏳ Lần đầu chạy: đang tạo môi trường và cài thư viện (chờ 1-2 phút)..."
  python3 -m venv .venv || { echo "❌ Không tạo được môi trường ảo."; read -r -p "Enter..."; exit 1; }
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt || { echo "❌ Cài thư viện thất bại."; read -r -p "Enter..."; exit 1; }
fi

# Dùng Claude thay vì Google (bỏ dấu # ở 2 dòng dưới và điền API key)
# export PBT_ENGINE=claude
# export ANTHROPIC_API_KEY="sk-ant-..."

echo "✅ Đang khởi động... trình duyệt sẽ tự mở sau vài giây."
echo "   Muốn DỪNG tool: quay lại cửa sổ này bấm  Control + C"
( sleep 3 ; open "http://localhost:8000" ) &

./.venv/bin/uvicorn backend.main:app --port 8000
