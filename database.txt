import psycopg2
from psycopg2 import pool
import json
import os
import sys
import threading
from contextlib import contextmanager
import time
import atexit
import bcrypt
from psycopg2.extras import Json
import socket
from datetime import timedelta

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


def test_connection():
    """Return (ok:boolean, message:str) for a simple connectivity test."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            return True, "Connection successful"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"


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

            _add_column_if_not_exists(cur, 'connectors', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'connectors', 'color', "VARCHAR(7) DEFAULT '#C8C8FF'")
            _add_column_if_not_exists(cur, 'connectors', 'pos_x', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'connectors', 'pos_y', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'connectors', 'width', 'REAL DEFAULT 60')
            _add_column_if_not_exists(cur, 'connectors', 'height', 'REAL DEFAULT 20')
            _add_column_if_not_exists(cur, 'connectors', 'side', "VARCHAR(10) DEFAULT 'top'")

            _add_column_if_not_exists(cur, 'pins', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'pins', 'pin_number', 'INTEGER')
            _add_column_if_not_exists(cur, 'pins', 'current', 'REAL DEFAULT 0.0')

            _add_column_if_not_exists(cur, 'interfaces', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'interfaces', 'color', "VARCHAR(7) DEFAULT '#C8C8FF'")
            _add_column_if_not_exists(cur, 'interfaces', 'pos_x', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'interfaces', 'pos_y', 'REAL DEFAULT 0')
            _add_column_if_not_exists(cur, 'interfaces', 'rotation', 'REAL DEFAULT 0')

            _add_column_if_not_exists(cur, 'interface_points', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'interface_points', 'description', 'TEXT')  # for routing metadata

            _add_column_if_not_exists(cur, 'modes', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'mode_modules', 'project_id', 'INTEGER')
            _add_column_if_not_exists(cur, 'mode_positions', 'project_id', 'INTEGER')

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

            for mid in module_ids:
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


        # indexes for faster auth lookups

        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (LOWER(username))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_role_perms_role ON role_permissions(role_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_subsystems_user ON user_subsystems(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON user_sessions(user_id, is_active)")
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

def add_interface_guarded(user_id: int, pin1_id: int, pin2_id: int, color: str, project_id: int):
    guard_or_raise(user_id, "interface.create", None, "interface.create",
                   details={"pin1_id": pin1_id, "pin2_id": pin2_id})
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
            INSERT INTO interfaces (pin1_id, pin2_id, color, project_id)
            VALUES (%s, %s, %s, %s)
        """, (pin1_id, pin2_id, color, project_id))
        conn.commit()
        return (True, "Interface added.")

def update_interface_guarded(user_id: int, iface_id: int, pin1_id: int, pin2_id: int, color: str, project_id: int):
    guard_or_raise(user_id, "interface.edit", None, "interface.edit",
                   details={"iface_id": iface_id})
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE interfaces
            SET pin1_id=%s, pin2_id=%s, color=%s
            WHERE id=%s AND project_id=%s
        """, (pin1_id, pin2_id, color, iface_id, project_id))
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
