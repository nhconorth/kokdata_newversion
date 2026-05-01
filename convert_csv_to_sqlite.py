#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert CSV files in the csv folder to SQLite database
"""

import csv
import sqlite3
import os
from pathlib import Path

# Database file path
DB_PATH = "kok_data.db"

# CSV folder path
CSV_FOLDER = "csv"

def create_table_from_csv(conn, csv_path, table_name):
    """Create a table from CSV file and import data"""
    print(f"Processing {csv_path}...")
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        # Read first line to get column names
        reader = csv.reader(f)
        columns = next(reader)
        
        # Clean column names (remove spaces, replace special chars)
        clean_columns = [col.strip() for col in columns]
        
        # Create table with columns
        placeholders = ', '.join([f'"{col}" TEXT' for col in clean_columns])
        create_table_sql = f'''
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {placeholders}
        )
        '''
        
        conn.execute(create_table_sql)
        
        # Insert data
        insert_sql = f'''
        INSERT INTO "{table_name}" ({', '.join([f'"{col}"' for col in clean_columns])})
        VALUES ({', '.join(['?' for _ in clean_columns])})
        '''
        
        rows_inserted = 0
        for row in reader:
            # Pad row if it's shorter than columns
            while len(row) < len(clean_columns):
                row.append('')
            # Truncate row if it's longer than columns
            row = row[:len(clean_columns)]
            conn.execute(insert_sql, row)
            rows_inserted += 1
        
        conn.commit()
        print(f"  ✓ Created table '{table_name}' with {rows_inserted} rows")
        return rows_inserted

def main():
    # Remove existing database if it exists
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Removed existing database: {DB_PATH}")
    
    # Create SQLite connection
    conn = sqlite3.connect(DB_PATH)
    print(f"Creating SQLite database: {DB_PATH}\n")
    
    # CSV files to process
    csv_files = {
        'water_raw_melted.csv': 'water_data',
        'soil_raw_melted.csv': 'soil_data',
        'station.csv': 'station_data'
    }
    
    total_rows = 0
    for csv_file, table_name in csv_files.items():
        csv_path = os.path.join(CSV_FOLDER, csv_file)
        if os.path.exists(csv_path):
            rows = create_table_from_csv(conn, csv_path, table_name)
            total_rows += rows
        else:
            print(f"  ✗ File not found: {csv_path}")
    
    conn.close()
    print(f"\n✓ Conversion complete!")
    print(f"  Database: {DB_PATH}")
    print(f"  Total rows imported: {total_rows}")

if __name__ == "__main__":
    main()


