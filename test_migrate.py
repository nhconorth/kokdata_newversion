# test_migrate.py
import os
from dotenv import load_dotenv
load_dotenv()

# Import ฟังก์ชันจาก migrate_to_supabase.py
import migrate_to_supabase

try:
    conn = migrate_to_supabase.get_supabase_connection()
    print("✅ Connected via migrate script!")
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")