from database import apply_auth_migration, seed_auth_basics, seed_subsystem_admins_from_db

def run():
    apply_auth_migration()
    seed_auth_basics()
    seed_subsystem_admins_from_db("Aa123456")
    print("Seeded Subsystem.Admin users with default password 'Aa123456'.")

if __name__ == "__main__":
    run()
