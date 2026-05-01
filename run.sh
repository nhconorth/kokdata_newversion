#!/bin/bash
# Script to run the Flask application

cd "$(dirname "$0")"

echo "กำลังตรวจสอบ Flask..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "⚠️  Flask ยังไม่ได้ติดตั้ง"
    echo "กำลังติดตั้ง Flask..."
    pip3 install Flask
fi

echo ""
echo "กำลังเริ่มเว็บแอปพลิเคชัน..."
python3 app.py


