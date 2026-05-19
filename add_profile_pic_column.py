"""
Migration script to add profile_pic column to users table.
Run: python add_profile_pic_column.py
"""
import os
from db_adapter import get_connection

def add_profile_pic_column():
    connection = get_connection()
    
    try:
        # Try to add the column
        connection.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS profile_pic TEXT
        """)
        connection.commit()
        print("✓ Successfully added profile_pic column to users table")
    except Exception as e:
        print(f"! Column may already exist or error: {e}")
        connection.rollback()
    finally:
        connection.close()

if __name__ == "__main__":
    add_profile_pic_column()
