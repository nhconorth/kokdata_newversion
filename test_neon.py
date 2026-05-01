#!/usr/bin/env python3
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn_string = os.environ.get('DATABASE_URL')

if not conn_string:
    print("❌ DATABASE_URL not set!")
    exit(1)

print(f"Connecting to: {conn_string[:50]}...")  # แสดงแค่บางส่วน

try:
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()
    print(f"✅ Connected successfully!")
    print(f"PostgreSQL version: {version[0]}")
    conn.close()
except Exception as e:
    print(f"❌ Connection failed: {e}")