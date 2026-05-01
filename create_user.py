# create_user.py สร้างตาราง user และเพิ่มผู้ใช้ 'admin'
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kok_data.db")
print("Using DB:", DB_PATH)

# ใช้ with เพื่อปิด connection อัตโนมัติ
with sqlite3.connect(DB_PATH) as conn:
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    try:
        cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("admin", "password123"))
        print("✅ สร้างผู้ใช้ 'admin' สำเร็จ!")
    except sqlite3.IntegrityError:
        print("ℹ️ ผู้ใช้ 'admin' มีอยู่แล้ว")