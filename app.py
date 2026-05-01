#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flask web application with PostgreSQL (Neon)
"""
# Flask เป็น framework หลักสำหรับสร้างเว็บ
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash
import psycopg2 # ใช้เชื่อมต่อ Database ของ PostgreSQL
from psycopg2.extras import RealDictCursor
import os #ใช้จัดการ Environment Variables เช่น รหัสผ่าน ,URL Database
import secrets #ใช้สร้าง Secret Key ของ Flask
from dotenv import load_dotenv #ใช้จัดการ Environment Variables เช่น รหัสผ่าน ,URL Database
import requests # ใช้ดึงข้อมูลข่าวจากเว็บภายนอก
from bs4 import BeautifulSoup # ใช้ดึงข้อมูลข่าวจากเว็บภายนอก
from datetime import datetime
from functools import lru_cache
import time

load_dotenv()  # โหลดทันทีหลัง import
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16) #ใช้สำหรับจัดการ Session (การเข้าสู่ระบบ)
app.config['JSON_AS_ASCII'] = False

# === Register API Blueprint ===
from api.index import api_bp
app.register_blueprint(api_bp) #ลงทะเบียน API Blueprint

#  ฟังก์ชันสำหรับสร้างการเชื่อมต่อไปยังฐานข้อมูล โดยใช้ URL จาก SUPABASE_DATABASE_URL
def get_db():
    db_url = os.environ.get('SUPABASE_DATABASE_URL')
    if not db_url:
        raise ValueError("SUPABASE_DATABASE_URL not set in environment")
    conn = psycopg2.connect(db_url)
    conn.cursor_factory = RealDictCursor
    return conn

#ฟังก์ชันสำหรับสร้างตารางฐานข้อมูลอัตโนมัติหากยังไม่มีข้อมูลใน Database
def init_db():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # ตารางสถานี
        cur.execute("""
        CREATE TABLE IF NOT EXISTS station_data (
            id SERIAL PRIMARY KEY,
            station TEXT UNIQUE NOT NULL,
            river TEXT, tambon TEXT, amphoe TEXT, province TEXT, location TEXT,
            lat DECIMAL(10, 7),   
            lon DECIMAL(10, 7) 
        )
        """)
        # ตารางข้อมูลน้ำ — ใช้ station TEXT (ไม่ใช่ station_id)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS water_data (
            id SERIAL PRIMARY KEY,
            station TEXT REFERENCES station_data(station) ON DELETE CASCADE,
            parameter TEXT, unit TEXT, location TEXT,
            check_number TEXT, value TEXT, numeric_value REAL
        )
        """)
        # ตารางข้อมูลดิน — ใช้ station TEXT เช่นกัน
        cur.execute("""
        CREATE TABLE IF NOT EXISTS soil_data (
            id SERIAL PRIMARY KEY,
            station TEXT REFERENCES station_data(station) ON DELETE CASCADE,
            parameter TEXT, location TEXT,
            check_number TEXT, value TEXT, numeric_value REAL
        )
        """)
        # ตาราง users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        """)
        # Indexes — ใช้ชื่อคอลัมน์ที่ถูกต้อง (station)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_water_station ON water_data(station)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_soil_station ON soil_data(station)")
        # สร้าง admin user
        cur.execute('SELECT COUNT(*) FROM users WHERE username = %s', ('admin',))
        if cur.fetchone()[0] == 0:
            cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', ('admin', 'admin123'))
            print("✅ สร้าง user admin: username='admin', password='admin123'")
        conn.commit()
        print("✅ ตารางฐานข้อมูลพร้อมใช้งาน")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

# === Debug Route ===
@app.route('/debug/db')
def debug_db():
    """แสดงข้อมูลฐานข้อมูลสำหรับ debug"""
    info = {
        'POSTGRES_HOST': os.environ.get('POSTGRES_HOST', 'Not set'),
        'POSTGRES_DATABASE': os.environ.get('POSTGRES_DATABASE', 'Not set'),
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        info['Tables'] = [row['table_name'] for row in cur.fetchall()]
        conn.close()
    except Exception as e:
        info['Error'] = str(e)
    return info

# ฟังก์ชันที่ใช้เช็คก่อนเข้าถึงส่วนที่แอดมินมีสิทธิ์ ถ้ายังไม่ได้ Login จะถูกเด้งไปหน้า Login
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# === Login Route ===
@app.route('/login', methods=['GET', 'POST'])
def login(): # รับค่า username กับ password จาก form
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        print(f"🔍 DEBUG: login attempt - username='{username}'")
        # ตรวจสอบว่าเป็น AJAX request หรือไม่
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT password FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        conn.close()
        print(f"🔍 DEBUG: DB result - {row}")
        if row and row['password'] == password:
            # เข้าสู่ระบบสำเร็จ
            session['logged_in'] = True
            session['username'] = username
            if is_ajax:
                # คืนค่า JSON สำหรับ AJAX
                return jsonify({
                    'success': True,
                    'message': 'เข้าสู่ระบบสำเร็จ',
                    'redirect_url': url_for('index')
                })
            else:
                # Redirect สำหรับ form submit ปกติ (รองรับกรณีไม่มี JS)
                return redirect(url_for('index'))
        else:
            # กรณี เข้าสู่ระบบไม่สำเร็จ
            if is_ajax:
                return jsonify({
                    'success': False,
                    'message': 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'
                }), 401  # HTTP 401 Unauthorized
            else:
                flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง กรุณากรอกใหม่', 'error')
                return redirect(url_for('login'))
    # GET request: แสดงหน้า login
    return render_template('login.html')

# === Logout Route ===
@app.route('/logout')
def logout(): # ออกแล้วจะเด้งกลับไปหน้าหลัก
    session.clear()
    return redirect(url_for('index'))

# === CORS Headers ===
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# === Get All Stations ===
def get_stations():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    SELECT id, station, river, tambon, amphoe, province, location
    FROM station_data
    ORDER BY river, station
    """)
    stations = cur.fetchall()
    conn.close()
    return stations

# === Index Route ===
@app.route('/stations')
def stations_manage():
    try:
        stations = get_stations() # ดึงรายการสถานีทั้งหมด
        # แยกข้อมูล จังหวัด, อำเภอ, ตำบล เพื่อใช้ทำ Filter ในหน้าเว็บ
        unique_rivers = sorted(list(set([s['river'] for s in stations if s['river']])))
        unique_provinces = sorted(list(set([s['province'] for s in stations if s['province']])))
        unique_tambons = sorted(list(set([s['tambon'] for s in stations if s['tambon']])))
        unique_amphoes = sorted(list(set([s['amphoe'] for s in stations if s['amphoe']])))
        location_hierarchy = {}
        for station in stations:
            prov = station.get('province', '')
            amph = station.get('amphoe', '')
            tamb = station.get('tambon', '')
            if prov and amph and tamb:
                if prov not in location_hierarchy:
                    location_hierarchy[prov] = {}
                if amph not in location_hierarchy[prov]:
                    location_hierarchy[prov][amph] = set()
                location_hierarchy[prov][amph].add(tamb)
        for prov in location_hierarchy:
            for amph in location_hierarchy[prov]:
                location_hierarchy[prov][amph] = sorted(list(location_hierarchy[prov][amph]))
        return render_template('index.html',
            stations=stations,
            unique_rivers=unique_rivers,
            unique_provinces=unique_provinces,
            unique_tambons=unique_tambons,
            unique_amphoes=unique_amphoes,
            location_hierarchy=location_hierarchy)
    except Exception as e:
        return f"Error loading page: {str(e)}", 500

# === Test Route ===
@app.route('/test')
def test():
    return "Flask app is working with PostgreSQL!"

def get_station_by_code(station_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    SELECT id, station, river, tambon, amphoe, province, location, lat, lon
    FROM station_data
    WHERE TRIM(station) = %s
    """, (station_code.strip(),))
    row = cur.fetchone()
    conn.close()
    return row

# === Get Water Data ===
def get_water_data(station_code):
    conn = get_db()
    cur = conn.cursor()
    # ดึงข้อมูลน้ำเรียงตามครั้งที่ตรวจ
    cur.execute(r"""
    SELECT parameter, unit, location, check_number, value, numeric_value
    FROM water_data
    WHERE TRIM(station) = %s
    ORDER BY
        NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST,
        check_number,
        parameter
    """, (station_code.strip(),))
    # สรา้งโครงสร้างข้อมูลแบบ Pivot
    pivot_data = {}
    numeric_data = {}
    check_numbers = []
    unit_info = {}
    for row in cur.fetchall():
        param = row['parameter']
        check_num = row['check_number']
        value = row['value']
        numeric_value = row['numeric_value'] if row['numeric_value'] is not None else 0
        unit = row['unit']
        if param not in pivot_data:
            pivot_data[param] = {}
            numeric_data[param] = {}
            unit_info[param] = unit
        # แปลงครั้งที่ 1 เป็น ตัวเลข 1 สำหรับเรียงลำดับ
        try:
            check_num_int = int(check_num.split('ครั้งที่')[-1].strip())
            if check_num_int not in check_numbers:
                check_numbers.append(check_num_int)
            pivot_data[param][check_num_int] = value
            numeric_data[param][check_num_int] = numeric_value
        except (ValueError, IndexError):
            if check_num not in check_numbers:
                check_numbers.append(check_num)
            pivot_data[param][check_num] = value
            numeric_data[param][check_num] = numeric_value
    conn.close()
    # เรียงลำดับครั้งที่ตรวจ โดยตัวเลขก่อน และตามด้วยข้อความ
    numeric_checks = sorted([c for c in check_numbers if isinstance(c, int)])
    text_checks = sorted([c for c in check_numbers if not isinstance(c, int)])
    sorted_checks = numeric_checks + text_checks
    parameters = sorted(pivot_data.keys())
    pivot_list = []
    for param in parameters:
        row_data = {'parameter': param, 'check_values': {}, 'unit': unit_info.get(param, '')}
        for check_num in sorted_checks:
            value = pivot_data[param].get(check_num, None)
            row_data['check_values'][str(check_num)] = value if value else None
        pivot_list.append(row_data)
    pivot_list_filtered = []
    for param in parameters:
        row_data_filtered = {'parameter': param, 'check_values': {}, 'numeric_values': {}, 'unit': unit_info.get(param, '')}
        for check_num in sorted_checks:
            value = pivot_data[param].get(check_num, None)
            numeric_value = numeric_data[param].get(check_num, 0)
            row_data_filtered['check_values'][str(check_num)] = value if value else None
            row_data_filtered['numeric_values'][str(check_num)] = numeric_value
        pivot_list_filtered.append(row_data_filtered)
    return {
        'pivot': pivot_data,
        'pivot_list': pivot_list,
        'pivot_list_filtered': pivot_list_filtered,
        'check_numbers': sorted_checks,
        'units': unit_info,
        'parameters': parameters
    }

# === Get Soil Data ===
def get_soil_data(station_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(r"""
    SELECT parameter, location, check_number, value, numeric_value
    FROM soil_data
    WHERE TRIM(station) = %s
    ORDER BY
        NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST,
        check_number,
        parameter
    """, (station_code.strip(),))
    pivot_data = {}
    numeric_data = {}
    check_numbers = []
    for row in cur.fetchall():
        param = row['parameter']
        check_num = row['check_number']
        value = row['value']
        numeric_value = row['numeric_value'] if row['numeric_value'] is not None else 0
        if param not in pivot_data:
            pivot_data[param] = {}
            numeric_data[param] = {}
        try:
            check_num_int = int(check_num.split('ครั้งที่')[-1].strip())
            if check_num_int not in check_numbers:
                check_numbers.append(check_num_int)
            pivot_data[param][check_num_int] = value
            numeric_data[param][check_num_int] = numeric_value
        except (ValueError, IndexError):
            if check_num not in check_numbers:
                check_numbers.append(check_num)
            pivot_data[param][check_num] = value
            numeric_data[param][check_num] = numeric_value
    conn.close()
    numeric_checks = sorted([c for c in check_numbers if isinstance(c, int)])
    text_checks = sorted([c for c in check_numbers if not isinstance(c, int)])
    sorted_checks = numeric_checks + text_checks
    parameters = sorted(pivot_data.keys())
    pivot_list = []
    for param in parameters:
        row_data = {'parameter': param, 'check_values': {}}
        for check_num in sorted_checks:
            value = pivot_data[param].get(check_num, None)
            row_data['check_values'][str(check_num)] = value if value else None
        pivot_list.append(row_data)
    pivot_list_filtered = []
    for param in parameters:
        row_data_filtered = {'parameter': param, 'check_values': {}, 'numeric_values': {}}
        for check_num in sorted_checks:
            value = pivot_data[param].get(check_num, None)
            numeric_value = numeric_data[param].get(check_num, 0)
            row_data_filtered['check_values'][str(check_num)] = value if value else None
            row_data_filtered['numeric_values'][str(check_num)] = numeric_value
        pivot_list_filtered.append(row_data_filtered)
    return {
        'pivot': pivot_data,
        'pivot_list': pivot_list,
        'pivot_list_filtered': pivot_list_filtered,
        'check_numbers': sorted_checks,
        'parameters': parameters
    }

# === Add Station Route ===
@app.route('/add-station', methods=['GET', 'POST'])
@login_required
def add_station():
    if request.method == 'POST':
        try:
            # === ดึงข้อมูลพื้นฐาน ===
            station = request.form.get('station', '').strip()
            river = request.form.get('river', '').strip()
            tambon = request.form.get('tambon', '').strip()
            amphoe = request.form.get('amphoe', '').strip()
            province = request.form.get('province', '').strip()
            location = request.form.get('location', '').strip()
            lat = request.form.get('lat', '').strip()
            lon = request.form.get('lon', '').strip()

            lat_value = float(lat) if lat else None
            lon_value = float(lon) if lon else None

            # === Debug: พิมพ์ข้อมูลที่ได้รับ ===
            print(f"\n🔍 === DEBUG: Add Station ===")
            print(f"📍 station={station}")
            print(f"📍 location={location}")
            # ดึงพารามิเตอร์น้ำ
            parameters = request.form.getlist('parameter[]')
            units = request.form.getlist('unit[]')
            print(f"💧 parameters={parameters}")
            print(f"💧 units={units}")
            # ดึงพารามิเตอร์ดิน
            soil_params = request.form.getlist('soil_parameter[]')
            print(f"🌱 soil_params={soil_params}")
            conn = get_db()
            cur = conn.cursor()
            # === 1. บันทึกสถานี ===
            cur.execute("""
            INSERT INTO station_data (station, river, tambon, amphoe, province, location, lat, lon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station) DO UPDATE SET
                river = EXCLUDED.river,
                tambon = EXCLUDED.tambon,
                amphoe = EXCLUDED.amphoe,
                province = EXCLUDED.province,
                location = EXCLUDED.location,
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon
            """, (station, river, tambon, amphoe, province, location, lat_value, lon_value))
            print(f"✅ Saved station: {station}")
            # === 2. บันทึกข้อมูลน้ำ ===
            water_count = 0
            water_check_count = int(request.form.get('water_check_count', 18))
            for i in range(1, water_check_count + 1):
                check_values = request.form.getlist(f'check{i}[]')
                if not check_values or all(v == '' for v in check_values):
                    continue
                for idx, param in enumerate(parameters):
                    if idx >= len(check_values):
                        break
                    value = check_values[idx].strip() if check_values[idx] else ''
                    if not value:
                        continue
                    unit = units[idx].strip() if idx < len(units) else ''
                    numeric_value = None
                    if value and value not in ['-', 'ND', '']:
                        try:
                            numeric_value = 0.0 if value.startswith('<') else float(value)
                        except ValueError:
                            pass
                    cur.execute("""
                    INSERT INTO water_data (station, parameter, unit, location, check_number, value, numeric_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (station, param, unit, location, f'ครั้งที่ {i}', value, numeric_value))
                    water_count += 1
            print(f"✅ Saved {water_count} water data records")
            # === 3. บันทึกข้อมูลดิน ===
            soil_count = 0
            soil_check_count = int(request.form.get('soil_check_count', 11))
            for i in range(1, soil_check_count + 1):
                soil_check_values = request.form.getlist(f'soil_check{i}[]')
                if not soil_check_values or all(v == '' for v in soil_check_values):
                    continue
                for idx, param in enumerate(soil_params):
                    if idx >= len(soil_check_values):
                        break
                    value = soil_check_values[idx].strip() if soil_check_values[idx] else ''
                    if not value:
                        continue
                    numeric_value = None
                    if value and value not in ['-', 'ND', '']:
                        try:
                            numeric_value = 0.0 if value.startswith('<') else float(value)
                        except ValueError:
                            pass
                    cur.execute("""
                    INSERT INTO soil_data (station, parameter, location, check_number, value, numeric_value)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """, (station, param, location, f'ครั้งที่ {i}', value, numeric_value))
                    soil_count += 1
            print(f"✅ Saved {soil_count} soil data records")
            conn.commit()
            conn.close()
            return jsonify({
                'success': True,
                'message': 'Saved successfully',
                'water': water_count,
                'soil': soil_count
            })
        except Exception as e:
            print(f"❌ Error saving station: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'message': str(e)}), 500
    return render_template('add_station.html')

# === Delete Station Route ===
@app.route('/delete-station/<station_code>', methods=['DELETE'])
@login_required
def delete_station(station_code):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('DELETE FROM station_data WHERE station = %s', (station_code.strip(),))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print("Error deleting station:", str(e))
        return jsonify({'success': False, 'message': str(e)}), 500

# === Station Detail Route ===
@app.route('/station/<station_code>')
def station_detail(station_code):
    try:
        station = get_station_by_code(station_code)  # มี lat/lon แล้ว
        if not station:
            return f"ไม่พบสถานี: {station_code}", 404
        water_data = get_water_data(station_code)
        soil_data = get_soil_data(station_code)
        return render_template('station_detail.html',
            station=station,  #  station มี lat/lon
            water_data=water_data,
            soil_data=soil_data)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return f"Error loading station: {str(e)}", 500

# === Edit Station Route ===
@app.route('/edit-station/<station_code>', methods=['GET', 'POST'])
@login_required
def edit_station(station_code):
    if request.method == 'POST':
        try:
            # === ดึงข้อมูลพื้นฐาน ===
            station = request.form['station'].strip()
            river = request.form['river'].strip()
            tambon = request.form['tambon'].strip()
            amphoe = request.form['amphoe'].strip()
            province = request.form['province'].strip()
            location = request.form['location'].strip()
            lat_str = request.form.get('lat', '').strip()
            lon_str = request.form.get('lon', '').strip()

            try:
                lat = float(lat_str) if lat_str else None
            except ValueError:
                lat = None
            try:
                lon = float(lon_str) if lon_str else None
            except ValueError:
                lon = None
            
            conn = get_db()
            cur = conn.cursor()
            # อัปเดตข้อมูลสถานี
            cur.execute("""
            UPDATE station_data
            SET station = %s, river = %s, tambon = %s, amphoe = %s, province = %s, location = %s,  lat = %s, lon = %s
            WHERE station = %s
            """, (station, river, tambon, amphoe, province, location, lat, lon, station_code))
            # ลบเฉพาะข้อมูลน้ำและดินเดิม
            cur.execute('DELETE FROM water_data WHERE station = %s', (station_code.strip(),))
            cur.execute('DELETE FROM soil_data WHERE station = %s', (station_code.strip(),))
            parameters = request.form.getlist('parameter[]')
            units = request.form.getlist('unit[]')
            soil_params = request.form.getlist('soil_parameter[]')
            # บันทึกข้อมูลน้ำใหม่
            water_check_count = int(request.form.get('water_check_count', 14))
            for i in range(1, water_check_count + 1):
                check_values = request.form.getlist(f'check{i}[]')
                for idx, param in enumerate(parameters):
                    if idx < len(check_values):
                        value = check_values[idx].strip()
                        unit = units[idx].strip() if idx < len(units) else ''
                        numeric_value = None
                        if value and value not in ['-', 'ND']:
                            try:
                                numeric_value = 0.0 if value.startswith('<') else float(value)
                            except ValueError:
                                pass
                        cur.execute("""
                        INSERT INTO water_data (station, parameter, unit, location, check_number, value, numeric_value)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (station, param, unit, location, f'ครั้งที่ {i}', value, numeric_value))
            # บันทึกข้อมูลดินใหม่
            soil_check_count = int(request.form.get('soil_check_count', 8))
            for i in range(1, soil_check_count + 1):
                soil_check_values = request.form.getlist(f'soil_check{i}[]')
                for idx, param in enumerate(soil_params):
                    if idx < len(soil_check_values):
                        value = soil_check_values[idx].strip()
                        numeric_value = None
                        if value and value not in ['-', 'ND']:
                            try:
                                numeric_value = 0.0 if value.startswith('<') else float(value)
                            except ValueError:
                                pass
                        cur.execute("""
                        INSERT INTO soil_data (station, parameter, location, check_number, value, numeric_value)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """, (station, param, location, f'ครั้งที่ {i}', value, numeric_value))
            conn.commit()
            conn.close()
            return jsonify({'success': True})
        except Exception as e:
            print(f"ERROR in edit_station POST: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500
    # === GET: ดึงข้อมูลเดิมมา pre-fill ===
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
        SELECT river, station, location, tambon, amphoe, province, lat, lon
        FROM station_data WHERE station = %s
        """, (station_code.strip(),))
        station_row = cur.fetchone()
        if not station_row:
            conn.close()
            return "ไม่พบสถานี", 404
        cur.execute(r"""
        SELECT parameter, unit, check_number, value
        FROM water_data WHERE station = %s
        ORDER BY
            NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST,
            check_number
        """, (station_code.strip(),))
        water_rows = cur.fetchall()
        cur.execute(r"""
        SELECT parameter, check_number, value
        FROM soil_data WHERE station = %s
        ORDER BY
            NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST,
            check_number
        """, (station_code.strip(),))
        soil_rows = cur.fetchall()
        conn.close()
        water_data = {}
        for row in water_rows:
            param = row['parameter']
            if param not in water_data:
                water_data[param] = {'unit': row['unit'], 'checks': {}}
            check_num = row['check_number']
            water_data[param]['checks'][check_num] = row['value']
        soil_data = {}
        for row in soil_rows:
            param = row['parameter']
            if param not in soil_data:
                soil_data[param] = {'checks': {}}
            check_num = row['check_number']
            soil_data[param]['checks'][check_num] = row['value']
        water_check_count = len(next(iter(water_data.values()))['checks']) if water_data else 14
        soil_check_count = len(next(iter(soil_data.values()))['checks']) if soil_data else 8
        return render_template('edit_station.html',
            station=station_row,
            station_lat = station_row.get('lat'),
            station_lon = station_row.get('lon'),
            water_data=water_data,
            soil_data=soil_data,
            water_check_count=water_check_count,
            soil_check_count=soil_check_count)
    except Exception as e:
        print(f"ERROR in edit_station GET: {e}")
        import traceback
        traceback.print_exc()
        return f"Error loading edit form: {str(e)}", 500

# ค่าคงที่ — เพิ่มบนสุดของไฟล์ถัดจาก import
WATER_CHECK_COUNT = 17
SOIL_CHECK_COUNT  = 10

@app.route('/api/stations', methods=['POST'])
def api_add_station():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'ไม่พบข้อมูล JSON'}), 400

        station  = str(data.get('station',  '') or '').strip()
        river    = str(data.get('river',    '') or '').strip()
        tambon   = str(data.get('tambon',   '') or '').strip()
        amphoe   = str(data.get('amphoe',   '') or '').strip()
        province = str(data.get('province', '') or '').strip()
        location = str(data.get('location', '') or '').strip()

        lat_raw = data.get('lat')
        lon_raw = data.get('lon')
        try:
            lat = float(str(lat_raw).strip()) if lat_raw not in [None, '', 'null'] else None
        except (TypeError, ValueError):
            lat = None

        try:
            lon = float(str(lon_raw).strip()) if lon_raw not in [None, '', 'null'] else None
        except (TypeError, ValueError):
            lon = None

        print(f"📥 lat_raw={repr(lat_raw)} → lat={lat}")
        print(f"📥 lon_raw={repr(lon_raw)} → lon={lon}")

        if not station:
            return jsonify({'success': False, 'message': 'กรุณาระบุรหัสสถานี'}), 400

        conn = get_db()
        cur  = conn.cursor()

        # INSERT พร้อม lat, lon
        cur.execute("""
            INSERT INTO station_data
                (station, river, tambon, amphoe, province, location, lat, lon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station) DO UPDATE SET
                river    = EXCLUDED.river,
                tambon   = EXCLUDED.tambon,
                amphoe   = EXCLUDED.amphoe,
                province = EXCLUDED.province,
                location = EXCLUDED.location,
                lat      = EXCLUDED.lat,
                lon      = EXCLUDED.lon
        """, (station, river, tambon, amphoe, province, location, lat, lon))

        # บันทึกข้อมูลน้ำ
        water_count = 0
        for row in data.get('waterData', []):
            param = str(row.get('parameter', '') or '').strip()
            unit  = str(row.get('unit', '')      or '').strip()
            if not param:
                continue
            for i in range(1, WATER_CHECK_COUNT + 1):
                value = str(row.get(f'check{i}', '') or '').strip()
                if not value:
                    continue
                numeric_value = None
                if value not in ['-', 'ND']:
                    try:
                        numeric_value = 0.0 if value.startswith('<') else float(value)
                    except ValueError:
                        pass
                cur.execute("""
                    INSERT INTO water_data
                        (station, parameter, unit, location, check_number, value, numeric_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (station, param, unit, location, f'ครั้งที่ {i}', value, numeric_value))
                water_count += 1

        # บันทึกข้อมูลดิน
        soil_count = 0
        for row in data.get('soilData', []):
            param = str(row.get('parameter', '') or '').strip()
            if not param:
                continue
            for i in range(1, SOIL_CHECK_COUNT + 1):
                value = str(row.get(f'check{i}', '') or '').strip()
                if not value:
                    continue
                numeric_value = None
                if value not in ['-', 'ND']:
                    try:
                        numeric_value = 0.0 if value.startswith('<') else float(value)
                    except ValueError:
                        pass
                cur.execute("""
                    INSERT INTO soil_data
                        (station, parameter, location, check_number, value, numeric_value)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (station, param, location, f'ครั้งที่ {i}', value, numeric_value))
                soil_count += 1

        conn.commit()
        conn.close()

        print(f"✅ บันทึกสำเร็จ: {station} lat={lat} lon={lon} น้ำ={water_count} ดิน={soil_count}")
        return jsonify({'success': True, 'message': 'บันทึกสำเร็จ',
                        'station': station, 'lat': lat, 'lon': lon,
                        'water': water_count, 'soil': soil_count})

    except Exception as e:
        print(f"❌ api_add_station error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/env-test')
def env_test():
    return {
        'POSTGRES_HOST': os.environ.get('POSTGRES_HOST'),
        'DATABASE_URL': os.environ.get('DATABASE_URL')[:50] + '...' if os.environ.get('DATABASE_URL') else None
    }

@app.route('/check-env')
def check_env():
    return {
        "SUPABASE_DATABASE_URL": "SET" if os.environ.get('SUPABASE_DATABASE_URL') else "NOT SET",
        "SECRET_KEY": "SET" if os.environ.get('SECRET_KEY') else "NOT SET"
    }

# ===== ROUTE หน้าหลัก =====
@app.route('/')
def index():
    """หน้าหลัก: แสดงแผนที่และรายงานสถานีตรวจสอบ"""
    try:
        stations = get_stations()
        unique_rivers = sorted(list(set([s['river'] for s in stations if s['river']])))
        unique_provinces = sorted(list(set([s['province'] for s in stations if s['province']])))
        unique_tambons = sorted(list(set([s['tambon'] for s in stations if s['tambon']])))
        unique_amphoes = sorted(list(set([s['amphoe'] for s in stations if s['amphoe']])))
        location_hierarchy = {}
        for station in stations:
            prov = station.get('province', '')
            amph = station.get('amphoe', '')
            tamb = station.get('tambon', '')
            if prov and amph and tamb:
                if prov not in location_hierarchy:
                    location_hierarchy[prov] = {}
                if amph not in location_hierarchy[prov]:
                    location_hierarchy[prov][amph] = set()
                location_hierarchy[prov][amph].add(tamb)
        for prov in location_hierarchy:
            for amph in location_hierarchy[prov]:
                location_hierarchy[prov][amph] = sorted(list(location_hierarchy[prov][amph]))
        return render_template('mapandnews.html',
            stations=stations,
            unique_rivers=unique_rivers,
            unique_provinces=unique_provinces,
            unique_tambons=unique_tambons,
            unique_amphoes=unique_amphoes,
            location_hierarchy=location_hierarchy)
    except Exception as e:
        return f"Error loading page: {str(e)}", 500

# หน้าแสดงแผนที่และข่าว
@app.route('/map/<station_code>')
def map_page(station_code):
    station = get_station_by_code(station_code)
    if not station:
        return "ไม่พบสถานี", 404
    water_data = get_water_data(station_code)
    soil_data = get_soil_data(station_code)
    return render_template(
        'mapandnews.html',
        station=station,
        water_data=water_data,
        soil_data=soil_data
    )

# === API: Get All Monitoring Data for Map ===
@app.route('/api/map-data')
def api_map_data():
    """ส่งข้อมูลน้ำและดินทั้งหมดในรูปแบบ JSON สำหรับแผนที่"""
    try:
        conn = get_db()
        cur = conn.cursor()
        # ดึงรายการครั้งที่ตรวจที่มีข้อมูล
        cur.execute("""
        SELECT DISTINCT check_number
        FROM water_data
        ORDER BY NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST, check_number
        """)
        water_checks = [row['check_number'] for row in cur.fetchall()]
        cur.execute("""
        SELECT DISTINCT check_number
        FROM soil_data
        ORDER BY NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST, check_number
        """)
        soil_checks = [row['check_number'] for row in cur.fetchall()]
        cur.execute("SELECT DISTINCT parameter, unit FROM water_data ORDER BY parameter")
        water_params = {row['parameter']: row['unit'] for row in cur.fetchall()}
        cur.execute("SELECT DISTINCT parameter FROM soil_data ORDER BY parameter")
        soil_params = [row['parameter'] for row in cur.fetchall()]
        water_data = {}
        for param in water_params:
            water_data[param] = {}
            cur.execute("""
            SELECT station, check_number, numeric_value
            FROM water_data
            WHERE parameter = %s AND numeric_value IS NOT NULL
            ORDER BY station, NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST, check_number
            """, (param,))
            rows = cur.fetchall()
            for row in rows:
                st = row['station']
                val = row['numeric_value']
                if st not in water_data[param]:
                    water_data[param][st] = [None] * len(water_checks)
                try:
                    idx = water_checks.index(row['check_number'])
                    water_data[param][st][idx] = val
                except ValueError:
                    pass
        soil_data = {}
        for param in soil_params:
            soil_data[param] = {}
            cur.execute("""
            SELECT station, check_number, numeric_value
            FROM soil_data
            WHERE parameter = %s AND numeric_value IS NOT NULL
            ORDER BY station, NULLIF(REGEXP_REPLACE(check_number, '\D', '', 'g'), '')::INTEGER NULLS LAST, check_number
            """, (param,))
            rows = cur.fetchall()
            for row in rows:
                st = row['station']
                val = row['numeric_value']
                if st not in soil_data[param]:
                    soil_data[param][st] = [None] * len(soil_checks)
                try:
                    idx = soil_checks.index(row['check_number'])
                    soil_data[param][st][idx] = val
                except ValueError:
                    pass
        conn.close()
        return jsonify({
            'success': True,
            'water': {
                'check_numbers': water_checks,
                'parameters': water_params,
                'data': water_data
            },
            'soil': {
                'check_numbers': soil_checks,
                'parameters': soil_params,
                'data': soil_data
            }
        })
    except Exception as e:
        print(f"❌ API Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# === API: Get Latest Data for Map Chart ===
@app.route('/api/map-latest-data')
def api_map_latest_data():
    """ส่งข้อมูลค่าล่าสุดของแต่ละสถานี สำหรับแสดงกราฟ"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT parameter, unit FROM water_data ORDER BY parameter")
        water_params = {row['parameter']: row['unit'] for row in cur.fetchall()}
        cur.execute("SELECT DISTINCT parameter FROM soil_data ORDER BY parameter")
        soil_params = [row['parameter'] for row in cur.fetchall()]
        water_latest = {}
        for param in water_params:
            cur.execute("""
            SELECT DISTINCT ON (station)
                station, check_number, numeric_value, value
            FROM water_data
            WHERE parameter = %s AND numeric_value IS NOT NULL
            ORDER BY station,
                NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER DESC NULLS LAST,
                check_number DESC
            """, (param,))
            rows = cur.fetchall()
            water_latest[param] = {}
            for row in rows:
                if row['numeric_value'] is not None:
                    water_latest[param][row['station']] = {
                        'value': row['numeric_value'],
                        'raw_value': row['value'],
                        'check_number': row['check_number']
                    }
        soil_latest = {}
        for param in soil_params:
            cur.execute("""
            SELECT DISTINCT ON (station)
                station, check_number, numeric_value, value
            FROM soil_data
            WHERE parameter = %s AND numeric_value IS NOT NULL
            ORDER BY station,
                NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER DESC NULLS LAST,
                check_number DESC
            """, (param,))
            rows = cur.fetchall()
            soil_latest[param] = {}
            for row in rows:
                if row['numeric_value'] is not None:
                    soil_latest[param][row['station']] = {
                        'value': row['numeric_value'],
                        'raw_value': row['value'],
                        'check_number': row['check_number']
                    }
        conn.close()
        response = {
            'success': True,
            'water': {
                'parameters': water_params,
                'latest': water_latest
            },
            'soil': {
                'parameters': soil_params,
                'latest': soil_latest
            },
            'timestamp': datetime.now().isoformat()
        }
        return jsonify(response)
    except Exception as e:
        print(f"❌ API Latest Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/station-history')
def api_station_history():
    """ดึงข้อมูลย้อนหลังของแต่ละสถานี"""
    try:
        station_id = request.args.get('station')
        param_key = request.args.get('param')
        data_type = request.args.get('type', 'water')
        
        print(f"🔍 API Request: station={station_id}, param={param_key}, type={data_type}")
        
        if not station_id or not param_key:
            return jsonify({'success': False, 'error': 'กรุณาระบุสถานีและพารามิเตอร์'}), 400
        
        conn = get_db()
        cur = conn.cursor()
        
        # ✅ ใช้ตารางที่ถูกต้องตาม type
        if data_type == 'water':
            cur.execute("""
                SELECT check_number, numeric_value
                FROM water_data
                WHERE station = %s AND parameter = %s AND numeric_value IS NOT NULL
                ORDER BY NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER ASC
            """, (station_id, param_key))
        else:  # soil
            cur.execute("""
                SELECT check_number, numeric_value
                FROM soil_data
                WHERE station = %s AND parameter = %s AND numeric_value IS NOT NULL
                ORDER BY NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER ASC
            """, (station_id, param_key))
        
        rows = cur.fetchall()
        conn.close()
        
        # แปลงข้อมูลให้เป็นรูปแบบเดียวกัน (ทั้งน้ำและดิน)
        check_numbers = [row['check_number'] for row in rows]
        values = [float(row['numeric_value']) if row['numeric_value'] is not None else None for row in rows]
        
        print(f"📊 Found {len(rows)} records")
        print(f"📊 Check numbers: {check_numbers}")
        print(f"📊 Values: {values}")
        
        return jsonify({
            'success': True,
            'station': station_id,
            'parameter': param_key,
            'type': data_type,  # เพิ่ม type เพื่อ frontend รู้ว่าเป็นน้ำหรือดิน
            'check_numbers': check_numbers,
            'values': values
        })
        
    except Exception as e:
        print(f"❌ API Station History Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# === API: Get Latest Data by Tambon ===
@app.route('/api/latest-by-tambon')
def api_latest_by_tambon():
    """ดึงข้อมูลล่าสุดจัดกลุ่มตามตำบล + รายการรอบตรวจวัดทั้งหมด"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
        SELECT station, tambon, amphoe, province, river, location
        FROM station_data
        ORDER BY tambon, station
        """)
        stations = cur.fetchall()
        cur.execute("SELECT DISTINCT parameter, unit FROM water_data ORDER BY parameter")
        water_params = {row['parameter']: row['unit'] for row in cur.fetchall()}
        cur.execute("SELECT DISTINCT parameter FROM soil_data ORDER BY parameter")
        soil_params = [row['parameter'] for row in cur.fetchall()]
        cur.execute("""
        SELECT check_number
        FROM (
            SELECT DISTINCT check_number,
                NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER as sort_key
            FROM water_data
        ) AS temp
        ORDER BY sort_key DESC NULLS LAST, check_number DESC
        """)
        water_checks = [row['check_number'] for row in cur.fetchall()]
        cur.execute("""
        SELECT check_number
        FROM (
            SELECT DISTINCT check_number,
                NULLIF(REGEXP_REPLACE(check_number, '[^0-9]', '', 'g'), '')::INTEGER as sort_key
            FROM soil_data
        ) AS temp
        ORDER BY sort_key DESC NULLS LAST, check_number DESC
        """)
        soil_checks = [row['check_number'] for row in cur.fetchall()]
        water_latest_check = water_checks[0] if water_checks else None
        soil_latest_check = soil_checks[0] if soil_checks else None
        if not water_latest_check and not soil_latest_check:
            tambons = sorted(list(set(s['tambon'] for s in stations if s['tambon'])))
            conn.close()
            return jsonify({
                'success': True,
                'water_latest_check': None,
                'soil_latest_check': None,
                'tambons': tambons,
                'stations': stations,
                'water': {
                    'parameters': water_params,
                    'latest': {},
                    'check_numbers': []
                },
                'soil': {
                    'parameters': soil_params,
                    'latest': {},
                    'check_numbers': []
                }
            })
        water_latest = {}
        if water_latest_check:
            for param in water_params:
                water_latest[param] = {}
                cur.execute("""
                SELECT wd.station, sd.tambon, wd.numeric_value, wd.value, wd.check_number
                FROM water_data wd
                JOIN station_data sd ON wd.station = sd.station
                WHERE wd.parameter = %s AND wd.check_number = %s AND wd.numeric_value IS NOT NULL
                """, (param, water_latest_check))
                for row in cur.fetchall():
                    tambon = row['tambon'] or 'ไม่ระบุ'
                    if tambon not in water_latest[param]:
                        water_latest[param][tambon] = []
                    raw_val = row['value'] or ''
                    numeric_val = row['numeric_value']
                    prefix = ''
                    if raw_val.startswith('<'):
                        prefix = '<'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('<', '').strip())
                            except: numeric_val = 0.0
                    elif raw_val.startswith('>'):
                        prefix = '>'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('>', '').strip())
                            except: numeric_val = 0.0
                    water_latest[param][tambon].append({
                        'station': row['station'],
                        'value': float(numeric_val) if numeric_val is not None else None,
                        'raw_value': raw_val,
                        'prefix': prefix,
                        'check_number': row['check_number']
                    })
        soil_latest = {}
        if soil_latest_check:
            for param in soil_params:
                soil_latest[param] = {}
                cur.execute("""
                SELECT sd.station, st.tambon, sd.numeric_value, sd.value, sd.check_number
                FROM soil_data sd
                JOIN station_data st ON sd.station = st.station
                WHERE sd.parameter = %s AND sd.check_number = %s AND sd.numeric_value IS NOT NULL
                """, (param, soil_latest_check))
                for row in cur.fetchall():
                    tambon = row['tambon'] or 'ไม่ระบุ'
                    if tambon not in soil_latest[param]:
                        soil_latest[param][tambon] = []
                    raw_val = row['value'] or ''
                    numeric_val = row['numeric_value']
                    prefix = ''
                    if raw_val.startswith('<'):
                        prefix = '<'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('<', '').strip())
                            except: numeric_val = 0.0
                    elif raw_val.startswith('>'):
                        prefix = '>'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('>', '').strip())
                            except: numeric_val = 0.0
                    soil_latest[param][tambon].append({
                        'station': row['station'],
                        'value': float(numeric_val) if numeric_val is not None else None,
                        'raw_value': raw_val,
                        'prefix': prefix,
                        'check_number': row['check_number']
                    })
        tambons = sorted(list(set(s['tambon'] for s in stations if s['tambon'])))
        conn.close()
        return jsonify({
            'success': True,
            'water_latest_check': water_latest_check,
            'soil_latest_check': soil_latest_check,
            'tambons': tambons,
            'stations': stations,
            'water': {
                'parameters': water_params,
                'latest': water_latest,
                'check_numbers': water_checks
            },
            'soil': {
                'parameters': soil_params,
                'latest': soil_latest,
                'check_numbers': soil_checks
            }
        })
    except Exception as e:
        print(f"❌ API Tambon Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# === API: Get Data by Check Number ===
@app.route('/api/data-by-check')
def api_data_by_check():
    """ดึงข้อมูลตามรอบตรวจวัดที่ระบุ"""
    try:
        check_number = request.args.get('check_number')
        data_type = request.args.get('type', 'water')
        if not check_number:
            return jsonify({'success': False, 'error': 'กรุณาระบุรอบตรวจวัด'}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
        SELECT station, tambon, amphoe, province, river, location
        FROM station_data
        ORDER BY tambon, station
        """)
        stations = cur.fetchall()
        if data_type == 'water':
            cur.execute("SELECT DISTINCT parameter, unit FROM water_data ORDER BY parameter")
            params = {row['parameter']: row['unit'] for row in cur.fetchall()}
            data_by_param = {}
            for param in params:
                data_by_param[param] = {}
                cur.execute("""
                SELECT wd.station, sd.tambon, wd.numeric_value, wd.value, wd.check_number
                FROM water_data wd
                JOIN station_data sd ON wd.station = sd.station
                WHERE wd.parameter = %s
                AND wd.check_number = %s
                AND wd.numeric_value IS NOT NULL
                """, (param, check_number))
                for row in cur.fetchall():
                    tambon = row['tambon'] or 'ไม่ระบุ'
                    if tambon not in data_by_param[param]:
                        data_by_param[param][tambon] = []
                    raw_val = row['value'] or ''
                    numeric_val = row['numeric_value']
                    prefix = ''
                    if raw_val.startswith('<'):
                        prefix = '<'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('<', '').strip())
                            except: numeric_val = 0.0
                    elif raw_val.startswith('>'):
                        prefix = '>'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('>', '').strip())
                            except: numeric_val = 0.0
                    data_by_param[param][tambon].append({
                        'station': row['station'],
                        'value': float(numeric_val) if numeric_val is not None else None,
                        'raw_value': raw_val,
                        'prefix': prefix,
                        'check_number': row['check_number']
                    })
        else:
            cur.execute("SELECT DISTINCT parameter FROM soil_data ORDER BY parameter")
            params = [row['parameter'] for row in cur.fetchall()]
            data_by_param = {}
            for param in params:
                data_by_param[param] = {}
                cur.execute("""
                SELECT sd.station, st.tambon, sd.numeric_value, sd.value, sd.check_number
                FROM soil_data sd
                JOIN station_data st ON sd.station = st.station
                WHERE sd.parameter = %s
                AND sd.check_number = %s
                AND sd.numeric_value IS NOT NULL
                """, (param, check_number))
                for row in cur.fetchall():
                    tambon = row['tambon'] or 'ไม่ระบุ'
                    if tambon not in data_by_param[param]:
                        data_by_param[param][tambon] = []
                    raw_val = row['value'] or ''
                    numeric_val = row['numeric_value']
                    prefix = ''
                    if raw_val.startswith('<'):
                        prefix = '<'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('<', '').strip())
                            except: numeric_val = 0.0
                    elif raw_val.startswith('>'):
                        prefix = '>'
                        if numeric_val is None:
                            try: numeric_val = float(raw_val.replace('>', '').strip())
                            except: numeric_val = 0.0
                    data_by_param[param][tambon].append({
                        'station': row['station'],
                        'value': float(numeric_val) if numeric_val is not None else None,
                        'raw_value': raw_val,
                        'prefix': prefix,
                        'check_number': row['check_number']
                    })
        tambons = sorted(list(set(s['tambon'] for s in stations if s['tambon'])))
        conn.close()
        return jsonify({
            'success': True,
            'check_number': check_number,
            'type': data_type,
            'tambons': tambons,
            'stations': stations,
            'data': data_by_param,
            'parameters': params
        })
    except Exception as e:
        print(f"❌ API Data By Check Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stations-list')
def get_stations_list():
    try:
        conn = get_db()
        cur  = conn.cursor()
        # เพิ่ม lat, lon ใน SELECT
        cur.execute("""
            SELECT station, river, tambon, amphoe, province, location, lat, lon
            FROM station_data
            ORDER BY river, station
        """)
        rows = cur.fetchall()
        conn.close()

        stations = []
        for row in rows:
            river  = row['river']  or ''
            tambon = row['tambon'] or ''
            stations.append({
                'id':       row['station'],
                'name':     f"{river} ({tambon})" if river else row['station'],
                'river':    river,
                'tambon':   tambon,
                'amphoe':   row['amphoe']   or '',
                'province': row['province'] or '',
                'lat': float(row['lat']) if row['lat'] is not None else None,  
                'lon': float(row['lon']) if row['lon'] is not None else None,  
            })

        return jsonify({'success': True, 'stations': stations, 'count': len(stations)})

    except Exception as e:
        print(f"❌ stations-list error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    
# เพิ่มตัวแปร Hardcode ข่าว (แทน Database)
HARDCODED_NEWS = [
    {
        'id': 1,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'กรมควบคุมมลพิษ เผยคุณภาพน้ำ "แม่น้ำกก-สาย-รวก-โขง" ครั้งที่ 17 พบส่วนใหญ่ยังอยู่ในเกณฑ์ ยกเว้นบางจุดพบสารหนูเกินมาตรฐาน',
        'excerpt': 'กรมควบคุมมลพิษ เผยคุณภาพน้ำ "แม่น้ำกก-สาย-รวก-โขง" ครั้งที่ 17 พบส่วนใหญ่ยังอยู่ในเกณฑ์ ยกเว้นบางจุดพบสารหนูเกินมาตรฐาน #คุณภาพน้ำ #สารปนเปื้อน #สารหนู',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMixwdBVV95cUxNdG5ieWVMZ3BIN285d0c5cnJBY2xaZklpWlRwdGxUSXZ4RDlMZ2xnVC1COXhaMUYtZkRDT2tWeWgxOUkzZ29DVnRQcDBBSUNBa05kYkl5NUNSRThoSkVHVWQwMUVCLXZaQXlIN0lub29yb2Fmdi1HODFWeUMycjRBSWttU3BHa2s4Mi1aRnRvTjlwTzNmbUZYVGV2LS15NjZmWU0xY24yQW5ya3haUzFIVm5mbGVrXzZlR1BIUV91MFctNDZyS3dQSUJ4ZmpRa2ZHWUhJVGJQMXVJM0EzLTJWU1RCcjlNd05sYXFTa0dpT01hR1dQay1QbGg2NXZCcjJ3S0I5bzZod3JiTndwNHMyYWtVdXk0cjd2MWZaay0wTVlmZkVEUk9ad0tJZ1lzelhFcDFISmtjT3c4MVlvMVF6cjBOc0RXbVcxM1I4RVVfMzk0R1VPbmRXbngzV2xPdTh1MDFCc2tLMm9TVVRCRVMxZ3ZWU2dxZ3dhLVpoN3BzZVpmSjc5UWhIV19WVDM0ejhpM2ZrcGVXYl90em8wVnh5Q0lsMlczWVFBc3F3TlVWUHR1T1R4Yzl4Q011V1pPLXR4LUhZaUJRQmRlV3Y5R2l6N21jTFcxRFdiYXBNYXlkUURCQm93R2VaVUtVWEZPWXBwLXVkVUxIcktyaWIzZ2NXM2cxNk05WDZNRFlpNFJZM3lTTnkzVUUxMVFZN1didGRmRXhkaC1RTllhamZoQk5ORzF3Z2dPTGt1Qi1oVHNMZmNlZjFkdEFZVnVpWGdMTFlBWm1pVEJKTUxaR2xmSV9fYUs4Q3JRSnlJQWRjNUxscUZ6aDZFU29ZX3kydTd2YTEzSDBmN2RPc2U0eDk4UEpUSHRENHQ1ZVJTemZmcUpsTklQVkRRTVBOM0FuMkNVMTZzTGxtTE52Vi1kamo3NWdEdmVLT3VGdzJBeEY5SklrLTJaREc2WHM5cGxETnFSZkh2dDBnaWs5cUg0amRGdUFOR0lLRmxPOUNfamp6aXF0RjBxaHFOU29WaWFGNDc0cV9qbTQ5YktVcEs1UTExdGlhTmtZaF9GY19IYUxKQ2psZlV1STJWdjc5dDFHVHFyUjhHUWRmQmpWd0tMRjU5ZENaUzBDX0V5RFpJbmRaTXp4Z2UtRFh0WEhfRXJyMndaa0dwd1A1Si10QVp0d0syMEZpRWpVWnZpTFJ0TmRBTHdTd2d2Wkk1dEhEVEFrMFBNTkNBSkJzX19fN1Y0QVFBMWtIT0I2eFhIbHBHb0JYWkktOGJGNTI5QU9F?oc=5',
        'sourceKey': 'fb_1',
        'pubDate': 'Mon, 30 Mar 2026',
    },
    {
        'id': 2,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ตรวจคุณภาพน้ำ \'แม่น้ำกก-สาย-รวก-โขง\' ครั้งที่ 17 ส่วนใหญ่อยู่ในเกณฑ์ ยังมีจุดพบสารหนู',
        'excerpt': 'กรมควบคุมมลพิษ รายงานผลการติดตามตรวจสอบคุณภาพน้ำและตะกอนดินในแม่น้ำกก ลำน้ำสาขา แม่น้ำสาย แม่น้ำรวก และแม่น้ำโขง ครอบคลุมพื้นที่จังหวัดเชียงใหม่และเชียงราย ครั้งที่ 17 พบว่าส่วนใหญ่อยู่ในเกณฑ์มาตรฐาน แต่ยังมีบางจุดพบสารหนู',
        'source': 'Matichon Online',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWEFVX3lxTE1rRlFwRGJwaGZHS19HUlhiOVBUcENWbDhDR1RIcTFVd1dJLXZrclBjQjBHMm1oR1R4SEUza18xRlVUSUczeTM2US16Q0pEeG1hQzJ1VWlFSUQ?oc=5',
        'sourceKey': 'matichon_1',
        'pubDate': 'Mon, 30 Mar 2026',
    },
    {
        'id': 3,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'มลพิษอื้อทั้งในน้ำ-อากาศ คนเชียงรายเผชิญทุกข์หนัก พบสารพิษเกินมาตรฐานในแม่น้ำกก ค่าฝุ่นสูงลิ่ว',
        'excerpt': 'กรมควบคุมมลพิษรายงานสถานการณ์คุณภาพน้ำในแม่น้ำกกและลำน้ำสาขา ครั้งที่ 17 พบสารหนู (As) บริเวณสะพานท่าตอน จ.เชียงใหม่ และสะพานมิตรภาพ แม่ยาว-ดอยฮาง จ.เชียงราย มีค่าอยู่ที่ 0.011 มก./ล. (ค่ามาตรฐาน 0.01 มก./ล.) ขณะที่ค่าฝุ่น PM2.5 ก็สูงเกินเกณฑ์',
        'source': 'Thaipost.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE5HbEFmaTZRWlB4TXFRNjZLTEJJcndkc1JrZFpIUll2ZmdWM1l5SnJPaENoRWh5QmpxRHFIeGFoUGNSQnUzbUNPMkRRRHdfcUpVdU96SUMxWENiUQ?oc=5',
        'sourceKey': 'thaipost_1',
        'pubDate': 'Mon, 30 Mar 2026',
    },
    {
        'id': 4,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. เผย แม่น้ำกก-สาย-รวก-โขง ยังพบสารโลหะหนักเกินมาตรฐาน | The Reporters',
        'excerpt': 'กรมควบคุมมลพิษ รายงานสถานการณ์คุณภาพน้ำ ครั้งที่ 17 ในแม่น้ำกก สาย รวก โขง ยังคงพบสารโลหะหนักเกินมาตรฐาน โดยเฉพาะสารหนูในบางพื้นที่ของเชียงใหม่และเชียงราย',
        'source': 'LINE TODAY',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVkFVX3lxTFBSWGhKazY0VXM5SERnSG5zM3dGdWQxc1pHVlJQVDNJRDBBY24zbnYwUHVVeUpCQTRkSDBWeEw4eEZQT191WkhQbkdkZEdUc1hKOXpEOTNR?oc=5',
        'sourceKey': 'linetoday_1',
        'pubDate': 'Mon, 30 Mar 2026',
    },
    {
        'id': 5,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'พ่อค้าแม่ขายหลังเทศกาลสงกรานต์หวังดึงนักท่องเที่ยวกลับคืนริมน้ำกก กู้เงินปรับปรุงร้าน นายกอบจ.ให้ความมั่นใจนักท่องเที่ยวปลอดภัย',
        'excerpt': 'ผู้ประกอบการริมหาดเชียงรายหวังดึงนักท่องเที่ยวกลับคืนหลังเทศกาลสงกรานต์ หลังจาก 1 ปีที่ผ่านมาแหล่งท่องเที่ยวริมแม่น้ำกกเงียบเหงา เพราะสารโลหะหนักปนเปื้อนเกินมาตรฐาน บางรายกู้เงินปรับปรุงร้าน ขณะที่นายก อบจ. ยืนยันนักท่องเที่ยวปลอดภัย',
        'source': 'transbordernews.in.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVkFVX3lxTE9fTFpobjJtclFrYWNYMFg5TjEwekpjUXZscVp6UUxXMVVIU01xSkkyZE1IRF94cGxveXE1d2EwSy0tUDRmOG5qTTRHMDdaWkE2clliam9n?oc=5',
        'sourceKey': 'transborder_1',
        'pubDate': 'Wed, 01 Apr 2026',
    },
    {
        'id': 6,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': '"แม่น้ำกก" กำลังป่วย อย่าปล่อยให้วิกฤตสารพิษ คร่าชีวิตทั้งคนและสัตว์',
        'excerpt': 'แม่น้ำกก สายน้ำสำคัญที่หล่อเลี้ยงชีวิตผู้คนในเชียงรายและเชียงใหม่ กำลังส่งสัญญาณขอความช่วยเหลืออย่างชัดเจนที่สุดในรอบหลายทศวรรษ ด้วยภาวะปนเปื้อนของโลหะหนัก ทั้งสารหนู ตะกั่ว และแร่หายาก',
        'source': 'topnews.co.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiT0FVX3lxTE5zYmozcDNGOGYwbzh6eDlsNnZsOU9UbnZTR1REeVhMM2xnNklnNk8yZmlLSThDd3Mzd1cySnFYSThsVE13MDhtazdycllwa3M?oc=5',
        'sourceKey': 'topnews_1',
        'pubDate': 'Wed, 25 Mar 2026',
    },
    {
        'id': 7,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. เปิดผลตรวจคุณภาพน้ำล่าสุด \'แม่น้ำกก-สาย-โขง\' ยังพบ \'สารหนู\' พุ่งเกินเกณฑ์',
        'excerpt': 'กรมควบคุมมลพิษ รายงานผลการติดตามตรวจสอบคุณภาพน้ำและตะกอนดินในแม่น้ำกก แม่น้ำสาย และแม่น้ำโขง ครั้งที่ 16 พบสารหนูยังคงพุ่งเกินค่ามาตรฐานในหลายจุด',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE9GY0g0WFJaMFNmZGN6YllncDBMd0o1WG1TMzkwZGlmTFVCWlhzMnVHemlWRE15d3BBdkNYa1VfSW9ZOG90YWdlbkdJX0lpdWRTVkRDand1cXJ0eUZMMW91MEdwaFRDUQ?oc=5',
        'sourceKey': 'bkbiz_1',
        'pubDate': 'Fri, 20 Mar 2026',
    },
    {
        'id': 8,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'เปิดตัวแอปพลิเคชันแก้ปัญหา "น้ำกก"',
        'excerpt': 'ความพยายามแก้ไขปัญหาสารปนเปื้อนในแม่น้ำกกพื้นที่ภาคเหนืออย่างต่อเนื่อง โดยล่าสุดมีการเปิดตัวแอปพลิเคชันเตือนภัยที่จะช่วยวิเคราะห์ความปลอดภัยของ "ปลา" ในแม่น้ำกก แจ้งพิกัดให้ผู้บริโภคตรวจสอบได้ก่อนซื้อ',
        'source': 'Thai PBS',
        'externalLink': 'https://news.google.com/rss/articles/CBMicEFVX3lxTFBRWEJCbDRXSFc5VnpjLTg1OGxhT09ydzFZY2Z2T3g4R1pOSWltZnZDNnV2czdBQVVtOGNnS2FfTjhsUEJqZ2xwazFrdk96WkZRa2ZVT1FmOWI0enZBeldlTDhrR05oZTE2NUVuOHZrLWw?oc=5',
        'sourceKey': 'thaipbs_1',
        'pubDate': 'Tue, 31 Mar 2026',
    },
    {
        'id': 9,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. รายงานคุณภาพน้ำ \'แม่น้ำกก-สาย-รวก-โขง\' ครั้งที่ 16 พบสารหนูเกินมาตรฐานบางจุด',
        'excerpt': 'กรมควบคุมมลพิษ รายงานผลคุณภาพน้ำ ครั้งที่ 16 ในแม่น้ำกก สาย รวก และโขง พบสารหนูเกินมาตรฐานบางจุด พร้อมเฝ้าระวังอย่างต่อเนื่อง',
        'source': 'thansettakij',
        'externalLink': 'https://news.google.com/rss/articles/CBMia0FVX3lxTFBHZmdCWXV3Vzl6a2o1UjExRDctVnZJaTRuSUpENG96N0d4TDFKRkJJUmRmSnNlbFZhSUFqZm03OUxCUHpQVk5GSHZNbXZGbHo3VlZpZzBTaVFxbWFDSmNYa1N1VTR3d1BwQm9B?oc=5',
        'sourceKey': 'thansettakij_1',
        'pubDate': 'Wed, 25 Mar 2026',
    },
    {
        'id': 10,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกก: ชีวิตชาวเชียงราย เมื่อน้ำที่มีสารหนูถูกสูบมาทำน้ำประปา',
        'excerpt': 'เกือบปีแล้วที่ประชาชนในภาคเหนือผู้พึ่งพิงลุ่มแม่น้ำกก สาย รวก โขง ต้องทนใช้น้ำที่ปนเปื้อนโลหะหนัก ถึงแม้ทางการบอกว่าเกณฑ์การปนเปื้อนยังอยู่ในค่ามาตรฐานก็ตาม',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTFBHNi12RXA5YThKNUhHV3p1YUJmbzBEZThYTFpmcjNaSkV1SXpscExWUjdUNEQ2azZrejhGRmxudEcxVlEwY3FtTE5GQ3ZzN2g1cE90TzJDMlN5Z9IBX0FVX3lxTE5SNlllZmtDUjgtd0h1Mm9QcmlsWDRjRGVTMmQxMzFnNDJfWDVsZFk2QnlaTllZVWVZLTRULXhRdWpqcmxHakQ2SkQ5YlJnTVRTNW1UT2lJSFdQZnNINEtn?oc=5',
        'sourceKey': 'bbc_1',
        'pubDate': 'Wed, 03 Dec 2025',
    },
    {
        'id': 11,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': '"ผศ.สิตางศุ์" ฉงนข้อมูลหน่วยงานรัฐ สถานการณ์ปนเปื้อนสารพิษแม่น้ำกก อาจกระจายไกลถึงกทม.',
        'excerpt': '"ผศ.สิตางศุ์" ฉงนข้อมูลหน่วยงานรัฐ สถานการณ์ปนเปื้อนสารพิษแม่น้ำกกดีขึ้น ทั้งที่ยังไม่ได้จัดการต้นเหตุปัญหา พร้อมชี้สารพิษเข้าสู่ห่วงโซ่อาหารอาจกระจายไกลถึงกทม.',
        'source': 'ข่าวสด',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTFBQT2pQUUJIZUR0bWxqbDdMUTgxTnBrMEJTcXNIRmtUUDBiS3Mwa1V5OVN4UVU0N0NWYkROUzdyN0lIaFJzZHZoQWpiSXFJaHRINzBWM3hrUlV0S0stUFlaWEcxV0hQUQ?oc=5',
        'sourceKey': 'khaosod_1',
        'pubDate': 'Wed, 11 Mar 2026',
    },
    {
        'id': 12,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'สธ.-คพ.แถลงตรวจพบ "สารหนูแม่น้ำกก" ไม่อันตราย ส่วนคร.พบ 7 คนอาจเชื่อมโยงอาชีพเกษตร',
        'excerpt': 'กระทรวงสาธารณสุขและกรมควบคุมมลพิษแถลงผลตรวจสารหนูในแม่น้ำกก ระบุไม่อันตราย ขณะที่กรมควบคุมโรคพบ 7 คนอาจมีความเชื่อมโยงกับอาชีพเกษตรกรรม',
        'source': 'Hfocus.org',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE1LSl85b3NDa0VrdF9EcFg2V0dnR2VCZ1pfQjVSdkg5NlJwNm1ZTTZ6RGgtam9rTHFtVzVCWDkyOG1teWZmeXFwVUtIUFcxNFIzb2Y0ODVoNA?oc=5',
        'sourceKey': 'hfocus_1',
        'pubDate': 'Thu, 26 Feb 2026',
    },
    {
        'id': 13,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ค่าสารหนูน้ำกก-น้ำสาย-น้ำโขงประจำเดือน ก.พ. พุ่งขึ้น คพ.เผยมีค่าความขุ่นสูงในบางพื้นที่',
        'excerpt': 'กรมควบคุมมลพิษ รายงานผลการติดตามตรวจสอบคุณภาพน้ำ พบค่าสารหนูในแม่น้ำกก น้ำสาย และน้ำโขงประจำเดือนกุมภาพันธ์พุ่งขึ้น มีค่าความขุ่นสูงในบางพื้นที่ แนะนำหากจำเป็นต้องใช้น้ำควรผ่านกระบวนการปรับปรุงคุณภาพน้ำก่อน',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMihwdBVV95cUxObXRTbHA2WVVxc3BRUXZVV051VzlFN2w0V2IyNjdINGxNbXplWEtWWkF6UGhlcE1xQWJSWEt5ZTYxT1ZiYURQb3BRa1c0WWJNNnZRV2tCa3dGMVJJaGx2aUE3bWZSQUJybDU5YUk1ODZTaEJFbXhLTS1SLU5nUUNnSThZU0ZCUnJsOE9jRm9uV29zY1JiaVhUMUVzXzdySXN4Q2VDcmZzdEF1MVJYYThuT1UwSGVPLXVzZzc5a0UwOVlldzI2N3dSUjZicjBYemJySVhqSTdOZFF1OHRYb2Q2SXBMNUwtX1c0MHZHZVY4ckJyRE9IZjhFMGtBcW1xbmRZcEN2OElsRGg2eDNtZXpWcEJVR2FweDVkTTkzNnlLSG03TllpLUtDcmJHSDhpQ3NMZGl1Z2h0ZTdBdEdieGlQVkp1bkdMdGhkN19KeFpNSjhPcllKUmk1d3VJbDgyQzhITUE2dlc4cUdXRDlReThiY09kdF93dGs5U2hrS0NrclltMVJSRzNfZVZvUzExTWM0WjRxS3BBSHQ2RjNrdUhyUHRKdnFnVDN3VWNaV0FDZ0FCeDc3UWRCWFlUVzc4SkNsTjZySE1hRnctc1M5VEZUYzB5Ty11Q2R3QmJIZS1SS05UX0N2emVOdUlROWpRdHRjbm42OFJtVHZ3bDJNakR2YnlrTU9uRzdZMV9iSGdmdHZHZG5jdFNHSk1uY3pBSVdRYk5fNEcxTC1MTzNOV1ZLSkhDaU03LWxBMUVFeFdKY2gweEItNU01S1FXVTEyTzlQUXpiVExfdzdtUm9IU3gxSTIwWGNfLTF6UDhkQnk4OHA2Rk1Oc1lkYmdhT3ZGQW53X1NQenVKZE40dndGTGJXRXl5MFVGOGRkZEpFZnNIT29VNnJleXJvaWxxTU5pWnBva3hqLUhLem5qdHRRNDBMX0dPUGNySmFOalZPamQ2UnJEV2RxX3phQ3E2MnpmQjR1eEhOY1FaMFlfMEV3dUlYWV9ZeUJRbWZ4c2hSLWVVakdVODZpOFgxT3NwX3JGU3o2U0pmNzZHNkg2a1d3X1VfZS1KVXAwSl9LUjNtQ0VVTlk0dTFvX3lmSllOSGpUNnBOcHJYeXgxekpqN0lGNUhUd0x2RW1PQS1pVHRUSi1vMFNmczl1VVYzZUFhRGo4NUxpVHBiZUlfXzFFTXY3T1ZJaHh1WnNoWGs?oc=5',
        'sourceKey': 'fb_2',
        'pubDate': 'Fri, 20 Mar 2026',
    },
    {
        'id': 14,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'เชียงรายถกเครียดสารปนเปื้อนแม่น้ำกก ผลวิจัยพบสารหนูในเล็บ-เส้นผม ผวจ.สั่งตรวจซ้ำ',
        'excerpt': 'การประชุมถกเถียงปัญหาสารปนเปื้อนในแม่น้ำกกที่เชียงราย หลังผลวิจัยพบสารหนูสะสมในเล็บและเส้นผมของชาวบ้าน ผู้ว่าราชการจังหวัดสั่งให้ตรวจซ้ำเพื่อยืนยันผล',
        'source': 'Thaipost.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE15QWk0OHVDVTUyZnVndm5UblpUcXF4dzczS0xKXzFQNkRJMmhLb3NrcERrd0dpTmdmSDhkZUdSbkxFeENYdDM1V0VmZVhZLWpWdGFWeGpWdjRwUQ?oc=5',
        'sourceKey': 'thaipost_2',
        'pubDate': 'Thu, 26 Feb 2026',
    },
    {
        'id': 15,
        'type': 'normal',
        'badge': '💰 งบประมาณ',
        'badgeClass': 'badge-primary',
        'title': 'ครม. แก้ปัญหามลพิษแม่น้ำสาย-แม่น้ำกก ทุ่ม 188 ล้าน ฟื้นฟูคุณภาพน้ำ',
        'excerpt': 'คณะรัฐมนตรีอนุมัติงบประมาณ 188 ล้านบาท เพื่อแก้ปัญหามลพิษในแม่น้ำสายและแม่น้ำกก มุ่งฟื้นฟูคุณภาพน้ำให้กลับสู่มาตรฐาน',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE12T0czSHc5MXhGaDd4WWdKQzl3ZUJST3pKU2R6andPdmFJQ0xoYWdQVDBYUWZWNE9NTERXWEFod2tLUnoyTE1mZ3h1NDJFWFhvUktDdWxTdnM4ZkhxOC0xMzBPaDBXQQ?oc=5',
        'sourceKey': 'bkbiz_2',
        'pubDate': 'Tue, 17 Mar 2026',
    },
    {
        'id': 16,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'ปลัด สธ. สั่งติดตามกลุ่มเสี่ยงริมแม่น้ำกก หลังวิจัยพบ "สารหนู" ในร่างกายคน พร้อมเฝ้าระวังคุณภาพน้ำประปา',
        'excerpt': 'ปลัดกระทรวงสาธารณสุขสั่งติดตามกลุ่มเสี่ยงที่อาศัยริมแม่น้ำกก หลังผลวิจัยพบสารหนูในร่างกายประชาชน พร้อมสั่งการเฝ้าระวังคุณภาพน้ำประปาอย่างเข้มงวด',
        'source': 'Hfocus.org',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE95Sy1Dd0lRZjNaZ0hzVklzS0xQb1dNb1dEZmoxVm9NUkZpSExBSWlUbTRFU0lsdXJ4Tno2cGlrT0hkdU5wYWpycTJlLTBneGdHZmFSVEdEZw?oc=5',
        'sourceKey': 'hfocus_2',
        'pubDate': 'Wed, 25 Feb 2026',
    },
    {
        'id': 17,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'ทีมวิจัยสารหนูแม่น้ำกก ชงออกประกาศ "พิษจากสารหนู" เป็นโรคต้องเฝ้าระวังจากการประกอบอาชีพ',
        'excerpt': 'ทีมวิจัยเสนอให้กระทรวงสาธารณสุขออกประกาศให้ "พิษจากสารหนู" เป็นโรคที่ต้องเฝ้าระวังจากการประกอบอาชีพ หลังพบชาวบ้านริมแม่น้ำกกมีสารหนูสะสมในร่างกาย',
        'source': 'Hfocus.org',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE9kT0U4Nlpuc0Vld25lMjRVaGxaaFM4cnphM0RUaENmZzVreko0cnJWdXpiWjdHVW5iR294b1I0ZzNwMzRCRzJGX0hkR3RlWGZZZ0NjY3Bwdw?oc=5',
        'sourceKey': 'hfocus_3',
        'pubDate': 'Mon, 09 Mar 2026',
    },
    {
        'id': 18,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ครม.รับทราบผลแก้มลพิษข้ามพรมแดน "แม่น้ำกก-แม่น้ำสาย"',
        'excerpt': 'คณะรัฐมนตรีรับทราบรายงานผลการดำเนินการแก้ไขปัญหามลพิษข้ามพรมแดนในแม่น้ำกกและแม่น้ำสาย พร้อมสั่งการให้หน่วยงานที่เกี่ยวข้องเร่งดำเนินการต่อ',
        'source': 'ผู้จัดการออนไลน์',
        'externalLink': 'https://news.google.com/rss/articles/CBMiYEFVX3lxTE8yWHFkWGE0SWR4VUpuSTloblB5UU5ydFZjRjd3YUlVaWxxRHUzaFFrOEd1NzJaUk9xcXVxV3BCV3p0T3dnbVd6MlRncGROSVZvVU5pdmpvNDJXY1V6T3N1eQ?oc=5',
        'sourceKey': 'mgr_1',
        'pubDate': 'Tue, 17 Mar 2026',
    },
    {
        'id': 19,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'พบสารหนูสะสมในเล็บชาวบ้านพื้นที่เสี่ยงสารพิษปนเปื้อนลุ่มน้ำกก น่ากังวลอย่างไรในทางการแพทย์?',
        'excerpt': 'BBC รายงานผลการวิจัยพบสารหนูสะสมในเล็บของชาวบ้านที่อาศัยในพื้นที่เสี่ยงตามลุ่มน้ำกก พร้อมวิเคราะห์ผลกระทบต่อสุขภาพในเชิงการแพทย์',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE9rNGxyVGU4bTZqWHNYdzhQamlZYnNuQ2ZfZnlpTHVSalJzOHBPb0tHc2ZPMzNmTUJqcWdFa0hBOUdjUkg1UnhOR21oMzRJbk9wMnU0S21BNVhlQdIBX0FVX3lxTE1QMUNoSkE1eWRidzFydElTZ1dmTld6dG1FWk4xQk95NlFJYUI4dlZ2SzlvTDEybUg5TFhNZlpzbU1uSklBNFV6S09DNjZYend4bGZOTzNxNlBZcmRvNVE0?oc=5',
        'sourceKey': 'bbc_2',
        'pubDate': 'Wed, 25 Feb 2026',
    },
    {
        'id': 20,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'กรมควบคุมมลพิษ โชว์ตรวจสารพิษแม่น้ำกก จ.เชียงราย อยู่ในเกณฑ์มาตรฐาน แนะวิธีกินปลา',
        'excerpt': 'กรมควบคุมมลพิษออกมาแสดงผลการตรวจสารพิษในแม่น้ำกก จ.เชียงราย ระบุอยู่ในเกณฑ์มาตรฐาน พร้อมแนะนำวิธีการรับประทานปลาอย่างปลอดภัย',
        'source': 'ข่าวสด',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE8xMkNKTmR0N2hlSEg3bG9xQkJwbTgxMW9iRVZUbmdZVGJuUFFkTVdLNWN1czNKM1hBTXdUeGxTUzhpQ2g1d2wxbFJHb2w0X2lnWUdjM1dzVlBzTUtyNXI2QjZVdi0wUQ?oc=5',
        'sourceKey': 'khaosod_2',
        'pubDate': 'Tue, 24 Feb 2026',
    },
    {
        'id': 21,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. เกาะติดปัญหาสารหนูแม่น้ำกก พบเกินมาตรฐานเล็กน้อยบางจุด ชี้ส่วนใหญ่ยังอยู่ในเกณฑ์มาตรฐาน',
        'excerpt': 'กรมควบคุมมลพิษติดตามปัญหาสารหนูในแม่น้ำกก พบเกินมาตรฐานเล็กน้อยบางจุดพื้นที่สะพานท่าตอนและบ้านแม่นาวาง จ.เชียงใหม่ ชี้ส่วนใหญ่ยังอยู่ในเกณฑ์ ด้านกรมอนามัยแนะใช้น้ำประปาที่ผ่านการตรวจสอบ',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMi0wdBVV95cUxOaGE3WXZ3eVludURJLS1kVW9PYVZGc3RZLUZPSlJ2QWtPcTZ3bFpLZWdGdWFpS1VaemRjZDM5ajZBekxic2NlbkpUUTQxakhkSFpDTHlpMDhqSnpxV05vSWhrcWpzSTAxRDBZcmhUYXFNaDllcjZkWERnMjgzWGYya0FHLTQ3TlVITzJpLUkxZ2tqNk8tWmtfTUlPQnV4aktVWE10UDB1QV9GdVUyMmZlLUJiVzJfSzhVMXBMQzd4ZE80WU5xNVMwbjNVczVycWNWdVpfWE0tQ3pHcWJRVk9Id1RsNjZ1SGdRYUcxM1ZxdlVXVnhDZVVQdlV3QWVpd2RvQ0hQMFpNNkZKU050X1BhcmFXdTdzS3o1Zm1mQ21IZlUzTmRGRTl2TXItU2tWZDZzZzdaa3BaZ1pIVTZxdk1qckRrb2g0WGlCMUlCSjE3RHFXNmpjRlk3QU81eUtCWmFKc0FyYmZ3MUlsU3F0c2dfbEdIckVDWUZEN1JtOE94NEp1R2dfaUxKNzFMdmcycGxJX0FuR3F3bTFtbmlCd21fdF82bGU3dUpna0RMbGZ1aGhWeEN3NzVkNlhIVFNYU0l0ZUZnQWdoZ3lMdXVYMkk2c0VOWTFoMkFSNWg1ekRkaW96UTBDR2ZubTJ2UzR0OVJqTVg3UzkzVk9GUV9IeHRVSGR2aW82NVJEZkpOTVBwMllLNm5YRnhCRUtTdXpPMWN4ODhNbDBadFUzXzV4YTEySURwNzVNMDZnTjJDeElVVUtURDY5WmpJRUpBSUxPSzdTVVd6Tk5TcGFLdGYxWmc0YWxPbnJyRlNNdG5lQURnTDY4amVlQm1fZWNCQUxnUE1kNzUxQkY5S09sOUF0eS1ZNktmQlMwUUh3RDF0VE5DRnJXRk04TmFWWkhPNXc5X01kako3MHJrU3pHQldKa0RUT2RxODA3dllfZmNnVm5Ua2t1VXlTWC1ncVdsVnhlRDBVbDJEdUpBSG5jVlh6Q0FCUEtnSlZkWlhmMGJwYTFsQkxDWF81emNETXF0WGFlaW83M0tBVEtjdmF2VHcwNE5odnNDQkxFYzhWUlRrZlhIWlU3b2c0MVJuYnh0ZVA1eElpX29UcFZUVnVxZ1ZiSUI2WFFtM1Y3amtKQktGV3NyVHVHQ3hwcVNWeGNwdnVBbXdVQWJhWkRVVzR1Q1lDX1ZyNV85ODRrcXNaWi1sVDM1V2tEWHkwRFlpajloSkpwbWNQSXRWcnhzcGFBU3dSTW9hUWd6dEZpVWNNUzNTUTVsV2dwV1g1dXdxbnJLeGVuMWpFSERN?oc=5',
        'sourceKey': 'fb_3',
        'pubDate': 'Wed, 25 Feb 2026',
    },
    {
        'id': 22,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'การตรวจสารหนูในแม่น้ำกก',
        'excerpt': 'รายงานพิเศษว่าด้วยการตรวจสารหนูในแม่น้ำกก ครอบคลุมวิธีการตรวจ ผลการตรวจ และแนวทางการเฝ้าระวัง',
        'source': 'Hfocus.org',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE1wTkY0enB0WWlIVGN5SnF5QXROVUFVQ2FHMTh5a2duWkdoOGdSTGJJTk8xV3Fqc3NYTV8ybHIzT1VjU1FsSFBIenB2bm5lVzZPY05hR0RIYw?oc=5',
        'sourceKey': 'hfocus_4',
        'pubDate': 'Thu, 26 Feb 2026',
    },
    {
        'id': 23,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เช็กด่วน! คพ. เตือน \'แม่น้ำกก-แม่น้ำสาย\' สารหนูเกินมาตรฐาน ย้ำเลี่ยงใช้น้ำโดยตรง',
        'excerpt': 'กรมควบคุมมลพิษออกประกาศเตือนประชาชน พบสารหนูเกินมาตรฐานในแม่น้ำกกและแม่น้ำสาย ย้ำให้หลีกเลี่ยงการใช้น้ำโดยตรงจากแหล่งน้ำดังกล่าว',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE9RMk1rbW0zM1NyWFFfQUk1a182MGpaQlNCSm5MWVc0VGlMTVd0amFqTU54eXE1b3J1TTZseHZMaU1CX1ltODRCTFVibmhzeHIxdFpDN1h0OXJVME9veXpYWjlzZDdCUQ?oc=5',
        'sourceKey': 'bkbiz_3',
        'pubDate': 'Sat, 14 Feb 2026',
    },
    {
        'id': 24,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'กระทรวงทรัพย์ฯ เฝ้าตรวจมลพิษข้ามแดนทุกแม่น้ำ \'แม่น้ำกระบุรี\' จ.ระนอง ไม่พบโลหะหนัก',
        'excerpt': 'กระทรวงทรัพยากรธรรมชาติและสิ่งแวดล้อมเฝ้าระวังตรวจสอบมลพิษข้ามแดนในทุกแม่น้ำ ล่าสุดตรวจแม่น้ำกระบุรี จ.ระนอง ไม่พบโลหะหนักเกินมาตรฐาน',
        'source': 'เดลินิวส์',
        'externalLink': 'https://news.google.com/rss/articles/CBMiU0FVX3lxTE53c3hkQnloQzN1YjZMczBFenN6eEY3dkZWOTVsOXZ2aUlMYUlOQ0hhWkM1d00ySHZLQ1BYd1VvU3pyaXhyamZTbXo1YnFpQllPbjlZ?oc=5',
        'sourceKey': 'dailynews_1',
        'pubDate': 'Sat, 28 Mar 2026',
    },
    {
        'id': 25,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ผลตรวจคุณภาพ น้ำโขง-น้ำสาย ค่าสารหนูยังสูงเกินมาตรฐาน ส่วนแม่น้ำกก ปริ่มๆ',
        'excerpt': 'ผลการตรวจคุณภาพน้ำในแม่น้ำโขงและแม่น้ำสาย พบค่าสารหนูยังสูงเกินมาตรฐาน ขณะที่แม่น้ำกกอยู่ในระดับปริ่มมาตรฐาน',
        'source': 'แนวหน้า',
        'externalLink': 'https://news.google.com/rss/articles/CBMiS0FVX3lxTE9Wbkx5M2Y3ZGhMLXRCYkZuaXBZYTBVVmNjR3I5WFM3Rk1aclN3Q1padFZLR3ZLZ3VzbUlkUkRndDFFeThXUkVKMFI2NA?oc=5',
        'sourceKey': 'naewna_1',
        'pubDate': 'Fri, 16 Jan 2026',
    },
    {
        'id': 26,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ปัญหา \'แม่น้ำกก\' ต้องรีบแก้ รัฐต้องเด็ดขาด สื่อสารประชาชนชัดเจน',
        'excerpt': 'บทวิเคราะห์ปัญหาแม่น้ำกกที่ต้องการการแก้ไขอย่างเร่งด่วน เรียกร้องให้รัฐบาลดำเนินการอย่างเด็ดขาดและสื่อสารข้อมูลให้ประชาชนอย่างชัดเจน',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZ0FVX3lxTE9kUGhtWDNpR1B6dDhDOTg1ZDV3M0xYQUJTV1lEM21sYkNMQnFfWHNsd1F0UHNoYm5ON3JlN09YRlM1Y2Y3eHRFTjZFMkdGVXRCQ3hWSVBNLXlaSGt0YkRZLUlOUERtNDA?oc=5',
        'sourceKey': 'bkbiz_4',
        'pubDate': 'Mon, 01 Dec 2025',
    },
    {
        'id': 27,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ข่าวพิษแม่น้ำกก! ชาวบ้านริมน้ำขาดรายได้',
        'excerpt': 'ผลกระทบจากปัญหาสารพิษปนเปื้อนในแม่น้ำกก ทำให้ชาวบ้านที่ประกอบอาชีพริมน้ำขาดรายได้ เนื่องจากนักท่องเที่ยวและผู้ใช้น้ำลดน้อยลงอย่างมาก',
        'source': 'Ch7.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiSkFVX3lxTE05M1VHSGdPUVFuX1R1UUlLMG5SNUpkMXVtcnE0ZE5zdkRXWWFhTzVhVTY2b3lWZFNDazc5cTUwdW55RS1MdXZnYUdn?oc=5',
        'sourceKey': 'ch7_1',
        'pubDate': 'Mon, 02 Mar 2026',
    },
    {
        'id': 28,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'พาล่องลำน้ำกก สัมผัสหัวใจแห่งเชียงราย "แม่น้ำกก"',
        'excerpt': 'แนะนำการล่องแม่น้ำกก สายน้ำสำคัญที่ไหลผ่านใจกลางจังหวัดเชียงราย พร้อมข้อมูลการท่องเที่ยวและสถานที่น่าสนใจริมสองฝั่งแม่น้ำ',
        'source': 'travel.trueid.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWEFVX3lxTE9wRzF5cThHaGY1QWtnSGktazFYd2o2T1lJVV96Y0tKVHY5a3hrSU9qTjBrNi1TVFc2b19jd0stZzhpSFVyd1VaMkVrZEVxbUxSSXFTLTZoak8?oc=5',
        'sourceKey': 'trueid_1',
        'pubDate': 'Sun, 01 Feb 2026',
    },
    {
        'id': 29,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'วิกฤติความมั่นคงระดับลุ่มน้ำ: "ดร.ว่าน วิริยา" มช. เปิดงานวิจัยพบโลหะหนักลุ่มน้ำกก–สาย–โขง แนะรัฐสื่อสารความเสี่ยงให้ชุมชน',
        'excerpt': 'นักวิจัยจากมหาวิทยาลัยเชียงใหม่เปิดเผยผลงานวิจัยพบโลหะหนักในลุ่มน้ำกก สาย และโขง พร้อมเรียกร้องให้รัฐบาลสื่อสารความเสี่ยงให้ประชาชนในชุมชนได้รับทราบ',
        'source': 'ThaiPublica',
        'externalLink': 'https://news.google.com/rss/articles/CBMifEFVX3lxTE5KYmhfMDhhRXFqd193YWJKTXFjWHdydjlRRkw3anhDS2luLUFOeGVDZUE3WVNrM00zR3VXQTlZczVGWnl6ekVveThrTHlnR2h0a05mbUd6eHV2ajR6WEdlM3lUVlBnWDJ2OWVHbXVFVmloUFlFVTdVdEh0UHQ?oc=5',
        'sourceKey': 'thaipublica_1',
        'pubDate': 'Sat, 17 Jan 2026',
    },
    {
        'id': 30,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ผู้ว่าฯ เชียงราย เร่งสางปมสารหนูปนเปื้อนแม่น้ำกก ชู 4 มาตรการเชิงรุกรับมือสงกรานต์',
        'excerpt': 'ผู้ว่าราชการจังหวัดเชียงรายเร่งแก้ปัญหาสารหนูปนเปื้อนในแม่น้ำกก ประกาศ 4 มาตรการเชิงรุกเพื่อรับมือเทศกาลสงกรานต์และสร้างความมั่นใจให้นักท่องเที่ยว',
        'source': 'thestandard.co',
        'externalLink': 'https://news.google.com/rss/articles/CBMibkFVX3lxTE8yMzlyY3ZjbVFZcVJpUlROQ25WTWxIQXp4bWQ3VGhSTnRNbWdpWE50MWxBRElzcC01TlZKVmtCa3BScE1aanhZMmRnMl9sd2d1dmJ4cU1fRmZ0NTEyQlY0M1JuVkFPQ0hId1lRSVR3?oc=5',
        'sourceKey': 'standard_1',
        'pubDate': 'Thu, 26 Feb 2026',
    },
    {
        'id': 31,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': '1 ปี \'สารหนู\' แม่น้ำกกไม่คืบ จี้รัฐหาแหล่งน้ำสะอาด-ค้านฝ่ายดักตะกอน',
        'excerpt': 'ครบ 1 ปีวิกฤตสารหนูในแม่น้ำกก แต่ปัญหายังไม่คืบหน้า ภาคประชาชนเรียกร้องให้รัฐจัดหาแหล่งน้ำสะอาดสำรอง พร้อมคัดค้านมาตรการดักตะกอนที่มองว่าไม่ตรงจุด',
        'source': 'prachachat.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiY0FVX3lxTFBnZHJkd0NHVXdRbHdQbUItYlBLck8tZUxLX1JmUWVrd2E3Z0R3T1M0RzFhVkxWYnVzdDZFdEFfRDR1VkdWN0RCQ3loTTdSRlNEMnZBTlVRVzNiQnBMZFpXUUdEMA?oc=5',
        'sourceKey': 'prachachat_1',
        'pubDate': 'Sun, 16 Nov 2025',
    },
    {
        'id': 32,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. เผยแม่น้ำกกยังพบสารหนู เตรียมจับมือญี่ปุ่นเร่งแก้ปัญหาข้ามแดน',
        'excerpt': 'กรมควบคุมมลพิษเผยแม่น้ำกกยังคงพบสารหนู พร้อมประกาศความร่วมมือกับญี่ปุ่นในการเร่งแก้ไขปัญหามลพิษข้ามแดน',
        'source': 'prachachat.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiY0FVX3lxTE15LUpyNnViUkhtaHVYV1JNb2R2R2FPWk5XMmNSYVhpSFBBVjFEMjkxdHpWSUdfYzF3XzkyRFpFemo5d05DaFRQS0R1VEU0TkhpemtlY29FbzA0b1F2U2stQUplTQ?oc=5',
        'sourceKey': 'prachachat_2',
        'pubDate': 'Wed, 25 Feb 2026',
    },
    {
        'id': 33,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'เพจดังเผย ผลสุ่มตรวจชาวบ้านริมแม่น้ำกก เจอสารหนูฝังในเล็บ-เส้นผม',
        'excerpt': 'เพจข่าวชื่อดังเผยผลการสุ่มตรวจชาวบ้านที่อาศัยริมแม่น้ำกก พบสารหนูสะสมอยู่ในเล็บและเส้นผม สร้างความตื่นตระหนกในพื้นที่',
        'source': 'pptvhd36.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiigFBVV95cUxOSVNpbnIxTnJzd2VUM05UU25tWGtPeU1EajZ2S3lzejdPVEpxY1g3cGlheUYyUTFvMXYzV005TVRZOWVnNVpxV0cyVWVEZEpQSGJCeTd3c2NndEoyaVctcXBNQ09HM0FwNENwSTVqR242NlJzMmtiaHR4VTE4WVRCYUhXNk05WklGeEE?oc=5',
        'sourceKey': 'pptv_1',
        'pubDate': 'Wed, 25 Feb 2026',
    },
    {
        'id': 34,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': '\'วิสาหกิจจีน\' ลุยขยายเหมืองแรร์เอิร์ธและทองคำ ริมแม่น้ำกก ที่ไหลผ่านเชียงใหม่-เชียงราย',
        'excerpt': 'รายงานพบบริษัทจากจีนขยายการทำเหมืองแร่หายาก (Rare Earth) และทองคำบริเวณต้นน้ำแม่น้ำกกที่ไหลผ่านเชียงใหม่และเชียงราย ซึ่งเชื่อว่าเป็นสาเหตุของมลพิษในแม่น้ำ',
        'source': 'ประชาไท Prachatai.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE9ROFdVRkoyVUNYUlhkYjhPalpKUnZNb09fVFl6QmlMTmlBMUZpcTJWU0M1T1FqaGNJd0dUNm5ESmdURWpiWWhFX2xqUXFSZ1JqeWlWOVZ0Yw?oc=5',
        'sourceKey': 'prachatai_1',
        'pubDate': 'Thu, 30 Oct 2025',
    },
    {
        'id': 35,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'คืนข้อมูล สร้างทางเลือกอนาคต ข้อเสนอการจัดการแม่น้ำกก',
        'excerpt': 'Thai PBS นำเสนอข้อเสนอการจัดการแม่น้ำกกอย่างยั่งยืน โดยเน้นการคืนข้อมูลให้ประชาชนและสร้างทางเลือกสำหรับอนาคตที่ดีกว่า',
        'source': 'Thai PBS',
        'externalLink': 'https://news.google.com/rss/articles/CBMidkFVX3lxTE1rMHNRdHAzc2JYcjh6NVExYVhwVnhOS3hyMGxEUGo3UW93SVhNVmtJN1dYaWtJVFczVGx3V1drVDlFVDJxelQzd2JvNXUtMzM1U01RdjhUQkd4T1QwT0RyakYxVmVMZ1NWS3dtTVZzY2ZNVWFGVnc?oc=5',
        'sourceKey': 'thaipbs_2',
        'pubDate': 'Fri, 19 Dec 2025',
    },
    {
        'id': 36,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'เปิดแผนเตรียมย้าย \'แหล่งน้ำดิบ\' ผลิตประปาเชียงราย หนีน้ำกกเปื้อนพิษ คาดเริ่มปี 71',
        'excerpt': 'เปิดเผยแผนการย้ายแหล่งน้ำดิบสำหรับผลิตน้ำประปาเชียงราย เพื่อหลีกเลี่ยงการใช้น้ำจากแม่น้ำกกที่ปนเปื้อนสารพิษ คาดว่าจะเริ่มดำเนินการได้ในปี พ.ศ. 2571',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTFBua0gtRmpZNTFtRTQtRzl0MlFMdTJXeWtuNW1teDRobmczNUlhX0pleVVybFNJMUh2b003UXB1cVNlWmlINWkyZXZGV3YxOFlNSTRYYTNRNnZJZXpLSnlzaTVKVC14QQ?oc=5',
        'sourceKey': 'theactive_1',
        'pubDate': 'Wed, 12 Nov 2025',
    },
    {
        'id': 37,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'หนีน้ำกกปนเปื้อน! "แม่ยาว" รื้อแผนท่องเที่ยว ย้ายจุดล่องแพสู่ลำน้ำแม่ยาว',
        'excerpt': 'พื้นที่แม่ยาวปรับแผนการท่องเที่ยว หลีกเลี่ยงการล่องแพในแม่น้ำกกที่ปนเปื้อน โดยย้ายจุดล่องแพไปยังลำน้ำแม่ยาวซึ่งยังสะอาดกว่า',
        'source': 'topnews.co.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiT0FVX3lxTE02ZS1vWXB3NFBFYWREc05paTBnYW05a3RkcWQ3R3NnYmtMX2prWmwzZjV1NW1ManNPbC1MdHZ0SVFMaFA4NFBHV0JYZG1qZlE?oc=5',
        'sourceKey': 'topnews_2',
        'pubDate': 'Fri, 20 Feb 2026',
    },
    {
        'id': 38,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เกาะติดวิกฤตแม่น้ำกก จี้รัฐเจรจาเมียนมา-ปิดเหมืองแรร์เอิร์ธ',
        'excerpt': 'ติดตามวิกฤตมลพิษแม่น้ำกก พร้อมเรียกร้องให้รัฐบาลเร่งเจรจากับเมียนมาเพื่อปิดเหมืองแรร์เอิร์ธที่เชื่อว่าเป็นต้นเหตุ',
        'source': 'prachachat.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiY0FVX3lxTFBGWjJnYTRYZlhSY3Eza0lDeWZlRzF2alp4VWtMeDlrbUtCMjZJQ1dCN0lqVUxsYUQxUDlJTWlOWW1TX0JpWDZIbW5naERBY1QxeENxNlQ0QlJleHhtTXplbkhuNA?oc=5',
        'sourceKey': 'prachachat_3',
        'pubDate': 'Wed, 19 Nov 2025',
    },
    {
        'id': 39,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกกปนเปื้อนคุกคามชีวิต สิ่งแวดล้อมย่ำแย่-แก้ที่ต้นตอ!',
        'excerpt': 'รายงานสถานการณ์การปนเปื้อนของแม่น้ำกกที่คุกคามชีวิตผู้คน พร้อมเรียกร้องให้แก้ปัญหาที่ต้นตอแทนการแก้ไขที่ปลายเหตุ',
        'source': 'Thaipost.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE5Xd2x5UV9PXzV3QmVzQzAxcDJnZzRHWjktSUhUdGp2ajhKLUR6bHA0ckR2REhfN3Z1WloyMzNjVGl6d0s1S1k1RjNLTXlBcEgtUzIzYWtvMA?oc=5',
        'sourceKey': 'thaipost_3',
        'pubDate': 'Sun, 19 Oct 2025',
    },
    {
        'id': 40,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'สำรวจชุมชนริมแม่น้ำสาละวิน จ.แม่ฮ่องสอน ในวันที่ผลตรวจน้ำยืนยันปนเปื้อนสารหนู-ตะกั่ว',
        'excerpt': 'BBC ลงพื้นที่สำรวจชุมชนริมแม่น้ำสาละวิน จ.แม่ฮ่องสอน หลังผลการตรวจน้ำยืนยันการปนเปื้อนทั้งสารหนูและตะกั่วในแม่น้ำ',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE1MYWNVaUVEVXd5LXd4TkJlalhTcC1yMUVVN3JlV3VwU2Vmd1p6aGJpTDdRQUFsREtPM3J0Y0Q1TVk3aEZmU1NlSkdINVJ1X1hyWm1yQkVydi0yZ9IBX0FVX3lxTE5uVFExb1pTTFo2blZyUkkzeVRZTDZHQVdSOVRJNWpGeDB2ZE1yekx0VXE1V1lFSnpBeWJ2ZEhvaFdmOEdDdmFlZldqYVVOMjMxbFlZRFdQdkFET2NuUEZr?oc=5',
        'sourceKey': 'bbc_3',
        'pubDate': 'Sun, 23 Nov 2025',
    },
    {
        'id': 41,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '\'พรรคประชาชน\' บี้รัฐบาลแก้ปัญหาสารพิษแม่น้ำกก หลังลุกลามแม่น้ำโขง-สาละวิน',
        'excerpt': 'พรรคประชาชนยื่นกระทู้ถามรัฐบาลให้เร่งแก้ปัญหาสารพิษในแม่น้ำกก หลังพบว่าปัญหาได้ลุกลามไปยังแม่น้ำโขงและสาละวินด้วย',
        'source': 'เดลินิวส์',
        'externalLink': 'https://news.google.com/rss/articles/CBMiU0FVX3lxTE1mdXFxUTRZMzU4SW9RTWQ3SGtjR0djaEptcTFHZHNPM0Q3SEs4cHhUbHZVNVY4aEY0bGVsYjE3aG5zN2RycE5zS3lwdXVnWUN4UGh3?oc=5',
        'sourceKey': 'dailynews_2',
        'pubDate': 'Mon, 17 Nov 2025',
    },
    {
        'id': 42,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ผลตรวจนักวิชาการจาก มช. พบแม่น้ำสาละวินปนเปื้อนสารหนูและโครเมียมเกินค่ามาตรฐาน',
        'excerpt': 'นักวิชาการจากมหาวิทยาลัยเชียงใหม่เปิดเผยผลการตรวจพบการปนเปื้อนของสารหนูและโครเมียมในแม่น้ำสาละวินเกินค่ามาตรฐานที่กำหนด',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE9zNTAxOTlPY2xkbWM5MkhvQ05pOTRsc0NucUM2WTU3c09OZmxSaENDN05DREJ3ZXpZSDl1QlZ5WlBzMWhHTUtKdVlDckRDVmUwYWFwLTJ1X0N2d9IBX0FVX3lxTE9PbkJiTmtPS1ZLRDZRWlhRU1NYZGh6Q0JkQk1qYTk2QVZuSkdhS1BjNUtrazRZN2UzbDZ1Z2Q1Q0w2ZkJxaW1KTW5vbDNnS1VWOVNKMXlaVVRzNTZLUjRB?oc=5',
        'sourceKey': 'bbc_4',
        'pubDate': 'Thu, 06 Nov 2025',
    },
    {
        'id': 43,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'กรมควบคุมมลพิษพบสารหนูเกินมาตรฐานบางจุดในแม่น้ำกก-สาย-รวก-โขง',
        'excerpt': 'กรมควบคุมมลพิษรายงานผลการตรวจสอบคุณภาพน้ำพบสารหนูเกินมาตรฐานในบางจุดของแม่น้ำกก สาย รวก และโขง พร้อมแนะนำมาตรการระมัดระวัง',
        'source': 'เดลินิวส์',
        'externalLink': 'https://news.google.com/rss/articles/CBMiU0FVX3lxTE1UMFl3Q09reVZmcC1va2RFLUlTSlZJU19jb3g0Y0NkT2JEd051aUxtQmhCVWQzLWRzYnBjNEZ6TC1VaW5ZbDNfcG5NWXlORHpsbGg0?oc=5',
        'sourceKey': 'dailynews_3',
        'pubDate': 'Sat, 14 Feb 2026',
    },
    {
        'id': 44,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกกป่วย รุนแรงเพียงใดและใครจะรับผิดชอบค่าใช้จ่าย',
        'excerpt': 'วิเคราะห์ความรุนแรงของปัญหามลพิษในแม่น้ำกก พร้อมตั้งคำถามว่าใครควรรับผิดชอบค่าใช้จ่ายในการฟื้นฟูและแก้ไขปัญหา',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiX0FVX3lxTE90ZUxURFZaV3FJLWwzcWtQRjBRa29EdG5wZ3drcmlKX3lWN1BJR2hGR1dzcXd0bGRPaVJJakVxb292M0F0cEp1V3pOTjQ1dC0zOUsxWVE0SGRkY2Y5VkI0?oc=5',
        'sourceKey': 'bkbiz_5',
        'pubDate': 'Wed, 15 Oct 2025',
    },
    {
        'id': 45,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'กมธ.ฯ ลุยเชียงราย แม่น้ำกก "น้ำใสแต่ท้องน้ำป่วย" พบสารหนูเกินเกณฑ์ในตะกอนดิน 9 จุด',
        'excerpt': 'คณะกรรมาธิการลงพื้นที่เชียงราย สำรวจแม่น้ำกก พบว่าแม้น้ำจะดูใส แต่ตะกอนดินใต้น้ำกลับพบสารหนูเกินเกณฑ์มาตรฐานถึง 9 จุด',
        'source': 'Nakorn Chiang Rai News',
        'externalLink': 'https://news.google.com/rss/articles/CBMif0FVX3lxTE5FeWx6enRSMHBObG9pb0lEdlc5RGVHY1BqdjUwbTU3MU9HcmxncDloSmtFNG8zdmhjWHpHajVZLWhSVExBV3B6bWdqajNZUlBrXzZ2VFMzVDNLU2puT3V6Z0JmQ0QwMDBWZl8xaG9XdXpWT09xaG51dHN2US11QkE?oc=5',
        'sourceKey': 'cnr_1',
        'pubDate': 'Fri, 31 Oct 2025',
    },
    {
        'id': 46,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'กระทู้แม่น้ำกกเดือด! สุชาติ เจอปชน.เย้ยแรง เป็นถึงรองนายกฯ ไม่รู้ข้อมูลพื้นฐาน',
        'excerpt': 'การอภิปรายกระทู้เรื่องแม่น้ำกกในสภาร้อนระอุ เมื่อนายสุชาติ รองนายกรัฐมนตรี ถูกฝ่ายค้านท้าทายว่าไม่รู้ข้อมูลพื้นฐาน ก่อนจะโต้กลับ',
        'source': 'Matichon Online',
        'externalLink': 'https://news.google.com/rss/articles/CBMiXEFVX3lxTE1UcXBMb2NpcWwyUlpZcm5oTzRtM1FabnN0cUd6cENOOHJwYkZodVFVWEUzR3RTbkJGeVdzNGhhUTV5dDZONC1OektrVmRXS0RQQ0FIY3llRVQ0OUNM?oc=5',
        'sourceKey': 'matichon_2',
        'pubDate': 'Thu, 16 Oct 2025',
    },
    {
        'id': 47,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ภัทรพงษ์เผยสารพิษแม่น้ำกก-สาย เข้าสู่ร่างกายมนุษย์แล้ว ผิดหวัง 2 รองนายกฯ ลงพื้นที่แต่แก้ปัญหาไม่คืบ',
        'excerpt': 'ส.ส.ภัทรพงษ์เปิดเผยว่าสารพิษจากแม่น้ำกกและสายได้เข้าสู่ร่างกายมนุษย์แล้ว พร้อมแสดงความผิดหวังที่รองนายกรัฐมนตรี 2 คนลงพื้นที่แต่ปัญหายังไม่คืบหน้า',
        'source': 'thestandard.co',
        'externalLink': 'https://news.google.com/rss/articles/CBMibkFVX3lxTFBPeFhxWjFHcmhRanpUVG16a1RPOTA4VzNmbVJWMjBUelJYcHVmVVVvdDhzYW9EY29EQjFwcEhuRE9YVDhrRlVDOXVYMXdoQXE2VzBJNHpDWUtEZUVGVS0xOWlFdXc1N3hGbGxuYk5n?oc=5',
        'sourceKey': 'standard_2',
        'pubDate': 'Sun, 12 Oct 2025',
    },
    {
        'id': 48,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'จีนเดินหน้าขยายเหมืองแร่ต้นแม่น้ำกก กระทบไทยอย่างไร',
        'excerpt': 'BBC วิเคราะห์ผลกระทบจากการที่จีนเดินหน้าขยายเหมืองแร่บริเวณต้นน้ำแม่น้ำกก และผลกระทบที่เกิดขึ้นต่อประเทศไทย',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTFBhZWFtd01IX1R2Q3ZkeEhJTTgyTk5uN0FfSk5lWTJqZUxhX3RsdHhMdlVzYnJuOEJSSHlyM05yVUtyMkpiZWNJWE5zU2Z3VXhiMVFYVkdzVWRud9IBX0FVX3lxTFBodlEzcTJfQU5LX0pmQm1Ydkh0M3NOYy1wcDY0STR2NHBtWGZpMWZLX1RTR2ZhVXIxdGdkYjFsZmtUUUVUYVYwZ3JlanZlQ0hEN1N5OXFMOG5LWHdZT2Fn?oc=5',
        'sourceKey': 'bbc_5',
        'pubDate': 'Mon, 03 Nov 2025',
    },
    {
        'id': 49,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกกกับวิกฤตสารพิษข้ามพรมแดน สิทธิของคนริมน้ำอยู่ตรงไหน?',
        'excerpt': 'Thai PBS วิเคราะห์วิกฤตสารพิษข้ามพรมแดนในแม่น้ำกก และตั้งคำถามถึงสิทธิของประชาชนที่อาศัยอยู่ริมแม่น้ำที่ได้รับผลกระทบ',
        'source': 'Thai PBS',
        'externalLink': 'https://news.google.com/rss/articles/CBMidkFVX3lxTE1DeHN1UjZha2xuSmpsdkRLSlIxc2JyX2NaM05BNjl1U0V3dzI1NVRBVFd4V0hLNU5fUTFsdzRfNzlGbUdaVlpLVm4xQkxnelBVbUdCSldrNE01T0xDeFlVSnJtdE1Lblp1LW9qVFFVRTV0RGZhQmc?oc=5',
        'sourceKey': 'thaipbs_3',
        'pubDate': 'Fri, 10 Oct 2025',
    },
    {
        'id': 50,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ชาวประมงแม่น้ำกก โอดรายได้หายวับ วอนรัฐเยียวยา-เจรจาหยุดปล่อยสารพิษ',
        'excerpt': 'ชาวประมงริมแม่น้ำกกเผยรายได้หายไปอย่างรวดเร็ว ขอให้รัฐบาลช่วยเหลือเยียวยาและเจรจากับต้นเหตุให้หยุดปล่อยสารพิษลงแม่น้ำ',
        'source': 'prachachat.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiY0FVX3lxTE16amFDUjBvRWg1M3p0ZHNkOXAyay1wQ2pnN1VyM0FQcVIxM0lkbjJESllwY0ZQSjF6LUpNUVpvTG9tRENCNHozaTg0aTlDbnY1YnQ3NHEzbHlHNnF1eEJVM2llYw?oc=5',
        'sourceKey': 'prachachat_4',
        'pubDate': 'Wed, 15 Oct 2025',
    },
    {
        'id': 51,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '"สุชาติ" ลงพื้นที่เชียงใหม่-เชียงราย ลุยแก้ปัญหาสารปนเปื้อน "แม่น้ำกก"',
        'excerpt': 'รองนายกรัฐมนตรี สุชาติ ลงพื้นที่เชียงใหม่และเชียงราย เพื่อติดตามและเร่งรัดการแก้ไขปัญหาสารปนเปื้อนในแม่น้ำกกด้วยตนเอง',
        'source': 'Thairath.co.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWEFVX3lxTE9uVklyemV4cnlKeVR6dmk1MHlaTEdnZkFsQTc5VVBIOUd6OHNiYVNtb2tPMjkxTEJjODVkTGtiNDNrSW5TbW1Tbmd0WnZ2UHBFTm5TMEtRZ1c?oc=5',
        'sourceKey': 'thairath_1',
        'pubDate': 'Thu, 09 Oct 2025',
    },
    {
        'id': 52,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'จังหวัดเชียงรายเสนอยกระดับประเด็นแม่น้ำปนเปื้อนเป็นวาระแห่งชาติ',
        'excerpt': 'จังหวัดเชียงรายเสนอให้ยกระดับปัญหาแม่น้ำปนเปื้อนโลหะหนักเป็นวาระแห่งชาติ พร้อมผลักดันให้มหาวิทยาลัยแม่ฟ้าหลวงจัดตั้งศูนย์ตรวจโลหะหนัก',
        'source': 'transbordernews.in.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVkFVX3lxTE5HOTBqd2hTOHdOWDRkTlhwMXJ3U2t4c0V6eVlSQ2hHNnRwUERrTmkyM3dvZGwwUEtRUFo4bzJWYjROYmlJQVE0clRmYjluVUQwYkl5TWtB?oc=5',
        'sourceKey': 'transborder_2',
        'pubDate': 'Tue, 23 Dec 2025',
    },
    {
        'id': 53,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '\'ธรรมนัส\' สั่งตั้ง คกก.จังหวัด ฟื้นแหล่งน้ำเชียงราย เตรียมนำเรื่องเข้า ครม. แก้สารปนเปื้อนแม่น้ำกก',
        'excerpt': 'รองนายกรัฐมนตรี ธรรมนัส พรหมเผ่า สั่งการให้ตั้งคณะกรรมการระดับจังหวัดเพื่อฟื้นฟูแหล่งน้ำเชียงราย พร้อมเตรียมนำเรื่องเข้าคณะรัฐมนตรีเพื่อแก้ปัญหาสารปนเปื้อนในแม่น้ำกก',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMinAdBVV95cUxOOGZZaGZEMEl0TXlrM3FvYTBjZkZaSGF0QXVkZFktZDhWaFZNVHlKdjVoT3VJNnpLV2g0aGJJV2ZCdUV1NzRhQmtJdTR3ZUJKcE51NFZhQWhMLURNZWtGN1pkVEo1b0tNQ3E1Um9zbzhNZmNYV3VMWGR2YjdWdzA0ZnozVTBtaDF2VUVvcVhFRFlnbDBBQmxJZXZxakcxQzdMemJZS1BpQ2RmNFZrWHZQNDM2REluUW5CNnRvRXlmU0RITWYtZDZVMGhOMUJFZjd4ckZLTGw5VjBxQnR3N2pjNF9VLXRYRjdjZlU0UWFGamZ6R1dxYk5xcWtCQzA1M1pBc1ZXUmlCY2Q3bjMzQnhQQzVNNWhPcW5yMEtibzExZDJ0VUdrbi1ELUxrZjlZSjAwZE1JOFA3aHNQOGJIQWJNYjVsamtBajU2RlpxNWVnemZSOEJSZUo3dU1ZekJZc25nWWN0N3pqX3IxelRWUUpYR0VUVTZRSjJ2Tkh4ajhpRTFWdDFPSWFucGVYTkM0T2dwTnc2enhxTk4zZmU1b1NIUDhKMU5rb3JNaVFWcEN6WnVHNndHRGppVjVoOWpHWldtNnFIcGxsb0otU1E0WTVZeDA0X2FSc2p5LU5iNG9veTZSekNuT2ZfUzc1TEJjNU9zSzZWcEVoWmRTSm9STWdRRERlNTEtVjZRZ2dvRDJrdXp0RmZZRmpBVFVOU013NDltZy1JWEFNQ3AweVBzNlpybWJNRkdyWmt0elVqSVhTSWZBQUVfZk52Q19RNFVfcmdCbms1c0hEeUlVVVJMY3pQMl9OcTd2Vjh4SWJQUktBeS1VTlRKUmJSUkNxYmZqX2pvX01tWkVDVllFVGd3VnI0WFRWclNtc3dfTWtLZ2hZOFhMV3hrRktCd1kyUHdibXVlMWFmTXJwS0xsODdGYzZQXzY5NTV0c2xkUW1jWU1uREwtakpQVnl2RkdSbUx2LU9VVGxRN00xcGNzcDJiZDJVRHh3cHZLaHduR2xwTW15R2dLMUFoUlBpTnh4QjNmSzRpaHBiQnFSWWtUMjJMVi1QWnNuWE8xOGVzeGlTay1QNWxLZFJQQmZ6d1BpRnFta2diZUR5d1BxbVVCakhXTVBqbTZiRUF2NklVb196ZTZuS2lyaHRCYWZhVHNVbWVYMGFZeDhoZXlLTVY0SjY3OHlybURDUGRpNzBxMGdUZUFmdndQcXpZamNlY2N6Y3c?oc=5',
        'sourceKey': 'fb_4',
        'pubDate': 'Sat, 11 Oct 2025',
    },
    {
        'id': 54,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ทส.เร่งแก้ประปาหมู่บ้าน 18 แห่งปนเปื้อน-เป่าล้างบ่อบาดาล ชาวบ้านโอดข้าวนาปีราคาตกแถมต้องรอลุ้นสารปนเปื้อน',
        'excerpt': 'กระทรวงทรัพยากรธรรมชาติเร่งแก้ปัญหาประปาหมู่บ้าน 18 แห่งที่ปนเปื้อน พร้อมเป่าล้างบ่อบาดาล ขณะชาวบ้านเผยข้าวนาปีราคาตกต่ำ และปูปลาหายหมดจากท้องนา',
        'source': 'transbordernews.in.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVkFVX3lxTE9iTzlMSlFCM2VwUmJnbTRxWEhGckhnUHRnNXZNdzdwamN1MnRxbElDbGsxMk9KNkljQVhLNVhuNnNsdzJ5Y2VZbG1HNGNRTkYwbE8zZEhn?oc=5',
        'sourceKey': 'transborder_3',
        'pubDate': 'Sat, 25 Oct 2025',
    },
    {
        'id': 55,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'วิกฤตแม่น้ำกก พบ "สารตะกั่วเกินมาตรฐาน" 18 หมู่บ้าน กปภ. ทุ่ม 2 พันล้านย้ายแหล่งน้ำดิบ',
        'excerpt': 'พบสารตะกั่วเกินมาตรฐานในพื้นที่ 18 หมู่บ้านริมแม่น้ำกก การประปาส่วนภูมิภาคเตรียมทุ่มงบ 2,000 ล้านบาทย้ายแหล่งน้ำดิบ',
        'source': 'Nakorn Chiang Rai News',
        'externalLink': 'https://news.google.com/rss/articles/CBMijAFBVV95cUxNaGU1WXpyTXpDVU5RX1VGMDZ3N1Q0dTRLZGFBQXZ4d0xUZnFucU84VGZPdW1KTDZ1NlBycEJJX2xSUVpYdEg1S3E3a3p6UU81cFlFU1p2THZ1d1dNOHMwMVNMM1M0Xy1ISU1RX2IwdHQ1TVZZVkprclA1UE1KVnJrVWVRdjFhNXlfemVlOA?oc=5',
        'sourceKey': 'cnr_2',
        'pubDate': 'Fri, 10 Oct 2025',
    },
    {
        'id': 56,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'การเมืองไม่นิ่ง ไร้เจ้าภาพ แก้พิษน้ำกกปนเปื้อน',
        'excerpt': 'บทวิเคราะห์ปัญหาการเมืองที่ส่งผลต่อการแก้ไขปัญหาสารพิษในแม่น้ำกก เมื่อความไม่แน่นอนทางการเมืองทำให้ขาดผู้รับผิดชอบที่ชัดเจน',
        'source': 'Policy Watch',
        'externalLink': 'https://news.google.com/rss/articles/CBMiX0FVX3lxTFAyLVVSWHp5Y1hZbExENXlEeVhocjZMMHBIeWJUaFpaVHNPaVA1UlpYVzZtMUFHTHBDRGJ3RmJOX0o0VzdSSHJJdzNWSUhoaUsxOHROQnBOVE8zM1pxeXE0?oc=5',
        'sourceKey': 'policywatch_1',
        'pubDate': 'Mon, 15 Sep 2025',
    },
    {
        'id': 57,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'สารหนูแม่น้ำกก: ผลวิจัยชี้ ปลาป่วยเพราะสารพิษปนเปื้อน ตอนนี้ \'แม่น้ำกก\' อันตรายต่อมนุษย์-สัตว์น้ำแค่ไหน?',
        'excerpt': 'ผลวิจัยชี้ว่าปลาในแม่น้ำกกป่วยเนื่องจากสารพิษปนเปื้อน BBC วิเคราะห์ว่าขณะนี้แม่น้ำกกเป็นอันตรายต่อมนุษย์และสัตว์น้ำมากน้อยเพียงใด',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTFBrcFlUbmp1Z1UwYmFjUE03VzgwcG83X09kWV9QbVhoX2JQLWJmZzhvR3ZhRFN0SzlWOUtiWjJPYTRZMWdBYlRSR3IxSDNVZWxRSTdhRTZFMWVFZ9IBX0FVX3lxTFBxV016MVA2ZzRLaGtlQ0lmSUV6TF9ON2FDVFprUmRkclBUYmU4TnJCNkxDS1FCdVc0MF9mVklwZ2doMDdleUJaMndCRVMwTUc1bnJtT1U1OTZKaDVlSUsw?oc=5',
        'sourceKey': 'bbc_6',
        'pubDate': 'Wed, 09 Jul 2025',
    },
    {
        'id': 58,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ไม่ใช่แค่แม่น้ำกก แต่แม่น้ำสาย แม่น้ำรวกก็ปนเปื้อน ความเสียหายภาคการเกษตรที่อาจเกิดขึ้นกว่า 547 ล้านบาท',
        'excerpt': 'ปัญหามลพิษไม่ได้จำกัดเพียงแม่น้ำกก แต่ลุกลามไปยังแม่น้ำสายและรวกด้วย โดยความเสียหายในภาคการเกษตรอาจสูงถึง 547 ล้านบาท',
        'source': 'ประชาไท Prachatai.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE1HUVYxWlI2cEZBNk40dklCZ1h5eVo0b1JvZ1NmdjZfSm51NjgxeDRNcDdla1VTSC1tUTFuczJVOVUzYVNlMzRZMHlqQnZ5dlVQOXFLaW9qTQ?oc=5',
        'sourceKey': 'prachatai_2',
        'pubDate': 'Sat, 20 Sep 2025',
    },
    {
        'id': 59,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'สารหนูแม่น้ำกก: รัฐบาลชี้แจงเร่งแก้ปัญหา แต่เหตุใดยังมีคนมองว่าทำงานเหมือนไม่รู้ว่า "ไฟกำลังไหม้บ้าน"',
        'excerpt': 'BBC รายงานว่าแม้รัฐบาลจะชี้แจงว่าเร่งแก้ปัญหาสารหนูในแม่น้ำกก แต่ยังมีผู้ตั้งข้อสังเกตว่าการดำเนินการยังขาดความจริงจัง',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE53TFp1anl2RDBLdkduQ3Q0Z2ZVN0cxejJLWkd1c3FoRWlLV2NGazZZakRFMWF2U2tTMU5CclI5c192VWFqRlhLeWNYVlJ2enQ0QUtqOC16dVNqd9IBX0FVX3lxTE9OQ2w3RmlUTDc4VVFyNlFDTXlZQU9GT0ZNLXFEeklkQ1U4MzJEMjZwRjExeEFNWkstdGpUT2ZSQ2ptZjRqdURYSFVuVGIxYVE5bmhNNTJ1cGNjbDF3bzNz?oc=5',
        'sourceKey': 'bbc_7',
        'pubDate': 'Fri, 18 Jul 2025',
    },
    {
        'id': 60,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'สารหนูเปื้อนน้ำกก-สาย-รวก คุกคามเกษตรเชียงรายต่อเนื่อง เสี่ยงสูญเกือบ 4 พันล้านบาท',
        'excerpt': 'การปนเปื้อนของสารหนูในแม่น้ำกก สาย และรวก ยังคงคุกคามภาคเกษตรของเชียงรายอย่างต่อเนื่อง โดยความเสียหายอาจสูงถึงเกือบ 4,000 ล้านบาท',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE1Nc2pxNHlZSFMzZGZOSUNzdTZ2UWJaUWZIWkFHUXJNR211Wm8ydTJxQ0trb3NJTFU1bVN1dUJTeGRZbER1ME8tU3E2NWhxalVrU3lVY2tEcUhOVDM5TVpjWmhxNWZ0QQ?oc=5',
        'sourceKey': 'theactive_2',
        'pubDate': 'Sat, 20 Sep 2025',
    },
    {
        'id': 61,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ครม. รับทราบข้อเสนอแนะแก้ปัญหาแม่น้ำกก แม่น้ำสายปนเปื้อน มอบ ทส. สรุปผลดำเนินการใน 30 วัน',
        'excerpt': 'คณะรัฐมนตรีรับทราบข้อเสนอแนะในการแก้ไขปัญหาแม่น้ำกกและแม่น้ำสาย พร้อมมอบหมายให้กระทรวงทรัพยากรฯ สรุปผลการดำเนินการภายใน 30 วัน',
        'source': 'กรมประชาสัมพันธ์',
        'externalLink': 'https://news.google.com/rss/articles/CBMic0FVX3lxTE5RNmc3Z01iNGs2cXllVnR1dW9FRkhPVHd0TFZocVNIVkNjZEExZGZ3TFlUcmdPNUxsRGZqYnZLa19EZ09Ucks1LWplOTR3Y01vQ2JyVmZCbGgyWU1WUGtsSi1UTldIUGZfV0dUNVZyQW9RS2M?oc=5',
        'sourceKey': 'prd_1',
        'pubDate': 'Wed, 16 Jul 2025',
    },
    {
        'id': 62,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ประกาศคุ้มครองสิ่งแวดล้อม รับมือมลพิษ แม่น้ำกก-สาย',
        'excerpt': 'Policy Watch รายงานการออกประกาศคุ้มครองสิ่งแวดล้อมเพื่อรับมือกับปัญหามลพิษในแม่น้ำกกและแม่น้ำสาย',
        'source': 'Policy Watch',
        'externalLink': 'https://news.google.com/rss/articles/CBMiaEFVX3lxTE9ueldlMzhiVTVJeDZDRjdSM1hvYV9fR09YUlBNWTZDdmJSY1pSM2ZlVUt1akhjQ2p2bDkxUGRJeWFKQVFicmZpdWJNN2hlYWZuS2oyT2tCcEJIcGVEekFWMXZVTEZmUHB6?oc=5',
        'sourceKey': 'policywatch_2',
        'pubDate': 'Wed, 28 May 2025',
    },
    {
        'id': 63,
        'type': 'normal',
        'badge': '💰 งบประมาณ',
        'badgeClass': 'badge-primary',
        'title': '\'แม่ฟ้าหลวงฯ\' ชี้สารพิษในแม่น้ำกก กระทบเศรษฐกิจ ปีละ 1,300 ล้าน',
        'excerpt': 'มูลนิธิแม่ฟ้าหลวงชี้ว่าสารพิษที่ปนเปื้อนในแม่น้ำกกสร้างผลกระทบทางเศรษฐกิจสูงถึงปีละ 1,300 ล้านบาท',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZ0FVX3lxTE43azhMQTdBc1pzaGd0eFFUSkhucUhOXzR6bUVfZHNHeGxfOFdqcDVWU2NkUGZKd3pBTDRFRkdnZ1hJYWQ0cmF5V0o1MXVxX3BoNTh6a1l2ZEdXZzJyMDFnM1hWcjN6MUE?oc=5',
        'sourceKey': 'bkbiz_6',
        'pubDate': 'Mon, 22 Sep 2025',
    },
    {
        'id': 64,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ยิ่งแก้ช้า ยิ่งจ่ายหนัก: ทำอย่างไรในวันที่แม่น้ำกก-สาย-โขง เต็มไปด้วยสารพิษ',
        'excerpt': 'บทความวิเคราะห์ว่ายิ่งรัฐบาลแก้ปัญหาช้าเท่าไร ค่าใช้จ่ายในการแก้ไขก็ยิ่งสูงขึ้น พร้อมเสนอแนวทางรับมือกับวิกฤตสารพิษในแม่น้ำกก สาย และโขง',
        'source': 'The101.world',
        'externalLink': 'https://news.google.com/rss/articles/CBMiYkFVX3lxTE9Ea3FueFk3VUVVeUVWQ25vZWZKZGp4MVNZYXFVdXdSZ1pUVmJnWGdtMGhrNnNFVHFiYmx4TUo2d25xaXNWTXZfdGtJNTFkX3ZjVXRORnBJbkUyTlFfZTJrZ01R?oc=5',
        'sourceKey': 'the101_1',
        'pubDate': 'Tue, 03 Jun 2025',
    },
    {
        'id': 65,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '"สุชาติ" จ่อลงพื้นที่แม่น้ำกก แก้ปัญหาสารหนู รับภารกิจเยอะแต่สนุก',
        'excerpt': 'รองนายกรัฐมนตรี สุชาติ เตรียมลงพื้นที่แม่น้ำกกเพื่อแก้ปัญหาสารหนู พร้อมระบุว่าแม้ภารกิจจะมาก แต่ยังรู้สึกสนุกกับการทำงาน',
        'source': 'Thairath.co.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiW0FVX3lxTE1Zb01QR1hLdW1iQkQ0M3h1VkZwcW0wdl9HeVAtOWR1YTdPOV9WTEtVSUc1YWFjTjd2Tk01WjBQTEZVZnBoU2IydFhPWnRWU2RtanhBUjJUN0dTX00?oc=5',
        'sourceKey': 'thairath_2',
        'pubDate': 'Sat, 04 Oct 2025',
    },
    {
        'id': 66,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกก-แม่น้ำสาย: เหตุใดภาคประชาชนมองว่าภาครัฐยังแก้ไขปัญหาสารพิษปนเปื้อนแม่น้ำไม่ดีพอ',
        'excerpt': 'BBC สำรวจความเห็นภาคประชาชนที่มองว่าภาครัฐยังดำเนินการแก้ไขปัญหาสารพิษปนเปื้อนในแม่น้ำกกและสายได้ไม่เพียงพอ',
        'source': 'BBC',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE1kNGwzOUZvaTFhMGU2ZTV2NmZudFN3b2ZuWTdJZS04NlpRTGVkTVpXd1YyUWRWRXBOdThpdkNLN3FWMnk1emVuZjl4UlVZcnR4YjJvMkZ0VU1CZ9IBX0FVX3lxTE1KTGtIaHFtVXZxUU9PTDVvQ2hCdEJac1ZaWDE3TmNiWUk1cl9oSFkza0h3dmxMMTBKTHlieWpGY3NRcW9ocXNNQzFFQ1VDRV9IWlpQZ2VNZFZFLWdHTWtR?oc=5',
        'sourceKey': 'bbc_8',
        'pubDate': 'Tue, 20 May 2025',
    },
    {
        'id': 67,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'สุชาติ จ่อลุยเชียงราย แก้ปัญหาสารพิษแม่น้ำกก เร่งดัน พ.ร.บ.อากาศสะอาด',
        'excerpt': 'รองนายกรัฐมนตรี สุชาติ เตรียมลงพื้นที่เชียงรายเพื่อแก้ปัญหาสารพิษในแม่น้ำกก พร้อมเร่งผลักดัน พ.ร.บ.อากาศสะอาดเพื่อแก้ปัญหาฝุ่น',
        'source': 'ข่าวสด',
        'externalLink': 'https://news.google.com/rss/articles/CBMiW0FVX3lxTE1LMTlIekdSYno2Tk5YRG1CMDE0UU9QeHdhOG10ZWY1RGRpMjRhMUZYdUxsT1A1aHBSeEdVc2J6UEVZM1k1UjV2U24yMVljV2NScFBVNGU2YV9ndzQ?oc=5',
        'sourceKey': 'khaosod_3',
        'pubDate': 'Sat, 04 Oct 2025',
    },
    {
        'id': 68,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'กรมอนามัย พบสารหนูแม่น้ำกก ในประชาชนกลุ่มเสี่ยง แต่ไม่เกินค่าอ้างอิง!',
        'excerpt': 'กรมอนามัยเผยผลการตรวจพบสารหนูในร่างกายของประชาชนกลุ่มเสี่ยงริมแม่น้ำกก แต่ยืนยันว่าระดับที่พบยังไม่เกินค่าอ้างอิงที่เป็นอันตราย',
        'source': 'Hfocus.org',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE52NmRBbW5GLU1hd01oVVhibTBtaV9QRkJJVFVTZUtnRFplU0tkNGJWNVVXaWJCZ2lqZ1NoVUYtUDhMSHQ0YXlMR1ZNTk9EQi1WeENnMXRvMA?oc=5',
        'sourceKey': 'hfocus_5',
        'pubDate': 'Thu, 10 Jul 2025',
    },
    {
        'id': 69,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'นายกฯ "สั่งการด่วนแก้ปัญหาแม่น้ำกก" ให้ทุกส่วนที่เกี่ยวข้องเร่งดำเนินการแก้ไขปัญหาสารปนเปื้อน',
        'excerpt': 'นายกรัฐมนตรีออกคำสั่งด่วนให้กระทรวงทรัพยากรฯ ทหาร และกระทรวงต่างประเทศ เร่งดำเนินการแก้ไขปัญหาสารปนเปื้อนเกินมาตรฐานในแม่น้ำกกในทุกมิติ',
        'source': 'กรมประชาสัมพันธ์',
        'externalLink': 'https://news.google.com/rss/articles/CBMic0FVX3lxTE1zdjBBY0hjQ2hxYVlSZTh0ZXRfYl9HelhQcFFlSFF3c1gyRk5YZUNSb3dFQXgyNm5pOFJCRUhMOWtnRHFWUm5GcVY0Yl96allZU3lqdFNPRnk3M1pZNVJ5d3V6Mms4dnpXN0NaeU0tQlpjYmM?oc=5',
        'sourceKey': 'prd_2',
        'pubDate': 'Wed, 21 May 2025',
    },
    {
        'id': 70,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เปิดรายงานลับ (2) เหมืองแร่ต้นแม่น้ำกก',
        'excerpt': 'เปิดเผยรายงานลับฉบับที่ 2 เกี่ยวกับเหมืองแร่บริเวณต้นน้ำแม่น้ำกก ซึ่งเชื่อว่าเป็นสาเหตุหลักของมลพิษในแม่น้ำกก',
        'source': 'transbordernews.in.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVkFVX3lxTE95eW43cUJWN1ZaZnlLQzNCMllGZVQxX3BPVWMwbVQxUl9tYUs2aV82ZkxFUUc0RDdCLVF5cGxwR3A1Q293ZU5paDJRQ3FDLWZfYnBoMGtn?oc=5',
        'sourceKey': 'transborder_4',
        'pubDate': 'Sun, 28 Sep 2025',
    },
    {
        'id': 71,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'อานิสงส์ \'พายุวิภา\' ลดสารหนูปนเปื้อนแม่น้ำกก–โขง ครั้งแรกในรอบหลายเดือน',
        'excerpt': 'ผลกระทบเชิงบวกจากพายุวิภา ช่วยเจือจางสารหนูในแม่น้ำกกและโขงให้ลดลงครั้งแรกในรอบหลายเดือน แม้เป็นเพียงการบรรเทาชั่วคราว',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiX0FVX3lxTE1QUE5iaUJFY2VTRVE1Z3Z2bmI2M1FXeDdTM2Z1NUlIN3ZMNlo1aGVTQkpTZnhfNDh2UC12dFFJclMxak1OcHRYSDg2bnMtTlh5ZGMwN2xKT0QydkhLNHA0?oc=5',
        'sourceKey': 'bkbiz_7',
        'pubDate': 'Sat, 06 Sep 2025',
    },
    {
        'id': 72,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำสองสี! แม่น้ำกก-แม่น้ำโขง ที่สบกก สีต่างกันชัด หลังพบสารหนู-สารตะกั่วเกินมาตรฐาน',
        'excerpt': 'The Reporters ลงพื้นที่บ้านสบกก อ.เชียงแสน จ.เชียงราย บันทึกภาพแม่น้ำกกและโขงที่สบกันมีสีต่างกันอย่างชัดเจน หลังพบสารหนูและตะกั่วเกินมาตรฐาน',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMipwdBVV95cUxOMXdpV0hVc2h0MWdLaVdsNjlqT1FJZlZ4b1Rndlg0V0RtMUt5SVFGSDZ0S19HUHBoTGVsMFZjT1VWMzg3aFZ6QXNReDI1MnlUaUdZVVlLQjNHYW43cjkxa1RHYlZnSEstOTgtOExaOG9HbXBvbjRHMWJqWWl0cmx6NERaNi1fbDhpM2RVaWhncEpEOEhVUjRwNEp4bUFBaXpMS0tweTJhU0U5SkdNWlRZZG5qTVpVd2J4clZKTDgtc0FfeDBack1DRkU0blU5anE0RGhPS19TV1lBZ1hIUjVWQ1ppakpvWXliNm5WSC13cjlrcFRZYnBudlZnY1kxZkVOSUJhaVJZdFEzVEp4cEhpQXZhRHhkLWN6dGhzRW1KX2ZaYmdLZ0tzRjJTeldjQkhxTGxCLWNUTGhsRUZiU1g2NmZyN0VKQ2J1R1NqNzg0dnNFc0tZem5kelY2X0VVbF9TUkpzM0tuWmYxX2gxZWlYcEdmZTlyU1VzWFFQencwU092MVRaRWI1QnA4RHhINkRhU3J2UkpfaXdycHBybGhXcHJ1TFRkdXNVaTZIMUpLZXIxV2pxQUpkeDRXMlBLX2xVM09YYWR1VWJZNzFRbkdSZ09CZ3JlaUwzZk5MalNubmpaS09ORU9HSExJeWVGOHUxOGxhbEQxaERuOUNaaHVPWkJ4SHlhMmpwbnNDTEVqZnhzY3h0b05Fb2w0bGdfRVFoZF9qLVkwYW1pTGpfay0zQVItdU5hZExwS3V5a0dSVEYwSzJ5bFFIRVViU0NJQWR3dV9MRXR3ejgwalhZdzJsYTQtVGQySTVjV1ZyNkhTQWtrcHpScUIxb0EwZXRHWXQ0MTJmbWV2Ry1JZVdNY2NudUlsTHZ5ZUFwWmdrY2ZZT2lIYzNhVnFnNzlJcXFmRmJjSU5oc09MM2lXX2taS211cWxvcEZhbXNrOHJBcFRnNWozR1U2OUxra2lTLXBadTAxZHhxTlpZYksxLTdFRXB3X2tsVENoYU85RVgxVlJYTVRJdTVKQ1VPb3o3ajJsNjc0UkVLaXFjV1VrZXd0eGE0ckxLSVNQVm10TnJpZDJDRmdvUjAyMGZIdWtpa1JTZ0FIeU14S2c0OG4wZVZZYUtCN3BKOTJrNk00Y2JRM3UwRlJtaW9lbXoxNUFsdmtkVldqYWdTMzVjME8tNDB0cVJQWGV1bG54RmoyTWZJOE5DUWkyY19ZNHR6X2hfNl9FbUNNQ014WHU3bw?oc=5',
        'sourceKey': 'fb_5',
        'pubDate': 'Wed, 21 May 2025',
    },
    {
        'id': 73,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ทำไม? "สารหนู" ปนเปื้อนแม่น้ำกก จ.เชียงราย เป็นภัยร้ายแก้ไขยาก',
        'excerpt': 'Thai PBS อธิบายสาเหตุว่าทำไมปัญหาสารหนูปนเปื้อนในแม่น้ำกก จ.เชียงราย จึงเป็นภัยที่ยากต่อการแก้ไข และอุปสรรคที่ขัดขวางการแก้ปัญหา',
        'source': 'Thai PBS',
        'externalLink': 'https://news.google.com/rss/articles/CBMiVEFVX3lxTE00dHpPUEFQRnhJb3ZjdUl2TVlfN2ZLWm5jeFQ0elA1Mm5sZ0U2VUgybjB2SXEzLS16TFFFYllGcWdOZEM0b3VPNjFIcmctaFBQVjcwUQ?oc=5',
        'sourceKey': 'thaipbs_4',
        'pubDate': 'Mon, 01 Sep 2025',
    },
    {
        'id': 74,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'สมาคมแม่น้ำเพื่อชีวิต เปิดเผยภาพปลาติดเชื้อในแม่น้ำกกและแม่น้ำโขง จ.เชียงราย',
        'excerpt': 'สมาคมแม่น้ำเพื่อชีวิตประมวลภาพปลาที่ติดเชื้อในแม่น้ำกกและโขง จ.เชียงราย ท่ามกลางการเรียกร้องให้ตรวจสอบสารปนเปื้อนที่เชื่อว่ามาจากเหมืองในรัฐฉาน เมียนมา',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMi3AdBVV95cUxOMDBpZGVQZjItZ2twT3BNNmhIX213NWtTMW8yODBuZXBEUmxoTWNpa0RBcEE2azNYYVN5aGM1NjFjWlNWOTZuYUtpeDNMQVNtZEhRQVJmcFc1M2VvQVZTb3FoUHd6S3dQS2NrZnJWZk9BRlpnYUZuM1oyVnE2akpNYU1rT3pnT29ra0U1Z3Y0S1ZyX1dQT2x4Rmowa01iYlVyczgyZ3pFM0V3QUJiQ3lKTXZlX0pIZmNEZlItdTMzaldZa005U1NUcURCX3pZUkxONlpEZUh1dW16YUhFd2xEcW13UUV0eXdfSU9GVFpOeEhCdzZ0YVdqVVVucEpTZ1lONjhZTkpGYUVfQ2pueE9MTC1idldLV2F5dUZnTm9lVGpwdjBOQWJzWktBRER5Tlc3UXNoUnh4TTJiVzQ1T19jVjlLbVFUMjI1bUZJV2NkdUtNMUNKWGRoY29BV0V4Q1VjbjBfcHA3UDBoSXNYLXI0YVh4ekJuLTJfZEE1X2lOZ2xTamI1S1REcFY3aWQ0Ny01Nm1LVzQ0QThHekZpMXhickFvLWhydDJLY2xFZVJzc3VhTFFJQk0wNHFfYUVScUYxbE5wM3pwSjRqbjZNRUZTX09QeGExbVdvczA1cVFaVTBXeEljQU1qUmhDdnU5OEEtQjl4ZnhoNUVTSVE0aXZDM1dTcXh4NlBrOFVOTTFNelVYYUNoVFNldGItdEN5emU1aXlPRlBpcWJQOXV6b2lzdHhvczd3STlJblo4MTVwRmI3dVhHXzdSZE5Wb09PM0xWeW9fMGwyMVZ4UGVJMkluMm13RUtKQVA0RXNpQ3htMHlLcWJzWHNObmFtSDB0QzdCUXVMVC1sTzV0VThxc1FBZWNQc2pvRUJXUE8wRzR6ME1ZWXNrQUY2emNoSzRmTGVYbTg3UTU1aVk2ZGE1QkxCTTYzZW5pVkE1VGdiY2FwN3k1MkhvVk5EZXNlbnlzc1ZiekhlRENJUVJXelk1c1FrYndsN1pXQzRheUdUd2RYcXZTb2J5T1lScVhIMndGQmVPbXJZZG9JcGNHcmFMZ1I0NVZlTG5nbVY2S2dCMGhXVkp5a2tZVUpwTUphTWwyNGZ2Y1pkZVZZMTBPTzVjSDJ0akQtR1NLZzJnYmNiX1VPb3dKUzdTZlBBVXNLZEx0eWZVOVNjOVVyenhGWDhrYzNKOFhveDdLODhDck5ObnQ0aWNsQVN6Sl93YWc3Q3lDRlN0WlR1YVViZXQtblhTWlRvVEZOUWRTb0pmckRZT05IOTJUTzVKNXFJU2RBLXVFNk0yU2lBNFlndjgtSzE1?oc=5',
        'sourceKey': 'fb_6',
        'pubDate': 'Sat, 17 May 2025',
    },
    {
        'id': 75,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'SHRF เปิดภาพเหมืองแร่หายากต้นแม่น้ำกก ห่างไทย 25 กม. เผยใช้สารเคมีรุนแรงเทละลายหิน',
        'excerpt': 'มูลนิธิสิทธิมนุษยชนไทใหญ่ (SHRF) เปิดเผยภาพเหมืองแร่หายากที่ตั้งอยู่ห่างจากชายแดนไทยเพียง 25 กม. ซึ่งใช้สารเคมีรุนแรงละลายหิน และเชื่อว่าเป็นต้นเหตุสารพิษในแม่น้ำกก',
        'source': 'facebook.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMisgZBVV95cUxNZlZKVlJvdnpkLW5Id1dsbmxEdE5TalZFa1RRQzctUk1obnNRU0wtaVhVbFBIMkZXbGNQWUlTT2tLSnlNcEg3MWJiTGJRYmR6QWJyZ0JKQTg1OFlNNkZfaDFfdi0ya1ozVEJfZzN6Z095YzMwVnliTE9kYXVnLXR4WjlSdUdNbDAxVlktMjdJQldJZ1ZrVHNYTjBud3VRZTJOUXk0ZHVxLWVkY28zSEpELVVWeG92QTJkR1A2WDJ2SGtyX0lIX0VFNU9lLTZpLXp2TzZQLXlNV1VZZTFIcmR6VGlTUHpvWW15SHk3OGQzeVhRRWhWSnFVZzVYYlltUGx2dGs2dmZxZU5JUURsbG1CelJOdndqemFubUtScFpMVF9qYVB4WjFESXpZUUJHNEhrTFVoQ3drUjRHQkJxZnZtbGFJSHRLdlJvNW1kOEJTTlVXQk5EaWV4aDJpS1hFMHE0T2pFa29ST0JzOXlYcmFDSDAtS0lPU3I0bUVjSThPdUlpU1dlSmxKZlFXX1p5ZV96cVlEQ2gtY19ncHdHalhNS0hkSDJPMDdtZENHOURZNG1ZZTZGcEV3SHNzTy1GZDM2cGJmb1BSdkgxeldBZ2JUMjJXRGRMUFNpWmVpZ080MkVHcjlfRmlyVTdJUUt0aHM2SWkyd2ZZWW0xdnE2UlhwRGwwNVhFYlVyQU1IN09KcnlkRDBmcjhLcGxoVVZNZTBTQ18zem1XX3JkSUdTV0tkMy1icGVIMHdCTDRSSzBsS0tXcFo2bmhlUnhpLU9qS1RGMU5xa0VVV1pKTE9mMDN0UTdrd0RSeUozTkZzXzVvOWVKeWJPTHI0YXJ4LU9EVkNXQ0VQRFYwR2liVlcyZnk1VDVnVVVGbThBOVNRMDJqNmFoaVBaX3FaaDEwRXRhTWlza2dWWXpMVjZtcTR0TEQ4OTczaDVDbUlQT1V2aTdpa05zdEk0SWFSZEZobjd1TlRfeDFTRHZ1dFptLTJUS0ZpQ2NpQWJGNE5US002cjBDZzRaYkR2X3lZZnRTLW1jcEp0emZkLUMzVm9QTUdJc1FPM3BIeVduSUhIMUFubjB2UDNBQQ?oc=5',
        'sourceKey': 'fb_7',
        'pubDate': 'Thu, 15 May 2025',
    },
    {
        'id': 76,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่กกร่ำไห้-แม่อายสะอื้น: ความขมขื่นของคนริมน้ำเชียงใหม่-เชียงราย เมื่อสายน้ำเปื้อนสารพิษ',
        'excerpt': 'รายงานพิเศษถึงความขมขื่นของชาวบ้านริมน้ำในเชียงใหม่และเชียงราย ที่ต้องเผชิญกับผลกระทบจากสายน้ำที่ปนเปื้อนสารพิษ',
        'source': 'The101.world',
        'externalLink': 'https://news.google.com/rss/articles/CBMiaEFVX3lxTFBrRm1mckNKSXQ2dG81azdnT0NsT2FxdVA0X19PU3Q5dnFSZFV4a0x3dlZjT0JTZXZmOU4tN18yMnZoS1VLaGUxemw0dTYtMGJXcUwzZXI0RU5jWjNya0VsSU9yMld2NjZl?oc=5',
        'sourceKey': 'the101_2',
        'pubDate': 'Mon, 26 May 2025',
    },
    {
        'id': 77,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'ชง 10 ข้อเสนอด่วน ถึงรัฐบาลใหม่ แก้ปม \'มลพิษน้ำกก\' ใน 4 เดือน',
        'excerpt': 'ภาคประชาสังคมรวบรวม 10 ข้อเสนอเร่งด่วนส่งถึงรัฐบาลชุดใหม่ เพื่อให้แก้ไขปัญหามลพิษในแม่น้ำกกให้ได้ภายใน 4 เดือน',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE96aHM0b2hqZEdyS2o0SVZ1WENYeEcyMVJEdngwYWdyV0ZfNHJsN2JNUzZEWkR6ckxxajB6Wjl4NVJGSVJHTHVHS1YtWHdDVGlXU2NoQTktdE1wTlp3YW5hM2VESHp6QQ?oc=5',
        'sourceKey': 'theactive_3',
        'pubDate': 'Mon, 15 Sep 2025',
    },
    {
        'id': 78,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '\'สุชาติ\' จ่อลงพื้นที่แม่น้ำกก แก้ปัญหาสารหนู เผยอธิบดีมีวิธีแก้',
        'excerpt': 'รองนายกรัฐมนตรี สุชาติ เตรียมลงพื้นที่แม่น้ำกกเพื่อแก้ปัญหาสารหนู เผยว่าอธิบดีกรมควบคุมมลพิษมีแนวทางในการแก้ไขปัญหาแล้ว',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiW0FVX3lxTFBRWHFyZExJVkRfX1NmcG1FdVRvYkh1aW9Fd0Q0TlYzZVdMSW1hd0JoM3R5eV9VdFc2SnFJaXlWSC1OX0JIT1ZubnpFcWlVZGh3UjNTX2dkdXItRXM?oc=5',
        'sourceKey': 'bkbiz_8',
        'pubDate': 'Sat, 04 Oct 2025',
    },
    {
        'id': 79,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แก้สารปนเปื้อน \'แม่น้ำกก\' ยังไม่คืบ ชาวเชียงรายนัดรวมตัว จัดเวทีฟังเสียงผู้เดือดร้อน',
        'excerpt': 'ปัญหาสารปนเปื้อนในแม่น้ำกกยังคงไม่คืบหน้า ชาวเชียงรายนัดรวมตัวจัดเวทีเพื่อรับฟังความเดือดร้อนของผู้ที่ได้รับผลกระทบ',
        'source': 'Thaipost.net',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTFA3elQ0RC1qc0hfWjNuVE12QnZuekdkWmhkbUZFOF81ZDdvVG9FNnhJSHBLZWxBNkxtSHJReFNQcVc1VjBlcXo2STNyamlMWmZYR3FWQ1psQlFFZw?oc=5',
        'sourceKey': 'thaipost_4',
        'pubDate': 'Fri, 20 Jun 2025',
    },
    {
        'id': 80,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'แนะเจรจาทวิภาคี-พหุภาคี แก้ปัญหาสารพิษปนเปื้อนแม่น้ำกก-สาย',
        'excerpt': 'ผู้เชี่ยวชาญแนะนำให้รัฐบาลไทยใช้การเจรจาทั้งแบบทวิภาคีและพหุภาคีเพื่อแก้ไขปัญหาสารพิษปนเปื้อนในแม่น้ำกกและสาย',
        'source': 'ประชาไท Prachatai.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE9hNkFqa2dtVzY1U3J1VUlabjlzdThRZWNoYmM1OWtoSHpfbWdFeUVyQnR3VjA2VjdEaDEzRHBwNnlvYzJBVVc0aDhsYlhqQmNuWFBNOTQzbw?oc=5',
        'sourceKey': 'prachatai_3',
        'pubDate': 'Sat, 28 Jun 2025',
    },
    {
        'id': 81,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': '\'สุชาติ\' เตรียมลงเชียงราย แก้ปัญหาสารพิษแม่น้ำกก ย้ำเป็นเรื่องเร่งด่วน',
        'excerpt': 'รองนายกรัฐมนตรี สุชาติ เตรียมลงพื้นที่เชียงรายแก้ปัญหาสารพิษแม่น้ำกก ย้ำว่าเป็นเรื่องเร่งด่วน พร้อมเร่งผลักดัน พ.ร.บ.อากาศสะอาด',
        'source': 'Thaigov',
        'externalLink': 'https://news.google.com/rss/articles/CBMiTkFVX3lxTE85RVgwM0djMmE2RGpHR0FoMkttdWJNVUNvUVdxMWRETjBlNzNDcnNRR01jQWZWczBWM25fVDJyaVp1SnRDNjA0dThXZGtPQQ?oc=5',
        'sourceKey': 'thaigov_1',
        'pubDate': 'Fri, 03 Oct 2025',
    },
    {
        'id': 82,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'จี้รัฐบาลใหม่ ตั้งกรรมการระดับชาติ แก้ปัญหาสารพิษแม่น้ำกก-สาย-รวก-โขง',
        'excerpt': 'ภาคประชาชนเรียกร้องให้รัฐบาลชุดใหม่จัดตั้งคณะกรรมการระดับชาติเพื่อแก้ไขปัญหาสารพิษปนเปื้อนในแม่น้ำกก สาย รวก และโขงอย่างเป็นระบบ',
        'source': 'ข่าวสด',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZEFVX3lxTE9kdWNmMExUdDlCRTBhUG5hbzROd3NXckl3VEV0M3hCTzNsdGI0WG5GcTFjejVPNHozcXRVcUtuZHJWZkh1UXloZ2dOeU5uempJbWNRUjdiYjhaX3JrWERSNFIxNnQ?oc=5',
        'sourceKey': 'khaosod_4',
        'pubDate': 'Sat, 27 Sep 2025',
    },
    {
        'id': 83,
        'type': 'normal',
        'badge': '🏥 สุขภาพ',
        'badgeClass': 'badge-health',
        'title': 'จากเหมืองอัคราถึงแม่น้ำกก จะรับมืออย่างไร เมื่อพบสารหนูในเด็ก',
        'excerpt': 'Policy Watch วิเคราะห์บทเรียนจากคดีเหมืองอัครา เพื่อนำมาประยุกต์ใช้กับวิกฤตแม่น้ำกก พร้อมเสนอแนวทางรับมือเมื่อพบสารหนูในเด็ก',
        'source': 'Policy Watch',
        'externalLink': 'https://news.google.com/rss/articles/CBMiaEFVX3lxTE96SUdtdnNuU0FjU3R5ak5QWTdDcm42T20xMFZua3dxQVZZUmxGVGNUakJuU01zc0h0dkRqNlhVbTRFVmo0WWpRNzN6UjNsRV9qZlFHaWxXOUNpYUo1MURjRm1kdUxCb2Fn?oc=5',
        'sourceKey': 'policywatch_3',
        'pubDate': 'Tue, 15 Jul 2025',
    },
    {
        'id': 84,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'มลพิษแม่น้ำกก สารพิษข้ามชาติ รัฐไทยจะแก้ได้กี่โมง',
        'excerpt': 'Stockholm Environment Institute วิเคราะห์ปัญหามลพิษข้ามชาติในแม่น้ำกก พร้อมตั้งคำถามว่ารัฐไทยจะสามารถแก้ไขปัญหานี้ได้เมื่อใด',
        'source': 'Stockholm Environment Institute',
        'externalLink': 'https://news.google.com/rss/articles/CBMi8AFBVV95cUxPQ0dSRTFNaTlQdHpKSHMya1ppMzhCdUtuaF9mOGk3dXpGQkd3RnRMMUZ4bFNiT2dpTUtVRmExc3ZxSFJnYUFpclBmX2Nmdkl4cEFLXzhFS1g1azFCTkU2ektxSGtkMUJmdGxUYUYxU3lGdGxpUTlDNTlwbEpzbkM5U3dMLUw3R0h2RGNzSDNKai11Uk9VMlY4TUFwZ3BUZWVGRGNRMVYxSFk0RnBIVFRJVFNYTTJuWjNYU1ltVkFIcXdiNXNOajNGTTN5X3pWV0tfaTFsZHRHTEF1Rk56MkE0ZDV2T1dXTUtjdmxsVVFOZEg?oc=5',
        'sourceKey': 'sei_1',
        'pubDate': 'Mon, 16 Jun 2025',
    },
    {
        'id': 85,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'มองวิกฤตแม่น้ำกกปนเปื้อนสารพิษ ที่คนทำงานด้านแม่น้ำกังวลว่า อาจทำให้ \'ห่วงโซ่อาหาร\' ติดเชื้อทั้งระบบ',
        'excerpt': 'The MATTER รายงานความกังวลของนักอนุรักษ์และผู้ทำงานด้านแม่น้ำ ที่เชื่อว่าวิกฤตสารพิษในแม่น้ำกกอาจทำให้ห่วงโซ่อาหารทั้งระบบได้รับผลกระทบ',
        'source': 'The MATTER',
        'externalLink': 'https://news.google.com/rss/articles/CBMia0FVX3lxTE4xYVBxNjdHcC1VTUNRVjQzZHJMUFgtZ1RwNzJDT2tmY0lRUDNScU11T2FEcGtEN0N3MTl5WUlaWHExZzJqWUQ0RVhCSzVUbm9rSkQ3R09oeVh2NURwRkhkTVJfTVBvejhpN2xF?oc=5',
        'sourceKey': 'thematter_1',
        'pubDate': 'Thu, 22 May 2025',
    },
    {
        'id': 86,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เชียงรายเสี่ยงท่วม! แม่น้ำกกจ่อวิกฤต ชาวบ้านเร่งย้ายสิ่งของ',
        'excerpt': 'ระดับน้ำในแม่น้ำกกสูงขึ้นอย่างรวดเร็ว เสี่ยงน้ำท่วมในพื้นที่เชียงราย ชาวบ้านริมน้ำเร่งย้ายสิ่งของขึ้นที่สูง',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTE1hcnF1Ylg2QTJTY1hNWHI1YnF3YkVybFNmc3V4TTlsNVIzRHlSUl9Cam5hSDNENlBzRmNaX0ZrbUFMTmlwQXYxRWRPZzNDUG9JdmFTMmQxQU9BRmFVYXlZNUx5UFZSZw?oc=5',
        'sourceKey': 'bkbiz_9',
        'pubDate': 'Tue, 29 Jul 2025',
    },
    {
        'id': 87,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'อาการประหลาด ปลาแม่น้ำกก แค่ \'ปรสิต\' หรือส่งสัญญาณ ภัยเงียบจากสารหนู?',
        'excerpt': 'The Active วิเคราะห์อาการผิดปกติของปลาในแม่น้ำกก ว่าเป็นเพียงการติดเชื้อปรสิตทั่วไป หรือเป็นสัญญาณเตือนจากสารหนูที่ปนเปื้อน',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMiaEFVX3lxTE5YUWNaVlh2aXMzRmpPUlBIajNETWMyTS1WbmM4SEUwNDEyeVFHc0tqUzdwMGNvOEtIRXAxc0lHQjFoNEU0d0NPeWxWS2k0ZXd3OE5wV0tpZFpsX1hERktvWlhEaWE1RDY2?oc=5',
        'sourceKey': 'theactive_4',
        'pubDate': 'Wed, 21 May 2025',
    },
    {
        'id': 88,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'คพ. พบสารหนูเกินค่ามาตรฐานในแม่น้ำกก, แม่น้ำสาย, แม่น้ำรวก และแม่น้ำโขง โดยเฉพาะในพื้นที่จังหวัดเชียงราย-เชียงใหม่',
        'excerpt': 'กรมควบคุมมลพิษพบสารหนูเกินค่ามาตรฐานในแม่น้ำกก สาย รวก และโขง โดยเฉพาะในพื้นที่เชียงรายและเชียงใหม่',
        'source': 'thestandard.co',
        'externalLink': 'https://news.google.com/rss/articles/CBMiaEFVX3lxTE1ZOGxVWTRpXzFQczVzdjBfSFhpZEZHdzZCaWc2dGpSVzhEYU5ndzhFNXRydzZRZ2JHTHVfa19WQTdnVDJoLWY2dURNcWQtZHJ4QUZ1cWt6UDM0MVpzTGRBZV82WGN1LWNw?oc=5',
        'sourceKey': 'standard_3',
        'pubDate': 'Fri, 25 Jul 2025',
    },
    {
        'id': 89,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'วิกฤตการเมืองส่งผลกระทบ "ไร้เจ้าภาพ" แก้ปัญหามลพิษแม่น้ำกก',
        'excerpt': 'The Active รายงานว่าวิกฤตการเมืองในประเทศส่งผลกระทบโดยตรงต่อการแก้ไขปัญหามลพิษแม่น้ำกก เนื่องจากขาดผู้รับผิดชอบที่ชัดเจน',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZkFVX3lxTFBQQjJKSG15SzY4enotcFlrT05lalBxSnJXdTFScndYMklRbDFoLTFlTXRqcDBRREtuMFBSNHNLLVl2Unl6SDJYaGtHUThPNHpfOFcyeEI0ZGttNzJHb1lWVmhHUzM3Zw?oc=5',
        'sourceKey': 'theactive_5',
        'pubDate': 'Sat, 30 Aug 2025',
    },
    {
        'id': 90,
        'type': 'normal',
        'badge': '💰 งบประมาณ',
        'badgeClass': 'badge-primary',
        'title': 'Rocket Media Lab เปิดตัวผลกระทบทางเศรษฐกิจแม่น้ำกกปนเปื้อนสารพิษ',
        'excerpt': 'Rocket Media Lab เปิดตัวรายงานวิเคราะห์ผลกระทบทางเศรษฐกิจจากปัญหาสารพิษปนเปื้อนในแม่น้ำกก',
        'source': 'ประชาไท Prachatai.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE9WZ1lUVENmXzFOS1ZqcFVSY0xROE44bzN5dXZOVUg1VzV3aXUwWFFMSG5FQUtOVFhfMlU1bUdfYThpbzVNSnpaTGsxZmx5cFFucVh6bXdCOA?oc=5',
        'sourceKey': 'prachatai_4',
        'pubDate': 'Mon, 04 Aug 2025',
    },
    {
        'id': 91,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แก้พิษ \'สารหนู\' เปื้อนน้ำกก ใคร..ทำอะไร…ถึงไหนแล้ว?',
        'excerpt': 'The Active ตรวจสอบความคืบหน้าการแก้ไขปัญหาสารหนูปนเปื้อนในแม่น้ำกก ว่าแต่ละหน่วยงานที่เกี่ยวข้องได้ดำเนินการอะไรไปบ้าง',
        'source': 'The Active',
        'externalLink': 'https://news.google.com/rss/articles/CBMifkFVX3lxTE5Fd29uaW1Vc3luWXByX1RxWnQwYlljZ3l0bXdNb3ItazdmVUNFRkFzNkZmeVV5NVk0OWVUTWRGdlNpLWxTMVhtajhCZkotUXY2Yy1kUndJZ0h2VVA0QkJsSXNtekxxNklDV2h5Vm93eGdtZ0NjN0pyZE00X1MwZw?oc=5',
        'sourceKey': 'theactive_6',
        'pubDate': 'Sat, 17 May 2025',
    },
    {
        'id': 92,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เชียงรายผวา คพ. ตรวจคุณภาพน้ำแม่น้ำกกและลำน้ำสาขา ครั้งที่ 6 พบสารหนูเกินค่ามาตรฐานทุกจุด',
        'excerpt': 'ผลการตรวจคุณภาพน้ำแม่น้ำกกและลำน้ำสาขาครั้งที่ 6 พบสารหนูเกินค่ามาตรฐานทุกจุดที่ตรวจ ทำให้ชาวเชียงรายเกิดความตื่นตระหนก',
        'source': 'Matichon Online',
        'externalLink': 'https://news.google.com/rss/articles/CBMiWkFVX3lxTE1meVY4N3ZlYjYwMEVYQjNuZlFGT1c2bW54ZHdHbGRQandTbEc1aDIyTWFETkNWSzNkN1JoRFREZnN1VU9HcUM0VWVNYVFidDFhZnZvdzNkU29iZw?oc=5',
        'sourceKey': 'matichon_3',
        'pubDate': 'Fri, 18 Jul 2025',
    },
    {
        'id': 93,
        'type': 'normal',
        'badge': '🏛️ รัฐบาล',
        'badgeClass': 'badge-info',
        'title': 'จี้รัฐเร่งแก้ปัญหาสารพิษในแม่น้ำกก แม่น้ำสาย และแม่น้ำรวก ดึงจีนร่วมตรวจสอบเหมืองต้นน้ำ',
        'excerpt': 'ภาคประชาชนเรียกร้องให้รัฐเร่งแก้ปัญหาสารพิษในแม่น้ำกก สาย และรวก พร้อมเชิญชวนให้ดึงจีนเข้ามาร่วมตรวจสอบเหมืองที่ต้นน้ำ',
        'source': 'ประชาไท Prachatai.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiV0FVX3lxTE1XSzFDc25oYXhoUEFlY205VnU0V3JuQ3RITW1ta3BHT3NHUXVNVDFMekxhTVhVeVhGNm13bDFQQkctY096aVNSTU1fMEEwWlEwRlI3ci1FMA?oc=5',
        'sourceKey': 'prachatai_5',
        'pubDate': 'Sun, 22 Jun 2025',
    },
    {
        'id': 94,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'เหมืองจีนลุ่มน้ำกก เสี่ยงทำสารพิษไหลลงโขง หลังจีนทำเหมืองแรร์เอิร์ธ ที่เคยทำสัตว์ตาย-คนเป็นโรค',
        'excerpt': 'The Momentum วิเคราะห์ความเสี่ยงจากการทำเหมืองแรร์เอิร์ธของจีนในลุ่มน้ำกก ซึ่งอาจทำให้สารพิษไหลลงสู่แม่น้ำโขง เหมือนที่เคยเกิดขึ้นในพื้นที่อื่น',
        'source': 'The Momentum',
        'externalLink': 'https://news.google.com/rss/articles/CBMibkFVX3lxTFBodXNxRElVZWZtR0k5YTVhbUxxaktLdE9NX09hTnRPaTU4dGZtczdzUHltOXp1dmVNZm1NcjB5amxfZVl3MFd3YnJvMFNxYndJQW9EM2tWd1ZFOEJkbXdBQTBjOUpkNzA0bjN6b0N3?oc=5',
        'sourceKey': 'momentum_1',
        'pubDate': 'Thu, 15 May 2025',
    },
    {
        'id': 95,
        'type': 'normal',
        'badge': '🔵 ทั่วไป',
        'badgeClass': 'badge-normal',
        'title': 'ด่วนสุดระทึก นักเรียนหญิง ตกลงไปในแม่น้ำกก ลอยเกาะขอนไม้เอาชีวิตรอด',
        'excerpt': 'เหตุการณ์ฉุกเฉิน นักเรียนหญิงพลัดตกลงไปในแม่น้ำกก โชคดีสามารถเกาะขอนไม้ลอยน้ำรอความช่วยเหลือจนปลอดภัย',
        'source': 'ข่าวสด',
        'externalLink': 'https://news.google.com/rss/articles/CBMiZEFVX3lxTE5YWlpUZ0NlbEt0NGtjMTE1UDdtVTZLd3BuNmZtQ0RrMEQ0RVVnOEdJbEtBMmg0Ny0xdlo1RU9rb25OdFA3QnRhcm1icnhvdFhfM1FXZ2VIUl8walZLWmp6bmxQckE?oc=5',
        'sourceKey': 'khaosod_5',
        'pubDate': 'Mon, 14 Jul 2025',
    },
    {
        'id': 96,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'ซินโครตรอน วิเคราะห์แม่น้ำกก พบสารหนูอยู่ในเกณฑ์ปลอดภัย',
        'excerpt': 'สถาบันวิจัยแสงซินโครตรอน (องค์การมหาชน) วิเคราะห์ตัวอย่างน้ำจากแม่น้ำกก พบว่าระดับสารหนูยังอยู่ในเกณฑ์ปลอดภัย',
        'source': 'bangkokbiznews.com',
        'externalLink': 'https://news.google.com/rss/articles/CBMiX0FVX3lxTE9vVU9nckZ3Q2NQSjhNMVprcmNuamRLNzRxYWwzUjFjb0xCNHNfMDJXQ0haRVEyTHo0MlpDSnA3UE1kU1RlbElpV2FIRGpzeWpJc1VWcDlLRUU1YWhvRkt3?oc=5',
        'sourceKey': 'bkbiz_10',
        'pubDate': 'Fri, 05 Sep 2025',
    },
    {
        'id': 97,
        'type': 'normal',
        'badge': '⚠️ สิ่งแวดล้อม',
        'badgeClass': 'badge-warning',
        'title': 'แม่น้ำกก - แม่น้ำสาย หลากชีวิตสังเวยมลพิษข้ามแดน',
        'excerpt': 'ไทยรัฐรายงานชีวิตผู้คนที่ต้องแบกรับผลกระทบจากมลพิษข้ามแดนในแม่น้ำกกและสาย ครอบคลุมทั้งมิติสุขภาพ เศรษฐกิจ และสังคม',
        'source': 'Thairath.co.th',
        'externalLink': 'https://news.google.com/rss/articles/CBMiXkFVX3lxTE9qSmp4SVZXQTVvMUZWdm5jbDh5Y2F5aFJvRkI2YWNBV2YzQ1plN1lzZV9YclR5MXJ1b3RhZGRnQjNtQWlDU1l2dXY2VTNjSXRQQk42ZUxCYUluSEI0ZUE?oc=5',
        'sourceKey': 'thairath_3',
        'pubDate': 'Sat, 28 Jun 2025',
    },
]

# แก้ไขฟังก์ชัน get_ryt9_news() ให้ใช้ Hardcode
@app.route('/api/ryt9-news', methods=['GET'])
def get_ryt9_news():
    """ดึงข่าวจาก HARDCODED_NEWS (ไม่ต้องใช้ Database)"""
    try:
        print("✅ Using hardcoded news data")
        
        return jsonify({
            'success': True,
            'data': HARDCODED_NEWS,
            'count': len(HARDCODED_NEWS),
            'cached': False
        })
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# === Main Entry Point ===
if __name__ == '__main__':
    # สร้างตารางถ้ายังไม่มี
    init_db()
    port = int(os.environ.get('PORT', 8080))
    print("=" * 50)
    print("🚀 กำลังเริ่มเว็บแอปพลิเคชัน...")
    print("=" * 50)
    print(f"📊 ฐานข้อมูล: PostgreSQL (Neon)")
    print(f"🌐 เปิดเบราว์เซอร์ที่: http://localhost:{port}")
    print("=" * 50)
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)