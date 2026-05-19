from db_adapter import get_connection

try:
    conn = get_connection()
    result = conn.execute("SELECT COUNT(*) FROM users WHERE profile_pic IS NULL OR profile_pic IS NOT NULL")
    row = result.fetchone()
    print("✓ profile_pic column verified - query executed successfully")
    conn.close()
except Exception as e:
    print(f"✗ Error: {e}")
