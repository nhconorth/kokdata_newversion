# แก้ปัญหา Error 403 Forbidden

## วิธีแก้ปัญหา

### 1. ล้าง Cache ของ Chrome
- กด `Cmd+Shift+Delete` (Mac) หรือ `Ctrl+Shift+Delete` (Windows)
- เลือก "Cached images and files"
- กด "Clear data"

### 2. ใช้ Incognito/Private Mode
- เปิด Chrome ในโหมด Incognito (Cmd+Shift+N หรือ Ctrl+Shift+N)
- เปิด `http://localhost:5000`

### 3. ตรวจสอบว่าแอปพลิเคชันรันอยู่
```bash
# ใน Terminal ตรวจสอบว่าเห็นข้อความนี้:
# * Running on http://0.0.0.0:5000
```

### 4. ลอง URL ต่างๆ
- `http://localhost:5000`
- `http://127.0.0.1:5000`
- `http://0.0.0.0:5000` (อาจไม่ทำงาน)

### 5. ทดสอบ endpoint ง่ายๆ
เปิด `http://localhost:5000/test` ควรเห็นข้อความ "Flask app is working!"

### 6. ตรวจสอบ Chrome Extensions
- ปิด Extensions ที่เกี่ยวกับ Security หรือ Privacy
- เช่น AdBlock, Privacy Badger, หรือ Security extensions อื่นๆ

### 7. ใช้เบราว์เซอร์อื่น
- ลอง Safari หรือ Firefox

### 8. เปลี่ยน Port
ถ้ายังไม่ได้ ให้เปลี่ยน port ใน app.py:
```python
app.run(debug=True, host='0.0.0.0', port=8080, threaded=True, use_reloader=False)
```
แล้วเปิด `http://localhost:8080`


