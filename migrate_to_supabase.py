#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration script from SQLite to PostgreSQL (Supabase) - SIMPLIFIED VERSION
"""

import sqlite3 #ไลบรารีมาตรฐานของ Python สำหรับอ่านข้อมูลจากไฟล์ kok_data.db เดิม
import psycopg2 #ไดรเวอร์สำหรับเชื่อมต่อและเขียนข้อมูลลง PostgreSQL (Supabase)
from psycopg2.extras import execute_batch # execute_batch: ฟังก์ชันสำคัญจาก psycopg2.extras ที่ใช้สำหรับ Batch Insert (แทรกข้อมูลทีละมากๆ ในคำสั่งเดียว) ช่วยให้การย้ายข้อมูลเร็วขึ้นมากเมื่อเทียบกับการแทรกทีละแถว
import os
import traceback
from dotenv import load_dotenv #โหลดตัวแปรสภาพแวดล้อมจากไฟล์ .env เพื่อความปลอดภัย (ไม่ Hardcode รหัสผ่าน)

def safe_float(value):
    """แปลง string เป็น float อย่างปลอดภัย"""
    if value is None:
        return None
    val_str = str(value).strip()
    val_str = val_str.replace(',', '').replace(' ', '')
    #ถ้าค่าขึ้นต้นด้วย < หรือ > (เช่น <0.01) → ฟังก์ชันจะคืนค่า None (เพราะไม่สามารถแปลงเป็นตัวเลขตรงๆ ได้)
    if not val_str or val_str[0] in '<>' or not val_str.replace('.', '', 1).isdigit():
        return None
    try: # ถ้าค่าเป็น '-', 'ND', '' → คืนค่า None
        return float(val_str)
    except ValueError: # ถ้าแปลงไม่สำเร็จ → คืนค่า None เพื่อป้องกัน Error
        return None

# โหลด .env ก่อนใช้ os.environ
load_dotenv()

SQLITE_DB_PATH = 'kok_data.db'

# ใช้ Supabase URL จาก .env
SUPABASE_URL = os.environ.get('SUPABASE_DATABASE_URL')
if not SUPABASE_URL:
    raise ValueError("SUPABASE_DATABASE_URL not set. Please check your .env file")

# ตรวจสอบ/เพิ่ม sslmode ถ้ายังไม่มี
if 'sslmode' not in SUPABASE_URL.lower():
    sep = '&' if '?' in SUPABASE_URL else '?'
    SUPABASE_URL += f"{sep}sslmode=require" #sslmode=require: สำคัญมาก สำหรับ Supabase ที่บังคับให้เชื่อมต่อผ่าน SSL (การเข้ารหัส) ถ้าไม่เพิ่มพารามิเตอร์นี้ การเชื่อมต่อจะล้มเหลว


def get_sqlite_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row # ทำให้เข้าถึงคอลัมน์ด้วยชื่อได้ (เช่น row['station'])
    return conn


def get_supabase_connection():
    """เชื่อมต่อ Supabase PostgreSQL แบบง่าย"""
    db_url = os.environ.get('SUPABASE_DATABASE_URL')
    if not db_url:
        raise ValueError("SUPABASE_DATABASE_URL not set")
    
    # เพิ่ม sslmode ถ้ายังไม่มี
    if 'sslmode' not in db_url.lower():
        sep = '&' if '?' in db_url else '?'
        db_url += f"{sep}sslmode=require"
    
    # เชื่อมต่อตรงๆ โดยไม่ต้องแก้ hostname
    return psycopg2.connect(db_url)


def create_supabase_tables(conn):
    print("📊 Creating tables in Supabase...")
    with conn.cursor() as cur:
        # ลบตารางเก่า (เพื่อเริ่มใหม่ด้วย schema ที่ถูกต้อง)
        cur.execute("DROP TABLE IF EXISTS water_data, soil_data, users")
        cur.execute("DROP TABLE IF EXISTS station_data")

        # ตารางสถานี
        cur.execute("""
            CREATE TABLE station_data (
                id SERIAL PRIMARY KEY,
                station TEXT UNIQUE NOT NULL,
                river TEXT, tambon TEXT, amphoe TEXT, province TEXT, location TEXT
            )
        """)
        
        # ตารางข้อมูลน้ำ — ใช้ station TEXT
        cur.execute("""
            CREATE TABLE water_data (
                id SERIAL PRIMARY KEY,
                station TEXT REFERENCES station_data(station) ON DELETE CASCADE,
                parameter TEXT, location TEXT,
                check_number TEXT, value TEXT, numeric_value REAL, unit TEXT
            )
        """)
        
        # ตารางข้อมูลดิน
        cur.execute("""
            CREATE TABLE soil_data (
                id SERIAL PRIMARY KEY,
                station TEXT REFERENCES station_data(station) ON DELETE CASCADE,
                parameter TEXT, location TEXT,
                check_number TEXT, value TEXT, numeric_value REAL
            )
        """)
        
        # ตาราง users
        cur.execute("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        
        # Indexes
        cur.execute("CREATE INDEX idx_water_station ON water_data(station)")
        cur.execute("CREATE INDEX idx_soil_station ON soil_data(station)")
        
    conn.commit()
    print("✅ Tables created successfully in Supabase")

# ย้ายข้อมูลผู้ใช้
def migrate_users(sqlite_conn, supabase_conn):
    print("👤 Migrating users...")
    sqlite_cur = sqlite_conn.cursor()
    supabase_cur = supabase_conn.cursor()
    sqlite_cur.execute("SELECT * FROM users")
    users = sqlite_cur.fetchall()
    if users:
        # ใช้ execute_batch เพื่อแทรกข้อมูลทีละหลายๆ แถว (เร็วขึ้น)
        execute_batch(supabase_cur, """
            INSERT INTO users (username, password) VALUES (%s, %s)
            ON CONFLICT (username) DO NOTHING
        """, [(user['username'], user['password']) for user in users])
        supabase_conn.commit()
        print(f"   ✅ Migrated {len(users)} users")
    else:
        print("   ℹ️  No users to migrate")


def migrate_stations(sqlite_conn, supabase_conn):
    """Migrate stations"""
    print("🏭 Migrating stations...")
    sqlite_cur = sqlite_conn.cursor()
    supabase_cur = supabase_conn.cursor()
    # ดึงข้อมูลจาก SQLite (ชื่อคอลัมน์ภาษาไทยตามไฟล์เดิม)
    sqlite_cur.execute("""
        SELECT id, "สถานี", "\ufeffแม่น้ำ", "ตำบล", "อำเภอ", "จังหวัด", "บริเวณที่เก็บ"
        FROM station_data
    """)
    stations = sqlite_cur.fetchall()

    if stations:
        for station in stations:
            station_dict = dict(station)
            for key, val in station_dict.items():
                if isinstance(val, str):
                    station_dict[key] = val.strip()

            supabase_cur.execute("""
                INSERT INTO station_data (station, river, tambon, amphoe, province, location)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (station) DO UPDATE SET
                    river = EXCLUDED.river,
                    tambon = EXCLUDED.tambon,
                    amphoe = EXCLUDED.amphoe,
                    province = EXCLUDED.province,
                    location = EXCLUDED.location
            """, (
                station_dict['สถานี'],
                station_dict['\ufeffแม่น้ำ'],  #  จัดการกับ BOM (\ufeff) ที่อาจติดมากับชื่อคอลัมน์จาก CSV
                station_dict['ตำบล'], 
                station_dict['อำเภอ'],
                station_dict['จังหวัด'],
                station_dict['บริเวณที่เก็บ']
            ))

        supabase_conn.commit()
        print(f"   ✅ Migrated {len(stations)} stations")
    else:
        print("   ℹ️  No stations to migrate")


def migrate_water_data(sqlite_conn, supabase_conn):
    print("💧 Migrating water data...")
    sqlite_cur = sqlite_conn.cursor()
    supabase_cur = supabase_conn.cursor()

    sqlite_cur.execute("""
        SELECT "\ufeffสิ่งที่ตรวจ", "ที่ตั้ง", "ครั้งที่ตรวจ", "ค่าที่ได้", "หน่วย", "สถานี"
        FROM water_data
    """)
    water_data = sqlite_cur.fetchall()

    if water_data:
        batch_data = []  #  รวบรวมข้อมูลเพื่อแทรกแบบกลุ่ม (Batch)
        for row in water_data:
            row_dict = dict(row)
            for key, val in row_dict.items():
                if isinstance(val, str):
                    row_dict[key] = val.strip()

            station_code = row_dict['สถานี']
            if not station_code:
                continue

            raw_value = row_dict['ค่าที่ได้']
            numeric_val = safe_float(raw_value)  # แปลงเป็นตัวเลขสำหรับคอลัมน์ numeric_value

            batch_data.append((
                row_dict['\ufeffสิ่งที่ตรวจ'], # parameter
                row_dict['ที่ตั้ง'],  # location
                row_dict['ครั้งที่ตรวจ'], # check_number
                raw_value, # value (เก็บเป็น text เดิม)
                numeric_val,  # numeric_value (เก็บเป็นตัวเลขสำหรับคำนวณ)
                station_code,  # station (Foreign Key)
                row_dict['หน่วย']  # unit
            ))
        # แทรกข้อมูลทีละ 1,000 แถว (เร็วมาก!)
        execute_batch(supabase_cur, """
            INSERT INTO water_data 
            (parameter, location, check_number, value, numeric_value, station, unit)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, batch_data, page_size=1000)

        supabase_conn.commit()
        print(f"   ✅ Migrated {len(batch_data)} water data records")
    else:
        print("   ℹ️  No water data to migrate")


def migrate_soil_data(sqlite_conn, supabase_conn):
    print("🌱 Migrating soil data...")
    sqlite_cur = sqlite_conn.cursor()
    supabase_cur = supabase_conn.cursor()

    sqlite_cur.execute("""
        SELECT "สารที่ตรวจ", "บริเวณจุดเก็บ", "ครั้งที่ตรวจ", "ค่าที่ได้", "สถานี"
        FROM soil_data
    """)
    soil_data = sqlite_cur.fetchall()

    if soil_data:
        batch_data = []
        for row in soil_data:
            row_dict = dict(row)
            for key, val in row_dict.items():
                if isinstance(val, str):
                    row_dict[key] = val.strip()

            station_code = row_dict['สถานี']
            if not station_code:
                continue

            raw_value = row_dict['ค่าที่ได้']
            numeric_val = safe_float(raw_value)

            batch_data.append((
                row_dict['สารที่ตรวจ'],
                row_dict['บริเวณจุดเก็บ'],
                row_dict['ครั้งที่ตรวจ'],
                raw_value,
                numeric_val,
                station_code
            ))

        execute_batch(supabase_cur, """
            INSERT INTO soil_data 
            (parameter, location, check_number, value, numeric_value, station)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, batch_data, page_size=1000)

        supabase_conn.commit()
        print(f"   ✅ Migrated {len(batch_data)} soil data records")
    else:
        print("   ℹ️  No soil data to migrate")


def verify_migration(sqlite_conn, supabase_conn):
    print("\n🔍 Verifying migration...")
    sqlite_cur = sqlite_conn.cursor()
    supabase_cur = supabase_conn.cursor()

    tables = ['station_data', 'water_data', 'soil_data', 'users']
    for table in tables:
        # นับจำนวนแถวใน SQLite
        sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
        sqlite_count = sqlite_cur.fetchone()[0]
        # นับจำนวนแถวใน Supabase
        supabase_cur.execute(f"SELECT COUNT(*) FROM {table}")
        supabase_count = supabase_cur.fetchone()[0]
        # เปรียบเทียบและแสดงผล
        status = "✅" if sqlite_count == supabase_count else "❌"
        print(f"   {status} {table}: SQLite={sqlite_count}, Supabase={supabase_count}")


def main():
    print("=" * 60)
    print("🚀 Starting SQLite to Supabase Migration")
    print("=" * 60)

    try:
        print("\n📡 Connecting to databases...")
        sqlite_conn = get_sqlite_connection()
        supabase_conn = get_supabase_connection()
        print("   ✅ Connected to Supabase successfully")
        # ลำดับการทำงาน:
        # 1. สร้างตารางใหม่ (ลบของเก่าก่อน)
        create_supabase_tables(supabase_conn)
        # 2. ย้ายข้อมูลตามลำดับความสัมพันธ์ (Parent → Child)
        migrate_users(sqlite_conn, supabase_conn)  # users (ไม่ขึ้นกับใคร)
        migrate_stations(sqlite_conn, supabase_conn) # stations (Parent ของ water/soil)
        migrate_water_data(sqlite_conn, supabase_conn)# water_data (Child ของ stations)
        migrate_soil_data(sqlite_conn, supabase_conn)# soil_data (Child ของ stations)
        # 3. ตรวจสอบความถูกต้อง
        verify_migration(sqlite_conn, supabase_conn)

        # ปิดการเชื่อมต่อ
        sqlite_conn.close()
        supabase_conn.close()

        print("\n" + "=" * 60)
        print("✅ Migration to Supabase completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        traceback.print_exc() # พิมพ์รายละเอียดข้อผิดพลาดสำหรับ Debug
        raise # โยนข้อผิดพลาดต่อเพื่อให้โปรแกรมหยุดทำงาน

if __name__ == '__main__':
    main()