# diagnose_login.py
from database import get_connection, seed_subsystem_admins_from_db, set_subsystem_admin_password, verify_credentials

def show_users():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, is_active FROM users ORDER BY id")
        rows = cur.fetchall()
        print("== users ==")
        for r in rows:
            print(r)

def show_subsystems():
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, name FROM subsystems ORDER BY id")
            print("== subsystems ==")
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            print("no subsystems table? error:", e)

def try_login(u, p):
    urow = verify_credentials(u, p)
    print(f"verify_credentials({u!r}, ****) ->", bool(urow))

if __name__ == "__main__":
    show_subsystems()
    show_users()
    # ensure seeded (safe to call multiple times)
    seed_subsystem_admins_from_db("Aa123456")
    # test login
    try_login("ADCS.Admin", "Aa123456")
    # If still False, force-reset the password once:
    # from database import set_subsystem_admin_password
    # set_subsystem_admin_password("ADCS", "Aa123456")
    # try_login("ADCS.Admin", "Aa123456")
