# test_supabase.py
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('SUPABASE_DATABASE_URL')
if 'sslmode' not in db_url.lower():
    db_url += "&sslmode=require" if '?' in db_url else "?sslmode=require"

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    print(f"✅ Connected! Version: {cur.fetchone()[0]}")
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")