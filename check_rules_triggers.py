from db_adapter import get_connection

def main():
    conn = get_connection()
    try:
        print("Checking pg_rules:")
        rules = conn.execute("SELECT * FROM pg_rules;").fetchall()
        for r in rules:
            print(f"  Rule: {r['schemaname']}.{r['tablename']} | Name: {r['rulename']}")

        print("\nChecking all triggers (including internal ones):")
        triggers = conn.execute("""
            SELECT tgname, relname, pg_get_triggerdef(t.oid) as def
            FROM pg_trigger t
            JOIN pg_class c ON t.tgrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = 'public';
        """).fetchall()
        for tg in triggers:
            print(f"  Trigger: {tg['tgname']} on {tg['relname']}")
            print(f"    Def: {tg['def']}")

    except Exception as e:
        print("Error:", e)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
