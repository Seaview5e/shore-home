import os
import sqlite3


DATABASE_FILE = os.environ.get(
    "DATABASE_FILE",
    "shore_home.db"
)


def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS booking_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            arrival_date TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            adults INTEGER NOT NULL,
            children INTEGER NOT NULL,
            pets TEXT NOT NULL,
            food_restrictions TEXT,
            comments TEXT,
            status TEXT DEFAULT 'pending',
            guest_profile_id INTEGER,
            invitation_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            floor TEXT,
            bed_type TEXT,
            capacity INTEGER NOT NULL,
            pet_friendly TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            arrival_date TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            status TEXT DEFAULT 'approved',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (request_id) REFERENCES booking_requests(id),
            FOREIGN KEY (room_id) REFERENCES rooms(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocked_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            email_type TEXT,
            recipient TEXT,
            subject TEXT,
            body TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS guest_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_name TEXT NOT NULL,
            primary_email TEXT NOT NULL UNIQUE,
            phone TEXT,
            additional_names TEXT,
            pet_notes TEXT,
            food_notes TEXT,
            host_notes TEXT,
            photo_path TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guest_profile_id INTEGER NOT NULL,
            invitation_title TEXT,
            arrival_date TEXT,
            departure_date TEXT,
            message TEXT,
            status TEXT DEFAULT 'draft',
            response_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guest_profile_id) REFERENCES guest_profiles(id)
        )
    """)

    try:
        cursor.execute("""
            ALTER TABLE booking_requests
            ADD COLUMN additional_names TEXT
        """)
    except:
        pass

    try:
        cursor.execute("""
            ALTER TABLE booking_requests
            ADD COLUMN rooms_requested INTEGER DEFAULT 1
        """)
    except:
        pass

    try:
        cursor.execute("""
            ALTER TABLE booking_requests
            ADD COLUMN response_message TEXT
        """)
    except:
        pass

    try:
        cursor.execute("""
            ALTER TABLE booking_requests
            ADD COLUMN email_status TEXT DEFAULT 'not_needed'
        """)
    except:
        pass

    try:
        cursor.execute("""
            ALTER TABLE booking_requests
            ADD COLUMN email_needed_type TEXT
        """)
    except:
        pass

    room_count = cursor.execute(
        "SELECT COUNT(*) FROM rooms"
    ).fetchone()[0]

    if room_count == 0:
        cursor.executemany("""
            INSERT INTO rooms
            (name, floor, bed_type, capacity, pet_friendly)
            VALUES (?, ?, ?, ?, ?)
        """, [
            ("Yellow Room", "1st floor", "Queen", 2, "No"),
            ("Blue Room", "1st floor", "Queen", 2, "No"),
            ("Green Room", "Lower level", "King", 2, "Yes"),
            ("Twin Room", "Lower level", "Twin beds", 2, "No")
        ])

    conn.commit()
    conn.close()

def reset_demo_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM bookings")
    cursor.execute("DELETE FROM booking_requests")
    cursor.execute("DELETE FROM blocked_dates")
    cursor.execute("DELETE FROM email_log")
    cursor.execute("DELETE FROM invitations")
    cursor.execute("DELETE FROM guest_profiles")

    conn.commit()
    conn.close()

    print("Operational data reset complete.")


def reset_demo_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM bookings")
    cursor.execute("DELETE FROM booking_requests")
    cursor.execute("DELETE FROM invitations")
    cursor.execute("DELETE FROM guest_profiles")
    cursor.execute("DELETE FROM email_log")

    conn.commit()
    conn.close()

    print("Operational demo data reset.")

if __name__ == "__main__":
    init_db()
    print("Database initialized!")