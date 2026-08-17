import psycopg2
from psycopg2 import pool
import json
import os
import re
import shutil
import sys
import threading
from contextlib import contextmanager
import time
import atexit
import bcrypt
from psycopg2.extras import Json
import socket
from datetime import timedelta
from datetime import datetime

# -----------------------------------------------------------------------------
# Database configuration (ENV first; UI dialog can override via set_db_config)
# -----------------------------------------------------------------------------
DB_CONFIG = {
    'host': os.getenv('SA_DB_HOST', 'localhost'),
    'database': os.getenv('SA_DB_NAME', 'systemarchitecture'),
    'user': os.getenv('SA_DB_USER', 'postgres'),
    'password': os.getenv('SA_DB_PASS', '2981390228'),
    'port': int(os.getenv('SA_DB_PORT', '5432')),
}

# -----------------------------------------------------------------------------
# Globals
# -----------------------------------------------------------------------------
current_project_id = None
current_project_name = None
connection_pool = None
pool_lock = threading.Lock()

# -----------------------------------------------------------------------------
# Pin types & same-type wiring rules
# -----------------------------------------------------------------------------
# Fixed electrical classes recognised everywhere (ground / power) plus the
# default data pin types seeded for the admin-managed list.
GROUND_PIN_ALIASES = {"GND", "GROUND", "VSS", "AGND", "DGND"}
POWER_PIN_ALIASES = {"VCC", "VDD", "POWER", "PWR", "VOLTAGE"}
DEFAULT_PIN_TYPES = [
    "UART", "RS422", "RS485", "RS232", "TTL", "CAN", "I2C", "SPI",
    "QSPI", "LVDS", "SpaceWire", "SpaceFibre",
]


def init_connection_pool(min_conn=2, max_conn=20):
    """Initialize the global connection pool if not already initialized."""
    global connection_pool
    with pool_lock:
        if connection_pool is None:
            try:
                connection_pool = psycopg2.pool.ThreadedConnectionPool(
                    min_conn, max_conn, **DB_CONFIG
                )
                print("Connection pool initialized successfully")
            except Exception as e:
                print(f"Error initializing connection pool: {e}")
                raise


@contextmanager
def get_connection():
    """Yield a connection from the pool as a context manager."""
    if connection_pool is None:
        init_connection_pool()

    conn = None
    try:
        conn = connection_pool.getconn()
        if conn:
            yield conn
        else:
            raise RuntimeError("Could not get connection from pool")
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            connection_pool.putconn(conn)


def set_db_config(host, database, user, password, port=5432):
    """Override DB_CONFIG at runtime (e.g., from UI dialog) and reset the pool."""
    global DB_CONFIG, connection_pool
    DB_CONFIG = {
        'host': host,
        'database': database,
        'user': user,
        'password': password,
        'port': port
    }
    with pool_lock:
        if connection_pool:
            connection_pool.closeall()
            connection_pool = None


def _is_missing_database_error(e):
    """Return True if the exception indicates the target database does not exist.

    Uses the SQLSTATE code when available (psycopg2: '3D000' =
    invalid_catalog_name), falling back to the message text.
    """
    if getattr(e, 'pgcode', None) == '3D000':
        return True
    msg = str(e).lower()
    return 'database' in msg and 'does not exist' in msg


def test_connection():
    """Return (ok:boolean, message:str) for a simple connectivity test.

    On the very first launch the target database may not exist yet on the
    PostgreSQL server; in that case it is created automatically first.
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
        return True, "Connection successful"
    except Exception as e:
        if _is_missing_database_error(e):
            # Fresh server — create the database, then retry once.
            ok, create_err = _create_database(
                DB_CONFIG.get('database', 'systemarchitecture')
            )
            if ok:
                try:
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT 1")
                    return True, "Connection successful (database was created)"
                except Exception as e2:
                    return False, f"Connection failed: {str(e2)}"
            return False, (
                "Connection failed: the database does not exist and could not "
                f"be created automatically. Reason: {create_err}"
            )
        return False, f"Connection failed: {str(e)}"


def server_database_exists(db_name=None):
    """
    Check whether the target database exists on the PostgreSQL server.
    Uses the maintenance 'postgres' database to inspect.
    """
    db_name = db_name or DB_CONFIG.get('database', 'systemarchitecture')
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database='postgres',
            connect_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as e:
        print(f"Error checking database existence: {e}")
        return False


def _create_database(db_name):
    """Create the database, returning (ok:bool, error_msg:str).

    Tries the server default template first, then falls back to
    ``TEMPLATE template0``. On PostgreSQL >= 16 the default template
    (template1) can fail with a 'collation version mismatch' when the
    operating system's libc collation was upgraded after the cluster was
    initialized; template0 carries no collation version, so it is exempt
    from that check and always works.
    """
    # Guard against SQL injection / malformed identifiers.
    if not re.match(r'^[A-Za-z0-9_]+$', db_name):
        return False, f"invalid database name: {db_name!r}"
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database='postgres',
            connect_timeout=5,
        )
        conn.autocommit = True
        try:
            cur = conn.cursor()
            last_error = None
            for statement in (
                f'CREATE DATABASE "{db_name}"',
                f'CREATE DATABASE "{db_name}" TEMPLATE template0',
            ):
                try:
                    cur.execute(statement)
                    print(f"Database '{db_name}' created successfully")
                    return True, ""
                except Exception as e:
                    last_error = str(e)
                    # A connection drop after a successful CREATE would
                    # surface as an error too — re-check before giving up.
                    if server_database_exists(db_name):
                        return True, ""
            print(f"Error creating database: {last_error}")
            return False, last_error
        finally:
            conn.close()
    except Exception as e:
        print(f"Error creating database: {e}")
        return False, str(e)


def create_server_database(db_name=None):
    """
    Create the target database if it does not exist yet.
    Returns True if the database exists (or was created), False on failure.
    """
    db_name = db_name or DB_CONFIG.get('database', 'systemarchitecture')
    if server_database_exists(db_name):
        return True
    ok, _ = _create_database(db_name)
    return ok


def ensure_database_initialized():
    """
    Make sure the app database exists and the schema is fully initialized.
    Creates the database if missing (first launch), then runs init_db() to
    create/upgrade all tables. Safe to call on every launch.
    Returns (ok:bool, message:str).
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
    except Exception as e:
        if _is_missing_database_error(e):
            ok, create_err = _create_database(
                DB_CONFIG.get('database', 'systemarchitecture')
            )
            if not ok:
                return False, f"Could not create database: {create_err}"
        else:
            return False, f"Connection failed: {str(e)}"
    try:
        init_db()
        return True, "Database initialized"
    except Exception as e:
        return False, f"Error initializing schema: {str(e)}"


def get_app_data_dir():
    """Get app data directory (used if needed for config files)."""
    if hasattr(sys, '_MEIPASS'):
        if os.name == 'nt':  # Windows
            app_data = os.path.join(os.environ.get('APPDATA', '.'), 'SystemArchitecture')
        else:  # Linux/Mac
            app_data = os.path.join(os.path.expanduser('~'), '.systemarchitecture')
    else:
        app_data = os.path.dirname(os.path.abspath(__file__))

    try:
        os.makedirs(app_data, exist_ok=True)
    except Exception:
        app_data = '.'

    return app_data


def set_current_project(project_name):
    """Set the current project by name and cache its id."""
    global current_project_id, current_project_name
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
            row = cur.fetchone()
            if row:
                current_project_id = row[0]
                current_project_name = project_name
                return True
            else:
                raise ValueError(f"Project '{project_name}' not found")
    except Exception as e:
        print(f"Error setting current project: {e}")
        return False


def get_current_project_name():
    return current_project_name


def get_current_project_id():
    return current_project_id


def create_new_project(project_name, save_path=None):
    """Create a new project and seed initial subsystems."""
    if not project_name:
        return False, "Project name cannot be empty"

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Ensure schema exists
            init_db()

            # Unique name check
            cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
            if cur.fetchone():
                return False, f"Project '{project_name}' already exists"

            # Insert project
            cur.execute(
                "INSERT INTO projects (name, created_at) VALUES (%s, CURRENT_TIMESTAMP) RETURNING id",
                (project_name,)
            )
            project_id = cur.fetchone()[0]

            # Cache current project
            global current_project_id, current_project_name
            current_project_id = project_id
            current_project_name = project_name

            # Seed basic subsystems
            subsystems = ['OBC', 'ADCS', 'Power', 'Propulsion', 'Structure', 'TT&C', 'Thermal', 'Payload']
            for s in subsystems:
                cur.execute(
                    "INSERT INTO subsystems (name, project_id) VALUES (%s, %s)",
                    (s, project_id)
                )

            conn.commit()
            return True, f"Project '{project_name}' created successfully"
    except Exception as e:
        return False, f"Error creating project: {str(e)}"
    
def create_new_project_guarded(user_id: int, project_name: str, save_path=None):
    """
    Guarded wrapper: requires 'project.create' permission at DB layer.
    """
    guard_or_raise(
        user_id,
        "project.create",
        target_subsystem_id=None,
        action_label="project.create",
        details={"project_name": project_name},
    )
    return create_new_project(project_name, save_path)


def open_existing_project(project_name):
    """Open an existing project by name."""
    if not project_name:
        return False, "Project name cannot be empty"
    try:
        if set_current_project(project_name):
            return True, f"Project '{project_name}' opened successfully"
        else:
            return False, f"Project '{project_name}' not found"
    except Exception as e:
        return False, f"Error opening project: {str(e)}"


def get_all_projects():
    """Return project names ordered by creation date desc."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            init_db()
            cur.execute("SELECT name FROM projects ORDER BY created_at DESC")
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error getting projects: {e}")
        return []


def database_exists(project_name):
    """Check project existence by name."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
            return cur.fetchone() is not None
    except Exception:
        return False


def _table_exists(cursor, table_name):
    """Return True if a table exists."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        )
    """, (table_name,))
    return cursor.fetchone()[0]


def _column_exists(cursor, table_name, column_name):
    """Return True if a column exists."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        )
    """, (table_name, column_name))
    return cursor.fetchone()[0]


def _add_column_if_not_exists(cursor, table_name, column_name, column_type):
    """Add a column if it does not exist (best-effort)."""
    try:
        if not _column_exists(cursor, table_name, column_name):
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            return True
    except Exception as e:
        print(f"Warning: Could not add column {column_name} to {table_name}: {e}")
    return False


def init_db():
    """Create/upgrade schema. Safe to call multiple times."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # --- projects ---
            if not _table_exists(cur, 'projects'):
                cur.execute("""
                    CREATE TABLE projects (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        description TEXT
                    )
                """)

            # --- subsystems ---
            if not _table_exists(cur, 'subsystems'):
                cur.execute("""
                    CREATE TABLE subsystems (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        project_id INTEGER NOT NULL,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        UNIQUE(name, project_id)
                    )
                """)

            # --- modules ---
            if not _table_exists(cur, 'modules'):
                cur.execute("""
                    CREATE TABLE modules (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        subsystem_id INTEGER,
                        project_id INTEGER NOT NULL,
                        mass REAL DEFAULT 0.0,
                        power REAL DEFAULT 0.0,
                        min_temp REAL,
                        max_temp REAL,
                        num_connectors INTEGER DEFAULT 0,
                        color VARCHAR(7) DEFAULT '#C8C8FF',
                        photo TEXT,
                        pos_x REAL DEFAULT 0,
                        pos_y REAL DEFAULT 0,
                        width REAL DEFAULT 120,
                        height REAL DEFAULT 80,
                        FOREIGN KEY(subsystem_id) REFERENCES subsystems(id),
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # --- connectors ---
            if not _table_exists(cur, 'connectors'):
                cur.execute("""
                    CREATE TABLE connectors (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        module_id INTEGER,
                        project_id INTEGER NOT NULL,
                        number_of_pins INTEGER DEFAULT 0,
                        color VARCHAR(7) DEFAULT '#C8C8FF',
                        pos_x REAL DEFAULT 0,
                        pos_y REAL DEFAULT 0,
                        width REAL DEFAULT 60,
                        height REAL DEFAULT 20,
                        side VARCHAR(10) DEFAULT 'top',
                        collapsed BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY(module_id) REFERENCES modules(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # --- pins ---
            if not _table_exists(cur, 'pins'):
                cur.execute("""
                    CREATE TABLE pins (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        connector_id INTEGER,
                        project_id INTEGER NOT NULL,
                        pin_number INTEGER,
                        pin_type VARCHAR(50),
                        is_ground BOOLEAN DEFAULT FALSE,
                        value REAL,
                        current REAL DEFAULT 0.0,
                        description TEXT,
                        FOREIGN KEY(connector_id) REFERENCES connectors(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # --- entity_attachments (datasheets / files attached to a
            # module, connector or pin; files are copied into the project's
            # managed attachments folder) ---
            if not _table_exists(cur, 'entity_attachments'):
                cur.execute("""
                    CREATE TABLE entity_attachments (
                        id SERIAL PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        entity_type VARCHAR(16) NOT NULL,  -- module | connector | pin
                        entity_id INTEGER NOT NULL,
                        file_path TEXT NOT NULL,
                        file_name TEXT NOT NULL,
                        uploaded_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_attachments_entity "
                    "ON entity_attachments(project_id, entity_type, entity_id)"
                )

            # --- interfaces ---
            if not _table_exists(cur, 'interfaces'):
                cur.execute("""
                    CREATE TABLE interfaces (
                        id SERIAL PRIMARY KEY,
                        pin1_id INTEGER,
                        pin2_id INTEGER,
                        project_id INTEGER NOT NULL,
                        interface_type VARCHAR(100),
                        color VARCHAR(7) DEFAULT '#C8C8FF',
                        current REAL DEFAULT 0,
                        pos_x REAL DEFAULT 0,
                        pos_y REAL DEFAULT 0,
                        rotation REAL DEFAULT 0,
                        FOREIGN KEY(pin1_id) REFERENCES pins(id) ON DELETE CASCADE,
                        FOREIGN KEY(pin2_id) REFERENCES pins(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # --- interface_points ---
            if not _table_exists(cur, 'interface_points'):
                cur.execute("""
                    CREATE TABLE interface_points (
                        id SERIAL PRIMARY KEY,
                        interface_id INTEGER,
                        project_id INTEGER NOT NULL,
                        point_index INTEGER NOT NULL,
                        x REAL NOT NULL,
                        y REAL NOT NULL,
                        FOREIGN KEY(interface_id) REFERENCES interfaces(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # --- pin_types (admin-managed data pin types) ---
            if not _table_exists(cur, 'pin_types'):
                cur.execute("""
                    CREATE TABLE pin_types (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(50) NOT NULL UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # --- modes ---
            if not _table_exists(cur, 'modes'):
                cur.execute("""
                    CREATE TABLE modes (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        project_id INTEGER NOT NULL,
                        data TEXT NOT NULL,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        UNIQUE(name, project_id)
                    )
                """)

            # --- mode_modules ---
            if not _table_exists(cur, 'mode_modules'):
                cur.execute("""
                    CREATE TABLE mode_modules (
                        id SERIAL PRIMARY KEY,
                        mode_name VARCHAR(255) NOT NULL,
                        module_id INTEGER NOT NULL,
                        project_id INTEGER NOT NULL,
                        FOREIGN KEY(module_id) REFERENCES modules(id) ON DELETE CASCADE,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        UNIQUE(mode_name, module_id, project_id)
                    )
                """)

            # --- mode_positions ---
            if not _table_exists(cur, 'mode_positions'):
                cur.execute("""
                    CREATE TABLE mode_positions (
                        id SERIAL PRIMARY KEY,
                        mode_name VARCHAR(255) NOT NULL,
                        item_type VARCHAR(20) NOT NULL,
                        item_id INTEGER NOT NULL,
                        project_id INTEGER NOT NULL,
                        pos_x REAL DEFAULT 0,
                        pos_y REAL DEFAULT 0,
                        width REAL DEFAULT 0,
                        height REAL DEFAULT 0,
                        rotation REAL DEFAULT 0,
                        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        UNIQUE(mode_name, item_type, item_id, project_id)
                    )
                """)

            # --- schema upgrades (add missing columns if needed) ---
            _add_column_if_not_exists(cur, 'subsystems', 'project_id', 'INTEGER')

            _add_column_if_not_exists(cur, 'modules', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'modules', 'num_connectors', 'INTEGER DEFAULT 0')
            _add_column_if_not_exists(cur, 'modules', 'color', "VARCHAR(7) DEFAULT '#C8C8FF'")
            _add_column_if_not_exists(cur, 'modules', 'photo', 'TEXT')
            _add_column_if_not_exists(cur, 'modules', 'pos_x', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'modules', 'pos_y', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'modules', 'width', 'REAL DEFAULT 120')
            _add_column_if_not_exists(cur, 'modules', 'height', 'REAL DEFAULT 80')
            _add_column_if_not_exists(cur, 'modules', 'min_temp', 'REAL')
            _add_column_if_not_exists(cur, 'modules', 'max_temp', 'REAL')

            # Enforce min operating temp <= max operating temp at the DB level
            # (belt-and-braces on top of the UI/persistence checks). Repair any
            # pre-existing inconsistent rows first so the constraint can apply.
            try:
                cur.execute("""
                    UPDATE modules
                    SET max_temp = min_temp
                    WHERE min_temp IS NOT NULL AND max_temp IS NOT NULL
                      AND min_temp > max_temp
                """)
                cur.execute(
                    "ALTER TABLE modules DROP CONSTRAINT IF EXISTS chk_module_temp_range"
                )
                cur.execute("""
                    ALTER TABLE modules ADD CONSTRAINT chk_module_temp_range
                    CHECK (min_temp IS NULL OR max_temp IS NULL OR min_temp <= max_temp)
                """)
            except Exception as e:
                print(f"Warning: Could not add module temperature range constraint: {e}")

            _add_column_if_not_exists(cur, 'connectors', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'connectors', 'color', "VARCHAR(7) DEFAULT '#C8C8FF'")
            _add_column_if_not_exists(cur, 'connectors', 'pos_x', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'connectors', 'pos_y', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'connectors', 'width', 'REAL DEFAULT 60')
            _add_column_if_not_exists(cur, 'connectors', 'height', 'REAL DEFAULT 20')
            _add_column_if_not_exists(cur, 'connectors', 'side', "VARCHAR(10) DEFAULT 'top'")
            _add_column_if_not_exists(cur, 'connectors', 'collapsed', 'BOOLEAN DEFAULT FALSE')

            _add_column_if_not_exists(cur, 'pins', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'pins', 'pin_number', 'INTEGER')
            _add_column_if_not_exists(cur, 'pins', 'current', 'REAL DEFAULT 0.0')

            _add_column_if_not_exists(cur, 'interfaces', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'interfaces', 'color', "VARCHAR(7) DEFAULT '#C8C8FF'")
            _add_column_if_not_exists(cur, 'interfaces', 'pos_x', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'interfaces', 'pos_y', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'interfaces', 'rotation', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'interfaces', 'current', 'REAL DEFAULT 0')

            _add_column_if_not_exists(cur, 'interface_points', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'interface_points', 'description', 'TEXT')  # for routing metadata

            _add_column_if_not_exists(cur, 'modes', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'mode_modules', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'mode_positions', 'project_id', 'INTEGER')

            # seed default data pin types (idempotent)
            for _pt in DEFAULT_PIN_TYPES:
                cur.execute(
                    "INSERT INTO pin_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (_pt,),
                )

            # --- indexes (idempotent) ---
            # project scoping
            cur.execute("CREATE INDEX IF NOT EXISTS idx_subsystems_project ON subsystems(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_modules_project    ON modules(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_connectors_project ON connectors(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pins_project       ON pins(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_interfaces_project ON interfaces(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ifpoints_project   ON interface_points(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_modes_project      ON modes(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_modpos_project     ON mode_positions(project_id)")

            # common foreign keys / heavy paths
            cur.execute("CREATE INDEX IF NOT EXISTS idx_connectors_module  ON connectors(module_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pins_connector     ON pins(connector_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ifpoints_if        ON interface_points(interface_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_interfaces_pins    ON interfaces(pin1_id, pin2_id)")

            conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise e


# =============================================================================
# Module, Connector, Interface Position Functions
# =============================================================================

def _ensure_project_selected():
    """Raise if no project is selected."""
    if current_project_id is None:
        raise RuntimeError("No project selected. Please create or open a project first.")


def save_module_positions(module_positions):
    """Save module positions [(x,y,id, [w,h])...] in one transaction."""
    if not module_positions:
        return
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE modules SET pos_x = %s, pos_y = %s WHERE id = %s AND project_id = %s",
                [(x, y, mid, current_project_id) for x, y, mid in module_positions]
            )
            conn.commit()
    except Exception as e:
        raise e


def get_module_positions(module_ids):
    """Return {id: (x,y,w,h)} for given module ids."""
    if not module_ids:
        return {}
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(module_ids))
            cur.execute(
                f"SELECT id, pos_x, pos_y, width, height FROM modules "
                f"WHERE id IN ({placeholders}) AND project_id = %s",
                (*module_ids, current_project_id)
            )
            out = {}
            for _id, x, y, w, h in cur.fetchall():
                out[_id] = (x or 0, y or 0, w or 300, h or 200)
            return out
    except Exception as e:
        raise e


def save_connector_positions(connector_positions):
    """Save connector positions [(x,y,w,h,id)] in one transaction."""
    if not connector_positions:
        return
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE connectors SET pos_x = %s, pos_y = %s, width = %s, height = %s "
                "WHERE id = %s AND project_id = %s",
                [(x, y, w, h, cid, current_project_id) for x, y, w, h, cid in connector_positions]
            )
            conn.commit()
    except Exception as e:
        raise e


def get_connector_positions(connector_ids):
    """Return {id: (x,y,w,h,side)} for given connector ids."""
    if not connector_ids:
        return {}
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(connector_ids))
            cur.execute(
                f"SELECT id, pos_x, pos_y, width, height, side FROM connectors "
                f"WHERE id IN ({placeholders}) AND project_id = %s",
                (*connector_ids, current_project_id)
            )
            out = {}
            for _id, x, y, w, h, side in cur.fetchall():
                out[_id] = (x or 0, y or 0, w or 60, h or 20, side or 'top')
            return out
    except Exception as e:
        raise e


def save_interface_positions(interface_positions):
    """Save interface positions [(x,y,rot,id)] in one transaction."""
    if not interface_positions:
        return
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE interfaces SET pos_x = %s, pos_y = %s, rotation = %s "
                "WHERE id = %s AND project_id = %s",
                [(x, y, rot, iid, current_project_id) for x, y, rot, iid in interface_positions]
            )
            conn.commit()
    except Exception as e:
        raise e


def get_interface_positions(interface_ids):
    """Return {id: (x,y,rot)} for given interface ids."""
    if not interface_ids:
        return {}
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(interface_ids))
            cur.execute(
                f"SELECT id, pos_x, pos_y, rotation FROM interfaces "
                f"WHERE id IN ({placeholders}) AND project_id = %s",
                (*interface_ids, current_project_id)
            )
            out = {}
            for _id, x, y, r in cur.fetchall():
                out[_id] = (x or 0, y or 0, r or 0)
            return out
    except Exception as e:
        raise e


def save_interface_points(interface_points_data):
    """Persist routing points for interfaces: {interface_id: [(x,y), ...], ...}."""
    if not interface_points_data:
        return
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            for interface_id, points in interface_points_data.items():
                cur.execute(
                    "DELETE FROM interface_points WHERE interface_id = %s AND project_id = %s",
                    (interface_id, current_project_id)
                )
                for idx, (x, y) in enumerate(points):
                    cur.execute(
                        "INSERT INTO interface_points (interface_id, point_index, x, y, project_id) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (interface_id, idx, x, y, current_project_id)
                    )
            conn.commit()
    except Exception as e:
        raise e


def get_interface_points(interface_ids):
    """Return {interface_id: [(x,y), ...]} for a list of ids."""
    if not interface_ids:
        return {}
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(interface_ids))
            cur.execute(
                f"SELECT interface_id, x, y FROM interface_points "
                f"WHERE interface_id IN ({placeholders}) AND project_id = %s "
                f"ORDER BY interface_id, point_index",
                (*interface_ids, current_project_id)
            )
            out = {}
            for iid, x, y in cur.fetchall():
                out.setdefault(iid, []).append((x, y))
            return out
    except Exception as e:
        raise e


def save_complete_layout(module_positions, connector_positions, interface_positions, interface_points_data):
    """Save modules/connectors/interfaces positions and interface routing points in one transaction."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # modules
            if module_positions:
                formatted = []
                for data in module_positions:
                    if len(data) == 5:  # (x, y, mod_id, w, h)
                        x, y, mid, w, h = data
                        formatted.append((x, y, w, h, mid, current_project_id))
                    elif len(data) == 3:  # legacy (x, y, mod_id)
                        x, y, mid = data
                        formatted.append((x, y, 300, 200, mid, current_project_id))
                cur.executemany(
                    "UPDATE modules SET pos_x = %s, pos_y = %s, width = %s, height = %s "
                    "WHERE id = %s AND project_id = %s",
                    formatted
                )

            # connectors
            if connector_positions:
                formatted_c = []
                for data in connector_positions:
                    if len(data) == 6:  # (x, y, w, h, side, id)
                        x, y, w, h, side, cid = data
                        formatted_c.append((x, y, w, h, side, cid, current_project_id))
                    elif len(data) == 5:  # legacy (x, y, w, h, id)
                        x, y, w, h, cid = data
                        formatted_c.append((x, y, w, h, 'top', cid, current_project_id))
                cur.executemany(
                    "UPDATE connectors SET pos_x = %s, pos_y = %s, width = %s, height = %s, side = %s "
                    "WHERE id = %s AND project_id = %s",
                    formatted_c
                )

            # interfaces
            if interface_positions:
                formatted_i = [(x, y, rot, iid, current_project_id) for x, y, rot, iid in interface_positions]
                cur.executemany(
                    "UPDATE interfaces SET pos_x = %s, pos_y = %s, rotation = %s "
                    "WHERE id = %s AND project_id = %s",
                    formatted_i
                )

            # interface routing points
            for interface_id, points in interface_points_data.items():
                cur.execute(
                    "DELETE FROM interface_points WHERE interface_id = %s AND project_id = %s",
                    (interface_id, current_project_id)
                )
                for idx, (x, y) in enumerate(points):
                    cur.execute(
                        "INSERT INTO interface_points (interface_id, point_index, x, y, project_id) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (interface_id, idx, x, y, current_project_id)
                    )

            conn.commit()
    except Exception as e:
        raise e


def get_complete_layout(module_ids, connector_ids, interface_ids):
    """Return (modules_pos, connectors_pos, interfaces_pos, interfaces_points)."""
    return (
        get_module_positions(module_ids),
        get_connector_positions(connector_ids),
        get_interface_positions(interface_ids),
        get_interface_points(interface_ids)
    )


# =============================================================================
# Mode Management
# =============================================================================

def get_all_modes():
    """List all mode names for current project."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM modes WHERE project_id = %s ORDER BY name", (current_project_id,))
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error getting modes: {e}")
        return []


def create_mode(mode_name, module_ids):
    """Create/update a mode and its module set."""
    if not mode_name or not module_ids:
        return False
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO modes (name, data, project_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (name, project_id)
                DO UPDATE SET data = EXCLUDED.data
            """, (mode_name, json.dumps({"created": True}), current_project_id))

            cur.execute("DELETE FROM mode_modules WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))

            # Safety: filter out module IDs that no longer exist in the modules table,
            # to avoid foreign-key constraint violations when modules were deleted
            # after being added to a mode.
            if module_ids:
                placeholders = ",".join("%s" for _ in module_ids)
                cur.execute(
                    f"SELECT id FROM modules WHERE id IN ({placeholders}) AND project_id = %s",
                    (*module_ids, current_project_id),
                )
                valid_ids = {row[0] for row in cur.fetchall()}
            else:
                valid_ids = set()

            for mid in module_ids:
                if mid in valid_ids:
                    cur.execute("""
                        INSERT INTO mode_modules (mode_name, module_id, project_id)
                        VALUES (%s, %s, %s)
                    """, (mode_name, mid, current_project_id))

            conn.commit()
            return True
    except Exception as e:
        print(f"Error creating mode: {e}")
        return False


def delete_mode(mode_name):
    """Delete a mode and associated data."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM modes WHERE name = %s AND project_id = %s", (mode_name, current_project_id))
            cur.execute("DELETE FROM mode_modules WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))
            cur.execute("DELETE FROM mode_positions WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error deleting mode: {e}")
        return False


def get_mode_modules(mode_name):
    """Return module ids belonging to a mode."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT module_id FROM mode_modules WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error getting mode modules: {e}")
        return []


def save_mode_positions(mode_name, module_positions=None, connector_positions=None, interface_positions=None):
    """Persist positions for a specific mode."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            cur.execute("DELETE FROM mode_positions WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))

            if module_positions:
                for module_id, (x, y) in module_positions.items():
                    cur.execute("""
                        INSERT INTO mode_positions
                        (mode_name, item_type, item_id, pos_x, pos_y, project_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (mode_name, 'module', module_id, x, y, current_project_id))

            if connector_positions:
                for connector_id, (x, y, w, h) in connector_positions.items():
                    cur.execute("""
                        INSERT INTO mode_positions
                        (mode_name, item_type, item_id, pos_x, pos_y, width, height, project_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (mode_name, 'connector', connector_id, x, y, w, h, current_project_id))

            if interface_positions:
                for interface_id, (x, y, rotation) in interface_positions.items():
                    cur.execute("""
                        INSERT INTO mode_positions
                        (mode_name, item_type, item_id, pos_x, pos_y, rotation, project_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (mode_name, 'interface', interface_id, x, y, rotation, current_project_id))

            conn.commit()
            return True
    except Exception as e:
        print(f"Error saving mode positions: {e}")
        return False


def get_mode_positions(mode_name):
    """Return (modules_pos, connectors_pos, interfaces_pos) for a mode."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            modules_pos = {}
            connectors_pos = {}
            interfaces_pos = {}

            cur.execute("""
                SELECT item_id, pos_x, pos_y FROM mode_positions
                WHERE mode_name = %s AND item_type = 'module' AND project_id = %s
            """, (mode_name, current_project_id))
            for item_id, x, y in cur.fetchall():
                modules_pos[item_id] = (x, y)

            cur.execute("""
                SELECT item_id, pos_x, pos_y, width, height FROM mode_positions
                WHERE mode_name = %s AND item_type = 'connector' AND project_id = %s
            """, (mode_name, current_project_id))
            for item_id, x, y, w, h in cur.fetchall():
                connectors_pos[item_id] = (x, y, w or 60, h or 20)

            cur.execute("""
                SELECT item_id, pos_x, pos_y, rotation FROM mode_positions
                WHERE mode_name = %s AND item_type = 'interface' AND project_id = %s
            """, (mode_name, current_project_id))
            for item_id, x, y, r in cur.fetchall():
                interfaces_pos[item_id] = (x, y, r or 0)

            return modules_pos, connectors_pos, interfaces_pos
    except Exception as e:
        print(f"Error getting mode positions: {e}")
        return {}, {}, {}


def clear_mode_positions(mode_name):
    """Remove all stored positions for a mode."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM mode_positions WHERE mode_name = %s AND project_id = %s",
                        (mode_name, current_project_id))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error clearing mode positions: {e}")
        return False


def get_vcc_pins_with_connections():
    """Return VCC-related pins that participate in any interface (for current project)."""
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT 
                    p.id, p.name, p.current, p.connector_id, c.module_id, m.name as module_name
                FROM pins p
                JOIN connectors c ON p.connector_id = c.id
                JOIN modules m ON c.module_id = m.id
                JOIN interfaces i ON (i.pin1_id = p.id OR i.pin2_id = p.id)
                WHERE p.project_id = %s
                  AND (
                    TRIM(UPPER(COALESCE(p.pin_type,''))) = 'VCC'
                    OR UPPER(COALESCE(p.name,'')) LIKE '%%VCC%%'
                    OR UPPER(COALESCE(p.name,'')) LIKE '%%PWR%%'
                    OR UPPER(COALESCE(p.name,'')) LIKE '%%POWER%%'
                    OR UPPER(COALESCE(p.description,'')) LIKE '%%VCC%%'
                  )
                ORDER BY m.name, c.name, p.name
            """, (current_project_id,))
            out = []
            for pid, pname, current, cid, mid, mname in cur.fetchall():
                out.append({
                    'pin_id': pid,
                    'pin_name': pname,
                    'current': current or 0.0,
                    'connector_id': cid,
                    'module_id': mid,
                    'module_name': mname
                })
            return out
    except Exception as e:
        print(f"Error getting VCC pins: {e}")
        return []


# -----------------------------------------------------------------------------
# Pool cleanup on interpreter exit
# -----------------------------------------------------------------------------
def cleanup_connections():
    """Close all pooled connections."""
    global connection_pool
    if connection_pool:
        connection_pool.closeall()

atexit.register(cleanup_connections)

# -----------------------------------------------------------------------------
# Backward-compatibility (deprecated)
# -----------------------------------------------------------------------------
def set_current_database(db_path):
    print("Warning: set_current_database() is deprecated. Use create_new_project() or open_existing_project() instead.")

def get_current_database():
    print("Warning: get_current_database() is deprecated. Use get_current_project_name() instead.")
    return current_project_name


def apply_auth_migration():
    """
    Create auth-related tables if they do not already exist.
    PostgreSQL syntax is used (SERIAL, NOW()).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            last_login TIMESTAMP
        );
        """)
        # roles
        cur.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        );
        """)
        # permissions
        cur.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL, -- e.g., 'module.create'
            description TEXT
        );
        """)
        # role_permissions
        cur.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER REFERENCES roles(id) ON DELETE CASCADE,
            permission_id INTEGER REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        );
        """)
        # user_roles
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            role_id INTEGER REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, role_id)
        );
        """)
        # user_subsystems: which subsystems a user can edit (scope)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_subsystems (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            subsystem_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, subsystem_id)
        );
        """)
        # audit logs (optional but recommended)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            action TEXT,
            details JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            host TEXT,
            started_at TIMESTAMP DEFAULT NOW(),
            last_seen TIMESTAMP DEFAULT NOW(),
            ended_at TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        );
        """)

        # --- pending_changes (change suggestions awaiting system-admin review) ---
        # Non-system-admin edits are recorded here instead of being applied
        # directly; the system admin approves/rejects them from the Reviews
        # tab. `temp_id` is a deterministic negative id (see suggestions.py)
        # used so pending-created items can be referenced by other pending
        # suggestions and by the optimistic previews in the UI.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_changes (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action VARCHAR(16) NOT NULL,          -- create | update | delete
            entity_type VARCHAR(24) NOT NULL,     -- module | connector | pin | interface | mode
            entity_id INTEGER,                    -- real id when the target exists (NULL for creates)
            subsystem_id INTEGER,                 -- scope for display / permission checks
            temp_id INTEGER UNIQUE,               -- deterministic negative id for creates
            payload JSONB NOT NULL,               -- canonical change payload (see suggestions.py)
            summary TEXT NOT NULL,                -- human-readable one-liner
            status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | stale
            review_note TEXT,
            resolved_id INTEGER,                  -- real id after a create is approved
            created_at TIMESTAMP DEFAULT NOW(),
            resolved_at TIMESTAMP,
            resolved_by INTEGER
        );
        """)


        # indexes for faster auth lookups

        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (LOWER(username))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_role_perms_role ON role_permissions(role_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_subsystems_user ON user_subsystems(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON user_sessions(user_id, is_active)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_project_status ON pending_changes(project_id, status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_changes(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_temp ON pending_changes(temp_id)")
        conn.commit()

def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False

def create_user(username: str, password: str, full_name: str = "", is_active: bool = True) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, is_active) VALUES (%s, %s, %s, %s) RETURNING id",
            (username, _hash_password(password), full_name, is_active)
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        return user_id

def set_user_password(user_id: int, new_password: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (_hash_password(new_password), user_id)
        )
        conn.commit()

def get_user_by_username(username: str):
    username = (username or "").strip()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, username, password_hash, full_name, is_active
            FROM users
            WHERE LOWER(username) = LOWER(%s)
        """, (username,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "full_name": row[3],
            "is_active": row[4],
        }

def record_login(user_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))
        conn.commit()

def add_role(name: str, description: str = "") -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO roles (name, description) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description RETURNING id",
            (name, description)
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid

def add_permission(code: str, description: str = "") -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO permissions (code, description) VALUES (%s, %s) ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description RETURNING id",
            (code, description)
        )
        pid = cur.fetchone()[0]
        conn.commit()
        return pid

def assign_permission_to_role(role_name: str, perm_code: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM roles WHERE name = %s", (role_name,))
        r = cur.fetchone()
        if not r:
            raise ValueError("Role not found")
        role_id = r[0]
        cur.execute("SELECT id FROM permissions WHERE code = %s", (perm_code,))
        p = cur.fetchone()
        if not p:
            raise ValueError("Permission not found")
        perm_id = p[0]
        cur.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (role_id, perm_id)
        )
        conn.commit()

def assign_role_to_user(user_id: int, role_name: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM roles WHERE name = %s", (role_name,))
        r = cur.fetchone()
        if not r:
            raise ValueError("Role not found")
        role_id = r[0]
        cur.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, role_id)
        )
        conn.commit()

def grant_user_subsystem(user_id: int, subsystem_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_subsystems (user_id, subsystem_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, subsystem_id)
        )
        conn.commit()

def get_user_roles(user_id: int) -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.name
            FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = %s
        """, (user_id,))
        return [row[0] for row in cur.fetchall()]

def get_user_permissions(user_id: int) -> set:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT p.code
            FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = %s
        """, (user_id,))
        return {row[0] for row in cur.fetchall()}

def get_user_subsystems(user_id: int) -> set:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT subsystem_id FROM user_subsystems WHERE user_id = %s", (user_id,))
        return {row[0] for row in cur.fetchall()}

def verify_credentials(username: str, password: str):
    u = get_user_by_username(username)
    if not u or not u["is_active"]:
        return None
    if not _verify_password(password, u["password_hash"]):
        return None
    return u

def record_audit(user_id: int, action: str, details: dict | None = None):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO audit_logs (user_id, action, details) VALUES (%s, %s, %s)",
            (user_id, action, Json(details) if details is not None else None)
        )
        conn.commit()

def seed_auth_basics():
    """
    Create base roles and permissions, and a default 'system' admin user if missing.
    Safe to call multiple times.
    """
    with get_connection() as conn:
        cur = conn.cursor()

        # --- Roles (idempotent) ---
        add_role("system_admin", "Full access to everything")
        add_role("subsystem_admin", "CRUD within own subsystem scope")
        add_role("viewer", "Read-only and export")

        # --- Permissions (CREATE FIRST, then assign) ---
        perms = [
            "project.create",  # <--- ensure this exists BEFORE assignment
            "module.create", "module.edit", "module.delete",
            "connector.create", "connector.edit", "connector.delete",
            "pin.create", "pin.edit", "pin.delete",
            "schematic.edit",
            "mode.edit",
            "export.excel", "export.image", "export.pdf",
            # add to perms list (alongside others)
            "interface.create", "interface.edit", "interface.delete",
            "project.delete",
            "subsystem.create", "subsystem.delete",
            "pin_type.create", "pin_type.delete",
        ]
        for code in perms:
            add_permission(code, code.replace(".", " "))

        # --- Assign permissions to roles ---
        # system_admin gets everything
        for code in perms:
            assign_permission_to_role("system_admin", code)

        # subsystem_admin: CRUD within scope + exports (NO project.create, NO schematic.edit, NO mode.edit if you want read-only there)
        for code in [
            "module.create", "module.edit", "module.delete",
            "connector.create", "connector.edit", "connector.delete",
            "pin.create", "pin.edit", "pin.delete",
            "interface.create", "interface.edit", "interface.delete",
            "export.excel", "export.image", "export.pdf",
        ]:
            assign_permission_to_role("subsystem_admin", code)

        # viewer: only exports
        for code in ["export.excel", "export.image", "export.pdf"]:
            assign_permission_to_role("viewer", code)

        # --- Default 'system' user (username: system / password: system) ---
        cur.execute("SELECT id FROM users WHERE username = %s", ("system",))
        row = cur.fetchone()
        if not row:
            uid = create_user("system", "system", "System Administrator", True)
            assign_role_to_user(uid, "system_admin")

        conn.commit()


# --- Access control core (append near auth helpers) ---
class UnauthorizedError(Exception):
    pass

def get_module_subsystem_id(module_id: int) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT subsystem_id FROM modules WHERE id = %s", (module_id,))
        r = cur.fetchone()
        return r[0] if r else None

def get_connector_subsystem_id(connector_id: int) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT m.subsystem_id
            FROM connectors c
            JOIN modules m ON m.id = c.module_id
            WHERE c.id = %s
        """, (connector_id,))
        r = cur.fetchone()
        return r[0] if r else None

def get_pin_subsystem_id(pin_id: int) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT m.subsystem_id
            FROM pins p
            JOIN connectors c ON c.id = p.connector_id
            JOIN modules m ON m.id = c.module_id
            WHERE p.id = %s
        """, (pin_id,))
        r = cur.fetchone()
        return r[0] if r else None

def check_perm_and_scope(user_id: int, perm_code: str, target_subsystem_id: int | None) -> bool:
    # 1) permission gate
    perms = get_user_permissions(user_id) or set()
    if perm_code not in perms and "*" not in perms:
        return False

    # 2) scope gate
    # Policy: empty scope => unrestricted (e.g., system admin or global role)
    scope_ids = get_user_subsystems(user_id) or set()
    if not scope_ids:
        return True

    # If no specific target, allow (caller should validate target when needed)
    if target_subsystem_id is None:
        return True

    # Map both sides by NAME to be project-agnostic:
    #   user scope: convert allowed IDs -> names (distinct across all projects)
    #   target: id -> name, then check name membership
    try:
        # get distinct allowed names
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT LOWER(s.name)
                FROM subsystems s
                WHERE s.id = ANY(%s)
            """, (list(scope_ids),))
            allowed_names = {row[0] for row in cur.fetchall()}

        # target name
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT LOWER(name) FROM subsystems WHERE id = %s", (target_subsystem_id,))
            r = cur.fetchone()
            target_name = r[0] if r else None
    except Exception:
        return False

    if not target_name:
        return False

    return target_name in allowed_names


def guard_or_raise(user_id: int, perm_code: str, target_subsystem_id: int | None,
                   action_label: str = "", details: dict | None = None):
    if not check_perm_and_scope(user_id, perm_code, target_subsystem_id):
        try:
            record_audit(user_id, "deny", {"perm": perm_code,
                                           "subsystem_id": target_subsystem_id,
                                           "action": action_label, **(details or {})})
        except Exception:
            pass
        raise UnauthorizedError(f"Denied: {perm_code} on subsystem={target_subsystem_id}")

def get_interface_pins(interface_id: int) -> tuple[int | None, int | None]:
    """
    Return (pin1_id, pin2_id) for the given interface.
    Returns (None, None) if not found.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pin1_id, pin2_id FROM interfaces WHERE id = %s",
            (interface_id,),
        )
        r = cur.fetchone()
        return (r[0], r[1]) if r else (None, None)


# =============================================================================
# Same-type wiring rules (pins may only connect to pins of the same class)
# =============================================================================
def _normalize_pin_type(pin_type) -> str:
    return (pin_type or "").strip().upper()


def classify_pin(pin_type, is_ground=False, value=None) -> dict:
    """
    Classify a pin for the same-type wiring rules.

    Returns {"class": "ground"|"power"|"data"|"untyped", "type": str,
             "voltage": float}.
    """
    t = _normalize_pin_type(pin_type)
    if is_ground or t in GROUND_PIN_ALIASES:
        return {"class": "ground", "type": t or "GND", "voltage": 0.0}
    if t in POWER_PIN_ALIASES:
        return {"class": "power", "type": t, "voltage": float(value) if value else 0.0}
    if not t:
        return {"class": "untyped", "type": "", "voltage": 0.0}
    return {"class": "data", "type": t, "voltage": 0.0}


def pins_connectable_from_data(pin_a: dict, pin_b: dict):
    """
    Pure compatibility check for two pin dicts (each with "pin_type",
    "is_ground", "value" and optionally "name"/"id"). Returns (ok, reason).

    Rules:
      * ground  ↔ ground
      * power   ↔ power with matching voltage (an unknown voltage never
                 blocks — only two known, different voltages are rejected)
      * data    ↔ data of the exact same type (UART↔UART, I2C↔I2C, …)
      * untyped pins block the connection until a type is set
    """
    def _label(d):
        return d.get("name") or f"Pin {d.get('id', '?')}"

    a_name, b_name = _label(pin_a), _label(pin_b)
    ca = classify_pin(pin_a.get("pin_type"), bool(pin_a.get("is_ground")), pin_a.get("value"))
    cb = classify_pin(pin_b.get("pin_type"), bool(pin_b.get("is_ground")), pin_b.get("value"))

    if ca["class"] == "untyped" or cb["class"] == "untyped":
        which = a_name if ca["class"] == "untyped" else b_name
        return False, (
            f"Pin '{which}' has no type set. Set a pin type "
            "(GND, VCC/voltage or a data type) before connecting."
        )

    if ca["class"] == "ground" or cb["class"] == "ground":
        if ca["class"] == "ground" and cb["class"] == "ground":
            return True, ""
        return False, "Ground pins can only connect to other ground pins."

    if ca["class"] == "power" or cb["class"] == "power":
        if ca["class"] != "power" or cb["class"] != "power":
            return False, "Voltage (VCC) pins can only connect to other voltage pins."
        va, vb = ca["voltage"], cb["voltage"]
        if va and vb and abs(va - vb) > 1e-6:
            return False, f"Voltage pins must have the same voltage ({va:g}V ≠ {vb:g}V)."
        return True, ""

    # Both data pins — exact same type required
    if ca["type"] != cb["type"]:
        return False, (
            f"'{ca['type']}' pins can only connect to other '{ca['type']}' pins "
            f"(cannot connect '{ca['type']}' to '{cb['type']}')."
        )
    return True, ""


def check_pin_change_interfaces(pin_id: int, pin_type: str, is_ground: bool = False, value: float | None = None):
    """
    Before changing a pin's electrical type, verify it does not break any
    existing connection. Returns (ok, reason); reason names the first
    connected pin the new type is incompatible with (empty when ok).

    E.g. a GND↔GND connection becomes illegal the moment one side is
    retyped to VCC or a data type, so the change is rejected.
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            project_id = get_current_project_id()
            if project_id is None:
                return True, ""

            # No electrical change (e.g. a rename-only edit) → existing
            # connections are untouched, so there is nothing to validate.
            cur.execute(
                "SELECT pin_type, is_ground, value FROM pins WHERE id=%s AND project_id=%s",
                (pin_id, project_id),
            )
            own = cur.fetchone()
            if own is None:
                return True, ""
            old_type_norm = (own[0] or "").strip().upper() or None
            old_ground = bool(own[1]) if own[1] is not None else False
            old_value = None if own[2] is None else float(own[2] or 0.0)
            new_type_norm = (pin_type or "").strip().upper() or None
            new_value = None if value is None else float(value or 0.0)
            if (
                new_type_norm == old_type_norm
                and bool(is_ground) == old_ground
                and new_value == old_value
            ):
                return True, ""

            cur.execute(
                "SELECT pin1_id, pin2_id FROM interfaces "
                "WHERE project_id=%s AND (pin1_id=%s OR pin2_id=%s)",
                (project_id, pin_id, pin_id),
            )
            other_ids = []
            for p1, p2 in cur.fetchall():
                other_ids.append(p2 if p1 == pin_id else p1)
            if not other_ids:
                return True, ""
            cur.execute(
                "SELECT id, name, pin_type, is_ground, value FROM pins WHERE id = ANY(%s)",
                (other_ids,),
            )
            connected = cur.fetchall()
    except Exception as e:
        return False, f"Error checking existing connections: {e}"

    new_cfg = {
        "id": pin_id,
        "pin_type": pin_type,
        "is_ground": bool(is_ground),
        "value": value,
    }
    for cid, cname, ctype, cgnd, cval in connected:
        other = {
            "id": cid,
            "name": cname,
            "pin_type": ctype,
            "is_ground": bool(cgnd) if cgnd is not None else False,
            "value": cval,
        }
        ok, reason = pins_connectable_from_data(new_cfg, other)
        if not ok:
            other_label = other.get("name") or f"Pin {other.get('id', '?')}"
            return False, f"'{other_label}' would become incompatible ({reason})"
    return True, ""


def pins_connectable(pin1_id: int, pin2_id: int):
    """Check whether two pins may be wired together. Returns (ok, reason)."""
    if pin1_id == pin2_id:
        return False, "Cannot connect a pin to itself."
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, pin_type, is_ground, value FROM pins WHERE id = ANY(%s)",
                ([pin1_id, pin2_id],),
            )
            rows = {r[0]: r for r in cur.fetchall()}
    except Exception as e:
        return False, f"Error checking pin types: {e}"
    if pin1_id not in rows or pin2_id not in rows:
        return False, "One of the pins no longer exists."
    r1, r2 = rows[pin1_id], rows[pin2_id]
    return pins_connectable_from_data(
        {"id": r1[0], "name": r1[1], "pin_type": r1[2], "is_ground": r1[3], "value": r1[4]},
        {"id": r2[0], "name": r2[1], "pin_type": r2[2], "is_ground": r2[3], "value": r2[4]},
    )


# =============================================================================
# Managed data pin types (system admin)
# =============================================================================
def list_pin_types() -> list:
    """Return the managed data pin type names (sorted)."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM pin_types ORDER BY name")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def count_pins_by_type(name: str) -> int:
    """How many pins currently use the given type (for the management dialog)."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM pins WHERE LOWER(TRIM(COALESCE(pin_type,''))) = LOWER(%s)",
                (name,),
            )
            return cur.fetchone()[0]
    except Exception:
        return 0


def add_pin_type_guarded(user_id: int, name: str):
    """System-admin guarded: add a new data pin type. Returns (ok, msg)."""
    name = (name or "").strip()
    if not name:
        return False, "Pin type name cannot be empty."
    guard_or_raise(user_id, "pin_type.create", None, "pin_type.create", details={"name": name})
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pin_types WHERE LOWER(name) = LOWER(%s)", (name,))
            if cur.fetchone():
                return False, f"Pin type '{name}' already exists."
            cur.execute("INSERT INTO pin_types (name) VALUES (%s)", (name,))
            conn.commit()
    except Exception as e:
        return False, f"Failed to add pin type: {e}"
    return True, f"Pin type '{name}' added."


def delete_pin_type_guarded(user_id: int, name: str):
    """System-admin guarded: remove a data pin type. Returns (ok, msg)."""
    name = (name or "").strip()
    guard_or_raise(user_id, "pin_type.delete", None, "pin_type.delete", details={"name": name})
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pin_types WHERE LOWER(name) = LOWER(%s)", (name,))
            if not cur.fetchone():
                return False, f"Pin type '{name}' not found."
            cur.execute(
                "SELECT COUNT(*) FROM pins WHERE LOWER(TRIM(COALESCE(pin_type,''))) = LOWER(%s)",
                (name,),
            )
            count = cur.fetchone()[0]
            if count:
                return False, f"Cannot delete '{name}': {count} pin(s) still use this type."
            cur.execute("DELETE FROM pin_types WHERE LOWER(name) = LOWER(%s)", (name,))
            conn.commit()
    except Exception as e:
        return False, f"Failed to delete pin type: {e}"
    return True, f"Pin type '{name}' deleted."


def get_interface_subsystem_ids(interface_id: int) -> set[int]:
    """
    Return the set of subsystem IDs associated with the modules
    connected by this interface (via its two pins).
    """
    pin1_id, pin2_id = get_interface_pins(interface_id)
    ids = set()
    if pin1_id is not None:
        sid = get_pin_subsystem_id(pin1_id)
        if sid is not None:
            ids.add(sid)
    if pin2_id is not None:
        sid = get_pin_subsystem_id(pin2_id)
        if sid is not None:
            ids.add(sid)
    return ids


def get_subsystem_id_by_name(name: str) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        # Assumes you have a table named 'subsystems' with columns (id, name).
        cur.execute("SELECT id FROM subsystems WHERE LOWER(name) = LOWER(%s)", (name,))
        row = cur.fetchone()
        return row[0] if row else None

def create_subsystem_admin(username: str, password: str, subsystem_name: str, full_name: str = "") -> int:
    """
    Create a user with 'subsystem_admin' role and grant scope to the given subsystem.
    Returns the new user's id.
    """
    uid = create_user(username, password, full_name or f"{subsystem_name} Subsystem Admin", True)
    assign_role_to_user(uid, "subsystem_admin")
    sid = get_subsystem_id_by_name(subsystem_name)
    if sid is None:
        raise ValueError(f"Subsystem not found: {subsystem_name}")
    grant_user_subsystem(uid, sid)
    return uid

def seed_subsystem_admins_from_db(default_password: str = "Aa123456"):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM subsystems")
        rows = cur.fetchall()
        for sid, name in rows:
            clean_name = (name or "").strip()
            if not clean_name:
                continue
            username = f"{clean_name}.Admin"
            cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s)", (username,))
            if cur.fetchone():
                continue  # already exists
            uid = create_user(username=username, password=default_password,
                              full_name=f"{clean_name} Admin", is_active=True)
            assign_role_to_user(uid, "subsystem_admin")
            grant_user_subsystem(uid, sid)
        conn.commit()

def get_user_id_by_username(username: str) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s)", (username,))
        row = cur.fetchone()
        return row[0] if row else None

def set_subsystem_admin_password(subsystem_name: str, new_password: str) -> bool:
    """
    Set password for 'Subsystem.Admin' user. Returns True if updated.
    """
    username = f"{subsystem_name}.Admin"
    uid = get_user_id_by_username(username)
    if not uid:
        return False
    set_user_password(uid, new_password)
    return True

def start_user_session(user_id: int, host: str | None = None) -> int:
    host = host or socket.gethostname()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_sessions (user_id, host) VALUES (%s, %s) RETURNING id
        """, (user_id, host))
        sid = cur.fetchone()[0]
        conn.commit()
        return sid

def touch_user_session(session_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE user_sessions SET last_seen = NOW() WHERE id = %s", (session_id,))
        conn.commit()

def end_user_session(session_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE user_sessions
            SET is_active = FALSE, ended_at = NOW()
            WHERE id = %s
        """, (session_id,))
        conn.commit()

def get_active_users(window_seconds: int = 60) -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT u.username, u.full_name,
                       s.last_seen,
                       (s.is_active AND s.last_seen >= NOW() - INTERVAL '{window_seconds} seconds') AS online
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.started_at >= NOW() - INTERVAL '1 day'
                ORDER BY s.last_seen DESC
            """)
        except Exception as e:
            # auto-migrate once if table is missing, then retry
            if "user_sessions" in str(e):
                apply_auth_migration()
                cur.execute(f"""
                    SELECT u.username, u.full_name,
                           s.last_seen,
                           (s.is_active AND s.last_seen >= NOW() - INTERVAL '{window_seconds} seconds') AS online
                    FROM user_sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.started_at >= NOW() - INTERVAL '1 day'
                    ORDER BY s.last_seen DESC
                """)
            else:
                raise
        rows = cur.fetchall()
        return [{"username": r[0], "full_name": r[1], "last_seen": r[2], "online": bool(r[3])} for r in rows]

def set_user_full_name(user_id: int, full_name: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET full_name = %s WHERE id = %s", (full_name, user_id))
        conn.commit()

def get_login_audit(limit: int = 200) -> list[dict]:
    """
    Return recent login/logout audit rows (action in {'login','logout'}).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.created_at, u.username, COALESCE(u.full_name,''), a.action
            FROM audit_logs a
            JOIN users u ON u.id = a.user_id
            WHERE a.action IN ('login','logout')
            ORDER BY a.created_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        return [{"time": r[0], "username": r[1], "full_name": r[2], "action": r[3]} for r in rows]

def get_all_users_simple() -> list[dict]:
    """
    Return all users (for totals): username, full_name, is_active.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT username, COALESCE(full_name,''), is_active FROM users ORDER BY username")
        return [{"username": r[0], "full_name": r[1], "is_active": bool(r[2])} for r in cur.fetchall()]
def get_subsystem_name_by_id(subsystem_id: int) -> str | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM subsystems WHERE id = %s", (subsystem_id,))
        r = cur.fetchone()
        return r[0] if r else None

def get_subsystem_ids_by_names_in_project(names: list[str], project_id: int) -> set[int]:
    if not names:
        return set()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM subsystems WHERE project_id = %s AND LOWER(name) = ANY(%s)",
            (project_id, [n.lower().strip() for n in names]),
        )
        return {row[0] for row in cur.fetchall()}

def get_user_subsystem_names(user_id: int) -> set[str]:
    """
    Return the set of subsystem *names* granted to user, deduped across projects.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT LOWER(s.name)
            FROM user_subsystems us
            JOIN subsystems s ON s.id = us.subsystem_id
            WHERE us.user_id = %s
        """, (user_id,))
        return {row[0] for row in cur.fetchall()}

def add_interface_guarded(user_id: int, pin1_id: int, pin2_id: int, color: str, project_id: int, current: float = 0.0):
    guard_or_raise(user_id, "interface.create", None, "interface.create",
                   details={"pin1_id": pin1_id, "pin2_id": pin2_id})
    # Same-type wiring rule (ground↔ground, matching VCC, same data type)
    ok, reason = pins_connectable(pin1_id, pin2_id)
    if not ok:
        return (False, reason)
    with get_connection() as conn:
        cur = conn.cursor()
        # avoid duplicates (both orders)
        cur.execute("""
            SELECT id FROM interfaces
            WHERE project_id=%s AND
                  ((pin1_id=%s AND pin2_id=%s) OR (pin1_id=%s AND pin2_id=%s))
        """, (project_id, pin1_id, pin2_id, pin2_id, pin1_id))
        if cur.fetchone():
            return (False, "Interface already exists.")
        cur.execute("""
            INSERT INTO interfaces (pin1_id, pin2_id, color, current, project_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (pin1_id, pin2_id, color, current, project_id))
        conn.commit()
        return (True, "Interface added.")

def update_interface_guarded(user_id: int, iface_id: int, pin1_id: int, pin2_id: int, color: str, project_id: int, current: float = 0.0):
    guard_or_raise(user_id, "interface.edit", None, "interface.edit",
                   details={"iface_id": iface_id})
    # Same-type wiring rule — but only when the pin pair actually changes.
    # Legacy interfaces created before this rule may already pair mismatched
    # pins; re-validating an unchanged pair would block harmless colour-only
    # edits and prevent the user from fixing old data.
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pin1_id, pin2_id FROM interfaces WHERE id=%s AND project_id=%s",
            (iface_id, project_id),
        )
        row = cur.fetchone()
        pair_changed = row is None or not (
            (row[0] == pin1_id and row[1] == pin2_id)
            or (row[0] == pin2_id and row[1] == pin1_id)
        )
    if pair_changed:
        ok, reason = pins_connectable(pin1_id, pin2_id)
        if not ok:
            return (False, reason)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE interfaces
            SET pin1_id=%s, pin2_id=%s, color=%s, current=%s
            WHERE id=%s AND project_id=%s
        """, (pin1_id, pin2_id, color, current, iface_id, project_id))
        conn.commit()
        return (True, "Interface updated.")

def delete_interface_guarded(user_id: int, iface_id: int, project_id: int):
    guard_or_raise(user_id, "interface.delete", None, "interface.delete",
                   details={"iface_id": iface_id})
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM interfaces WHERE id=%s AND project_id=%s", (iface_id, project_id))
        conn.commit()
        return (True, "Interface deleted.")


def get_project_id_by_name(name: str) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM projects WHERE name = %s", (name.strip(),))
        row = cur.fetchone()
        return row[0] if row else None

def delete_project_guarded(user_id: int, project_id: int) -> tuple[bool, str]:
    """
    Delete a project. Prefer a single CASCADE delete on projects;
    if the schema in this database instance lacks some cascades (legacy),
    we fall back to manual child deletes in a safe order.
    """
    guard_or_raise(user_id, "project.delete", None, "project.delete", details={"project_id": project_id})

    try:
        with get_connection() as conn:
            conn.autocommit = False
            cur = conn.cursor()

            # 1) Try the simple, atomic CASCADE delete
            try:
                cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))
                conn.commit()
                return True, "Project deleted."
            except Exception as e1:
                # 2) Fallback (legacy schemas without full cascades)
                conn.rollback()

                # remove children that may not have proper cascades in older DBs
                try:
                    # mode-related (no strict FKs on item_id in some schemas)
                    cur.execute("DELETE FROM mode_positions WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM mode_modules   WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM modes          WHERE project_id=%s", (project_id,))
                except Exception:
                    pass

                try:
                    # interface routing points → interfaces → pins/connectors/modules/subsystems
                    cur.execute("DELETE FROM interface_points WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM interfaces       WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM pins             WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM connectors       WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM modules          WHERE project_id=%s", (project_id,))
                except Exception:
                    pass
                try:
                    cur.execute("DELETE FROM subsystems       WHERE project_id=%s", (project_id,))
                except Exception:
                    pass

                # finally project
                cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))
                conn.commit()
                return True, "Project deleted (legacy fallback)."

    except Exception as e:
        # IMPORTANT: do not keep the transaction aborted
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"Failed to delete project: {e}"


# =============================================================================
# Subsystem Management (system admin only)
# =============================================================================

def list_subsystems_for_project(project_id: int | None = None) -> list[tuple[int, str]]:
    """
    Return [(id, name), ...] for the current project, ordered by name.
    """
    _ensure_project_selected()
    pid = project_id or current_project_id
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name",
                (pid,),
            )
            return list(cur.fetchall())
    except Exception as e:
        print(f"Error listing subsystems: {e}")
        return []


def count_subsystem_data(subsystem_id: int, project_id: int | None = None) -> dict:
    """
    Return a small breakdown of how much data belongs to a subsystem
    (modules / connectors / pins / interfaces), used by the delete UI.
    """
    _ensure_project_selected()
    pid = project_id or current_project_id
    counts = {"modules": 0, "connectors": 0, "pins": 0, "interfaces": 0}
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM modules WHERE subsystem_id = %s AND project_id = %s",
                (subsystem_id, pid),
            )
            counts["modules"] = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM connectors
                WHERE project_id = %s AND module_id IN (
                    SELECT id FROM modules
                    WHERE subsystem_id = %s AND project_id = %s
                )
                """,
                (pid, subsystem_id, pid),
            )
            counts["connectors"] = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM pins
                WHERE project_id = %s AND connector_id IN (
                    SELECT id FROM connectors
                    WHERE project_id = %s AND module_id IN (
                        SELECT id FROM modules
                        WHERE subsystem_id = %s AND project_id = %s
                    )
                )
                """,
                (pid, pid, subsystem_id, pid),
            )
            counts["pins"] = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(DISTINCT i.id) FROM interfaces i
                JOIN pins p ON (p.id = i.pin1_id OR p.id = i.pin2_id)
                JOIN connectors c ON c.id = p.connector_id
                JOIN modules m ON m.id = c.module_id
                WHERE i.project_id = %s AND m.subsystem_id = %s AND m.project_id = %s
                """,
                (pid, subsystem_id, pid),
            )
            counts["interfaces"] = cur.fetchone()[0]
    except Exception as e:
        print(f"Error counting subsystem data: {e}")
    return counts


def add_subsystem_guarded(user_id: int, subsystem_name: str) -> tuple[bool, str]:
    """
    Add a new subsystem to the current project. System admin only
    ('subsystem.create' permission — granted solely to system_admin).
    Also auto-creates the '<name>.Admin' subsystem-admin account, matching
    the behaviour of seed_subsystem_admins_from_db() on startup.
    """
    name = (subsystem_name or "").strip()
    if not name:
        return False, "Subsystem name cannot be empty."
    guard_or_raise(
        user_id,
        "subsystem.create",
        None,
        "subsystem.create",
        details={"subsystem_name": name},
    )
    try:
        _ensure_project_selected()
        with get_connection() as conn:
            conn.autocommit = False
            cur = conn.cursor()

            # friendly duplicate check (case-insensitive)
            cur.execute(
                "SELECT id FROM subsystems WHERE project_id = %s AND LOWER(name) = LOWER(%s)",
                (current_project_id, name),
            )
            if cur.fetchone():
                conn.rollback()
                return False, f"Subsystem '{name}' already exists in this project."

            cur.execute(
                "INSERT INTO subsystems (name, project_id) VALUES (%s, %s) RETURNING id",
                (name, current_project_id),
            )
            new_id = cur.fetchone()[0]

            # auto-create the matching subsystem-admin account (idempotent)
            admin_username = f"{name}.Admin"
            cur.execute(
                "SELECT id FROM users WHERE LOWER(username) = LOWER(%s)",
                (admin_username,),
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (username, password_hash, full_name, is_active) "
                    "VALUES (%s, %s, %s, TRUE) RETURNING id",
                    (admin_username, _hash_password("Aa123456"), f"{name} Admin"),
                )
                admin_uid = cur.fetchone()[0]
                cur.execute(
                    "SELECT id FROM roles WHERE name = 'subsystem_admin'",
                )
                role_row = cur.fetchone()
                if role_row:
                    cur.execute(
                        "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (admin_uid, role_row[0]),
                    )
                cur.execute(
                    "INSERT INTO user_subsystems (user_id, subsystem_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (admin_uid, new_id),
                )

            conn.commit()

        try:
            record_audit(
                user_id,
                "subsystem.create",
                {"subsystem_id": new_id, "subsystem_name": name, "project_id": current_project_id},
            )
        except Exception:
            pass
        return True, f"Subsystem '{name}' created (admin account '{admin_username}' ready)."
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"Failed to create subsystem: {e}"


def delete_subsystem_guarded(user_id: int, subsystem_id: int) -> tuple[bool, str]:
    """
    Delete a subsystem and everything related to it:

      * interfaces (connections) touching any of its pins — including
        cross-subsystem links whose other end lives elsewhere,
      * interface routing points,
      * pins, connectors, modules of the subsystem,
      * mode references (mode_modules / mode_positions),
      * user_subsystems grants (subsystem-admin scope),
      * the auto-created '<name>.Admin' account when it is a pure
        subsystem admin with no other roles/grants,
      * the subsystem row itself.

    System admin only ('subsystem.delete' permission).
    """
    guard_or_raise(
        user_id,
        "subsystem.delete",
        subsystem_id,
        "subsystem.delete",
        details={"subsystem_id": subsystem_id},
    )
    try:
        with get_connection() as conn:
            conn.autocommit = False
            cur = conn.cursor()

            # 0) identify subsystem
            cur.execute(
                "SELECT name, project_id FROM subsystems WHERE id = %s",
                (subsystem_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False, "Subsystem not found."
            sub_name, project_id = row

            # 1) interfaces (connections) touching any pin of this subsystem
            cur.execute(
                """
                SELECT DISTINCT i.id
                FROM interfaces i
                JOIN pins p ON (p.id = i.pin1_id OR p.id = i.pin2_id)
                JOIN connectors c ON c.id = p.connector_id
                JOIN modules m ON m.id = c.module_id
                WHERE i.project_id = %s AND m.subsystem_id = %s AND m.project_id = %s
                """,
                (project_id, subsystem_id, project_id),
            )
            iface_ids = [r[0] for r in cur.fetchall()]

            # 2) interface routing points → interfaces
            if iface_ids:
                cur.execute(
                    "DELETE FROM interface_points WHERE interface_id = ANY(%s)",
                    (iface_ids,),
                )
                cur.execute(
                    "DELETE FROM interfaces WHERE id = ANY(%s)",
                    (iface_ids,),
                )

            # 3) pins → connectors → modules of this subsystem
            cur.execute(
                """
                DELETE FROM pins
                WHERE project_id = %s AND connector_id IN (
                    SELECT id FROM connectors
                    WHERE project_id = %s AND module_id IN (
                        SELECT id FROM modules
                        WHERE project_id = %s AND subsystem_id = %s
                    )
                )
                """,
                (project_id, project_id, project_id, subsystem_id),
            )
            cur.execute(
                """
                DELETE FROM connectors
                WHERE project_id = %s AND module_id IN (
                    SELECT id FROM modules
                    WHERE project_id = %s AND subsystem_id = %s
                )
                """,
                (project_id, project_id, subsystem_id),
            )

            # 4) mode references (item_id has no FK in legacy schemas)
            cur.execute(
                """
                DELETE FROM mode_positions
                WHERE project_id = %s AND (
                    (item_type = 'module' AND item_id IN (
                        SELECT id FROM modules WHERE project_id = %s AND subsystem_id = %s))
                    OR (item_type = 'connector' AND item_id IN (
                        SELECT id FROM connectors WHERE project_id = %s AND module_id IN (
                            SELECT id FROM modules WHERE project_id = %s AND subsystem_id = %s)))
                    OR (item_type = 'interface' AND item_id = ANY(%s))
                )
                """,
                (project_id, project_id, subsystem_id,
                 project_id, project_id, subsystem_id,
                 iface_ids or [-1]),
            )
            cur.execute(
                """
                DELETE FROM mode_modules
                WHERE project_id = %s AND module_id IN (
                    SELECT id FROM modules WHERE project_id = %s AND subsystem_id = %s
                )
                """,
                (project_id, project_id, subsystem_id),
            )

            # 5) modules
            cur.execute(
                "DELETE FROM modules WHERE project_id = %s AND subsystem_id = %s",
                (project_id, subsystem_id),
            )

            # 6) auto-created '<name>.Admin' account — only when it is a pure
            #    subsystem admin with exactly one role and one grant.
            #    (Checked BEFORE removing grants below, so we can still see it.)
            admin_username = f"{sub_name.strip()}.Admin"
            cur.execute(
                "SELECT id FROM users WHERE LOWER(username) = LOWER(%s)",
                (admin_username,),
            )
            admin_row = cur.fetchone()
            if admin_row:
                admin_uid = admin_row[0]
                cur.execute(
                    "SELECT COUNT(*) FROM user_roles WHERE user_id = %s",
                    (admin_uid,),
                )
                n_roles = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT COUNT(*) FROM user_roles ur
                    JOIN roles r ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.name <> 'subsystem_admin'
                    """,
                    (admin_uid,),
                )
                n_other_roles = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM user_subsystems WHERE user_id = %s",
                    (admin_uid,),
                )
                n_grants = cur.fetchone()[0]
                if n_other_roles == 0 and n_roles == 1 and n_grants == 1:
                    # cascades user_roles / user_subsystems / user_sessions
                    cur.execute("DELETE FROM users WHERE id = %s", (admin_uid,))

            # 7) remaining user grants (user_subsystems has no FK to subsystems)
            cur.execute(
                "DELETE FROM user_subsystems WHERE subsystem_id = %s",
                (subsystem_id,),
            )

            # 8) the subsystem itself
            cur.execute(
                "DELETE FROM subsystems WHERE id = %s AND project_id = %s",
                (subsystem_id, project_id),
            )
            conn.commit()

        try:
            record_audit(
                user_id,
                "subsystem.delete",
                {"subsystem_id": subsystem_id, "subsystem_name": sub_name, "project_id": project_id},
            )
        except Exception:
            pass
        return True, f"Subsystem '{sub_name}' and all related data deleted."
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"Failed to delete subsystem: {e}"


# =============================================================================
# Full Project Export / Import (JSON dump / restore)
# =============================================================================


def export_project_data(project_id: int) -> dict:
    """
    Export ALL data for a project as a nested dictionary, suitable for
    JSON serialisation.  Auth tables (users, roles, etc.) are system-wide
    and are NOT included — the export is purely project-scoped.
    """
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            data: dict = {}

            # ── project ──
            cur.execute(
                "SELECT id, name, created_at, description FROM projects WHERE id = %s",
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Project {project_id} not found")
            data["project"] = {
                "id": row[0],
                "name": row[1],
                "created_at": row[2].isoformat() if row[2] else None,
                "description": row[3],
            }

            # ── subsystems ──
            cur.execute(
                "SELECT id, name, project_id FROM subsystems WHERE project_id = %s ORDER BY id",
                (project_id,),
            )
            data["subsystems"] = [
                {"id": r[0], "name": r[1], "project_id": r[2]} for r in cur.fetchall()
            ]
            subsystem_ids = [s["id"] for s in data["subsystems"]]

            # ── modules ──
            cur.execute(
                """SELECT id, name, subsystem_id, project_id, mass, power,
                           min_temp, max_temp, num_connectors, color, photo,
                           pos_x, pos_y, width, height
                    FROM modules WHERE project_id = %s ORDER BY id""",
                (project_id,),
            )
            cols = [
                "id", "name", "subsystem_id", "project_id", "mass", "power",
                "min_temp", "max_temp", "num_connectors", "color", "photo",
                "pos_x", "pos_y", "width", "height",
            ]
            data["modules"] = [dict(zip(cols, r)) for r in cur.fetchall()]
            module_ids = [m["id"] for m in data["modules"]]

            # ── connectors ──
            cur.execute(
                """SELECT id, name, module_id, project_id, number_of_pins,
                           color, pos_x, pos_y, width, height, side, collapsed
                    FROM connectors WHERE project_id = %s ORDER BY id""",
                (project_id,),
            )
            cols = [
                "id", "name", "module_id", "project_id", "number_of_pins",
                "color", "pos_x", "pos_y", "width", "height", "side", "collapsed",
            ]
            data["connectors"] = [dict(zip(cols, r)) for r in cur.fetchall()]
            connector_ids = [c["id"] for c in data["connectors"]]

            # ── pins ──
            if connector_ids:
                placeholders = ",".join("%s" for _ in connector_ids)
                cur.execute(
                    """SELECT id, name, connector_id, project_id, pin_number,
                               pin_type, is_ground, value, current, description
                        FROM pins
                        WHERE connector_id IN ({}) AND project_id = %s
                        ORDER BY id""".format(placeholders),
                    (*connector_ids, project_id),
                )
            else:
                cur.execute(
                    """SELECT id, name, connector_id, project_id, pin_number,
                               pin_type, is_ground, value, current, description
                        FROM pins WHERE project_id = %s ORDER BY id""",
                    (project_id,),
                )
            cols = [
                "id", "name", "connector_id", "project_id", "pin_number",
                "pin_type", "is_ground", "value", "current", "description",
            ]
            data["pins"] = [dict(zip(cols, r)) for r in cur.fetchall()]

            # ── interfaces ──
            cur.execute(
                """SELECT id, pin1_id, pin2_id, project_id, interface_type,
                           color, pos_x, pos_y, rotation
                    FROM interfaces WHERE project_id = %s ORDER BY id""",
                (project_id,),
            )
            cols = [
                "id", "pin1_id", "pin2_id", "project_id", "interface_type",
                "color", "pos_x", "pos_y", "rotation",
            ]
            data["interfaces"] = [dict(zip(cols, r)) for r in cur.fetchall()]
            interface_ids = [i["id"] for i in data["interfaces"]]

            # ── interface_points ──
            if interface_ids:
                placeholders = ",".join("%s" for _ in interface_ids)
                cur.execute(
                    """SELECT id, interface_id, project_id, point_index, x, y, description
                        FROM interface_points
                        WHERE interface_id IN ({}) AND project_id = %s
                        ORDER BY interface_id, point_index""".format(placeholders),
                    (*interface_ids, project_id),
                )
            else:
                cur.execute(
                    """SELECT id, interface_id, project_id, point_index, x, y, description
                        FROM interface_points WHERE project_id = %s
                        ORDER BY interface_id, point_index""",
                    (project_id,),
                )
            cols = ["id", "interface_id", "project_id", "point_index", "x", "y", "description"]
            data["interface_points"] = [dict(zip(cols, r)) for r in cur.fetchall()]

            # ── modes ──
            cur.execute(
                "SELECT id, name, project_id, data FROM modes WHERE project_id = %s ORDER BY id",
                (project_id,),
            )
            cols = ["id", "name", "project_id", "data"]
            data["modes"] = [dict(zip(cols, r)) for r in cur.fetchall()]
            mode_names = [m["name"] for m in data["modes"]]

            # ── mode_modules ──
            if mode_names:
                cur.execute(
                    """SELECT id, mode_name, module_id, project_id
                        FROM mode_modules
                        WHERE mode_name = ANY(%s) AND project_id = %s
                        ORDER BY id""",
                    (mode_names, project_id),
                )
            else:
                cur.execute(
                    """SELECT id, mode_name, module_id, project_id
                        FROM mode_modules WHERE project_id = %s ORDER BY id""",
                    (project_id,),
                )
            cols = ["id", "mode_name", "module_id", "project_id"]
            data["mode_modules"] = [dict(zip(cols, r)) for r in cur.fetchall()]

            # ── mode_positions ──
            if mode_names:
                cur.execute(
                    """SELECT id, mode_name, item_type, item_id, project_id,
                               pos_x, pos_y, width, height, rotation
                        FROM mode_positions
                        WHERE mode_name = ANY(%s) AND project_id = %s
                        ORDER BY id""",
                    (mode_names, project_id),
                )
            else:
                cur.execute(
                    """SELECT id, mode_name, item_type, item_id, project_id,
                               pos_x, pos_y, width, height, rotation
                        FROM mode_positions WHERE project_id = %s ORDER BY id""",
                    (project_id,),
                )
            cols = [
                "id", "mode_name", "item_type", "item_id", "project_id",
                "pos_x", "pos_y", "width", "height", "rotation",
            ]
            data["mode_positions"] = [dict(zip(cols, r)) for r in cur.fetchall()]

            # ── version & metadata ──
            data["_export_meta"] = {
                "version": "1.0",
                "exported_at": datetime.now().isoformat(),
                "description": "System Architecture full project export",
            }

            return data

    except Exception as e:
        raise RuntimeError(f"Failed to export project data: {e}") from e


def import_project_data(project_id: int, data: dict) -> tuple[bool, str]:
    """
    Import full project data from a dictionary previously returned by
    export_project_data().  **Existing project data for this project_id
    will be replaced.**  Auth-level tables (users, roles, etc.) are
    never touched.

    Returns (ok: bool, message: str).
    """
    _ensure_project_selected()
    try:
        with get_connection() as conn:
            conn.autocommit = False
            cur = conn.cursor()

            # ── 1. Validate structure ──
            required = [
                "subsystems", "modules", "connectors", "pins",
                "interfaces", "interface_points", "modes",
                "mode_modules", "mode_positions",
            ]
            for key in required:
                if key not in data:
                    conn.rollback()
                    return False, f"Missing required section '{key}' in import data"

            # ── 2. Clear existing project data (reverse FK order) ──
            # mode_positions
            cur.execute("DELETE FROM mode_positions WHERE project_id = %s", (project_id,))
            # mode_modules
            cur.execute("DELETE FROM mode_modules WHERE project_id = %s", (project_id,))
            # modes
            cur.execute("DELETE FROM modes WHERE project_id = %s", (project_id,))
            # interface_points
            cur.execute("DELETE FROM interface_points WHERE project_id = %s", (project_id,))
            # interfaces
            cur.execute("DELETE FROM interfaces WHERE project_id = %s", (project_id,))
            # pins
            cur.execute("DELETE FROM pins WHERE project_id = %s", (project_id,))
            # connectors
            cur.execute("DELETE FROM connectors WHERE project_id = %s", (project_id,))
            # modules
            cur.execute("DELETE FROM modules WHERE project_id = %s", (project_id,))
            # subsystems
            cur.execute("DELETE FROM subsystems WHERE project_id = %s", (project_id,))

            # ── 3. Import subsystems ──
            for s in data["subsystems"]:
                cur.execute(
                    "INSERT INTO subsystems (id, name, project_id) VALUES (%s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                    (s["id"], s["name"], project_id),
                )

            # ── 4. Import modules ──
            for m in data["modules"]:
                cur.execute(
                    """INSERT INTO modules
                       (id, name, subsystem_id, project_id, mass, power,
                        min_temp, max_temp, num_connectors, color, photo,
                        pos_x, pos_y, width, height)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         name        = EXCLUDED.name,
                         subsystem_id = EXCLUDED.subsystem_id,
                         mass        = EXCLUDED.mass,
                         power       = EXCLUDED.power,
                         min_temp    = EXCLUDED.min_temp,
                         max_temp    = EXCLUDED.max_temp,
                         num_connectors = EXCLUDED.num_connectors,
                         color       = EXCLUDED.color,
                         photo       = EXCLUDED.photo,
                         pos_x       = EXCLUDED.pos_x,
                         pos_y       = EXCLUDED.pos_y,
                         width       = EXCLUDED.width,
                         height      = EXCLUDED.height""",
                    (
                        m["id"], m["name"], m["subsystem_id"], project_id,
                        m.get("mass", 0.0), m.get("power", 0.0),
                        m.get("min_temp"), m.get("max_temp"),
                        m.get("num_connectors", 0), m.get("color", "#C8C8FF"),
                        m.get("photo"), m.get("pos_x", 0), m.get("pos_y", 0),
                        m.get("width", 120), m.get("height", 80),
                    ),
                )

            # ── 5. Import connectors ──
            for c in data["connectors"]:
                cur.execute(
                    """INSERT INTO connectors
                       (id, name, module_id, project_id, number_of_pins,
                        color, pos_x, pos_y, width, height, side, collapsed)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         name          = EXCLUDED.name,
                         module_id     = EXCLUDED.module_id,
                         number_of_pins = EXCLUDED.number_of_pins,
                         color         = EXCLUDED.color,
                         pos_x         = EXCLUDED.pos_x,
                         pos_y         = EXCLUDED.pos_y,
                         width         = EXCLUDED.width,
                         height        = EXCLUDED.height,
                         side          = EXCLUDED.side,
                         collapsed     = EXCLUDED.collapsed""",
                    (
                        c["id"], c["name"], c["module_id"], project_id,
                        c.get("number_of_pins", 0), c.get("color", "#C8C8FF"),
                        c.get("pos_x", 0), c.get("pos_y", 0),
                        c.get("width", 60), c.get("height", 20),
                        c.get("side", "top"),
                        bool(c.get("collapsed", False)),
                    ),
                )

            # ── 6. Import pins ──
            for p in data["pins"]:
                cur.execute(
                    """INSERT INTO pins
                       (id, name, connector_id, project_id, pin_number,
                        pin_type, is_ground, value, current, description)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         name         = EXCLUDED.name,
                         connector_id = EXCLUDED.connector_id,
                         pin_number   = EXCLUDED.pin_number,
                         pin_type     = EXCLUDED.pin_type,
                         is_ground    = EXCLUDED.is_ground,
                         value        = EXCLUDED.value,
                         current      = EXCLUDED.current,
                         description  = EXCLUDED.description""",
                    (
                        p["id"], p["name"], p["connector_id"], project_id,
                        p.get("pin_number"), p.get("pin_type"),
                        p.get("is_ground", False), p.get("value"),
                        p.get("current", 0.0), p.get("description"),
                    ),
                )

            # ── 7. Import interfaces ──
            for i in data["interfaces"]:
                cur.execute(
                    """INSERT INTO interfaces
                       (id, pin1_id, pin2_id, project_id, interface_type,
                        color, pos_x, pos_y, rotation)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         pin1_id        = EXCLUDED.pin1_id,
                         pin2_id        = EXCLUDED.pin2_id,
                         interface_type = EXCLUDED.interface_type,
                         color          = EXCLUDED.color,
                         pos_x          = EXCLUDED.pos_x,
                         pos_y          = EXCLUDED.pos_y,
                         rotation       = EXCLUDED.rotation""",
                    (
                        i["id"], i["pin1_id"], i["pin2_id"], project_id,
                        i.get("interface_type"), i.get("color", "#C8C8FF"),
                        i.get("pos_x", 0), i.get("pos_y", 0),
                        i.get("rotation", 0),
                    ),
                )

            # ── 8. Import interface_points ──
            for ip in data["interface_points"]:
                cur.execute(
                    """INSERT INTO interface_points
                       (id, interface_id, project_id, point_index, x, y, description)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         interface_id = EXCLUDED.interface_id,
                         point_index  = EXCLUDED.point_index,
                         x            = EXCLUDED.x,
                         y            = EXCLUDED.y,
                         description  = EXCLUDED.description""",
                    (
                        ip["id"], ip["interface_id"], project_id,
                        ip["point_index"], ip["x"], ip["y"],
                        ip.get("description"),
                    ),
                )

            # ── 9. Import modes ──
            for m in data["modes"]:
                cur.execute(
                    """INSERT INTO modes (id, name, project_id, data)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         name = EXCLUDED.name,
                         data = EXCLUDED.data""",
                    (m["id"], m["name"], project_id, m.get("data", "{}")),
                )

            # ── 10. Import mode_modules ──
            for mm in data["mode_modules"]:
                cur.execute(
                    """INSERT INTO mode_modules (id, mode_name, module_id, project_id)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         mode_name  = EXCLUDED.mode_name,
                         module_id  = EXCLUDED.module_id""",
                    (mm["id"], mm["mode_name"], mm["module_id"], project_id),
                )

            # ── 11. Import mode_positions ──
            for mp in data["mode_positions"]:
                cur.execute(
                    """INSERT INTO mode_positions
                       (id, mode_name, item_type, item_id, project_id,
                        pos_x, pos_y, width, height, rotation)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         mode_name  = EXCLUDED.mode_name,
                         item_type  = EXCLUDED.item_type,
                         item_id    = EXCLUDED.item_id,
                         pos_x      = EXCLUDED.pos_x,
                         pos_y      = EXCLUDED.pos_y,
                         width      = EXCLUDED.width,
                         height     = EXCLUDED.height,
                         rotation   = EXCLUDED.rotation""",
                    (
                        mp["id"], mp["mode_name"], mp["item_type"],
                        mp["item_id"], project_id,
                        mp.get("pos_x", 0), mp.get("pos_y", 0),
                        mp.get("width", 0), mp.get("height", 0),
                        mp.get("rotation", 0),
                    ),
                )

            # ── 12. Reset sequences to avoid PK conflicts on future inserts ──
            seq_tables = [
                "subsystems", "modules", "connectors", "pins",
                "interfaces", "interface_points", "modes",
                "mode_modules", "mode_positions",
            ]
            for tbl in seq_tables:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                    f"COALESCE(MAX(id), 0) + 1, false) FROM {tbl}"
                )

            conn.commit()
            return True, "Project data imported successfully"

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"Failed to import project data: {e}"


# -----------------------------------------------------------------------------
# File attachments (datasheets) for modules / connectors / pins
# -----------------------------------------------------------------------------
# Attached files are copied into a managed per-project folder so they survive
# the original file being moved/deleted. The DB stores the copied path.


def _attachments_base_dir() -> str:
    """Root folder for copied attachment files (per-project subfolders)."""
    return os.path.join(get_app_data_dir(), "attachments")


def attach_entity_file(project_id: int, entity_type: str, entity_id: int,
                       src_path: str) -> int | None:
    """
    Copy a file into the project's managed attachments folder and record it.
    Returns the new attachment id, or None on failure.
    """
    entity_type = str(entity_type or "").strip().lower()
    if entity_type not in ("module", "connector", "pin"):
        return None
    if not src_path or not os.path.isfile(src_path):
        return None
    # One datasheet per entity: refuse if a file is already attached so the
    # UIs can rely on the invariant (attach disabled once one exists).
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM entity_attachments WHERE project_id = %s "
                "AND entity_type = %s AND entity_id = %s LIMIT 1",
                (project_id, entity_type, entity_id),
            )
            if cur.fetchone() is not None:
                return None
    except Exception:
        return None
    file_name = os.path.basename(src_path)
    dest_dir = os.path.join(
        _attachments_base_dir(), str(project_id), f"{entity_type}_{entity_id}"
    )
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception:
        return None
    dest = os.path.join(dest_dir, file_name)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(file_name)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{stem}_{n}{ext}")
            n += 1
    try:
        shutil.copy2(src_path, dest)
    except Exception:
        return None
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO entity_attachments "
                "(project_id, entity_type, entity_id, file_path, file_name) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (project_id, entity_type, entity_id, dest, file_name),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
    except Exception:
        return None


def list_entity_files(project_id: int, entity_type: str, entity_id: int) -> list[dict]:
    """
    Return attachment records for an entity:
    [{"id", "file_name", "file_path", "uploaded_at"}, ...] (empty on error).
    """
    entity_type = str(entity_type or "").strip().lower()
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, file_name, file_path, uploaded_at FROM entity_attachments "
                "WHERE project_id = %s AND entity_type = %s AND entity_id = %s ORDER BY id",
                (project_id, entity_type, entity_id),
            )
            return [
                {"id": r[0], "file_name": r[1], "file_path": r[2], "uploaded_at": r[3]}
                for r in cur.fetchall()
            ]
    except Exception:
        return []


def remove_entity_file(attachment_id: int) -> bool:
    """Delete an attachment record and its copied file. Returns True on success."""
    path = None
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT file_path FROM entity_attachments WHERE id = %s",
                (attachment_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            path = row[0]
            cur.execute(
                "DELETE FROM entity_attachments WHERE id = %s", (attachment_id,)
            )
            conn.commit()
    except Exception:
        return False
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass
    return True


def copy_entity_files(project_id: int, entity_type: str,
                      src_entity_id: int, dst_entity_id: int) -> int:
    """
    Copy every attached file (datasheet) from one entity to another:
    the file is copied into the destination's managed folder and a new
    entity_attachments row is recorded. Returns the number of files copied
    (0 when the source has none).
    """
    entity_type = str(entity_type or "").strip().lower()
    copied = 0
    for f in list_entity_files(project_id, entity_type, src_entity_id):
        new_id = attach_entity_file(
            project_id, entity_type, dst_entity_id, f["file_path"]
        )
        if new_id is not None:
            copied += 1
    return copied


def project_attachment_keys(project_id: int) -> set:
    """
    Return {(entity_type, entity_id)} for every entity in the project that
    has at least one attachment (used to badge items in the UIs).
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT entity_type, entity_id FROM entity_attachments "
                "WHERE project_id = %s",
                (project_id,),
            )
            return {(r[0], r[1]) for r in cur.fetchall()}
    except Exception:
        return set()
