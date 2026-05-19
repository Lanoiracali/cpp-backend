from db_adapter import get_connection

def main():
    conn = get_connection()
    try:
        print("Columns in record table:")
        columns = conn.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'record';
        """).fetchall()
        for col in columns:
            print(f"  Column: {col['column_name']} | Type: {col['data_type']}")
            
        print("\nAll tables in database:")
        tables = conn.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        """).fetchall()
        for t in tables:
            print(f"  Table: {t['table_name']}")
            
        print("\nChecking for triggers on any table:")
        triggers = conn.execute("""
            SELECT trigger_name, event_object_table, action_statement 
            FROM information_schema.triggers;
        """).fetchall()
        for tg in triggers:
            print(f"  Trigger: {tg['trigger_name']} on {tg['event_object_table']} | Action: {tg['action_statement']}")

        print("\nChecking for foreign key constraints referencing record:")
        fks = conn.execute("""
            SELECT
                tc.constraint_name, 
                tc.table_name, 
                kcu.column_name, 
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name 
            FROM 
                information_schema.table_constraints AS tc 
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY';
        """).fetchall()
        for fk in fks:
            print(f"  FK: {fk['constraint_name']} | Table: {fk['table_name']}.{fk['column_name']} -> {fk['foreign_table_name']}.{fk['foreign_column_name']}")

    except Exception as e:
        print("Error:", e)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
