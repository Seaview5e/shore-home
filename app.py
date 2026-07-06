from flask import Flask, request, redirect, render_template, render_template_string, session, send_from_directory
from datetime import date, datetime, timedelta
from database import get_db_connection, DATABASE_FILE, init_db
import smtplib
from email.message import EmailMessage
import os
import shutil
import sqlite3
import html as html_escape_module
import logging
import traceback
import re
import hmac
import secrets
from werkzeug.exceptions import HTTPException
 
 
app = Flask(__name__)

with app.app_context():
    init_db()

    schema_conn = get_db_connection()
    try:
        schema_conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                action_type TEXT NOT NULL DEFAULT '',
                old_status TEXT,
                new_status TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        activity_columns = {
            row["name"]
            for row in schema_conn.execute("PRAGMA table_info(activity_log)").fetchall()
        }

        required_activity_columns = {
            "request_id": "INTEGER",
            "action_type": "TEXT NOT NULL DEFAULT ''",
            "old_status": "TEXT",
            "new_status": "TEXT",
            "notes": "TEXT",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }

        for column_name, column_definition in required_activity_columns.items():
            if column_name not in activity_columns:
                schema_conn.execute(
                    f"ALTER TABLE activity_log ADD COLUMN {column_name} {column_definition}"
                )

        booking_columns = {
            row["name"]
            for row in schema_conn.execute("PRAGMA table_info(booking_requests)").fetchall()
        }

        required_booking_columns = {
            "additional_names": "TEXT",
            "rooms_requested": "INTEGER DEFAULT 1",
            "response_message": "TEXT",
            "email_status": "TEXT DEFAULT 'not_needed'",
            "email_needed_type": "TEXT",
            "coordination_notes": "TEXT",
        }

        for column_name, column_definition in required_booking_columns.items():
            if column_name not in booking_columns:
                schema_conn.execute(
                    f"ALTER TABLE booking_requests ADD COLUMN {column_name} {column_definition}"
                )

        schema_conn.commit()
        print("DATABASE SCHEMA CHECK COMPLETE", flush=True)
    finally:
        schema_conn.close()

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "shore-home-local-dev-key-change-in-production"
)

# Production hardening: basic file logging.
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename=os.path.join("logs", "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
error_logger = logging.getLogger("shore_home_errors")
error_handler = logging.FileHandler(os.path.join("logs", "errors.log"))
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
error_logger.addHandler(error_handler)
error_logger.setLevel(logging.ERROR)


APP_VERSION = os.environ.get(
    "APP_VERSION",
    "app_V36_2_RECOVERY_HARDENING"
)

BASE_URL = os.environ.get(
    "BASE_URL",
    "http://127.0.0.1:5000"
).rstrip("/")


def standard_new_request_url():

    return BASE_URL.rstrip("/") + "/new-request"


def invitation_request_url(invitation_id):

    invitation_id_text = safe_text(invitation_id).strip()

    if invitation_id_text.isdigit():
        return BASE_URL.rstrip("/") + "/invite/" + invitation_id_text

    return standard_new_request_url()


def repeat_visit_request_url_for_row(request_row):

    if not request_row:
        return standard_new_request_url()

    invitation_id = row_value(
        request_row,
        "invitation_id"
    )

    return invitation_request_url(invitation_id)


def organizer_planning_url(member_id):

    member_id_text = safe_text(member_id).strip()

    if member_id_text.isdigit():
        return BASE_URL.rstrip("/") + "/coordination-group-member/" + member_id_text + "/organizer-planning"

    return BASE_URL.rstrip("/") + "/coordination-groups"


def existing_reservations_section_for_guest(conn, guest_profile_id):

    guest_profile_id_text = safe_text(guest_profile_id).strip()

    if not guest_profile_id_text.isdigit():
        return ""

    request_row = conn.execute("""
        SELECT id
        FROM booking_requests
        WHERE guest_profile_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (
        guest_profile_id_text,
    )).fetchone()

    if not request_row:
        return ""

    all_reservations_link = BASE_URL + "/request/" + safe_text(request_row["id"]) + "/all-reservations"

    return (
        "Already have a visit scheduled?\n\n"
        "View or Change Your Reservations:\n"
        + all_reservations_link
        + "\n"
    )
 

    member_id_text = safe_text(member_id).strip()

    if member_id_text.isdigit():
        return BASE_URL.rstrip("/") + "/coordination-group-member/" + member_id_text + "/organizer-planning"

    return BASE_URL.rstrip("/") + "/coordination-groups"

EMAIL_ADDRESS = os.environ.get(
    "EMAIL_ADDRESS",
    "strathmere.visits@gmail.com"
)

EMAIL_APP_PASSWORD = os.environ.get(
    "EMAIL_APP_PASSWORD",
    ""
)

HTML_EMAILS_ENABLED = os.environ.get(
    "HTML_EMAILS_ENABLED",
    "true"
).lower() not in ("0", "false", "no")

ADMIN_NOTIFICATION_EMAIL = os.environ.get(
    "ADMIN_NOTIFICATION_EMAIL",
    EMAIL_ADDRESS
)

ADMIN_NOTIFICATIONS_ENABLED = os.environ.get(
    "ADMIN_NOTIFICATIONS_ENABLED",
    "1"
).strip().lower() not in ["0", "false", "no", "off"]

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "admin"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    ""
)

ADMIN_AUTH_ENABLED = bool(ADMIN_PASSWORD)

PRODUCTION_MODE = (
    os.environ.get("RENDER", "").strip().lower() == "true"
    or os.environ.get("FLASK_ENV", "").strip().lower() == "production"
    or os.environ.get("APP_ENV", "").strip().lower() == "production"
)

if PRODUCTION_MODE:
    missing_required_env = []

    if not os.environ.get("SECRET_KEY"):
        missing_required_env.append("SECRET_KEY")

    if not ADMIN_PASSWORD:
        missing_required_env.append("ADMIN_PASSWORD")

    if not EMAIL_APP_PASSWORD:
        missing_required_env.append("EMAIL_APP_PASSWORD")

    if not BASE_URL or BASE_URL.startswith("http://127.0.0.1") or BASE_URL.startswith("http://localhost"):
        missing_required_env.append("BASE_URL")

    if missing_required_env:
        raise RuntimeError(
            "Production startup blocked. Missing or unsafe required environment variable(s): "
            + ", ".join(missing_required_env)
        )


print("APP VERSION:", APP_VERSION)
print("DATABASE FILE:", DATABASE_FILE)
print("BASE URL:", BASE_URL)
print("EMAIL ADDRESS:", EMAIL_ADDRESS)
print("EMAIL PASSWORD CONFIGURED:", bool(EMAIL_APP_PASSWORD))
print("ADMIN NOTIFICATIONS ENABLED:", ADMIN_NOTIFICATIONS_ENABLED)
print("ADMIN NOTIFICATION EMAIL:", ADMIN_NOTIFICATION_EMAIL)
print("ADMIN AUTH ENABLED:", ADMIN_AUTH_ENABLED)

# EMAIL TEMPLATES

DECLINE_EMAIL_TEMPLATE = """
Hi {guest_name},

Thanks again for reaching out about a visit to Strathmere.

Unfortunately, the dates requested below will not work for this visit:

━━━━━━━━━━━━━━━━━━

Requested Stay

Arrival: {arrival_date}
Departure: {departure_date}
Length of Stay: {nights} night(s)
Rooms Requested: {rooms_requested}
Confirmed Group Members: {additional_names}

━━━━━━━━━━━━━━━━━━
Additional Notes:
{decline_reason}

If you would like, you can use the link below to submit alternate dates for consideration:

{request_link}

We’d still love to coordinate another time if schedules and space allow.

Feel free to reply directly with any questions.

John & Mark
302-521-5401
"""

DATE_CHANGE_EMAIL_TEMPLATE = """
Hi {guest_name},

We’ve updated the details for your Strathmere visit.

VISIT DETAILS:
- Arrival: {arrival_date}
- Departure: {departure_date}
- Nights: {nights}
- Rooms: {rooms_requested}
- Assigned Room(s): {room_list}

Confirmed Group Members: {additional_names}
{coordinating_with_section}{optional_admin_message}If anything does not look right, just reply to this email.

Looking forward to seeing everyone at the shore!

Need to make a change?

Change Visit:
{{ change_link }}

Cancel Visit:
{{ cancel_link }}

Request Another Visit:
{{ new_request_link }}

John & Mark
302-521-5401
"""

APPROVAL_EMAIL_TEMPLATE = """
Hi {guest_name},

WE’RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!

VISIT DETAILS:
- Arrival: {arrival_date}
- Departure: {departure_date}
- Nights: {nights} night(s)
- Room(s) Approved: {rooms_requested}
- Assigned Room(s): {room_list}
(this may still adjust slightly depending on final house planning)

Confirmed Group Members: {additional_names}
{coordinating_with_section}{optional_admin_message}If your plans change or you need anything before your visit, just reply to this email.

Looking forward to having everyone down at the shore and hoping for great weather.

John & Mark
302-521-5401
"""


EMAIL_TEMPLATE_METADATA = {
    "approval": {
        "name": "Approval Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Standard request approval / confirmation text."
    },
    "decline": {
        "name": "Decline Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Standard request decline with alternate request link."
    },
    "date_change": {
        "name": "Date Change / Update Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Guest-facing update when approved visit details change."
    },
    "cancellation": {
        "name": "Cancellation Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Cancellation confirmation after guest/admin cancellation action."
    },
    "invitation": {
        "name": "Standard Invitation Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Initial guest invitation with request link."
    },
    "organizer_kickoff": {
        "name": "Organizer Setup Email",
        "version": "1.0",
        "last_updated": "2026-06-16",
        "updated_by": "John",
        "notes": "Organizer setup email. Stored in templates/emails/organizer_kickoff.txt."
    },
    "organizer_suggestions_admin": {
        "name": "Organizer Email Sent / Returned Suggestions Admin Alert",
        "version": "1.0",
        "last_updated": "2026-06-16",
        "updated_by": "John",
        "notes": "Admin alert sent after organizer submits group setup suggestions. Stored in templates/emails/organizer_suggestions_admin.txt."
    },
    "coordination_invitation": {
        "name": "Coordination Invitation / Update Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Group date coordination invitation/update with each guest's personal link."
    },
    "coordination_follow_up": {
        "name": "Targeted Coordination Follow-Up Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Sent only to unmatched guests to update dates/flexibility."
    },
    "tentative_confirmation": {
        "name": "Tentative Date Confirmation Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Asks guests to confirm whether tentative group dates work."
    },
    "final_coordination": {
        "name": "Final Group Confirmation Email",
        "version": "1.0",
        "last_updated": "2026-05-31",
        "updated_by": "John",
        "notes": "Final coordination confirmation after group dates are accepted."
    },
    "profile_welcome": {
        "name": "Profile Welcome Email",
        "version": "1.0",
        "last_updated": "2026-06-17",
        "updated_by": "John",
        "notes": "Sent when a new guest profile is created before invitations are sent."
    },
    "coordination_unmatched_follow_up": {
        "name": "Coordination Unmatched Follow-Up Email",
        "version": "1.0",
        "last_updated": "2026-06-18",
        "updated_by": "John",
        "notes": "Sent to guests whose dates do not overlap with a possible group option."
    },
    "tentative_group_dates": {
        "name": "Tentative Dates That May Work for Everyone Email",
        "version": "1.0",
        "last_updated": "2026-06-18",
        "updated_by": "John",
        "notes": "Sent when tentative coordination dates need guest confirmation."
    },
    "final_group_ready": {
        "name": "Final Group Ready Email",
        "version": "1.0",
        "last_updated": "2026-06-18",
        "updated_by": "John",
        "notes": "Sent when all group members confirm tentative dates."
    },
    "final_visit_confirmation": {
        "name": "Final Visit Confirmation Email",
        "version": "1.0",
        "last_updated": "2026-06-18",
        "updated_by": "John",
        "notes": "Final guest confirmation sent from coordination close/finalize."
    },
    "admin_notification": {
        "name": "Admin Notification Email",
        "version": "1.0",
        "last_updated": "2026-06-18",
        "updated_by": "John",
        "notes": "Generic admin action notification."
    }
}



EMAIL_TEMPLATE_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "templates",
    "emails"
)

DEFAULT_EMAIL_TEMPLATES = {
    "approval.txt": """Hi {{ guest_name }},

WE’RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!

VISIT DETAILS:
- Arrival: {{ arrival_date }}
- Departure: {{ departure_date }}
- Nights: {{ nights }} night(s)
- Room(s) Approved: {{ rooms_requested }}
- Assigned Room(s): {{ room_list }}
(this may still adjust slightly depending on final house planning)

Confirmed Group Members: {{ additional_names }}
{{ coordinating_with_section }}{{ optional_admin_message }}If your plans change or you need anything before your visit, just reply to this email.

{{ change_links_section }}

Looking forward to having everyone down at the shore and hoping for great weather.

John & Mark
302-521-5401
""",
    "decline.txt": """Hi {{ guest_name }},

Thanks again for reaching out about a visit to Strathmere.

Unfortunately, the dates requested below will not work for this visit:

━━━━━━━━━━━━━━━━━━

Requested Stay

Arrival: {{ arrival_date }}
Departure: {{ departure_date }}
Length of Stay: {{ nights }} night(s)
Rooms Requested: {{ rooms_requested }}
Confirmed Group Members: {{ additional_names }}

━━━━━━━━━━━━━━━━━━
Additional Notes:
{{ decline_reason }}

If you would like, you can use the link below to submit alternate dates for consideration:

{{ request_link }}

We’d still love to coordinate another time if schedules and space allow.

Feel free to reply directly with any questions.

John & Mark
302-521-5401
""",
    "date_change.txt": """Hi {{ guest_name }},

We’ve updated the details for your Strathmere visit.

VISIT DETAILS:
- Arrival: {{ arrival_date }}
- Departure: {{ departure_date }}
- Nights: {{ nights }}
- Rooms: {{ rooms_requested }}
- Assigned Room(s): {{ room_list }}

Confirmed Group Members: {{ additional_names }}
{{ coordinating_with_section }}{{ optional_admin_message }}If anything does not look right, just reply to this email.

{{ change_links_section }}

Looking forward to seeing everyone at the shore!

John & Mark
302-521-5401
""",
    "cancellation.txt": """Hi {{ guest_name }},

Your Strathmere visit has been cancelled.

Cancelled Visit Details:
- Arrival: {{ arrival_date }}
- Departure: {{ departure_date }}
- Nights: {{ nights }}
- Rooms: {{ rooms_requested }}

Thanks for letting us know.

John & Mark
302-521-5401
""",

    "invitation.txt": """Hi {{ guest_name }},

We’d love to invite you to request a visit to Strathmere.

Please use the request link below to submit your visit request:

{{ request_link }}

You can still use regular email or a phone call at any point if that’s easier.

Looking forward to hopefully seeing everyone down at the shore.

John & Mark
302-521-5401
""",

    "organizer_kickoff.txt": """Hi {{ guest_name }},

A group visit planning process has started for:

{{ group_title }}

Your role: Organizer

Please use the link below to help set up the group. You can suggest who should be included and one preferred date range to start the planning process.


{{ planning_link }}

After you submit this first setup information, John and Mark will review it. Then everyone in the group, including you, will receive individual requests to submit or confirm dates.

Nothing is confirmed or booked yet.

John & Mark
302-521-5401
""",
    "organizer_suggestions_admin.txt": """Organizer email sent / organizer setup returned submitted for {{ group_title }}

Organizer:
{{ organizer_name }} <{{ organizer_email }}>

Suggested group members:
{{ suggested_guests }}

Preferred dates to start planning:
{{ preferred_dates }}

Expected rooms:
{{ rooms_requested }}

Organizer notes:
{{ date_notes }}

Admin actions:
Open Group Planning Page:
{{ group_link }}

Open Guest Profiles:
{{ guest_profiles_link }}

Next step: review the suggested people and dates, then add/confirm guest profiles before sending broader coordination invitations.
""",
    "coordination_invitation.txt": """Hi {{ guest_name }},

We are starting a group date coordination process for:

{{ group_title }}

This visit is being organized by:
{{ organizer_name }} {{ organizer_email_display }}

Your role in this group:
{{ guest_role }}

The goal is simple: collect preferred and alternate dates from everyone, compare overlap, and then propose tentative dates for the group to confirm.

Nothing is confirmed or booked yet.

Group members:
{{ group_member_text }}

Current dates and analysis so far:
{{ suggestion_text }}

Please use your personal link below to submit or update your date options:

{{ request_link }}

Thanks!

John & Mark
302-521-5401
""",
    "coordination_follow_up.txt": """Hi {{ guest_name }},

We’re still trying to find dates that work for the group.

Current proposed dates:
{{ tentative_dates }}

Please use this link to confirm or update your availability:

{{ request_link }}

Thanks!

John & Mark
302-521-5401
""",
    "tentative_confirmation.txt": """Hi {{ guest_name }},

The group has a possible date option:

{{ tentative_dates }}

Please use this link to let us know if these dates work for you:

{{ request_link }}

Need to make a change?

Change Visit:
{{ request_link }}

Cancel / Cannot Make These Dates:
{{ request_link }}

Request Another Visit:
{{ base_url }}

Thanks!

John & Mark
302-521-5401
""",
    "booking_confirmation.txt": """Hi {{ guest_name }},

Your Strathmere visit is confirmed.

VISIT DETAILS:
- Arrival: {{ arrival_date }}
- Departure: {{ departure_date }}
- Nights: {{ nights }}
- Room(s): {{ room_list }}

Confirmed Group Members: {{ additional_names }}

{{ change_links_section }}

Looking forward to seeing everyone at the shore!

John & Mark
302-521-5401
""",
    "admin_alert.txt": """ADMIN ACTION – {{ group_name }}

Current Status:
{{ current_status }}

NEXT STEP:
{{ next_step }}

Open Group:
{{ group_link }}
""",
    "planning_failed.txt": """ADMIN ACTION – {{ group_name }}

Current Status:
No group date worked after the planning rounds.

NEXT STEP:
Start a new group, duplicate this group, or archive it.

Open Group:
{{ group_link }}
""",
    "reminder.txt": """Hi {{ guest_name }},

Quick reminder to add or review your dates for {{ group_title }}.

Current responses:
{{ response_count }}

Your link:
{{ request_link }}

Thanks!

John & Mark
302-521-5401
""",
    "thank_you.txt": """Thanks!

Your response has been saved.

You’re all set for now.
""",
    "coordination_unmatched_follow_up.txt": """Hi {{ guest_name }},

We found a possible group date for {{ group_title }}:

{{ suggested_dates }}

Right now, your submitted dates do not overlap with this option.

Please use the link below to review your dates. You can add another date option, increase your flexibility, or let us know if these dates will not work.

{{ request_link }}

Thanks!

John & Mark
Strathmere Visit Request System
302-521-5401
""",

        "capacity_review.txt": """Hi {{ guest_name }},

We are reviewing the group visit request for:

{{ group_name }}

{{ capacity_message }}

Current group date / bedroom information:

{{ group_summary }}

Change Visit:
{{ request_link }}

Nothing is confirmed or declined yet.

Thanks!
John & Mark
302-521-5401
""",

    "tentative_group_dates.txt": """Hi {{ guest_name }},

We are trying to confirm tentative dates for {{ group_title }}.

Tentative dates:
{{ tentative_dates }}

Response due date:
{{ due_date }}

Please use your link below to let us know whether these dates work, do not work, or need discussion.

{{ request_link }}

Nothing is fully booked yet. This helps us coordinate the group before final approvals.

John & Mark
Strathmere Visit Request System
302-521-5401
""",

    "final_group_ready.txt": """Hi {{ guest_name }},

Good news — everyone has confirmed the tentative dates for {{ group_title }}.

Confirmed group dates:
{{ tentative_dates }}

The next step is final booking review and room planning. Nothing is fully booked until the normal booking requests are reviewed and approved.

If anything changes before final approvals are completed, please reply as soon as possible.

Thanks everyone for coordinating together.

John & Mark
Strathmere Visit Request System
302-521-5401
""",

    "final_visit_confirmation.txt": """Hi {{ guest_name }},

Your Strathmere visit is confirmed.

Visit Details:
- Arrival: {{ arrival_date }}
- Departure: {{ departure_date }}
- Nights: {{ nights }}
- Room(s): {{ room_list }}
- Rooms Requested: {{ rooms_requested }}

Confirmed Group Members:
{{ confirmed_group_members }}

Food Preferences / Restrictions:
{{ food_restrictions }}

Pets:
{{ pets }}

Need to change or cancel this visit?
Change request: {{ change_link }}
Cancel request: {{ cancel_link }}
Request another visit: {{ new_request_link }}

If anything does not look right, just reply to this email.

Looking forward to seeing everyone at the shore!

John & Mark
Strathmere Visit Request System
302-521-5401
""",

    "admin_notification.txt": """Action needed in the Strathmere Visit Request System

Action:
{{ action_title }}

Details:
{{ details }}

Review:
{{ review_url }}

John & Mark
Strathmere Visit Request System
""",
    "profile_welcome.txt": """Hi {{ guest_name }},

Welcome to the Strathmere Visit Request System.

You’ve officially been added to this season’s highly sophisticated, mildly experimental, AI-assisted visit planning system.

Translation: I’ve been having way too much fun building something to make planning visits easier… and hopefully slightly more entertaining.

Like any beta, there may still be a few rough edges. If something feels confusing, awkward, or unexpectedly robotic, please let me know so I can keep improving it. Any mistakes are probably AI-generated, but customer support is still delightfully human. Email and phone calls still work too — we haven’t handed full control over to the robots… yet.

You don’t need to do anything today.

Soon you may receive an invitation to request a visit or to coordinate dates with others.

A Request a Visit invitation is for planning a stay for yourself or for people traveling with you. That could mean your family, children, relatives, friends, or anyone you are helping organize for the same dates.

A Coordination invitation is for a group trying to line up dates before anything is finalized. Everyone shares dates that work best, and we try to find overlap without creating seventeen separate text chains.

A few helpful notes:

• Each visit date range needs its own request.
• You can request multiple rooms when everyone is staying the same dates.
• Please include everyone’s names so we can plan accommodations accurately.
• Nothing is confirmed until you receive approval.

Once invitations begin, the system will walk you through the rest and send follow-up emails with confirmations and next steps.

Looking forward to seeing everyone this season.

John & Mark
Strathmere Visit Request System
302-521-5401
""",
}


def ensure_room_name_updates(conn):

    try:
        conn.execute("""
            UPDATE rooms
            SET name = 'Twin/King Room'
            WHERE LOWER(TRIM(name)) IN ('twin room', 'twin')
        """)
        conn.commit()
    except Exception:
        pass


def ensure_email_template_files():

    os.makedirs(
        EMAIL_TEMPLATE_FOLDER,
        exist_ok=True
    )

    for template_name, template_text in DEFAULT_EMAIL_TEMPLATES.items():

        template_path = os.path.join(
            EMAIL_TEMPLATE_FOLDER,
            template_name
        )

        # V29E: Never auto-create invitation.txt from app.py defaults.
        # The invitation preview/email must come from the real editable
        # templates/emails/invitation.txt file only.
        if template_name == "invitation.txt":
            continue

        if not os.path.exists(template_path):

            with open(template_path, "w", encoding="utf-8") as handle:
                handle.write(template_text)


def email_template_path(template_name):

    return os.path.join(
        EMAIL_TEMPLATE_FOLDER,
        template_name
    )


def load_email_template(template_name):

    ensure_email_template_files()

    template_path = email_template_path(template_name)

    if os.path.exists(template_path):

        with open(template_path, "r", encoding="utf-8") as handle:
            template_text = handle.read()

        return template_text

    if template_name == "invitation.txt":
        raise RuntimeError(
            "templates/emails/invitation.txt is missing. Invitation preview/email stopped so app.py default text cannot replace your template."
        )

    return DEFAULT_EMAIL_TEMPLATES.get(template_name, "")


def save_email_template(template_name, template_text):

    os.makedirs(
        EMAIL_TEMPLATE_FOLDER,
        exist_ok=True
    )

    template_path = email_template_path(template_name)

    with open(template_path, "w", encoding="utf-8") as handle:
        handle.write(safe_text(template_text))


def invitation_template_admin_box():

    template_path = email_template_path("invitation.txt")

    try:
        template_text = load_email_template("invitation.txt")
        first_lines = "\n".join(template_text.splitlines()[:10])
        status = "READING REAL FILE"
    except Exception as error:
        first_lines = safe_text(error)
        status = "ERROR"

    return f"""
    <div style="
        border: 3px solid #dc3545;
        background: #fff5f5;
        padding: 12px;
        max-width: 950px;
        margin: 12px 0;
        font-size: 13px;
    ">
        <strong>Invitation Template Source Check</strong><br>
        Status: {safe_text(status)}<br>
        File: <code>{safe_text(template_path)}</code><br>
        <a href="/admin/invitation-template" style="font-weight:bold;">Open / Edit Actual invitation.txt</a>
        <pre style="white-space: pre-wrap; background: white; padding: 8px; border: 1px solid #ddd;">{safe_text(first_lines)}</pre>
    </div>
    """


ensure_email_template_files()

try:
    startup_conn = get_db_connection()
    ensure_room_name_updates(startup_conn)
    startup_conn.close()
except Exception:
    pass


def rebuild_email_template_files():

    os.makedirs(
        EMAIL_TEMPLATE_FOLDER,
        exist_ok=True
    )

    for template_name, template_text in DEFAULT_EMAIL_TEMPLATES.items():

        # V29E: do not overwrite the real invitation template with
        # hardcoded app.py wording during rebuild.
        if template_name == "invitation.txt":
            continue

        template_path = os.path.join(
            EMAIL_TEMPLATE_FOLDER,
            template_name
        )

        with open(template_path, "w", encoding="utf-8") as handle:
            handle.write(template_text)


@app.route("/admin/invitation-template", methods=["GET", "POST"])
def admin_invitation_template_editor():

    message = ""

    if request.method == "POST":

        template_text = request.form.get("template_text")

        save_email_template(
            "invitation.txt",
            template_text
        )

        message = "Saved. Preview/send will now use this exact invitation.txt file."

    try:
        current_template = load_email_template("invitation.txt")
    except Exception:
        current_template = ""

    template_path = email_template_path("invitation.txt")

    return f"""
    {nav_links()}

    <h1>Edit Actual Invitation Template</h1>

    <p style="font-weight:bold; color:#dc3545;">
        This is the exact file used by invitation preview and invitation send.
    </p>

    <p>
        File: <code>{safe_text(template_path)}</code>
    </p>

    <p style="color: green; font-weight: bold;">
        {safe_text(message)}
    </p>

    <form method="POST">
        <textarea name="template_text" rows="28" cols="100" style="width:100%; max-width:950px; font-family:monospace; font-size:14px; line-height:1.45;">{safe_text(current_template)}</textarea>
        <br><br>
        <button type="submit" style="font-weight:bold; padding:8px 14px;">
            Save invitation.txt
        </button>
    </form>

    <p>
        Required variables you can use: <code>{{{{ guest_name }}}}</code>, <code>{{{{ request_link }}}}</code>, <code>{{{{ coordination_link }}}}</code>
    </p>

    <p>
        <a href="/invitations">Back to Invitations</a>
    </p>
    """


@app.route("/admin/rebuild-email-templates", methods=["GET", "POST"])
def admin_rebuild_email_templates():

    if request.method != "POST":
        return action_confirmation_page(
            "Rebuild Email Templates",
            "This resets template files from app defaults and can overwrite wording. Continue only if you intentionally want to rebuild templates.",
            "/admin/rebuild-email-templates",
            "/production-check"
        )

    rebuild_email_template_files()

    return f"""
    {nav_links()}

    <h1>Email Templates Rebuilt</h1>

    <p>All email TXT templates were reset to the current app default wording.</p>

    <p><a href="/dashboard">Back to Dashboard</a></p>
    """


def email_template_metadata(template_key):

    return EMAIL_TEMPLATE_METADATA.get(
        safe_text(template_key).strip(),
        {
            "name": "Email Template",
            "version": "1.0",
            "last_updated": "2026-05-31",
            "updated_by": "John",
            "notes": "Template metadata not yet customized."
        }
    )


def email_template_metadata_html(template_key):

    metadata = email_template_metadata(template_key)

    return f"""
    <div style="
        border: 2px solid #0d6efd;
        background-color: #f8fbff;
        padding: 10px 12px;
        max-width: 950px;
        margin: 8px 0 14px 0;
        font-size: 12px;
        line-height: 1.35;
        border-radius: 6px;
    ">
        <div style="
            font-weight: bold;
            color: #0d6efd;
            margin-bottom: 4px;
            font-size: 13px;
        ">
            Template Information — Admin Only
        </div>
        <strong>Template:</strong> {safe_text(metadata['name'])}
        &nbsp; | &nbsp;
        <strong>Version:</strong> {safe_text(metadata['version'])}
        &nbsp; | &nbsp;
        <strong>Last Updated:</strong> {safe_text(metadata['last_updated'])}
        by {safe_text(metadata['updated_by'])}<br>
        <span style="color: #555;">
            <strong>Admin Notes:</strong> {safe_text(metadata['notes'])}
        </span>
    </div>
    """


def render_email_template(template_name, **context):

    if "base_url" not in context:
        # Guest-facing email templates use base_url in "Request a Visit" sections.
        # Point it to the standard visitor request page, not the app root/dashboard-style page.
        context["base_url"] = standard_new_request_url()

    if "new_request_link" not in context:
        context["new_request_link"] = standard_new_request_url()

    template_text = load_email_template(
        template_name
    )

    body = render_template_string(
        template_text,
        **context
    )

    while "\n\n\n" in body:
        body = body.replace(
            "\n\n\n",
            "\n\n"
        )

    return body


def plain_text_to_html_email(subject, body):

    escaped_subject = html_escape_module.escape(str(subject or ""))
    body_text = str(body or "")

    # V30.9: embed the header image as an inline CID attachment.
    # This avoids email clients blocking or failing to fetch the public URL.
    email_header_html = """
                <div style="background:#ffffff; border-bottom:1px solid #d5e0ea; text-align:center; line-height:0;">
                    <img src="cid:shore_home_header"
                         alt="Shore Home"
                         width="600"
                         style="display:block; width:100%; max-width:600px; height:auto; max-height:220px; object-fit:cover; border:0; margin:0 auto; line-height:0;">
                </div>
    """

    # Keep TXT templates as the source of truth.
    # HTML email cleans up presentation by moving detail rows into one card
    # and URL lines into buttons, so those sections do not repeat below.
    url_pattern = re.compile(r"(https?://[^\s<]+)")

    def action_label_for_url(url, nearby_text=""):

        nearby_lower = safe_text(nearby_text).lower()
        url_lower = safe_text(url).lower()
        normalized_url = url_lower.rstrip("/")
        normalized_base = BASE_URL.rstrip("/").lower()

        # URL-specific paths win over nearby text. This prevents a previous
        # label line from causing the next button to inherit the wrong label.
        if "/all-reservations" in url_lower:
            return "All Reservations"

        if "/change" in url_lower:
            return "Change Request"

        if "/cancel" in url_lower:
            return "Cancel Request"

        if (
            normalized_url == normalized_base
            or normalized_url == normalized_base + "/new-request"
            or "/new-request" in url_lower
            or "/invite" in url_lower
        ):
            return "Request a Visit"

        if "all reservations" in nearby_lower:
            return "All Reservations"

        if "guest profile" in nearby_lower or "/profiles" in url:
            return "Open Guest Profiles"

        if "group planning" in nearby_lower or "open group" in nearby_lower or "/coordination-group/" in url:
            return "Open Group Planning Page"

        if "coordination" in nearby_lower:
            return "Open Coordination Link"

        if "change" in nearby_lower:
            return "Change Request"

        if "cancel" in nearby_lower:
            return "Cancel Request"

        if "another" in nearby_lower or "new invitation" in nearby_lower or "request a visit" in nearby_lower:
            return "Request a Visit"

        if "request" in nearby_lower:
            return "Request a Visit"

        return "Open Link"

    def make_inline_link(match):

        url = match.group(1)
        safe_url = html_escape_module.escape(url, quote=True)

        return (
            f'<a href="{safe_url}" '
            f'style="color:#0f4c81; font-weight:bold; text-decoration:underline;">'
            f'{safe_url}</a>'
        )

    detail_labels = [
        "Arrival:",
        "Departure:",
        "Nights:",
        "Length of Stay:",
        "Rooms:",
        "Room(s):",
        "Rooms Requested:",
        "Room(s) Approved:",
        "Assigned Room(s):",
        "Additional Guests:",
        "Additional Guests for Your Room(s):",
        "Confirmed Group Members:",
        "Group members:",
        "Current proposed dates:"
    ]

    link_label_lines = [
        "Change Visit:",
        "Cancel Visit:",
        "Cancel Request:",
        "All Reservations:",
        "Request Another Visit:",
        "Request Another Visit:",
        "Request Another Visit:",
        "Change Visit:",
        "Cancel / Cannot Make These Dates:",
        "Open Group:",
        "Your link:",
        "Review:",
        "Visit Details",
        "VISIT DETAILS:",
        "Requested Stay",
        "Additional Notes:",
        "Cancelled Visit Details:",
        "Canceled Visit Details:",
        "Cancellation Details:",
        "Current dates and analysis so far:",
        "Need to make a change?",
        "━━━━━━━━━━━━━━━━━━",
        "Change Request",
        "Cancel Visit",
        "All Reservations",
        "Request a Visit",
        "Request a Visit",
        "Request a Visit"
    ]

    detail_lines = []
    message_lines = []
    action_buttons = []

    lines = body_text.splitlines()
    skip_next_blank_after_link_label = False

    for index, line in enumerate(lines):

        stripped = line.strip()

        if not stripped and skip_next_blank_after_link_label:
            skip_next_blank_after_link_label = False
            continue

        url_match = url_pattern.search(stripped)

        if url_match:

            label_source = ""

            if index > 0:
                label_source += lines[index - 1] + " "

            label_source += stripped

            action_buttons.append({
                "label": action_label_for_url(url_match.group(1), label_source),
                "url": url_match.group(1)
            })

            skip_next_blank_after_link_label = True
            continue

        if any(stripped.startswith(label) for label in detail_labels):
            detail_lines.append(stripped)
            continue

        if stripped.startswith("- "):
            detail_lines.append(stripped[2:].strip())
            continue

        if stripped in link_label_lines or any(stripped.startswith(label) for label in link_label_lines):
            skip_next_blank_after_link_label = True
            continue

        message_lines.append(line)

    # Clean repeated blank lines in message body.
    cleaned_message_lines = []
    previous_blank = False

    for line in message_lines:

        is_blank = not line.strip()

        if is_blank and previous_blank:
            continue

        cleaned_message_lines.append(line)
        previous_blank = is_blank

    message_body = "\n".join(cleaned_message_lines).strip()

    # Tighten common template spacing now that details are displayed in the card.
    message_body = message_body.replace(
        "WE’RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!\n\n",
        "WE’RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!\n"
    )

    message_body = message_body.replace(
        "WE'RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!\n\n",
        "WE'RE EXCITED YOUR VISIT TO STRATHMERE WILL WORK OUT!\n"
    )

    escaped_message_body = html_escape_module.escape(message_body)
    linked_message_body = url_pattern.sub(
        make_inline_link,
        escaped_message_body
    )

    def final_button_label_is_new_request(button):

        return safe_text(button.get("label")).strip().lower() == "open new request"

    # De-duplicate buttons while preserving order.
    seen_urls = set()
    final_buttons = []

    for button in action_buttons:

        button_url = button["url"]
        normalized_button_url = button_url.rstrip("/")

        # V31.10: Do not rewrite invitation links just because their label is
        # "Request a Visit". Invitation/repeat-visit links must stay on
        # /invite/<id> so the guest gets the prefilled invitation request flow.
        # Only true generic root/new-request links should go to /new-request.
        if (
            normalized_button_url == BASE_URL.rstrip("/")
            or normalized_button_url == (BASE_URL.rstrip("/") + "/new-request")
            or final_button_label_is_new_request(button)
        ):
            if "/invite/" not in normalized_button_url:
                button_url = standard_new_request_url()

        if button_url in seen_urls:
            continue

        seen_urls.add(button_url)

        final_button = dict(button)
        final_button["url"] = button_url
        final_buttons.append(final_button)

    details_html = ""

    if detail_lines:

        detail_rows = []

        for detail_line in detail_lines:

            if ":" in detail_line:
                label, value = detail_line.split(":", 1)
                label_html = html_escape_module.escape(label.strip())
                value_html = html_escape_module.escape(value.strip())
            else:
                label_html = ""
                value_html = html_escape_module.escape(detail_line.strip())

            if label_html:
                detail_rows.append(f"""
                    <tr>
                        <td style="padding:6px 10px 6px 0; color:#64748b; font-size:13px; vertical-align:top; width:38%;">
                            {label_html}
                        </td>
                        <td style="padding:6px 0; color:#1f2937; font-size:14px; font-weight:bold; vertical-align:top;">
                            {value_html}
                        </td>
                    </tr>
                """)
            else:
                detail_rows.append(f"""
                    <tr>
                        <td colspan="2" style="padding:6px 0; color:#1f2937; font-size:14px; font-weight:bold;">
                            {value_html}
                        </td>
                    </tr>
                """)

        details_html = f"""
            <div style="background:#f8fbff; border:1px solid #d8e6f3; border-radius:12px; padding:14px 16px; margin:0 0 18px 0;">
                <div style="font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:#0f4c81; font-weight:bold; margin-bottom:6px;">
                    Details
                </div>
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%; border-collapse:collapse;">
                    {''.join(detail_rows)}
                </table>
            </div>
        """

    buttons_html = ""

    if final_buttons:

        button_parts = []

        for button in final_buttons:

            safe_url = html_escape_module.escape(button["url"], quote=True)
            safe_label = html_escape_module.escape(button["label"])

            button_parts.append(f"""
                <a href="{safe_url}" style="
                    display:inline-block;
                    background:#0f4c81;
                    color:#ffffff;
                    text-decoration:none;
                    font-weight:bold;
                    font-size:14px;
                    padding:10px 14px;
                    border-radius:8px;
                    margin:4px 8px 4px 0;
                ">{safe_label}</a>
            """)

        buttons_html = f"""
            <div style="border-top:1px solid #e5e7eb; padding-top:16px; margin-top:18px;">
                <div style="font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:#0f4c81; font-weight:bold; margin-bottom:8px;">
                    Actions
                </div>
                {''.join(button_parts)}
            </div>
        """

    return f"""
    <!doctype html>
    <html>
    <body style="margin:0; padding:0; background-color:#eef4f8; font-family: Arial, Helvetica, sans-serif; color:#1f2937;">
        <div style="max-width:720px; margin:0 auto; padding:22px;">
            <div style="background:#ffffff; border:1px solid #d5e0ea; border-radius:14px; overflow:hidden; box-shadow:0 2px 8px rgba(15,76,129,0.08);">

                {email_header_html}

                <div style="background:#0f4c81; color:white; padding:12px 18px;">
                    <div style="font-size:10px; letter-spacing:.08em; text-transform:uppercase; opacity:.9; margin-bottom:3px;">
                        Shore Home
                    </div>
                    <div style="font-size:16px; font-weight:bold; line-height:1.2;">
                        Strathmere Visit Coordination
                    </div>
                    <div style="font-size:12px; opacity:.92; margin-top:5px; line-height:1.3;">
                        {escaped_subject}
                    </div>
                </div>

                <div style="padding:22px;">
                    {details_html}
                    <div style="font-size:16px; line-height:1.6; white-space:pre-wrap;">{linked_message_body}</div>
                    {buttons_html}
                </div>

                <div style="border-top:1px solid #e5e7eb; background:#f8fafc; padding:14px 22px; font-size:12px; color:#64748b; line-height:1.45;">
                    Questions? Just reply to this email.<br>
                    Shore Home • Strathmere Visit Coordination
                </div>
            </div>
        </div>
    </body>
    </html>
    """


def write_email_audit(to_email, subject, status, detail=""):

    try:
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "email_audit.log"), "a") as handle:
            handle.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{status} | {safe_text(to_email)} | {safe_text(subject)} | {safe_text(detail)}\n"
            )
    except Exception:
        pass


def add_email_header_image_if_available(msg):

    header_filename = "shore_home_header.jpeg"
    header_path = os.path.join(
        app.root_path,
        header_filename
    )

    if not os.path.exists(header_path):
        write_email_audit(
            msg.get("To", ""),
            msg.get("Subject", ""),
            "HEADER_IMAGE_MISSING",
            header_path
        )
        return

    try:
        with open(header_path, "rb") as handle:
            header_bytes = handle.read()

        # The HTML alternative is the last payload after add_alternative().
        html_part = msg.get_payload()[-1]
        html_part.add_related(
            header_bytes,
            maintype="image",
            subtype="jpeg",
            cid="<shore_home_header>",
            filename=header_filename
        )

    except Exception as error:
        write_email_audit(
            msg.get("To", ""),
            msg.get("Subject", ""),
            "HEADER_IMAGE_FAILED",
            error
        )


def send_email(to_email, subject, body, html_body=None):

    if not EMAIL_APP_PASSWORD:
        write_email_audit(to_email, subject, "FAILED", "EMAIL_APP_PASSWORD missing")
        raise RuntimeError(
            "EMAIL_APP_PASSWORD environment variable is not configured."
        )

    msg = EmailMessage()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    if HTML_EMAILS_ENABLED:

        try:
            if html_body is None:
                html_body = plain_text_to_html_email(
                    subject,
                    body
                )

            msg.add_alternative(
                html_body,
                subtype="html"
            )

            add_email_header_image_if_available(msg)

        except Exception as error:
            # V32.7: HTML decoration, header image, or visit-summary formatting
            # must not prevent the plain-text confirmation email from sending.
            write_email_audit(to_email, subject, "HTML_SKIPPED", error)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        write_email_audit(to_email, subject, "SENT")
    except Exception as error:
        write_email_audit(to_email, subject, "FAILED", error)
        raise



def notify_admin(action_title, details, review_path="/dashboard"):

    if not ADMIN_NOTIFICATIONS_ENABLED:
        return

    admin_email = safe_text(ADMIN_NOTIFICATION_EMAIL).strip()

    if not is_valid_email_address(admin_email):
        return

    review_url = review_path

    if review_path.startswith("/"):
        review_url = BASE_URL + review_path

    body = render_email_template(
        "admin_notification.txt",
        action_title=safe_text(action_title),
        details=safe_text(details),
        review_url=review_url
    )

    try:
        send_email(
            admin_email,
            f"Strathmere Visit Request System: {safe_text(action_title)}",
            body
        )
    except Exception as error:
        print("ADMIN NOTIFICATION FAILED:", safe_text(error))


def get_coordination_organizer_info(conn, group_id):

    organizer = conn.execute("""
        SELECT
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
          AND LOWER(COALESCE(coordination_group_members.role, '')) = 'organizer'
        ORDER BY coordination_group_members.id
        LIMIT 1
    """, (
        group_id,
    )).fetchone()

    if organizer:
        return {
            "name": safe_text(organizer["primary_name"]),
            "email": safe_text(organizer["primary_email"])
        }

    return {
        "name": "John and Mark",
        "email": ""
    }


def notify_admin_coordination_response(conn, group_id, guest_name, action_title="Coordination dates submitted"):

    group = conn.execute("""
        SELECT title
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    total_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM coordination_group_members
        WHERE coordination_group_id = ?
    """, (
        group_id,
    )).fetchone()["count"]

    responded_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM coordination_group_members
        WHERE coordination_group_id = ?
          AND invitation_status = 'responded'
    """, (
        group_id,
    )).fetchone()["count"]

    group_title = safe_text(group["title"] if group else f"Group {group_id}")

    notify_admin(
        action_title,
        f"Group: {group_title}\nGuest: {safe_text(guest_name)}\nResponses: {responded_count} of {total_count}",
        f"/coordination-group/{group_id}"
    )

    if total_count > 0 and responded_count >= total_count:
        notify_admin(
            "All coordination guests responded",
            f"Group: {group_title}\nResponses: {responded_count} of {total_count}\nReady to review date matches.",
            f"/coordination-group/{group_id}"
        )

def format_date(date_string):
    try:
        return datetime.strptime(date_string, "%Y-%m-%d").strftime("%B %d, %Y")
    except:
        return date_string


def parse_iso_date_safe(date_string):

    date_string = safe_text(date_string).strip()

    if not date_string:
        return None

    try:
        return datetime.strptime(date_string, "%Y-%m-%d").date()
    except Exception:
        return None


def valid_date_range(start_date, end_date):

    start = parse_iso_date_safe(start_date)
    end = parse_iso_date_safe(end_date)

    if not start or not end:
        return False

    return end >= start


def format_datetime_display(value):

    value = safe_text(value).strip()

    if not value:
        return ""

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):

        try:
            parsed = datetime.strptime(value[:19], pattern)
            return parsed.strftime("%b %d, %Y • %I:%M %p")
        except Exception:
            pass

    return value

@app.route("/test-email", methods=["GET", "POST"])
def test_email():

    if request.method != "POST":
        return action_confirmation_page(
            "Send Test Email",
            "Send one test email to the configured Shore Home email address.",
            "/test-email",
            "/dashboard"
        )

    send_email(
        EMAIL_ADDRESS,
        "Test email from Shore Home App",
        "This is a test email from the Shore Home App."
    )

    return """
    <h2>Test email sent.</h2>
    <p><a href="/dashboard">Back to Dashboard</a></p>
    """



@app.route("/shore_home_header.jpeg")
def shore_home_header_image():

    header_filename = "shore_home_header.jpeg"
    header_path = os.path.join(
        app.root_path,
        header_filename
    )

    if not os.path.exists(header_path):
        return (
            "Header image not found. Confirm shore_home_header.jpeg exists in the GitHub repo root and redeploy.",
            404
        )

    return send_from_directory(
        app.root_path,
        header_filename,
        mimetype="image/jpeg",
        max_age=3600
    )



def csrf_token():

    token = session.get("csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    return token


def csrf_input():

    return f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'


def is_public_guest_endpoint(endpoint):

    return endpoint in PUBLIC_ENDPOINTS or endpoint.startswith("static")


def csrf_exempt_endpoint(endpoint):

    return endpoint in {
        "admin_login",
        "shore_home_header_image",
        "static"
    } or endpoint in PUBLIC_ENDPOINTS


def inject_csrf_tokens(html_text):

    if not html_text or "<form" not in html_text:
        return html_text

    token_html = csrf_input()

    def add_token(match):
        form_tag = match.group(0)
        lower_tag = form_tag.lower()

        if 'method="post"' not in lower_tag and "method='post'" not in lower_tag:
            return form_tag

        return form_tag + "\n        " + token_html

    return re.sub(
        r'<form\b[^>]*>',
        add_token,
        html_text,
        flags=re.IGNORECASE
    )


@app.after_request
def add_csrf_to_admin_forms(response):

    try:
        endpoint = request.endpoint or ""

        if (
            admin_is_logged_in()
            and not is_public_guest_endpoint(endpoint)
            and response.content_type
            and response.content_type.startswith("text/html")
        ):
            body = response.get_data(as_text=True)
            new_body = inject_csrf_tokens(body)

            if new_body != body:
                response.set_data(new_body)
                response.headers["Content-Length"] = str(len(response.get_data()))

    except Exception as error:
        try:
            error_logger.warning("CSRF injection skipped: %s", error)
        except Exception:
            pass

    return response


@app.after_request
def add_security_headers(response):

    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff"
    )

    response.headers.setdefault(
        "X-Frame-Options",
        "SAMEORIGIN"
    )

    response.headers.setdefault(
        "Referrer-Policy",
        "same-origin"
    )

    return response


PUBLIC_ENDPOINTS = {
    "admin_login",
    "shore_home_header_image",
    "static",
    "invite_request",
    "invitation_request_alias",
    "invitation_request",
    "guest_invitation_request",
    "request_form",
    "home",
    "public_request",
    "guest_request",
    "submit",
    "request_submitted_review",
    "request_submitted_complete",
    "all_reservations",
    "change_request",
    "change_request_bad_link",
    "cancel_request",
    "coordination_group_member_request",
    "coordination_group_member_organizer_planning",
    "coordination_group_member_date_options",
    "coordination_group_member_date_options_thanks",
    "coordination_group_member_cannot_change_dates",
    "coordination_group_member_clear_date_options",
    "coordination_group_member_follow_up_dates_work",
    "coordination_group_member_tentative_response",
    "coordination_group_member_tentative_response_thanks"
}


def admin_is_logged_in():

    return session.get("admin_logged_in") == True


@app.before_request
def require_admin_login():

    endpoint = request.endpoint or ""

    if endpoint in PUBLIC_ENDPOINTS:
        return None

    if endpoint.startswith("static"):
        return None

    if not ADMIN_AUTH_ENABLED:
        if PRODUCTION_MODE:
            return "Admin authentication is not configured.", 503
        return None

    if admin_is_logged_in():

        if request.method == "POST" and not csrf_exempt_endpoint(endpoint):
            submitted_token = safe_text(request.form.get("csrf_token")).strip()
            header_token = safe_text(request.headers.get("X-CSRF-Token")).strip()
            session_token = safe_text(session.get("csrf_token")).strip()

            if not session_token or not (
                hmac.compare_digest(submitted_token, session_token)
                or hmac.compare_digest(header_token, session_token)
            ):
                return """
                <h1>Security Check Failed</h1>
                <p>This action was not completed because the page security token was missing or expired.</p>
                <p>Please go back, refresh the page, and try the action again.</p>
                """, 400

        return None

    return redirect(
        "/admin-login?next=" + safe_text(request.path)
    )


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():

    error_message = ""

    if request.method == "POST":

        username = safe_text(request.form.get("username")).strip()
        password = safe_text(request.form.get("password")).strip()

        username_ok = hmac.compare_digest(
            username,
            safe_text(ADMIN_USERNAME)
        )

        password_ok = hmac.compare_digest(
            password,
            safe_text(ADMIN_PASSWORD)
        )

        if username_ok and password_ok:

            session["admin_logged_in"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)

            next_path = safe_text(request.args.get("next")).strip()

            if not next_path.startswith("/"):
                next_path = "/dashboard"

            return redirect(next_path)

        error_message = "Invalid login."

    return f"""
    <h1>Shore Home Admin Login</h1>

    <form method="POST">
        <p>
            <label>
                Username<br>
                <input type="text" name="username" autocomplete="username">
            </label>
        </p>

        <p>
            <label>
                Password<br>
                <input type="password" name="password" autocomplete="current-password">
            </label>
        </p>

        <p style="color: red; font-weight: bold;">
            {safe_text(error_message)}
        </p>

        <button type="submit">
            Login
        </button>
    </form>
    """


@app.route("/admin-logout")
def admin_logout():

    session.clear()

    return redirect("/admin-login")


def compact_admin_table_css():

    return """
    <style>
        /* V32.1: tighter admin tables so columns do not overflow. */
        .shore-admin-nav + br + small + hr + table,
        table {
            max-width: 100%;
            table-layout: fixed;
        }

        th, td {
            padding: 4px 6px !important;
            font-size: 12px;
            line-height: 1.25;
            vertical-align: top;
            overflow-wrap: anywhere;
            word-break: break-word;
        }

        th {
            white-space: normal;
        }

        td a, th a {
            overflow-wrap: anywhere;
            word-break: break-word;
        }

        td form, td button, td input, td select {
            max-width: 100%;
            box-sizing: border-box;
        }

        .admin-action-cell,
        td:last-child {
            width: auto;
        }

        @media (max-width: 760px) {
            th, td {
                padding: 3px 4px !important;
                font-size: 11px;
            }
        }
    </style>
    """


def nav_links():

    guest_path = safe_text(request.path)

    guest_page_prefixes = (
        "/new-request",
        "/invite/",
        "/invitation/",
        "/request-submitted",
        "/coordination-group-member/"
    )

    if any(guest_path.startswith(prefix) for prefix in guest_page_prefixes):
        return ""

    if guest_path.startswith("/request/") and (
        "/submitted" in guest_path
        or "/change" in guest_path
        or "/cancel" in guest_path
        or "/all-reservations" in guest_path
    ):
        return ""

    return f"""
    {compact_admin_table_css()}
    <div class="shore-admin-nav" style="font-size: 14px; line-height: 1.8;">
        <strong>Workflow:</strong>
        <a href="/dashboard">Dashboard</a> |
        <a href="/invitations">Invitations</a> |
        <a href="/requests">Request Review</a> |
        <a href="/room-assignments">Room Assignments</a> |
        <a href="/bookings">Confirmed Stays</a> |
        <a href="/profiles">Guest Profiles</a>
        <br>
        <strong>Coordination:</strong>
        <a href="/coordination-groups">All Coordination Groups</a> |
        <a href="/coordination-group/new">Create New Group</a> |
        <span style="color: #666;">Planning / Booking Handoff opens from each group</span>
        <br>
        <strong>Admin Tools:</strong>
        <a href="/booking-audit">Booking Audit</a> |
        <a href="/status-sanity">Status Sanity</a> |
        <a href="/activity-log">Activity Log</a> |
        <a href="/blocked">House Blocks</a> |
        <a href="/manual-request">Manual Request</a> |
        <a href="/admin-backup">Backup & Recovery</a> |
        <a href="/production-health">Production Health</a> |
        <a href="/production-check">Production Check</a> |
        <a href="/booking-consistency-repair">Booking Consistency Repair</a> |
        <a href="/admin-reset-test-data">Reset Test Data</a> |\n        <a href="/admin-logout">Logout</a>
    </div>
    <br>
    <small style="color: gray;">Version: {APP_VERSION}</small>
    <hr>
    """



def action_confirmation_page(title, message, post_action, back_link):

    return f"""
    {nav_links()}

    <h1>{safe_text(title)}</h1>

    <div style="
        background-color: #fff3cd;
        border: 2px solid #fd7e14;
        padding: 14px;
        border-radius: 8px;
        max-width: 760px;
        margin-bottom: 10px;
    ">
        <p style="font-weight: bold; margin-top: 0;">
            Please confirm this admin action before continuing.
        </p>

        <p>
            {safe_text(message)}
        </p>
    </div>

    <form method="POST" action="{post_action}">
        <input type="hidden" name="confirm_action" value="yes">

        <button type="submit" style="
            background-color: #dc3545;
            color: white;
            border: none;
            padding: 8px 12px;
            border-radius: 5px;
            font-weight: bold;
        ">
            Yes, Continue
        </button>

        &nbsp;

        <a href="{back_link}">
            Cancel / Go Back
        </a>
    </form>
    """


def production_status_row(label, ok, detail):

    if ok:
        status = "OK"
        background = "#e8f7ea"
        color = "#198754"
    else:
        status = "Needs Attention"
        background = "#fff3cd"
        color = "#856404"

    return f"""
    <tr style="background-color: {background};">
        <td><strong>{safe_text(label)}</strong></td>
        <td style="color: {color}; font-weight: bold;">{status}</td>
        <td>{safe_text(detail)}</td>
    </tr>
    """

def request_status_display(status):

    if status == "pending":

        return """
        <strong style='color: orange;'>
            Pending Review
        </strong>
        """

    elif status == "approved":

        return """
        <strong style='color: green;'>
            Approved
        </strong>
        """

    elif status == "declined":

        return """
        <strong style='color: red;'>
            Declined
        </strong>
        """

    else:
        return status


def email_status_display(email_status, email_needed_type, request_id=None):

    if not email_status:
        email_status = "not_needed"

    if email_status == "needs_email":

        if email_needed_type == "approval":

            return f"""
            <a href="/request/{request_id}/email-preview"
               style="
                   color: red;
                   font-weight: bold;
                   text-decoration: none;
               ">
                Send Approval Email
            </a>
            """

        elif email_needed_type == "decline":

            return f"""
            <a href="/request/{request_id}/email-preview"
               style="
                   color: red;
                   font-weight: bold;
                   text-decoration: none;
               ">
                Send Decline Email
            </a>
            """

        elif email_needed_type == "cancellation":

            return f"""
            <a href="/request/{request_id}/email-preview"
               style="
                   color: red;
                   font-weight: bold;
                   text-decoration: none;
               ">
                Send Cancellation Email
            </a>
            """

        else:

            if request_id:

                return f"""
                <a href="/request/{request_id}/email-preview"
                   style="
                       color: red;
                       font-weight: bold;
                       text-decoration: none;
                   ">
                    Email Needed
                </a>
                """

            return """
            <strong style='color: red;'>
                Email Needed
            </strong>
            """

    elif email_status == "needs_update":

        return f"""
        <a href="/request/{request_id}/email-preview"
           style="
               color: red;
               font-weight: bold;
               text-decoration: none;
           ">
            Send Update Email
        </a>
        """

    elif email_status == "sent":

        return """
        <strong style='color: green;'>
            Sent
        </strong>
        """

    else:

        return """
        <span style='color: gray;'>
            Not Needed
        </span>
        """


def safe_text(value):

    if value is None:
        return ""

    return str(value)


def display_room_name(value):

    value_text = safe_text(value)

    if value_text.strip().lower() == "twin room":
        return "Twin/King Room"

    if value_text.strip().lower() == "twin":
        return "Twin/King"

    return value_text


def row_value(row, *keys):

    for key in keys:

        try:
            value = row[key]
        except Exception:
            value = None

        if value is not None:
            return value

    return ""


def ensure_guest_profile_welcome_column(conn):

    try:
        columns = set(
            row["name"]
            for row in conn.execute("PRAGMA table_info(guest_profiles)").fetchall()
        )

        if "welcome_email_sent_at" not in columns:
            conn.execute("""
                ALTER TABLE guest_profiles
                ADD COLUMN welcome_email_sent_at TEXT
            """)

    except Exception:
        # Existing profile behavior should not fail because this optional
        # tracking column could not be added.
        pass


def profile_welcome_status_text(profile):

    sent_at = safe_text(row_value(profile, "welcome_email_sent_at")).strip()

    if sent_at:
        return "Sent " + sent_at[:16]

    return "Not sent"


def send_profile_welcome_email(conn, profile_id, force=False):

    ensure_guest_profile_welcome_column(conn)

    profile = conn.execute("""
        SELECT *
        FROM guest_profiles
        WHERE id = ?
    """, (
        profile_id,
    )).fetchone()

    if not profile:
        raise RuntimeError("Guest profile not found.")

    if not force and safe_text(row_value(profile, "welcome_email_sent_at")).strip():
        return "already_sent"

    recipient = safe_text(profile["primary_email"]).strip()
    guest_name = safe_text(profile["primary_name"]).strip()

    body = render_email_template(
        "profile_welcome.txt",
        guest_name=guest_name
    )

    subject = "Welcome to the Strathmere Visit Request System"

    send_email(
        recipient,
        subject,
        body
    )

    conn.execute("""
        UPDATE guest_profiles
        SET welcome_email_sent_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        profile_id,
    ))

    try:
        conn.execute("""
            INSERT INTO email_log
            (request_id, email_type, recipient, subject, body)
            VALUES (?, ?, ?, ?, ?)
        """, (
            None,
            "profile_welcome",
            recipient,
            subject,
            body
        ))
    except Exception:
        pass

    return "sent"


def coordination_member_row_id(row):

    return row_value(row, "member_id", "id", "coordination_group_member_id")


def clean_text(value):

    return safe_text(value).strip()


def is_valid_email_address(value):

    value = clean_text(value)

    if not value:
        return False

    if "@" not in value:
        return False

    if "." not in value.split("@")[-1]:
        return False

    if " " in value:
        return False

    return True


def guest_profile_validation_error(primary_name, primary_email):

    if not clean_text(primary_name):
        return "Guest profile name is required."

    if not is_valid_email_address(primary_email):
        return "Guest profile email address is required and must be valid."

    return ""


def profile_error_page(message, back_link="/profiles"):

    return f"""
    {nav_links()}

    <h1>Guest Profile Not Saved</h1>

    <p style="
        color: red;
        font-weight: bold;
    ">
        {message}
    </p>

    <p>
        Guest profiles must have both a name and a valid email address
        before they can be used for invitations or request emails.
    </p>

    <p>
        <a href="{back_link}">
            Back
        </a>
    </p>
    """



def request_identity_validation_error(name, email):

    if not clean_text(name):
        return "Guest name is required."

    if not is_valid_email_address(email):
        return "Guest email address is required and must be valid."

    return ""


def request_identity_error_page(message, back_link="javascript:history.back()"):

    return f"""
    {nav_links()}

    <h1>Request Not Saved</h1>

    <p style="
        color: red;
        font-weight: bold;
    ">
        {message}
    </p>

    <p>
        Every new request, change request, and cancellation request
        must have a guest name and valid guest email address.
    </p>

    <p>
        <a href="{back_link}">
            Back
        </a>
    </p>
    """


def timestamped_comment_block(title, body):

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return f"""

----- {title} -----
Timestamp:
{created_at}

{body}
"""


def display_comments_sorted(comments):

    comments = safe_text(comments).strip()

    if not comments:
        return ""

    sections = []
    current_section = []

    for line in comments.splitlines():

        if line.startswith("----- ") and current_section:

            sections.append(
                "\n".join(current_section).strip()
            )

            current_section = []

        current_section.append(line)

    if current_section:

        sections.append(
            "\n".join(current_section).strip()
        )

    def section_sort_key(section):

        lines = section.splitlines()

        for index, line in enumerate(lines):

            if line.strip() == "Timestamp:" and index + 1 < len(lines):

                return lines[index + 1].strip()

        return ""

    sections = sorted(
        sections,
        key=section_sort_key,
        reverse=True
    )

    if not sections:
        return comments

    return "\n\n".join(sections)


def latest_timestamped_section(comments, title):

    marker = f"----- {title} -----"

    comments = safe_text(comments)

    if marker not in comments:
        return ""

    return comments.split(marker)[-1].strip()


def value_after_label(section, label):

    lines = section.splitlines()

    for index, line in enumerate(lines):

        if line.strip() == label:

            next_index = index + 1

            while next_index < len(lines):

                value = lines[next_index].strip()

                if value:
                    return value

                next_index += 1

    return ""


def latest_change_values(comments):

    section = latest_timestamped_section(
        comments,
        "Change Request"
    )

    return {
        "section": section,
        "timestamp": value_after_label(section, "Timestamp:"),
        "original_arrival": value_after_label(section, "Original Arrival:"),
        "original_departure": value_after_label(section, "Original Departure:"),
        "original_rooms": value_after_label(section, "Original Rooms Requested:"),
        "new_arrival": value_after_label(section, "Requested New Arrival:"),
        "new_departure": value_after_label(section, "Requested New Departure:"),
        "new_rooms": value_after_label(section, "Requested Rooms:"),
        "notes": value_after_label(section, "Change Notes:")
    }


def display_change_requested_date(timestamp):

    timestamp = safe_text(timestamp).strip()

    if not timestamp:
        return ""

    try:
        return datetime.strptime(
            timestamp[:10],
            "%Y-%m-%d"
        ).strftime("%B %d, %Y")
    except:
        return timestamp[:10]




def build_coordination_intersection_suggestions(date_options, approved_bookings, blocked_ranges, total_rooms):
    """Phase 3: find true shared overlap windows across one option per responding member.

    This is intentionally separate from the older ranked match engine. It answers the
    core question: is there a common window where every responding guest can attend?
    It uses the intersection rule: latest available arrival to earliest available departure.
    """

    options_by_member = {}
    member_names = {}

    for option in date_options:

        member_id = row_value(option, "member_id", "coordination_group_member_id")

        if not member_id:
            continue

        try:
            arrival = datetime.strptime(option["arrival_date"], "%Y-%m-%d").date()
            departure = datetime.strptime(option["departure_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        if departure <= arrival:
            continue

        try:
            flexibility_days = int(option["flexibility_days"] or 0)
        except Exception:
            flexibility_days = 0

        if flexibility_days < 0:
            flexibility_days = 0

        available_start = arrival - timedelta(days=flexibility_days)
        available_end = departure + timedelta(days=flexibility_days)

        options_by_member.setdefault(member_id, []).append({
            "member_id": member_id,
            "primary_name": safe_text(option["primary_name"]),
            "role": safe_text(row_value(option, "role")),
            "priority": safe_text(option["priority"]),
            "arrival": arrival,
            "departure": departure,
            "available_start": available_start,
            "available_end": available_end,
            "flexibility_days": flexibility_days,
            "rooms_requested": normalize_rooms_requested(option["rooms_requested"], total_rooms),
        })

        member_names[member_id] = safe_text(option["primary_name"])

    if not options_by_member:
        return []

    # Limit combinations defensively so a malformed test group cannot blow up the page.
    member_ids = list(options_by_member.keys())
    combinations = [[]]

    for member_id in member_ids:
        next_combinations = []
        for existing in combinations:
            for option in options_by_member[member_id][:6]:
                next_combinations.append(existing + [option])
                if len(next_combinations) > 500:
                    break
            if len(next_combinations) > 500:
                break
        combinations = next_combinations[:500]

    blocked_dates = set()

    for block in blocked_ranges:
        try:
            current = datetime.strptime(block["start_date"], "%Y-%m-%d").date()
            end = datetime.strptime(block["end_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        # Existing full-house blocks still remove the date entirely. Partial-room
        # blocks are handled by the capacity layer elsewhere and should not make
        # the intersection invalid by themselves.
        block_type = safe_text(row_value(block, "block_type", "type", "is_full_block")).lower()
        blocked_rooms = safe_text(row_value(block, "blocked_rooms", "rooms_blocked")).strip()
        is_partial = False
        try:
            if blocked_rooms and int(blocked_rooms) > 0 and int(blocked_rooms) < int(total_rooms):
                is_partial = True
        except Exception:
            pass
        if "partial" in block_type:
            is_partial = True
        if block_type in ("0", "false", "partial"):
            is_partial = True

        if is_partial:
            continue

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    approved_by_date = {}

    for booking in approved_bookings:
        try:
            current = datetime.strptime(booking["arrival_date"], "%Y-%m-%d").date()
            end = datetime.strptime(booking["departure_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        rooms_to_count = 1
        try:
            rooms_to_count = int(row_value(booking, "rooms_held", "rooms_requested") or 1)
        except Exception:
            rooms_to_count = 1

        while current < end:
            date_string = current.strftime("%Y-%m-%d")
            approved_by_date[date_string] = approved_by_date.get(date_string, 0) + rooms_to_count
            current += timedelta(days=1)

    suggestions = []
    seen = set()

    for combination in combinations:

        if len(combination) != len(member_ids):
            continue

        overlap_start = max(item["available_start"] for item in combination)
        overlap_end = min(item["available_end"] for item in combination)

        if overlap_end <= overlap_start:
            continue

        key = (overlap_start.strftime("%Y-%m-%d"), overlap_end.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        seen.add(key)

        rooms_needed = sum(item["rooms_requested"] for item in combination)
        min_rooms_open = total_rooms
        capacity_ok = True
        capacity_notes = []
        current = overlap_start

        while current < overlap_end:
            date_string = current.strftime("%Y-%m-%d")

            if date_string in blocked_dates:
                capacity_ok = False
                capacity_notes.append(f"{format_date(date_string)} is fully blocked")

            rooms_open = total_rooms - approved_by_date.get(date_string, 0)
            if rooms_open < min_rooms_open:
                min_rooms_open = rooms_open

            if rooms_open < rooms_needed:
                capacity_ok = False
                capacity_notes.append(f"{format_date(date_string)} has only {rooms_open} room(s) open")

            current += timedelta(days=1)

        preferred_count = sum(1 for item in combination if item["priority"] == "preferred")
        alternate_count = sum(1 for item in combination if item["priority"] == "alternate")
        flexibility_used = []
        changed_range_names = []

        for item in combination:
            if overlap_start != item["arrival"] or overlap_end != item["departure"]:
                changed_range_names.append(item["primary_name"])
            if overlap_start < item["arrival"] or overlap_end > item["departure"]:
                flexibility_used.append(item["primary_name"])

        suggestions.append({
            "arrival_date": overlap_start.strftime("%Y-%m-%d"),
            "departure_date": overlap_end.strftime("%Y-%m-%d"),
            "nights": (overlap_end - overlap_start).days,
            "matched_count": len(combination),
            "total_member_count": len(member_ids),
            "rooms_needed": rooms_needed,
            "rooms_available": total_rooms,
            "min_rooms_open": min_rooms_open,
            "capacity_ok": capacity_ok,
            "capacity_notes": capacity_notes,
            "preferred_count": preferred_count,
            "alternate_count": alternate_count,
            "guest_names": sorted(item["primary_name"] for item in combination),
            "changed_range_names": sorted(set(changed_range_names)),
            "flexibility_used_names": sorted(set(flexibility_used)),
            "score": (100000 if capacity_ok else 0) + (overlap_end - overlap_start).days * 100 + preferred_count * 10 - rooms_needed,
        })

    return sorted(suggestions, key=lambda item: item["score"], reverse=True)[:5]

def build_coordination_match_suggestions(date_options, approved_bookings, blocked_ranges, total_rooms):

    if not date_options:
        return []

    # V31.8: House blocks may be full blocks or partial room-capacity limits.
    # Full blocks make dates unavailable; partial blocks only reduce room capacity.
    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked_ranges,
        total_rooms
    )

    approved_by_date = {}

    for booking in approved_bookings:

        try:
            current = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

        except:
            continue

        while current < end:

            date_string = current.strftime("%Y-%m-%d")

            if date_string not in approved_by_date:
                approved_by_date[date_string] = 0

            approved_by_date[date_string] += 1

            current += timedelta(days=1)

    expanded_options = []
    boundary_dates = set()
    all_member_names = set()

    for option in date_options:

        try:
            arrival = datetime.strptime(
                option["arrival_date"],
                "%Y-%m-%d"
            ).date()

            departure = datetime.strptime(
                option["departure_date"],
                "%Y-%m-%d"
            ).date()

        except:
            continue

        if departure <= arrival:
            continue

        try:
            flexibility_days = int(option["flexibility_days"] or 0)
        except:
            flexibility_days = 0

        if flexibility_days < 0:
            flexibility_days = 0

        available_start = arrival - timedelta(days=flexibility_days)
        available_end = departure + timedelta(days=flexibility_days)

        member_name = safe_text(option["primary_name"])
        all_member_names.add(member_name)

        expanded_option = {
            "member_id": option["member_id"],
            "primary_name": member_name,
            "priority": safe_text(option["priority"]),
            "arrival": arrival,
            "departure": departure,
            "available_start": available_start,
            "available_end": available_end,
            "flexibility_days": flexibility_days,
            "rooms_requested": normalize_rooms_requested(
                option["rooms_requested"],
                total_rooms
            )
        }

        expanded_options.append(expanded_option)

        boundary_dates.add(available_start)
        boundary_dates.add(available_end)
        boundary_dates.add(arrival)
        boundary_dates.add(departure)

        # Add nearby dates so the best overlap is not limited only to hard boundaries.
        boundary_dates.add(available_start + timedelta(days=1))
        boundary_dates.add(available_end - timedelta(days=1))
        boundary_dates.add(arrival + timedelta(days=1))
        boundary_dates.add(departure - timedelta(days=1))

    if not expanded_options:
        return []

    candidate_dates = sorted(
        date_value
        for date_value in boundary_dates
        if date_value is not None
    )

    suggestions = []
    seen_windows = set()
    total_member_count = len(all_member_names)

    for start in candidate_dates:

        for end in candidate_dates:

            if end <= start:
                continue

            nights = (end - start).days

            if nights < 1:
                continue

            window_key = (
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d")
            )

            if window_key in seen_windows:
                continue

            seen_windows.add(window_key)

            best_by_member = {}

            for option in expanded_options:

                if option["available_start"] <= start and option["available_end"] >= end:

                    member_id = option["member_id"]

                    option_score = 2

                    if option["priority"] == "preferred":
                        option_score = 1

                    current_best = best_by_member.get(member_id)

                    if not current_best or option_score < current_best["option_score"]:

                        option_copy = dict(option)
                        option_copy["option_score"] = option_score

                        best_by_member[member_id] = option_copy

            matched_options = list(best_by_member.values())

            if not matched_options:
                continue

            rooms_needed = 0
            preferred_count = 0
            alternate_count = 0
            guest_names = []
            alternate_names = []
            flexibility_penalty = 0
            flexibility_used_names = []

            for option in matched_options:

                rooms_needed += option["rooms_requested"]
                guest_names.append(option["primary_name"])

                if option["priority"] == "preferred":
                    preferred_count += 1
                else:
                    alternate_count += 1
                    alternate_names.append(option["primary_name"])

                option_penalty = 0

                if start < option["arrival"]:
                    option_penalty += (option["arrival"] - start).days

                if end > option["departure"]:
                    option_penalty += (end - option["departure"]).days

                if option_penalty > 0:
                    flexibility_used_names.append(option["primary_name"])

                flexibility_penalty += option_penalty

            capacity_ok = True
            capacity_notes = []
            min_rooms_open = total_rooms
            current = start

            while current < end:

                date_string = current.strftime("%Y-%m-%d")

                if date_string in blocked_dates:
                    capacity_ok = False
                    capacity_notes.append(
                        f"{format_date(date_string)} is blocked"
                    )

                capacity_limit = room_capacity_limit_for_date(
                    room_limit_by_date,
                    date_string,
                    total_rooms
                )

                rooms_open = capacity_limit - approved_by_date.get(date_string, 0)

                if rooms_open < min_rooms_open:
                    min_rooms_open = rooms_open

                if rooms_open < rooms_needed:
                    capacity_ok = False
                    capacity_notes.append(
                        f"{format_date(date_string)} has only {rooms_open} room(s) open"
                    )

                current += timedelta(days=1)

            unmatched_names = sorted(
                member_name
                for member_name in all_member_names
                if member_name not in guest_names
            )

            nearby_before_names = []
            nearby_after_names = []

            before_date = start - timedelta(days=1)
            after_date = end

            for option in expanded_options:

                if option["available_start"] <= before_date <= option["available_end"]:
                    nearby_before_names.append(option["primary_name"])

                if option["available_start"] <= after_date <= option["available_end"]:
                    nearby_after_names.append(option["primary_name"])

            matched_count = len(matched_options)
            all_guests_match = total_member_count > 0 and matched_count == total_member_count

            # Human-friendly score. The score is not shown to guests; it drives ranking only.
            score = 0
            score += matched_count * 10000
            score += preferred_count * 700
            score += alternate_count * 250
            score += nights * 80

            if all_guests_match:
                score += 2500

            if capacity_ok:
                score += 1500
            else:
                score -= 3000

            score -= flexibility_penalty * 120
            score -= rooms_needed * 10

            why_bullets = []

            why_bullets.append(
                f"{matched_count} of {total_member_count} guest(s) can attend"
            )

            why_bullets.append(
                f"{rooms_needed} room(s) needed / {total_rooms} room(s) available"
            )

            if preferred_count > 0:
                why_bullets.append(
                    f"Uses {preferred_count} preferred date choice(s)"
                )

            if alternate_count > 0:
                why_bullets.append(
                    f"Uses {alternate_count} alternate date choice(s)"
                )

            if flexibility_penalty > 0:
                why_bullets.append(
                    f"Uses {flexibility_penalty} day(s) of flexibility"
                )
            else:
                why_bullets.append(
                    "Fits without using extra flexibility"
                )

            if unmatched_names:
                why_bullets.append(
                    "Needs follow-up with: " + ", ".join(unmatched_names)
                )

            if capacity_ok:
                why_bullets.append(
                    "No capacity conflict found"
                )
            else:
                why_bullets.append(
                    "Capacity needs review"
                )

            match_quality_label = "Best practical option"

            if all_guests_match and capacity_ok:
                match_quality_label = "Best group option"
            elif matched_count < total_member_count:
                match_quality_label = "Partial group option"
            elif not capacity_ok:
                match_quality_label = "Capacity issue"

            suggestions.append({
                "arrival_date": start.strftime("%Y-%m-%d"),
                "departure_date": end.strftime("%Y-%m-%d"),
                "nights": nights,
                "matched_count": matched_count,
                "total_member_count": total_member_count,
                "rooms_needed": rooms_needed,
                "rooms_available": total_rooms,
                "preferred_count": preferred_count,
                "alternate_count": alternate_count,
                "flexibility_penalty": flexibility_penalty,
                "flexibility_used_names": sorted(set(flexibility_used_names)),
                "guest_names": sorted(guest_names),
                "alternate_names": sorted(alternate_names),
                "unmatched_names": unmatched_names,
                "capacity_ok": capacity_ok,
                "capacity_notes": capacity_notes,
                "min_rooms_open": min_rooms_open,
                "nearby_before_date": before_date.strftime("%Y-%m-%d"),
                "nearby_before_names": sorted(set(nearby_before_names)),
                "nearby_after_date": after_date.strftime("%Y-%m-%d"),
                "nearby_after_names": sorted(set(nearby_after_names)),
                "score": score,
                "match_quality_label": match_quality_label,
                "why_bullets": why_bullets
            })

    suggestions = sorted(
        suggestions,
        key=lambda suggestion: suggestion["score"],
        reverse=True
    )

    final_suggestions = []
    seen = set()

    for suggestion in suggestions:

        key = (
            suggestion["arrival_date"],
            suggestion["departure_date"],
            tuple(suggestion["guest_names"])
        )

        if key in seen:
            continue

        seen.add(key)
        final_suggestions.append(suggestion)

        if len(final_suggestions) >= 6:
            break

    return final_suggestions


def tentative_response_display(status):

    status = safe_text(status).strip()

    if status == "confirmed":
        return "These Dates Work For Me"

    if status == "cannot_make":
        return "These Dates Do Not Work"

    if status == "needs_discussion":
        return "Need Different Dates"

    return "No Response Yet"


def tentative_response_color(status):

    status = safe_text(status).strip()

    if status == "confirmed":
        return "#e8f7ea"

    if status == "cannot_make":
        return "#ffe5e5"

    if status == "needs_discussion":
        return "#fff3cd"

    return "#f8f9fa"


def coordination_role_display(role):

    role = safe_text(role).strip().lower()

    if role == "organizer":
        return "Organizer"

    if role in ("participant", "guest", "member", ""):
        return "Participant"

    if role == "admin":
        return "Admin"

    return safe_text(role).title()


def coordination_role_badge(role):

    label = coordination_role_display(role)
    role_key = safe_text(role).strip().lower()

    if role_key == "organizer":
        background = "#e7f1ff"
        border = "#0d6efd"
        color = "#084298"
    elif role_key == "admin":
        background = "#fff3cd"
        border = "#ffc107"
        color = "#664d03"
    else:
        background = "#f8f9fa"
        border = "#ced4da"
        color = "#495057"

    return f"""
    <span style="
        display:inline-block;
        background:{background};
        border:1px solid {border};
        color:{color};
        border-radius:999px;
        padding:2px 8px;
        font-size:12px;
        font-weight:bold;
        white-space:nowrap;
    ">{safe_text(label)}</span>
    """


def date_range_nights(arrival_date, departure_date):

    try:
        return (
            datetime.strptime(departure_date, "%Y-%m-%d")
            - datetime.strptime(arrival_date, "%Y-%m-%d")
        ).days
    except:
        return 0


def coordination_option_fits_window(option, arrival_date, departure_date):

    try:
        tentative_arrival = datetime.strptime(
            arrival_date,
            "%Y-%m-%d"
        ).date()

        tentative_departure = datetime.strptime(
            departure_date,
            "%Y-%m-%d"
        ).date()

        option_arrival = datetime.strptime(
            option["arrival_date"],
            "%Y-%m-%d"
        ).date()

        option_departure = datetime.strptime(
            option["departure_date"],
            "%Y-%m-%d"
        ).date()

    except:
        return False

    try:
        flexibility_days = int(option["flexibility_days"] or 0)
    except:
        flexibility_days = 0

    if flexibility_days < 0:
        flexibility_days = 0

    available_start = option_arrival - timedelta(days=flexibility_days)
    available_end = option_departure + timedelta(days=flexibility_days)

    return available_start <= tentative_arrival and available_end >= tentative_departure


def coordination_member_rooms_for_tentative(conn, member_id, arrival_date, departure_date, total_rooms=4):

    options = conn.execute("""
        SELECT *
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
        ORDER BY
            CASE priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END,
            arrival_date
    """, (
        member_id,
    )).fetchall()

    for option in options:

        if coordination_option_fits_window(
            option,
            arrival_date,
            departure_date
        ):

            return normalize_rooms_requested(
                option["rooms_requested"],
                total_rooms
            )

    return 1


def coordination_group_is_overdue(group):

    due_date = safe_text(group["tentative_response_due_date"]).strip()

    if not due_date:
        return False

    try:
        return date.today() > datetime.strptime(
            due_date,
            "%Y-%m-%d"
        ).date()
    except:
        return False


def default_coordination_due_date():

    return (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")


def ensure_coordination_due_date(conn, group_id, group):

    current_due_date = safe_text(row_value(group, "tentative_response_due_date")).strip()

    if current_due_date and current_due_date.lower() not in ["no due date set", "none", "null"]:
        return current_due_date, group

    due_date_value = default_coordination_due_date()

    try:
        conn.execute("""
            UPDATE coordination_groups
            SET tentative_response_due_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            due_date_value,
            group_id
        ))

        conn.commit()

        refreshed_group = conn.execute("""
            SELECT *
            FROM coordination_groups
            WHERE id = ?
        """, (
            group_id,
        )).fetchone()

        if refreshed_group:
            group = refreshed_group

    except Exception:
        pass

    return due_date_value, group


def add_follow_up_acceptance_date_option(conn, member_id, arrival_date, departure_date):

    existing_rooms_row = conn.execute("""
        SELECT rooms_requested
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
        ORDER BY
            CASE priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END,
            created_at DESC
        LIMIT 1
    """, (
        member_id,
    )).fetchone()

    rooms_requested = 1

    if existing_rooms_row:
        rooms_requested = normalize_rooms_requested(
            existing_rooms_row["rooms_requested"]
        )

    existing_match = conn.execute("""
        SELECT id
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
          AND arrival_date = ?
          AND departure_date = ?
        LIMIT 1
    """, (
        member_id,
        arrival_date,
        departure_date
    )).fetchone()

    if existing_match:
        conn.execute("""
            UPDATE coordination_date_options
            SET priority = 'preferred',
                flexibility_days = 0,
                rooms_requested = ?,
                notes = ?
            WHERE id = ?
        """, (
            rooms_requested,
            "Accepted targeted follow-up proposed group dates.",
            existing_match["id"]
        ))

    else:
        conn.execute("""
            INSERT INTO coordination_date_options
            (coordination_group_member_id, priority, arrival_date, departure_date, flexibility_days, rooms_requested, notes)
            VALUES (?, 'preferred', ?, ?, 0, ?, ?)
        """, (
            member_id,
            arrival_date,
            departure_date,
            rooms_requested,
            "Accepted targeted follow-up proposed group dates."
        ))


def coordination_member_fits_or_confirmed(conn, member_row, arrival_date, departure_date):

    if safe_text(member_row["tentative_response_status"]) == "confirmed":
        return True

    member_options = conn.execute("""
        SELECT *
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
    """, (
        member_row["id"],
    )).fetchall()

    for option in member_options:

        if coordination_option_fits_window(
            option,
            arrival_date,
            departure_date
        ):
            return True

    return False


def update_coordination_ready_for_booking_if_all_fit(conn, group_id, arrival_date=None, departure_date=None):

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:
        return False

    if not arrival_date:
        arrival_date = safe_text(group["tentative_arrival_date"]).strip()

    if not departure_date:
        departure_date = safe_text(group["tentative_departure_date"]).strip()

    if not arrival_date or not departure_date:
        return False

    members = conn.execute("""
        SELECT *
        FROM coordination_group_members
        WHERE coordination_group_id = ?
    """, (
        group_id,
    )).fetchall()

    if not members:
        return False

    for member_row in members:

        if not coordination_member_fits_or_confirmed(
            conn,
            member_row,
            arrival_date,
            departure_date
        ):
            return False

    conn.execute("""
        UPDATE coordination_groups
        SET tentative_arrival_date = ?,
            tentative_departure_date = ?,
            tentative_selected_at = COALESCE(tentative_selected_at, CURRENT_TIMESTAMP),
            status = 'ready_for_booking',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        arrival_date,
        departure_date,
        group_id
    ))

    return True



def coordination_round_number(group):

    try:
        round_number = int(row_value(group, "current_round") or 1)
    except Exception:
        round_number = 1

    if round_number < 1:
        round_number = 1

    return round_number






def ensure_house_block_columns(conn):

    try:
        conn.execute("ALTER TABLE blocked_dates ADD COLUMN is_full_block INTEGER DEFAULT 1")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE blocked_dates ADD COLUMN rooms_available INTEGER")
    except Exception:
        pass

    try:
        conn.commit()
    except Exception:
        pass


def block_is_full(block):

    value = row_value(block, "is_full_block")

    if safe_text(value).strip() == "0":
        return False

    return True


def block_rooms_available(block, total_rooms):

    if block_is_full(block):
        return 0

    try:
        rooms_available = int(row_value(block, "rooms_available") or total_rooms)
    except Exception:
        rooms_available = total_rooms

    if rooms_available < 0:
        rooms_available = 0

    if rooms_available > total_rooms:
        rooms_available = total_rooms

    return rooms_available


def build_blocked_date_capacity(blocked_rows, total_rooms):

    blocked_dates = set()
    room_limit_by_date = {}

    for block in blocked_rows:

        try:
            start = datetime.strptime(block["start_date"], "%Y-%m-%d").date()
            end = datetime.strptime(block["end_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        full_block = block_is_full(block)
        rooms_available = block_rooms_available(block, total_rooms)
        current = start

        while current <= end:
            date_key = current.strftime("%Y-%m-%d")

            if full_block or rooms_available <= 0:
                blocked_dates.add(date_key)
                room_limit_by_date[date_key] = 0
            else:
                existing_limit = room_limit_by_date.get(date_key, total_rooms)
                room_limit_by_date[date_key] = min(existing_limit, rooms_available)

            current += timedelta(days=1)

    return blocked_dates, room_limit_by_date


def room_capacity_limit_for_date(room_limit_by_date, date_key, total_rooms):

    try:
        return int(room_limit_by_date.get(date_key, total_rooms))
    except Exception:
        return total_rooms


def coordination_group_rooms_needed_for_window(conn, group_id, arrival_date, departure_date, total_rooms=4):

    members = conn.execute("""
        SELECT id
        FROM coordination_group_members
        WHERE coordination_group_id = ?
    """, (
        group_id,
    )).fetchall()

    rooms_needed = 0

    for member in members:
        rooms_needed += coordination_member_rooms_for_tentative(
            conn,
            member["id"],
            arrival_date,
            departure_date,
            total_rooms
        )

    if rooms_needed < 1 and members:
        rooms_needed = len(members)

    if rooms_needed < 1:
        rooms_needed = 1

    return rooms_needed


def coordination_capacity_check_for_window(conn, group_id, arrival_date, departure_date):

    total_rooms_row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()

    total_rooms = 4
    if total_rooms_row and total_rooms_row["count"]:
        total_rooms = total_rooms_row["count"]

    rooms_needed = coordination_group_rooms_needed_for_window(
        conn,
        group_id,
        arrival_date,
        departure_date,
        total_rooms
    )

    blocked_ranges = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked_ranges,
        total_rooms
    )

    approved_by_date = {}

    approved_bookings = conn.execute("""
        SELECT arrival_date, departure_date, 1 AS rooms_held
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    tentative_holds = get_coordination_tentative_holds(
        conn,
        exclude_group_id=group_id,
        expand_rooms=True
    )

    for booking in list(approved_bookings) + list(tentative_holds):
        try:
            current = datetime.strptime(booking["arrival_date"], "%Y-%m-%d").date()
            end = datetime.strptime(booking["departure_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        rooms_to_count = 1
        try:
            rooms_to_count = int(row_value(booking, "rooms_held", "rooms_requested") or 1)
        except Exception:
            rooms_to_count = 1

        while current < end:
            date_key = current.strftime("%Y-%m-%d")
            approved_by_date[date_key] = approved_by_date.get(date_key, 0) + rooms_to_count
            current += timedelta(days=1)

    notes = []
    capacity_ok = True
    min_rooms_open = total_rooms

    try:
        current = datetime.strptime(arrival_date, "%Y-%m-%d").date()
        end = datetime.strptime(departure_date, "%Y-%m-%d").date()
    except Exception:
        return {"capacity_ok": False, "rooms_needed": rooms_needed, "rooms_available": total_rooms, "min_rooms_open": 0, "notes": ["Invalid date range."]}

    while current < end:
        date_key = current.strftime("%Y-%m-%d")
        if date_key in blocked_dates:
            capacity_ok = False
            notes.append(f"{format_date(date_key)} is fully blocked")

        daily_capacity = room_capacity_limit_for_date(room_limit_by_date, date_key, total_rooms)
        rooms_open = daily_capacity - approved_by_date.get(date_key, 0)

        if rooms_open < min_rooms_open:
            min_rooms_open = rooms_open

        if rooms_open < rooms_needed:
            capacity_ok = False
            notes.append(f"{format_date(date_key)} has only {rooms_open} room(s) open for {rooms_needed} room(s) needed")

        current += timedelta(days=1)

    return {"capacity_ok": capacity_ok, "rooms_needed": rooms_needed, "rooms_available": total_rooms, "min_rooms_open": min_rooms_open, "notes": notes}


def latest_coordination_system_overlap(conn, group_id):

    group_date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options
        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY coordination_date_options.arrival_date
    """, (group_id,)).fetchall()

    approved_bookings_for_matching = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    blocked_ranges_for_matching = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    total_rooms_for_matching = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    tentative_holds_for_matching = get_coordination_tentative_holds(conn, exclude_group_id=group_id, expand_rooms=True)

    suggestions = build_coordination_intersection_suggestions(
        group_date_options,
        list(approved_bookings_for_matching) + tentative_holds_for_matching,
        blocked_ranges_for_matching,
        total_rooms_for_matching
    )

    if suggestions:
        return suggestions[0]

    return None


def get_coordination_tentative_holds(conn, exclude_group_id=None, expand_rooms=False):

    ensure_coordination_tables(conn)

    params = []
    exclude_clause = ""

    if exclude_group_id is not None:
        exclude_clause = "AND id != ?"
        params.append(exclude_group_id)

    groups = conn.execute(f"""
        SELECT *
        FROM coordination_groups
        WHERE tentative_arrival_date IS NOT NULL
          AND TRIM(tentative_arrival_date) != ''
          AND tentative_departure_date IS NOT NULL
          AND TRIM(tentative_departure_date) != ''
          AND (closed_at IS NULL OR TRIM(closed_at) = '')
          AND status != 'archived'
          {exclude_clause}
        ORDER BY tentative_arrival_date, title
    """, params).fetchall()

    total_rooms_row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()

    total_rooms = 4

    if total_rooms_row and total_rooms_row["count"]:
        total_rooms = total_rooms_row["count"]

    holds = []

    for group in groups:

        members = conn.execute("""
            SELECT
                coordination_group_members.id AS member_id,
                guest_profiles.primary_name
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.coordination_group_id = ?
            ORDER BY guest_profiles.primary_name
        """, (
            group["id"],
        )).fetchall()

        rooms_held = 0
        member_names = []

        for member in members:

            member_names.append(
                safe_text(member["primary_name"])
            )

            rooms_held += coordination_member_rooms_for_tentative(
                conn,
                member["member_id"],
                group["tentative_arrival_date"],
                group["tentative_departure_date"],
                total_rooms
            )

        if rooms_held < 1 and members:
            rooms_held = len(members)

        if rooms_held < 1:
            rooms_held = 1

        hold = {
            "group_id": group["id"],
            "title": safe_text(group["title"]),
            "arrival_date": safe_text(group["tentative_arrival_date"]),
            "departure_date": safe_text(group["tentative_departure_date"]),
            "rooms_held": rooms_held,
            "member_names": member_names
        }

        if expand_rooms:

            for _ in range(rooms_held):
                holds.append(dict(hold, rooms_held=1))

        else:
            holds.append(hold)

    return holds

def request_change_links(request_id, repeat_visit_url=None):

    request_id_text = safe_text(request_id).strip()

    if request_id_text.isdigit():
        change_url = f"{BASE_URL}/request/{request_id_text}/change"
        cancel_url = f"{BASE_URL}/request/{request_id_text}/cancel"
        all_reservations_url = f"{BASE_URL}/request/{request_id_text}/all-reservations"
    else:
        change_url = BASE_URL
        cancel_url = ""
        all_reservations_url = standard_new_request_url()

    if not repeat_visit_url:
        repeat_visit_url = standard_new_request_url()

    cancel_block = ""

    if cancel_url:
        cancel_block = f"""
Cancel Visit:
{cancel_url}
"""

    return f"""

━━━━━━━━━━━━━━━━━━

Need to make a change?

Change Visit:
{change_url}
{cancel_block}
Request Another Visit:
{repeat_visit_url}

All Reservations:
{all_reservations_url}

━━━━━━━━━━━━━━━━━━
"""


def ensure_guest_change_links(body, request_id, repeat_visit_url=None):

    body = safe_text(body)

    if "Need to make a change?" in body:
        return body

    return body + request_change_links(request_id, repeat_visit_url)


def guest_visit_history_summary(conn, request_row, current_request_id=None):

    if not request_row:
        return ""

    guest_profile_id = row_value(request_row, "guest_profile_id")
    guest_email = clean_text(row_value(request_row, "email")).lower()

    rows = []

    if guest_profile_id:
        rows = conn.execute("""
            SELECT
                booking_requests.id,
                booking_requests.arrival_date,
                booking_requests.departure_date,
                booking_requests.rooms_requested,
                booking_requests.status
            FROM booking_requests
            WHERE guest_profile_id = ?
              AND status IN ('pending', 'approved', 'change_requested', 'cancel_requested')
            ORDER BY arrival_date DESC, id DESC
            LIMIT 6
        """, (
            guest_profile_id,
        )).fetchall()

    elif guest_email:
        rows = conn.execute("""
            SELECT
                id,
                arrival_date,
                departure_date,
                rooms_requested,
                status
            FROM booking_requests
            WHERE LOWER(email) = ?
              AND status IN ('pending', 'approved', 'change_requested', 'cancel_requested')
            ORDER BY arrival_date DESC, id DESC
            LIMIT 6
        """, (
            guest_email,
        )).fetchall()

    if not rows:
        return ""

    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Visit Summary",
        ""
    ]

    for row in rows:
        marker = "Current" if safe_text(row["id"]) == safe_text(current_request_id) else "Previous/Other"
        lines.append(
            f"- {marker}: {format_date(row['arrival_date'])} to {format_date(row['departure_date'])} | Rooms: {row['rooms_requested'] or 1} | Status: {safe_text(row['status']).replace('_', ' ').title()}"
        )

    lines.extend([
        "━━━━━━━━━━━━━━━━━━",
        ""
    ])

    return "\n".join(lines)


def append_guest_visit_history_summary(body, conn, request_row, current_request_id=None):

    # V32.7: visit history is helpful, but it must never block confirmation emails.
    # If the DB row shape or history query is not available, return the original body.
    try:
        summary = guest_visit_history_summary(
            conn,
            request_row,
            current_request_id
        )

        if not summary:
            return body

        if "Visit Summary" in safe_text(body):
            return body

        return safe_text(body).rstrip() + "\n" + summary

    except Exception as error:
        try:
            write_email_audit(
                row_value(request_row, "email", "primary_email"),
                "Visit Summary",
                "SUMMARY_SKIPPED",
                error
            )
        except Exception:
            pass

        return body


def guest_reservations_html(conn, request_row):

    if not request_row:
        return ""

    guest_profile_id = row_value(request_row, "guest_profile_id")
    guest_email = clean_text(row_value(request_row, "email", "primary_email")).lower()

    params = []
    where_clause = ""

    if guest_profile_id:
        where_clause = "guest_profile_id = ?"
        params.append(guest_profile_id)
    elif guest_email:
        where_clause = "LOWER(email) = ?"
        params.append(guest_email)
    else:
        return "<p>No reservation history is available for this request.</p>"

    rows = conn.execute(f"""
        SELECT
            id,
            arrival_date,
            departure_date,
            rooms_requested,
            status,
            created_at
        FROM booking_requests
        WHERE {where_clause}
          AND status IN ('pending', 'approved', 'change_requested', 'cancel_requested', 'declined')
        ORDER BY arrival_date DESC, id DESC
    """, params).fetchall()

    if not rows:
        return "<p>No confirmed or pending visits found.</p>"

    row_html = ""

    for visit in rows:

        status_text = safe_text(visit["status"]).replace("_", " ").title()

        if safe_text(visit["status"]) == "approved":
            badge_bg = "#e8f7ea"
            badge_color = "#198754"
        elif safe_text(visit["status"]) == "pending":
            badge_bg = "#fff3cd"
            badge_color = "#856404"
        elif safe_text(visit["status"]) in ("change_requested", "cancel_requested"):
            badge_bg = "#e7f1ff"
            badge_color = "#0d6efd"
        else:
            badge_bg = "#f8d7da"
            badge_color = "#842029"

        row_html += f"""
        <tr>
            <td style="padding:6px 8px; border-bottom:1px solid #eee; white-space:nowrap;">
                {format_date(visit['arrival_date'])}
            </td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee; white-space:nowrap;">
                {format_date(visit['departure_date'])}
            </td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee; text-align:center;">
                {safe_text(visit['rooms_requested'] or 1)}
            </td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee;">
                <span style="background:{badge_bg}; color:{badge_color}; padding:3px 7px; border-radius:999px; font-size:12px; font-weight:bold;">
                    {safe_text(status_text)}
                </span>
            </td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee; white-space:nowrap;">
                <a href="/request/{visit['id']}/change" style="
                    display:inline-block;
                    background:#0f4c81;
                    color:#ffffff;
                    padding:6px 9px;
                    border-radius:7px;
                    text-decoration:none;
                    font-size:12px;
                    font-weight:bold;
                    margin:2px 4px 2px 0;
                ">Change Request</a>
                <a href="/request/{visit['id']}/cancel" style="
                    display:inline-block;
                    background:#842029;
                    color:#ffffff;
                    padding:6px 9px;
                    border-radius:7px;
                    text-decoration:none;
                    font-size:12px;
                    font-weight:bold;
                    margin:2px 0;
                ">Cancel Request</a>
            </td>
        </tr>
        """

    return f"""
    <table border="0" cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%; max-width:850px; font-size:14px;">
        <tr style="background:#f5f7fa;">
            <th align="left" style="padding:7px 8px; border-bottom:1px solid #ddd;">Arrival</th>
            <th align="left" style="padding:7px 8px; border-bottom:1px solid #ddd;">Departure</th>
            <th align="center" style="padding:7px 8px; border-bottom:1px solid #ddd;">Rooms</th>
            <th align="left" style="padding:7px 8px; border-bottom:1px solid #ddd;">Status</th>
            <th align="left" style="padding:7px 8px; border-bottom:1px solid #ddd;">Actions</th>
        </tr>
        {row_html}
    </table>
    """


@app.route("/request/<int:request_id>/all-reservations")
def all_reservations(request_id):

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (
        request_id,
    )).fetchone()

    if not request_row:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Reservations Not Found</h1>
        <p>We could not find the visit request for this link.</p>
        """

    guest_name = safe_text(row_value(request_row, "name", "primary_name"))
    invitation_id = row_value(request_row, "invitation_id")

    if invitation_id:
        request_visit_link = f"/invite/{safe_text(invitation_id)}"
    else:
        request_visit_link = "/new-request"

    reservations_html = guest_reservations_html(
        conn,
        request_row
    )

    conn.close()

    return f"""
    {nav_links()}

    <h1>All Reservations</h1>

    <p style="max-width:850px; line-height:1.4;">
        This is a guest summary of confirmed and pending Shore Home visits for {safe_text(guest_name)}. Use Change Request or Cancel Request next to a visit if you need to update plans.
    </p>

    {reservations_html}

    <p style="margin-top:16px;">
        <a href="{request_visit_link}" style="
            display:inline-block;
            background:#0f4c81;
            color:white;
            padding:10px 14px;
            border-radius:8px;
            text-decoration:none;
            font-weight:bold;
        ">
            Request a Visit
        </a>
    </p>
    """


def short_date(date_string):

    if not date_string:
        return ""

    return datetime.strptime(
        date_string,
        "%Y-%m-%d"
    ).strftime("%m/%d")


def ensure_activity_log_table(conn):

    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            action_type TEXT NOT NULL DEFAULT '',
            old_status TEXT,
            new_status TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(activity_log)").fetchall()
        }

        required_columns = {
            "request_id": "INTEGER",
            "action_type": "TEXT NOT NULL DEFAULT ''",
            "old_status": "TEXT",
            "new_status": "TEXT",
            "notes": "TEXT",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }

        for column_name, column_definition in required_columns.items():
            if column_name not in columns:
                conn.execute(
                    f"ALTER TABLE activity_log ADD COLUMN {column_name} {column_definition}"
                )
    except Exception:
        pass


def write_activity_log(conn, request_id, action_type, old_status, new_status, notes):

    ensure_activity_log_table(conn)

    conn.execute("""
        INSERT INTO activity_log
        (request_id, action_type, old_status, new_status, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (
        request_id,
        action_type,
        old_status,
        new_status,
        notes
    ))


def ensure_coordination_tables(conn):

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coordination_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            target_year INTEGER,
            status TEXT NOT NULL DEFAULT 'planning',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coordination_group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coordination_group_id INTEGER NOT NULL,
            guest_profile_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'participant',
            invitation_status TEXT NOT NULL DEFAULT 'draft',
            last_response_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coordination_date_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coordination_group_member_id INTEGER NOT NULL,
            priority TEXT NOT NULL DEFAULT 'preferred',
            arrival_date TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            flexibility_days INTEGER DEFAULT 0,
            rooms_requested INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coordination_tentative_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coordination_group_id INTEGER NOT NULL,
            system_arrival_date TEXT,
            system_departure_date TEXT,
            admin_arrival_date TEXT NOT NULL,
            admin_departure_date TEXT NOT NULL,
            adjustment_reason TEXT,
            rooms_needed INTEGER DEFAULT 0,
            capacity_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    coordination_group_columns = [
        "tentative_arrival_date TEXT",
        "tentative_departure_date TEXT",
        "tentative_selected_at TIMESTAMP",
        "tentative_response_due_date TEXT",
        "coordination_reminder_sent_at TIMESTAMP",
        "final_coordination_email_sent_at TIMESTAMP",
        "final_visit_confirmation_sent_at TIMESTAMP",
        "converted_at TIMESTAMP",
        "closed_at TIMESTAMP",
        "current_round INTEGER DEFAULT 1",
        "current_round_started_at TIMESTAMP",
        "round_status TEXT DEFAULT 'collecting'"
    ]

    for column_definition in coordination_group_columns:

        try:
            conn.execute(
                f"ALTER TABLE coordination_groups ADD COLUMN {column_definition}"
            )
        except:
            pass

    coordination_member_columns = [
        "tentative_response_status TEXT",
        "tentative_response_at TIMESTAMP",
        "tentative_response_notes TEXT",
        "converted_request_id INTEGER",
        "converted_at TIMESTAMP",
        "follow_up_round INTEGER",
        "follow_up_sent_at TIMESTAMP",
        "follow_up_response_at TIMESTAMP",
        "follow_up_suggested_arrival TEXT",
        "follow_up_suggested_departure TEXT",
        "organizer_suggested_guests TEXT",
        "organizer_suggested_dates_notes TEXT",
        "organizer_suggestions_at TIMESTAMP",
        "organizer_kickoff_sent_at TIMESTAMP"
    ]

    for column_definition in coordination_member_columns:

        try:
            conn.execute(
                f"ALTER TABLE coordination_group_members ADD COLUMN {column_definition}"
            )
        except:
            pass

    coordination_date_option_columns = [
        "rooms_requested INTEGER DEFAULT 1",
        "notes TEXT",
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    ]

    for column_definition in coordination_date_option_columns:

        try:
            conn.execute(
                f"ALTER TABLE coordination_date_options ADD COLUMN {column_definition}"
            )
        except:
            pass

    booking_request_coordination_columns = [
        "coordination_group_id INTEGER",
        "coordination_group_member_id INTEGER"
    ]

    for column_definition in booking_request_coordination_columns:

        try:
            conn.execute(
                f"ALTER TABLE booking_requests ADD COLUMN {column_definition}"
            )
        except:
            pass

    try:
        conn.commit()
    except:
        pass


def create_database_backup(reason):

    backup_folder = "backups"

    os.makedirs(
        backup_folder,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y_%m_%d__%H_%M_%S"
    )

    safe_reason = reason.replace(" ", "_").lower()

    backup_filename = (
        f"shore_backup_{safe_reason}_{timestamp}.db"
    )

    backup_path = os.path.join(
        backup_folder,
        backup_filename
    )

    if os.path.exists(DATABASE_FILE):

        shutil.copy2(
            DATABASE_FILE,
            backup_path
        )

        return backup_path

    return ""




def rollback_and_close(conn):

    try:
        conn.rollback()
    except:
        pass

    conn.close()


def transaction_error_page(error, back_link="/requests"):

    return f"""
    {nav_links()}

    <h1>Action Not Completed</h1>

    <p style="
        color: red;
        font-weight: bold;
    ">
        The action was stopped before any partial database changes were saved.
    </p>

    <p>
        This is the transaction safety guard working.
    </p>

    <p>
        <strong>Error:</strong><br>
        {safe_text(error)}
    </p>

    <p>
        <a href="{back_link}">
            Back
        </a>
    </p>
    """

def normalize_rooms_requested(value, total_rooms=4):

    if not value:
        value = 1

    try:
        value = int(value)
    except:
        value = 1

    if value < 1:
        value = 1

    if value > total_rooms:
        value = total_rooms

    return value


def combined_confirmed_group_members(conn, request_row):

    names = []

    def add_names(raw_value):
        raw_text = safe_text(raw_value).strip()

        if not raw_text or raw_text.lower() in ["none", "none listed", "n/a", "na"]:
            return

        # Keep casual text usable, but split common separators.
        parts = re.split(r"[,;\n]+", raw_text)

        for part in parts:
            clean_part = safe_text(part).strip()

            if clean_part and clean_part.lower() not in [safe_text(existing).strip().lower() for existing in names]:
                names.append(clean_part)

    add_names(row_value(request_row, "name"))

    try:
        guest_profile_id = row_value(request_row, "guest_profile_id")

        if guest_profile_id:
            profile_row = conn.execute("""
                SELECT primary_name, additional_names
                FROM guest_profiles
                WHERE id = ?
            """, (
                guest_profile_id,
            )).fetchone()

            if profile_row:
                add_names(row_value(profile_row, "primary_name"))
                add_names(row_value(profile_row, "additional_names"))

    except Exception:
        pass

    add_names(row_value(request_row, "additional_names"))

    if not names:
        return "None listed"

    return ", ".join(names)


def resolve_request_recipient_email(conn, request_row):

    if not request_row:
        return ""

    recipient_email = safe_text(
        request_row["email"]
    ).strip()

    if recipient_email:
        return recipient_email

    try:
        guest_profile_id = request_row["guest_profile_id"]
    except:
        guest_profile_id = None

    if guest_profile_id:

        profile_row = conn.execute("""
            SELECT primary_email
            FROM guest_profiles
            WHERE id = ?
        """, (
            guest_profile_id,
        )).fetchone()

        if profile_row:

            recipient_email = safe_text(
                profile_row["primary_email"]
            ).strip()

            if recipient_email:
                return recipient_email

    try:
        invitation_id = request_row["invitation_id"]
    except:
        invitation_id = None

    if invitation_id:

        invitation_guest = conn.execute("""
            SELECT guest_profiles.primary_email
            FROM invitations

            JOIN guest_profiles
                ON invitations.guest_profile_id = guest_profiles.id

            WHERE invitations.id = ?
        """, (
            invitation_id,
        )).fetchone()

        if invitation_guest:

            recipient_email = safe_text(
                invitation_guest["primary_email"]
            ).strip()

            if recipient_email:
                return recipient_email

    return ""


def get_booking_audit_problems(conn):

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    bookings = conn.execute("""
        SELECT
            bookings.id,
            bookings.request_id,
            bookings.room_id,
            bookings.arrival_date,
            bookings.departure_date,
            bookings.status,
            booking_requests.name AS guest_name,
            booking_requests.email,
            booking_requests.status AS request_status,
            booking_requests.rooms_requested,
            rooms.name AS room_name
        FROM bookings
        LEFT JOIN booking_requests
            ON bookings.request_id = booking_requests.id
        LEFT JOIN rooms
            ON bookings.room_id = rooms.id
        ORDER BY
            bookings.arrival_date,
            rooms.name,
            booking_requests.name
    """).fetchall()

    approved_requests = conn.execute("""
        SELECT
            booking_requests.id,
            booking_requests.name,
            booking_requests.email,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.rooms_requested,
            COUNT(bookings.id) AS booking_count
        FROM booking_requests
        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'
        WHERE booking_requests.status = 'approved'
        GROUP BY booking_requests.id
        ORDER BY booking_requests.arrival_date,
                 booking_requests.name
    """).fetchall()

    problems = []

    approved_bookings = []

    for booking in bookings:

        if booking["status"] == "approved":
            approved_bookings.append(booking)

    for booking in bookings:

        if not booking["request_id"]:

            problems.append({
                "type": "Booking Missing Request",
                "severity": "High",
                "details": f"Booking ID {booking['id']} is not tied to a request.",
                "link": "/bookings"
            })

        elif not booking["guest_name"]:

            problems.append({
                "type": "Booking Request Not Found",
                "severity": "High",
                "details": f"Booking ID {booking['id']} points to request ID {booking['request_id']}, but that request was not found.",
                "link": "/bookings"
            })

        if not booking["room_id"] or not booking["room_name"]:

            problems.append({
                "type": "Booking Missing Room",
                "severity": "High",
                "details": f"Booking ID {booking['id']} is missing a valid room assignment.",
                "link": f"/request/{booking['request_id']}" if booking["request_id"] else "/bookings"
            })

        if booking["request_status"] and booking["request_status"] != "approved" and booking["status"] == "approved":

            problems.append({
                "type": "Approved Booking On Non-Approved Request",
                "severity": "High",
                "details": f"{booking['guest_name']} has an approved booking, but the request status is {booking['request_status']}.",
                "link": f"/request/{booking['request_id']}"
            })

        try:
            arrival = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            )

            departure = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            )

            if departure <= arrival:

                problems.append({
                    "type": "Invalid Booking Date Range",
                    "severity": "High",
                    "details": f"{booking['guest_name']} has a booking where departure is not after arrival.",
                    "link": f"/request/{booking['request_id']}"
                })

        except:

            problems.append({
                "type": "Invalid Booking Date Format",
                "severity": "High",
                "details": f"Booking ID {booking['id']} has an invalid date format.",
                "link": f"/request/{booking['request_id']}" if booking["request_id"] else "/bookings"
            })

    for i, booking_one in enumerate(approved_bookings):

        for j, booking_two in enumerate(approved_bookings):

            if j <= i:
                continue

            if booking_one["room_id"] != booking_two["room_id"]:
                continue

            if not booking_one["room_id"]:
                continue

            if (
                booking_one["arrival_date"] < booking_two["departure_date"]
                and booking_one["departure_date"] > booking_two["arrival_date"]
            ):

                problems.append({
                    "type": "Double-Booked Room",
                    "severity": "Critical",
                    "details": f"{booking_one['room_name']} is booked for both {booking_one['guest_name']} and {booking_two['guest_name']} during overlapping dates.",
                    "link": f"/request/{booking_one['request_id']}"
                })

    occupancy_by_date = {}

    for booking in approved_bookings:

        try:
            current = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            departure = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

        except:
            continue

        while current < departure:

            date_string = current.strftime("%Y-%m-%d")

            if date_string not in occupancy_by_date:
                occupancy_by_date[date_string] = []

            occupancy_by_date[date_string].append(booking)

            current += timedelta(days=1)

    for date_string in sorted(occupancy_by_date.keys()):

        day_bookings = occupancy_by_date[date_string]

        if len(day_bookings) > total_rooms:

            guest_names = []

            for booking in day_bookings:
                guest_names.append(booking["guest_name"])

            problems.append({
                "type": "House Over Capacity",
                "severity": "Critical",
                "details": f"{format_date(date_string)} has {len(day_bookings)} approved room bookings, but only {total_rooms} room(s) exist. Guests: {', '.join(guest_names)}.",
                "link": "/bookings"
            })

    for request_row in approved_requests:

        rooms_requested = normalize_rooms_requested(
            request_row["rooms_requested"],
            total_rooms
        )

        if request_row["rooms_requested"] and int(request_row["rooms_requested"]) > total_rooms:

            problems.append({
                "type": "Request Asks For Too Many Rooms",
                "severity": "High",
                "details": f"{request_row['name']} has {request_row['rooms_requested']} room(s) requested, but only {total_rooms} room(s) exist.",
                "link": f"/request/{request_row['id']}"
            })

        if request_row["booking_count"] == 0:

            problems.append({
                "type": "Approved Request Missing Booking",
                "severity": "High",
                "details": f"{request_row['name']} is approved, but has no approved booking rows.",
                "link": f"/request/{request_row['id']}"
            })

        elif request_row["booking_count"] != rooms_requested:

            problems.append({
                "type": "Room Count Mismatch",
                "severity": "High",
                "details": f"{request_row['name']} requested {rooms_requested} room(s), but has {request_row['booking_count']} approved booking row(s).",
                "link": f"/request/{request_row['id']}"
            })

    return problems, total_rooms, len(approved_bookings), len(approved_requests)

@app.route("/dashboard")
def dashboard():

    conn = get_db_connection()

    selected_year = int(request.args.get("year", date.today().year))
    selected_month = int(request.args.get("month", date.today().month))

    if selected_month < 1:
        selected_month = 12
        selected_year -= 1

    if selected_month > 12:
        selected_month = 1
        selected_year += 1

    pending_requests = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE status = 'pending'
    """).fetchall()

    pending_calendar_requests = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE status IN ('pending', 'change_requested')
        ORDER BY arrival_date, name
    """).fetchall()

    approved_bookings = conn.execute("""
        SELECT
            bookings.*,
            booking_requests.name,
            booking_requests.email,
            booking_requests.additional_names,
            rooms.name AS room_name
        FROM bookings
        JOIN booking_requests
            ON bookings.request_id = booking_requests.id
        JOIN rooms
            ON bookings.room_id = rooms.id
        WHERE bookings.status = 'approved'
        ORDER BY
            bookings.arrival_date,
            booking_requests.name,
            rooms.name
    """).fetchall()

    emails_need_sending = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE email_status IN ('needs_email', 'needs_update')
    """).fetchone()["count"]

    today_string = date.today().strftime("%Y-%m-%d")

    arrivals_today = conn.execute("""
        SELECT COUNT(*) AS count
        FROM bookings
        WHERE arrival_date = ?
          AND status = 'approved'
    """, (today_string,)).fetchone()["count"]

    departures_today = conn.execute("""
        SELECT COUNT(*) AS count
        FROM bookings
        WHERE departure_date = ?
          AND status = 'approved'
    """, (today_string,)).fetchone()["count"]

    declined_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE status = 'declined'
    """).fetchone()["count"]

    profiles_needing_review = conn.execute("""
        SELECT COUNT(*) AS count
        FROM guest_profiles
        WHERE status = 'needs_review'
    """).fetchone()["count"]

    active_profiles = conn.execute("""
        SELECT COUNT(*) AS count
        FROM guest_profiles
        WHERE status = 'active'
    """).fetchone()["count"]

    archived_profiles = conn.execute("""
        SELECT COUNT(*) AS count
        FROM guest_profiles
        WHERE status = 'archived'
    """).fetchone()["count"]

    ensure_house_block_columns(conn)

    blocked_ranges = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    invitations_not_sent = conn.execute("""
        SELECT COUNT(*) AS count
        FROM invitations
        WHERE status = 'draft'
    """).fetchone()["count"]

    invitations_no_reply = conn.execute("""
        SELECT COUNT(*) AS count
        FROM invitations
        WHERE status = 'sent'
          AND id NOT IN (
                SELECT DISTINCT invitation_id
                FROM booking_requests
                WHERE invitation_id IS NOT NULL
          )
    """).fetchone()["count"]

    coordination_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE coordination_notes IS NOT NULL
          AND TRIM(coordination_notes) != ''
          AND status = 'pending'
    """).fetchone()["count"]

    change_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE status = 'change_requested'
    """).fetchone()["count"]

    cancel_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE status = 'cancel_requested'
    """).fetchone()["count"]


    upcoming_arrivals = conn.execute("""
        SELECT
            booking_requests.id AS request_id,
            booking_requests.name,
            booking_requests.additional_names,
            bookings.arrival_date,
            bookings.departure_date,
            rooms.name AS room_name
        FROM bookings
        JOIN booking_requests
            ON bookings.request_id = booking_requests.id
        JOIN rooms
            ON bookings.room_id = rooms.id
        WHERE bookings.arrival_date >= date('now')
          AND bookings.status = 'approved'
        ORDER BY
            bookings.arrival_date,
            booking_requests.name,
            rooms.name
        LIMIT 20
    """).fetchall()

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    ensure_coordination_tables(conn)

    coordination_groups = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE COALESCE(status, '') != 'archived'
        ORDER BY
            updated_at DESC,
            created_at DESC
    """).fetchall()

    coordination_dashboard_rows = []

    for coordination_group in coordination_groups:

        coordination_members = conn.execute("""
            SELECT
                coordination_group_members.id AS member_id,
                coordination_group_members.invitation_status,
                coordination_group_members.tentative_response_status,
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM coordination_group_members

            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id

            WHERE coordination_group_members.coordination_group_id = ?

            ORDER BY guest_profiles.primary_name
        """, (
            coordination_group["id"],
        )).fetchall()

        coordination_date_options = conn.execute("""
            SELECT
                coordination_date_options.*,
                coordination_group_members.id AS member_id,
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM coordination_date_options

            JOIN coordination_group_members
                ON coordination_date_options.coordination_group_member_id = coordination_group_members.id

            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id

            WHERE coordination_group_members.coordination_group_id = ?

            ORDER BY
                guest_profiles.primary_name,
                coordination_date_options.priority,
                coordination_date_options.arrival_date
        """, (
            coordination_group["id"],
        )).fetchall()

        match_suggestions = build_coordination_match_suggestions(
            coordination_date_options,
            approved_bookings,
            blocked_ranges,
            total_rooms
        )

        member_count = len(coordination_members)

        responded_count = 0

        for coordination_member in coordination_members:

            if coordination_member["invitation_status"] == "responded":
                responded_count += 1

        best_match_text = "No date options yet"
        top_match_options = []
        unmatched_count = member_count
        capacity_status = "Not Checked"
        tentative_status = "No Tentative Dates"
        confirmation_status = ""

        if safe_text(coordination_group["tentative_arrival_date"]) and safe_text(coordination_group["tentative_departure_date"]):

            tentative_status = (
                f"{format_date(coordination_group['tentative_arrival_date'])} "
                f"to {format_date(coordination_group['tentative_departure_date'])}"
            )

            confirmed_count = 0
            cannot_count = 0
            discussion_count = 0
            no_response_count = 0

            for coordination_member in coordination_members:

                response_status = safe_text(coordination_member["tentative_response_status"])

                if response_status == "confirmed":
                    confirmed_count += 1
                elif response_status == "cannot_make":
                    cannot_count += 1
                elif response_status == "needs_discussion":
                    discussion_count += 1
                else:
                    no_response_count += 1

            confirmation_status = (
                f"Confirmed {confirmed_count}/{member_count}; "
                f"No {cannot_count}; Discuss {discussion_count}; "
                f"No Response {no_response_count}"
            )

            if safe_text(coordination_group["tentative_response_due_date"]):

                confirmation_status += (
                    f"<br>Due {safe_text(coordination_group['tentative_response_due_date'])}"
                )

                if coordination_group_is_overdue(coordination_group):
                    confirmation_status += " <strong style='color:red;'>Overdue</strong>"

        if match_suggestions:

            best_match = match_suggestions[0]

            best_match_text = (
                f"{format_date(best_match['arrival_date'])} "
                f"to {format_date(best_match['departure_date'])} "
                f"({best_match['matched_count']} of {member_count})"
            )

            unmatched_count = member_count - best_match["matched_count"]

            if best_match["capacity_ok"]:
                capacity_status = "OK"
            else:
                capacity_status = "Issue"

            for match_suggestion in match_suggestions[:2]:

                top_match_options.append(
                    f"{format_date(match_suggestion['arrival_date'])} "
                    f"to {format_date(match_suggestion['departure_date'])} "
                    f"({match_suggestion['matched_count']} of {member_count})"
                )

        elif coordination_date_options:

            best_match_text = "No overlap yet"
            capacity_status = "No Match"

        converted_request_rows = conn.execute("""
            SELECT
                coordination_group_members.converted_request_id,
                booking_requests.status AS request_status,
                COUNT(bookings.id) AS approved_booking_count
            FROM coordination_group_members

            LEFT JOIN booking_requests
                ON coordination_group_members.converted_request_id = booking_requests.id

            LEFT JOIN bookings
                ON booking_requests.id = bookings.request_id
               AND bookings.status = 'approved'

            WHERE coordination_group_members.coordination_group_id = ?
              AND coordination_group_members.converted_request_id IS NOT NULL

            GROUP BY
                coordination_group_members.converted_request_id,
                booking_requests.status
        """, (
            coordination_group["id"],
        )).fetchall()

        converted_request_count = len(converted_request_rows)
        converted_requests_needing_review = 0

        for converted_request_row in converted_request_rows:

            if converted_request_row["request_status"] != "approved":
                converted_requests_needing_review += 1

            elif converted_request_row["approved_booking_count"] == 0:
                converted_requests_needing_review += 1

        booking_handoff_status = "Not Created"

        if converted_request_count > 0:

            booking_handoff_status = (
                f"{converted_request_count} created"
            )

            if converted_requests_needing_review > 0:

                booking_handoff_status += (
                    f"<br><strong style='color: #dc3545;'>"
                    f"{converted_requests_needing_review} need room assignment / approval"
                    f"</strong>"
                )

            else:

                booking_handoff_status += (
                    "<br><strong style='color: #198754;'>All reviewed</strong>"
                )

        needs_attention = False

        coordination_status = safe_text(coordination_group["status"]).strip()

        if coordination_status in ["finalized", "closed", "archived"]:
            needs_attention = False
            booking_handoff_status = "Closed / finalized"
            confirmation_status = "Complete"
            tentative_status = "Closed / finalized"
            capacity_status = "OK"

        else:

            if member_count == 0:
                needs_attention = True

            if responded_count < member_count:
                needs_attention = True

            if unmatched_count > 0:
                needs_attention = True

            if capacity_status in ["Issue", "No Match"]:
                needs_attention = True

            if converted_requests_needing_review > 0:
                needs_attention = True

        coordination_dashboard_rows.append({
            "group_id": coordination_group["id"],
            "title": coordination_group["title"],
            "status": coordination_group["status"],
            "member_count": member_count,
            "responded_count": responded_count,
            "best_match_text": best_match_text,
            "top_match_options": top_match_options,
            "unmatched_count": unmatched_count,
            "capacity_status": capacity_status,
            "tentative_status": tentative_status,
            "confirmation_status": confirmation_status,
            "booking_handoff_status": booking_handoff_status,
            "converted_request_count": converted_request_count,
            "converted_requests_needing_review": converted_requests_needing_review,
            "needs_attention": needs_attention
        })

    coordination_dashboard_rows = sorted(
        coordination_dashboard_rows,
        key=lambda row: (
            0 if row["needs_attention"] else 1,
            row["title"]
        )
    )

    coordination_attention_count = 0
    room_assignment_attention_count = 0

    for coordination_row in coordination_dashboard_rows:

        if coordination_row["needs_attention"]:
            coordination_attention_count += 1

        room_assignment_attention_count += coordination_row["converted_requests_needing_review"]

    audit_problems, audit_total_rooms, audit_booking_count, audit_request_count = get_booking_audit_problems(conn)

    audit_problem_count = len(audit_problems)
    critical_audit_count = 0

    for audit_problem in audit_problems:

        if audit_problem["severity"] == "Critical":
            critical_audit_count += 1

    tentative_holds = get_coordination_tentative_holds(conn)

    conn.close()

    total_pending_nights = 0
    total_pending_rooms = 0

    for row in pending_requests:

        nights = (
            datetime.strptime(
                row["departure_date"],
                "%Y-%m-%d"
            )
            - datetime.strptime(
                row["arrival_date"],
                "%Y-%m-%d"
            )
        ).days

        rooms_requested = row["rooms_requested"]

        if not rooms_requested:
            rooms_requested = 1

        rooms_requested = int(rooms_requested)

        total_pending_nights += nights
        total_pending_rooms += rooms_requested

    total_approved_nights = 0

    for row in approved_bookings:

        total_approved_nights += (
            datetime.strptime(
                row["departure_date"],
                "%Y-%m-%d"
            )
            - datetime.strptime(
                row["arrival_date"],
                "%Y-%m-%d"
            )
        ).days

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked_ranges,
        total_rooms
    )
    blocked_reasons_by_date = {}

    for block in blocked_ranges:

        if not block_is_full(block):
            continue

        start = parse_iso_date_safe(block["start_date"])
        end = parse_iso_date_safe(block["end_date"])

        if not start or not end:
            continue

        block_reason = ""
        try:
            block_reason = safe_text(block["reason"]).strip()
        except Exception:
            block_reason = ""

        current = start

        while current <= end:

            current_date_key = current.strftime("%Y-%m-%d")

            if block_reason:
                blocked_reasons_by_date[current_date_key] = block_reason

            current += timedelta(days=1)

    first_day = date(
        selected_year,
        selected_month,
        1
    )

    if selected_month == 12:

        next_month_date = date(
            selected_year + 1,
            1,
            1
        )

    else:

        next_month_date = date(
            selected_year,
            selected_month + 1,
            1
        )

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:

        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:

        next_month = 1
        next_year += 1

    days_in_month = (
        next_month_date - first_day
    ).days

    start_weekday = (first_day.weekday() + 1) % 7

    month_title = first_day.strftime("%B %Y")

    calendar_html = f"""
    <h2 style="
        margin-top: 28px;
        margin-bottom: 8px;
    ">
        Operations Calendar - {month_title}
    </h2>

    <p style="margin-bottom: 10px;">

        <a href="/dashboard?year={previous_year}&month={previous_month}">
            Previous
        </a>

        |

        <strong>{month_title}</strong>

        |

        <a href="/dashboard?year={next_year}&month={next_month}">
            Next
        </a>

    </p>

    <table border="1"
           cellpadding="4"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               font-size: 11px;
           ">

        <tr style="background-color: #f5f5f5;">
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>

        <tr>
    """

    for _ in range(start_weekday):

        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):

        current_date = date(
            selected_year,
            selected_month,
            day
        )

        current_date_str = current_date.strftime(
            "%Y-%m-%d"
        )

        rooms_used = 0

        day_bookings_html = ""

        for booking in approved_bookings:

            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            booking_calendar_label = ""

            if booking_start == current_date:
                booking_calendar_label = "ARR"
            elif booking_end == current_date:
                booking_calendar_label = "DEP"
            elif booking_start < current_date < booking_end:
                booking_calendar_label = "STAY"

            if booking_start <= current_date < booking_end:
                rooms_used += 1

            if booking_calendar_label:

                day_bookings_html += f"""
                <div style="
                        margin-top: 2px;
                        font-size: 10px;
                        line-height: 1.1;
                    ">

                    <strong>{booking_calendar_label}</strong>

                    <a href="/request/{booking['request_id']}">
                        <strong>{booking['name']}</strong>
                    </a>

                    <small>
                        ({booking['room_name']})
                    </small>

                </div>
                """

        pending_count = 0
        pending_day_html = ""

        for pending_request in pending_calendar_requests:

            try:
                pending_start = datetime.strptime(
                    pending_request["arrival_date"],
                    "%Y-%m-%d"
                ).date()

                pending_end = datetime.strptime(
                    pending_request["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except:
                continue

            if pending_start <= current_date < pending_end:

                pending_count += 1

                pending_day_html += f"""
                <div style="
                        margin-top: 2px;
                        font-size: 10px;
                        line-height: 1.1;
                        color: #856404;
                    ">
                    Pending Review:
                    <a href="/request/{pending_request['id']}">
                        {pending_request['name']}
                    </a>
                </div>
                """

        tentative_hold_rooms = 0
        tentative_hold_html = ""

        for tentative_hold in tentative_holds:

            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()

                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except:
                continue

            if hold_start <= current_date < hold_end:

                tentative_hold_rooms += int(tentative_hold.get("rooms_held", 1) or 1)

                tentative_hold_html += f"""
                <div style="
                        margin-top: 2px;
                        font-size: 10px;
                        line-height: 1.1;
                        color: #fd7e14;
                    ">
                    Tentative Hold:
                    <a href="/coordination-group/{tentative_hold['group_id']}/handoff">
                        {safe_text(tentative_hold['title'])}
                    </a>
                    <small>({tentative_hold.get('rooms_held', 1)} room(s))</small>
                </div>
                """

        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            current_date_str,
            total_rooms
        )

        rooms_open = capacity_limit - rooms_used - tentative_hold_rooms

        blocked_reason_html = ""

        if current_date_str in blocked_dates:

            background = "#f8d7da"

            # Admin/non-guest calendar only: show the saved house-hold reason.
            # Guest-facing calendars intentionally keep hold reasons hidden.
            block_reason = safe_text(
                blocked_reasons_by_date.get(
                    current_date_str,
                    ""
                )
            ).strip()

            if block_reason:
                blocked_reason_html = f"""
                <div style="
                    margin-top: 4px;
                    padding: 3px 4px;
                    border: 1px solid #dc3545;
                    background-color: #fff5f5;
                    color: #842029;
                    font-size: 11px;
                    line-height: 1.2;
                    font-weight: bold;
                ">
                    Hold Reason: {safe_text(block_reason)}
                </div>
                """

        elif rooms_open <= 0:

            background = "#f8d7da"

        elif tentative_hold_rooms > 0:

            background = "#ffe5b4"

        elif pending_count > 0:

            background = "#fff3cd"

        elif rooms_open <= 2:

            background = "#fff3cd"

        else:

            background = "#d4edda"

        if pending_count == 1:
            pending_text = "1 pending request"
        else:
            pending_text = f"{pending_count} pending requests"

        calendar_html += f"""
        <td style="
            background-color: {background};
            vertical-align: top;
            height: 92px;
            width: 14%;
            padding: 4px;
        ">

            <strong>{day}</strong><br>

            <small>
                {rooms_open} Rooms Open
            </small><br>

            <small style="color: #856404;">
                {pending_text}
            </small>

            {day_bookings_html}
            {pending_day_html}
            {tentative_hold_html}
            {blocked_reason_html}

        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0 and day != days_in_month:

            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:

        calendar_html += "<td></td>"

        day_counter += 1

    calendar_html += """
        </tr>
    </table>
    """

    today = date.today()

    week_end = today + timedelta(days=7)

    week_view_html = """
    <h2 style="
        margin-top: 28px;
        margin-bottom: 8px;
    ">
        Next 7 Days
    </h2>

    <table border="1"
           cellpadding="5"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               font-size: 12px;
           ">

        <tr style="background-color: #f5f5f5;">
            <th>Day</th>
            <th>Type</th>
            <th>Guest</th>
            <th>Additional Guests</th>
            <th>Room</th>
            <th>View</th>
        </tr>
    """

    current_day = today

    previous_group = ""

    while current_day < week_end:

        day_title = current_day.strftime("%a %m/%d")

        day_has_activity = False

        for booking in approved_bookings:

            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            activity_type = ""

            if booking_start == current_day:

                activity_type = "Arrival"

            elif booking_end == current_day:

                activity_type = "Departure"

            elif booking_start < current_day < booking_end:

                activity_type = "Staying"

            if activity_type:

                day_has_activity = True

                additional_names = booking["additional_names"]

                if not additional_names:

                    additional_names = ""

                current_group = (
                    f"{booking['name']}"
                    f"{activity_type}"
                )

                show_guest = True

                if current_group == previous_group:

                    show_guest = False

                if show_guest:

                    guest_display = f"""
                    <strong>{booking['name']}</strong>
                    """

                    type_display = activity_type

                else:

                    guest_display = ""

                    type_display = ""

                previous_group = current_group

                week_view_html += f"""
                <tr>

                    <td>
                        <strong>{day_title}</strong>
                    </td>

                    <td>{type_display}</td>

                    <td>{guest_display}</td>

                    <td>{additional_names}</td>

                    <td>{booking['room_name']}</td>

                    <td>
                        <a href="/request/{booking['request_id']}">
                            View
                        </a>
                    </td>

                </tr>
                """

        if not day_has_activity:

            week_view_html += f"""
            <tr>

                <td>
                    <strong>{day_title}</strong>
                </td>

                <td colspan="5">
                    No arrivals or departures.
                </td>

            </tr>
            """

        current_day += timedelta(days=1)

    week_view_html += "</table>"

    html = nav_links() + """
    <h1 style="
        margin-bottom: 12px;
    ">
        Dashboard
    </h1>
    """

    action_needed_rows = ""

    def dashboard_action_row(label, count, detail, link, action_label, priority="normal"):

        if priority == "critical":
            row_background = "#f8d7da"
            count_color = "#dc3545"
        elif priority == "warning":
            row_background = "#fff3cd"
            count_color = "#856404"
        else:
            row_background = "#f8f9fa"
            count_color = "#333"

        return f"""
        <tr style="background-color: {row_background};">
            <td>
                <strong>{label}</strong><br>
                <small>{detail}</small>
            </td>
            <td align="center" style="font-size: 18px; font-weight: bold; color: {count_color};">
                {count}
            </td>
            <td>
                <a href="{link}">
                    {action_label}
                </a>
            </td>
        </tr>
        """

    action_needed_rows += dashboard_action_row(
        "Pending Requests",
        len(pending_requests),
        "Request another visits waiting for review, room assignment, approval, or decline.",
        "/requests",
        "Review Requests",
        "warning" if len(pending_requests) > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Room Assignment / Approval Needed",
        room_assignment_attention_count,
        "Converted coordination requests that still need room assignment or approval.",
        "/room-assignments",
        "Assign Rooms",
        "critical" if room_assignment_attention_count > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Coordination Groups Need Attention",
        coordination_attention_count,
        "Groups that need responses, confirmations, capacity review, or booking handoff.",
        "/coordination-groups",
        "Review Coordination",
        "warning" if coordination_attention_count > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Emails Ready To Send",
        emails_need_sending,
        "Requests have approval, decline, cancellation, or update emails waiting.",
        "/requests",
        "Review Emails",
        "critical" if emails_need_sending > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Change Requests",
        change_requests,
        "Guest-requested changes waiting for review.",
        "/requests",
        "Review Changes",
        "warning" if change_requests > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Cancel Requests",
        cancel_requests,
        "Cancellation requests or cancellation email follow-up items.",
        "/requests",
        "Review Cancellations",
        "critical" if cancel_requests > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Booking Audit Warnings",
        audit_problem_count,
        f"{critical_audit_count} critical issue(s). Review before major approvals.",
        "/booking-audit",
        "Open Booking Audit",
        "critical" if critical_audit_count > 0 else ("warning" if audit_problem_count > 0 else "normal")
    )

    action_needed_rows += dashboard_action_row(
        "Profiles Needing Review",
        profiles_needing_review,
        "Guest profiles requiring cleanup or review.",
        "/profiles?filter=needs_review",
        "Review Profiles",
        "warning" if profiles_needing_review > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Invitations Not Sent",
        invitations_not_sent,
        "Draft invitations not yet sent.",
        "/invitations?filter=draft",
        "Review Drafts",
        "warning" if invitations_not_sent > 0 else "normal"
    )

    action_needed_rows += dashboard_action_row(
        "Invitations With No Replies",
        invitations_no_reply,
        "Sent invitations that have not produced a request yet.",
        "/invitations?filter=sent",
        "Follow Up",
        "warning" if invitations_no_reply > 0 else "normal"
    )

    html += f"""
    <h2 style="
        margin-bottom: 8px;
    ">
        Action Needed Now
    </h2>

    <table border="1"
           cellpadding="7"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               font-size: 13px;
               margin-bottom: 10px;
           ">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Action Area</th>
            <th align="center">Count</th>
            <th align="left">Next Step</th>
        </tr>
        {action_needed_rows}
    </table>

    <h2 style="
        margin-bottom: 8px;
    ">
        Coordination Workflow Status
    </h2>
    """

    if not coordination_dashboard_rows:

        html += """
        <p>
            No active coordination groups.
        </p>
        """

    else:

        html += """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
                   margin-bottom: 10px;
               ">

            <tr style="background-color: #f5f5f5;">
                <th align="left">Group</th>
                <th align="left">Status</th>
                <th align="center">Responses</th>
                <th align="left">Best Match</th>
                <th align="center">Unmatched</th>
                <th align="left">Capacity</th>
                <th align="left">Tentative</th>
                <th align="left">Confirmations</th>
                <th align="left">Booking Requests</th>
                <th align="left">Action</th>
            </tr>
        """

        for coordination_row in coordination_dashboard_rows:

            if coordination_row["needs_attention"]:

                row_background = "#fff3cd"

            else:

                row_background = "#e8f7ea"

            html += f"""
            <tr style="background-color: {row_background};">

                <td>
                    <strong>
                        {coordination_row['title']}
                    </strong>
                </td>

                <td>
                    {coordination_row['status']}
                </td>

                <td align="center">
                    {coordination_row['responded_count']}
                    /
                    {coordination_row['member_count']}
                </td>

                <td>
                    {'<br>'.join(coordination_row['top_match_options']) if coordination_row['top_match_options'] else coordination_row['best_match_text']}
                </td>

                <td align="center">
                    {coordination_row['unmatched_count']}
                </td>

                <td>
                    {coordination_row['capacity_status']}
                </td>

                <td>
                    {coordination_row['tentative_status']}
                </td>

                <td>
                    {coordination_row['confirmation_status']}
                </td>

                <td>
                    {coordination_row['booking_handoff_status']}
                </td>

                <td>
                    <a href="/coordination-group/{coordination_row['group_id']}">
                        Review Group
                    </a>
                </td>

            </tr>
            """

        html += """
        </table>
        """


    html += f"""
    <h2 style="
        margin-top: 12px;
        margin-bottom: 8px;
    ">
        Summary Counts
    </h2>

    <table border="1"
           cellpadding="5"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               font-size: 13px;
               margin-bottom: 20px;
           ">

        <tr style="background-color: #f5f5f5;">
            <th align="left">Metric</th>
            <th align="left">Value</th>
        </tr>

        <tr>
            <td>Pending Requests</td>
            <td>
                {len(pending_requests)} requests /
                {total_pending_rooms} rooms /
                {total_pending_nights} nights
            </td>
        </tr>

        <tr>
            <td>Approved Room Bookings</td>
            <td>
                {len(approved_bookings)} bookings /
                {total_approved_nights} room nights
            </td>
        </tr>

        <tr>
            <td>Declined Requests</td>
            <td>{declined_requests}</td>
        </tr>

        <tr>
            <td>Active Profiles</td>
            <td>{active_profiles}</td>
        </tr>

        <tr>
            <td>Archived Profiles</td>
            <td>{archived_profiles}</td>
        </tr>

        <tr>
            <td>House Blocks</td>
            <td>{len(blocked_ranges)}</td>
        </tr>

    </table>
    """

    html += calendar_html

    html += """
    <h2 style="
        margin-top: 28px;
        margin-bottom: 8px;
    ">
        Upcoming Arrivals
    </h2>
    """

    if not upcoming_arrivals:

        html += """
        <p>No upcoming arrivals.</p>
        """

    else:

        html += """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">

            <tr style="background-color: #f5f5f5;">

                <th align="left">Guest</th>
                <th align="left">Additional Guests</th>
                <th align="left">Arrive</th>
                <th align="left">Depart</th>
                <th align="center">Nights</th>
                <th align="left">Room</th>
                <th align="left">View</th>

            </tr>
        """

        previous_group = ""

        for arrival in upcoming_arrivals:

            nights = (
                datetime.strptime(
                    arrival["departure_date"],
                    "%Y-%m-%d"
                )
                - datetime.strptime(
                    arrival["arrival_date"],
                    "%Y-%m-%d"
                )
            ).days

            arrival_short = datetime.strptime(
                arrival["arrival_date"],
                "%Y-%m-%d"
            ).strftime("%m/%d")

            departure_short = datetime.strptime(
                arrival["departure_date"],
                "%Y-%m-%d"
            ).strftime("%m/%d")

            additional_names = safe_text(
                arrival["additional_names"]
            )

            current_group = (
                f"{arrival['name']}"
                f"{arrival['arrival_date']}"
            )

            show_guest = True

            if current_group == previous_group:

                show_guest = False

            if show_guest:

                guest_display = arrival["name"]

                arrival_display = arrival_short

                departure_display = departure_short

                nights_display = nights

            else:

                guest_display = ""

                arrival_display = ""

                departure_display = ""

                nights_display = ""

            previous_group = current_group

            html += f"""
            <tr>

                <td>{guest_display}</td>

                <td>{additional_names}</td>

                <td>{arrival_display}</td>

                <td>{departure_display}</td>

                <td align="center">
                    {nights_display}
                </td>

                <td>{arrival['room_name']}</td>

                <td>
                    <a href="/request/{arrival['request_id']}">
                        View
                    </a>
                </td>

            </tr>
            """

        html += "</table>"

    return html


def audit_routes_and_links():

    route_rules = []

    try:
        for rule in app.url_map.iter_rules():
            route_rules.append({
                "rule": safe_text(rule.rule),
                "endpoint": safe_text(rule.endpoint),
                "methods": sorted([
                    method
                    for method in rule.methods
                    if method not in ("HEAD", "OPTIONS")
                ])
            })
    except Exception as error:
        return {
            "ok": False,
            "problems": [
                "Route audit failed: " + safe_text(error)
            ],
            "routes": []
        }

    problems = []
    route_by_rule = {}
    endpoint_rules = {}

    for route in route_rules:

        rule = route["rule"].rstrip("/") or "/"
        endpoint = route["endpoint"]

        if rule in route_by_rule and route_by_rule[rule] != endpoint:
            problems.append(
                f"Conflicting route rule: {route['rule']}"
            )

        route_by_rule[rule] = endpoint

        if endpoint not in endpoint_rules:
            endpoint_rules[endpoint] = []

        endpoint_rules[endpoint].append(route["rule"])

    for endpoint, rules in endpoint_rules.items():

        normalized_rules = sorted(set(
            rule.rstrip("/") or "/"
            for rule in rules
        ))

        # /path and /path/ are normal aliases and should not warn.
        if len(normalized_rules) > 1:
            problems.append(
                f"Conflicting endpoint name: {endpoint} -> {', '.join(sorted(rules))}"
            )

    required_routes = [
        "/dashboard",
        "/requests",
        "/bookings",
        "/blocked",
        "/coordination-groups",
        "/production-check",
        "/system-health"
    ]

    for required_route in required_routes:

        if required_route not in route_by_rule:
            problems.append(
                f"Missing critical route: {required_route}"
            )

    return {
        "ok": len(problems) == 0,
        "problems": problems,
        "routes": route_rules
    }



def calendar_diagnostics_summary():

    diagnostics = []

    try:

        conn = get_db_connection()

        invitation_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM invitations
        """).fetchone()["count"]

        active_invitation_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM invitations
            WHERE COALESCE(status, '') NOT IN ('cancelled', 'archived', 'closed')
        """).fetchone()["count"]

        approved_booking_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM bookings
            WHERE status = 'approved'
        """).fetchone()["count"]

        blocked_date_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM blocked_dates
        """).fetchone()["count"]

        pending_request_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM booking_requests
            WHERE COALESCE(status, '') IN ('pending', 'submitted', 'change_requested')
        """).fetchone()["count"]

        conn.close()

        diagnostics.append(
            "Invitations: "
            + str(invitation_count)
            + " total / "
            + str(active_invitation_count)
            + " active"
        )

        diagnostics.append(
            "Approved bookings used by calendar capacity: "
            + str(approved_booking_count)
        )

        diagnostics.append(
            "Blocked date records: "
            + str(blocked_date_count)
        )

        diagnostics.append(
            "Pending/change requests: "
            + str(pending_request_count)
        )

        diagnostics.append(
            "Guest calendar JS expected fields: arrival_date, departure_date, rooms_requested"
        )

        diagnostics.append(
            "Diagnostic note: this does not change guest behavior; it only confirms calendar data sources."
        )

        return True, "<br>".join(diagnostics)

    except Exception as error:

        return False, "Calendar diagnostics failed: " + safe_text(error)




def email_template_files_diagnostics_summary():

    try:

        expected_templates = sorted(DEFAULT_EMAIL_TEMPLATES.keys())

        # V30.2: keep template protection read-only, but only enforce
        # placeholders that are truly required for the current stable workflow.
        # Optional sections like change_links_section, base_url, and guest_role
        # should not make Production Check fail just because the wording changed.
        required_placeholders = {
            "invitation.txt": [
                "{{ guest_name }}",
                "{{ request_link }}"
            ]
        }

        existing_templates = []

        if os.path.isdir(EMAIL_TEMPLATE_FOLDER):

            existing_templates = sorted(
                [
                    filename
                    for filename in os.listdir(EMAIL_TEMPLATE_FOLDER)
                    if filename.endswith(".txt")
                ]
            )

        missing_templates = [
            template_name
            for template_name in expected_templates
            if template_name not in existing_templates
        ]

        placeholder_warnings = []

        for template_name, placeholders in required_placeholders.items():

            if template_name in missing_templates:
                continue

            template_path = os.path.join(
                EMAIL_TEMPLATE_FOLDER,
                template_name
            )

            if not os.path.exists(template_path):
                continue

            with open(template_path, "r", encoding="utf-8") as handle:
                template_text = handle.read()

            missing_placeholders = [
                placeholder
                for placeholder in placeholders
                if placeholder not in template_text
            ]

            if missing_placeholders:
                placeholder_warnings.append(
                    template_name
                    + " missing "
                    + ", ".join(missing_placeholders)
                )

        detail_lines = []

        detail_lines.append(
            "Email template folder: "
            + safe_text(EMAIL_TEMPLATE_FOLDER)
        )

        detail_lines.append(
            "Template files found: "
            + str(len(existing_templates))
            + " / "
            + str(len(expected_templates))
        )

        if missing_templates:
            detail_lines.append(
                "Missing files: "
                + ", ".join(missing_templates)
            )

        if placeholder_warnings:
            detail_lines.append(
                "Missing required placeholders: "
                + " | ".join(placeholder_warnings)
            )

        if missing_templates or placeholder_warnings:
            detail_lines.append(
                "Template protection is read-only. Fix template files in GitHub, commit, deploy, then recheck."
            )

            return False, "<br>".join(detail_lines)

        detail_lines.append(
            "Email TXT templates are available. Essential invitation placeholders are present."
        )

        return True, "<br>".join(detail_lines)

    except Exception as error:

        return False, "Email template diagnostics failed: " + safe_text(error)


def route_safety_diagnostics_summary():

    sensitive_routes = [
        "/test-email",
        "/admin/rebuild-email-templates",
        "/admin-backup",
        "/admin-reset-test-data",
        "/approve-cancel/<int:request_id>",
        "/invitation/<int:invitation_id>/status/<new_status>"
    ]

    try:
        route_methods = {}

        for rule in app.url_map.iter_rules():
            route_methods[rule.rule] = set(rule.methods or [])

        missing_post = []

        for route in sensitive_routes:
            methods = route_methods.get(route, set())

            if "POST" not in methods:
                missing_post.append(route)

        if missing_post:
            return False, "Sensitive route(s) missing POST confirmation: " + ", ".join(missing_post)

        return True, "Sensitive admin actions require POST confirmation before side effects."

    except Exception as error:
        return False, "Route safety diagnostics failed: " + safe_text(error)



def database_schema_diagnostics_summary():

    required_columns = {
        "guest_profiles": ["id", "primary_name", "primary_email", "status", "welcome_email_sent_at"],
        "rooms": ["id", "name"],
        "blocked_dates": ["id", "start_date", "end_date", "is_full_block", "rooms_available"],
        "invitations": ["id", "guest_profile_id"],
        "booking_requests": ["id", "name", "email", "arrival_date", "departure_date", "status", "rooms_requested", "invitation_id"],
        "bookings": ["id", "request_id", "room_id", "arrival_date", "departure_date", "status"],
        "coordination_groups": ["id", "title", "status", "tentative_arrival_date", "tentative_departure_date"],
        "coordination_group_members": ["id", "coordination_group_id", "guest_profile_id", "invitation_status"],
        "coordination_date_options": ["id", "coordination_group_member_id", "arrival_date", "departure_date", "rooms_requested"],
        "activity_log": ["id", "action_type", "created_at"],
        "email_log": ["id"]
    }

    conn = None

    try:
        conn = get_db_connection()
        existing_tables = set(
            row["name"]
            for row in conn.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
            """).fetchall()
        )

        problems = []

        for table_name, columns in required_columns.items():

            if table_name not in existing_tables:
                problems.append(f"missing table {table_name}")
                continue

            table_columns = set(
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            )

            for column_name in columns:
                if column_name not in table_columns:
                    problems.append(f"{table_name}.{column_name}")

        if problems:
            return False, "Schema issue(s): " + ", ".join(problems)

        return True, "Critical database tables and columns are present, including partial house-block capacity fields."

    except Exception as error:
        return False, "Database schema diagnostics failed: " + safe_text(error)

    finally:
        if conn:
            conn.close()


def critical_guest_route_diagnostics_summary():

    required_routes = [
        "/invite/<int:invitation_id>",
        "/invitation/<int:invitation_id>/request",
        "/new-request",
        "/submit",
        "/request/<int:request_id>/submitted",
        "/request-submitted/complete",
        "/request/<int:request_id>/all-reservations",
        "/request/<request_id>/change",
        "/request/<int:request_id>/change/",
        "/request/<int:request_id>/change",
        "/request/<int:request_id>/cancel/",
        "/request/<int:request_id>/cancel",
        "/coordination-group-member/<int:member_id>/request",
        "/coordination-group-member/<int:member_id>/organizer-planning",
        "/coordination-group-member/<int:member_id>/date-options",
        "/coordination-group-member/<int:member_id>/date-options/thanks",
        "/coordination-group-member/<int:member_id>/cannot-change-dates",
        "/coordination-group-member/<int:member_id>/clear-date-options",
        "/coordination-group-member/<int:member_id>/follow-up-dates-work",
        "/coordination-group-member/<int:member_id>/tentative-response",
        "/coordination-group-member/<int:member_id>/tentative-response/thanks"
    ]

    try:
        routes = set(rule.rule for rule in app.url_map.iter_rules())
        missing = [route for route in required_routes if route not in routes]

        if missing:
            return False, "Missing guest route(s): " + ", ".join(missing)

        return True, "Critical guest-facing invitation, request, change, cancel, and coordination routes are registered."

    except Exception as error:
        return False, "Guest route diagnostics failed: " + safe_text(error)


def email_header_asset_diagnostics_summary():

    try:
        header_path = os.path.join(
            app.root_path,
            "shore_home_header.jpeg"
        )

        routes = set(rule.rule for rule in app.url_map.iter_rules())

        problems = []

        if "/shore_home_header.jpeg" not in routes:
            problems.append("missing /shore_home_header.jpeg route")

        if not os.path.exists(header_path):
            problems.append("missing shore_home_header.jpeg in app root")

        if problems:
            return False, "; ".join(problems)

        return True, "Email header image exists and the public image route is registered."

    except Exception as error:
        return False, "Email header asset diagnostics failed: " + safe_text(error)


def booking_consistency_diagnostics_summary():

    conn = None

    try:
        conn = get_db_connection()

        mismatches = conn.execute("""
            SELECT
                booking_requests.id,
                booking_requests.name,
                COALESCE(booking_requests.rooms_requested, 1) AS rooms_requested,
                COUNT(bookings.id) AS approved_booking_rows
            FROM booking_requests
            LEFT JOIN bookings
                ON booking_requests.id = bookings.request_id
               AND bookings.status = 'approved'
            WHERE booking_requests.status = 'approved'
            GROUP BY booking_requests.id
            HAVING approved_booking_rows != rooms_requested
            ORDER BY booking_requests.id
        """).fetchall()

        if mismatches:
            details = []

            for row in mismatches[:8]:
                details.append(
                    f"Request {row['id']} {safe_text(row['name'])}: approved rooms {row['rooms_requested']} / booking rows {row['approved_booking_rows']}"
                )

            return False, "Booking mismatch detected: " + " | ".join(details)

        return True, "Approved booking requests match approved booking rows."

    except Exception as error:
        return False, "Booking consistency diagnostics failed: " + safe_text(error)

    finally:
        if conn:
            conn.close()


def reset_tool_contract_diagnostics_summary():

    try:
        preserved_tables = [
            "guest_profiles",
            "rooms",
            "blocked_dates"
        ]

        operational_tables = [
            "activity_log",
            "email_log",
            "invitations",
            "booking_requests",
            "bookings",
            "coordination_date_options",
            "coordination_group_members",
            "coordination_groups"
        ]

        route_methods = {}
        for rule in app.url_map.iter_rules():
            route_methods[rule.rule] = set(rule.methods or [])

        if "POST" not in route_methods.get("/admin-reset-test-data", set()):
            return False, "Reset Test Data route does not require POST."

        return True, (
            "Reset Test Data contract loaded: backup outward only; preserve "
            + ", ".join(preserved_tables)
            + "; clear operational tables "
            + ", ".join(operational_tables)
            + "."
        )

    except Exception as error:
        return False, "Reset tool diagnostics failed: " + safe_text(error)


@app.route("/production-check")
def production_check():

    checks = []

    database_exists = os.path.exists(DATABASE_FILE)
    checks.append((
        "Database file",
        database_exists,
        DATABASE_FILE if database_exists else f"Not found: {DATABASE_FILE}"
    ))

    backup_folder = "backups"

    try:
        os.makedirs(
            backup_folder,
            exist_ok=True
        )
        test_file = os.path.join(
            backup_folder,
            ".write_test"
        )
        with open(test_file, "w") as handle:
            handle.write("ok")
        os.remove(test_file)
        backup_ok = True
        backup_detail = f"Writable: {backup_folder}"
    except Exception as error:
        backup_ok = False
        backup_detail = safe_text(error)

    checks.append((
        "Backup folder",
        backup_ok,
        backup_detail
    ))

    checks.append((
        "Base URL",
        bool(BASE_URL),
        BASE_URL or "BASE_URL is missing"
    ))

    checks.append((
        "Email address",
        bool(EMAIL_ADDRESS),
        EMAIL_ADDRESS or "EMAIL_ADDRESS is missing"
    ))

    checks.append((
        "Email app password",
        bool(EMAIL_APP_PASSWORD),
        "Configured" if EMAIL_APP_PASSWORD else "EMAIL_APP_PASSWORD is missing; sending real emails will fail."
    ))

    checks.append((
        "Public access protection",
        ADMIN_AUTH_ENABLED,
        "Admin login protection configured." if ADMIN_AUTH_ENABLED else "ADMIN_PASSWORD is missing; admin pages are not protected."
    ))

    route_safety_ok, route_safety_detail = route_safety_diagnostics_summary()

    checks.append((
        "Route Safety",
        route_safety_ok,
        route_safety_detail
    ))

    schema_diag_ok, schema_diag_detail = database_schema_diagnostics_summary()

    checks.append((
        "Database Schema",
        schema_diag_ok,
        schema_diag_detail
    ))

    guest_route_diag_ok, guest_route_diag_detail = critical_guest_route_diagnostics_summary()

    checks.append((
        "Guest Route Coverage",
        guest_route_diag_ok,
        guest_route_diag_detail
    ))

    header_diag_ok, header_diag_detail = email_header_asset_diagnostics_summary()

    checks.append((
        "Email Header Asset",
        header_diag_ok,
        header_diag_detail
    ))

    booking_diag_ok, booking_diag_detail = booking_consistency_diagnostics_summary()

    checks.append((
        "Booking Consistency",
        booking_diag_ok,
        booking_diag_detail
    ))

    reset_diag_ok, reset_diag_detail = reset_tool_contract_diagnostics_summary()

    checks.append((
        "Reset Tool Contract",
        reset_diag_ok,
        reset_diag_detail
    ))


    calendar_diag_ok, calendar_diag_detail = calendar_diagnostics_summary()

    checks.append((
        "Calendar Diagnostics",
        calendar_diag_ok,
        calendar_diag_detail
    ))


    email_template_diag_ok, email_template_diag_detail = email_template_files_diagnostics_summary()

    checks.append((
        "Email Template Files",
        email_template_diag_ok,
        email_template_diag_detail
    ))

    rows = ""

    for label, ok, detail in checks:
        rows += production_status_row(
            label,
            ok,
            detail
        )

    return f"""
    {nav_links()}

    <h1>Production Check</h1>

    <p>
        This page checks the minimum operational items before using the app for real guests.
    </p>

    <table border="1"
           cellpadding="6"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               max-width: 980px;
           ">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Area</th>
            <th align="left">Status</th>
            <th align="left">Details</th>
        </tr>
        {rows}
    </table>

    <h2>Production Safety Notes</h2>

    <ul>
        <li>Create an admin backup before major workflow testing.</li>
        <li>Do not expose this app on the public internet without login protection.</li>
        <li>Use the Booking Audit and Status Sanity pages before approving many requests.</li>
        <li>Test one full request, approval, change, cancel, and coordination flow before real use.</li>
    </ul>

    <p>
        <a href="/production-checklist">
            Open Production Checklist / Restore Notes
        </a>
    </p>
    """


@app.route("/production-checklist")
def production_checklist():

    return f"""
    {nav_links()}

    <h1>Production Checklist / Restore Notes</h1>

    <h2>Before Real Use</h2>

    <ol>
        <li>Run Admin Backup.</li>
        <li>Open Production Check and resolve anything marked Needs Attention.</li>
        <li>Open Booking Audit and confirm there are no critical issues.</li>
        <li>Run one normal request approval test.</li>
        <li>Run one change request test.</li>
        <li>Run one cancellation test.</li>
        <li>Run one coordination group test through booking handoff.</li>
    </ol>

    <h2>Manual Restore Reminder</h2>

    <p>
        Backups are database files in the backups folder. To restore, stop Flask,
        copy the desired backup database over the active database file, then restart Flask.
    </p>

    <p style="color: red; font-weight: bold;">
        Always make a copy of the current database before restoring an older backup.
    </p>
    """



# -----------------------------------------------------------------------------
# Backup & Recovery Hardening
# Phase 1: Full Recovery Backup creation + validation.
# Phase 2: Restore Wizard with validation and pre-restore safety backup.
# -----------------------------------------------------------------------------

REQUIRED_BACKUP_TABLES = [
    "guest_profiles",
    "booking_requests",
    "bookings",
    "invitations",
    "coordination_groups",
    "coordination_group_members",
    "rooms",
    "blocked_dates",
]


def backup_root_folder():

    return os.path.join(
        app.root_path,
        "backups"
    )


def backup_display_path(backup_name=""):

    if backup_name:
        return os.path.join(
            "shore_home_app",
            "backups",
            backup_name
        )

    return os.path.join(
        "shore_home_app",
        "backups"
    )


def file_sha256(path):

    import hashlib

    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def directory_size_bytes(path):

    total = 0

    if not os.path.exists(path):
        return total

    for folder, _dirs, files in os.walk(path):
        for filename in files:
            full_path = os.path.join(folder, filename)
            try:
                total += os.path.getsize(full_path)
            except Exception:
                pass

    return total


def human_file_size(size_bytes):

    try:
        size = float(size_bytes)
    except Exception:
        return "0 B"

    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size = size / 1024

    return f"{size_bytes} B"


def database_backup_summary(db_path):

    summary = {
        "readable": False,
        "tables": {},
        "missing_tables": [],
        "error": ""
    }

    try:
        db_conn = sqlite3.connect(db_path)
        db_conn.row_factory = sqlite3.Row

        existing_tables = set(
            row["name"] for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        )

        for table_name in REQUIRED_BACKUP_TABLES:
            if table_name not in existing_tables:
                summary["missing_tables"].append(table_name)
                summary["tables"][table_name] = None
                continue

            try:
                count = db_conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table_name}"
                ).fetchone()["count"]
                summary["tables"][table_name] = count
            except Exception as table_error:
                summary["tables"][table_name] = f"ERROR: {safe_text(table_error)}"

        db_conn.close()
        summary["readable"] = True

    except Exception as error:
        summary["error"] = safe_text(error)

    return summary


def template_file_list():

    template_folder = EMAIL_TEMPLATE_FOLDER
    files = []

    if os.path.isdir(template_folder):
        for filename in sorted(os.listdir(template_folder)):
            if filename.endswith(".txt"):
                files.append(filename)

    return files



def profile_photos_folder():

    return os.path.join(
        app.root_path,
        "static",
        "profile_photos"
    )


def profile_photo_file_list():

    folder = profile_photos_folder()
    files = []

    if os.path.isdir(folder):
        for filename in sorted(os.listdir(folder)):
            if filename.startswith("."):
                continue
            full_path = os.path.join(folder, filename)
            if os.path.isfile(full_path):
                files.append(filename)

    return files


def guest_profile_photo_references(db_path=None):

    references = []
    target_db = db_path or DATABASE_FILE

    try:
        conn = sqlite3.connect(target_db)
        conn.row_factory = sqlite3.Row

        existing_tables = set(
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        )

        if "guest_profiles" not in existing_tables:
            conn.close()
            return references

        rows = conn.execute("""
            SELECT primary_name, primary_email, photo_path
            FROM guest_profiles
            WHERE COALESCE(photo_path, '') <> ''
            ORDER BY primary_name
        """).fetchall()

        for row in rows:
            references.append({
                "primary_name": safe_text(row["primary_name"]),
                "primary_email": safe_text(row["primary_email"]),
                "photo_path": safe_text(row["photo_path"]),
            })

        conn.close()
    except Exception:
        pass

    return references


def missing_profile_photo_references(photo_folder=None, db_path=None):

    folder = photo_folder or profile_photos_folder()
    missing = []

    for reference in guest_profile_photo_references(db_path):
        photo_path = safe_text(reference.get("photo_path")).strip()
        if not photo_path:
            continue

        filename = os.path.basename(photo_path)

        # Stored values may be either "filename.jpeg" or "profile_photos/filename.jpeg".
        candidate_paths = [
            os.path.join(folder, filename),
            os.path.join(app.root_path, photo_path.lstrip("/")),
        ]

        if not any(os.path.exists(path) for path in candidate_paths):
            missing.append(reference)

    return missing


def copy_profile_photos_to_backup(destination_folder):

    copied = []
    source_folder = profile_photos_folder()

    os.makedirs(destination_folder, exist_ok=True)

    if os.path.isdir(source_folder):
        for filename in profile_photo_file_list():
            source = os.path.join(source_folder, filename)
            destination = os.path.join(destination_folder, filename)
            shutil.copy2(source, destination)
            copied.append(filename)

    return copied


def restore_profile_photos_from_backup(source_folder):

    restored = []

    if not os.path.isdir(source_folder):
        return restored

    destination_folder = profile_photos_folder()
    os.makedirs(destination_folder, exist_ok=True)

    for filename in sorted(os.listdir(source_folder)):
        if filename.startswith("."):
            continue
        source = os.path.join(source_folder, filename)
        if not os.path.isfile(source):
            continue
        shutil.copy2(source, os.path.join(destination_folder, filename))
        restored.append(filename)

    return restored

def backup_preview_details():

    source_db = DATABASE_FILE
    db_exists = os.path.exists(source_db)
    db_size = os.path.getsize(source_db) if db_exists else 0
    templates = template_file_list()
    photos = profile_photo_file_list()
    missing_photos = missing_profile_photo_references()

    app_files = []
    for filename in ["app.py", "database.py", "requirements.txt"]:
        app_files.append({
            "name": filename,
            "exists": os.path.exists(os.path.join(app.root_path, filename))
        })

    return {
        "backup_root": backup_root_folder(),
        "display_root": backup_display_path(),
        "database_file": source_db,
        "database_exists": db_exists,
        "database_size": db_size,
        "templates": templates,
        "profile_photos": photos,
        "missing_photo_references": missing_photos,
        "app_files": app_files,
        "app_version": APP_VERSION,
        "created_preview_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_text_file(path, text):

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def copy_if_exists(source, destination):

    if os.path.exists(source):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        return True

    return False


def create_full_recovery_backup():

    import json
    import zipfile

    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M")
    backup_name = f"ShoreHome_Backup_{timestamp}"
    root_folder = backup_root_folder()
    backup_folder = os.path.join(root_folder, backup_name)

    if os.path.exists(backup_folder):
        timestamp = datetime.now().strftime("%Y_%m_%d_%H%M_%S")
        backup_name = f"ShoreHome_Backup_{timestamp}"
        backup_folder = os.path.join(root_folder, backup_name)

    os.makedirs(backup_folder, exist_ok=False)

    app_folder = os.path.join(backup_folder, "app")
    templates_folder = os.path.join(backup_folder, "templates", "emails")
    photos_folder = os.path.join(backup_folder, "static", "profile_photos")
    data_folder = os.path.join(backup_folder, "data")
    metadata_folder = os.path.join(backup_folder, "metadata")

    os.makedirs(app_folder, exist_ok=True)
    os.makedirs(templates_folder, exist_ok=True)
    os.makedirs(photos_folder, exist_ok=True)
    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(metadata_folder, exist_ok=True)

    copied_app_files = []
    missing_app_files = []

    for filename in ["app.py", "database.py", "requirements.txt"]:
        source = os.path.join(app.root_path, filename)
        destination = os.path.join(app_folder, filename)
        if copy_if_exists(source, destination):
            copied_app_files.append(filename)
        else:
            missing_app_files.append(filename)

    copied_templates = []

    if os.path.isdir(EMAIL_TEMPLATE_FOLDER):
        for filename in sorted(os.listdir(EMAIL_TEMPLATE_FOLDER)):
            if not filename.endswith(".txt"):
                continue
            source = os.path.join(EMAIL_TEMPLATE_FOLDER, filename)
            destination = os.path.join(templates_folder, filename)
            shutil.copy2(source, destination)
            copied_templates.append(filename)

    copied_profile_photos = copy_profile_photos_to_backup(photos_folder)

    source_db = DATABASE_FILE
    backup_db_path = os.path.join(data_folder, "shore_home.db")

    if not os.path.exists(source_db):
        raise RuntimeError(
            "Full backup failed because the active database file does not exist: "
            + safe_text(source_db)
        )

    shutil.copy2(source_db, backup_db_path)

    db_summary = database_backup_summary(backup_db_path)
    backup_missing_photo_refs = missing_profile_photo_references(photos_folder, backup_db_path)
    db_checksum = file_sha256(backup_db_path)

    validation_errors = []

    if not os.path.exists(backup_db_path):
        validation_errors.append("Database was not copied.")

    if not db_summary["readable"]:
        validation_errors.append("Database backup is not readable: " + safe_text(db_summary.get("error")))

    for missing_table in db_summary["missing_tables"]:
        validation_errors.append("Missing required table: " + safe_text(missing_table))

    if not copied_templates:
        validation_errors.append("No email template TXT files were copied.")

    if backup_missing_photo_refs:
        validation_errors.append(
            "Missing profile photo file(s): "
            + ", ".join(safe_text(item.get("photo_path")) for item in backup_missing_photo_refs)
        )

    if "app.py" not in copied_app_files:
        validation_errors.append("app.py was not copied.")

    if "database.py" not in copied_app_files:
        validation_errors.append("database.py was not copied.")

    status = "VERIFIED" if not validation_errors else "FAILED"

    manifest = {
        "backup_name": backup_name,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": APP_VERSION,
        "database_source": source_db,
        "database_backup_path": os.path.join("data", "shore_home.db"),
        "database_sha256": db_checksum,
        "database_size_bytes": os.path.getsize(backup_db_path),
        "copied_app_files": copied_app_files,
        "missing_app_files": missing_app_files,
        "copied_templates": copied_templates,
        "copied_profile_photos": copied_profile_photos,
        "missing_profile_photo_references": backup_missing_photo_refs,
        "required_tables": REQUIRED_BACKUP_TABLES,
        "table_counts": db_summary["tables"],
        "status": status,
        "validation_errors": validation_errors,
    }

    write_text_file(
        os.path.join(metadata_folder, "app_version.txt"),
        APP_VERSION + "\n"
    )

    write_text_file(
        os.path.join(metadata_folder, "created_at.txt"),
        manifest["created_at"] + "\n"
    )

    db_summary_lines = [
        "Database Summary",
        "================",
        "Source: " + safe_text(source_db),
        "Backup: data/shore_home.db",
        "SHA256: " + safe_text(db_checksum),
        "Size: " + human_file_size(manifest["database_size_bytes"]),
        "",
        "Table Counts:",
    ]

    for table_name in REQUIRED_BACKUP_TABLES:
        db_summary_lines.append(
            f"- {table_name}: {db_summary['tables'].get(table_name)}"
        )

    write_text_file(
        os.path.join(metadata_folder, "database_summary.txt"),
        "\n".join(db_summary_lines) + "\n"
    )

    write_text_file(
        os.path.join(metadata_folder, "checksum.sha256"),
        db_checksum + "  data/shore_home.db\n"
    )

    restore_notes = f"""Shore Home Full Recovery Backup
Created: {manifest['created_at']}
App Version: {APP_VERSION}
Status: {status}

This backup contains the application files, email templates, profile photos, and the active SQLite database.
Restore supports the database, email templates, and profile photos.

Database included:
data/shore_home.db

Profile photos included:
static/profile_photos/

Original database path:
{source_db}

Before any future restore, create a new pre-restore backup first.
"""

    write_text_file(
        os.path.join(backup_folder, "restore_notes.txt"),
        restore_notes
    )

    validation_report_lines = [
        "Shore Home Backup Validation Report",
        "===================================",
        "Status: " + status,
        "Created: " + manifest["created_at"],
        "Backup: " + backup_name,
        "Database included: YES",
        "Database readable: " + ("YES" if db_summary["readable"] else "NO"),
        "Templates copied: " + str(len(copied_templates)),
        "Profile photos copied: " + str(len(copied_profile_photos)),
        "Missing profile photo references: " + str(len(backup_missing_photo_refs)),
        "App files copied: " + ", ".join(copied_app_files),
        "",
        "Table Counts:",
    ]

    for table_name in REQUIRED_BACKUP_TABLES:
        validation_report_lines.append(
            f"- {table_name}: {db_summary['tables'].get(table_name)}"
        )

    if validation_errors:
        validation_report_lines.extend(["", "Validation Errors:"])
        validation_report_lines.extend("- " + error for error in validation_errors)
    else:
        validation_report_lines.extend(["", "Validation Errors: none"])

    write_text_file(
        os.path.join(backup_folder, "validation_report.txt"),
        "\n".join(validation_report_lines) + "\n"
    )

    write_text_file(
        os.path.join(backup_folder, "manifest.json"),
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    zip_path = os.path.join(root_folder, backup_name + ".zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _dirs, files in os.walk(backup_folder):
            for filename in files:
                full_path = os.path.join(folder, filename)
                archive_name = os.path.relpath(full_path, root_folder)
                archive.write(full_path, archive_name)

    manifest["backup_folder"] = backup_folder
    manifest["backup_zip"] = zip_path
    manifest["backup_zip_name"] = backup_name + ".zip"
    manifest["folder_size_bytes"] = directory_size_bytes(backup_folder)
    manifest["zip_size_bytes"] = os.path.getsize(zip_path)

    # Update manifest after ZIP details are known.
    write_text_file(
        os.path.join(backup_folder, "manifest.json"),
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    return manifest


@app.route("/admin-backup", methods=["GET", "POST"])
def admin_backup():

    if request.method != "POST":
        details = backup_preview_details()

        app_file_rows = "".join(
            f"<li>{'✓' if item['exists'] else '⚠'} {safe_text(item['name'])}</li>"
            for item in details["app_files"]
        )

        template_rows = "".join(
            f"<li>✓ {safe_text(filename)}</li>"
            for filename in details["templates"]
        )

        if not template_rows:
            template_rows = "<li style='color:red;'>No TXT templates found.</li>"

        photo_rows = "".join(
            f"<li>✓ {safe_text(filename)}</li>"
            for filename in details["profile_photos"]
        )

        if not photo_rows:
            photo_rows = "<li style='color:#856404;'>No profile photo files found.</li>"

        missing_photo_rows = "".join(
            f"<li>{safe_text(item.get('primary_name'))}: {safe_text(item.get('photo_path'))}</li>"
            for item in details["missing_photo_references"]
        )

        if not missing_photo_rows:
            missing_photo_rows = "<li>None</li>"

        db_status = "✓ Found" if details["database_exists"] else "⚠ Missing"

        return f"""
        {nav_links()}

        <h1>Backup & Recovery</h1>

        <div style="border:2px solid #d8e6f3; background:#f8fbff; padding:12px; border-radius:10px; max-width:900px; margin-bottom:16px;">
            <h2 style="margin-top:0;">Recovery Actions</h2>
            <p><a href="/admin-restore-backup" style="font-weight:bold;">Restore Full Backup</a></p>
            <p><a href="/admin-selective-data-recovery" style="font-weight:bold; color:#0f4c81;">Restore Guest Profiles & Rooms</a></p>
        </div>

        <h2>Phase 1 — Full Recovery Backup</h2>

        <div style="border:2px solid #0f4c81; background:#f8fbff; padding:14px; border-radius:10px; max-width:900px;">
            <p style="font-weight:bold; margin-top:0;">
                This creates a full recovery backup folder under <code>{safe_text(details['display_root'])}</code>.
            </p>

            <p>
                <strong>App Version:</strong> {safe_text(details['app_version'])}<br>
                <strong>Database:</strong> {safe_text(details['database_file'])}<br>
                <strong>Database Status:</strong> {db_status}<br>
                <strong>Database Size:</strong> {human_file_size(details['database_size'])}<br>
                <strong>Email Templates:</strong> {len(details['templates'])}<br>
                <strong>Profile Photos:</strong> {len(details['profile_photos'])}<br>
                <strong>Missing Photo References:</strong> {len(details['missing_photo_references'])}
            </p>
        </div>

        <h2>Preview Contents</h2>

        <h3>Application Files</h3>
        <ul>{app_file_rows}</ul>

        <h3>Email Templates</h3>
        <ul>{template_rows}</ul>

        <h3>Profile Photos</h3>
        <ul>{photo_rows}</ul>

        <h3>Missing Photo References</h3>
        <ul>{missing_photo_rows}</ul>

        <h3>Database</h3>
        <ul>
            <li>{db_status} active SQLite database</li>
            <li>Backup copy name: <code>data/shore_home.db</code></li>
        </ul>

        <h3>Metadata</h3>
        <ul>
            <li>manifest.json</li>
            <li>restore_notes.txt</li>
            <li>validation_report.txt</li>
            <li>metadata/checksum.sha256</li>
            <li>metadata/database_summary.txt</li>
        </ul>

        <form method="POST">
            <button type="submit" style="font-weight:bold; padding:10px 16px; background:#198754; color:white; border:0; border-radius:8px;">
                Create Full Recovery Backup
            </button>
            &nbsp;
            <a href="/dashboard">Cancel</a>
        </form>
        """

    try:
        manifest = create_full_recovery_backup()
    except Exception as error:
        return f"""
        {nav_links()}

        <h1>Backup Failed</h1>

        <p style="color:red; font-weight:bold;">
            {safe_text(error)}
        </p>

        <p><a href="/admin-backup">Back to Backup Preview</a></p>
        """, 500

    status = safe_text(manifest.get("status"))
    status_color = "green" if status == "VERIFIED" else "red"

    table_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in manifest.get("table_counts", {}).items()
    )

    error_rows = "".join(
        f"<li>{safe_text(error)}</li>"
        for error in manifest.get("validation_errors", [])
    )

    if not error_rows:
        error_rows = "<li>None</li>"

    download_name = safe_text(manifest.get("backup_zip_name"))

    return f"""
    {nav_links()}

    <h1>Backup Complete</h1>

    <p style="color:{status_color}; font-weight:bold; font-size:18px;">
        Status: {status}
    </p>

    <p>
        <strong>Backup Folder:</strong><br>
        <code>{safe_text(backup_display_path(manifest.get('backup_name', '')))}</code>
    </p>

    <p>
        <strong>Download ZIP:</strong><br>
        <a href="/admin-backup/download/{download_name}">{download_name}</a>
    </p>

    <p>
        <strong>Database Included:</strong> YES<br>
        <strong>Database Size:</strong> {human_file_size(manifest.get('database_size_bytes', 0))}<br>
        <strong>ZIP Size:</strong> {human_file_size(manifest.get('zip_size_bytes', 0))}<br>
        <strong>Templates Copied:</strong> {len(manifest.get('copied_templates', []))}<br>
        <strong>Profile Photos Copied:</strong> {len(manifest.get('copied_profile_photos', []))}<br>
        <strong>Missing Photo References:</strong> {len(manifest.get('missing_profile_photo_references', []))}
    </p>

    <h2>Table Counts</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows</th></tr>
        {table_rows}
    </table>

    <h2>Validation Errors</h2>
    <ul>{error_rows}</ul>

    <p><a href="/admin-restore-backup">Restore Backup</a> | <a href="/dashboard">Back to Dashboard</a></p>
    """


@app.route("/admin-backup/download/<backup_zip_name>")
def admin_backup_download(backup_zip_name):

    safe_name = os.path.basename(safe_text(backup_zip_name))

    if not safe_name.startswith("ShoreHome_Backup_") or not safe_name.endswith(".zip"):
        return "Invalid backup file.", 400

    return send_from_directory(
        backup_root_folder(),
        safe_name,
        as_attachment=True
    )


# -----------------------------------------------------------------------------
# Phase 2 Backup & Recovery Hardening
# Restore Wizard: upload backup ZIP, validate, preview, pre-restore backup, restore.
# -----------------------------------------------------------------------------


def restore_work_root():

    return os.path.join(
        backup_root_folder(),
        "restore_uploads"
    )


def safe_restore_token():

    return secrets.token_urlsafe(16).replace("-", "_").replace(".", "_")


def extract_backup_zip_for_restore(zip_path, token):

    import zipfile

    work_root = restore_work_root()
    os.makedirs(work_root, exist_ok=True)

    restore_folder = os.path.join(work_root, token)

    if os.path.exists(restore_folder):
        shutil.rmtree(restore_folder)

    os.makedirs(restore_folder, exist_ok=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        # Safe extraction: block zip-slip/path traversal entries.
        restore_folder_abs = os.path.abspath(restore_folder)

        for member in archive.infolist():
            member_path = os.path.abspath(os.path.join(restore_folder, member.filename))
            if not (member_path == restore_folder_abs or member_path.startswith(restore_folder_abs + os.sep)):
                raise RuntimeError("Unsafe backup ZIP path blocked: " + safe_text(member.filename))

        archive.extractall(restore_folder)

    # Phase 1 zips store everything under ShoreHome_Backup_.../
    candidates = []

    for name in os.listdir(restore_folder):
        full_path = os.path.join(restore_folder, name)
        if os.path.isdir(full_path) and name.startswith("ShoreHome_Backup_"):
            candidates.append(full_path)

    if candidates:
        return candidates[0]

    return restore_folder


def validate_restore_backup_folder(extracted_root):

    import json

    result = {
        "valid": False,
        "errors": [],
        "manifest": {},
        "table_counts": {},
        "template_count": 0,
        "profile_photo_count": 0,
        "missing_profile_photo_references": [],
        "database_size_bytes": 0,
        "database_path": "",
        "backup_name": os.path.basename(extracted_root),
    }

    manifest_path = os.path.join(extracted_root, "manifest.json")
    restore_notes_path = os.path.join(extracted_root, "restore_notes.txt")
    validation_report_path = os.path.join(extracted_root, "validation_report.txt")
    db_path = os.path.join(extracted_root, "data", "shore_home.db")
    templates_path = os.path.join(extracted_root, "templates", "emails")
    photos_path = os.path.join(extracted_root, "static", "profile_photos")

    if not os.path.exists(manifest_path):
        result["errors"].append("manifest.json is missing.")
    else:
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                result["manifest"] = json.load(handle)
        except Exception as error:
            result["errors"].append("manifest.json is not readable: " + safe_text(error))

    if not os.path.exists(restore_notes_path):
        result["errors"].append("restore_notes.txt is missing.")

    if not os.path.exists(validation_report_path):
        result["errors"].append("validation_report.txt is missing.")

    if not os.path.exists(db_path):
        result["errors"].append("data/shore_home.db is missing.")
    else:
        result["database_path"] = db_path
        result["database_size_bytes"] = os.path.getsize(db_path)
        db_summary = database_backup_summary(db_path)
        result["table_counts"] = db_summary.get("tables", {})

        if not db_summary.get("readable"):
            result["errors"].append("Backup database is not readable: " + safe_text(db_summary.get("error")))

        for missing_table in db_summary.get("missing_tables", []):
            result["errors"].append("Backup database missing required table: " + safe_text(missing_table))

    if not os.path.isdir(templates_path):
        result["errors"].append("templates/emails folder is missing.")
    else:
        result["template_count"] = len([
            name for name in os.listdir(templates_path)
            if name.endswith(".txt")
        ])
        if result["template_count"] == 0:
            result["errors"].append("No email TXT templates found in backup.")

    if os.path.isdir(photos_path):
        result["profile_photo_count"] = len([
            name for name in os.listdir(photos_path)
            if not name.startswith(".") and os.path.isfile(os.path.join(photos_path, name))
        ])

    if result.get("database_path"):
        result["missing_profile_photo_references"] = missing_profile_photo_references(
            photos_path,
            result.get("database_path")
        )
        if result["missing_profile_photo_references"]:
            result["errors"].append(
                "Backup has guest profile photo references without matching files: "
                + ", ".join(safe_text(item.get("photo_path")) for item in result["missing_profile_photo_references"])
            )

    result["valid"] = not result["errors"]
    return result


def restore_from_validated_backup(extracted_root):

    source_db = os.path.join(extracted_root, "data", "shore_home.db")
    source_templates = os.path.join(extracted_root, "templates", "emails")
    source_photos = os.path.join(extracted_root, "static", "profile_photos")

    if not os.path.exists(source_db):
        raise RuntimeError("Restore blocked: backup database is missing.")

    validation = validate_restore_backup_folder(extracted_root)

    if not validation["valid"]:
        raise RuntimeError("Restore blocked: backup validation failed.")

    # Create a full safety backup before touching production data.
    safety_manifest = create_full_recovery_backup()

    if safe_text(safety_manifest.get("status")) != "VERIFIED":
        raise RuntimeError("Restore blocked: pre-restore safety backup failed validation.")

    os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)
    shutil.copy2(source_db, DATABASE_FILE)

    restored_templates = []

    if os.path.isdir(source_templates):
        os.makedirs(EMAIL_TEMPLATE_FOLDER, exist_ok=True)
        for filename in sorted(os.listdir(source_templates)):
            if not filename.endswith(".txt"):
                continue
            shutil.copy2(
                os.path.join(source_templates, filename),
                os.path.join(EMAIL_TEMPLATE_FOLDER, filename)
            )
            restored_templates.append(filename)

    restored_profile_photos = restore_profile_photos_from_backup(source_photos)

    restored_db_summary = database_backup_summary(DATABASE_FILE)

    return {
        "safety_backup_name": safety_manifest.get("backup_name"),
        "safety_backup_zip_name": safety_manifest.get("backup_zip_name"),
        "restored_database_size_bytes": os.path.getsize(DATABASE_FILE),
        "restored_templates": restored_templates,
        "restored_profile_photos": restored_profile_photos,
        "missing_profile_photo_references": missing_profile_photo_references(),
        "restored_table_counts": restored_db_summary.get("tables", {}),
    }


@app.route("/admin-restore-backup", methods=["GET", "POST"])
def admin_restore_backup():

    if request.method != "POST":
        return f"""
        {nav_links()}

        <h1>Phase 2 — Restore Backup</h1>

        <div style="border:2px solid #fd7e14; background:#fff8ef; padding:14px; border-radius:10px; max-width:900px;">
            <p style="font-weight:bold; margin-top:0;">
                Restore is guarded. A backup ZIP must validate before restore is available.
            </p>
            <p>
                Before restore runs, Shore Home automatically creates a new full pre-restore safety backup.
            </p>
        </div>

        <h2>Select Backup ZIP</h2>

        <form method="POST" enctype="multipart/form-data">
            <p>
                <input type="file" name="backup_zip" accept=".zip" required>
            </p>
            <button type="submit" style="font-weight:bold; padding:10px 16px; background:#0f4c81; color:white; border:0; border-radius:8px;">
                Upload and Validate Backup
            </button>
            &nbsp;
            <a href="/admin-backup">Cancel</a>
        </form>
        """

    upload = request.files.get("backup_zip")

    if not upload or not safe_text(upload.filename).lower().endswith(".zip"):
        return "Backup ZIP is required.", 400

    token = safe_restore_token()
    upload_folder = restore_work_root()
    os.makedirs(upload_folder, exist_ok=True)

    safe_filename = os.path.basename(safe_text(upload.filename))
    saved_zip_path = os.path.join(upload_folder, token + "_" + safe_filename)
    upload.save(saved_zip_path)

    try:
        extracted_root = extract_backup_zip_for_restore(saved_zip_path, token)
        validation = validate_restore_backup_folder(extracted_root)
    except Exception as error:
        return f"""
        {nav_links()}
        <h1>Restore Validation Failed</h1>
        <p style="color:red; font-weight:bold;">{safe_text(error)}</p>
        <p><a href="/admin-restore-backup">Back to Restore</a></p>
        """, 500

    table_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in validation.get("table_counts", {}).items()
    )

    error_rows = "".join(
        f"<li>{safe_text(error)}</li>"
        for error in validation.get("errors", [])
    )

    if not error_rows:
        error_rows = "<li>None</li>"

    manifest = validation.get("manifest", {})
    status = "VERIFIED" if validation.get("valid") else "FAILED"
    status_color = "green" if validation.get("valid") else "red"
    restore_button = ""

    if validation.get("valid"):
        restore_button = f"""
        <form method="POST" action="/admin-restore-backup/confirm">
            <input type="hidden" name="restore_token" value="{safe_text(token)}">
            <button type="submit" style="font-weight:bold; padding:10px 16px; background:#dc3545; color:white; border:0; border-radius:8px;">
                Create Safety Backup and Restore
            </button>
            &nbsp;
            <a href="/admin-restore-backup">Cancel</a>
        </form>
        """

    return f"""
    {nav_links()}

    <h1>Restore Preview</h1>

    <p style="color:{status_color}; font-weight:bold; font-size:18px;">
        Validation Status: {status}
    </p>

    <p>
        <strong>Backup Name:</strong> {safe_text(validation.get('backup_name'))}<br>
        <strong>Created:</strong> {safe_text(manifest.get('created_at'))}<br>
        <strong>App Version:</strong> {safe_text(manifest.get('app_version'))}<br>
        <strong>Database Included:</strong> {'YES' if validation.get('database_path') else 'NO'}<br>
        <strong>Database Size:</strong> {human_file_size(validation.get('database_size_bytes', 0))}<br>
        <strong>Templates:</strong> {safe_text(validation.get('template_count'))}<br>
        <strong>Profile Photos:</strong> {safe_text(validation.get('profile_photo_count'))}<br>
        <strong>Missing Photo References:</strong> {len(validation.get('missing_profile_photo_references', []))}
    </p>

    <h2>Table Counts in Backup</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows</th></tr>
        {table_rows}
    </table>

    <h2>Validation Errors</h2>
    <ul>{error_rows}</ul>

    <div style="border:2px solid #dc3545; background:#fff5f5; padding:12px; border-radius:8px; max-width:900px; margin-top:16px;">
        <strong>Restore will replace the active database and email templates.</strong><br>
        A full safety backup will be created first. Restore will not continue if that safety backup fails.
    </div>

    <br>
    {restore_button}
    <p><a href="/admin-backup">Back to Backup & Recovery</a></p>
    """


@app.route("/admin-restore-backup/confirm", methods=["POST"])
def admin_restore_backup_confirm():

    token = safe_text(request.form.get("restore_token")).strip()

    if not token or "/" in token or ".." in token:
        return "Invalid restore token.", 400

    extracted_root = None
    token_folder = os.path.join(restore_work_root(), token)

    if os.path.isdir(token_folder):
        for name in os.listdir(token_folder):
            candidate = os.path.join(token_folder, name)
            if os.path.isdir(candidate) and name.startswith("ShoreHome_Backup_"):
                extracted_root = candidate
                break
        if not extracted_root:
            extracted_root = token_folder

    if not extracted_root or not os.path.isdir(extracted_root):
        return "Restore package not found. Please upload and validate the backup again.", 400

    try:
        restore_result = restore_from_validated_backup(extracted_root)
    except Exception as error:
        return f"""
        {nav_links()}
        <h1>Restore Failed</h1>
        <p style="color:red; font-weight:bold;">{safe_text(error)}</p>
        <p>No restore should be trusted unless the completion screen appears.</p>
        <p><a href="/admin-restore-backup">Back to Restore</a></p>
        """, 500

    table_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in restore_result.get("restored_table_counts", {}).items()
    )

    return f"""
    {nav_links()}

    <h1>Restore Complete</h1>

    <p style="color:green; font-weight:bold; font-size:18px;">
        Status: RESTORED
    </p>

    <p>
        <strong>Pre-Restore Safety Backup:</strong><br>
        {safe_text(restore_result.get('safety_backup_name'))}<br>
        <a href="/admin-backup/download/{safe_text(restore_result.get('safety_backup_zip_name'))}">{safe_text(restore_result.get('safety_backup_zip_name'))}</a>
    </p>

    <p>
        <strong>Restored Database Size:</strong> {human_file_size(restore_result.get('restored_database_size_bytes', 0))}<br>
        <strong>Templates Restored:</strong> {len(restore_result.get('restored_templates', []))}<br>
        <strong>Profile Photos Restored:</strong> {len(restore_result.get('restored_profile_photos', []))}<br>
        <strong>Missing Photo References After Restore:</strong> {len(restore_result.get('missing_profile_photo_references', []))}
    </p>

    <h2>Restored Table Counts</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows</th></tr>
        {table_rows}
    </table>

    <p><a href="/dashboard">Open Dashboard</a></p>
    <p><a href="/admin-backup">Back to Backup & Recovery</a></p>
    """




# -----------------------------------------------------------------------------
# Phase 3: Selective Data Recovery (rooms + guest_profiles only).
# -----------------------------------------------------------------------------

SELECTIVE_RECOVERY_TABLES = [
    "rooms",
    "guest_profiles",
]


def selective_restore_work_root():

    return os.path.join(
        app.root_path,
        "selective_restore_uploads"
    )


def validate_selective_recovery_db(db_path):

    result = {
        "valid": False,
        "errors": [],
        "database_size_bytes": 0,
        "table_counts": {},
        "columns": {},
    }

    if not os.path.exists(db_path):
        result["errors"].append("Uploaded database file is missing.")
        return result

    result["database_size_bytes"] = os.path.getsize(db_path)

    try:
        source_conn = sqlite3.connect(db_path)
        source_conn.row_factory = sqlite3.Row

        existing_tables = set(
            row["name"] for row in source_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        )

        for table_name in SELECTIVE_RECOVERY_TABLES:

            if table_name not in existing_tables:
                result["errors"].append("Source database missing required table: " + table_name)
                result["table_counts"][table_name] = None
                result["columns"][table_name] = []
                continue

            result["table_counts"][table_name] = source_conn.execute(
                f"SELECT COUNT(*) AS count FROM {table_name}"
            ).fetchone()["count"]

            result["columns"][table_name] = [
                row["name"] for row in source_conn.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            ]

        source_conn.close()

    except Exception as error:
        result["errors"].append("Uploaded database is not readable: " + safe_text(error))

    result["valid"] = not result["errors"]
    return result


def table_column_names(conn, table_name):

    return [
        row["name"] for row in conn.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    ]


def restore_table_from_source_db(source_conn, target_conn, table_name):

    source_columns = table_column_names(source_conn, table_name)
    target_columns = table_column_names(target_conn, table_name)

    common_columns = [
        column for column in target_columns
        if column in source_columns
    ]

    if not common_columns:
        raise RuntimeError("No matching columns found for table: " + table_name)

    rows = source_conn.execute(
        f"SELECT {', '.join(common_columns)} FROM {table_name}"
    ).fetchall()

    target_conn.execute(f"DELETE FROM {table_name}")

    if rows:
        placeholders = ", ".join(["?"] * len(common_columns))
        column_list = ", ".join(common_columns)

        target_conn.executemany(
            f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})",
            [tuple(row[column] for column in common_columns) for row in rows]
        )

    return {
        "table": table_name,
        "rows_restored": len(rows),
        "columns_restored": common_columns,
    }


def selective_restore_rooms_and_guest_profiles(source_db_path):

    validation = validate_selective_recovery_db(source_db_path)

    if not validation.get("valid"):
        raise RuntimeError("Selective recovery blocked: source database validation failed.")

    safety_manifest = create_full_recovery_backup()

    if safe_text(safety_manifest.get("status")) != "VERIFIED":
        raise RuntimeError("Selective recovery blocked: pre-restore safety backup failed validation.")

    source_conn = sqlite3.connect(source_db_path)
    source_conn.row_factory = sqlite3.Row

    target_conn = get_db_connection()

    restored_tables = []

    try:
        for table_name in SELECTIVE_RECOVERY_TABLES:
            restored_tables.append(
                restore_table_from_source_db(
                    source_conn,
                    target_conn,
                    table_name
                )
            )

        target_conn.commit()

    except Exception:
        target_conn.rollback()
        raise

    finally:
        source_conn.close()
        target_conn.close()

    current_summary = database_backup_summary(DATABASE_FILE)

    return {
        "safety_backup_name": safety_manifest.get("backup_name"),
        "safety_backup_zip_name": safety_manifest.get("backup_zip_name"),
        "restored_tables": restored_tables,
        "current_table_counts": current_summary.get("tables", {}),
    }


@app.route("/admin-selective-data-recovery", methods=["GET", "POST"])
def admin_selective_data_recovery():

    if request.method != "POST":
        return f"""
        {nav_links()}

        <h1>Phase 3 — Selective Data Recovery</h1>

        <div style="border:2px solid #fd7e14; background:#fff8ef; padding:14px; border-radius:10px; max-width:900px;">
            <p style="font-weight:bold; margin-top:0;">
                This restores only rooms and guest profiles from an uploaded SQLite database.
            </p>
            <p>
                It does not restore requests, bookings, invitations, coordination groups, blocked dates, templates, or app files.
            </p>
            <p>
                A full pre-restore safety backup is created before anything is changed.
            </p>
        </div>

        <h2>Select Source Database</h2>

        <form method="POST" enctype="multipart/form-data">
            <p>
                <input type="file" name="source_db" accept=".db,.sqlite,.sqlite3" required>
            </p>
            <button type="submit" style="font-weight:bold; padding:10px 16px; background:#0f4c81; color:white; border:0; border-radius:8px;">
                Upload and Preview
            </button>
            &nbsp;
            <a href="/admin-backup">Cancel</a>
        </form>
        """

    upload = request.files.get("source_db")

    filename = safe_text(upload.filename if upload else "").strip()

    if not upload or not filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return "SQLite .db file is required.", 400

    token = safe_restore_token()
    upload_folder = selective_restore_work_root()
    os.makedirs(upload_folder, exist_ok=True)

    safe_filename = os.path.basename(filename)
    saved_db_path = os.path.join(upload_folder, token + "_" + safe_filename)
    upload.save(saved_db_path)

    validation = validate_selective_recovery_db(saved_db_path)

    table_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in validation.get("table_counts", {}).items()
    )

    error_rows = "".join(
        f"<li>{safe_text(error)}</li>"
        for error in validation.get("errors", [])
    )

    if not error_rows:
        error_rows = "<li>None</li>"

    status = "VERIFIED" if validation.get("valid") else "FAILED"
    status_color = "green" if validation.get("valid") else "red"

    restore_button = ""

    if validation.get("valid"):
        restore_button = f"""
        <form method="POST" action="/admin-selective-data-recovery/confirm">
            <input type="hidden" name="restore_token" value="{safe_text(token)}">
            <input type="hidden" name="source_filename" value="{safe_text(safe_filename)}">
            <button type="submit" style="font-weight:bold; padding:10px 16px; background:#dc3545; color:white; border:0; border-radius:8px;">
                Create Safety Backup and Restore Rooms + Guest Profiles
            </button>
            &nbsp;
            <a href="/admin-selective-data-recovery">Cancel</a>
        </form>
        """

    return f"""
    {nav_links()}

    <h1>Selective Data Recovery Preview</h1>

    <p style="color:{status_color}; font-weight:bold; font-size:18px;">
        Validation Status: {status}
    </p>

    <p>
        <strong>Source DB:</strong> {safe_text(safe_filename)}<br>
        <strong>Database Size:</strong> {human_file_size(validation.get('database_size_bytes', 0))}<br>
        <strong>Restore Scope:</strong> rooms + guest_profiles only
    </p>

    <h2>Rows Available to Restore</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows</th></tr>
        {table_rows}
    </table>

    <h2>Validation Errors</h2>
    <ul>{error_rows}</ul>

    <div style="border:2px solid #dc3545; background:#fff5f5; padding:12px; border-radius:8px; max-width:900px; margin-top:16px;">
        <strong>This will replace only the current rooms and guest_profiles tables.</strong><br>
        It will not touch booking requests, bookings, invitations, coordination groups, blocked dates, email templates, or app files.
    </div>

    <br>
    {restore_button}
    <p><a href="/admin-backup">Back to Backup & Recovery</a></p>
    """


@app.route("/admin-selective-data-recovery/confirm", methods=["POST"])
def admin_selective_data_recovery_confirm():

    token = safe_text(request.form.get("restore_token")).strip()
    source_filename = safe_text(request.form.get("source_filename")).strip()

    if not token or "/" in token or ".." in token:
        return "Invalid restore token.", 400

    if not source_filename or "/" in source_filename or ".." in source_filename:
        return "Invalid source filename.", 400

    source_db_path = os.path.join(
        selective_restore_work_root(),
        token + "_" + source_filename
    )

    if not os.path.exists(source_db_path):
        return "Source database not found. Please upload and preview it again.", 400

    try:
        restore_result = selective_restore_rooms_and_guest_profiles(source_db_path)
    except Exception as error:
        return f"""
        {nav_links()}
        <h1>Selective Data Recovery Failed</h1>
        <p style="color:red; font-weight:bold;">{safe_text(error)}</p>
        <p>No selective restore should be trusted unless the completion screen appears.</p>
        <p><a href="/admin-selective-data-recovery">Back to Selective Data Recovery</a></p>
        """, 500

    restored_rows = "".join(
        f"<tr><td>{safe_text(item.get('table'))}</td><td>{safe_text(item.get('rows_restored'))}</td><td>{safe_text(', '.join(item.get('columns_restored', [])))}</td></tr>"
        for item in restore_result.get("restored_tables", [])
    )

    table_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in restore_result.get("current_table_counts", {}).items()
    )

    return f"""
    {nav_links()}

    <h1>Selective Data Recovery Complete</h1>

    <p style="color:green; font-weight:bold; font-size:18px;">
        Status: RESTORED
    </p>

    <p>
        <strong>Pre-Restore Safety Backup:</strong><br>
        {safe_text(restore_result.get('safety_backup_name'))}<br>
        <a href="/admin-backup/download/{safe_text(restore_result.get('safety_backup_zip_name'))}">{safe_text(restore_result.get('safety_backup_zip_name'))}</a>
    </p>

    <h2>Tables Restored</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows Restored</th><th>Columns Restored</th></tr>
        {restored_rows}
    </table>

    <h2>Current Table Counts After Restore</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Table</th><th>Rows</th></tr>
        {table_rows}
    </table>

    <p><a href="/profiles">Open Guest Profiles</a></p>
    <p><a href="/dashboard">Open Dashboard</a></p>
    <p><a href="/admin-backup">Back to Backup & Recovery</a></p>
    """


@app.route("/admin-reset-test-data", methods=["GET", "POST"])
def admin_reset_test_data():

    conn = get_db_connection()
    before_counts = admin_reset_test_data_counts(conn)

    if request.method != "POST":

        counts_html = admin_reset_test_data_counts_html(before_counts)
        conn.close()

        return f"""
        {nav_links()}

        <h1>Reset Test Data</h1>

        <div style="
            background-color: #fff3cd;
            border: 2px solid #fd7e14;
            padding: 14px;
            border-radius: 8px;
            max-width: 820px;
            margin-bottom: 14px;
        ">
            <p style="font-weight: bold; margin-top: 0; color: #856404;">
                This clears operational test data but keeps guest profiles, rooms, and blocked dates.
            </p>

            <p>
                A database backup will be created automatically before the reset runs.
                The backup is copied outward only; this tool never restores or copies an older database back over the current one.
            </p>

            <p><strong>Preserved and verified before/after:</strong> guest_profiles, rooms, blocked_dates</p>
            <p><strong>Cleared:</strong> invitations, booking_requests, bookings, coordination tables, activity_log, email_log</p>
        </div>

        <h2>Current Counts</h2>
        {counts_html}

        <form method="POST" action="/admin-reset-test-data" style="margin-top: 16px;">
            <input type="hidden" name="confirm_action" value="yes">
            <button type="submit" style="
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 9px 14px;
                border-radius: 5px;
                font-weight: bold;
            ">
                Create Backup and Reset Test Data
            </button>
            &nbsp;
            <a href="/dashboard">Cancel / Go Back</a>
        </form>
        """

    try:

        if request.form.get("confirm_action") != "yes":
            conn.close()
            return redirect("/admin-reset-test-data")

        os.makedirs("backups", exist_ok=True)

        timestamp = datetime.now().strftime(
            "%Y_%m_%d__%H_%M_%S"
        )

        backup_filename = f"shore_backup_pre_test_reset_{timestamp}.db"
        backup_path = os.path.join(
            "backups",
            backup_filename
        )

        # Backup is copy-out only. This reset tool must never copy or restore
        # a database file back over DATABASE_FILE.
        shutil.copy2(
            DATABASE_FILE,
            backup_path
        )

        preserved_rows_snapshot = admin_reset_preserved_rows_snapshot(conn)

        conn.execute("BEGIN")

        # Clear child/detail tables before parent tables.
        conn.execute("DELETE FROM activity_log")
        conn.execute("DELETE FROM email_log")
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM coordination_date_options")
        conn.execute("DELETE FROM coordination_group_members")
        conn.execute("DELETE FROM coordination_groups")
        conn.execute("DELETE FROM booking_requests")
        conn.execute("DELETE FROM invitations")

        # Guardrail: actively restore preserved master tables from the snapshot
        # taken immediately before reset. This prevents any reset/seed behavior
        # from rolling guest profiles, rooms, or blocked dates back to older data.
        admin_reset_restore_preserved_rows(
            conn,
            preserved_rows_snapshot
        )

        conn.commit()

        after_counts = admin_reset_test_data_counts(conn)
        preserved_ok, preserved_problems = admin_reset_test_data_preserved_ok(
            before_counts,
            after_counts
        )
        conn.close()

        before_html = admin_reset_test_data_counts_html(before_counts)
        after_html = admin_reset_test_data_counts_html(after_counts)

        if preserved_ok:
            preserved_message = "Operational test data was cleared. Guest profiles, rooms, and blocked dates were preserved."
            preserved_color = "green"
        else:
            preserved_message = "RESET WARNING: Preserved table counts changed. Review before continuing: " + " | ".join(preserved_problems)
            preserved_color = "red"

        return f"""
        {nav_links()}

        <h1>Reset Test Data Complete</h1>

        <p style="color: {preserved_color}; font-weight: bold;">
            {safe_text(preserved_message)}
        </p>

        <p>
            <strong>Automatic backup created before reset:</strong><br>
            {safe_text(backup_filename)}<br>
            <span style="color: #666;">{safe_text(backup_path)}</span>
        </p>

        <h2>Before Reset</h2>
        {before_html}

        <h2>After Reset</h2>
        {after_html}

        <p>
            <a href="/dashboard">Back to Dashboard</a> |
            <a href="/production-check">Open Production Check</a>
        </p>
        """

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            "/admin-reset-test-data"
        )


@app.route("/new-request")
@app.route("/")
def home():
    conn = get_db_connection()

    pending_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE status = 'pending'
    """).fetchone()["count"]

    email_needed = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE email_status IN ('needs_email', 'needs_update')
    """).fetchone()["count"]

    approved_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM booking_requests
        WHERE status = 'approved'
    """).fetchone()["count"]

    selected_year = int(request.args.get("year", date.today().year))
    selected_month = int(request.args.get("month", date.today().month))

    if selected_month < 1:
        selected_month = 12
        selected_year -= 1

    if selected_month > 12:
        selected_month = 1
        selected_year += 1


    ensure_house_block_columns(conn)

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    total_rooms = conn.execute(
        "SELECT COUNT(*) AS count FROM rooms"
    ).fetchone()["count"]

    tentative_holds = get_coordination_tentative_holds(conn)

    conn.close()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked,
        total_rooms
    )

    blocked_list = sorted(blocked_dates)

    first_day = date(selected_year, selected_month, 1)

    if selected_month == 12:
        next_month_date = date(selected_year + 1, 1, 1)
    else:
        next_month_date = date(selected_year, selected_month + 1, 1)

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:
        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:
        next_month = 1
        next_year += 1

    days_in_month = (next_month_date - first_day).days
    start_weekday = (first_day.weekday() + 1) % 7
    month_title = first_day.strftime("%B %Y")

    room_capacity = {}

    current = first_day

    while current < next_month_date:
        rooms_used = 0

        for booking in bookings:
            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            if booking_start <= current < booking_end:
                rooms_used += 1

        tentative_rooms_held = 0

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()
                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            if hold_start <= current < hold_end:
                tentative_rooms_held += int(tentative_hold.get("rooms_held", 1) or 1)

        current_date_key = current.strftime("%Y-%m-%d")
        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            current_date_key,
            total_rooms
        )

        room_capacity[current_date_key] = max(
            0,
            capacity_limit - rooms_used - tentative_rooms_held
        )

        current += timedelta(days=1)

    alert_box = f"""
    <div style="
        border: 1px solid #dee2e6;
        background-color: #f8f9fa;
        padding: 16px;
        margin-bottom: 24px;
        border-radius: 8px;
        max-width: 650px;
    ">
        <h2 style="margin-top: 0; margin-bottom: 12px;">
            Dashboard Alerts
        </h2>

        <div style="margin-bottom: 8px;">
            <a href="/requests"
               style="color: #dc3545; font-weight: bold; text-decoration: none;">
                {pending_requests} Pending Request(s)
            </a>
        </div>

        <div style="margin-bottom: 8px;">
            <a href="/requests"
               style="color: #fd7e14; font-weight: bold; text-decoration: none;">
                {email_needed} Email(s) Need Attention
            </a>
        </div>

        <div>
            <a href="/bookings"
               style="color: #198754; font-weight: bold; text-decoration: none;">
                {approved_count} Approved Stay(s)
            </a>
        </div>
    </div>
    """

    calendar_base_path = "/new-request" if request.path == "/new-request" else "/"

    calendar_html = f"""
    <h2>Capacity Calendar - {month_title}</h2>

    <p>
        <a data-calendar-nav="1" href="{calendar_base_path}?year={previous_year}&month={previous_month}">
            Previous Month
        </a>
        |
        <strong>{month_title}</strong>
        |
        <a data-calendar-nav="1" href="{calendar_base_path}?year={next_year}&month={next_month}">
            Next Month
        </a>
    </p>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
        <tr>
    """

    for _ in range(start_weekday):
        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):
        current_date = date(selected_year, selected_month, day)
        current_date_str = current_date.strftime("%Y-%m-%d")

        today = date.today()
        past_date = current_date < today

        rooms_open = room_capacity.get(current_date_str, total_rooms)

        holds_for_day = []

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()
                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            if hold_start <= current_date < hold_end:
                holds_for_day.append(tentative_hold)

        has_tentative_hold = len(holds_for_day) > 0

        if past_date:
            background = "#e9ecef"
            status = "Past"
            display_line_1 = ""
            display_line_2 = "Past"
            click_handler = ""
            cursor = "not-allowed"

        elif current_date_str in blocked_dates:
            background = "#f8d7da"
            status = "Blocked"
            display_line_1 = ""
            display_line_2 = "Blocked"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 0:
            background = "#f8d7da"
            status = "Full"
            display_line_1 = "0 open"
            display_line_2 = "Full"
            click_handler = ""
            cursor = "not-allowed"

        elif has_tentative_hold:
            background = "#cfe8ff"
            status = "Coordination Hold"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Coordination Hold"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        elif rooms_open <= 2:
            background = "#fff3cd"
            status = "Almost Full"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Almost Full"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        else:
            background = "#d4edda"
            status = "Open"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Open"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        calendar_html += f"""
        <td {click_handler}
            data-date="{current_date_str}"
            data-rooms-open="{rooms_open}"
            data-status="{status}"
            style="
                background-color: {background};
                vertical-align: top;
                width: 56px;
                height: 44px;
                font-size: 12px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong><br>
            <span style="font-size: 9px; font-weight: normal; line-height: 1.05;">
                {str(rooms_open) + " ROOM" + ("" if rooms_open == 1 else "S") + " OPEN" if (not past_date and current_date_str not in blocked_dates and rooms_open > 0) else ("FULL" if (not past_date and current_date_str not in blocked_dates and rooms_open <= 0) else "")}
            </span><br>
            <span style="font-size: 9px;">{display_line_2 if has_tentative_hold else ''}</span>
        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0 and day != days_in_month:
            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:
        calendar_html += "<td></td>"
        day_counter += 1

    calendar_html += """
        </tr>
    </table>

    <p>
        <strong>Legend:</strong>
        <span style="background-color: #d4edda; padding: 4px;">Open</span>
        <span style="background-color: #fff3cd; padding: 4px;">Almost Full</span>
        <span style="background-color: #cfe8ff; padding: 4px;">Coordination Hold</span>
        <span style="background-color: #f8d7da; padding: 4px;">Full / Blocked</span>
        <span style="background-color: #e9ecef; padding: 4px;">Past</span>
    
        <span style="border: 2px dotted #dc3545; padding: 3px;">Tentative Group Dates</span>
    </p>
    """

    html = ""

    if request.path != "/new-request":
        html = nav_links()
        html += alert_box

    html += """
    
    <style>
        .guest-request-page label,
        .guest-request-page input,
        .guest-request-page select,
        .guest-request-page textarea,
        .guest-request-page button {
            font-size: 20px;
        }

        .guest-request-page select,
        .guest-request-page input,
        .guest-request-page textarea {
            line-height: 1.35;
        }

        .guest-request-page .guest-bedroom-instruction {
            font-size: 28px;
            font-weight: 800;
            line-height: 1.15;
            margin-bottom: 4px;
        }

        .guest-request-page .guest-bedroom-subtext {
            font-size: 22px;
            font-weight: 700;
            line-height: 1.2;
        }
    </style>

    <div class="guest-request-page">
    <h1 style="margin-bottom: 6px;">Standard Visit Request</h1>

    <div style="
        background-color: #f8fbff;
        border: 1px solid #cfe2ff;
        padding: 10px 12px;
        border-radius: 8px;
        max-width: 760px;
        margin-bottom: 12px;
        line-height: 1.35;
    ">
        <strong>Pick your bedrooms, then pick your dates.</strong><br>
        This is just a request for now — no one is packing a beach bag until it is approved.
    </div>

    <div style="
        display: flex;
        gap: 36px;
        align-items: flex-start;
        flex-wrap: wrap;
        max-width: 1180px;
    ">

        <div style="
            flex: 0 0 330px;
            max-width: 360px;
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 12px 14px;
            line-height: 1.35;
        ">
            <h2 style="margin-top: 0; margin-bottom: 8px;">Standard Visitor Request Page</h2>
            <p style="margin-top: 0; margin-bottom: 8px;">
                Use this form to start a new shore visit request.
            </p>
            <p style="margin-top: 0; margin-bottom: 0;">
                Choose bedrooms first, then select arrival and departure dates from the calendar.
            </p>
        </div>

        <div style="
            flex: 1;
            min-width: 340px;
            max-width: 720px;
        ">

    <h2 style="margin-bottom: 6px;">Visit Details</h2>

    <form method="POST"
          action="/submit"
          onsubmit="return checkUnavailableDates();">

        <input type="hidden" name="invitation_id" value="">
        <input type="hidden" name="adults" value="1">
        <input type="hidden" name="children" value="0">

        <label style="font-size: 18px; font-weight: bold;">
            <strong class="guest-bedroom-instruction">Choose the number of bedrooms you need first then the dates.</strong>
        </label><br>

        <div style="font-size: 15px; font-weight: bold; margin-bottom: 4px;">
            Each bedroom sleeps up to 2 guests.
        </div>

        <select name="rooms_requested"
                id="rooms_requested">
            <option value="1">1 Bedroom</option>
            <option value="2">2 Bedrooms</option>
            <option value="3">3 Bedrooms</option>
            <option value="4">4 Bedrooms</option>
        </select>

        <br>
    """

    html += calendar_html

    html += """
        <h3 style="font-size: 28px; margin-bottom: 8px;">Selected Stay</h3>

        <p id="date_selection_message"
           style="
               font-size: 24px;
               font-weight: bold;
               color: #0d6efd;
           ">
           No dates selected yet.
        </p>

        <p id="nights_message"
           style="
               font-size: 24px;
               font-weight: bold;
               color: #198754;
           ">
        </p>

        <input type="hidden"
               id="arrival_date"
               name="arrival_date"
               value="">

        <input type="hidden"
               id="departure_date"
               name="departure_date"
               value="">

        <button type="button"
                onclick="resetDateSelection();">
            Clear Selected Dates and Start Over
        </button>

        <br>

        <hr>

        <label>
            <strong>Contact Name</strong>
        </label><br>

        <input type="text"
               name="name"
               required
               style="width: 320px;">

        <br>

        <label>
            <strong>Email Address</strong>
        </label><br>

        <input type="email"
               name="email"
               required
               style="width: 320px;">

        <br>

        <label>
            <strong>Additional Guest Name(s) for Your Room(s)</strong>
        </label><br>

        <small>
            Please include everyone expected to stay.
        </small><br>

        <textarea name="additional_names"
                  rows="2"
                  style="width: 420px;"></textarea>

        <br>

        <label>
            <strong style="font-size: 24px;">Bringing a pet?</strong>
        </label><br>

        <select name="pets">
            <option value="No">No</option>
            <option value="Yes">Yes</option>
        </select>

        <br>

        <label>
            <strong style="font-size: 24px;">Food Restrictions or Preferences</strong>
        </label><br>

        <small style="font-size: 18px;">
            Optional — dietary restrictions, allergies, etc.
        </small><br>

        <textarea name="food_restrictions"
                  rows="3"
                  style="width: 420px; font-size: 22px; padding: 8px; line-height: 1.35;"></textarea>

        <br>

        <label>
            <strong style="font-size: 24px;">Comments or Notes</strong>
        </label><br>

        <textarea name="comments"
                  rows="2"
                  style="width: 420px; font-size: 22px; padding: 8px; line-height: 1.35;"></textarea>

        <br>

        <label>
            <strong style="font-size: 24px;">
                Who else are you hoping to visit with at the same time?
            </strong>
        </label><br>

        <small style="font-size: 18px;">
            Optional — example: Jack Smith and family, Florida group etc.
        </small><br>

        <textarea name="coordination_notes"
                  rows="3"
                  style="width: 420px; font-size: 22px; padding: 8px; line-height: 1.35;"></textarea>

        <br>

        <div style="
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            padding: 10px 12px;
            border-radius: 8px;
            max-width: 620px;
            margin-top: 10px;
            margin-bottom: 10px;
        ">
            <strong>What happens next?</strong><br>
            I’ll review the request and let you know if the dates work.
            If something needs adjusting, I’ll follow up. Easy enough.
        </div>

        <input type="submit"
               value="Submit Visit Request"
               style="padding: 12px 18px; font-size: 22px; font-weight: bold;">

    </form>
    """

    # Standard request page is not part of a coordination group, so there are no other group rooms.
    other_group_rooms_total = 0

    html += f"""
    <script>
        const blockedDates = {blocked_list};
        const roomCapacity = {room_capacity};
        const totalRooms = {total_rooms};
        const otherGroupRooms = {other_group_rooms_total};

        let nextDateField = "arrival";
        let selectedArrivalCell = null;
        let selectedDepartureCell = null;


        const dateSelectionStorageKey = "shore_home_date_selection_" + window.location.pathname;

        function saveDateSelectionState() {{
            try {{
                localStorage.setItem(
                    dateSelectionStorageKey,
                    JSON.stringify({{
                        arrival: document.getElementById("arrival_date").value,
                        departure: document.getElementById("departure_date").value,
                        next: nextDateField
                    }})
                );
            }} catch (error) {{}}
        }}

        function restoreDateSelectionState() {{
            try {{
                const queryState = new URLSearchParams(window.location.search);
                const queryArrival = queryState.get("arrival_date") || "";
                const queryDeparture = queryState.get("departure_date") || "";
                const queryNext = queryState.get("next_date_field") || "";

                // V35.2.2: request pages must open with no stale selected stay.
                // Only explicit query params from calendar navigation are allowed.
                if (queryArrival) {{
                    document.getElementById("arrival_date").value = queryArrival;
                    nextDateField = queryNext || "departure";
                }}

                if (queryDeparture) {{
                    document.getElementById("departure_date").value = queryDeparture;
                    nextDateField = queryNext || "arrival";
                }}

                if (!queryArrival && !queryDeparture) {{
                    document.getElementById("arrival_date").value = "";
                    document.getElementById("departure_date").value = "";
                    document.getElementById("date_selection_message").innerText = "No dates selected yet.";
                    document.getElementById("nights_message").innerText = "";
                    nextDateField = "arrival";

                    try {{
                        localStorage.removeItem(dateSelectionStorageKey);
                    }} catch (storageError) {{}}
                }}
            }} catch (error) {{}}
        }}

        function getRequestedRooms() {{
            return parseInt(document.getElementById("rooms_requested").value);
        }}

        function formatDateForMessage(dateString) {{
            const parts = dateString.split("-");
            return parts[1] + "/" + parts[2] + "/" + parts[0];
        }}

        function getRoomsOpen(dateString) {{
            if (roomCapacity[dateString] === undefined) {{
                return totalRooms;
            }}

            return roomCapacity[dateString];
        }}

        function clearSelectedCellColors() {{
            if (selectedArrivalCell) {{
                selectedArrivalCell.style.outline = "";
                selectedArrivalCell.style.backgroundColor = selectedArrivalCell.dataset.originalColor;
            }}

            if (selectedDepartureCell) {{
                selectedDepartureCell.style.outline = "";
                selectedDepartureCell.style.backgroundColor = selectedDepartureCell.dataset.originalColor;
            }}

            selectedArrivalCell = null;
            selectedDepartureCell = null;
        }}

        function highlightSelectedDates() {{
            const arrivalValue = document.getElementById("arrival_date").value;
            const departureValue = document.getElementById("departure_date").value;

            if (arrivalValue) {{
                const arrivalCell = document.querySelector('[data-date="' + arrivalValue + '"]');

                if (arrivalCell) {{
                    if (!arrivalCell.dataset.originalColor) {{
                        arrivalCell.dataset.originalColor = arrivalCell.style.backgroundColor;
                    }}

                    selectedArrivalCell = arrivalCell;
                    arrivalCell.style.backgroundColor = "#9ec5fe";
                    arrivalCell.style.outline = "3px solid #0d6efd";
                }}
            }}

            if (departureValue) {{
                const departureCell = document.querySelector('[data-date="' + departureValue + '"]');

                if (departureCell) {{
                    if (!departureCell.dataset.originalColor) {{
                        departureCell.dataset.originalColor = departureCell.style.backgroundColor;
                    }}

                    selectedDepartureCell = departureCell;
                    departureCell.style.backgroundColor = "#b6d7a8";
                    departureCell.style.outline = "3px solid #198754";
                }}
            }}
        }}

        function resetDateSelection() {{
            document.getElementById("arrival_date").value = "";
            document.getElementById("departure_date").value = "";

            document.getElementById("date_selection_message").innerText =
                "No dates selected yet.";

            document.getElementById("nights_message").innerText = "";

            clearSelectedCellColors();

            nextDateField = "arrival";

            try {{
                localStorage.removeItem(dateSelectionStorageKey);
            }} catch (error) {{}}
        }}

        function updateNightsMessage() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const nightsMessage = document.getElementById("nights_message");

            if (!arrival || !departure || departure <= arrival) {{
                nightsMessage.innerText = "";
                return;
            }}

            const arrivalDate = new Date(arrival + "T00:00:00");
            const departureDate = new Date(departure + "T00:00:00");

            const nights = Math.round(
                (departureDate - arrivalDate) / (1000 * 60 * 60 * 24)
            );

            nightsMessage.innerText =
                "Requested stay: "
                + nights
                + " night"
                + (nights === 1 ? "" : "s")
                + " / "
                + getRequestedRooms()
                + " bedroom"
                + (getRequestedRooms() === 1 ? "" : "s");
        }}

        function selectCalendarDate(dateString) {{
            const requestedRooms = getRequestedRooms();
            const roomsOpen = getRoomsOpen(dateString);

            if (blockedDates.includes(dateString)) {{
                alert(formatDateForMessage(dateString) + " is blocked.");
                return;
            }}

            if (roomsOpen < requestedRooms) {{
                alert(
                    "Only "
                    + roomsOpen
                    + " bedroom(s) available on "
                    + formatDateForMessage(dateString)
                );

                return;
            }}

            const clickedCell =
                document.querySelector('[data-date="' + dateString + '"]');

            if (clickedCell && !clickedCell.dataset.originalColor) {{
                clickedCell.dataset.originalColor =
                    clickedCell.style.backgroundColor;
            }}

            const arrivalField = document.getElementById("arrival_date");
            const departureField = document.getElementById("departure_date");
            const message = document.getElementById("date_selection_message");

            if (nextDateField === "arrival" && arrivalField.value && !departureField.value && dateString > arrivalField.value) {{
                nextDateField = "departure";
            }}

            if (nextDateField === "arrival") {{
                clearSelectedCellColors();

                arrivalField.value = dateString;
                departureField.value = "";
                nextDateField = "departure";

                if (clickedCell) {{
                    selectedArrivalCell = clickedCell;
                    clickedCell.style.backgroundColor = "#9ec5fe";
                    clickedCell.style.outline = "3px solid #0d6efd";
                }}

                message.innerText =
                    "Arrival selected: "
                    + formatDateForMessage(dateString)
                    + ". Now click a departure date.";

                updateNightsMessage();
                saveDateSelectionState();

            }} else {{

                if (dateString <= arrivalField.value) {{
                    alert("Departure date must be after arrival date.");
                    return;
                }}

                if (selectedDepartureCell) {{
                    selectedDepartureCell.style.outline = "";
                    selectedDepartureCell.style.backgroundColor = selectedDepartureCell.dataset.originalColor;
                    selectedDepartureCell = null;
                }}

                departureField.value = dateString;
                nextDateField = "arrival";

                if (clickedCell) {{
                    selectedDepartureCell = clickedCell;
                    clickedCell.style.backgroundColor = "#b6d7a8";
                    clickedCell.style.outline = "3px solid #198754";
                }}

                message.innerText =
                    "Selected stay: "
                    + formatDateForMessage(arrivalField.value)
                    + " to "
                    + formatDateForMessage(dateString)
                    + ".";

                updateNightsMessage();
                saveDateSelectionState();
            }}
        }}

        restoreDateSelectionState();
        highlightSelectedDates();
        updateNightsMessage();
        preserveCalendarNavigationSelection();

        document.getElementById("rooms_requested")
            .addEventListener("change", function () {{
                resetDateSelection();
                updateNightsMessage();
            }});

        function preserveCalendarNavigationSelection() {{
            document.querySelectorAll('[data-calendar-nav="1"]').forEach(function (link) {{
                link.addEventListener("click", function () {{
                    const arrival = document.getElementById("arrival_date").value;
                    const departure = document.getElementById("departure_date").value;
                    const url = new URL(link.href, window.location.origin);

                    if (arrival) {{
                        url.searchParams.set("arrival_date", arrival);
                    }}

                    if (departure) {{
                        url.searchParams.set("departure_date", departure);
                    }}

                    url.searchParams.set("next_date_field", nextDateField || "arrival");
                    link.href = url.toString();
                }});
            }});
        }}

        function checkUnavailableDates() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const requestedRooms = getRequestedRooms();

            if (!arrival || !departure) {{
                alert("Please select both an arrival date and a departure date from the calendar.");
                return false;
            }}

            if (departure <= arrival) {{
                alert("Error: Departure date must be after the arrival date.");
                return false;
            }}

            let current = new Date(arrival + "T00:00:00");
            const end = new Date(departure + "T00:00:00");

            while (current < end) {{
                const dateString = current.toISOString().slice(0, 10);

                if (blockedDates.includes(dateString)) {{
                    alert(
                        "Error: "
                        + formatDateForMessage(dateString)
                        + " is blocked."
                    );

                    return false;
                }}

                const roomsOpen = getRoomsOpen(dateString);

                if (roomsOpen < requestedRooms) {{
                    alert(
                        "Only "
                        + roomsOpen
                        + " bedroom(s) available on "
                        + formatDateForMessage(dateString)
                    );

                    return false;
                }}

                current.setDate(current.getDate() + 1);
            }}

            return true;
        }}

        // Do not reset here: preserve arrival/departure when navigating calendar months.
    </script>
    """

    html += """
    </div>
    """

    return html


@app.route("/invite/<int:invitation_id>")
def invite_request(invitation_id):

    conn = get_db_connection()

    invitation = conn.execute("""
        SELECT
            invitations.*,
            guest_profiles.id AS guest_profile_id,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            guest_profiles.additional_names,
            guest_profiles.pet_notes,
            guest_profiles.food_notes
        FROM invitations

        JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id

        WHERE invitations.id = ?
    """, (
        invitation_id,
    )).fetchone()

    if not invitation:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Invitation Not Found</h1>

        <p>
            The invitation link could not be found.
        </p>

        <p>
            Please reply to the invitation email
            if you need help.
        </p>
        """

    selected_year = int(request.args.get("year", date.today().year))
    selected_month = int(request.args.get("month", date.today().month))

    follow_up_mode = clean_text(request.args.get("follow_up")) == "1"
    suggested_arrival = clean_text(request.args.get("suggested_arrival"))
    suggested_departure = clean_text(request.args.get("suggested_departure"))

    if selected_month < 1:
        selected_month = 12
        selected_year -= 1

    if selected_month > 12:
        selected_month = 1
        selected_year += 1

    ensure_house_block_columns(conn)

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    previous_bookings = conn.execute("""
        SELECT
            booking_requests.id AS request_id,
            booking_requests.name,
            booking_requests.additional_names,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.rooms_requested,
            rooms.name AS room_name
        FROM booking_requests
        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'
        LEFT JOIN rooms
            ON bookings.room_id = rooms.id
        WHERE booking_requests.guest_profile_id = ?
          AND booking_requests.status = 'approved'
        ORDER BY
            booking_requests.arrival_date DESC,
            rooms.name
    """, (
        invitation["guest_profile_id"],
    )).fetchall()

    pending_requests_for_guest = conn.execute("""
        SELECT
            id AS request_id,
            arrival_date,
            departure_date,
            rooms_requested,
            status
        FROM booking_requests
        WHERE guest_profile_id = ?
          AND status IN ('pending', 'change_requested', 'cancel_requested')
        ORDER BY arrival_date DESC
    """, (
        invitation["guest_profile_id"],
    )).fetchall()

    total_rooms = conn.execute(
        "SELECT COUNT(*) AS count FROM rooms"
    ).fetchone()["count"]

    tentative_holds = get_coordination_tentative_holds(conn)

    conn.close()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked,
        total_rooms
    )

    blocked_list = sorted(blocked_dates)

    first_day = date(selected_year, selected_month, 1)

    if selected_month == 12:
        next_month_date = date(selected_year + 1, 1, 1)
    else:
        next_month_date = date(selected_year, selected_month + 1, 1)

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:
        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:
        next_month = 1
        next_year += 1

    days_in_month = (next_month_date - first_day).days
    start_weekday = (first_day.weekday() + 1) % 7
    month_title = first_day.strftime("%B %Y")

    room_capacity = {}

    current = first_day

    while current < next_month_date:
        rooms_used = 0

        for booking in bookings:
            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            if booking_start <= current < booking_end:
                rooms_used += 1

        tentative_rooms_held = 0

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()
                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            if hold_start <= current < hold_end:
                tentative_rooms_held += int(tentative_hold.get("rooms_held", 1) or 1)

        current_date_key = current.strftime("%Y-%m-%d")
        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            current_date_key,
            total_rooms
        )

        room_capacity[current_date_key] = max(
            0,
            capacity_limit - rooms_used - tentative_rooms_held
        )

        current += timedelta(days=1)

    calendar_html = f"""
    <h2 id="calendar-section">Capacity Calendar - {month_title}</h2>

    <p>
        <a data-calendar-nav="1" href="/invite/{invitation_id}?year={previous_year}&month={previous_month}#calendar-section">
            Previous Month
        </a>
        |
        <strong>{month_title}</strong>
        |
        <a data-calendar-nav="1" href="/invite/{invitation_id}?year={next_year}&month={next_month}#calendar-section">
            Next Month
        </a>
    </p>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 760px;">
        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
        <tr>
    """

    for _ in range(start_weekday):
        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):
        current_date = date(selected_year, selected_month, day)
        current_date_str = current_date.strftime("%Y-%m-%d")

        today = date.today()
        past_date = current_date < today

        rooms_open = room_capacity.get(current_date_str, total_rooms)

        holds_for_day = []

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()
                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            if hold_start <= current_date < hold_end:
                holds_for_day.append(tentative_hold)

        has_tentative_hold = len(holds_for_day) > 0

        if past_date:
            background = "#e9ecef"
            status = "Past"
            display_line_1 = ""
            display_line_2 = "Past"
            click_handler = ""
            cursor = "not-allowed"

        elif current_date_str in blocked_dates:
            background = "#f8d7da"
            status = "Blocked"
            display_line_1 = ""
            display_line_2 = "Blocked"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 0:
            background = "#f8d7da"
            status = "Full"
            display_line_1 = "0 open"
            display_line_2 = "Full"
            click_handler = ""
            cursor = "not-allowed"

        elif has_tentative_hold:
            background = "#cfe8ff"
            status = "Coordination Hold"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Coordination Hold"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        elif rooms_open <= 2:
            background = "#fff3cd"
            status = "Almost Full"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Almost Full"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        else:
            background = "#d4edda"
            status = "Open"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Open"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        calendar_html += f"""
        <td {click_handler}
            data-date="{current_date_str}"
            data-rooms-open="{rooms_open}"
            data-status="{status}"
            style="
                background-color: {background};
                vertical-align: top;
                width: 56px;
                height: 44px;
                font-size: 12px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong><br>
            <span style="font-size: 9px; font-weight: normal; line-height: 1.05;">
                {str(rooms_open) + " ROOM" + ("" if rooms_open == 1 else "S") + " OPEN" if (not past_date and current_date_str not in blocked_dates and rooms_open > 0) else ("FULL" if (not past_date and current_date_str not in blocked_dates and rooms_open <= 0) else "")}
            </span><br>
            <span style="font-size: 9px;">{display_line_2 if has_tentative_hold else ''}</span>
        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0 and day != days_in_month:
            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:
        calendar_html += "<td></td>"
        day_counter += 1

    calendar_html += """
        </tr>
    </table>

    <p style="font-size: 12px; margin: 6px 0 0 0;">
        <strong>Legend:</strong>
        <span style="background-color: #d4edda; padding: 3px;">Open</span>
        <span style="background-color: #fff3cd; padding: 3px;">Almost Full</span>
        <span style="background-color: #cfe8ff; padding: 3px;">Coordination Hold</span>
        <span style="background-color: #f8d7da; padding: 3px;">Full / Blocked</span>
        <span style="background-color: #e9ecef; padding: 3px;">Past</span>
    
        <span style="border: 2px dotted #dc3545; padding: 3px;">Tentative Group Dates</span>
    </p>
    """

    previous_html = """
    <p>No previous approved stays found for this guest.</p>
    """

    if previous_bookings:

        previous_html = """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
                   margin-top: 8px;
               ">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Dates</th>
                <th align="left">Rooms</th>
                <th align="left">Room</th>
                <th align="left">View</th>
            </tr>
        """

        for booking in previous_bookings:

            previous_html += f"""
            <tr>
                <td>
                    {format_date(booking['arrival_date'])}<br>
                    to {format_date(booking['departure_date'])}
                </td>
                <td>{booking['rooms_requested'] or 1}</td>
                <td>{display_room_name(booking['room_name'])}</td>
                <td>
                    <a href="/request/{booking['request_id']}">
                        View
                    </a>
                </td>
            </tr>
            """

        previous_html += "</table>"

    pending_html = """
    <p>No pending requests found for this guest.</p>
    """

    if pending_requests_for_guest:

        pending_html = """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
                   margin-top: 8px;
               ">
            <tr style="background-color: #fff8d6;">
                <th align="left">Dates</th>
                <th align="left">Rooms</th>
                <th align="left">Status</th>
                <th align="left">Edit</th>
            </tr>
        """

        for pending_request in pending_requests_for_guest:

            pending_html += f"""
            <tr>
                <td>
                    {format_date(pending_request['arrival_date'])}<br>
                    to {format_date(pending_request['departure_date'])}
                </td>
                <td>{pending_request['rooms_requested'] or 1}</td>
                <td>{request_status_display(pending_request['status'])}</td>
                <td>
                    <a href="/request/{pending_request['request_id']}/edit?return_to=submitted">
                        Edit Request
                    </a>
                </td>
            </tr>
            """

        pending_html += "</table>"

    invitation_title = html_escape_module.escape(
        safe_text(invitation["invitation_title"])
    )

    primary_name = html_escape_module.escape(
        safe_text(invitation["primary_name"]),
        quote=True
    )

    primary_email = html_escape_module.escape(
        safe_text(invitation["primary_email"]),
        quote=True
    )

    profile_additional_names = html_escape_module.escape(
        safe_text(invitation["additional_names"]),
        quote=True
    )

    profile_pet_notes = html_escape_module.escape(
        safe_text(invitation["pet_notes"]),
        quote=True
    )

    profile_food_notes = html_escape_module.escape(
        safe_text(invitation["food_notes"])
    )

    pet_yes_selected = ""
    pet_no_selected = "selected"

    if profile_pet_notes.lower() in ("yes", "y", "true", "1"):
        pet_yes_selected = "selected"
        pet_no_selected = ""

    return f"""
    {nav_links()}

    <h1>Request Another Visit</h1>

    <div style="
        display: flex;
        gap: 40px;
        align-items: flex-start;
        flex-wrap: wrap;
    ">

        <div style="
            flex: 1;
            min-width: 340px;
            max-width: 520px;
        ">

            <div style="
                border: 1px solid #dee2e6;
                background-color: #f8f9fa;
                padding: 14px;
                margin-bottom: 10px;
                border-radius: 8px;
            ">

                <h2 style="
                    margin-top: 0;
                    margin-bottom: 8px;
                ">
                    Invitation Request Form
                </h2>

                <p style="
                    margin-bottom: 0;
                ">
                    <strong>{invitation_title}</strong>
                </p>

            </div>

            <h3>Guest</h3>

            <p>
                <strong>Name:</strong> {primary_name}<br>
                <strong>Email:</strong> {primary_email}
            </p>

            <h3>Your Current Confirmed Dates</h3>

            {previous_html}

            <h3>Your Pending Requests</h3>

            {pending_html}


        </div>

        <div style="
            flex: 1;
            min-width: 340px;
            max-width: 620px;
        ">

            <form method="POST"
                  action="/submit"
                  onsubmit="return checkUnavailableDates();">

                <input type="hidden" name="invitation_id" value="{invitation_id}">
                <input type="hidden" name="name" value="{primary_name}">
                <input type="hidden" name="email" value="{primary_email}">
                <input type="hidden" name="adults" value="1">
                <input type="hidden" name="children" value="0">

                <label style="font-size: 18px; font-weight: bold;">
                    <strong style="font-size: 22px; font-weight: bold;">Choose the number of bedrooms you need first then the dates.</strong>
                </label><br>

                <div style="font-size: 18px; font-weight: bold; margin-bottom: 4px;">Each bedroom sleeps up to 2 guests.</div>

                <select name="rooms_requested" id="rooms_requested" style="font-size: 18px; padding: 7px; min-height: 40px;">
                    <option value="1">1 Bedroom</option>
                    <option value="2">2 Bedrooms</option>
                    <option value="3">3 Bedrooms</option>
                    <option value="4">4 Bedrooms</option>
                </select>

                <br>

                {calendar_html}

                <h3 style="font-size: 22px; font-weight: bold;">Selected Stay</h3>

                <p id="date_selection_message" style="font-size: 18px; font-weight: bold; color: #0d6efd;">
           No dates selected yet.
                </p>

                <p id="nights_message" style="font-size: 18px; font-weight: bold; color: #198754;">
        </p>

                <input type="hidden"
                       id="arrival_date"
                       name="arrival_date"
                       value="">

                <input type="hidden"
                       id="departure_date"
                       name="departure_date"
                       value="">

                <button type="button" onclick="resetDateSelection();" style="padding: 8px 12px; font-size: 16px; font-weight: bold; background-color: #0d6efd; color: white; border: none; border-radius: 8px;">Clear Selected Dates and Start Over</button>

                <br>

                <label>
<strong style="font-size: 22px; font-weight: bold;">Additional Guest Name(s) for Your Room(s)</strong>
                </label><br>

                <small style="font-size: 18px; font-weight: bold;">Please include everyone expected to stay.</small><br>

                <textarea name="additional_names" rows="2" style="width: 100%; max-width: 980px; font-size: 18px; padding: 9px; line-height: 1.35;">{profile_additional_names}</textarea>

                <br>

                <label>
                    <strong style="font-size: 22px; font-weight: bold;">Bringing a pet?</strong>
                </label><br>

                <select name="pets" style="font-size: 18px; padding: 7px; min-height: 40px;">
                    <option value="No" {pet_no_selected}>No</option>
                    <option value="Yes" {pet_yes_selected}>Yes</option>
                </select>

                <br>

                <label>
                    <strong style="font-size: 22px; font-weight: bold;">Food Restrictions or Preferences</strong>
                </label><br>

                <small style="font-size: 18px; font-weight: bold;">Optional — dietary restrictions, allergies, etc.</small><br>

                <textarea name="food_restrictions" rows="3" style="width: 100%; max-width: 980px; font-size: 18px; padding: 9px; line-height: 1.35;">{profile_food_notes}</textarea>

                <br>

                <label>
                    <strong style="font-size: 22px; font-weight: bold;">Comments or Notes</strong>
                </label><br>

                <textarea name="comments" rows="2" style="width: 100%; max-width: 980px; font-size: 18px; padding: 9px; line-height: 1.35;"></textarea>

                <br>

                <input type="submit" value="Submit Visit Request" style="padding: 8px 12px; font-size: 16px; font-weight: bold; background-color: #0d6efd; color: white; border: none; border-radius: 8px;">

            </form>

        </div>

    </div>

    <script>
        const blockedDates = {blocked_list};
        const roomCapacity = {room_capacity};
        const totalRooms = {total_rooms};

        let nextDateField = "arrival";
        let selectedArrivalCell = null;
        let selectedDepartureCell = null;


        const dateSelectionStorageKey = "shore_home_date_selection_" + window.location.pathname;

        function saveDateSelectionState() {{
            try {{
                localStorage.setItem(
                    dateSelectionStorageKey,
                    JSON.stringify({{
                        arrival: document.getElementById("arrival_date").value,
                        departure: document.getElementById("departure_date").value,
                        next: nextDateField
                    }})
                );
            }} catch (error) {{}}
        }}

        function restoreDateSelectionState() {{
            try {{
                const queryState = new URLSearchParams(window.location.search);
                const queryArrival = queryState.get("arrival_date") || "";
                const queryDeparture = queryState.get("departure_date") || "";
                const queryNext = queryState.get("next_date_field") || "";

                // V35.2.2: request pages must open with no stale selected stay.
                // Only explicit query params from calendar navigation are allowed.
                if (queryArrival) {{
                    document.getElementById("arrival_date").value = queryArrival;
                    nextDateField = queryNext || "departure";
                }}

                if (queryDeparture) {{
                    document.getElementById("departure_date").value = queryDeparture;
                    nextDateField = queryNext || "arrival";
                }}

                if (!queryArrival && !queryDeparture) {{
                    document.getElementById("arrival_date").value = "";
                    document.getElementById("departure_date").value = "";
                    document.getElementById("date_selection_message").innerText = "No dates selected yet.";
                    document.getElementById("nights_message").innerText = "";
                    nextDateField = "arrival";

                    try {{
                        localStorage.removeItem(dateSelectionStorageKey);
                    }} catch (storageError) {{}}
                }}
            }} catch (error) {{}}
        }}

        function getRequestedRooms() {{
            return parseInt(document.getElementById("rooms_requested").value);
        }}

        function formatDateForMessage(dateString) {{
            const parts = dateString.split("-");
            return parts[1] + "/" + parts[2] + "/" + parts[0];
        }}

        function getRoomsOpen(dateString) {{
            if (roomCapacity[dateString] === undefined) {{
                return totalRooms;
            }}

            return roomCapacity[dateString];
        }}

        function clearSelectedCellColors() {{
            if (selectedArrivalCell) {{
                selectedArrivalCell.style.outline = "";
                selectedArrivalCell.style.backgroundColor = selectedArrivalCell.dataset.originalColor;
            }}

            if (selectedDepartureCell) {{
                selectedDepartureCell.style.outline = "";
                selectedDepartureCell.style.backgroundColor = selectedDepartureCell.dataset.originalColor;
            }}

            selectedArrivalCell = null;
            selectedDepartureCell = null;
        }}

        function resetDateSelection() {{
            document.getElementById("arrival_date").value = "";
            document.getElementById("departure_date").value = "";

            document.getElementById("date_selection_message").innerText =
                "No dates selected yet.";

            document.getElementById("nights_message").innerText = "";

            clearSelectedCellColors();

            nextDateField = "arrival";

            try {{
                localStorage.removeItem(dateSelectionStorageKey);
            }} catch (error) {{}}
        }}

        function updateNightsMessage() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const nightsMessage = document.getElementById("nights_message");

            if (!arrival || !departure || departure <= arrival) {{
                nightsMessage.innerText = "";
                return;
            }}

            const arrivalDate = new Date(arrival + "T00:00:00");
            const departureDate = new Date(departure + "T00:00:00");

            const nights = Math.round(
                (departureDate - arrivalDate) / (1000 * 60 * 60 * 24)
            );

            nightsMessage.innerText =
                "Requested stay: "
                + nights
                + " night"
                + (nights === 1 ? "" : "s")
                + " / "
                + getRequestedRooms()
                + " bedroom"
                + (getRequestedRooms() === 1 ? "" : "s");
        }}

        function selectCalendarDate(dateString) {{
            const requestedRooms = getRequestedRooms();
            const roomsOpen = getRoomsOpen(dateString);

            if (blockedDates.includes(dateString)) {{
                alert(formatDateForMessage(dateString) + " is blocked.");
                return;
            }}

            if (roomsOpen < requestedRooms) {{
                alert(
                    "Only "
                    + roomsOpen
                    + " bedroom(s) available on "
                    + formatDateForMessage(dateString)
                );

                return;
            }}

            const clickedCell =
                document.querySelector('[data-date="' + dateString + '"]');

            if (clickedCell && !clickedCell.dataset.originalColor) {{
                clickedCell.dataset.originalColor =
                    clickedCell.style.backgroundColor;
            }}

            const arrivalField = document.getElementById("arrival_date");
            const departureField = document.getElementById("departure_date");
            const message = document.getElementById("date_selection_message");

            if (nextDateField === "arrival" && arrivalField.value && !departureField.value && dateString > arrivalField.value) {{
                nextDateField = "departure";
            }}

            if (nextDateField === "arrival") {{
                clearSelectedCellColors();

                arrivalField.value = dateString;
                departureField.value = "";
                nextDateField = "departure";

                if (clickedCell) {{
                    selectedArrivalCell = clickedCell;
                    clickedCell.style.backgroundColor = "#9ec5fe";
                    clickedCell.style.outline = "3px solid #0d6efd";
                }}

                message.innerText =
                    "Arrival selected: "
                    + formatDateForMessage(dateString)
                    + ". Now click a departure date.";

                updateNightsMessage();
                saveDateSelectionState();

            }} else {{

                if (dateString <= arrivalField.value) {{
                    alert("Departure date must be after arrival date.");
                    return;
                }}

                departureField.value = dateString;
                nextDateField = "arrival";

                if (clickedCell) {{
                    selectedDepartureCell = clickedCell;
                    clickedCell.style.backgroundColor = "#b6d7a8";
                    clickedCell.style.outline = "3px solid #198754";
                }}

                message.innerText =
                    "Selected stay: "
                    + formatDateForMessage(arrivalField.value)
                    + " to "
                    + formatDateForMessage(dateString)
                    + ".";

                updateNightsMessage();
                saveDateSelectionState();
            }}
        }}

        restoreDateSelectionState();
        updateNightsMessage();
        preserveCalendarNavigationSelection();

        document.getElementById("rooms_requested")
            .addEventListener("change", function () {{
                resetDateSelection();
                updateNightsMessage();
            }});

        function preserveCalendarNavigationSelection() {{
            document.querySelectorAll('[data-calendar-nav="1"]').forEach(function (link) {{
                link.addEventListener("click", function () {{
                    const arrival = document.getElementById("arrival_date").value;
                    const departure = document.getElementById("departure_date").value;
                    const url = new URL(link.href, window.location.origin);

                    if (arrival) {{
                        url.searchParams.set("arrival_date", arrival);
                    }}

                    if (departure) {{
                        url.searchParams.set("departure_date", departure);
                    }}

                    url.searchParams.set("next_date_field", nextDateField || "arrival");
                    link.href = url.toString();
                }});
            }});
        }}

        function checkUnavailableDates() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const requestedRooms = getRequestedRooms();

            if (!arrival || !departure) {{
                alert("Please select both an arrival date and a departure date from the calendar.");
                return false;
            }}

            if (departure <= arrival) {{
                alert("Error: Departure date must be after the arrival date.");
                return false;
            }}

            let current = new Date(arrival + "T00:00:00");
            const end = new Date(departure + "T00:00:00");

            while (current < end) {{
                const dateString = current.toISOString().slice(0, 10);

                if (blockedDates.includes(dateString)) {{
                    alert(
                        "Error: "
                        + formatDateForMessage(dateString)
                        + " is blocked."
                    );

                    return false;
                }}

                const roomsOpen = getRoomsOpen(dateString);

                if (roomsOpen < requestedRooms) {{
                    alert(
                        "Only "
                        + roomsOpen
                        + " bedroom(s) available on "
                        + formatDateForMessage(dateString)
                    );

                    return false;
                }}

                current.setDate(current.getDate() + 1);
            }}

            return true;
        }}

        // Do not reset here: preserve arrival/departure when navigating calendar months.
    </script>
    """


@app.route("/invitation/<int:invitation_id>/request")
def invitation_request_alias(invitation_id):

    return invite_request(invitation_id)



def render_request_submitted_page(request_id):

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (
        request_id,
    )).fetchone()

    if not request_row:

        conn.close()

        return f"""
        {nav_links()}
        <h1>Request Not Found</h1>
        <p>The request could not be found.</p>
        <p><a href="/">Back</a></p>
        """

    guest_profile_id = request_row["guest_profile_id"]

    confirmed_bookings = conn.execute("""
        SELECT
            bookings.arrival_date,
            bookings.departure_date,
            rooms.name AS room_name
        FROM bookings
        JOIN booking_requests ON bookings.request_id = booking_requests.id
        JOIN rooms ON bookings.room_id = rooms.id
        WHERE booking_requests.guest_profile_id = ?
          AND bookings.status = 'approved'
        ORDER BY bookings.arrival_date
    """, (
        guest_profile_id,
    )).fetchall()

    pending_requests = conn.execute("""
        SELECT
            id,
            arrival_date,
            departure_date,
            rooms_requested,
            status
        FROM booking_requests
        WHERE guest_profile_id = ?
          AND status IN ('pending', 'change_requested', 'cancel_requested')
        ORDER BY arrival_date DESC
    """, (
        guest_profile_id,
    )).fetchall()

    invitation_id = request_row["invitation_id"]

    conn.close()

    if invitation_id:
        another_link = f"/invite/{invitation_id}"
    else:
        another_link = "/new-request"

    nights = date_range_nights(
        request_row["arrival_date"],
        request_row["departure_date"]
    )

    html = nav_links() + f"""
    <h1 style="margin-bottom: 6px;">Thanks!</h1>

    <div style="
        background-color: #e8f7ea;
        border: 1px solid #198754;
        padding: 12px 14px;
        border-radius: 8px;
        margin-bottom: 10px;
        max-width: 780px;
        line-height: 1.4;
    ">
        <p style="font-weight: bold; margin-top: 0;">
            Your request has been submitted.
        </p>
        <p style="margin-bottom: 0;">
            <strong>What happens next?</strong><br>
            Please wait for confirmation. I’ll review the dates and room space and follow up if anything needs to change.
        </p>
    </div>

    <p>
        <strong>Requested Dates:</strong>
        {format_date(request_row['arrival_date'])}
        to
        {format_date(request_row['departure_date'])}
        ({nights} night{"s" if nights != 1 else ""})
    </p>

    <p>
        <strong>Rooms Requested:</strong>
        {request_row['rooms_requested'] or 1}
    </p>

    <p>
        <a href="{another_link}">
            Request a Visit
        </a>
    </p>

    <div style="
        background-color: #f8f9fa;
        border: 1px solid #ddd;
        padding: 10px 12px;
        border-radius: 8px;
        max-width: 780px;
        margin: 10px 0;
    ">
        <strong>Your request is now pending review.</strong><br>
        Please wait for confirmation before making changes. If something urgent changes, just reply to the email or contact John & Mark directly.
    </div>

    <p>
        <a href="/request-submitted/complete">
            Done
        </a>
    </p>
    """

    if confirmed_bookings:

        html += """
        <h2>Your Current Confirmed Dates</h2>
        <ul>
        """

        for booking in confirmed_bookings:
            booking_nights = date_range_nights(
                booking["arrival_date"],
                booking["departure_date"]
            )

            html += f"""
            <li>
                {format_date(booking['arrival_date'])}
                to
                {format_date(booking['departure_date'])}
                ({booking_nights} night{"s" if booking_nights != 1 else ""})
                — Room: {booking['room_name']}
            </li>
            """

        html += "</ul>"

    if pending_requests:

        html += """
        <h2>Your Pending Requests</h2>
        <ul>
        """

        for pending_request in pending_requests:
            pending_nights = date_range_nights(
                pending_request["arrival_date"],
                pending_request["departure_date"]
            )

            html += f"""
            <li>
                {format_date(pending_request['arrival_date'])}
                to
                {format_date(pending_request['departure_date'])}
                ({pending_nights} night{"s" if pending_nights != 1 else ""})
                — {request_status_display(pending_request['status'])}
            </li>
            """

        html += "</ul>"

    return html


@app.route("/request/<int:request_id>/submitted")
def request_submitted_review(request_id):

    return render_request_submitted_page(request_id)


@app.route("/request-submitted/complete")
def request_submitted_complete():

    return f"""
    {nav_links()}

    <h1>All Set!</h1>

    <div style="
        background-color: #e8f7ea;
        border: 1px solid #198754;
        padding: 12px 14px;
        border-radius: 8px;
        max-width: 760px;
        line-height: 1.4;
    ">
        <p style="font-weight: bold; margin-top: 0;">
            You’re done for now. Nice work.
        </p>
        <p style="margin-bottom: 0;">
            I’ll review everything and send an update when there’s news.
            No need to keep refreshing — unless you really enjoy suspense.
        </p>
    </div>
    """

@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name")
    email = request.form.get("email")
    additional_names = request.form.get("additional_names")
    arrival = request.form.get("arrival_date") or request.form.get("arrival")
    departure = request.form.get("departure_date") or request.form.get("departure")
    adults = request.form.get("adults") or "1"
    children = request.form.get("children")
    pets = request.form.get("pets")
    food_restrictions = request.form.get("food_restrictions")
    comments = request.form.get("comments")
    coordination_notes = request.form.get("coordination_notes")
    invitation_id = request.form.get("invitation_id")
    rooms_requested = request.form.get("rooms_requested") or "1"

    try:
        rooms_requested = int(rooms_requested)
    except:
        rooms_requested = 1

    if rooms_requested < 1:
        rooms_requested = 1

    if rooms_requested > 4:
        rooms_requested = 4

    if invitation_id == "":
        invitation_id = None

    conn = get_db_connection()
    cursor = conn.cursor()

    if invitation_id and (not safe_text(name).strip() or not safe_text(email).strip()):

        invitation_guest = conn.execute("""
            SELECT
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM invitations

            JOIN guest_profiles
                ON invitations.guest_profile_id = guest_profiles.id

            WHERE invitations.id = ?
        """, (
            invitation_id,
        )).fetchone()

        if invitation_guest:

            if not safe_text(name).strip():
                name = invitation_guest["primary_name"]

            if not safe_text(email).strip():
                email = invitation_guest["primary_email"]

    validation_error = request_identity_validation_error(
        name,
        email
    )

    if validation_error:

        conn.close()

        return request_identity_error_page(
            validation_error,
            "javascript:history.back()"
        )

    ensure_house_block_columns(conn)

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked,
        int(conn.execute("SELECT COUNT(*) AS count FROM rooms").fetchone()["count"] or 4)
    )

    bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    total_rooms = conn.execute(
        "SELECT COUNT(*) AS count FROM rooms"
    ).fetchone()["count"]

    try:
        arrival_date_obj = datetime.strptime(arrival, "%Y-%m-%d")
        departure_date_obj = datetime.strptime(departure, "%Y-%m-%d")
    except:
        conn.close()
        return """
        <h2>Invalid dates.</h2>
        <p>Please go back and select valid arrival and departure dates.</p>
        """

    if departure_date_obj <= arrival_date_obj:
        conn.close()
        return """
        <h2>Invalid date range.</h2>
        <p>Departure date must be after arrival date.</p>
        """

    current = arrival_date_obj

    while current < departure_date_obj:
        date_str = current.strftime("%Y-%m-%d")

        if date_str in blocked_dates:
            conn.close()
            return f"""
            <h2>Request Not Submitted</h2>
            <p>{format_date(date_str)} is blocked and unavailable.</p>
            <p><a href="javascript:history.back()">Go Back and Change Request</a></p>
            """

        rooms_used = 0

        for booking in bookings:
            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            )

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            )

            if booking_start <= current < booking_end:
                rooms_used += 1

        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            date_str,
            total_rooms
        )

        rooms_open = capacity_limit - rooms_used

        if rooms_open < rooms_requested:
            conn.close()
            return f"""
            <h2>Request Not Submitted</h2>
            <p>
                Only {rooms_open} room(s) are available on {format_date(date_str)}.
                You requested {rooms_requested} room(s).
            </p>
            <p><a href="javascript:history.back()">Go Back and Change Request</a></p>
            """

        current += timedelta(days=1)

    existing_profile = conn.execute(
        "SELECT * FROM guest_profiles WHERE primary_email = ?",
        (email,)
    ).fetchone()

    if existing_profile:
        guest_profile_id = existing_profile["id"]
    else:
        cursor.execute("""
            INSERT INTO guest_profiles
            (primary_name, primary_email, phone, additional_names, pet_notes, food_notes, host_notes, photo_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            email,
            "",
            additional_names,
            pets,
            food_restrictions,
            "Auto-created from request. Review recommended.",
            "",
            "needs_review"
        ))

        guest_profile_id = cursor.lastrowid

    cursor.execute("""
    INSERT INTO booking_requests
    (
        name,
        email,
        additional_names,
        arrival_date,
        departure_date,
        adults,
        children,
        pets,
        food_restrictions,
        comments,
        coordination_notes,
        status,
        guest_profile_id,
        invitation_id,
        rooms_requested
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    name,
    email,
    additional_names,
    arrival,
    departure,
    adults,
    children,
    pets,
    food_restrictions,
    comments,
    coordination_notes,
    "pending",
    guest_profile_id,
    invitation_id,
    rooms_requested
))
    new_request_id = cursor.lastrowid

    if invitation_id:
        conn.execute(
            "UPDATE invitations SET status = ? WHERE id = ?",
            ("replied", invitation_id)
        )

    confirmed_bookings = conn.execute("""
        SELECT
            bookings.arrival_date,
            bookings.departure_date,
            rooms.name AS room_name
        FROM bookings
        JOIN booking_requests ON bookings.request_id = booking_requests.id
        JOIN rooms ON bookings.room_id = rooms.id
        WHERE booking_requests.guest_profile_id = ?
        ORDER BY bookings.arrival_date
    """, (guest_profile_id,)).fetchall()

    conn.commit()
    conn.close()

    notify_admin(
        "Request another visit submitted",
        f"Guest: {safe_text(name)}\nArrival: {format_date(arrival)}\nDeparture: {format_date(departure)}\nRooms: {rooms_requested}",
        f"/request/{new_request_id}"
    )

    return redirect(f"/request/{new_request_id}/submitted")

    if invitation_id:
        another_link = f"/invite/{invitation_id}"
        done_link = f"/invite/{invitation_id}"
    else:
        another_link = "/"
        done_link = "/"

    nights = (
        datetime.strptime(departure, "%Y-%m-%d")
        - datetime.strptime(arrival, "%Y-%m-%d")
    ).days

    html = nav_links() + f"""
    <h1>Request Submitted</h1>

    <p>Your request has been submitted successfully.</p>
    <p>We will review your request and follow up.</p>

    <p>
        <strong>Requested Dates:</strong>
        {format_date(arrival)} to {format_date(departure)}
        ({nights} night{"s" if nights != 1 else ""})
    </p>

    <p>
        <strong>Rooms Requested:</strong>
        {rooms_requested}
    </p>

    <p>
        <a href="{another_link}">
            Submit Another Request
        </a>
    </p>

    <p>
        <a href="/request/{new_request_id}">
            Review or Edit This Request
        </a>
    </p>

    <p>
        <a href="{done_link}">
            Done
        </a>
    </p>
    """

    if confirmed_bookings:
        html += """
        <h2>Your Confirmed Stay(s)</h2>
        <ul>
        """

        for booking in confirmed_bookings:
            booking_nights = (
                datetime.strptime(booking["departure_date"], "%Y-%m-%d")
                - datetime.strptime(booking["arrival_date"], "%Y-%m-%d")
            ).days

            html += f"""
            <li>
                {format_date(booking['arrival_date'])}
                to
                {format_date(booking['departure_date'])}
                ({booking_nights} night{"s" if booking_nights != 1 else ""})
                — Room: {booking['room_name']}
            </li>
            """

        html += "</ul>"

    return html

@app.route("/approve/<int:request_id>", methods=["POST"])
def approve_request(request_id):

    response_message = request.form.get("response_message")

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not request_row:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href='/requests'>
                Back to requests
            </a>
        </p>
        """

    was_already_approved = safe_text(request_row["status"]).strip() == "approved"
    is_coordination_converted_request = False

    try:
        is_coordination_converted_request = bool(request_row["coordination_group_id"])
    except:
        is_coordination_converted_request = False

    current_rooms = request_row["rooms_requested"]

    if not current_rooms:
        current_rooms = 1

    validation_error = request_identity_validation_error(
        request_row["name"],
        request_row["email"]
    )

    if validation_error:

        profile_row = None

        try:
            guest_profile_id = request_row["guest_profile_id"]
        except:
            guest_profile_id = None

        if guest_profile_id:

            profile_row = conn.execute("""
                SELECT
                    primary_name,
                    primary_email
                FROM guest_profiles
                WHERE id = ?
            """, (
                guest_profile_id,
            )).fetchone()

        if not profile_row and request_row["invitation_id"]:

            profile_row = conn.execute("""
                SELECT
                    guest_profiles.primary_name,
                    guest_profiles.primary_email
                FROM invitations

                JOIN guest_profiles
                    ON invitations.guest_profile_id = guest_profiles.id

                WHERE invitations.id = ?
            """, (
                request_row["invitation_id"],
            )).fetchone()

        if profile_row:

            replacement_name = request_row["name"]
            replacement_email = request_row["email"]

            if not clean_text(replacement_name):
                replacement_name = profile_row["primary_name"]

            if not clean_text(replacement_email):
                replacement_email = profile_row["primary_email"]

            validation_error = request_identity_validation_error(
                replacement_name,
                replacement_email
            )

            if not validation_error:

                conn.execute("""
                    UPDATE booking_requests
                    SET name = ?,
                        email = ?
                    WHERE id = ?
                """, (
                    replacement_name,
                    replacement_email,
                    request_id
                ))

                conn.commit()

                request_row = conn.execute("""
                    SELECT *
                    FROM booking_requests
                    WHERE id = ?
                """, (
                    request_id,
                )).fetchone()

        validation_error = request_identity_validation_error(
            request_row["name"],
            request_row["email"]
        )

        if validation_error:

            conn.close()

            return request_identity_error_page(
                validation_error,
                f"/request/{request_id}"
            )

    invitation_id = request_row["invitation_id"]

    change_values = latest_change_values(
        request_row["comments"]
    )

    effective_arrival_date = clean_text(
        change_values["new_arrival"]
    )

    effective_departure_date = clean_text(
        change_values["new_departure"]
    )

    effective_rooms_requested = clean_text(
        change_values["new_rooms"]
    )

    if not effective_arrival_date:
        effective_arrival_date = request_row["arrival_date"]

    if not effective_departure_date:
        effective_departure_date = request_row["departure_date"]

    if not effective_rooms_requested:
        effective_rooms_requested = request_row["rooms_requested"]

    rooms_requested = effective_rooms_requested

    if not rooms_requested:
        rooms_requested = 1

    rooms_requested = int(rooms_requested)

    if rooms_requested < 1:
        rooms_requested = 1

    if rooms_requested > 4:
        rooms_requested = 4

    selected_room_ids = []

    for i in range(1, rooms_requested + 1):

        room_id = request.form.get(f"room_id_{i}")

        if room_id:
            selected_room_ids.append(room_id)

    if not selected_room_ids:

        old_room_id = request.form.get("room_id")

        if old_room_id:
            selected_room_ids.append(old_room_id)

    if len(selected_room_ids) != rooms_requested:

        conn.close()

        return f"""
        <h2>Not enough rooms selected.</h2>

        <p>
            This request needs {rooms_requested} room(s).
        </p>

        <p>
            Please select {rooms_requested} room(s)
            before approving.
        </p>

        <p>
            <a href='/requests'>
                Back to requests
            </a>
        </p>
        """

    if len(selected_room_ids) != len(set(selected_room_ids)):

        conn.close()

        return """
        <h2>Duplicate room selected.</h2>

        <p>
            Please choose a different room
            for each room assignment.
        </p>

        <p>
            <a href='/requests'>
                Back to requests
            </a>
        </p>
        """

    # V31.1: tentative coordination holds reserve capacity before final booking.
    # For coordination handoff approvals, ignore that request's own group hold.
    exclude_coordination_group_id = None

    try:
        exclude_coordination_group_id = request_row["coordination_group_id"]
    except Exception:
        exclude_coordination_group_id = None

    tentative_holds = get_coordination_tentative_holds(
        conn,
        exclude_group_id=exclude_coordination_group_id
    )

    total_rooms_row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()

    total_rooms = int(total_rooms_row["count"] or 4)

    ensure_house_block_columns(conn)
    blocked_for_capacity = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()
    blocked_dates_for_capacity, room_limit_by_date = build_blocked_date_capacity(
        blocked_for_capacity,
        total_rooms
    )

    try:
        check_start = datetime.strptime(effective_arrival_date, "%Y-%m-%d").date()
        check_end = datetime.strptime(effective_departure_date, "%Y-%m-%d").date()
    except Exception:
        check_start = None
        check_end = None

    if check_start and check_end:

        current_check_date = check_start

        while current_check_date < check_end:

            current_date_string = current_check_date.strftime("%Y-%m-%d")

            approved_rooms_used_row = conn.execute("""
                SELECT COUNT(*) AS count
                FROM bookings
                WHERE status = 'approved'
                  AND request_id != ?
                  AND arrival_date <= ?
                  AND departure_date > ?
            """, (
                request_id,
                current_date_string,
                current_date_string
            )).fetchone()

            approved_rooms_used = int(approved_rooms_used_row["count"] or 0)
            tentative_rooms_held = 0

            for tentative_hold in tentative_holds:
                try:
                    hold_start = datetime.strptime(tentative_hold["arrival_date"], "%Y-%m-%d").date()
                    hold_end = datetime.strptime(tentative_hold["departure_date"], "%Y-%m-%d").date()
                    hold_rooms = int(tentative_hold.get("rooms_held", 1) or 1)
                except Exception:
                    continue

                if hold_start <= current_check_date < hold_end:
                    tentative_rooms_held += hold_rooms

            capacity_limit = room_capacity_limit_for_date(
                room_limit_by_date,
                current_date_string,
                total_rooms
            )

            rooms_open_after_holds = capacity_limit - approved_rooms_used - tentative_rooms_held

            if rooms_open_after_holds < rooms_requested:

                conn.close()

                return f"""
                <h2>Not enough room capacity for those dates.</h2>

                <p>
                    {format_date(current_date_string)} has only
                    {rooms_open_after_holds} room(s) open after approved bookings
                    and tentative coordination holds are counted.
                </p>

                <p>
                    This protects coordination dates before final booking is complete.
                </p>

                <p>
                    <a href='/requests'>
                        Back to requests
                    </a>
                </p>
                """

            current_check_date += timedelta(days=1)

    ensure_house_block_columns(conn)

    blocked_conflict = conn.execute("""
        SELECT *
        FROM blocked_dates
        WHERE start_date < ?
          AND end_date > ?
          AND COALESCE(is_full_block, 1) = 1
    """, (
        effective_departure_date,
        effective_arrival_date
    )).fetchone()

    if blocked_conflict:

        conn.close()

        return """
        <h2>These dates are blocked.</h2>

        <p>
            The requested stay overlaps
            a blocked date range.
        </p>

        <p>
            <a href='/requests'>
                Back to requests
            </a>
        </p>
        """

    selected_room_names = []

    for room_id in selected_room_ids:

        conflict = conn.execute("""
            SELECT *
            FROM bookings
            WHERE room_id = ?
              AND request_id != ?
              AND status = 'approved'
              AND arrival_date < ?
              AND departure_date > ?
        """, (
            room_id,
            request_id,
            effective_departure_date,
            effective_arrival_date
        )).fetchone()

        if conflict:

            conn.close()

            return f"""
            <h2>Room is not available for those dates.</h2>

            <p>
                One of the selected rooms already
                has an overlapping booking.
            </p>

            <p>
                Room ID: {room_id}
            </p>

            <p>
                <a href='/requests'>
                    Back to requests
                </a>
            </p>
            """

        room = conn.execute("""
            SELECT name
            FROM rooms
            WHERE id = ?
        """, (room_id,)).fetchone()

        if room:
            selected_room_names.append(room["name"])

    backup_path = create_database_backup(
        "before_approve_request"
    )

    try:

        conn.execute("BEGIN")

        approval_email_status = "needs_email"
        approval_email_needed_type = "approval"
        activity_action_type = "request_approved"

        if was_already_approved:
            approval_email_status = "needs_update"
            approval_email_needed_type = "date_change"
            activity_action_type = "room_reassigned"

        if is_coordination_converted_request:
            approval_email_status = "not_needed"
            approval_email_needed_type = ""

        conn.execute("""
            UPDATE booking_requests
            SET status = ?,
                arrival_date = ?,
                departure_date = ?,
                rooms_requested = ?,
                response_message = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "approved",
            effective_arrival_date,
            effective_departure_date,
            rooms_requested,
            response_message,
            approval_email_status,
            approval_email_needed_type,
            request_id
        ))

        conn.execute("""
            DELETE FROM bookings
            WHERE request_id = ?
        """, (request_id,))

        for room_id in selected_room_ids:

            conn.execute("""
                INSERT INTO bookings
                (request_id, room_id, arrival_date, departure_date, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                request_id,
                room_id,
                effective_arrival_date,
                effective_departure_date,
                "approved"
            ))

        if invitation_id:

            conn.execute("""
                UPDATE invitations
                SET status = ?
                WHERE id = ?
            """, (
                "accepted",
                invitation_id
            ))
        
        write_activity_log(
            conn,
            request_id,
            activity_action_type,
            request_row["status"],
            "approved",
            f"Rooms assigned: {', '.join(selected_room_names)}. Backup: {backup_path}"
        )

        conn.commit()
        if was_already_approved:
            conn.close()

            return f"""
            {nav_links()}

            <h1>Room Assignment Updated</h1>

            <p style="color: green; font-weight: bold;">
                The room assignment was updated.
            </p>

            <p>
                No email was sent automatically.
            </p>

            <p>
                This reservation is now marked for an update email.
            </p>

            <p>
                <a href="/room-assignments">Back to Room Assignments</a> |
                <a href="/request/{request_id}/email-preview">Preview / Send Update Email</a>
            </p>
            """

        recipient_email = resolve_request_recipient_email(
            conn,
            request_row
        )

        if not recipient_email:
            recipient_email = safe_text(
                request_row["email"]
            ).strip()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            "/requests"
        )

    if is_coordination_converted_request:

        coordination_group_id = ""

        try:
            coordination_group_id = request_row["coordination_group_id"]
        except:
            coordination_group_id = ""
        
        conn.close()

        return nav_links() + f"""
  
    
        <h1>Coordination Request Approved</h1>

        <p>
            The room assignment and approval have been saved.
        </p>

        <div style="
            background-color: #e7f1ff;
            border-left: 4px solid #2563eb;
            padding: 12px;
            max-width: 850px;
            margin-bottom: 14px;
        ">
            <strong>No individual approval email was sent.</strong><br>
            This request was created from a coordination group, so the guest should receive the
            final group confirmation email from the Booking Handoff page after all rooms are assigned
            and all coordination requests are approved.
        </div>

        <p>
            <a href="/coordination-group/{coordination_group_id}/handoff">
                Back to Booking Handoff
            </a>
            |
            <strong style="color: #198754;">Done</strong>
            |
            <a href="/room-assignments">
                Room Assignments
            </a>
        </p>
        """

    nights = (
        datetime.strptime(
            effective_departure_date,
            "%Y-%m-%d"
        )
        -
        datetime.strptime(
            effective_arrival_date,
            "%Y-%m-%d"
        )
    ).days

    room_names = []

    for room_name in selected_room_names:
        room_names.append(room_name)

    room_list = ", ".join(room_names)

    optional_admin_message = ""

    if response_message:
        optional_admin_message = response_message.strip() + "\n"

    additional_names = combined_confirmed_group_members(
        conn,
        request_row
    )

    coordinating_with = safe_text(
        request_row["coordination_notes"]
    ).strip()

    if coordinating_with:
        coordinating_with_section = (
            f"Coordinating With: {coordinating_with}\n"
        )
    else:
        coordinating_with_section = ""

    approval_email_subject = (
        "Your Strathmere Shore Visit is Confirmed"
    )

    change_request_link = (
        f"{BASE_URL}/request/{request_id}/change"
    )

    cancel_request_link = (
        f"{BASE_URL}/request/{request_id}/cancel"
    )

    repeat_visit_url = repeat_visit_request_url_for_row(request_row)

    approval_email_body = render_email_template(
        "approval.txt",
        guest_name=safe_text(request_row["name"]),
        arrival_date=format_date(
            effective_arrival_date
        ),
        departure_date=format_date(
            effective_departure_date
        ),
        nights=nights,
        rooms_requested=rooms_requested,
        additional_names=additional_names,
        room_list=room_list,
        coordinating_with_section=coordinating_with_section,
        optional_admin_message=optional_admin_message
    )

    approval_email_body = approval_email_body.replace(
        "Additional Guests for Your Room(s):",
        "Confirmed Group Members:"
    ).replace(
        "Additional Guests:",
        "Confirmed Group Members:"
    )

    approval_email_body = append_guest_visit_history_summary(
        approval_email_body,
        conn,
        request_row,
        request_id
    )

    approval_email_body = ensure_guest_change_links(
        approval_email_body,
        request_id,
        repeat_visit_url
    )

    html = nav_links() + f"""
    <h1>Request Approved</h1>

    <p>
        The request has been approved
        and the room assignment has been saved.
    </p>

    <p>
        <strong>Email Status:</strong>
        Needs approval email
    </p>

    <h2>Email Preview</h2>

    <p>
        <strong>To:</strong>
        {recipient_email}
    </p>

    <p>
        <strong>Subject:</strong>
        {approval_email_subject}
    </p>

    <form method="POST"
          action="/send-preview-email">

        <input type="hidden"
               name="request_id"
               value="{request_id}">

        <input type="hidden"
               name="email_type"
               value="approval">

        <input type="hidden"
               name="to_email"
               value="{recipient_email}">

        <input type="hidden"
               name="subject"
               value="{approval_email_subject}">

        <input type="hidden"
               name="return_to"
               value="/request/{request_id}">

        <textarea id="approval_email_body"
                  name="body"
                  rows="26"
                  cols="90"
                  style="
                      width: 100%;
                      max-width: 900px;
                  ">{approval_email_body}</textarea>

        <br>

        <button type="button"
                onclick="copyApprovalEmail();">
            Copy Email Body
        </button>

        <button type="submit">
            Send Email
        </button>

    </form>

    <p id="copy_message"
       style="
           font-weight: bold;
           color: green;
       "></p>

    <script>
        function copyApprovalEmail() {{

            const emailBody =
                document.getElementById(
                    "approval_email_body"
                );

            emailBody.select();

            emailBody.setSelectionRange(
                0,
                99999
            );

            navigator.clipboard.writeText(
                emailBody.value
            );

            document.getElementById(
                "copy_message"
            ).innerText = "Email copied.";
        }}

        document.querySelectorAll('input[name="calendar_target"]')
            .forEach(function (field) {{
                field.addEventListener("change", updateCalendarTargetMessage);
            }});

        updateCalendarTargetMessage();

    </script>

    <br>

    <p>
        <a href="/requests">
            Back to Request Review
        </a>
        |
        <strong style="color: #198754;">Done</strong>
    </p>
    """

    return html
@app.route("/booking-audit")
def booking_audit():

    conn = get_db_connection()

    problems, total_rooms, approved_booking_count, approved_request_count = get_booking_audit_problems(conn)

    recent_bookings = conn.execute("""
        SELECT
            bookings.id,
            bookings.request_id,
            bookings.room_id,
            bookings.arrival_date,
            bookings.departure_date,
            bookings.status,
            booking_requests.name AS guest_name,
            booking_requests.created_at AS request_created_at,
            (
                SELECT MAX(activity_log.created_at)
                FROM activity_log
                WHERE activity_log.request_id = bookings.request_id
            ) AS last_activity_at,
            rooms.name AS room_name
        FROM bookings
        LEFT JOIN booking_requests
            ON bookings.request_id = booking_requests.id
        LEFT JOIN rooms
            ON bookings.room_id = rooms.id
        ORDER BY
            COALESCE(last_activity_at, booking_requests.created_at, bookings.arrival_date) DESC,
            bookings.id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    html = nav_links() + f"""
    <h1>Booking Audit</h1>

    <div style="
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 10px;
        max-width: 900px;
        margin-bottom: 16px;
    ">
        <div style="background:#f8f9fa; border:1px solid #ddd; padding:12px; border-radius:8px;">
            <strong>Total Rooms</strong><br>{total_rooms}
        </div>
        <div style="background:#f8f9fa; border:1px solid #ddd; padding:12px; border-radius:8px;">
            <strong>Approved Requests</strong><br>{approved_request_count}
        </div>
        <div style="background:#f8f9fa; border:1px solid #ddd; padding:12px; border-radius:8px;">
            <strong>Approved Bookings</strong><br>{approved_booking_count}
        </div>
        <div style="background:#f8f9fa; border:1px solid #ddd; padding:12px; border-radius:8px;">
            <strong>Problems Found</strong><br>{len(problems)}
        </div>
    </div>
    """

    if problems:
        html += """
        <div style="background:#fff3cd; border-left:5px solid #fd7e14; padding:12px; border-radius:6px; margin-bottom:12px;">
            <strong>Booking audit found items to review.</strong>
        </div>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
            <tr style="background:#f5f5f5;">
                <th>Severity</th>
                <th>Type</th>
                <th>Details</th>
                <th>Open</th>
            </tr>
        """

        for problem in problems:
            html += f"""
            <tr>
                <td>{safe_text(problem.get('severity', 'Review'))}</td>
                <td><strong>{safe_text(problem.get('type'))}</strong></td>
                <td>{safe_text(problem.get('details'))}</td>
                <td><a href="{safe_text(problem.get('link', '/bookings'))}">Open</a></td>
            </tr>
            """

        html += "</table>"

    else:
        html += """
        <div style="background:#d4edda; border-left:5px solid #198754; padding:12px; border-radius:6px; margin-bottom:12px;">
            <strong>No booking audit problems found.</strong>
        </div>
        """

    html += """
    <div style="background:#f8fbff; border-left:5px solid #0d6efd; padding:10px; border-radius:6px; margin:14px 0;">
        <strong>Last Updated column added:</strong> shows the most recent activity timestamp for each booking/request, falling back to the request created date.
    </div>
    <h2>Recent Bookings / Last Updated</h2>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
        <tr style="background:#f5f5f5;">
            <th>ID</th>
            <th>Guest</th>
            <th>Room</th>
            <th>Arrival</th>
            <th>Departure</th>
            <th>Status</th>
            <th style="background:#e8f7ea;">Last Updated</th>
            <th>Request</th>
        </tr>
    """

    for booking in recent_bookings:
        html += f"""
        <tr>
            <td>{booking['id']}</td>
            <td>{safe_text(booking['guest_name'])}</td>
            <td>{display_room_name(booking['room_name'])}</td>
            <td>{format_date(booking['arrival_date'])}</td>
            <td>{format_date(booking['departure_date'])}</td>
            <td>{safe_text(booking['status'])}</td>
            <td>{format_datetime_display(booking['last_activity_at'] or booking['request_created_at'])}</td>
            <td><a href="/request/{booking['request_id']}">Open</a></td>
        </tr>
        """

    html += """
    </table>

    <p><a href="/dashboard">Back to Dashboard</a></p>
    """

    return html


@app.route("/status-sanity")
def status_sanity():

    conn = get_db_connection()

    rows = conn.execute("""
        SELECT
            booking_requests.id,
            booking_requests.name,
            booking_requests.email,
            booking_requests.status,
            booking_requests.email_status,
            booking_requests.email_needed_type,
            booking_requests.rooms_requested,
            COUNT(bookings.id) AS booking_count
        FROM booking_requests
        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'
        GROUP BY booking_requests.id
        ORDER BY booking_requests.created_at DESC
    """).fetchall()

    profiles = conn.execute("""
        SELECT *
        FROM guest_profiles
        ORDER BY primary_name
    """).fetchall()

    invitations = conn.execute("""
        SELECT
            invitations.id,
            invitations.guest_profile_id,
            invitations.status,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM invitations
        LEFT JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id
        ORDER BY invitations.created_at DESC
    """).fetchall()

    conn.close()

    problems = []

    for profile in profiles:

        validation_error = guest_profile_validation_error(
            profile["primary_name"],
            profile["primary_email"]
        )

        if validation_error:

            problems.append({
                "type": "Guest Profile Missing Required Data",
                "details": f"Guest profile ID {profile['id']} is missing required data: {validation_error}",
                "link": f"/profile/{profile['id']}/edit"
            })

    for invitation in invitations:

        if not invitation["primary_name"]:

            problems.append({
                "type": "Invitation Missing Guest Profile",
                "details": f"Invitation ID {invitation['id']} points to a missing guest profile.",
                "link": "/invitations"
            })

        else:

            validation_error = guest_profile_validation_error(
                invitation["primary_name"],
                invitation["primary_email"]
            )

            if validation_error:

                problems.append({
                    "type": "Invitation Guest Profile Cannot Receive Email",
                    "details": f"Invitation ID {invitation['id']} is tied to a guest profile with missing required data: {validation_error}",
                    "link": f"/profile/{invitation['guest_profile_id']}/edit"
                })

    for row in rows:

        booking_count = row["booking_count"]
        status = row["status"]
        email_status = row["email_status"]
        email_needed_type = row["email_needed_type"]

        if not clean_text(row["name"]):

            problems.append({
                "type": "Request Missing Guest Name",
                "details": f"Request ID {row['id']} does not have a guest name.",
                "link": f"/request/{row['id']}"
            })

        if not is_valid_email_address(row["email"]):

            problems.append({
                "type": "Request Missing Guest Email",
                "details": f"Request ID {row['id']} does not have a valid guest email address.",
                "link": f"/request/{row['id']}"
            })

        if status == "approved" and booking_count == 0:

            problems.append({
                "type": "Approved Request Missing Booking",
                "details": f"{row['name']} is approved, but has no approved booking rows.",
                "link": f"/request/{row['id']}"
            })

        if status in ["declined", "cancelled"] and booking_count > 0:

            problems.append({
                "type": "Inactive Request Still Has Booking",
                "details": f"{row['name']} has status {status}, but still has {booking_count} approved booking row(s).",
                "link": f"/request/{row['id']}"
            })

        if email_status == "sent" and email_needed_type:

            problems.append({
                "type": "Sent Email Still Has Needed Type",
                "details": f"{row['name']} has email_status sent, but email_needed_type is still {email_needed_type}.",
                "link": f"/request/{row['id']}"
            })

        if email_status in ["needs_email", "needs_update"] and not email_needed_type:

            problems.append({
                "type": "Email Needed Type Missing",
                "details": f"{row['name']} needs an email, but the email type is blank.",
                "link": f"/request/{row['id']}"
            })

    html = nav_links() + """
    <h1>Status Sanity Check</h1>

    <p>
        This page looks for status combinations that usually mean
        something got stuck or partially updated.
    </p>
    """

    if not problems:

        html += """
        <div style="
            background-color: #d4edda;
            border-left: 5px solid #198754;
            padding: 14px;
            margin-top: 18px;
            border-radius: 6px;
            font-weight: bold;
            color: #155724;
        ">
            No status problems found.
        </div>
        """

    else:

        html += f"""
        <div style="
            background-color: #fff3cd;
            border-left: 5px solid #fd7e14;
            padding: 14px;
            margin-top: 18px;
            margin-bottom: 10px;
            border-radius: 6px;
            font-weight: bold;
            color: #664d03;
        ">
            {len(problems)} status problem(s) found.
        </div>
        """

        html += """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">

            <tr style="background-color: #f5f5f5;">
                <th align="left">Problem</th>
                <th align="left">Details</th>
                <th align="left">Review</th>
            </tr>
        """

        for problem in problems:

            html += f"""
            <tr>
                <td>{problem['type']}</td>
                <td>{problem['details']}</td>
                <td>
                    <a href="{problem['link']}">
                        Review
                    </a>
                </td>
            </tr>
            """

        html += "</table>"

    return html

@app.route("/activity-log")
def activity_log_page():

    conn = get_db_connection()

    ensure_activity_log_table(conn)

    logs = conn.execute("""
        SELECT
            activity_log.*,
            booking_requests.name AS guest_name
        FROM activity_log
        LEFT JOIN booking_requests
            ON activity_log.request_id = booking_requests.id
        ORDER BY activity_log.created_at DESC,
                 activity_log.id DESC
        LIMIT 200
    """).fetchall()

    conn.close()

    html = nav_links() + """
    <h1>Activity Log</h1>

    <p>
        This is a safety timeline of major admin actions.
    </p>
    """

    if not logs:

        html += "<p>No activity has been logged yet.</p>"

    else:

        html += """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">

            <tr style="background-color: #f5f5f5;">
                <th align="left">When</th>
                <th align="left">Action</th>
                <th align="left">Guest</th>
                <th align="left">Old Status</th>
                <th align="left">New Status</th>
                <th align="left">Notes</th>
                <th align="left">View</th>
            </tr>
        """

        for log in logs:

            guest_name = log["guest_name"] or ""

            view_link = ""

            if log["request_id"]:

                view_link = f"""
                <a href="/request/{log['request_id']}">
                    View
                </a>
                """

            html += f"""
            <tr>
                <td>{log['created_at']}</td>
                <td>{log['action_type']}</td>
                <td>{guest_name}</td>
                <td>{log['old_status'] or ''}</td>
                <td>{log['new_status'] or ''}</td>
                <td>{log['notes'] or ''}</td>
                <td>{view_link}</td>
            </tr>
            """

        html += "</table>"

    return html

@app.route("/rooms")

def rooms_page():
    conn = get_db_connection()
    rooms = conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
    conn.close()

    html = nav_links() + "<h1>Rooms</h1>"

    for room in rooms:
        html += f"""
        <hr>
        <p><strong>Name:</strong> {room['name']}</p>
        <p><strong>Floor:</strong> {room['floor']}</p>
        <p><strong>Bed Type:</strong> {room['bed_type']}</p>
        <p><strong>Capacity:</strong> {room['capacity']}</p>
        <p><strong>Pet Friendly:</strong> {room['pet_friendly']}</p>
        """

    return html

@app.route("/bookings")
def bookings_page():

    conn = get_db_connection()

    group_by = request.args.get("group", "date")

    if group_by not in ["date", "email"]:
        group_by = "date"

    order_clause = """
        bookings.arrival_date,
        booking_requests.id,
        rooms.name
    """

    if group_by == "email":

        order_clause = """
            booking_requests.email,
            bookings.arrival_date,
            booking_requests.id,
            rooms.name
        """

    bookings = conn.execute(f"""
        SELECT
            bookings.id,
            bookings.request_id,
            booking_requests.name AS guest_name,
            booking_requests.email,
            booking_requests.additional_names,
            booking_requests.food_restrictions,
            booking_requests.pets,
            booking_requests.coordination_notes,
            booking_requests.email_status,
            booking_requests.email_needed_type,
            booking_requests.status AS request_status,
            bookings.arrival_date,
            bookings.departure_date,
            bookings.status,
            rooms.name AS room_name
        FROM bookings

        JOIN booking_requests
            ON bookings.request_id = booking_requests.id

        JOIN rooms
            ON bookings.room_id = rooms.id

        ORDER BY {order_clause}
    """).fetchall()

    conflicts = set()

    for i, b1 in enumerate(bookings):

        for j, b2 in enumerate(bookings):

            if i == j:
                continue

            if b1["room_name"] == b2["room_name"]:

                if not (
                    b1["departure_date"] <= b2["arrival_date"]
                    or b1["arrival_date"] >= b2["departure_date"]
                ):
                    conflicts.add(b1["id"])

    conn.close()

    date_link_style = ""
    email_link_style = ""

    if group_by == "date":
        date_link_style = "font-weight: bold;"
    else:
        email_link_style = "font-weight: bold;"

    html = nav_links() + f"""
    <h1>Confirmed Stays</h1>

    <p>
        <a href="/bookings?group=date"
           style="{date_link_style}">
           View by Arrival Date
        </a>

        |

        <a href="/bookings?group=email"
           style="{email_link_style}">
           View by Email
        </a>
    </p>
    """

    if not bookings:

        html += "<p>No bookings found.</p>"

    else:

        html += """
        <style>
            .confirmed-stays-table {
                border-collapse: collapse;
                width: auto;
                max-width: 100%;
                table-layout: auto;
                font-size: 12px;
            }
            .confirmed-stays-table th,
            .confirmed-stays-table td {
                padding: 3px 5px;
                white-space: nowrap;
                vertical-align: top;
            }
            .confirmed-stays-table td.notes,
            .confirmed-stays-table td.guests {
                white-space: normal;
                max-width: 220px;
            }
        </style>
        <table class="confirmed-stays-table"
               border="1"
               cellpadding="3"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   table-layout: auto;
                   font-size: 13px;
               ">

            <tr style="background-color: #f2f2f2;">
                <th style="min-width: 90px;">Guest</th>
                <th style="min-width: 130px;">Email</th>
                <th style="min-width: 140px;">Additional Guests</th>
                <th style="min-width: 120px;">Food / Pets / Coordination</th>
                <th style="min-width: 100px;">Room</th>
                <th style="min-width: 55px;">Arrival</th>
                <th style="min-width: 55px;">Depart</th>
                <th style="min-width: 45px;">Nights</th>
                <th style="min-width: 110px;">Booking Status</th>
                <th style="min-width: 140px;">Request Status</th>
                <th style="min-width: 110px;">Email Status</th>
                <th style="min-width: 70px;">View</th>
                <th style="min-width: 110px;">Admin Cancel</th>
            </tr>
        """

        previous_group = None
        previous_request_id = None

        for booking in bookings:

            arrival_short = short_date(
                booking["arrival_date"]
            )

            departure_short = short_date(
                booking["departure_date"]
            )

            if group_by == "date":

                current_group = booking["arrival_date"]
                group_label = f"Arrival: {arrival_short}"

            else:

                current_group = booking["email"]
                group_label = f"Email: {booking['email']}"

            if current_group != previous_group:

                html += f"""
                <tr>
                    <td colspan="13"
                        style="
                            background-color: #eee;
                            font-weight: bold;
                            font-size: 13px;
                            padding: 4px;
                        ">
                        {group_label}
                    </td>
                </tr>
                """

                previous_group = current_group
                previous_request_id = None

            nights = (
                datetime.strptime(
                    booking["departure_date"],
                    "%Y-%m-%d"
                )
                -
                datetime.strptime(
                    booking["arrival_date"],
                    "%Y-%m-%d"
                )
            ).days

            conflict_note = ""

            if booking["id"] in conflicts:

                conflict_note = """
                <br>

                <strong style='color: red;'>
                    Conflict
                </strong>
                """

            additional_guests = safe_text(
                booking["additional_names"]
            )

            food_preferences = safe_text(
                booking["food_restrictions"]
            )

            pets = safe_text(
                booking["pets"]
            )

            coordination_comments = safe_text(
                booking["coordination_notes"]
            )

            planning_details_html = f"""
            <div>
                <strong>Food:</strong> {food_preferences}
            </div>

            <div>
                <strong>Pets:</strong> {pets}
            </div>

            <div>
                <strong>Coordination:</strong> {coordination_comments}
            </div>
            """

            email_display = email_status_display(
                booking["email_status"],
                booking["email_needed_type"],
                booking["request_id"]
            )

            show_guest_info = (
                booking["request_id"] != previous_request_id
            )

            request_status = booking["request_status"]

            if request_status == "pending":

                request_status_html = """
                <strong style='color: orange;'>
                    Pending Review
                </strong>
                """

            elif request_status == "change_requested":

                request_status_html = """
                <strong style='color: orange;'>
                    Change Requested
                </strong>
                """

            elif request_status == "cancel_requested":

                request_status_html = """
                <strong style='color: red;'>
                    Cancel Requested
                </strong>
                """

            elif request_status == "cancelled":

                request_status_html = """
                <strong style='color: red;'>
                    Cancelled
                </strong>
                """

            else:

                request_status_html = f"""
                <strong style='color: green;'>
                    {request_status.title()}
                </strong>
                """

            if show_guest_info:

                guest_name_html = booking["guest_name"]

                email_html = booking["email"]

                additional_guest_html = additional_guests

                planning_details_display = planning_details_html

                email_status_html = email_display

                view_html = f"""
                <a href="/request/{booking['request_id']}">
                    View
                </a>
                """

                admin_cancel_html = f"""
                <a href="/request/{booking['request_id']}/admin-cancel-confirmed"
                   style="color:#dc3545; font-weight:bold;">
                    Cancel Visit
                </a>
                """

            else:

                guest_name_html = ""

                email_html = ""

                additional_guest_html = ""

                planning_details_display = ""

                email_status_html = ""

                view_html = ""

                admin_cancel_html = ""

            html += f"""
            <tr>

                <td style="vertical-align: top;">
                    {guest_name_html}
                </td>

                <td style="
                        vertical-align: top;
                        word-break: break-word;
                    ">
                    {email_html}
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                    ">
                    {additional_guest_html}
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                    ">
                    {planning_details_display}
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                        overflow-wrap: anywhere;
                    ">
                    {booking['room_name']}
                    {conflict_note}
                </td>

                <td style="vertical-align: top;">
                    {arrival_short}
                </td>

                <td style="vertical-align: top;">
                    {departure_short}
                </td>

                <td style="
                        vertical-align: top;
                        text-align: center;
                    ">
                    {nights}
                </td>

                <td style="vertical-align: top;">
                    <strong style='color: green;'>
                        Approved
                    </strong>
                </td>

                <td style="vertical-align: top;">
                    {request_status_html}
                </td>

                <td style="vertical-align: top;">
                    {email_status_html}
                </td>

                <td style="vertical-align: top;">
                    {view_html}
                </td>

                <td style="vertical-align: top;">
                    {admin_cancel_html}
                </td>

            </tr>
            """

            previous_request_id = booking["request_id"]

        html += "</table>"

    return html

@app.route("/requests")
def requests_page():

    conn = get_db_connection()

    filter_status = request.args.get("filter")
    search = request.args.get("search", "").strip().lower()

    base_query = """
        SELECT
            booking_requests.*,
            invitations.invitation_title
        FROM booking_requests

        LEFT JOIN invitations
            ON booking_requests.invitation_id = invitations.id
    """

    where_clauses = []
    params = []

    if filter_status == "pending":

        where_clauses.append(
            "booking_requests.status = 'pending'"
        )

    elif filter_status == "approved":

        where_clauses.append(
            "booking_requests.status = 'approved'"
        )

    elif filter_status == "declined":

        where_clauses.append(
            "booking_requests.status = 'declined'"
        )

    elif filter_status == "needs_email":

        where_clauses.append("""
            booking_requests.email_status
            IN ('needs_email', 'needs_update')
        """)

    elif filter_status == "coordination":

        where_clauses.append("""
            booking_requests.coordination_notes IS NOT NULL
            AND TRIM(booking_requests.coordination_notes) != ''
        """)

    if search:

        where_clauses.append("""
            (
                LOWER(booking_requests.name) LIKE ?
                OR LOWER(booking_requests.email) LIKE ?
            )
        """)

        params.append(f"%{search}%")
        params.append(f"%{search}%")

    if where_clauses:

        base_query += " WHERE " + " AND ".join(where_clauses)

    base_query += """
        ORDER BY
            booking_requests.arrival_date,
            booking_requests.status,
            booking_requests.name
    """

    rows = conn.execute(
        base_query,
        params
    ).fetchall()

    rooms = conn.execute("""
        SELECT *
        FROM rooms
        ORDER BY id
    """).fetchall()

    existing_bookings = conn.execute("""
        SELECT
            bookings.room_id,
            bookings.arrival_date,
            bookings.departure_date,
            bookings.request_id
        FROM bookings
        WHERE bookings.status = 'approved'
    """).fetchall()

    conn.close()

    if filter_status:
        active = filter_status
    else:
        active = "all"

    def request_filter_link(label, value):

        if active == value:

            return f"<strong>{label}</strong>"

        else:

            return (
                f"<a href='/requests?filter={value}'>"
                f"{label}</a>"
            )

    html = nav_links() + f"""
    <h1>Request Review</h1>

    <p>
        {request_filter_link("All", "all")} |
        {request_filter_link("Pending", "pending")} |
        {request_filter_link("Approved", "approved")} |
        {request_filter_link("Declined", "declined")} |
        {request_filter_link("Needs Email", "needs_email")} |
        {request_filter_link("Coordination", "coordination")}
    </p>

    <form method="GET"
          action="/requests"
          style="margin-bottom: 18px;">

        <input type="hidden"
               name="filter"
               value="{active if active != 'all' else ''}">

        <input type="text"
               name="search"
               value="{search}"
               placeholder="Search guest or email"
               style="
                   padding: 6px;
                   width: 240px;
               ">

        <button type="submit">
            Search
        </button>

    </form>
    """

    if not rows:

        html += "<p>No requests found.</p>"

    else:

        html += """
        <table border="1"
               cellpadding="4"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   table-layout: auto;
                   font-size: 13px;
               ">

            <tr style="background-color: #f2f2f2;">

                <th style="min-width: 90px;">
                    Guest
                </th>

                <th style="min-width: 130px;">
                    Email
                </th>

                <th style="min-width: 130px;">
                    Additional Guests
                </th>

                <th style="min-width: 220px;">
                    Comments / Coordination
                </th>

                <th style="min-width: 55px;">
                    Arrival
                </th>

                <th style="min-width: 55px;">
                    Depart
                </th>

                <th style="min-width: 45px;">
                    Nights
                </th>

                <th style="min-width: 45px;">
                    Rooms
                </th>

                <th style="min-width: 90px;">
                    Status
                </th>

                <th style="min-width: 130px;">
                    Email Status
                </th>

                <th style="min-width: 180px;">
                    Actions
                </th>

            </tr>
        """

        previous_arrival = None

        for row in rows:

            arrival_short = short_date(
                row["arrival_date"]
            )

            departure_short = short_date(
                row["departure_date"]
            )

            if row["arrival_date"] != previous_arrival:

                html += f"""
                <tr>

                    <td colspan="11"
                        style="
                            background-color: #ddd;
                            font-weight: bold;
                            font-size: 13px;
                            padding: 6px;
                        ">

                        Arrival: {arrival_short}

                    </td>

                </tr>
                """

                previous_arrival = row["arrival_date"]

            status = row["status"]

            coordination_notes = safe_text(
                row["coordination_notes"]
            )

            if coordination_notes:

                row_background = "#e7f1ff"

            elif (
                status == "pending"
                or status == "change_requested"
                or status == "cancel_requested"
            ):

                row_background = "#fff8d6"

            elif (
                row["email_status"] == "needs_email"
                or row["email_status"] == "needs_update"
            ):

                row_background = "#ffe5e5"

            elif status == "declined":

                row_background = "#f2f2f2"

            else:

                row_background = "#ffffff"

            if status == "change_requested":

                status_display = """
                <strong style='color: orange;'>
                    Change Requested
                </strong>
                """

            elif status == "cancel_requested":

                status_display = """
                <strong style='color: red;'>
                    Cancel Requested
                </strong>
                """

            else:

                status_display = request_status_display(
                    status
                )

            email_display = email_status_display(
                row["email_status"],
                row["email_needed_type"],
                row["id"]
            )

            next_step = "No action needed."

            if row["status"] == "pending":
                next_step = "Assign room and approve, or decline."

            elif row["status"] == "change_requested":
                next_step = "Review the requested changes."

            elif row["status"] == "cancel_requested":
                next_step = "Review the cancellation request."

            elif row["email_status"] == "needs_email":
                next_step = "Approval email is ready to send."

            elif row["email_status"] == "needs_update":
                next_step = "Room update email is ready to send."
                
            rooms_requested = row["rooms_requested"] or 1

            rooms_requested = int(rooms_requested)

            rooms_requested = max(1, rooms_requested)

            rooms_requested = min(4, rooms_requested)

            nights = (
                datetime.strptime(
                    row["departure_date"],
                    "%Y-%m-%d"
                )
                - datetime.strptime(
                    row["arrival_date"],
                    "%Y-%m-%d"
                )
            ).days

            additional_guests = safe_text(
                row["additional_names"]
            )

            comments = safe_text(
                row["comments"]
            )

            coordination_html = ""

            if coordination_notes:

                coordination_html = f"""
                <div style="
                    margin-top: 10px;
                    padding: 8px;
                    background-color: #dbeafe;
                    border-left: 4px solid #2563eb;
                    border-radius: 4px;
                ">

                    <div style="
                        font-size: 11px;
                        font-weight: bold;
                        color: #1d4ed8;
                        margin-bottom: 4px;
                        text-transform: uppercase;
                        letter-spacing: 0.5px;
                    ">
                        Coordination Mentioned
                    </div>

                    <div style="
                        font-size: 13px;
                        font-weight: bold;
                        color: #003366;
                    ">
                        {coordination_notes}
                    </div>

                </div>
                """

            actions = f"""
            <div style="
                display: flex;
                flex-wrap: wrap;
                gap: 4px;
            ">

                <a href="/request/{row['id']}"
                   style="
                       background-color: #0d6efd;
                       color: white;
                       padding: 4px 8px;
                       text-decoration: none;
                       border-radius: 4px;
                       font-size: 12px;
                       font-weight: bold;
                   ">
                    View
                </a>

            </div>
            """

            if status == "pending":

                booked_room_ids = set()

                for booking in existing_bookings:

                    if booking["request_id"] == row["id"]:

                        continue

                    if not (
                        booking["departure_date"] <= row["arrival_date"]
                        or booking["arrival_date"] >= row["departure_date"]
                    ):

                        booked_room_ids.add(
                            booking["room_id"]
                        )

                room_selects_html = ""

                for i in range(1, rooms_requested + 1):

                    room_options = ""

                    for room in rooms:

                        if room["id"] in booked_room_ids:

                            room_options += f"""
                            <option value="{room['id']}" disabled>
                                {room['name']} - BOOKED
                            </option>
                            """

                        else:

                            room_options += f"""
                            <option value="{room['id']}">
                                {room['name']} - Available
                            </option>
                            """

                    room_selects_html += f"""
                    <label>
                        <strong>Room {i}:</strong>
                    </label><br>

                    <select name="room_id_{i}"
                            style="width: 150px;">

                        {room_options}

                    </select><br>
                    """

                actions += f"""

                <div style="margin-top: 8px;">

                <form method="POST"
                      action="/approve/{row['id']}">

                    {room_selects_html}

                    <label>
                        <strong>Approval Note:</strong>
                    </label><br>

                    <textarea name="response_message"
                              rows="2"
                              style="
                                  width: 160px;
                                  font-size: 12px;
                              "></textarea><br>

                    <div style="
                        display: flex;
                        gap: 6px;
                        flex-wrap: wrap;
                    ">

                        <button type="submit"
                                style="
                                    background-color: #198754;
                                    color: white;
                                    border: none;
                                    padding: 6px 10px;
                                    border-radius: 4px;
                                    font-size: 12px;
                                    font-weight: bold;
                                    cursor: pointer;
                                ">
                            Approve
                        </button>

                        <a href="/decline/{row['id']}"
                           style="
                               background-color: #dc3545;
                               color: white;
                               padding: 6px 10px;
                               text-decoration: none;
                               border-radius: 4px;
                               font-size: 12px;
                               font-weight: bold;
                           ">
                            Decline
                        </a>

                    </div>

                </form>

                </div>
                """



            elif status == "change_requested":

                actions += f"""

                <div style="margin-top: 8px;">

                    <a href="/request/{row['id']}"
                       style="
                           background-color: #fd7e14;
                           color: white;
                           padding: 6px 10px;
                           text-decoration: none;
                           border-radius: 4px;
                           font-size: 12px;
                           font-weight: bold;
                           display: inline-block;
                       ">
                        Review Change
                    </a>

                </div>
                """


            elif status == "cancel_requested":

                actions += f"""

                <div style="margin-top: 8px;">

                    <a href="/request/{row['id']}"
                       style="
                           background-color: #dc3545;
                           color: white;
                           padding: 6px 10px;
                           text-decoration: none;
                           border-radius: 4px;
                           font-size: 12px;
                           font-weight: bold;
                           display: inline-block;
                       ">
                        Approve Cancel
                    </a>

                </div>
                """

            html += f"""
            <tr style="
                    background-color: {row_background};
                ">

                <td style="vertical-align: top;">
                    {row['name']}
                </td>

                <td style="
                        vertical-align: top;
                        word-break: break-word;
                    ">
                    {row['email']}
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                    ">
                    {additional_guests}
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                    ">

                    {comments}

                    {coordination_html}

                </td>

                <td style="vertical-align: top;">
                    {arrival_short}
                </td>

                <td style="vertical-align: top;">
                    {departure_short}
                </td>

                <td style="
                        vertical-align: top;
                        text-align: center;
                    ">
                    {nights}
                </td>

                <td style="
                        vertical-align: top;
                        text-align: center;
                    ">
                    {rooms_requested}
                </td>

                <td style="vertical-align: top;">
                    {status_display}
                </td>

                <td style="vertical-align: top;">
                    {email_display}
                </td>

                <td style="vertical-align: top;">
                    {actions}
                </td>

            </tr>
            """

        html += "</table>"

    return html
@app.route("/profiles", methods=["GET", "POST"])
def profiles_page():
    conn = get_db_connection()
    ensure_guest_profile_welcome_column(conn)
    filter_status = request.args.get("filter")

    if request.method == "POST":
        primary_name = clean_text(request.form.get("primary_name"))
        primary_email = clean_text(request.form.get("primary_email")).lower()
        phone = request.form.get("phone")
        additional_names = request.form.get("additional_names")
        pet_notes = request.form.get("pet_notes")
        food_notes = request.form.get("food_notes")
        host_notes = request.form.get("host_notes")
        photo_path = request.form.get("photo_path")
        status = request.form.get("status")

        validation_error = guest_profile_validation_error(
            primary_name,
            primary_email
        )

        if validation_error:

            conn.close()

            return profile_error_page(
                validation_error,
                "/profiles"
            )

        welcome_message = ""

        try:
            cursor = conn.execute("""
                INSERT INTO guest_profiles
                (
                    primary_name,
                    primary_email,
                    phone,
                    additional_names,
                    pet_notes,
                    food_notes,
                    host_notes,
                    photo_path,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                primary_name,
                primary_email,
                phone,
                additional_names,
                pet_notes,
                food_notes,
                host_notes,
                photo_path,
                status
            ))

            new_profile_id = cursor.lastrowid

            conn.commit()

            if request.form.get("send_welcome_email", "no") == "yes":

                try:
                    send_profile_welcome_email(
                        conn,
                        new_profile_id,
                        force=False
                    )
                    conn.commit()
                    welcome_message = "Guest profile saved and welcome email sent."
                except Exception as email_error:
                    welcome_message = "Guest profile saved, but welcome email failed: " + safe_text(email_error)

            else:
                welcome_message = "Guest profile saved. Welcome email was not sent."

        except Exception as e:

            conn.close()

            error_text = str(e)

            if "UNIQUE constraint failed" in error_text:

                return """
                <h2>Guest email already exists.</h2>

                <p>
                    A guest profile with this email address
                    is already in the system.
                </p>

                <p>
                    <a href="/profiles">
                        Back to Profiles
                    </a>
                </p>
                """

            return f"""
            <h2>Could not save profile.</h2>

            <p>{e}</p>

            <p>
                <a href="/profiles">
                    Back to Profiles
                </a>
            </p>
            """
    if filter_status in ["active", "needs_review", "archived"]:

        profiles = conn.execute("""
            SELECT *
            FROM guest_profiles
            WHERE status = ?
            ORDER BY primary_name
        """, (filter_status,)).fetchall()

    else:

        profiles = conn.execute("""
            SELECT *
            FROM guest_profiles
            ORDER BY primary_name
        """).fetchall()

    conn.close()

    try:
        welcome_message
    except NameError:
        welcome_message = ""

    welcome_message_html = ""

    if welcome_message:
        welcome_message_html = f"""
        <p style="color: green; font-weight: bold;">
            {safe_text(welcome_message)}
        </p>
        """

    html = nav_links() + f"""
    <h1>Guest Profiles</h1>

    {welcome_message_html}

    <p>
        <a href="/profiles">All</a> |
        <a href="/profiles?filter=active">Active</a> |
        <a href="/profiles?filter=needs_review">Needs Review</a> |
        <a href="/profiles?filter=archived">Archived</a>
    </p>

    <h2>Add Guest Profile</h2>

        <form method="POST" action="/profiles">

        <div style="
            border: 2px solid #0d6efd;
            background-color: #f8fbff;
            padding: 10px 12px;
            margin: 8px 0 14px 0;
            max-width: 700px;
            border-radius: 6px;
        ">
            <label style="font-weight: bold;">
                <input type="checkbox" name="send_welcome_email" value="yes">
                Send Welcome Email
            </label><br>
            <small style="color: #555;">
                Uses templates/emails/profile_welcome.txt. No invitation is sent yet.
            </small>
        </div>

        <label>Primary First Name:</label><br>
        <input type="text" name="primary_name" required><br>

        <label>Primary Email:</label><br>
        <input type="email" name="primary_email" required><br>

        <label>Phone:</label><br>
        <input type="text" name="phone"><br>

        <label>Additional Names:</label><br>
        <textarea name="additional_names"></textarea><br>

        <label>Pet Notes:</label><br>
        <textarea name="pet_notes"></textarea><br>

        <label>Food Notes:</label><br>
        <textarea name="food_notes"></textarea><br>

        <label>Host Notes:</label><br>
        <textarea name="host_notes"></textarea><br>

        <label>Photo Filename:</label><br>
        <small>
            Put image files in /static/profile_photos/ and enter only the filename here.
            Example: mary.jpg
        </small><br>
        <input type="text" name="photo_path"><br>

        <label>Status:</label><br>
        <select name="status">
            <option value="active">Active</option>
            <option value="needs_review">Needs Review</option>
            <option value="archived">Archived</option>
        </select><br><br>

        <button type="submit">Add Profile</button>
    </form>

    <h2>Existing Profiles</h2>
    """

    if not profiles:

        html += "<p>No guest profiles yet.</p>"

    else:

        html += """
        <table border="1" cellpadding="6" cellspacing="0"
               style="border-collapse: collapse; font-size: 13px;">
            <tr>
                <th>Photo</th>
                <th>Name</th>
                <th>Email</th>
                <th>Additional Names</th>
                <th>Status</th>
                <th>Welcome Email</th>
                <th>Profile</th>
                <th>Actions</th>
            </tr>
        """

        for profile in profiles:

            status = profile["status"]

            if status == "needs_review":
                status_display = "<strong style='color: orange;'>Needs Review</strong>"

            elif status == "active":
                status_display = "<strong style='color: green;'>Active</strong>"

            elif status == "archived":
                status_display = "<strong style='color: gray;'>Archived</strong>"

            else:
                status_display = status

            if status == "needs_review":
                action_links = f"<a href='/profile/{profile['id']}/activate'>Activate</a>"

            elif status == "active":
                action_links = f"<a href='/profile/{profile['id']}/archive'>Archive</a>"

            else:
                action_links = ""

            photo_path = safe_text(profile["photo_path"])

            if photo_path:
                photo_html = f"""
                <img src="/static/profile_photos/{photo_path}"
                     style="
                         width: 60px;
                         height: 60px;
                         object-fit: cover;
                         border-radius: 6px;
                     ">
                """
            else:
                photo_html = """
                <span style="color: gray; font-size: 12px;">
                    No photo
                </span>
                """

            html += f"""
            <tr>
                <td>{photo_html}</td>
                <td>{profile['primary_name']}</td>
                <td>{profile['primary_email']}</td>
                <td>{safe_text(profile['additional_names'])}</td>
                <td>{status_display}</td>
                <td>{safe_text(profile_welcome_status_text(profile))}</td>

                <td>
                    <a href="/profile/{profile['id']}">View</a> |
                    <a href="/profile/{profile['id']}/edit">Edit</a>
                </td>

                <td>
                    {action_links}
                    <br>
                    <a href="/profile/{profile['id']}/send-welcome">Send Welcome Email</a>
                </td>
            </tr>
            """

        html += "</table>"

    return html

@app.route("/profile/<int:profile_id>/send-welcome", methods=["GET", "POST"])
def send_profile_welcome_again(profile_id):

    conn = get_db_connection()
    ensure_guest_profile_welcome_column(conn)

    profile = conn.execute("""
        SELECT *
        FROM guest_profiles
        WHERE id = ?
    """, (
        profile_id,
    )).fetchone()

    if not profile:
        conn.close()
        return profile_error_page(
            "Guest profile not found.",
            "/profiles"
        )

    if request.method != "POST":

        sent_at = safe_text(row_value(profile, "welcome_email_sent_at")).strip()
        already_sent_note = ""

        if sent_at:
            already_sent_note = f"""
            <p style="color: #856404; font-weight: bold;">
                A welcome email was already sent on {safe_text(sent_at)}.
                Continue only if you want to send it again.
            </p>
            """

        conn.close()

        return f"""
        {nav_links()}

        <h1>Send Welcome Email</h1>

        <p>
            Send the profile welcome email to:
            <strong>{safe_text(profile['primary_name'])}</strong>
            &lt;{safe_text(profile['primary_email'])}&gt;
        </p>

        {already_sent_note}

        <form method="POST" action="/profile/{profile_id}/send-welcome">
            <input type="hidden" name="confirm_action" value="yes">
            <button type="submit">
                Send Welcome Email
            </button>
            &nbsp;
            <a href="/profiles">Cancel</a>
        </form>
        """

    try:
        if request.form.get("confirm_action") != "yes":
            conn.close()
            return redirect("/profiles")

        send_profile_welcome_email(
            conn,
            profile_id,
            force=True
        )

        conn.commit()
        conn.close()

        return f"""
        {nav_links()}

        <h1>Welcome Email Sent</h1>

        <p>
            Welcome email sent to {safe_text(profile['primary_email'])}.
        </p>

        <p>
            <a href="/profiles">Back to Guest Profiles</a>
        </p>
        """

    except Exception as error:
        rollback_and_close(conn)
        return transaction_error_page(
            error,
            "/profiles"
        )


@app.route("/profile/<int:profile_id>/archive", methods=["GET", "POST"])
def archive_profile(profile_id):

    if request.method == "POST":
        conn = get_db_connection()
        conn.execute(
            "UPDATE guest_profiles SET status = ? WHERE id = ?",
            ("archived", profile_id)
        )
        conn.commit()
        conn.close()

        return redirect("/profiles")

    html = nav_links() + f"""
    <h2>Are you sure you want to archive this profile?</h2>

    <form method="POST" action="/profile/{profile_id}/archive">
        <button type="submit">Yes, Archive</button>
    </form>

    <p><a href="/profiles">Cancel</a></p>
    """

    return html

@app.route("/invitations", methods=["GET", "POST"])
def invitations_page():

    conn = get_db_connection()

    selected_year = int(
        request.args.get(
            "year",
            datetime.now().year
        )
    )

    filter_status = request.args.get("filter")

    if request.method == "POST":

        guest_profile_id = request.form.get("guest_profile_id")
        invitation_title = request.form.get("invitation_title")
        message = request.form.get("message")

        selected_profile = conn.execute("""
            SELECT *
            FROM guest_profiles
            WHERE id = ?
        """, (guest_profile_id,)).fetchone()

        if not selected_profile:

            conn.close()

            return profile_error_page(
                "Please choose a valid guest profile before creating an invitation.",
                "/invitations"
            )

        validation_error = guest_profile_validation_error(
            selected_profile["primary_name"],
            selected_profile["primary_email"]
        )

        if validation_error:

            conn.close()

            return profile_error_page(
                "This guest profile cannot be invited yet: " + validation_error,
                f"/profile/{guest_profile_id}/edit"
            )

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO invitations
            (
                guest_profile_id,
                invitation_title,
                arrival_date,
                departure_date,
                message,
                status,
                response_notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            guest_profile_id,
            invitation_title,
            None,
            None,
            message,
            "draft",
            ""
        ))

        new_invitation_id = cursor.lastrowid

        conn.commit()

        if request.form.get("next_action") == "preview_send":

            conn.close()

            return redirect(f"/preview-invitation-email/{new_invitation_id}")

    profiles = conn.execute("""
        SELECT *
        FROM guest_profiles
        ORDER BY primary_name
    """).fetchall()

    valid_filters = [
        "draft",
        "sent",
        "responded",
        "closed"
    ]

    if filter_status in valid_filters:

        invitations = conn.execute("""
            SELECT
                invitations.id,
                invitations.guest_profile_id,
                invitations.invitation_title,
                invitations.message,
                invitations.status,
                invitations.response_notes,
                invitations.created_at,
                guest_profiles.primary_name,
                guest_profiles.primary_email,
                COUNT(booking_requests.id) AS request_count
            FROM invitations

            JOIN guest_profiles
                ON invitations.guest_profile_id = guest_profiles.id

            LEFT JOIN booking_requests
                ON booking_requests.invitation_id = invitations.id

            WHERE invitations.status = ?
              AND strftime('%Y', invitations.created_at) = ?

            GROUP BY invitations.id

            ORDER BY
                guest_profiles.primary_email,
                invitations.created_at DESC
        """, (
            filter_status,
            str(selected_year)
        )).fetchall()

    else:

        invitations = conn.execute("""
            SELECT
                invitations.id,
                invitations.guest_profile_id,
                invitations.invitation_title,
                invitations.message,
                invitations.status,
                invitations.response_notes,
                invitations.created_at,
                guest_profiles.primary_name,
                guest_profiles.primary_email,
                COUNT(booking_requests.id) AS request_count
            FROM invitations

            JOIN guest_profiles
                ON invitations.guest_profile_id = guest_profiles.id

            LEFT JOIN booking_requests
                ON booking_requests.invitation_id = invitations.id

            WHERE strftime('%Y', invitations.created_at) = ?

            GROUP BY invitations.id

            ORDER BY
                guest_profiles.primary_email,
                invitations.created_at DESC
        """, (
            str(selected_year),
        )).fetchall()

    invitation_requests = conn.execute("""
        SELECT
            booking_requests.id,
            booking_requests.guest_profile_id,
            booking_requests.invitation_id,
            booking_requests.name,
            booking_requests.email,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.status
        FROM booking_requests
        WHERE booking_requests.invitation_id IS NOT NULL
        ORDER BY booking_requests.created_at DESC
    """).fetchall()

    conn.close()

    not_invited = []
    draft_not_sent = []
    invited_no_request = []
    invited_replied = []

    invited_profile_ids = set()

    for invite in invitations:

        invited_profile_ids.add(
            invite["guest_profile_id"]
        )

    for profile in profiles:

        profile_id = profile["id"]

        matching_invites = [
            i for i in invitations
            if i["guest_profile_id"] == profile_id
        ]

        if not matching_invites:

            not_invited.append(profile)

            continue

        latest_invite = matching_invites[0]

        if latest_invite["status"] == "draft":

            draft_not_sent.append(profile)

        elif latest_invite["request_count"] > 0:

            invited_replied.append(profile)

        else:

            invited_no_request.append(profile)

    if filter_status in valid_filters:

        active = filter_status

    else:

        active = "all"

    def link(label, value):

        if active == value:

            return f"<strong>{label}</strong>"

        else:

            return (
                f"<a href='/invitations?"
                f"filter={value}&year={selected_year}'>"
                f"{label}</a>"
            )

    previous_year = selected_year - 1
    next_year = selected_year + 1

    html = nav_links() + f"""
    <h1>Invitations</h1>

    <p>
        <a href="/invitations?year={previous_year}">
            Previous Year
        </a>

        |

        <strong>
            {selected_year}
        </strong>

        |

        <a href="/invitations?year={next_year}">
            Next Year
        </a>
    </p>

    <p>
        {link("All", "all")} |
        {link("Draft", "draft")} |
        {link("Sent", "sent")} |
        {link("Responded", "responded")} |
        {link("Closed", "closed")}
    </p>

    <h2>Invitation Status Summary</h2>

    <table border="1"
           cellpadding="6"
           cellspacing="0"
           style="
               border-collapse: collapse;
               margin-bottom: 30px;
               min-width: 700px;
           ">

        <tr style="background-color: #f2f2f2;">
            <th>Status</th>
            <th>Count</th>
            <th>Guests</th>
        </tr>

        <tr style="background-color: #f8f9fa;">

            <td>
                <strong>
                    Not Invited
                </strong>
            </td>

            <td align="center">
                <strong>
                    {len(not_invited)}
                </strong>
            </td>

            <td>
    """

    if not_invited:

        for profile in not_invited:

            html += f"""
            <div style="margin-bottom: 6px;">
                {profile['primary_name']}
                ({profile['primary_email']})
            </div>
            """

    else:

        html += """
        <span style="color: gray;">
            None
        </span>
        """

    html += """
            </td>
        </tr>

        <tr style="background-color: #fff8d6;">

            <td>
                <strong>
                    Draft / Not Sent
                </strong>
            </td>

            <td align="center">
                <strong>
    """

    html += f"""
                    {len(draft_not_sent)}
                </strong>
            </td>

            <td>
    """

    if draft_not_sent:

        for profile in draft_not_sent:

            html += f"""
            <div style="margin-bottom: 6px;">
                {profile['primary_name']}
                ({profile['primary_email']})
            </div>
            """

    else:

        html += """
        <span style="color: gray;">
            None
        </span>
        """

    html += """
            </td>
        </tr>

        <tr style="background-color: #e7f1ff;">

            <td>
                <strong>
                    Invited — No Request
                </strong>
            </td>

            <td align="center">
                <strong>
    """

    html += f"""
                    {len(invited_no_request)}
                </strong>
            </td>

            <td>
    """

    if invited_no_request:

        for profile in invited_no_request:

            html += f"""
            <div style="margin-bottom: 6px;">
                {profile['primary_name']}
                ({profile['primary_email']})
            </div>
            """

    else:

        html += """
        <span style="color: gray;">
            None
        </span>
        """

    html += """
            </td>
        </tr>

        <tr style="background-color: #e8f5e9;">

            <td>
                <strong>
                    Invited — Replied
                </strong>
            </td>

            <td align="center">
                <strong>
    """

    html += f"""
                    {len(invited_replied)}
                </strong>
            </td>

            <td>
    """

    if invited_replied:

        for profile in invited_replied:

            html += f"""
            <div style="margin-bottom: 6px;">
                {profile['primary_name']}
                ({profile['primary_email']})
            </div>
            """

    else:

        html += """
        <span style="color: gray;">
            None
        </span>
        """

    html += """
            </td>
        </tr>

    </table>

    <p>
        <small>
            Invitation status tracks the guest invitation only.
            Request approval is managed on Request Review.
        </small>
    </p>

    <h2>Create Invitation</h2>

    <form method="POST" action="/invitations">

        <label>Guest Profile:</label><br>

        <select name="guest_profile_id">
    """

    for profile in profiles:

        html += f"""
        <option value="{profile['id']}">
            {profile['primary_name']}
            ({profile['primary_email']})
        </option>
        """

    html += """
        </select><br>

        <label>Invitation Title:</label><br>

        <input type="text"
               name="invitation_title"
               placeholder="Summer Shore Visit"><br>

        <label>Additional Message on Invite Email:</label><br>

        <textarea name="message"
                  rows="2"
                  cols="50"></textarea><br>

        <button type="submit" name="next_action" value="draft">
            Save Draft Invitation
        </button>

        &nbsp;

        <button type="submit" name="next_action" value="preview_send">
            Save Draft and Preview / Send Invite
        </button>

    </form>

    <h2>Existing Invitations</h2>
    """

    if not invitations:

        html += "<p>No invitations yet.</p>"

    else:

        html += """
        <table border="1"
               cellpadding="4"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   table-layout: auto;
                   font-size: 13px;
               ">

            <tr style="background-color: #f2f2f2;">
                <th style="min-width: 160px;">Guest</th>
                <th style="min-width: 240px;">Invitation</th>
                <th style="min-width: 260px;">Invite Status</th>
                <th style="min-width: 160px;">Dates</th>
                <th style="min-width: 260px;">Related Requests</th>
            </tr>
        """

        previous_guest = None

        for invite in invitations:

            current_guest = invite["primary_email"]

            if current_guest != previous_guest:

                html += f"""
                <tr>
                    <td colspan="5"
                        style="
                            background-color: #eee;
                            font-weight: bold;
                            padding: 4px;
                        ">
                        {invite['primary_name']}
                        ({invite['primary_email']})
                    </td>
                </tr>
                """

                previous_guest = current_guest

            status = invite["status"]

            created_display = ""

            if invite["created_at"]:

                try:

                    created_display = datetime.strptime(
                        invite["created_at"][:10],
                        "%Y-%m-%d"
                    ).strftime("%m/%d/%Y")

                except:

                    created_display = invite["created_at"]

            if status == "draft":

                status_display = """
                <strong style='color: #856404; font-size: 14px;'>
                    DRAFT / NOT SENT
                </strong>
                """

                status_actions = f"""
                <div style="margin-top: 10px;">

                    <a href='/invitation/{invite["id"]}/edit'>
                        Edit Invitation
                    </a>

                    <br>

                    <a href='/preview-invitation-email/{invite["id"]}'>
                        Preview / Send Invite
                    </a>

                    <br>

                    <a href="/invite/{invite['id']}">
                        Open Standard Request Form
                    </a>

                    <br>

                    <a href="/coordinate/{invite['id']}">
                        Open Coordination Request Form
                    </a>
                    <br>

                    <a href='/invitation/{invite["id"]}/status/closed'>
                        Close Invitation
                    </a>

                </div>
                """

            elif status == "sent":

                status_display = """
                <strong style='color: #0d6efd; font-size: 14px;'>
                    SENT
                </strong>
                """

                status_actions = f"""
                <div style="margin-top: 10px;">

                                       <a href='/invitation/{invite["id"]}/edit'>
                        Edit Invitation
                    </a>

                    <br>

                    <a href='/preview-invitation-email/{invite["id"]}'>
                        Preview / Send Invite
                    </a>

                    <br>

                    <a href="/invite/{invite['id']}">
                        Open Standard Request Form
                    </a>

                    <br>

                    <a href="/coordinate/{invite['id']}">
                        Open Coordination Request Form
                    </a>

                    <br>

                    <a href='/invitation/{invite["id"]}/status/closed'>
                        Close Invitation
                    </a>

                </div>
                """
                status_actions = f"""
                <div style="margin-top: 10px;">

                    <a href='/preview-invitation-email/{invite["id"]}'>
                        Resend Invite
                    </a>

                    <br>

                    <a href="/invite/{invite['id']}">
                        Open Standard Request Form
                    </a>

                    <br>

                    <a href="/coordinate/{invite['id']}">
                        Open Coordination Request Form
                    </a>

                    <br>

                    <a href='/invitation/{invite["id"]}/status/closed'>
                        Close Invitation
                    </a>

                </div>
                """

            elif status == "closed":

                status_display = """
                <strong style='color: black; font-size: 14px;'>
                    CLOSED
                </strong>
                """

                status_actions = f"""
                <div style="margin-top: 10px;">

                    <a href='/preview-invitation-email/{invite["id"]}'>
                        Resend Invite
                    </a>

                    <br>

                    <a href="/invite/{invite['id']}">
                        Open Standard Request Form
                    </a>

                    <br>

                    <a href="/coordinate/{invite['id']}">
                        Open Coordination Request Form
                    </a>

                </div>
                """

            else:

                status_display = f"""
                <strong style='font-size: 14px;'>
                    {status.upper()}
                </strong>
                """

                status_actions = f"""
                <div style="margin-top: 10px;">

                    <a href='/invitation/{invite["id"]}/edit'>
                        Edit Invitation
                    </a>

                    <br>

                    <a href='/preview-invitation-email/{invite["id"]}'>
                        Preview / Send Invite
                    </a>

                    <br>

                    <a href="/invite/{invite['id']}">
                        Open Standard Request Form
                    </a>

                    <br>

                    <a href="/coordinate/{invite['id']}">
                        Open Coordination Request Form
                    </a>

                </div>
                """

            related_requests_html = ""

            for req in invitation_requests:

                if req["invitation_id"] == invite["id"]:

                    request_status = req["status"]

                    if request_status == "pending":

                        request_status_display = """
                        <strong style='color: orange;'>
                            Pending Review
                        </strong>
                        """

                    elif request_status == "approved":

                        request_status_display = """
                        <strong style='color: green;'>
                            Approved
                        </strong>
                        """

                    elif request_status == "declined":

                        request_status_display = """
                        <strong style='color: red;'>
                            Declined
                        </strong>
                        """

                    else:

                        request_status_display = request_status

                    related_requests_html += f"""
                    <div style="
                            margin-bottom: 8px;
                            padding: 4px;
                            border-bottom: 1px solid #ddd;
                        ">

                        <a href="/request/{req['id']}">
                            Request #{req['id']}
                        </a>

                        — {req['name']}

                        <br>

                        {short_date(req['arrival_date'])}
                        to
                        {short_date(req['departure_date'])}

                        — {request_status_display}

                    </div>
                    """

            if not related_requests_html:

                related_requests_html = """
                <span style="color: gray;">
                    No requests yet.
                </span>
                """

            html += f"""
            <tr>

                <td style="vertical-align: top;">
                    {invite['primary_name']}<br>
                    <small>{invite['primary_email']}</small>
                </td>

                <td style="
                        vertical-align: top;
                        white-space: normal;
                    ">

                    <strong>
                        {invite['invitation_title']}
                    </strong>

                    <br>

                    <small>
                        {invite['message']}
                    </small>

                </td>

                <td style="vertical-align: top;">

                    {status_display}

                    <br>

                    <small style="color: #666;">
                        Created:
                        {created_display}
                    </small>

                    {status_actions}

                </td>

                <td style="vertical-align: top;">

                    <small>

                        Requests:
                        <strong>
                            {invite['request_count']}
                        </strong>

                    </small>

                </td>

                <td style="vertical-align: top;">

                    {related_requests_html}

                </td>

            </tr>
            """

        html += "</table>"

    return html
@app.route("/invitation/<int:invitation_id>/edit", methods=["GET", "POST"])
def edit_invitation(invitation_id):

    conn = get_db_connection()

    invite = conn.execute("""
        SELECT
            invitations.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM invitations
        JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id
        WHERE invitations.id = ?
    """, (
        invitation_id,
    )).fetchone()

    if not invite:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Invitation Not Found</h1>

        <p>The invitation could not be found.</p>

        <p><a href="/invitations">Back to Invitations</a></p>
        """

    if request.method == "POST":

        invitation_title = clean_text(request.form.get("invitation_title"))
        message = safe_text(request.form.get("message")).strip()
        status = clean_text(request.form.get("status"))

        if status not in ["draft", "sent", "responded", "replied", "closed"]:
            status = safe_text(invite["status"]) or "draft"

        conn.execute("""
            UPDATE invitations
            SET invitation_title = ?,
                message = ?,
                status = ?
            WHERE id = ?
        """, (
            invitation_title,
            message,
            status,
            invitation_id
        ))

        conn.commit()
        conn.close()

        return redirect("/invitations")

    html = nav_links() + f"""
    <h1>Edit Invitation</h1>

    <p>
        <strong>Guest:</strong> {safe_text(invite['primary_name'])}<br>
        <strong>Email:</strong> {safe_text(invite['primary_email'])}
    </p>

    <form method="POST" action="/invitation/{invitation_id}/edit">

        <label><strong>Invitation Title</strong></label><br>
        <input type="text"
               name="invitation_title"
               value="{safe_text(invite['invitation_title'])}"
               style="width: 520px; max-width: 100%;">

        <br>

        <label><strong>Message</strong></label><br>
        <textarea name="message"
                  rows="6"
                  cols="70">{safe_text(invite['message'])}</textarea>

        <br>

        <label><strong>Status</strong></label><br>
        <select name="status">
            <option value="draft" {'selected' if safe_text(invite['status']) == 'draft' else ''}>Draft</option>
            <option value="sent" {'selected' if safe_text(invite['status']) == 'sent' else ''}>Sent</option>
            <option value="responded" {'selected' if safe_text(invite['status']) == 'responded' else ''}>Responded</option>
            <option value="closed" {'selected' if safe_text(invite['status']) == 'closed' else ''}>Closed</option>
        </select>

        <br>

        <button type="submit">Save Invitation</button>

        &nbsp;

        <a href="/invitations">Cancel</a>

    </form>
    """

    conn.close()

    return html


@app.route("/invitation/<int:invitation_id>/status/<new_status>", methods=["GET", "POST"])
def update_invitation_status(invitation_id, new_status):

    if request.method != "POST":
        return action_confirmation_page(
            "Change Invitation Status",
            f"Change invitation {invitation_id} status to {safe_text(new_status)}.",
            f"/invitation/{invitation_id}/status/{safe_text(new_status)}",
            "/invitations"
        )

    allowed_statuses = [
        "draft",
        "sent",
        "replied",
        "no_reply",
        "accepted",
        "declined",
        "closed"
    ]

    if new_status not in allowed_statuses:

        return """
        <h2>Invalid status.</h2>
        <p>
            <a href="/invitations">
                Back to invitations
            </a>
        </p>
        """

    create_database_backup(
        "before_invitation_status_change"
    )

    conn = get_db_connection()

    conn.execute(
        "UPDATE invitations SET status = ? WHERE id = ?",
        (new_status, invitation_id)
    )

    conn.commit()
    conn.close()

    return redirect("/invitations")

    if request.method == "POST":
        invitation_title = request.form.get("invitation_title")
        arrival_date = request.form.get("arrival_date")
        departure_date = request.form.get("departure_date")
        message = request.form.get("message")

        conn.execute("""
            UPDATE invitations
            SET invitation_title = ?,
                arrival_date = ?,
                departure_date = ?,
                message = ?
            WHERE id = ?
        """, (
            invitation_title,
            arrival_date,
            departure_date,
            message,
            invitation_id
        ))

        conn.commit()
        conn.close()

        return redirect("/invitations")

    html = nav_links() + f"""
    <h1>Edit Invitation</h1>

    <form method="POST" action="/invitation/{invitation_id}/edit">
        <label>Invitation Title:</label><br>
        <input type="text" name="invitation_title" value="{invite['invitation_title']}"><br>

HERE

        <label>Arrival Date:</label><br>
        <input type="date" name="arrival_date" 
value=""><br>

        <label>Departure Date:</label><br>
        <input type="date" name="departure_date" 
value=""><br>

        <label>Message:</label><br>
        <textarea name="message" rows="5" cols="50">{invite['message']}</textarea><br>

        <button type="submit">Save Changes</button>
    </form>

    <p><a href="/invitations">Cancel</a></p>
    """

    conn.close()

    return html

@app.route("/profile/<int:profile_id>/edit", methods=["GET", "POST"])
def edit_profile(profile_id):
    conn = get_db_connection()
    ensure_guest_profile_welcome_column(conn)

    profile = conn.execute(
        "SELECT * FROM guest_profiles WHERE id = ?",
        (profile_id,)
    ).fetchone()

    if not profile:
        conn.close()
        return """
        <h2>Profile not found.</h2>
        <p><a href="/profiles">Back to profiles</a></p>
        """

    if request.method == "POST":
        primary_name = clean_text(request.form.get("primary_name"))
        primary_email = clean_text(request.form.get("primary_email")).lower()
        phone = request.form.get("phone")
        additional_names = request.form.get("additional_names")
        pet_notes = request.form.get("pet_notes")
        food_notes = request.form.get("food_notes")
        host_notes = request.form.get("host_notes")
        photo_path = request.form.get("photo_path")
        status = request.form.get("status")

        validation_error = guest_profile_validation_error(
            primary_name,
            primary_email
        )

        if validation_error:

            conn.close()

            return profile_error_page(
                validation_error,
                f"/profile/{profile_id}/edit"
            )

        try:

            conn.execute("""
            UPDATE guest_profiles
            SET primary_name = ?,
                primary_email = ?,
                phone = ?,
                additional_names = ?,
                pet_notes = ?,
                food_notes = ?,
                host_notes = ?,
                photo_path = ?,
                status = ?
            WHERE id = ?
        """, (
            primary_name,
            primary_email,
            phone,
            additional_names,
            pet_notes,
            food_notes,
            host_notes,
            photo_path,
            status,
            profile_id
        ))

            conn.commit()
            conn.close()

            return redirect("/profiles")

        except Exception as e:

            conn.close()

            error_text = str(e)

            if "UNIQUE constraint failed" in error_text:

                return profile_error_page(
                    "A guest profile with this email address already exists.",
                    f"/profile/{profile_id}/edit"
                )

            return profile_error_page(
                f"Could not save profile: {e}",
                f"/profile/{profile_id}/edit"
            )

    html = nav_links() + f"""
    <h1>Edit Guest Profile</h1>

    <form method="POST" action="/profile/{profile_id}/edit">
        <label>Primary First Name:</label><br>
        <input type="text" name="primary_name" value="{profile['primary_name']}" required><br>

        <label>Primary Email:</label><br>
        <input type="email" name="primary_email" value="{profile['primary_email']}" required><br>

        <label>Phone:</label><br>
        <input type="text" name="phone" value="{profile['phone']}"><br>

        <label>Additional Names:</label><br>
        <textarea name="additional_names">{profile['additional_names']}</textarea><br>

        <label>Pet Notes:</label><br>
        <textarea name="pet_notes">{profile['pet_notes']}</textarea><br>

        <label>Food Notes:</label><br>
        <textarea name="food_notes">{profile['food_notes']}</textarea><br>

        <label>Host Notes:</label><br>
        <textarea name="host_notes">{profile['host_notes']}</textarea><br>

        <label>Photo Path / Filename:</label><br>
        <input type="text" name="photo_path" value="{profile['photo_path']}"><br>

        <label>Status:</label><br>
        <select name="status">
            <option value="active">Active</option>
            <option value="needs_review">Needs Review</option>
            <option value="archived">Archived</option>
        </select><br>

        <button type="submit">Save Profile</button>
    </form>

    <p><a href="/profile/{profile_id}">Cancel</a></p>
    """

    conn.close()

    return html


@app.route("/profile/<int:profile_id>")
def profile_detail(profile_id):

    conn = get_db_connection()
    ensure_guest_profile_welcome_column(conn)

    profile = conn.execute("""
        SELECT *
        FROM guest_profiles
        WHERE id = ?
    """, (profile_id,)).fetchone()

    if not profile:
        conn.close()

        return """
        <h2>Profile not found.</h2>
        <p><a href="/profiles">Back to Profiles</a></p>
        """

    photo_path = safe_text(profile["photo_path"])

    if photo_path:

        photo_html = f"""
        <img src="/static/profile_photos/{photo_path}"
             style="
                 width: 140px;
                 height: 140px;
                 object-fit: cover;
                 border-radius: 10px;
                 border: 1px solid #ccc;
             ">
        """

    else:

        photo_html = """
        <div style="
            width: 140px;
            height: 140px;
            border: 1px solid #ccc;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #777;
        ">
            No Photo
        </div>
        """

    conn.close()

    html = nav_links() + f"""
    <h1>Guest Profile</h1>

    <div style="
        display: flex;
        gap: 24px;
        align-items: flex-start;
        margin-bottom: 24px;
    ">

        <div>
            {photo_html}
        </div>

        <div>

            <h2 style="margin-top: 0;">
                {profile['primary_name']}
            </h2>

            <p>
                <strong>Email:</strong>
                {safe_text(profile['primary_email'])}
            </p>

            <p>
                <strong>Phone:</strong>
                {safe_text(profile['phone'])}
            </p>

            <p>
                <strong>Status:</strong>
                {safe_text(profile['status'])}
            </p>

            <p>
                <strong>Welcome Email:</strong>
                {safe_text(profile_welcome_status_text(profile))}
            </p>

        </div>

    </div>

    <h3>Additional Guests</h3>

    <p style="white-space: pre-line;">
        {safe_text(profile['additional_names'])}
    </p>

    <h3>Pet Notes</h3>

    <p style="white-space: pre-line;">
        {safe_text(profile['pet_notes'])}
    </p>

    <h3>Food Notes</h3>

    <p style="white-space: pre-line;">
        {safe_text(profile['food_notes'])}
    </p>

    <h3>Host Notes</h3>

    <p style="white-space: pre-line;">
        {safe_text(profile['host_notes'])}
    </p>

    <br>

    <p>
        <a href="/profile/{profile_id}/edit">
            Edit Profile
        </a>
        |
        <a href="/profiles">
            Back to Profiles
        </a>
    </p>
    """

    return html

@app.route("/decline/<int:request_id>", methods=["GET", "POST"])
def decline_request(request_id):
    conn = get_db_connection()

    request_row = conn.execute(
        "SELECT * FROM booking_requests WHERE id = ?",
        (request_id,)
    ).fetchone()

    if not request_row:
        conn.close()
        return """
        <h2>Request not found.</h2>
        <p><a href="/requests">Back to Request Review</a></p>
        """

    if request.method == "POST":
        decline_reason = request.form.get("decline_reason")

        if not decline_reason:
            decline_reason = "These dates are not available."

        backup_path = create_database_backup(
            "before_decline_request"
        )

        try:

            conn.execute("BEGIN")

            conn.execute("""
                UPDATE booking_requests
                SET status = ?,
                    response_message = ?,
                    email_status = ?,
                    email_needed_type = ?
                WHERE id = ?
            """, (
                "declined",
                decline_reason,
                "needs_email",
                "decline",
                request_id
            ))

            write_activity_log(
                conn,
                request_id,
                "request_declined",
                request_row["status"],
                "declined",
                f"Decline email needed. Backup: {backup_path}"
            )

            conn.commit()

        except Exception as error:

            rollback_and_close(conn)

            return transaction_error_page(
                error,
                f"/request/{request_id}"
            )

        conn.close()

        rooms_requested = request_row["rooms_requested"]

        if not rooms_requested:
            rooms_requested = 1

        rooms_requested = int(rooms_requested)

        nights = (
            datetime.strptime(request_row["departure_date"], "%Y-%m-%d")
            - datetime.strptime(request_row["arrival_date"], "%Y-%m-%d")
        ).days

        additional_names = request_row["additional_names"]

        if not additional_names:
            additional_names = "None listed"

        if request_row["invitation_id"]:
            request_link = f"{BASE_URL}/invite/{request_row['invitation_id']}"
        else:
            request_link = standard_new_request_url()

        decline_email_subject = "Your Strathmere Visit Request"

        decline_email_body = render_email_template(
            "decline.txt",
            guest_name=request_row["name"],
            arrival_date=format_date(request_row["arrival_date"]),
            departure_date=format_date(request_row["departure_date"]),
            nights=nights,
            rooms_requested=rooms_requested,
            additional_names=additional_names,
            decline_reason=decline_reason,
            request_link=request_link
        )

        decline_email_body += request_change_links(
            request_id,
            repeat_visit_request_url_for_row(request_row)
        )

        html = nav_links() + f"""
        <h1>Request Declined</h1>

        <p>The request has been declined.</p>
        <p><strong>Email Status:</strong> Needs decline email</p>

        <h2>Email Preview</h2>

        <p><strong>To:</strong> {request_row['email']}</p>
        <p><strong>Subject:</strong> {decline_email_subject}</p>

        <form method="POST" action="/send-preview-email">
            <input type="hidden" name="request_id" value="{request_id}">
            <input type="hidden" name="email_type" value="decline">

            <input type="hidden" name="to_email" value="{request_row['email']}">
            <input type="hidden" name="subject" value="{decline_email_subject}">
            <input type="hidden" name="return_to" value="/request/{request_id}">

            <textarea id="decline_email_body"
                      name="body"
                      rows="22"
                      cols="90"
                      style="width: 100%; max-width: 900px;">{decline_email_body}</textarea>

            <br>

            <button type="button" onclick="copyDeclineEmail();">
                Copy Email Body
            </button>

            <button type="submit">
                Send Email
            </button>
        </form>

        <p id="copy_message" style="font-weight: bold; color: green;"></p>

        <script>
            function copyDeclineEmail() {{
                const emailBody = document.getElementById("decline_email_body");

                emailBody.select();
                emailBody.setSelectionRange(0, 99999);

                navigator.clipboard.writeText(emailBody.value);

                document.getElementById("copy_message").innerText =
                    "Email copied.";
            }}
        </script>

        <br>

        <p>
            <a href="/requests">Back to Request Review</a> |
            <strong style="color: #198754;">Done</strong>
        </p>
        """

        return html

    conn.close()

    html = nav_links() + f"""
    <h2>Decline Request</h2>

    <p>Are you sure you want to decline this request?</p>

    <form method="POST" action="/decline/{request_id}">
        <label>Reason / Message to Guest:</label><br>
        <textarea name="decline_reason"
                  rows="5"
                  cols="70"
                  placeholder="Example: Sorry, those dates will not work. Please use the link to request alternate dates."></textarea><br>

        <button type="submit">Yes, Decline</button>
    </form>

    <p><a href="/requests">Cancel</a></p>
    """

    return html

@app.route("/request/<int:request_id>")
def request_detail(request_id):

    conn = get_db_connection()

    req = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not req:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href="/requests">
                Back to Requests
            </a>
        </p>
        """

    email_history = conn.execute("""
        SELECT *
        FROM email_log
        WHERE request_id = ?
        ORDER BY sent_at DESC
    """, (request_id,)).fetchall()

    return_to = request.args.get("return_to") or request.form.get("return_to") or ""

    assigned_rooms = conn.execute("""
        SELECT
            rooms.name AS room_name,
            bookings.arrival_date,
            bookings.departure_date
        FROM bookings
        JOIN rooms
            ON bookings.room_id = rooms.id
        WHERE bookings.request_id = ?
        ORDER BY rooms.name
    """, (request_id,)).fetchall()

    rooms = conn.execute("""
        SELECT *
        FROM rooms
        ORDER BY id
    """).fetchall()

    existing_bookings = conn.execute("""
        SELECT *
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    conn.close()

    rooms_requested = req["rooms_requested"] or 1

    nights = (
        datetime.strptime(req["departure_date"], "%Y-%m-%d")
        - datetime.strptime(req["arrival_date"], "%Y-%m-%d")
    ).days

    status_display = request_status_display(req["status"])

    email_display = email_status_display(
        req["email_status"],
        req["email_needed_type"],
        req["id"]
    )

    email_action = ""

    if req["status"] in ["approved", "declined"]:

        email_action = f"""
        <br>

        <a href="/request/{request_id}/email-preview">
            Preview / Resend Email
        </a>
        """

    coordination_notes = safe_text(
        req["coordination_notes"]
    )

    html = nav_links() + f"""
    <h1>Request Detail</h1>

    <table border="1"
           cellpadding="6"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               max-width: 900px;
               font-size: 14px;
           ">

        <tr>
            <td style="width: 220px;">
                <strong>Name</strong>
            </td>

            <td>
                {req['name']}
            </td>
        </tr>

        <tr>
            <td><strong>Email</strong></td>

            <td>
                {req['email']}
            </td>
        </tr>

        <tr>
            <td><strong>Status</strong></td>

            <td>
                {status_display}
            </td>
        </tr>

        <tr>
            <td><strong>Email Status</strong></td>

            <td>
                {email_display}
                {email_action}
            </td>
        </tr>

        <tr>
            <td><strong>Arrival</strong></td>

            <td>
                {format_date(req['arrival_date'])}
            </td>
        </tr>

        <tr>
            <td><strong>Departure</strong></td>

            <td>
                {format_date(req['departure_date'])}
            </td>
        </tr>

        <tr>
            <td><strong>Nights</strong></td>

            <td>
                {nights}
            </td>
        </tr>

        <tr>
            <td><strong>Rooms Requested</strong></td>

            <td>
                {rooms_requested}
            </td>
        </tr>

        <tr>
            <td><strong>Children</strong></td>

            <td>
                {req['children']}
            </td>
        </tr>

        <tr>
            <td><strong>Pets</strong></td>

            <td>
                {req['pets']}
            </td>
        </tr>

        <tr>
            <td><strong>Food Restrictions</strong></td>

            <td>
                {req['food_restrictions']}
            </td>
        </tr>

        <tr>
            <td><strong>Additional Guests for Your Room(s)</strong></td>

            <td>
                {req['additional_names']}
            </td>
        </tr>
    """

    if coordination_notes:

        html += f"""
        <tr>
            <td>
                <strong>Coordinating With</strong>
            </td>

            <td>
                <div style="
                    font-weight: bold;
                    color: #003366;
                ">
                    {coordination_notes}
                </div>
            </td>
        </tr>
        """

    html += f"""
        <tr>
            <td><strong>Comments</strong></td>

            <td>
                {display_comments_sorted(req['comments'])}
            </td>
        </tr>

    </table>
    """

    if assigned_rooms:

        html += """
        <h2>Assigned Room(s)</h2>

        <table border="1"
               cellpadding="6"
               cellspacing="0"
               style="border-collapse: collapse;">

            <tr>
                <th>Room</th>
                <th>Arrival</th>
                <th>Departure</th>
            </tr>
        """

        for room in assigned_rooms:

            html += f"""
            <tr>
                <td>{room['room_name']}</td>

                <td>
                    {format_date(room['arrival_date'])}
                </td>

                <td>
                    {format_date(room['departure_date'])}
                </td>
            </tr>
            """

        html += "</table>"


    if req["status"] == "change_requested":

        change_values_for_rooms = latest_change_values(
            req["comments"]
        )

        requested_arrival_for_room_check = clean_text(
            change_values_for_rooms["new_arrival"]
        ) or req["arrival_date"]

        requested_departure_for_room_check = clean_text(
            change_values_for_rooms["new_departure"]
        ) or req["departure_date"]

        booked_room_ids = set()

        for booking in existing_bookings:

            if booking["request_id"] == req["id"]:

                continue

            if not (
                booking["departure_date"] <= requested_arrival_for_room_check
                or booking["arrival_date"] >= requested_departure_for_room_check
            ):

                booked_room_ids.add(
                    booking["room_id"]
                )

        room_selects_html = ""

        rooms_requested_for_change = clean_text(
            change_values_for_rooms["new_rooms"]
        )

        if not rooms_requested_for_change:
            rooms_requested_for_change = req["rooms_requested"] or 1

        try:
            rooms_requested_for_change = int(
                rooms_requested_for_change
            )
        except Exception:
            rooms_requested_for_change = 1

        rooms_requested_for_change = max(
            1,
            rooms_requested_for_change
        )

        rooms_requested_for_change = min(
            4,
            rooms_requested_for_change
        )

        for i in range(1, rooms_requested_for_change + 1):

            room_options = ""

            for room in rooms:

                if room["id"] in booked_room_ids:

                    room_options += f"""
                    <option value="{room['id']}" disabled>
                        {room['name']} - BOOKED
                    </option>
                    """

                else:

                    room_options += f"""
                    <option value="{room['id']}">
                        {room['name']} - Available
                    </option>
                    """

            room_selects_html += f"""
            <label>
                <strong>Room {i}:</strong>
            </label><br>

            <select name="room_id_{i}"
                    style="width: 180px;">

                {room_options}

            </select><br>
            """

        change_summary_html = ""

        change_values = latest_change_values(
            req["comments"]
        )

        if change_values["section"]:

            change_requested_date = display_change_requested_date(
                change_values["timestamp"]
            )

            change_summary_html = f"""
            <div style="
                margin-top: 10px;
                padding: 10px;
                background-color: #ffffff;
                border-left: 4px solid #fd7e14;
            ">
                <strong>Requested Change Details</strong><br>
                <small>Requested: {change_requested_date}</small>

                <table border="1"
                       cellpadding="4"
                       cellspacing="0"
                       style="
                           border-collapse: collapse;
                           margin-top: 8px;
                           font-size: 13px;
                       ">
                    <tr style="background-color: #f5f5f5;">
                        <th align="left">Field</th>
                        <th align="left">Current</th>
                        <th align="left">Requested</th>
                    </tr>

                    <tr>
                        <td>Arrival</td>
                        <td>{format_date(change_values["original_arrival"])}</td>
                        <td><strong>{format_date(change_values["new_arrival"])}</strong></td>
                    </tr>

                    <tr>
                        <td>Departure</td>
                        <td>{format_date(change_values["original_departure"])}</td>
                        <td><strong>{format_date(change_values["new_departure"])}</strong></td>
                    </tr>

                    <tr>
                        <td>Rooms</td>
                        <td>{change_values["original_rooms"]}</td>
                        <td><strong>{change_values["new_rooms"]}</strong></td>
                    </tr>
                </table>

                <p style="margin-bottom: 0;">
                    <strong>Notes:</strong>
                    {change_values["notes"]}
                </p>
            </div>
            """

        html += f"""

        <div style="
            margin-top: 24px;
            padding: 16px;
            background-color: #fff3cd;
            border-radius: 8px;
        ">

            <h2 style="
                margin-top: 0;
                color: #fd7e14;
            ">
                Change Request Review
            </h2>

            <p>
                This guest requested changes
                to their approved stay.
            </p>

            <p>
                Approving this change will replace
                the existing room booking records
                with the requested dates and rooms.
            </p>

            {change_summary_html}

            <form method="POST"
                  action="/approve-change/{req['id']}">

                {room_selects_html}

                <label>
                    <strong>Update Note:</strong>
                </label><br>

                <textarea name="response_message"
                          rows="3"
                          style="
                              width: 520px;
                              max-width: 100%;
                          "></textarea><br>

                <button type="submit"
                        style="
                            background-color: #198754;
                            color: white;
                            padding: 10px 16px;
                            border: none;
                            border-radius: 6px;
                            font-weight: bold;
                            cursor: pointer;
                        ">
                    Approve Change
                </button>

            </form>

        </div>
        """

    if req["status"] == "cancel_requested":

        html += f"""

        <div style="
            margin-top: 24px;
            padding: 16px;
            background-color: #ffe5e5;
            border-radius: 8px;
        ">

            <h2 style="
                margin-top: 0;
                color: #dc3545;
            ">
                Cancellation Request
            </h2>

            <p>
                This guest requested cancellation
                of their approved stay.
            </p>

            <a href="/approve-cancel/{req['id']}"
               style="
                   background-color: #dc3545;
                   color: white;
                   padding: 10px 16px;
                   text-decoration: none;
                   border-radius: 6px;
                   font-weight: bold;
                   display: inline-block;
               ">
                Approve Cancellation
            </a>

        </div>
        """

    html += """
    <h2>Email History</h2>
    """

    if not email_history:

        html += "<p>No emails sent yet.</p>"

    else:

        html += """
        <table border="1"
               cellpadding="4"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   font-size: 13px;
               ">

            <tr>
                <th>Sent</th>
                <th>Type</th>
                <th>Recipient</th>
                <th>Subject</th>
            </tr>
        """

        for email in email_history:

            html += f"""
            <tr>
                <td>{email['sent_at']}</td>

                <td>{email['email_type']}</td>

                <td>{email['recipient']}</td>

                <td>{email['subject']}</td>
            </tr>
            """

        html += "</table>"

    html += f"""
    <br>

    <a href="/request/{request_id}/edit">
        Edit Request
    </a>

    <br>

    <a href="/requests">
        Back to Requests
    </a>
    """

    return html

@app.route("/approve-change/<int:request_id>", methods=["POST"])
def approve_change(request_id):

    response_message = request.form.get("response_message")

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not request_row:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href='/requests'>
                Back to requests
            </a>
        </p>
        """

    if request_row["status"] != "change_requested":

        conn.close()

        return f"""
        {nav_links()}

        <h1>
            Change Request Not Approved
        </h1>

        <p>
            This request is not currently
            marked as a change request.
        </p>

        <p>
            <strong style="color: #198754;">Done</strong>
        </p>
        """

    change_values = latest_change_values(
        request_row["comments"]
    )

    effective_arrival_date = clean_text(
        change_values["new_arrival"]
    )

    effective_departure_date = clean_text(
        change_values["new_departure"]
    )

    effective_rooms_requested = clean_text(
        change_values["new_rooms"]
    )

    if not effective_arrival_date:
        effective_arrival_date = request_row["arrival_date"]

    if not effective_departure_date:
        effective_departure_date = request_row["departure_date"]

    if not effective_rooms_requested:
        effective_rooms_requested = request_row["rooms_requested"]

    rooms_requested = effective_rooms_requested

    if not rooms_requested:
        rooms_requested = 1

    rooms_requested = int(rooms_requested)

    if rooms_requested < 1:
        rooms_requested = 1

    if rooms_requested > 4:
        rooms_requested = 4

    try:

        datetime.strptime(
            effective_arrival_date,
            "%Y-%m-%d"
        )

        datetime.strptime(
            effective_departure_date,
            "%Y-%m-%d"
        )

    except:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Change Request Not Approved</h1>

        <p>
            The requested change dates could not be read.
        </p>

        <p>
            <strong style="color: #198754;">Done</strong>
        </p>
        """

    selected_room_ids = []

    for i in range(1, rooms_requested + 1):

        room_id = request.form.get(f"room_id_{i}")

        if room_id:
            selected_room_ids.append(room_id)

    if len(selected_room_ids) != rooms_requested:

        conn.close()

        return f"""
        <h2>Not enough rooms selected.</h2>

        <p>
            This change request needs {rooms_requested} room(s).
        </p>

        <p>
            Please select {rooms_requested} room(s)
            before approving the change.
        </p>

        <p>
            <a href='/request/{request_id}'>Done</a>
        </p>
        """

    if len(selected_room_ids) != len(set(selected_room_ids)):

        conn.close()

        return f"""
        <h2>Duplicate room selected.</h2>

        <p>
            Please choose a different room
            for each room assignment.
        </p>

        <p>
            <a href='/request/{request_id}'>Done</a>
        </p>
        """

    ensure_house_block_columns(conn)

    total_rooms_row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()
    total_rooms = int(total_rooms_row["count"] or 4)

    blocked_for_capacity = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()
    blocked_dates_for_capacity, room_limit_by_date = build_blocked_date_capacity(
        blocked_for_capacity,
        total_rooms
    )

    tentative_holds = get_coordination_tentative_holds(conn)

    try:
        check_start = datetime.strptime(effective_arrival_date, "%Y-%m-%d").date()
        check_end = datetime.strptime(effective_departure_date, "%Y-%m-%d").date()
    except Exception:
        check_start = None
        check_end = None

    if check_start and check_end:

        current_check_date = check_start

        while current_check_date < check_end:

            current_date_string = current_check_date.strftime("%Y-%m-%d")

            approved_rooms_used_row = conn.execute("""
                SELECT COUNT(*) AS count
                FROM bookings
                WHERE status = 'approved'
                  AND request_id != ?
                  AND arrival_date <= ?
                  AND departure_date > ?
            """, (
                request_id,
                current_date_string,
                current_date_string
            )).fetchone()

            approved_rooms_used = int(approved_rooms_used_row["count"] or 0)
            tentative_rooms_held = 0

            for tentative_hold in tentative_holds:
                try:
                    hold_start = datetime.strptime(tentative_hold["arrival_date"], "%Y-%m-%d").date()
                    hold_end = datetime.strptime(tentative_hold["departure_date"], "%Y-%m-%d").date()
                    hold_rooms = int(tentative_hold.get("rooms_held", 1) or 1)
                except Exception:
                    continue

                if hold_start <= current_check_date < hold_end:
                    tentative_rooms_held += hold_rooms

            capacity_limit = room_capacity_limit_for_date(
                room_limit_by_date,
                current_date_string,
                total_rooms
            )

            rooms_open_after_holds = capacity_limit - approved_rooms_used - tentative_rooms_held

            if rooms_open_after_holds < rooms_requested:

                conn.close()

                return f"""
                <h2>Not enough room capacity for those changed dates.</h2>

                <p>
                    {format_date(current_date_string)} has only
                    {rooms_open_after_holds} room(s) open after house blocks,
                    approved bookings, and tentative coordination holds are counted.
                </p>

                <p>
                    <a href='/request/{request_id}'>Done</a>
                </p>
                """

            current_check_date += timedelta(days=1)

    blocked_conflict = conn.execute("""
        SELECT *
        FROM blocked_dates
        WHERE start_date < ?
          AND end_date > ?
          AND COALESCE(is_full_block, 1) = 1
    """, (
        effective_departure_date,
        effective_arrival_date
    )).fetchone()

    if blocked_conflict:

        conn.close()

        return f"""
        <h2>These dates are blocked.</h2>

        <p>
            The requested changed stay overlaps
            a blocked date range.
        </p>

        <p>
            <a href='/request/{request_id}'>Done</a>
        </p>
        """

    selected_room_names = []

    for room_id in selected_room_ids:

        conflict = conn.execute("""
            SELECT *
            FROM bookings
            WHERE room_id = ?
              AND request_id != ?
              AND status = 'approved'
              AND arrival_date < ?
              AND departure_date > ?
        """, (
            room_id,
            request_id,
            effective_departure_date,
            effective_arrival_date
        )).fetchone()

        if conflict:

            conn.close()

            return f"""
            <h2>Room is not available for those dates.</h2>

            <p>
                One of the selected rooms already
                has an overlapping booking.
            </p>

            <p>
                Room ID: {room_id}
            </p>

            <p>
                <a href='/request/{request_id}'>Done</a>
            </p>
            """

        room = conn.execute("""
            SELECT name
            FROM rooms
            WHERE id = ?
        """, (room_id,)).fetchone()

        if room:
            selected_room_names.append(room["name"])

    backup_path = create_database_backup(
        "before_approve_change"
    )

    try:

        conn.execute("BEGIN")

        conn.execute("""
            UPDATE booking_requests
            SET status = ?,
                arrival_date = ?,
                departure_date = ?,
                rooms_requested = ?,
                response_message = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "approved",
            effective_arrival_date,
            effective_departure_date,
            rooms_requested,
            response_message,
            "needs_update",
            "date_change",
            request_id
        ))

        conn.execute("""
            DELETE FROM bookings
            WHERE request_id = ?
        """, (request_id,))

        for room_id in selected_room_ids:

            conn.execute("""
                INSERT INTO bookings
                (request_id, room_id, arrival_date, departure_date, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                request_id,
                room_id,
                effective_arrival_date,
                effective_departure_date,
                "approved"
            ))

        write_activity_log(
            conn,
            request_id,
            "change_approved",
            request_row["status"],
            "approved",
            f"Rooms assigned: {', '.join(selected_room_names)}. Backup: {backup_path}"
        )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/request/{request_id}"
        )

    conn.close()

    room_list = ", ".join(selected_room_names)

    html = nav_links() + f"""
    <h1>
        Change Request Approved
    </h1>

    <p>
        The requested change has been approved
        and the room assignment has been updated.
    </p>

    <p>
        <strong>Updated Stay:</strong><br>
        {format_date(effective_arrival_date)}
        to
        {format_date(effective_departure_date)}
    </p>

    <p>
        <strong>Assigned Room(s):</strong><br>
        {room_list}
    </p>

    <p>
        <strong>Email Status:</strong>
        Needs update email
    </p>

    <div style="
        margin-top: 20px;
    ">

        <a href="/request/{request_id}/email-preview"
           style="
               background-color: #198754;
               color: white;
               padding: 10px 14px;
               text-decoration: none;
               border-radius: 6px;
               font-weight: bold;
           ">
            Preview Update Email
        </a>

        <strong style="color: #198754;">Done</strong>

        <a href="/requests"
           style="
               background-color: #6c757d;
               color: white;
               padding: 10px 14px;
               text-decoration: none;
               border-radius: 6px;
               font-weight: bold;
               margin-left: 8px;
           ">
            Back to Requests
        </a>

    </div>
    """

    return html

@app.route("/request/<int:request_id>/admin-cancel-confirmed", methods=["GET", "POST"])
def admin_cancel_confirmed_visit(request_id):

    if request.method != "POST":
        return action_confirmation_page(
            "Cancel Confirmed Visit",
            f"Cancel confirmed visit for request {request_id}, release assigned rooms, and send the cancellation email.",
            f"/request/{request_id}/admin-cancel-confirmed",
            "/bookings"
        )

    conn = get_db_connection()

    req = conn.execute("""
        SELECT
            booking_requests.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM booking_requests
        LEFT JOIN guest_profiles
            ON booking_requests.guest_profile_id = guest_profiles.id
        WHERE booking_requests.id = ?
    """, (
        request_id,
    )).fetchone()

    if not req:
        conn.close()
        return profile_error_page(
            "Request not found.",
            "/bookings"
        )

    if safe_text(req["status"]) != "approved":
        conn.close()
        return transaction_error_page(
            "Only approved confirmed visits can be cancelled from Confirmed Stays.",
            "/bookings"
        )

    recipient_email = resolve_request_recipient_email(
        conn,
        req
    )

    if not is_valid_email_address(recipient_email):
        conn.close()
        return profile_error_page(
            "Cancellation email was not sent because this guest does not have a valid email address.",
            f"/request/{request_id}"
        )

    guest_name = safe_text(req["name"]).strip()

    if not guest_name:
        guest_name = safe_text(row_value(req, "primary_name")).strip()

    rooms_requested = normalize_rooms_requested(
        row_value(req, "rooms_requested") or 1
    )

    nights = date_range_nights(
        req["arrival_date"],
        req["departure_date"]
    )

    subject = "Your Strathmere Visit Cancellation"

    body = render_email_template(
        "cancellation.txt",
        guest_name=guest_name,
        arrival_date=format_date(req["arrival_date"]),
        departure_date=format_date(req["departure_date"]),
        nights=safe_text(nights),
        rooms_requested=safe_text(rooms_requested),
        additional_names=safe_text(row_value(req, "additional_names")) or "None listed",
        request_link=standard_new_request_url(),
        new_request_link=standard_new_request_url()
    )

    backup_path = create_database_backup(
        "before_admin_cancel_confirmed"
    )

    old_status = safe_text(req["status"])

    try:
        conn.execute("BEGIN")

        conn.execute("""
            UPDATE booking_requests
            SET status = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "cancelled",
            "needs_email",
            "cancellation",
            request_id
        ))

        conn.execute("""
            DELETE FROM bookings
            WHERE request_id = ?
        """, (
            request_id,
        ))

        write_activity_log(
            conn,
            request_id,
            "admin_cancel_confirmed_started",
            old_status,
            "cancelled",
            f"Confirmed visit cancelled from Confirmed Stays. Backup: {backup_path}"
        )

        conn.commit()

    except Exception as error:
        rollback_and_close(conn)
        return transaction_error_page(
            error,
            "/bookings"
        )

    try:
        send_email(
            recipient_email,
            subject,
            body
        )

        conn.execute("BEGIN")

        conn.execute("""
            INSERT INTO email_log
            (request_id, email_type, recipient, subject, body)
            VALUES (?, ?, ?, ?, ?)
        """, (
            request_id,
            "cancellation",
            recipient_email,
            subject,
            body
        ))

        conn.execute("""
            UPDATE booking_requests
            SET email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "sent",
            "",
            request_id
        ))

        write_activity_log(
            conn,
            request_id,
            "admin_cancel_confirmed_email_sent",
            "cancelled",
            "cancelled",
            "Cancellation email sent from Confirmed Stays."
        )

        conn.commit()

    except Exception as error:
        try:
            conn.rollback()
        except Exception:
            pass

        conn.close()

        return f"""
        {nav_links()}

        <h1>Visit Cancelled, Email Needs Attention</h1>

        <p style="color: red; font-weight: bold;">
            The confirmed visit was cancelled and rooms were released, but the cancellation email did not send.
        </p>

        <p>
            Error: {safe_text(error)}
        </p>

        <p>
            <a href="/request/{request_id}/email-preview">Preview / Send Cancellation Email</a> |
            <a href="/bookings">Back to Confirmed Stays</a>
        </p>
        """

    conn.close()

    return f"""
    {nav_links()}

    <h1>Confirmed Visit Cancelled</h1>

    <p style="color: green; font-weight: bold;">
        The visit was cancelled, assigned rooms were released, and the cancellation email was sent.
    </p>

    <p>
        <strong>Guest:</strong> {safe_text(guest_name)}<br>
        <strong>Email:</strong> {safe_text(recipient_email)}<br>
        <strong>Subject:</strong> {safe_text(subject)}
    </p>

    <p>
        <a href="/bookings">Back to Confirmed Stays</a> |
        <a href="/request/{request_id}">Open Request</a>
    </p>
    """


@app.route("/approve-cancel/<int:request_id>", methods=["GET", "POST"])
def approve_cancel(request_id):

    if request.method != "POST":
        return action_confirmation_page(
            "Approve Cancellation",
            f"Approve cancellation for request {request_id}, release assigned bookings, and mark cancellation email as needed.",
            f"/approve-cancel/{request_id}",
            f"/request/{request_id}"
        )

    conn = get_db_connection()

    req = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not req:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href="/requests">
                Back to Requests
            </a>
        </p>
        """

    backup_path = create_database_backup(
        "before_approve_cancel"
    )

    try:

        conn.execute("BEGIN")

        conn.execute("""
            UPDATE booking_requests
            SET status = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "cancelled",
            "needs_email",
            "cancellation",
            request_id
        ))

        conn.execute("""
            DELETE FROM bookings
            WHERE request_id = ?
        """, (
            request_id,
        ))

        write_activity_log(
            conn,
            request_id,
            "cancellation_approved",
            req["status"],
            "cancelled",
            f"Assigned bookings released. Backup: {backup_path}"
        )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/request/{request_id}"
        )

    conn.close()

    html = nav_links() + f"""
    <h1>
        Cancellation Approved
    </h1>

    <p>
        The stay has been cancelled.
    </p>

    <p>
        All assigned rooms have been released.
    </p>

    <p>
        A cancellation email is now ready.
    </p>

    <div style="
        margin-top: 20px;
    ">

        <a href="/request/{request_id}/email-preview"
           style="
               background-color: #198754;
               color: white;
               padding: 10px 14px;
               text-decoration: none;
               border-radius: 6px;
               font-weight: bold;
           ">
            Preview Cancellation Email
        </a>

        <strong style="color: #198754;">Done</strong>

        <a href="/requests"
           style="
               background-color: #6c757d;
               color: white;
               padding: 10px 14px;
               text-decoration: none;
               border-radius: 6px;
               font-weight: bold;
               margin-left: 8px;
           ">
            Back to Requests
        </a>

    </div>
    """

    return html

@app.route("/request/<int:request_id>/email-preview")
def request_email_preview(request_id):
    conn = get_db_connection()

    req = conn.execute("""
        SELECT
            booking_requests.*,
            guest_profiles.photo_path,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM booking_requests
        LEFT JOIN guest_profiles
            ON booking_requests.guest_profile_id = guest_profiles.id
        WHERE booking_requests.id = ?
    """, (request_id,)).fetchone()

    if not req:
        conn.close()
        return """
        <h2>Request not found.</h2>
        <p><a href="/requests">Back to Requests</a></p>
        """

    assigned_rooms = conn.execute("""
        SELECT
            rooms.name AS room_name
        FROM bookings
        JOIN rooms ON bookings.room_id = rooms.id
        WHERE bookings.request_id = ?
        ORDER BY rooms.name
    """, (request_id,)).fetchall()

    recipient_email = resolve_request_recipient_email(
        conn,
        req
    )

    guest_name = safe_text(req["name"]).strip()

    if not guest_name:
        guest_name = safe_text(req["primary_name"]).strip()

    if not recipient_email:
        recipient_email = safe_text(req["primary_email"]).strip()

    repeat_visit_url = repeat_visit_request_url_for_row(req)

    rooms_requested = int(req["rooms_requested"] or 1)

    nights = (
        datetime.strptime(req["departure_date"], "%Y-%m-%d")
        - datetime.strptime(req["arrival_date"], "%Y-%m-%d")
    ).days

    additional_names = combined_confirmed_group_members(
        conn,
        req
    )

    room_names = []

    for room in assigned_rooms:
        room_names.append(room["room_name"])

    if room_names:
        room_list = ", ".join(room_names)
    else:
        room_list = "No rooms assigned"

    optional_admin_message = safe_text(req["response_message"]).strip()

    if optional_admin_message:
        optional_admin_message = optional_admin_message + "\n"

    coordinating_with = safe_text(req["coordination_notes"]).strip()

    if coordinating_with:
        coordinating_with_section = f"Coordinating With: {coordinating_with}\n"
    else:
        coordinating_with_section = ""

    email_type = req["email_needed_type"]

    if not email_type:
        if req["status"] == "approved":
            email_type = "approval"
        elif req["status"] == "declined":
            email_type = "decline"
        else:
            email_type = "general"

    if email_type == "cancellation":
        subject = "Your Strathmere Visit Cancellation"

        body = f"""Hi {guest_name},

Your Strathmere visit has been cancelled.

Cancelled Visit Details:
- Arrival: {format_date(req['arrival_date'])}
- Departure: {format_date(req['departure_date'])}
- Nights: {nights}
- Rooms: {rooms_requested}

Thanks for letting us know.

John & Mark
302-521-5401
"""

        body = body.replace(
            "Additional Guests for Your Room(s):",
            "Confirmed Group Members:"
        ).replace(
            "Additional Guests:",
            "Confirmed Group Members:"
        )

        body = append_guest_visit_history_summary(
            body,
            conn,
            req,
            request_id
        )

        email_type_label = "Cancellation Email"

    elif email_type == "date_change":
        subject = "Updated Details for Your Strathmere Visit"

        body = render_email_template(
            "date_change.txt",
            guest_name=guest_name,
            arrival_date=format_date(req["arrival_date"]),
            departure_date=format_date(req["departure_date"]),
            nights=nights,
            rooms_requested=rooms_requested,
            additional_names=additional_names,
            room_list=room_list,
            optional_admin_message=optional_admin_message,
            coordinating_with_section=coordinating_with_section,
            change_links_section=request_change_links(request_id, repeat_visit_url)
        )

        body = body.replace(
            "Additional Guests for Your Room(s):",
            "Confirmed Group Members:"
        ).replace(
            "Additional Guests:",
            "Confirmed Group Members:"
        )

        body = append_guest_visit_history_summary(
            body,
            conn,
            req,
            request_id
        )

        body = ensure_guest_change_links(
            body,
            request_id,
            repeat_visit_url
        )

        email_type_label = "Update Email"

    elif email_type == "decline":
        subject = "About Your Requested Strathmere Visit"

        decline_reason = safe_text(req["response_message"]).strip()

        if not decline_reason:
            decline_reason = "These dates are not available."

        if req["invitation_id"]:
            request_link = f"{BASE_URL}/invite/{req['invitation_id']}"
        else:
            request_link = standard_new_request_url()

        body = render_email_template(
            "decline.txt",
            guest_name=guest_name,
            arrival_date=format_date(req["arrival_date"]),
            departure_date=format_date(req["departure_date"]),
            nights=nights,
            rooms_requested=rooms_requested,
            additional_names=additional_names,
            decline_reason=decline_reason,
            request_link=request_link
        )

        body += request_change_links(
            request_id,
            repeat_visit_url
        )

        email_type_label = "Decline Email"

    else:
        email_type = "approval"
        subject = "Your Strathmere Shore Visit is Confirmed"

        body = render_email_template(
            "approval.txt",
            guest_name=guest_name,
            arrival_date=format_date(req["arrival_date"]),
            departure_date=format_date(req["departure_date"]),
            nights=nights,
            rooms_requested=rooms_requested,
            additional_names=additional_names,
            room_list=room_list,
            coordinating_with_section=coordinating_with_section,
            optional_admin_message=optional_admin_message,
            change_links_section=request_change_links(request_id, repeat_visit_url)
        )

        body = body.replace(
            "Additional Guests for Your Room(s):",
            "Confirmed Group Members:"
        ).replace(
            "Additional Guests:",
            "Confirmed Group Members:"
        )

        body = append_guest_visit_history_summary(
            body,
            conn,
            req,
            request_id
        )

        body = ensure_guest_change_links(
            body,
            request_id,
            repeat_visit_url
        )

        email_type_label = "Approval Email"

    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")

    template_metadata = email_template_metadata_html(email_type)

    photo_path = safe_text(req["photo_path"])

    if photo_path:
        photo_html = f"""
        <img src="/static/profile_photos/{photo_path}"
             style="
                 width: 90px;
                 height: 90px;
                 object-fit: cover;
                 border-radius: 8px;
                 border: 1px solid #ccc;
             ">
        """
    else:
        photo_html = """
        <div style="
            width: 90px;
            height: 90px;
            border: 1px solid #ccc;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #777;
            font-size: 12px;
            text-align: center;
        ">
            No Photo
        </div>
        """

    html = nav_links() + f"""
    <h1>Email Preview</h1>

    {template_metadata}

    <div style="
        border: 1px solid #ccc;
        padding: 14px;
        max-width: 950px;
        background-color: #f9f9f9;
        margin-bottom: 18px;
    ">

        <div style="
            display: flex;
            gap: 8px;
            align-items: flex-start;
        ">

            <div>
                {photo_html}
            </div>

            <div>
                <h2 style="margin-top: 0;">
                    {email_type_label}
                </h2>

                <p>
                    <strong>Guest:</strong> {guest_name}<br>
                    <strong>To:</strong> {recipient_email}<br>
                    <strong>Subject:</strong> {subject}
                </p>

                <p>
                    <strong>Visit:</strong>
                    {format_date(req['arrival_date'])}
                    to
                    {format_date(req['departure_date'])}
                    ({nights} night{"s" if nights != 1 else ""})
                </p>

                <p>
                    <strong>Rooms Requested:</strong> {rooms_requested}<br>
                    <strong>Assigned Room(s):</strong><br>
                    <span style="white-space: pre-line;">{room_list}</span>
                </p>
            </div>

        </div>
    </div>

    <form method="POST" action="/send-preview-email">
        <input type="hidden" name="request_id" value="{request_id}">
        <input type="hidden" name="email_type" value="{email_type}">

        <input type="hidden" name="to_email" value="{recipient_email}">
        <input type="hidden" name="subject" value="{subject}">
        <input type="hidden" name="return_to" value="/request/{request_id}">

        <label>
            <strong>Email Body</strong>
        </label><br>

        <textarea id="preview_email_body"
                  name="body"
                  rows="24"
                  cols="90"
                  style="
                      width: 100%;
                      max-width: 950px;
                      padding: 10px;
                      font-size: 17px;
                      line-height: 1.65;
                      box-sizing: border-box;
                  ">{body}</textarea>

        <br>

        <button type="button" onclick="copyPreviewEmail();">
            Copy Email Body
        </button>

        <button type="submit"
                style="
                    font-weight: bold;
                    padding: 6px 12px;
                ">
            Send {email_type_label}
        </button>
    </form>

    <p id="copy_message" style="font-weight: bold; color: green;"></p>

    <script>
        function copyPreviewEmail() {{
            const emailBody = document.getElementById("preview_email_body");

            emailBody.select();
            emailBody.setSelectionRange(0, 99999);

            navigator.clipboard.writeText(emailBody.value);

            document.getElementById("copy_message").innerText =
                "Email copied.";
        }}
    </script>

    <br>

    <p>
        <strong style="color: #198754;">Done</strong> |
        <a href="/requests">Request Review</a>
    </p>
    """

    try:
        conn.close()
    except Exception:
        pass

    return html

@app.route("/request/<int:request_id>/edit", methods=["GET", "POST"])
def edit_request(request_id):

    conn = get_db_connection()

    req = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (request_id,)).fetchone()

    if not req:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href="/requests">
                Back to requests
            </a>
        </p>
        """

    assigned_rooms = conn.execute("""
        SELECT
            bookings.room_id,
            rooms.name AS room_name
        FROM bookings

        JOIN rooms
            ON bookings.room_id = rooms.id

        WHERE bookings.request_id = ?

        ORDER BY rooms.name
    """, (request_id,)).fetchall()

    return_to = (
        request.args.get("return_to")
        or request.form.get("return_to")
        or ""
    )

    # V32.8: guest-submitted pending requests are read-only.
    # Admin edit pages do not use return_to=submitted, so this blocks only the guest-facing edit path.
    if return_to == "submitted" and safe_text(req["status"]) == "pending":
        conn.close()
        return redirect(f"/request/{request_id}/submitted")

    if request.method == "POST":

        name = request.form.get("name")

        email = request.form.get("email")

        arrival_date = request.form.get("arrival_date")

        departure_date = request.form.get("departure_date")

        children = request.form.get("children")

        pets = request.form.get("pets")

        food_restrictions = request.form.get(
            "food_restrictions"
        )

        comments = request.form.get("comments")

        additional_names = request.form.get(
            "additional_names"
        )

        coordination_notes = req["coordination_notes"]

        response_message = request.form.get(
            "response_message"
        )

        status = request.form.get("status")

        rooms_requested = request.form.get(
            "rooms_requested"
        )

        if not rooms_requested:

            rooms_requested = 1

        rooms_requested = int(rooms_requested)

        if rooms_requested < 1:

            rooms_requested = 1

        if rooms_requested > 4:

            rooms_requested = 4

        try:

            new_arrival = datetime.strptime(
                arrival_date,
                "%Y-%m-%d"
            )

            new_departure = datetime.strptime(
                departure_date,
                "%Y-%m-%d"
            )

        except:

            conn.close()

            return """
            <h2>Invalid dates.</h2>

            <p>
                Please enter valid arrival and departure dates.
            </p>

            <p>
                <a href="javascript:history.back()">
                    Go Back
                </a>
            </p>
            """

        if new_departure <= new_arrival:

            conn.close()

            return """
            <h2>Invalid date range.</h2>

            <p>
                Departure date must be after arrival date.
            </p>

            <p>
                <a href="javascript:history.back()">
                    Go Back
                </a>
            </p>
            """

        ensure_house_block_columns(conn)

        blocked_conflict = conn.execute("""
            SELECT *
            FROM blocked_dates
            WHERE start_date < ?
              AND end_date > ?
              AND COALESCE(is_full_block, 1) = 1
        """, (
            departure_date,
            arrival_date
        )).fetchone()

        if blocked_conflict:

            conn.close()

            return """
            <h2>These dates are blocked.</h2>

            <p>
                The updated stay overlaps a blocked date range.
            </p>

            <p>
                <a href="javascript:history.back()">
                    Go Back
                </a>
            </p>
            """

        if status == "approved":

            for assigned_room in assigned_rooms:

                conflict = conn.execute("""
                    SELECT *
                    FROM bookings
                    WHERE room_id = ?
                      AND request_id != ?
                      AND status = 'approved'
                      AND arrival_date < ?
                      AND departure_date > ?
                """, (
                    assigned_room["room_id"],
                    request_id,
                    departure_date,
                    arrival_date
                )).fetchone()

                if conflict:

                    conn.close()

                    return f"""
                    <h2>Room conflict.</h2>

                    <p>
                        {assigned_room['room_name']}
                        is not available for the updated dates.
                    </p>

                    <p>
                        <a href="javascript:history.back()">
                            Go Back
                        </a>
                    </p>
                    """

        important_change = False

        if (
            name != req["name"]
            or email != req["email"]
            or arrival_date != req["arrival_date"]
            or departure_date != req["departure_date"]
            or additional_names != req["additional_names"]
            or rooms_requested != req["rooms_requested"]
            or coordination_notes != req["coordination_notes"]
        ):

            important_change = True

        new_email_status = req["email_status"]

        new_email_needed_type = req["email_needed_type"]

        if important_change:

            status = "pending"

            new_email_status = "not_needed"

            new_email_needed_type = ""

        elif status == "declined":

            new_email_status = "needs_email"

            new_email_needed_type = "decline"

        elif (
            status == "approved"
            and req["status"] != "approved"
        ):

            new_email_status = "needs_email"

            new_email_needed_type = "approval"

        elif status == "pending":

            new_email_status = "not_needed"

            new_email_needed_type = ""

        conn.execute("""
            UPDATE booking_requests

            SET
                name = ?,
                email = ?,
                arrival_date = ?,
                departure_date = ?,
                children = ?,
                pets = ?,
                food_restrictions = ?,
                comments = ?,
                additional_names = ?,
                coordination_notes = ?,
                rooms_requested = ?,
                status = ?,
                response_message = ?,
                email_status = ?,
                email_needed_type = ?

            WHERE id = ?
        """, (
            name,
            email,
            arrival_date,
            departure_date,
            children,
            pets,
            food_restrictions,
            comments,
            additional_names,
            coordination_notes,
            rooms_requested,
            status,
            response_message,
            new_email_status,
            new_email_needed_type,
            request_id
        ))

        if status == "approved":

            conn.execute("""
                UPDATE bookings
                SET
                    arrival_date = ?,
                    departure_date = ?
                WHERE request_id = ?
            """, (
                arrival_date,
                departure_date,
                request_id
            ))

        else:

            coordination_link = conn.execute("""
                SELECT coordination_group_id
                FROM booking_requests
                WHERE id = ?
            """, (
                request_id,
            )).fetchone()

            if coordination_link and coordination_link["coordination_group_id"]:

                conn.execute("""
                    UPDATE coordination_groups
                    SET status = 'booking_handoff',
                        final_visit_confirmation_sent_at = NULL,
                        closed_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    coordination_link["coordination_group_id"],
                ))

        conn.commit()

        conn.close()

        if return_to == "submitted":

            return redirect(f"/request/{request_id}/submitted")

        return redirect(f"/request/{request_id}")

    rooms_requested = req["rooms_requested"]

    if not rooms_requested:

        rooms_requested = 1

    rooms_assigned_display = ""

    if assigned_rooms:

        rooms_assigned_display = "<ul>"

        for room in assigned_rooms:

            rooms_assigned_display += f"""
            <li>{room['room_name']}</li>
            """

        rooms_assigned_display += "</ul>"

    else:

        rooms_assigned_display = """
        <p>No rooms assigned yet.</p>
        """

    coordination_notes = req["coordination_notes"]

    if not coordination_notes:

        coordination_notes = ""

    html = nav_links() + f"""
    <h1>Edit Request</h1>

    <form method="POST"
          action="/request/{request_id}/edit">

        <input type="hidden" name="return_to" value="{return_to}">

        <label>Name:</label><br>

        <input type="text"
               name="name"
               value="{req['name']}"><br>

        <label>Email:</label><br>

        <input type="email"
               name="email"
               value="{req['email']}"><br>

        <label>Arrival Date:</label><br>

        <input type="date"
               id="arrival_date"
               name="arrival_date"
               value="{req['arrival_date']}"><br>

        <label>Departure Date:</label><br>

        <input type="date"
               id="departure_date"
               name="departure_date"
               value="{req['departure_date']}"><br>

        <script>
            document.getElementById("arrival_date")
                .addEventListener(
                    "change",
                    function () {{

                        const arrivalInput =
                            document.getElementById(
                                "arrival_date"
                            );

                        const departureInput =
                            document.getElementById(
                                "departure_date"
                            );

                        departureInput.min =
                            arrivalInput.value;

                        if (
                            !departureInput.value
                            || departureInput.value <= arrivalInput.value
                        ) {{

                            departureInput.value = "";
                        }}
                    }}
                );
        </script>

        <label>Rooms Requested:</label><br>

        <select name="rooms_requested">

            <option value="1"
                {"selected" if rooms_requested == 1 else ""}>
                1
            </option>

            <option value="2"
                {"selected" if rooms_requested == 2 else ""}>
                2
            </option>

            <option value="3"
                {"selected" if rooms_requested == 3 else ""}>
                3
            </option>

            <option value="4"
                {"selected" if rooms_requested == 4 else ""}>
                4
            </option>

        </select>

        <br>

        <label>Additional Guests for Your Room(s):</label><br>

        <textarea name="additional_names">
{req['additional_names']}
</textarea><br>

        <input type="hidden" name="children" value="{req['children'] or 0}">

        <label>Pets:</label><br>

        <input type="text"
               name="pets"
               value="{req['pets']}"><br>

        <label>Food Restrictions:</label><br>

        <textarea name="food_restrictions">
{req['food_restrictions']}
</textarea><br>

        <label>Comments:</label><br>

        <textarea name="comments">
{req['comments']}
</textarea><br>

        <label>Status:</label><br>

        <select name="status">

            <option value="pending"
                {"selected" if req['status'] == 'pending' else ""}>
                Pending
            </option>

            <option value="approved"
                {"selected" if req['status'] == 'approved' else ""}>
                Approved
            </option>

            <option value="declined"
                {"selected" if req['status'] == 'declined' else ""}>
                Declined
            </option>

        </select>

        <br>

        <label>
            Admin Message / Update Note:
        </label><br>

        <textarea name="response_message">
{req['response_message']}
</textarea><br>

        <h2>Assigned Room(s)</h2>

        {rooms_assigned_display}

        <button type="submit">
            Save Changes
        </button>

    </form>
    """

    cancel_target = f"/request/{request_id}"

    if return_to == "submitted":
        cancel_target = f"/request/{request_id}/submitted"

    html += f"""
    <p>
        <a href="{cancel_target}">
            Cancel
        </a>
    </p>
    """

    conn.close()

    return html
@app.route("/room-assignments")
def room_assignments_page():
    conn = get_db_connection()

    rows = conn.execute("""
        SELECT
            booking_requests.*,
            invitations.invitation_title
        FROM booking_requests
        LEFT JOIN invitations ON booking_requests.invitation_id = invitations.id
        WHERE booking_requests.status IN ('pending', 'approved')
        ORDER BY booking_requests.arrival_date, booking_requests.status, booking_requests.name
    """).fetchall()

    rooms = conn.execute("""
        SELECT *
        FROM rooms
        ORDER BY id
    """).fetchall()

    existing_bookings = conn.execute("""
        SELECT
            bookings.id,
            bookings.room_id,
            bookings.arrival_date,
            bookings.departure_date,
            bookings.request_id,
            rooms.name AS room_name
        FROM bookings
        JOIN rooms ON bookings.room_id = rooms.id
        WHERE bookings.status = 'approved'
    """).fetchall()

    assigned_rooms_by_request = {}
    assigned_room_ids_by_request = {}

    for booking in existing_bookings:
        request_id = booking["request_id"]

        if request_id not in assigned_rooms_by_request:
            assigned_rooms_by_request[request_id] = []

        if request_id not in assigned_room_ids_by_request:
            assigned_room_ids_by_request[request_id] = []

        assigned_rooms_by_request[request_id].append(booking["room_name"])
        assigned_room_ids_by_request[request_id].append(booking["room_id"])
    conn.close()

    html = nav_links() + """
    <h1>Room Assignments</h1>

    <table border="1"
           cellpadding="4"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: auto;
               table-layout: auto;
               font-size: 13px;
           ">

        <tr style="background-color: #f2f2f2;">
            <th style="min-width: 90px;">Guest</th>
            <th style="min-width: 130px;">Email</th>
            <th style="min-width: 130px;">Additional Guests</th>
            <th style="min-width: 150px;">Comments</th>
            <th style="min-width: 55px;">Arrival</th>
            <th style="min-width: 55px;">Depart</th>
            <th style="min-width: 45px;">Nights</th>
            <th style="min-width: 45px;">Rooms</th>
            <th style="min-width: 75px;">Status</th>
            <th style="min-width: 180px;">Room Assignment</th>
            <th style="min-width: 75px;">Action</th>
        </tr>
    """

    previous_arrival = None

    for row in rows:
        arrival_short = datetime.strptime(
            row["arrival_date"],
            "%Y-%m-%d"
        ).strftime("%m/%d")

        departure_short = datetime.strptime(
            row["departure_date"],
            "%Y-%m-%d"
        ).strftime("%m/%d")

        if row["arrival_date"] != previous_arrival:
            html += f"""
            <tr>
                <td colspan="11"
                    style="
                        background-color: #eee;
                        font-weight: bold;
                        font-size: 13px;
                        padding: 4px;
                    ">
                    Arrival: {arrival_short}
                </td>
            </tr>
            """

            previous_arrival = row["arrival_date"]

        status = row["status"]

        if status == "pending":
            status_display = "<strong style='color: orange;'>Pending</strong>"
        elif status == "approved":
            status_display = "<strong style='color: green;'>Approved</strong>"
        else:
            status_display = status

        rooms_requested = row["rooms_requested"]

        if not rooms_requested:
            rooms_requested = 1

        rooms_requested = int(rooms_requested)

        if rooms_requested < 1:
            rooms_requested = 1

        if rooms_requested > 4:
            rooms_requested = 4

        nights = (
            datetime.strptime(row["departure_date"], "%Y-%m-%d")
            - datetime.strptime(row["arrival_date"], "%Y-%m-%d")
        ).days

        booked_room_ids = set()

        for booking in existing_bookings:
            if booking["request_id"] == row["id"]:
                continue

            if not (
                booking["departure_date"] <= row["arrival_date"] or
                booking["arrival_date"] >= row["departure_date"]
            ):
                booked_room_ids.add(booking["room_id"])

        if status == "pending":
            room_selects_html = ""

            for i in range(1, rooms_requested + 1):
                room_options = ""

                for room in rooms:
                    if room["id"] in booked_room_ids:
                        room_options += f"""
                        <option value="{room['id']}" disabled>
                            {room['name']} - BOOKED
                        </option>
                        """
                    else:
                        room_options += f"""
                        <option value="{room['id']}">
                            {room['name']} - Available
                        </option>
                        """

                room_selects_html += f"""
                <label><strong>Room {i}:</strong></label><br>
                <select name="room_id_{i}" style="width: 150px;">
                    {room_options}
                </select><br>
                """

            conflict_warning = ""

            if booked_room_ids:
                conflict_warning = """
                <br>
                <strong style="color: red;">
                    Some rooms are already booked for these dates.
                </strong>
                """

            room_display = f"""
            <form method="POST" action="/approve/{row['id']}">
                {room_selects_html}

                <label><strong>Note:</strong></label><br>
                <textarea name="response_message"
                          rows="2"
                          style="width: 160px;"></textarea><br>

                <button type="submit">Approve</button>
            </form>
            {conflict_warning}
            """

            action_display = f"""
            <a href="/request/{row['id']}">View</a><br>
            <a href="/decline/{row['id']}">Decline</a>
            """

        else:
            assigned_rooms = assigned_rooms_by_request.get(row["id"], [])
            assigned_room_ids = assigned_room_ids_by_request.get(row["id"], [])

            current_room_display = "None"

            if assigned_rooms:
                current_room_display = "<ul style='margin: 0; padding-left: 16px;'>"

                for assigned_room in assigned_rooms:
                    current_room_display += f"<li>{assigned_room}</li>"

                current_room_display += "</ul>"

            room_selects_html = ""

            for i in range(1, rooms_requested + 1):
                current_room_id = ""

                if len(assigned_room_ids) >= i:
                    current_room_id = str(assigned_room_ids[i - 1])

                room_options = ""

                for room in rooms:
                    room_id_text = str(room["id"])
                    selected_attr = ""
                    disabled_attr = ""
                    availability_label = "Available"

                    if room_id_text == current_room_id:
                        selected_attr = "selected"
                        availability_label = "Current Room"
                    elif room["id"] in booked_room_ids:
                        disabled_attr = "disabled"
                        availability_label = "BOOKED"

                    room_options += f"""
                    <option value="{room['id']}" {selected_attr} {disabled_attr}>
                        {room['name']} - {availability_label}
                    </option>
                    """

                room_selects_html += f"""
                <label><strong>Room {i}:</strong></label><br>
                <select name="room_id_{i}" style="width: 170px;">
                    {room_options}
                </select><br>
                """

            room_display = f"""
            <strong>Current:</strong>
            {current_room_display}

            <form method="POST" action="/approve/{row['id']}" style="margin-top: 8px;">
                {room_selects_html}

                <label><strong>Update Note:</strong></label><br>
                <textarea name="response_message"
                          rows="2"
                          style="width: 170px;">Room assignment updated.</textarea><br>

                <button type="submit">
                    Save Room Change
                </button>
            </form>
            """

            action_display = f"""
            <a href="/request/{row['id']}">View</a><br>
            <a href="/request/{row['id']}/edit">Edit</a>
            """
       
        additional_guests = row["additional_names"]

        if not additional_guests:
            additional_guests = ""

        comments = row["comments"]

        if not comments:
            comments = ""

        html += f"""
        <tr>
            <td style="vertical-align: top;">
                {row['name']}
            </td>

            <td style="vertical-align: top; word-break: break-word;">
                {row['email']}
            </td>

            <td style="vertical-align: top; white-space: normal;">
                {additional_guests}
            </td>

            <td style="vertical-align: top; white-space: normal;">
                {comments}
            </td>

            <td style="vertical-align: top;">
                {arrival_short}
            </td>

            <td style="vertical-align: top;">
                {departure_short}
            </td>

            <td style="vertical-align: top; text-align: center;">
                {nights}
            </td>

            <td style="vertical-align: top; text-align: center;">
                {rooms_requested}
            </td>

            <td style="vertical-align: top;">
                {status_display}
            </td>

            <td style="vertical-align: top;">
                {room_display}
            </td>

            <td style="vertical-align: top;">
                {action_display}
            </td>
        </tr>
        """

    html += "</table>"

    return html


@app.route("/blocked", methods=["GET", "POST"])
def blocked_page():
    conn = get_db_connection()
    ensure_house_block_columns(conn)
    error_message = ""

    if request.method == "POST":
        start_date = clean_text(request.form.get("start_date"))
        end_date = clean_text(request.form.get("end_date"))
        reason = clean_text(request.form.get("reason"))
        block_type = clean_text(request.form.get("block_type") or "full")
        rooms_available_text = clean_text(request.form.get("rooms_available"))

        is_full_block = 1
        rooms_available = 0

        if block_type == "partial":
            is_full_block = 0
            try:
                rooms_available = int(rooms_available_text)
            except Exception:
                rooms_available = 0

            total_rooms_row = conn.execute("SELECT COUNT(*) AS count FROM rooms").fetchone()
            total_rooms_for_block = int(total_rooms_row["count"] or 4)

            if rooms_available < 1 or rooms_available >= total_rooms_for_block:
                error_message = "Partial house block must leave at least 1 room available and fewer than the full house room count."

        if error_message:
            pass
        elif not valid_date_range(start_date, end_date):
            error_message = "Start date and end date are required, and end date cannot be before start date."
        else:
            conn.execute("""
                INSERT INTO blocked_dates
                (start_date, end_date, reason, is_full_block, rooms_available)
                VALUES (?, ?, ?, ?, ?)
            """, (
                start_date,
                end_date,
                reason,
                is_full_block,
                rooms_available
            ))

            conn.commit()
            conn.close()
            return redirect("/blocked")

    blocked = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    conn.close()

    error_html = ""

    if error_message:
        error_html = f"""
        <div style="background:#ffe5e5; border:1px solid #dc3545; padding:10px; border-radius:6px; margin-bottom:12px;">
            <strong>Block not saved:</strong> {safe_text(error_message)}
        </div>
        """

    today_value = date.today().strftime("%Y-%m-%d")

    html = nav_links() + f"""
    <h1>House Blocks</h1>

    {error_html}

    <h2>Add House Block</h2>

    <form method="POST" action="/blocked">
        <label>Start Date:</label><br>
        <input type="date" name="start_date" value="{today_value}" min="{today_value}" autocomplete="off" required><br>

        <label>End Date:</label><br>
        <input type="date" name="end_date" value="{today_value}" min="{today_value}" autocomplete="off" required><br>

        <label>Block Type:</label><br>
        <select name="block_type">
            <option value="full">Full house block (calendar pink / unavailable)</option>
            <option value="partial">Partial capacity limit (calendar not pink)</option>
        </select><br>

        <label>Rooms Available During Block (partial only):</label><br>
        <input type="number" name="rooms_available" min="1" max="4"><br>

        <label>Reason:</label><br>
        <input type="text" name="reason"><br>

        <button type="submit">Add Block</button>
    </form>

    <h2>Current House Blocks</h2>
    
    <script>
        // V32.7: force add-block date pickers to start on the current date/month,
        // including browser back/forward cache and autofill restoration.
        function forceHouseBlockDatesToToday() {{
            const today = "{today_value}";
            document.querySelectorAll('form[action="/blocked"] input[type="date"]').forEach(function (input) {{
                input.min = today;
                input.defaultValue = today;
                input.setAttribute("value", today);
                input.value = today;
            }});
        }}

        document.addEventListener("DOMContentLoaded", forceHouseBlockDatesToToday);
        window.addEventListener("pageshow", forceHouseBlockDatesToToday);
        setTimeout(forceHouseBlockDatesToToday, 50);
    </script>

    """

    if not blocked:
        html += "<p>No house blocks yet.</p>"

    else:
        html += """
        <table border="1"
               cellpadding="4"
               cellspacing="0"
               style="border-collapse: collapse; font-size: 13px;">
            <tr style="background-color: #f2f2f2;">
                <th>Start</th>
                <th>End</th>
                <th>Type</th>
                <th>Rooms Available</th>
                <th>Reason</th>
                <th>Action</th>
            </tr>
        """

        for block in blocked:
            start_date_value = safe_text(block["start_date"]).strip()
            end_date_value = safe_text(block["end_date"]).strip()

            parsed_start = parse_iso_date_safe(start_date_value)
            parsed_end = parse_iso_date_safe(end_date_value)

            if parsed_start:
                start_short = parsed_start.strftime("%m/%d/%Y")
            else:
                start_short = "Invalid / blank"

            if parsed_end:
                end_short = parsed_end.strftime("%m/%d/%Y")
            else:
                end_short = "Invalid / blank"

            block_type_label = "Full Block" if block_is_full(block) else "Partial Capacity"
            rooms_available_label = "0" if block_is_full(block) else safe_text(block_rooms_available(block, 4))

            invalid_note = ""

            if not parsed_start or not parsed_end:
                invalid_note = "<br><strong style='color:red;'>Needs repair</strong>"

            html += f"""
            <tr>
                <td>{safe_text(start_short)}</td>
                <td>{safe_text(end_short)}</td>
                <td>{safe_text(block_type_label)}</td>
                <td>{safe_text(rooms_available_label)}</td>
                <td>{safe_text(block['reason'])}{invalid_note}</td>
                <td>
                    <a href="/blocked/{block['id']}/edit">Edit</a>
                    &nbsp;|&nbsp;
                    <form method="POST" action="/blocked/{block['id']}/delete" style="display:inline;" onsubmit="return confirm('Remove this house block?');">
                        <button type="submit" style="color:#dc3545;">Remove</button>
                    </form>
                </td>
            </tr>
            """

        html += "</table>"

    return html


@app.route("/blocked/<int:block_id>/delete", methods=["POST"])
def delete_blocked(block_id):
    conn = get_db_connection()

    conn.execute(
        "DELETE FROM blocked_dates WHERE id = ?",
        (block_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/blocked")


@app.route("/blocked/<int:block_id>/edit", methods=["GET", "POST"])
def edit_blocked(block_id):
    conn = get_db_connection()
    ensure_house_block_columns(conn)

    block = conn.execute(
        "SELECT * FROM blocked_dates WHERE id = ?",
        (block_id,)
    ).fetchone()

    if not block:
        conn.close()
        return """
        <h2>Blocked date not found.</h2>
        <p><a href="/blocked">Back to blocked dates</a></p>
        """

    error_message = ""

    if request.method == "POST":
        start_date = clean_text(request.form.get("start_date"))
        end_date = clean_text(request.form.get("end_date"))
        reason = clean_text(request.form.get("reason"))
        block_type = clean_text(request.form.get("block_type") or "full")
        rooms_available_text = clean_text(request.form.get("rooms_available"))

        is_full_block = 1
        rooms_available = 0

        if block_type == "partial":
            is_full_block = 0
            try:
                rooms_available = int(rooms_available_text)
            except Exception:
                rooms_available = 0

            total_rooms_row = conn.execute("SELECT COUNT(*) AS count FROM rooms").fetchone()
            total_rooms_for_block = int(total_rooms_row["count"] or 4)

            if rooms_available < 1 or rooms_available >= total_rooms_for_block:
                error_message = "Partial house block must leave at least 1 room available and fewer than the full house room count."

        if error_message:
            pass
        elif not valid_date_range(start_date, end_date):
            error_message = "Start date and end date are required, and end date cannot be before start date."
        else:
            conn.execute("""
                UPDATE blocked_dates
                SET start_date = ?,
                    end_date = ?,
                    reason = ?,
                    is_full_block = ?,
                    rooms_available = ?
                WHERE id = ?
            """, (
                start_date,
                end_date,
                reason,
                is_full_block,
                rooms_available,
                block_id
            ))

            conn.commit()
            conn.close()

            return redirect("/blocked")

    start_value = safe_text(block["start_date"]).strip()
    end_value = safe_text(block["end_date"]).strip()
    reason_value = safe_text(block["reason"]).strip()
    full_selected = "selected" if block_is_full(block) else ""
    partial_selected = "selected" if not block_is_full(block) else ""
    rooms_available_value = "" if block_is_full(block) else safe_text(block_rooms_available(block, 4))

    error_html = ""

    if error_message:
        error_html = f"""
        <div style="background:#ffe5e5; border:1px solid #dc3545; padding:10px; border-radius:6px; margin-bottom:12px;">
            <strong>Block not saved:</strong> {safe_text(error_message)}
        </div>
        """

    html = nav_links() + f"""
    <h1>Edit Blocked Date</h1>

    {error_html}

    <form method="POST" action="/blocked/{block_id}/edit">
        <label>Start Date:</label><br>
        <input type="date" name="start_date" value="{safe_text(start_value)}" required><br>

        <label>End Date:</label><br>
        <input type="date" name="end_date" value="{safe_text(end_value)}" required><br>

        <label>Block Type:</label><br>
        <select name="block_type">
            <option value="full" {full_selected}>Full house block (calendar pink / unavailable)</option>
            <option value="partial" {partial_selected}>Partial capacity limit (calendar not pink)</option>
        </select><br>

        <label>Rooms Available During Block (partial only):</label><br>
        <input type="number" name="rooms_available" min="1" max="4" value="{safe_text(rooms_available_value)}"><br>

        <label>Reason:</label><br>
        <input type="text" name="reason" value="{safe_text(reason_value)}"><br>

        <button type="submit">Save Changes</button>
    </form>

    <form method="POST" action="/blocked/{block_id}/delete" style="margin-top:10px;" onsubmit="return confirm('Remove this house block?');">
        <button type="submit" style="background:#dc3545; color:white; border:none; padding:6px 10px; border-radius:4px;">Remove Block</button>
    </form>

    <p><a href="/blocked">Cancel</a></p>
    """

    conn.close()

    return html

@app.route("/send-preview-email", methods=["POST"])
def send_preview_email():
    to_email = request.form.get("to_email")
    subject = request.form.get("subject")
    body = request.form.get("body")
    return_to = request.form.get("return_to") or "/requests"
    request_id = request.form.get("request_id")
    email_type = request.form.get("email_type") or "general"

    clean_request_id = None

    if request_id:

        try:

            clean_request_id = int(request_id)

        except:

            clean_request_id = None

    conn = get_db_connection()

    backup_path = ""

    request_row = None

    if clean_request_id:

        request_row = conn.execute("""
            SELECT *
            FROM booking_requests
            WHERE id = ?
        """, (
            clean_request_id,
        )).fetchone()

        if not safe_text(to_email).strip() and request_row:

            to_email = resolve_request_recipient_email(
                conn,
                request_row
            )

        if not safe_text(to_email).strip():

            conn.close()

            return f"""
            {nav_links()}

            <h1>Email Not Sent</h1>

            <p style="
                color: red;
                font-weight: bold;
            ">
                This request does not have a valid recipient email address.
            </p>

            <p>
                Please open the request, add the guest email address,
                and then try sending the email again.
            </p>

            <p>
                <a href="{return_to}">
                    Back
                </a>
            </p>
            """

        if request_row and request_row["email_status"] == "sent" and request.form.get("force_send") != "yes":

            conn.close()

            return f"""
            {nav_links()}

            <h1>Email Already Sent</h1>

            <p style="
                color: #842029;
                font-weight: bold;
            ">
                This request is already marked as Sent.
            </p>

            <p>
                This protects you from accidentally emailing
                the same guest twice.
            </p>

            <form method="POST"
                  action="/send-preview-email">

                <input type="hidden" name="request_id" value="{clean_request_id}">
                <input type="hidden" name="email_type" value="{email_type}">
                <input type="hidden" name="to_email" value="{to_email}">
                <input type="hidden" name="subject" value="{subject}">
                <input type="hidden" name="return_to" value="{return_to}">
                <input type="hidden" name="force_send" value="yes">

                <textarea name="body"
                          style="display: none;">{body}</textarea>

                <button type="submit">
                    Send Again Anyway
                </button>
            </form>

            <p>
                <a href="{return_to}">
                    Back
                </a>
            </p>
            """

    if not safe_text(to_email).strip():

        conn.close()

        return f"""
        {nav_links()}

        <h1>Email Not Sent</h1>

        <p style="
            color: red;
            font-weight: bold;
        ">
            No recipient email address was provided.
        </p>

        <p>
            <a href="{return_to}">
                Back
            </a>
        </p>
        """

    backup_path = create_database_backup(
        "before_send_email"
    )

    send_email(to_email, subject, body)

    try:

        conn.execute("BEGIN")

        conn.execute("""
            INSERT INTO email_log
            (request_id, email_type, recipient, subject, body)
            VALUES (?, ?, ?, ?, ?)
        """, (
            clean_request_id,
            email_type,
            to_email,
            subject,
            body
        ))

        updated_count = 0

        if clean_request_id:

            cursor = conn.execute("""
                UPDATE booking_requests
                SET email_status = ?,
                    email_needed_type = ?
                WHERE id = ?
            """, (
                "sent",
                "",
                clean_request_id
            ))

            updated_count = cursor.rowcount

        if clean_request_id:

            old_status = ""

            if request_row:
                old_status = request_row["status"]

            write_activity_log(
                conn,
                clean_request_id,
                "email_sent",
                old_status,
                old_status,
                f"Email type: {email_type}. Backup: {backup_path}"
            )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            return_to
        )

    conn.close()

    if clean_request_id and updated_count == 0:

        status_message = """
        <p style="color: red; font-weight: bold;">
            Email was sent, but the request status was not updated.
            Please return to the request and check the email status manually.
        </p>
        """

    else:

        status_message = """
        <p style="color: green; font-weight: bold;">
            Email status was updated to Sent.
        </p>
        """

    return f"""
    {nav_links()}
    <h1>Request Email Sent</h1>

    <p>Email was sent to: <strong>{to_email}</strong></p>
    <p><strong>Subject:</strong> {subject}</p>

    {status_message}

    <p>
        <a href="{return_to}">Back to Previous Page</a> |
        <a href="/requests">Request Review</a>
    </p>
    """

@app.route("/preview-invitation-email/<int:invitation_id>")
def preview_invitation_email(invitation_id):

    conn = get_db_connection()

    invite = conn.execute("""
        SELECT
            invitations.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            guest_profiles.photo_path
        FROM invitations

        JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id

        WHERE invitations.id = ?
    """, (
        invitation_id,
        
    )).fetchone()

    if not invite:

        conn.close()

        return """
        <h2>
            Invitation not found.
        </h2>

        <p>
            <a href="/invitations">
                Back to Invitations
            </a>
        </p>
        """

    request_link = f"{BASE_URL}/invite/{invitation_id}"

    coordination_link = f"{BASE_URL}/coordinate/{invitation_id}"

    subject = invite["invitation_title"]

    if not subject:

        subject = "Strathmere Visit Invitation"

    message = invite["message"]

    if not message:

        message = (
            "It’s that time of year again, and we’d love to invite "
            "you down to the shore! We always enjoy having visitors "
            "spend some time with us in Strathmere, and we hope "
            "you can make it down for a visit this summer."
        )

    photo_path = safe_text(
        invite["photo_path"]
    )

    if photo_path:

        photo_preview_html = f"""
        <img src="/static/profile_photos/{photo_path}"
             style="
                 width: 90px;
                 height: 90px;
                 object-fit: cover;
                 border-radius: 8px;
                 border: 1px solid #ccc;
             ">
        """

        photo_email_html = f"""
<img src="{BASE_URL}/static/profile_photos/{photo_path}"
     style="
         width: 140px;
         border-radius: 10px;
         margin-top: 10px;
         margin-bottom: 14px;
     ">
"""

    else:

        photo_preview_html = """
        <div style="
            width: 90px;
            height: 90px;
            border: 1px solid #ccc;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #777;
            font-size: 12px;
        ">
            No Photo
        </div>
        """

        photo_email_html = ""

    # V30.0:
    # Invitation wording is controlled by templates/emails/invitation.txt.
    # The optional saved invitation message is available ONLY if the template
    # explicitly includes {{ message }}. This prevents hidden/automatic leakage
    # while restoring your ability to add a custom invite note.
        
    existing_reservations_section = existing_reservations_section_for_guest(
        conn,
        row_value(invite, "guest_profile_id")
    )
    conn.close()
    body = render_email_template(
        "invitation.txt",
        guest_name=safe_text(invite["primary_name"]),
        message=safe_text(row_value(invite, "message")),
        request_link=request_link,
        coordination_link=coordination_link,
        existing_reservations_section=existing_reservations_section
    )

    template_metadata = email_template_metadata_html("invitation")
    template_admin_box = invitation_template_admin_box()

    html = nav_links() + f"""
    <h1>
        Invitation Email Preview
    </h1>

    {template_metadata}
    {template_admin_box}

    <div style="
        border: 1px solid #ccc;
        background-color: #f9f9f9;
        padding: 16px;
        max-width: 950px;
        margin-bottom: 20px;
    ">

        <div style="
            margin-bottom: 12px;
        ">
            {photo_preview_html}
        </div>

        <h2 style="
            margin-top: 0;
        ">
            {subject}
        </h2>

        <p>
            <strong>
                Guest:
            </strong>

            {invite['primary_name']}
        </p>

        <p>
            <strong>
                Email:
            </strong>

            {invite['primary_email']}
        </p>

        <p>
            <strong>
                Email Body Preview
            </strong>
        </p>

    </div>

    <form method="POST"
          action="/send-invitation-email">

        <input type="hidden"
               name="invitation_id"
               value="{invitation_id}">

        <input type="hidden"
               name="to_email"
               value="{invite['primary_email']}">

        <input type="hidden"
               name="subject"
               value="{subject}">

        <label>
            <strong>
                Email Preview
            </strong>
        </label><br>

        <textarea readonly
                  rows="30"
                  cols="90"
                  style="
                      width: 100%;
                      max-width: 950px;
                      padding: 12px;
                      box-sizing: border-box;
                      font-size: 14px;
                      line-height: 1.5;
                  ">{body}</textarea>

        <br>

        <button type="submit"
                style="
                    padding: 8px 14px;
                    font-weight: bold;
                ">

            Send Invitation Email

        </button>

    </form>

    <br>

    <p>
        <a href="/invitations">
            Back to Invitations
        </a>
    </p>
    """

    return html
   
@app.route("/send-invitation-email", methods=["POST"])
def send_invitation_email():
    invitation_id = request.form.get("invitation_id")
    to_email = clean_text(request.form.get("to_email")).lower()
    subject = request.form.get("subject")

    conn = get_db_connection()

    invite = conn.execute("""
        SELECT
            invitations.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM invitations
        JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id
        WHERE invitations.id = ?
    """, (invitation_id,)).fetchone()

    if not invite:

        conn.close()

        return profile_error_page(
            "Invitation not found.",
            "/invitations"
        )

    profile_email = clean_text(invite["primary_email"]).lower()

    if is_valid_email_address(profile_email):
        to_email = profile_email

    if not is_valid_email_address(to_email):

        conn.close()

        return profile_error_page(
            "Invitation email was not sent because this guest profile does not have a valid email address.",
            f"/profile/{invite['guest_profile_id']}/edit"
        )

    request_link = f"{BASE_URL}/invite/{invitation_id}"
    coordination_link = f"{BASE_URL}/coordinate/{invitation_id}"

    # V30.0:
    # Never send a posted preview textarea body.
    # Rebuild the final email from the current invitation.txt template at send time.
    # The optional saved invitation message is available only where the template
    # explicitly includes {{ message }}.
    
    existing_reservations_section = existing_reservations_section_for_guest(
        conn,
        row_value(invite, "guest_profile_id")
    )

    body = render_email_template(
        "invitation.txt",
        guest_name=safe_text(invite["primary_name"]),
        message=safe_text(row_value(invite, "message")),
        request_link=request_link,
        coordination_link=coordination_link,
        existing_reservations_section=existing_reservations_section
    )

    send_email(to_email, subject, body)

    conn.execute("""
        UPDATE invitations
        SET status = ?
        WHERE id = ?
    """, (
        "sent",
        invitation_id
    ))

    conn.commit()
    conn.close()

    return f"""
    {nav_links()}

    <h1>Invitation Email Sent</h1>

    <p>Email was sent to: <strong>{to_email}</strong></p>

    <p>
        <a href="/invitations">Back to Invitations</a>
    </p>
    """

@app.route("/coordinate/<int:invitation_id>", methods=["GET", "POST"])
def coordinate_request(invitation_id):

    conn = get_db_connection()

    invitation = conn.execute("""
        SELECT
            invitations.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM invitations

        JOIN guest_profiles
            ON invitations.guest_profile_id = guest_profiles.id

        WHERE invitations.id = ?
    """, (
        invitation_id,
    )).fetchone()

    if not invitation:

        conn.close()

        return """
        <h1>Invitation Not Found</h1>

        <p>
            The invitation could not be found.
        </p>
        """

    selected_year = int(
        request.args.get(
            "year",
            date.today().year
        )
    )

    selected_month = int(
        request.args.get(
            "month",
            date.today().month
        )
    )

    if selected_month < 1:

        selected_month = 12
        selected_year -= 1

    if selected_month > 12:

        selected_month = 1
        selected_year += 1

    ensure_house_block_columns(conn)

    blocked_rows = conn.execute("""
        SELECT
            start_date,
            end_date
        FROM blocked_dates
    """).fetchall()

    blocked_dates = set()

    for block in blocked_rows:

        current = datetime.strptime(
            block["start_date"],
            "%Y-%m-%d"
        )

        end = datetime.strptime(
            block["end_date"],
            "%Y-%m-%d"
        )

        while current <= end:

            blocked_dates.add(
                current.strftime("%Y-%m-%d")
            )

            current += timedelta(days=1)

    approved_bookings = conn.execute("""
        SELECT
            arrival_date,
            departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    if request.method == "POST":

        primary_name = request.form.get(
            "primary_name"
        )

        primary_email = request.form.get(
            "primary_email"
        )

        coordinator_name = request.form.get(
            "coordinator_name"
        )

        coordinator_email = request.form.get(
            "coordinator_email"
        )

        coordinating_with = request.form.get(
            "coordinating_with"
        )

        arrival_date = request.form.get(
            "arrival_date"
        )

        departure_date = request.form.get(
            "departure_date"
        )

        rooms_requested = request.form.get(
            "rooms_requested"
        )

        comments = request.form.get(
            "comments"
        )

        validation_error = request_identity_validation_error(
            primary_name,
            primary_email
        )

        if validation_error:

            conn.close()

            return request_identity_error_page(
                validation_error,
                f"/coordinate/{invitation_id}"
            )

        validation_error = request_identity_validation_error(
            coordinator_name,
            coordinator_email
        )

        if validation_error:

            conn.close()

            return request_identity_error_page(
                validation_error,
                f"/coordinate/{invitation_id}"
            )

        coordination_notes = f"""
Primary Coordinator:
{coordinator_name}

Coordinator Email:
{coordinator_email}

Coordinating With:
{coordinating_with}
"""

        conn.execute("""
            INSERT INTO booking_requests
            (
                invitation_id,
                name,
                email,
                arrival_date,
                departure_date,
                rooms_requested,
                comments,
                coordination_notes,
                status,
                email_status,
                email_needed_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            invitation_id,
            primary_name,
            primary_email,
            arrival_date,
            departure_date,
            rooms_requested,
            comments,
            coordination_notes,
            "pending",
            "needs_email",
            "coordination_request"
        ))

        conn.commit()

        conn.close()

        return f"""
        {nav_links()}

        <h1>Thanks!</h1>

        <div style="background-color: #e8f7ea; border: 1px solid #198754; padding: 12px 14px; border-radius: 8px; max-width: 760px; line-height: 1.4;">
            <p style="font-weight: bold; margin-top: 0;">
                Your group request has been sent. Herding calendars has begun.
            </p>
            <p style="margin-bottom: 0;">
                <strong>What happens next?</strong><br>
                I’ll look at the group details and follow up with the coordinator if anything needs sorting out.
            </p>
        </div>

        <p>
            <a href="/invitations">
                Return to Invitations
            </a>
        </p>
        """

    room_capacity = {}

    first_day = date(
        selected_year,
        selected_month,
        1
    )

    if selected_month == 12:

        next_month_date = date(
            selected_year + 1,
            1,
            1
        )

    else:

        next_month_date = date(
            selected_year,
            selected_month + 1,
            1
        )

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:

        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:

        next_month = 1
        next_year += 1

    days_in_month = (
        next_month_date - first_day
    ).days

    start_weekday = (first_day.weekday() + 1) % 7

    month_title = first_day.strftime(
        "%B %Y"
    )

    current = first_day

    while current < next_month_date:

        rooms_used = 0

        for booking in approved_bookings:

            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            if booking_start <= current < booking_end:

                rooms_used += 1

        room_capacity[
            current.strftime("%Y-%m-%d")
        ] = total_rooms - rooms_used

        current += timedelta(days=1)

    blocked_dates_list = sorted(
        list(blocked_dates)
    )

    conn.close()

    calendar_html = f"""
    <h2 id="calendar-section">
        Capacity Calendar - {month_title}
    </h2>

    <p>
        <a href="/coordinate/{invitation_id}?year={previous_year}&month={previous_month}#calendar-section">
            Previous Month
        </a>

        |

        <strong>
            {month_title}
        </strong>

        |

        <a href="/coordinate/{invitation_id}?year={next_year}&month={next_month}#calendar-section">
            Next Month
        </a>
    </p>

    <table border="1"
           cellpadding="5"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               max-width: 760px;
           ">

        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>

        <tr>
    """

    for _ in range(start_weekday):

        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):

        current_date = date(
            selected_year,
            selected_month,
            day
        )

        current_date_str = current_date.strftime(
            "%Y-%m-%d"
        )

        today = date.today()

        past_date = current_date < today

        rooms_open = room_capacity.get(
            current_date_str,
            total_rooms
        )

        if past_date:

            background = "#e9ecef"
            status = "Past"
            display_line_1 = ""
            display_line_2 = "Past"
            click_handler = ""
            cursor = "not-allowed"

        elif current_date_str in blocked_dates:

            background = "#f8d7da"
            status = "Blocked"
            display_line_1 = ""
            display_line_2 = "Blocked"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 0:

            background = "#f8d7da"
            status = "Full"
            display_line_1 = "0 open"
            display_line_2 = "Full"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 2:

            background = "#fff3cd"
            status = "Almost Full"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Almost Full"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        else:

            background = "#d4edda"
            status = "Open"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Open"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        calendar_html += f"""
        <td {click_handler}
            data-date="{current_date_str}"
            data-rooms-open="{rooms_open}"
            data-status="{status}"
            style="
                background-color: {background};
                vertical-align: top;
                width: 75px;
                height: 58px;
                font-size: 12px;
                text-align: center;
                cursor: {cursor};
            ">

            <strong>
                {day}
            </strong>

            <br>

            <small>
                {display_line_1}
            </small>

            <br>

            <small>
                {display_line_2}
            </small>

        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0 and day != days_in_month:

            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:

        calendar_html += "<td></td>"

        day_counter += 1

    calendar_html += """
        </tr>
    </table>

    <p style="font-size: 13px;">

        <strong>
            Legend:
        </strong>

        <span style="background-color: #d4edda; padding: 4px;">
            Open
        </span>

        <span style="background-color: #fff3cd; padding: 4px;">
            Almost Full
        </span>

        <span style="background-color: #f8d7da; padding: 4px;">
            Full / Blocked
        </span>

        <span style="background-color: #e9ecef; padding: 4px;">
            Past
        </span>

    </p>
    """

    return f"""
    {nav_links()}

    <h1>
        Coordination Request
    </h1>

    <div style="
        display: flex;
        gap: 40px;
        align-items: flex-start;
        flex-wrap: wrap;
    ">

        <div style="
            flex: 1;
            min-width: 340px;
            max-width: 520px;
        ">

            <p>
                <strong>
                    Invitation:
                </strong>

                {invitation['invitation_title']}
            </p>

            <div style="
                background-color: #f8f9fa;
                padding: 14px;
                border-radius: 8px;
                margin-bottom: 20px;
                line-height: 1.5;
            ">

                <strong>
                    Coordinating a Group Visit?
                </strong>

                <br>

                Use this form if you are working with
                friends or family to coordinate shared
                travel dates.

                <br>

                Select proposed dates directly
                on the calendar.

                <br>

                Once your group confirms dates,
                we can help finalize the visit details.

            </div>

            <form method="POST"
                  onsubmit="return checkDatesSelected();">

                <h3>
                    Primary Guest
                </h3>

                <label>
                    Primary Guest Name
                </label><br>

                <input type="text"
                       name="primary_name"
                       value="{invitation['primary_name']}"
                       required
                       style="
                           width: 100%;
                           padding: 8px;
                       "><br>

                <label>
                    Primary Guest Email
                </label><br>

                <input type="email"
                       name="primary_email"
                       value="{invitation['primary_email']}"
                       required
                       style="
                           width: 100%;
                           padding: 8px;
                       "><br>

                <h3>
                    Coordinator Information
                </h3>

                <p style="
                    background-color: #f8f9fa;
                    padding: 10px;
                    border-radius: 6px;
                    line-height: 1.4;
                ">

                    By default, we assume the primary guest
                    is coordinating the visit.

                    <br>

                    If someone else is coordinating dates
                    for the group, enter their information below.

                </p>

                <label>
                    Coordinator Name
                </label><br>

                <input type="text"
                       name="coordinator_name"
                       value="{invitation['primary_name']}"
                       required
                       style="
                           width: 100%;
                           padding: 8px;
                       "><br>

                <label>
                    Coordinator Email
                </label><br>

                <input type="email"
                       name="coordinator_email"
                       value="{invitation['primary_email']}"
                       required
                       style="
                           width: 100%;
                           padding: 8px;
                       "><br>

                <label>
                    Who Else Is Coordinating?
                </label><br>

                <textarea name="coordinating_with"
                          rows="6"
                          style="
                              width: 100%;
                              padding: 8px;
                          "
                          placeholder="List names and emails of people coordinating travel dates together."
                          required></textarea><br>

        </div>

        <div style="
            flex: 1;
            min-width: 340px;
            max-width: 520px;
        ">

            {calendar_html}

            <div style="
                margin-top: 24px;
            ">

                <label>
                    Approximate Rooms Needed
                </label><br>

                <select name="rooms_requested"
                        id="rooms_requested"
                        style="
                            width: 140px;
                            padding: 8px;
                        ">

                    <option value="1">1 Room</option>
                    <option value="2">2 Rooms</option>
                    <option value="3">3 Rooms</option>
                    <option value="4">4 Rooms</option>

                </select><br>

                <label>
                    Additional Notes
                </label><br>

                <textarea name="comments"
                          rows="5"
                          style="
                              width: 100%;
                              padding: 8px;
                          "
                          placeholder="Optional coordination notes, timing concerns, or other planning details."></textarea><br>

                <input type="hidden"
                       id="arrival_date"
                       name="arrival_date">

                <input type="hidden"
                       id="departure_date"
                       name="departure_date">

                <div id="selectedDates"
                     style="
                         margin-bottom: 18px;
                         font-weight: bold;
                         color: #0d6efd;
                     ">

                    No dates selected.

                </div>

                <button type="submit"
                        style="
                            padding: 10px 18px;
                            font-size: 15px;
                            font-weight: bold;
                        ">
                    Submit Coordination Request
                </button>

            </div>

            </form>

        </div>

    </div>

    <script>

        const blockedDates = {blocked_dates_list};

        let nextDateField = "arrival";

        function formatDateForMessage(dateString) {{

            const parts = dateString.split("-");

            return (
                parts[1]
                + "/"
                + parts[2]
                + "/"
                + parts[0]
            );
        }}

        function resetSelection() {{

            document.getElementById(
                "arrival_date"
            ).value = "";

            document.getElementById(
                "departure_date"
            ).value = "";

            document.getElementById(
                "selectedDates"
            ).innerHTML = "No dates selected.";

            nextDateField = "arrival";
        }}

        function selectCalendarDate(dateString) {{

            if (
                blockedDates.includes(dateString)
            ) {{

                return;
            }}

            const arrivalField =
                document.getElementById(
                    "arrival_date"
                );

            const departureField =
                document.getElementById(
                    "departure_date"
                );

            if (
                nextDateField === "arrival"
            ) {{

                arrivalField.value = dateString;

                departureField.value = "";

                nextDateField = "departure";

                document.getElementById(
                    "selectedDates"
                ).innerHTML =
                    "Arrival selected: "
                    + formatDateForMessage(
                        dateString
                    )
                    + "<br>Select departure date.";

            }}

            else {{

                if (
                    dateString <= arrivalField.value
                ) {{

                    alert(
                        "Departure date must be after arrival date."
                    );

                    return;
                }}

                departureField.value = dateString;

                nextDateField = "arrival";

                document.getElementById(
                    "selectedDates"
                ).innerHTML =
                    "Arrival: "
                    + formatDateForMessage(
                        arrivalField.value
                    )
                    + "<br>Departure: "
                    + formatDateForMessage(
                        dateString
                    );
            }}
        }}

        function checkDatesSelected() {{

            const arrival =
                document.getElementById(
                    "arrival_date"
                ).value;

            const departure =
                document.getElementById(
                    "departure_date"
                ).value;

            if (
                !arrival ||
                !departure
            ) {{

                alert(
                    "Please select both arrival and departure dates."
                );

                return false;
            }}

            return true;
        }}

    </script>
    """

@app.route("/manual-request", methods=["GET", "POST"])
def manual_request():

    conn = get_db_connection()

    profiles = conn.execute("""
        SELECT
            id,
            primary_name,
            primary_email,
            additional_names
        FROM guest_profiles
        ORDER BY primary_name
    """).fetchall()

    if request.method == "POST":

        guest_profile_id = request.form.get(
            "guest_profile_id"
        )

        name = request.form.get(
            "name"
        )

        email = request.form.get(
            "email"
        )

        additional_names = request.form.get(
            "additional_names"
        )

        arrival_date = request.form.get(
            "arrival_date"
        )

        departure_date = request.form.get(
            "departure_date"
        )

        rooms_requested = request.form.get(
            "rooms_requested"
        )

        comments = request.form.get(
            "comments"
        )

        coordination_notes = request.form.get(
            "coordination_notes"
        )

        if not guest_profile_id:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Guest Profile Required
            </h1>

            <p>
                Please select a guest profile first.
            </p>

            <p>
                <a href="/manual-request">
                    Back to Manual Request
                </a>
            </p>
            """

        selected_profile = conn.execute("""
            SELECT *
            FROM guest_profiles
            WHERE id = ?
        """, (
            guest_profile_id,
        )).fetchone()

        if not selected_profile:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Guest Profile Not Found
            </h1>

            <p>
                The selected guest profile could not be found.
            </p>

            <p>
                <a href="/manual-request">
                    Back to Manual Request
                </a>
            </p>
            """

        if not name:
            name = selected_profile["primary_name"]

        if not email:
            email = selected_profile["primary_email"]

        if not additional_names:
            additional_names = selected_profile["additional_names"]

        try:

            arrival_obj = datetime.strptime(
                arrival_date,
                "%Y-%m-%d"
            )

            departure_obj = datetime.strptime(
                departure_date,
                "%Y-%m-%d"
            )

        except:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Invalid Dates
            </h1>

            <p>
                Please enter valid arrival and departure dates.
            </p>

            <p>
                <a href="/manual-request">
                    Back to Manual Request
                </a>
            </p>
            """

        if departure_obj <= arrival_obj:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Invalid Dates
            </h1>

            <p>
                Departure date must be after arrival date.
            </p>

            <p>
                <a href="/manual-request">
                    Back to Manual Request
                </a>
            </p>
            """

        try:
            rooms_requested = int(
                rooms_requested
            )
        except:
            rooms_requested = 1

        if rooms_requested < 1:
            rooms_requested = 1

        if rooms_requested > 4:
            rooms_requested = 4

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO booking_requests
            (
                guest_profile_id,
                invitation_id,
                name,
                email,
                additional_names,
                arrival_date,
                departure_date,
                adults,
                children,
                pets,
                food_restrictions,
                comments,
                coordination_notes,
                rooms_requested,
                status,
                email_status,
                email_needed_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guest_profile_id,
            None,
            name,
            email,
            additional_names,
            arrival_date,
            departure_date,
            "1",
            "0",
            "",
            "",
            comments,
            coordination_notes,
            rooms_requested,
            "pending",
            "not_needed",
            ""
        ))

        new_request_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return f"""
        {nav_links()}

        <h1>
            Manual Request Created
        </h1>

        <p>
            The request has been created and linked
            to the selected guest profile.
        </p>

        <p>
            This request now follows the normal
            review and approval workflow.
        </p>

        <p>
            <a href="/request/{new_request_id}">
                Review This Request
            </a>
        </p>

        <p>
            <a href="/requests">
                Go to Requests
            </a>
        </p>
        """

    conn.close()

    profile_options = ""

    for profile in profiles:

        profile_options += f"""
        <option value="{profile['id']}">
            {profile['primary_name']} — {profile['primary_email']}
        </option>
        """

    return f"""
    {nav_links()}

    <h1>
        Manual Request Entry
    </h1>

    <div style="
        max-width: 720px;
        line-height: 1.5;
    ">

        <p>
            Use this page to create a visit request
            on behalf of a guest.
        </p>

        <p>
            Manual requests should always be linked
            to a guest profile so approvals,
            bookings, calendar exports, and future
            edits remain connected properly.
        </p>

        <p>
            If the guest does not already have
            a guest profile, create the guest
            profile first.
        </p>

    </div>

    <form method="POST" onsubmit="return checkUnavailableDates();">

        <h3>
            Guest Profile
        </h3>

        <label>
            Select Existing Guest Profile
        </label><br>

        <select name="guest_profile_id"
                required
                style="
                    width: 480px;
                    padding: 8px;
                ">

            <option value="">
                Select Guest Profile
            </option>

            {profile_options}

        </select>

        <br>

        <h3>
            Request Details
        </h3>

        <label>
            Contact Name
        </label><br>

        <input type="text"
               name="name"
               placeholder="Leave blank to use guest profile name."
               style="
                   width: 480px;
                   padding: 8px;
               ">

        <br>

        <label>
            Email Address
        </label><br>

        <input type="email"
               name="email"
               placeholder="Leave blank to use guest profile email."
               style="
                   width: 480px;
                   padding: 8px;
               ">

        <br>

        <label>
            Additional Guest Name(s) for Your Room(s)
        </label><br>

        <textarea name="additional_names"
                  rows="2"
                  placeholder="Leave blank to use guest profile additional names."
                  style="
                      width: 480px;
                      padding: 8px;
                  "></textarea>

        <br>

        <label>
            Arrival Date
        </label><br>

        <input type="date"
               name="arrival_date"
               required
               style="
                   padding: 8px;
               ">

        <br>

        <label>
            Departure Date
        </label><br>

        <input type="date"
               name="departure_date"
               required
               style="
                   padding: 8px;
               ">

        <br>

        <label>
            Rooms Requested
        </label><br>

        <select id="rooms_requested"
                name="rooms_requested"
                style="
                    width: 180px;
                    padding: 8px;
                ">

            <option value="1">
                1 Room
            </option>

            <option value="2">
                2 Rooms
            </option>

            <option value="3">
                3 Rooms
            </option>

            <option value="4">
                4 Rooms
            </option>

        </select>

        <br>

        <label>
            Comments / Notes
        </label><br>

        <textarea name="comments"
                  rows="5"
                  style="
                      width: 480px;
                      padding: 8px;
                  "></textarea>

        <br>

        <label>
            Coordination Notes
        </label><br>

        <textarea name="coordination_notes"
                  rows="2"
                  style="
                      width: 480px;
                      padding: 8px;
                  "></textarea>

        <br>

        <button type="submit"
                style="
                    padding: 10px 18px;
                    font-size: 15px;
                    font-weight: bold;
                ">

            Create Manual Request

        </button>

    </form>
    """



@app.route("/request/<request_id>/change", methods=["GET", "POST"])
def change_request_bad_link(request_id):

    request_id_text = safe_text(request_id).strip()

    if request_id_text.isdigit():
        return redirect(f"/request/{request_id_text}/change")

    return f"""
    {nav_links()}

    <h1>Change Link Not Available</h1>

    <p>That change link is missing the request number.</p>

    <p>Use the new request link from the email, or reply to the email and I’ll help fix it.</p>

    <p><a href="/">Request Another Visit</a></p>
    """


@app.route("/request/<int:request_id>/change/", methods=["GET", "POST"])
@app.route("/request/<int:request_id>/change", methods=["GET", "POST"])
def change_request(request_id):

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (
        request_id,
    )).fetchone()

    if not request_row:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href="/requests">
                Back to requests
            </a>
        </p>
        """

    current_rooms = request_row["rooms_requested"]

    if not current_rooms:
        current_rooms = 1

    validation_error = request_identity_validation_error(
        request_row["name"],
        request_row["email"]
    )

    if validation_error:

        conn.close()

        return request_identity_error_page(
            validation_error,
            f"/request/{request_id}"
        )

    if request.method == "POST":

        new_arrival_date = request.form.get(
            "arrival_date"
        )

        new_departure_date = request.form.get(
            "departure_date"
        )

        rooms_requested = request.form.get(
            "rooms_requested"
        )

        change_notes = request.form.get(
            "change_notes"
        )

        try:

            arrival_obj = datetime.strptime(
                new_arrival_date,
                "%Y-%m-%d"
            )

            departure_obj = datetime.strptime(
                new_departure_date,
                "%Y-%m-%d"
            )

        except:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Change Request Not Submitted
            </h1>

            <p>
                Please enter valid arrival
                and departure dates.
            </p>

            <p>
                <a href="/request/{request_id}/change">
                    Back to Change Request
                </a>
            </p>
            """

        if departure_obj <= arrival_obj:

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Change Request Not Submitted
            </h1>

            <p>
                Departure date must be after
                arrival date.
            </p>

            <p>
                <a href="/request/{request_id}/change">
                    Back to Change Request
                </a>
            </p>
            """

        try:
            rooms_requested = int(
                rooms_requested
            )

        except:
            rooms_requested = 1

        if rooms_requested < 1:
            rooms_requested = 1

        if rooms_requested > 4:
            rooms_requested = 4

        old_comments = safe_text(
            request_row["comments"]
        ).strip()

        change_log_body = f"""
Original Arrival:
{request_row['arrival_date']}

Original Departure:
{request_row['departure_date']}

Original Rooms Requested:
{request_row['rooms_requested']}

Requested New Arrival:
{new_arrival_date}

Requested New Departure:
{new_departure_date}

Requested Rooms:
{rooms_requested}

Change Notes:
{change_notes}
"""

        change_log = timestamped_comment_block(
            "Change Request",
            change_log_body
        )

        if old_comments:

            updated_comments = (
                old_comments
                + "\n"
                + change_log
            )

        else:

            updated_comments = change_log

        conn.execute("""
            UPDATE booking_requests
            SET comments = ?,
                status = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            updated_comments,
            "change_requested",
            "not_needed",
            "",
            request_id
        ))

        conn.commit()

        conn.close()

        notify_admin(
            "Change request submitted",
            f"Guest: {safe_text(request_row['name'])}\nCurrent: {format_date(request_row['arrival_date'])} to {format_date(request_row['departure_date'])}\nRequested: {format_date(new_arrival_date)} to {format_date(new_departure_date)}\nRooms: {rooms_requested}",
            f"/request/{request_id}"
        )

        return f"""
        {nav_links()}

        <h1>Change Request Sent</h1>

        <div style="background-color: #fff3cd; border: 1px solid #fd7e14; padding: 12px 14px; border-radius: 8px; max-width: 760px; line-height: 1.4; margin-bottom: 12px;">
            <p style="font-weight: bold; margin-top: 0;">
                Got it — your change request has been sent.
            </p>
            <p style="margin-bottom: 0;">
                Your current approved stay has <strong>not</strong> changed yet.
                I’ll review the new dates and follow up. No chaos, no surprise room swaps.
            </p>
        </div>

        <h3>
            Current Reservation
        </h3>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; margin-bottom: 14px;">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Arrival</th>
                <th align="left">Departure</th>
                <th align="left">Rooms</th>
            </tr>
            <tr>
                <td>{format_date(request_row['arrival_date'])}</td>
                <td>{format_date(request_row['departure_date'])}</td>
                <td>{current_rooms}</td>
            </tr>
        </table>

        <h3>
            Requested Change
        </h3>

        <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px 12px; border-radius: 8px; max-width: 760px; margin-bottom: 12px;">
            <strong>Your requested change was recorded.</strong><br>
            The calendar check was used before submitting; this page is only confirming what was sent for review.
        </div>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; margin-bottom: 14px;">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Arrival</th>
                <th align="left">Departure</th>
                <th align="left">Rooms</th>
            </tr>
            <tr>
                <td><strong>{format_date(new_arrival_date)}</strong></td>
                <td><strong>{format_date(new_departure_date)}</strong></td>
                <td><strong>{rooms_requested}</strong></td>
            </tr>
        </table>

        <div style="background-color: #fff3cd; border: 1px solid #fd7e14; padding: 10px; border-radius: 6px; margin-bottom: 12px;">
            <strong>Your change request was saved.</strong><br>
            <small>I’ll review the new dates and send an update once it’s sorted out.</small>
        </div>

        <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px 12px; border-radius: 8px; max-width: 760px; margin-top: 10px;">
            <strong>What happens next?</strong><br>
            I’ll compare the current booking with the new request and send an update when it’s sorted out.
        </div>

        <p>
            <strong style="color: #198754;">Done</strong>
        </p>
        """

    selected_year = int(request.args.get("year", datetime.strptime(request_row["arrival_date"], "%Y-%m-%d").year))
    selected_month = int(request.args.get("month", datetime.strptime(request_row["arrival_date"], "%Y-%m-%d").month))

    if selected_month < 1:
        selected_month = 12
        selected_year -= 1

    if selected_month > 12:
        selected_month = 1
        selected_year += 1

    ensure_house_block_columns(conn)

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    bookings = conn.execute("""
        SELECT request_id, arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
          AND (request_id IS NULL OR request_id != ?)
    """, (
        request_id,
    )).fetchall()

    total_rooms = conn.execute(
        "SELECT COUNT(*) AS count FROM rooms"
    ).fetchone()["count"]

    tentative_holds = get_coordination_tentative_holds(conn)

    conn.close()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked,
        total_rooms
    )

    blocked_list = sorted(blocked_dates)

    first_day = date(selected_year, selected_month, 1)

    if selected_month == 12:
        next_month_date = date(selected_year + 1, 1, 1)
    else:
        next_month_date = date(selected_year, selected_month + 1, 1)

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:
        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:
        next_month = 1
        next_year += 1

    days_in_month = (next_month_date - first_day).days
    start_weekday = (first_day.weekday() + 1) % 7
    month_title = first_day.strftime("%B %Y")

    room_capacity = {}

    current = first_day

    while current < next_month_date:
        rooms_used = 0

        for booking in bookings:
            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            if booking_start <= current < booking_end:
                rooms_used += 1

        tentative_rooms_held = 0

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(
                    tentative_hold["arrival_date"],
                    "%Y-%m-%d"
                ).date()
                hold_end = datetime.strptime(
                    tentative_hold["departure_date"],
                    "%Y-%m-%d"
                ).date()
            except Exception:
                continue

            if hold_start <= current < hold_end:
                tentative_rooms_held += int(tentative_hold.get("rooms_held", 1) or 1)

        current_date_key = current.strftime("%Y-%m-%d")
        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            current_date_key,
            total_rooms
        )

        room_capacity[current_date_key] = max(
            0,
            capacity_limit - rooms_used - tentative_rooms_held
        )

        current += timedelta(days=1)

    calendar_html = f"""
    <h2 id="calendar-section">Availability Calendar - {month_title}</h2>

    <p>
        <a href="/request/{request_id}/change?year={previous_year}&month={previous_month}#calendar-section">
            Previous Month
        </a>
        |
        <strong>{month_title}</strong>
        |
        <a href="/request/{request_id}/change?year={next_year}&month={next_month}#calendar-section">
            Next Month
        </a>
    </p>

    <p style="max-width: 720px; color: #555;">
        Click an available arrival date, then click the departure date.
        The calendar checks blocked days, approved bookings, and coordination holds.
    </p>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 920px; font-size: 12px;">
        <tr style="background-color: #f5f5f5;">
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
        <tr>
    """

    for _ in range(start_weekday):
        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):
        current_date = date(selected_year, selected_month, day)
        current_date_str = current_date.strftime("%Y-%m-%d")

        today = date.today()
        past_date = current_date < today
        rooms_open = room_capacity.get(current_date_str, total_rooms)

        has_tentative_hold = False

        for tentative_hold in tentative_holds:
            try:
                hold_start = datetime.strptime(tentative_hold["arrival_date"], "%Y-%m-%d").date()
                hold_end = datetime.strptime(tentative_hold["departure_date"], "%Y-%m-%d").date()
            except Exception:
                continue

            if hold_start <= current_date < hold_end:
                has_tentative_hold = True
                break

        if past_date:
            background = "#e9ecef"
            display_line_1 = ""
            display_line_2 = "Past"
            click_handler = ""
            cursor = "not-allowed"
        elif current_date_str in blocked_dates:
            background = "#f8d7da"
            display_line_1 = ""
            display_line_2 = "Blocked"
            click_handler = ""
            cursor = "not-allowed"
        elif rooms_open <= 0:
            background = "#f8d7da"
            display_line_1 = "0 open"
            display_line_2 = "Full"
            click_handler = ""
            cursor = "not-allowed"
        elif has_tentative_hold:
            background = "#cfe8ff"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Hold"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"
        elif rooms_open <= 2:
            background = "#fff3cd"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Almost Full"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"
        else:
            background = "#d4edda"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Open"
            click_handler = f"onclick=\"selectCalendarDate('{current_date_str}')\""
            cursor = "pointer"

        calendar_html += f"""
        <td {click_handler}
            data-date="{current_date_str}"
            data-rooms-open="{rooms_open}"
            style="background-color: {background}; cursor: {cursor}; vertical-align: top; height: 62px; min-width: 90px; padding: 4px;">
            <strong>{day}</strong><br>
            <span style="font-size: 10px; font-weight: normal;">
                {str(rooms_open) + " ROOM" + ("" if rooms_open == 1 else "S") + " OPEN" if (not past_date and current_date_str not in blocked_dates and rooms_open > 0) else ("FULL" if (not past_date and current_date_str not in blocked_dates and rooms_open <= 0) else "")}
            </span><br>
            <span style="font-size: 10px;">{display_line_2}</span>
        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0:
            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:
        calendar_html += "<td></td>"
        day_counter += 1

    calendar_html += """
        </tr>
    </table>
    """

    html = nav_links() + f"""

    <h1>
        Request a Change
    </h1>

    <p>
        Use this form if your plans
        changed and you need different
        dates or room counts reviewed.
    </p>

    <p>
        <strong>
            Important:
        </strong>

        Submitting this form does NOT
        automatically change your
        approved stay.
    </p>

    <hr>

    <div style="display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; max-width: 1180px;">

        <div style="flex: 0 0 285px; background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 10px 12px; line-height: 1.25; font-size: 13px;">
            <h3 style="margin: 0 0 8px 0; font-size: 16px;">
                Current Approved Details
            </h3>
            <div><strong>Guest:</strong> {request_row['name']}</div>
            <div><strong>Email:</strong> {request_row['email']}</div>
            <div><strong>Arrival:</strong> {format_date(request_row['arrival_date'])}</div>
            <div><strong>Departure:</strong> {format_date(request_row['departure_date'])}</div>
            <div><strong>Rooms:</strong> {current_rooms}</div>
        </div>

        <div style="flex: 1 1 650px; min-width: 320px;">

    <form method="POST" onsubmit="return checkUnavailableDates();">

        <h3>
            Requested Change
        </h3>

        <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px 12px; border-radius: 8px; max-width: 760px; margin-bottom: 12px;">
            <strong>Select the number of bedrooms first, then choose dates on the calendar.<br>Each bedroom sleeps up to 2 guests.</strong><br>
            This shows blocked dates, full dates, and coordination holds so you are not guessing.
        </div>

        <label>
            Rooms Requested
        </label><br>

        <select id="rooms_requested"
                name="rooms_requested"
                style="
                    width: 160px;
                    padding: 8px;
                    margin-bottom: 12px;
                ">
    """

    for room_count in range(1, 5):

        selected = ""

        if int(current_rooms) == room_count:
            selected = "selected"

        html += f"""
            <option value="{room_count}" {selected}>
                {room_count} Room{"s" if room_count != 1 else ""}
            </option>
        """

    html += f"""

        </select>

        <br>

        {calendar_html}

        <p id="date_selection_message" style="font-weight: bold; color: #0d6efd;">
            Click an arrival date on the calendar.
        </p>
        <p id="nights_message" style="color: #555;"></p>

        <label>
            New Arrival Date
        </label><br>

        <input type="date"
               id="arrival_date"
               name="arrival_date"
               value="{request_row['arrival_date']}"
               required
               style="
                   padding: 8px;
               ">

        <br>

        <label>
            New Departure Date
        </label><br>

        <input type="date"
               id="departure_date"
               name="departure_date"
               value="{request_row['departure_date']}"
               required
               style="
                   padding: 8px;
               ">

        <br>

        <label>
            Notes About This Change
        </label><br>

        <textarea name="change_notes"
                  rows="5"
                  style="
                      width: 520px;
                      padding: 8px;
                  "
                  placeholder="Example: arriving one day later or reducing room count."></textarea>

        <br>

        <button type="submit"
                style="
                    padding: 10px 18px;
                    font-weight: bold;
                ">

            Submit Change Request

        </button>

    </form>

        </div>
    </div>

    <script>
        const blockedDates = {blocked_list};
        const roomCapacity = {room_capacity};
        const totalRooms = {total_rooms};

        let nextDateField = "arrival";
        let selectedArrivalCell = null;
        let selectedDepartureCell = null;

        function getRequestedRooms() {{
            return parseInt(document.getElementById("rooms_requested").value);
        }}

        function formatDateForMessage(dateString) {{
            const parts = dateString.split("-");
            return parts[1] + "/" + parts[2] + "/" + parts[0];
        }}

        function getRoomsOpen(dateString) {{
            if (roomCapacity[dateString] === undefined) {{
                return totalRooms;
            }}
            return roomCapacity[dateString];
        }}

        function clearSelectedCellColors() {{
            if (selectedArrivalCell) {{
                selectedArrivalCell.style.outline = "";
                selectedArrivalCell.style.backgroundColor = selectedArrivalCell.dataset.originalColor;
            }}
            if (selectedDepartureCell) {{
                selectedDepartureCell.style.outline = "";
                selectedDepartureCell.style.backgroundColor = selectedDepartureCell.dataset.originalColor;
            }}
            selectedArrivalCell = null;
            selectedDepartureCell = null;
        }}

        function updateNightsMessage() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const nightsMessage = document.getElementById("nights_message");

            if (!arrival || !departure || departure <= arrival) {{
                nightsMessage.innerText = "";
                return;
            }}

            const arrivalDate = new Date(arrival + "T00:00:00");
            const departureDate = new Date(departure + "T00:00:00");
            const nights = Math.round((departureDate - arrivalDate) / (1000 * 60 * 60 * 24));

            nightsMessage.innerText =
                "Requested change: "
                + nights
                + " night"
                + (nights === 1 ? "" : "s")
                + " / "
                + getRequestedRooms()
                + " bedroom"
                + (getRequestedRooms() === 1 ? "" : "s");
        }}

        function selectCalendarDate(dateString) {{
            const requestedRooms = getRequestedRooms();
            const roomsOpen = getRoomsOpen(dateString);

            if (blockedDates.includes(dateString)) {{
                alert(formatDateForMessage(dateString) + " is blocked.");
                return;
            }}

            if (roomsOpen < requestedRooms) {{
                alert("Only " + roomsOpen + " bedroom(s) available on " + formatDateForMessage(dateString));
                return;
            }}

            const clickedCell = document.querySelector('[data-date="' + dateString + '"]');

            if (clickedCell && !clickedCell.dataset.originalColor) {{
                clickedCell.dataset.originalColor = clickedCell.style.backgroundColor;
            }}

            const arrivalField = document.getElementById("arrival_date");
            const departureField = document.getElementById("departure_date");
            const message = document.getElementById("date_selection_message");

            if (nextDateField === "arrival") {{
                clearSelectedCellColors();
                arrivalField.value = dateString;
                departureField.value = "";
                nextDateField = "departure";

                if (clickedCell) {{
                    selectedArrivalCell = clickedCell;
                    clickedCell.style.backgroundColor = "#9ec5fe";
                    clickedCell.style.outline = "3px solid #0d6efd";
                }}

                message.innerText = "Arrival selected: " + formatDateForMessage(dateString) + ". Now click a departure date.";
                updateNightsMessage();
            }} else {{
                if (dateString <= arrivalField.value) {{
                    alert("Departure date must be after arrival date.");
                    return;
                }}

                departureField.value = dateString;
                nextDateField = "arrival";

                if (clickedCell) {{
                    selectedDepartureCell = clickedCell;
                    clickedCell.style.backgroundColor = "#b6d7a8";
                    clickedCell.style.outline = "3px solid #198754";
                }}

                message.innerText = "Selected stay: " + formatDateForMessage(arrivalField.value) + " to " + formatDateForMessage(dateString) + ".";
                updateNightsMessage();
            }}
        }}

        document.getElementById("rooms_requested").addEventListener("change", function () {{
            document.getElementById("arrival_date").value = "";
            document.getElementById("departure_date").value = "";
            document.getElementById("date_selection_message").innerText = "Click an arrival date on the calendar.";
            document.getElementById("nights_message").innerText = "";
            clearSelectedCellColors();
            nextDateField = "arrival";
        }});

        function preserveCalendarNavigationSelection() {{
            document.querySelectorAll('[data-calendar-nav="1"]').forEach(function (link) {{
                link.addEventListener("click", function () {{
                    const arrival = document.getElementById("arrival_date").value;
                    const departure = document.getElementById("departure_date").value;
                    const url = new URL(link.href, window.location.origin);

                    if (arrival) {{
                        url.searchParams.set("arrival_date", arrival);
                    }}

                    if (departure) {{
                        url.searchParams.set("departure_date", departure);
                    }}

                    url.searchParams.set("next_date_field", nextDateField || "arrival");
                    link.href = url.toString();
                }});
            }});
        }}

        function checkUnavailableDates() {{
            const arrival = document.getElementById("arrival_date").value;
            const departure = document.getElementById("departure_date").value;
            const requestedRooms = getRequestedRooms();

            if (!arrival || !departure) {{
                alert("Please select both an arrival date and a departure date from the calendar.");
                return false;
            }}

            if (departure <= arrival) {{
                alert("Departure date must be after arrival date.");
                return false;
            }}

            let current = new Date(arrival + "T00:00:00");
            const end = new Date(departure + "T00:00:00");

            while (current < end) {{
                const dateString = current.toISOString().slice(0, 10);

                if (blockedDates.includes(dateString)) {{
                    alert(formatDateForMessage(dateString) + " is blocked.");
                    return false;
                }}

                const roomsOpen = getRoomsOpen(dateString);
                if (roomsOpen < requestedRooms) {{
                    alert("Only " + roomsOpen + " bedroom(s) available on " + formatDateForMessage(dateString));
                    return false;
                }}

                current.setDate(current.getDate() + 1);
            }}

            return true;
        }}

        updateNightsMessage();
    </script>

        </div>
    </div>

    <br>

    <p>
        <strong style="color: #198754;">Done</strong>
    </p>
    """

    return html



@app.route("/request/<int:request_id>/cancel/", methods=["GET", "POST"])
@app.route("/request/<int:request_id>/cancel", methods=["GET", "POST"])
def cancel_request(request_id):

    conn = get_db_connection()

    request_row = conn.execute("""
        SELECT *
        FROM booking_requests
        WHERE id = ?
    """, (
        request_id,
    )).fetchone()

    if not request_row:

        conn.close()

        return """
        <h2>Request not found.</h2>

        <p>
            <a href="/requests">
                Back to requests
            </a>
        </p>
        """

    current_rooms = request_row["rooms_requested"]

    if not current_rooms:
        current_rooms = 1

    validation_error = request_identity_validation_error(
        request_row["name"],
        request_row["email"]
    )

    if validation_error:

        conn.close()

        return request_identity_error_page(
            validation_error,
            f"/request/{request_id}"
        )

    rooms_requested = request_row["rooms_requested"]

    if not rooms_requested:
        rooms_requested = 1

    new_request_link = repeat_visit_request_url_for_row(request_row)

    if request.method == "POST":

        cancel_reason = request.form.get(
            "cancel_reason"
        )

        confirm_cancellation = request.form.get(
            "confirm_cancellation"
        )

        if confirm_cancellation != "yes":

            conn.close()

            return f"""
            {nav_links()}

            <h1>
                Confirm Cancellation
            </h1>

            <div style="
                background-color: #fff3cd;
                border: 2px solid #fd7e14;
                padding: 14px;
                border-radius: 8px;
                max-width: 760px;
                margin-bottom: 18px;
            ">
                <p style="font-weight: bold; margin-top: 0;">
                    This cancellation cannot be undone.
                </p>

                <p>
                    Your confirmed dates and assigned room space will be released immediately.
                </p>

                <p>
                    If you wish to visit on different dates, you can submit a new request after cancellation.
                </p>
            </div>

            <h3>Visit Being Cancelled</h3>

            <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; margin-bottom: 14px;">
                <tr style="background-color: #f5f5f5;">
                    <th align="left">Guest</th>
                    <th align="left">Arrival</th>
                    <th align="left">Departure</th>
                    <th align="left">Rooms</th>
                </tr>
                <tr>
                    <td>{request_row['name']}</td>
                    <td>{format_date(request_row['arrival_date'])}</td>
                    <td>{format_date(request_row['departure_date'])}</td>
                    <td>{rooms_requested}</td>
                </tr>
            </table>

            <form method="POST">
                <input type="hidden" name="cancel_reason" value="{safe_text(cancel_reason)}">
                <input type="hidden" name="confirm_cancellation" value="yes">

                <button type="submit"
                        style="
                            padding: 10px 18px;
                            font-weight: bold;
                            background-color: #b22222;
                            color: white;
                            border: none;
                            border-radius: 4px;
                        ">
                    Confirm Cancellation
                </button>

                &nbsp;

                <a href="/request/{request_id}">
                    Return to Reservation
                </a>
            </form>
            """

        old_comments = safe_text(
            request_row["comments"]
        ).strip()

        cancellation_log_body = f"""
Cancelled By:
Guest / Request Link

Original Arrival:
{request_row['arrival_date']}

Original Departure:
{request_row['departure_date']}

Cancellation Reason:
{cancel_reason}
"""

        cancellation_log = timestamped_comment_block(
            "Guest Cancellation",
            cancellation_log_body
        )

        if old_comments:

            updated_comments = (
                old_comments
                + "\n"
                + cancellation_log
            )

        else:

            updated_comments = cancellation_log

        backup_path = create_database_backup(
            "before_guest_confirmed_cancel"
        )

        try:

            conn.execute("BEGIN")

            conn.execute("""
                UPDATE booking_requests
                SET status = ?,
                    comments = ?,
                    email_status = ?,
                    email_needed_type = ?
                WHERE id = ?
            """, (
                "cancelled",
                updated_comments,
                "not_needed",
                "",
                request_id
            ))

            conn.execute("""
                DELETE FROM bookings
                WHERE request_id = ?
            """, (
                request_id,
            ))

            write_activity_log(
                conn,
                request_id,
                "guest_cancelled",
                request_row["status"],
                "cancelled",
                f"Guest confirmed cancellation. Assigned bookings released. Backup: {backup_path}"
            )

            conn.commit()

        except Exception as error:

            rollback_and_close(conn)

            return transaction_error_page(
                error,
                f"/request/{request_id}"
            )

        recipient_email = resolve_request_recipient_email(
            conn,
            request_row
        )

        email_send_error = ""

        if recipient_email:

            try:

                nights = date_range_nights(
                    request_row["arrival_date"],
                    request_row["departure_date"]
                )

                cancellation_body = render_email_template(
                    "cancellation.txt",
                    guest_name=safe_text(request_row["name"]),
                    arrival_date=format_date(request_row["arrival_date"]),
                    departure_date=format_date(request_row["departure_date"]),
                    nights=safe_text(nights),
                    rooms_requested=safe_text(rooms_requested),
                    additional_names=safe_text(row_value(request_row, "additional_names")) or "None listed",
                    request_link=repeat_visit_request_url_for_row(request_row),
                    new_request_link=repeat_visit_request_url_for_row(request_row)
                )

                send_email(
                    recipient_email,
                    "Your Strathmere Visit Cancellation",
                    cancellation_body
                )

                conn.execute("""
                    UPDATE booking_requests
                    SET email_status = ?,
                        email_needed_type = ?
                    WHERE id = ?
                """, (
                    "sent",
                    "cancellation",
                    request_id
                ))

                conn.commit()

            except Exception as error:

                email_send_error = safe_text(error)

                conn.execute("""
                    UPDATE booking_requests
                    SET email_status = ?,
                        email_needed_type = ?
                    WHERE id = ?
                """, (
                    "needs_email",
                    "cancellation",
                    request_id
                ))

                conn.commit()

        conn.close()

        notify_admin(
            "Cancellation completed",
            f"Guest: {safe_text(request_row['name'])}\nCancelled stay: {format_date(request_row['arrival_date'])} to {format_date(request_row['departure_date'])}",
            f"/request/{request_id}"
        )

        email_status_message = "Cancellation confirmation email sent."

        if email_send_error:
            email_status_message = "Cancellation was processed, but the confirmation email still needs to be sent by admin."

        return f"""
        {nav_links()}

        <h1>
            Visit Cancelled
        </h1>

        <p>
            Your visit has been cancelled and the room space has been released.
        </p>

        <p>
            {email_status_message}
        </p>

        <h3>Cancelled Visit</h3>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; margin-bottom: 14px;">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Arrival</th>
                <th align="left">Departure</th>
                <th align="left">Rooms</th>
            </tr>
            <tr>
                <td>{format_date(request_row['arrival_date'])}</td>
                <td>{format_date(request_row['departure_date'])}</td>
                <td>{rooms_requested}</td>
            </tr>
        </table>

        <p>
            <a href="{new_request_link}">
                Submit New Request
            </a>
        </p>
        """

    conn.close()

    html = nav_links() + f"""

    <h1>
        Request Cancellation
    </h1>

    <p>
        Review the visit below before continuing to the final cancellation confirmation.
    </p>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; margin-bottom: 14px;">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Guest</th>
            <th align="left">Email</th>
            <th align="left">Arrival</th>
            <th align="left">Departure</th>
            <th align="left">Rooms</th>
        </tr>
        <tr>
            <td>{request_row['name']}</td>
            <td>{request_row['email']}</td>
            <td>{format_date(request_row['arrival_date'])}</td>
            <td>{format_date(request_row['departure_date'])}</td>
            <td>{rooms_requested}</td>
        </tr>
    </table>

    <form method="POST">

        <label>
            Optional Cancellation Notes
        </label><br>

        <textarea name="cancel_reason"
                  rows="5"
                  style="
                      width: 520px;
                      padding: 8px;
                  "
                  placeholder="Optional reason for cancellation."></textarea>

        <br>

        <button type="submit"
                style="
                    padding: 10px 18px;
                    font-weight: bold;
                    background-color: #fd7e14;
                    color: white;
                    border: none;
                    border-radius: 4px;
                ">
            Continue to Cancellation Confirmation
        </button>

    </form>

    <br>

    <p>
        <strong style="color: #198754;">Done</strong>
    </p>
    """

    return html


@app.route("/coordination-groups")
def coordination_groups():

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    groups = conn.execute("""
        SELECT
            coordination_groups.*,
            COUNT(coordination_group_members.id) AS member_count
        FROM coordination_groups
        LEFT JOIN coordination_group_members
            ON coordination_groups.id = coordination_group_members.coordination_group_id
        GROUP BY coordination_groups.id
        ORDER BY
            coordination_groups.created_at DESC,
            coordination_groups.title
    """).fetchall()

    conn.commit()
    conn.close()

    html = nav_links() + """
    <h1>Coordination Groups</h1>

    <p>
        Use coordination groups for shared visit planning before
        individual stays are approved.
    </p>

    <p>
        <a href="/coordination-group/new"
           style="
               font-weight: bold;
           ">
            Create New Coordination Group
        </a>
    </p>
    """

    if not groups:

        html += """
        <p>No coordination groups yet.</p>
        """

    else:

        html += """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">

            <tr style="background-color: #f5f5f5;">
                <th align="left">Group</th>
                <th align="left">Year</th>
                <th align="left">Status</th>
                <th align="left">Final Email</th>
                <th align="center">Guests</th>
                <th align="left">Created</th>
                <th align="left">View</th>
            </tr>
        """

        for group in groups:

            status_display = safe_text(group["status"])

            if status_display == "confirmed_coordination":
                status_display = "Confirmed Coordination"
            elif status_display in ["finalized", "closed"]:
                status_display = "Closed / Finalized"
            elif status_display == "tentative":
                status_display = "Tentative"
            elif status_display == "planning":
                status_display = "Planning"

            final_email_display = "Not Sent"

            if safe_text(group["final_visit_confirmation_sent_at"]):

                final_email_display = (
                    "Final Visit Sent "
                    + safe_text(group["final_visit_confirmation_sent_at"])[:10]
                )

            elif safe_text(group["final_coordination_email_sent_at"]):

                final_email_display = (
                    "Final Coordination Sent "
                    + safe_text(group["final_coordination_email_sent_at"])[:10]
                )

            html += f"""
            <tr>
                <td>
                    <strong>{safe_text(group['title'])}</strong><br>
                    <small>{safe_text(group['description'])}</small>
                </td>
                <td>{safe_text(group['target_year'])}</td>
                <td>{status_display}</td>
                <td>{final_email_display}</td>
                <td align="center">{group['member_count']}</td>
                <td>{safe_text(group['created_at'])[:10]}</td>
                <td>
                    <a href="/coordination-group/{group['id']}">
                        View
                    </a>
                </td>
            </tr>
            """

        html += "</table>"

    return html


@app.route("/coordination-group/new", methods=["GET", "POST"])
def coordination_group_new():

    if request.method == "POST":

        title = clean_text(
            request.form.get("title")
        )

        description = clean_text(
            request.form.get("description")
        )

        target_year = clean_text(
            request.form.get("target_year")
        )

        status = clean_text(
            request.form.get("status")
        )

        organizer_guest_profile_id = clean_text(
            request.form.get("organizer_guest_profile_id")
        )

        organizer_admin_message = clean_text(
            request.form.get("organizer_admin_message")
        )

        organizer_suggested_guests = clean_text(
            request.form.get("organizer_suggested_guests")
        )

        if not title:

            return f"""
            {nav_links()}

            <h1>Coordination Group Not Saved</h1>

            <p style="
                color: red;
                font-weight: bold;
            ">
                Coordination group title is required.
            </p>

            <p>
                <a href="/coordination-group/new">
                    Back
                </a>
            </p>
            """

        try:
            target_year_value = int(target_year)
        except:
            target_year_value = date.today().year

        if not status:
            status = "forming"

        conn = get_db_connection()

        ensure_coordination_tables(conn)

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO coordination_groups
            (title, description, target_year, status)
            VALUES (?, ?, ?, ?)
        """, (
            title,
            description,
            target_year_value,
            status
        ))

        group_id = cursor.lastrowid

        try:
            organizer_profile_id_value = int(organizer_guest_profile_id)
        except Exception:
            organizer_profile_id_value = None

        if organizer_profile_id_value:

            organizer_profile = conn.execute("""
                SELECT *
                FROM guest_profiles
                WHERE id = ?
            """, (
                organizer_profile_id_value,
            )).fetchone()

            if organizer_profile:

                cursor.execute("""
                    INSERT INTO coordination_group_members
                    (coordination_group_id, guest_profile_id, role, invitation_status, organizer_suggested_guests, organizer_suggested_dates_notes)
                    VALUES (?, ?, 'organizer', 'draft', ?, ?)
                """, (
                    group_id,
                    organizer_profile_id_value,
                    organizer_suggested_guests,
                    organizer_admin_message
                ))

        conn.commit()
        conn.close()

        return redirect(
            f"/coordination-group/{group_id}"
        )

    current_year = date.today().year

    conn = get_db_connection()

    available_profiles = conn.execute("""
        SELECT id, primary_name, primary_email, status
        FROM guest_profiles
        ORDER BY primary_name, primary_email
    """).fetchall()

    conn.close()

    organizer_options_html = ""

    for profile in available_profiles:

        organizer_options_html += f"""
            <option value="{profile['id']}">
                {safe_text(profile['primary_name'])} — {safe_text(profile['primary_email'])} ({safe_text(profile['status'])})
            </option>
        """

    html = nav_links() + f"""
    <h1>Create Coordination Group</h1>

    <p>
        Start here for a shared visit, family weekend,
        or any group where dates need to be coordinated
        before individual bookings are approved.
    </p>

    <form method="POST">

        <label>
            <strong>Group Title</strong>
        </label><br>

        <input type="text"
               name="title"
               required
               style="width: 420px;">

        <br>

        <label>
            <strong>Description / Notes</strong>
        </label><br>

        <textarea name="description"
                  rows="2"
                  style="width: 520px;"></textarea>

        <br>

        <div style="border:1px solid #b6d4fe; background:#eef5ff; padding:12px; border-radius:8px; max-width:760px; margin:10px 0;">
            <h3 style="margin-top:0;">Organizer Formation</h3>
            <p style="font-size:13px; margin-top:0;">
                Phase 2: choose the organizer first. The organizer can suggest group members and initial dates before the rest of the group is invited.
            </p>

            <label>
                <strong>Organizer</strong>
            </label><br>
            <select name="organizer_guest_profile_id" style="width: 420px;">
                <option value="">No organizer yet</option>
                {organizer_options_html}
            </select>

            <br><br>

            <label>
                <strong>Admin Message to Organizer</strong>
            </label><br>
            <textarea name="organizer_admin_message" rows="3" style="width:520px;" placeholder="Example: We are starting a family visit group. Please suggest who should be included and your preferred dates."></textarea>

            <br><br>

            <label>
                <strong>Possible Guests / Notes</strong>
            </label><br>
            <textarea name="organizer_suggested_guests" rows="3" style="width:520px;" placeholder="Example: Kevin, Eric, Judy"></textarea>
        </div>

        <label>
            <strong>Target Year</strong>
        </label><br>

        <input type="number"
               name="target_year"
               value="{current_year}"
               style="width: 120px;">

        <br>

        <label>
            <strong>Status</strong>
        </label><br>

        <select name="status">
            <option value="forming" selected>Forming / Organizer Email Sent / Organizer Setup Returned</option>
            <option value="planning">Planning</option>
            <option value="collecting_dates">Collecting Dates</option>
            <option value="tentative">Tentative</option>
            <option value="finalized">Finalized</option>
            <option value="archived">Archived</option>
        </select>

        <br>

        <button type="submit">
            Create Coordination Group
        </button>

    </form>

    <p>
        <a href="/coordination-groups">
            Back to Coordination Groups
        </a>
    </p>
    """

    return html


@app.route("/coordination-group/<int:group_id>")
def coordination_group_detail(group_id):

    # V34.2 Planning View hard fallback: never crash the admin View page.
    try:

        conn = get_db_connection()

        ensure_coordination_tables(conn)

        group = conn.execute("""
            SELECT *
            FROM coordination_groups
            WHERE id = ?
        """, (
            group_id,
        )).fetchone()

        if not group:

            conn.close()

            return f"""
            {nav_links()}

            <h1>Coordination Group Not Found</h1>

            <p>
                The coordination group could not be found.
            </p>

            <p>
                <a href="/coordination-groups">
                    Back to Coordination Groups
                </a>
            </p>
            """

        try:
            members = conn.execute("""
                SELECT
                    coordination_group_members.*,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    COUNT(coordination_date_options.id) AS date_option_count,
                    MAX(COALESCE(coordination_date_options.rooms_requested, 1)) AS rooms_requested
                FROM coordination_group_members
                JOIN guest_profiles
                    ON coordination_group_members.guest_profile_id = guest_profiles.id
                LEFT JOIN coordination_date_options
                    ON coordination_group_members.id = coordination_date_options.coordination_group_member_id
                WHERE coordination_group_members.coordination_group_id = ?
                GROUP BY coordination_group_members.id
                ORDER BY
                    guest_profiles.primary_name
            """, (
                group_id,
            )).fetchall()
        except Exception:
            members = conn.execute("""
                SELECT
                    coordination_group_members.*,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    COUNT(coordination_date_options.id) AS date_option_count,
                    1 AS rooms_requested
                FROM coordination_group_members
                JOIN guest_profiles
                    ON coordination_group_members.guest_profile_id = guest_profiles.id
                LEFT JOIN coordination_date_options
                    ON coordination_group_members.id = coordination_date_options.coordination_group_member_id
                WHERE coordination_group_members.coordination_group_id = ?
                GROUP BY coordination_group_members.id
                ORDER BY
                    guest_profiles.primary_name
            """, (
                group_id,
            )).fetchall()

        available_profiles = conn.execute("""
            SELECT
                guest_profiles.id,
                guest_profiles.primary_name,
                guest_profiles.primary_email,
                guest_profiles.status
            FROM guest_profiles
            WHERE guest_profiles.id NOT IN (
                SELECT guest_profile_id
                FROM coordination_group_members
                WHERE coordination_group_id = ?
            )
            ORDER BY
                guest_profiles.primary_name,
                guest_profiles.primary_email
        """, (
            group_id,
        )).fetchall()

        group_date_options = conn.execute("""
            SELECT
                coordination_date_options.*,
                coordination_group_members.id AS member_id,
                coordination_group_members.invitation_status,
                coordination_group_members.role,
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM coordination_date_options
            JOIN coordination_group_members
                ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.coordination_group_id = ?
            ORDER BY
                coordination_date_options.arrival_date,
                coordination_date_options.departure_date,
                CASE coordination_date_options.priority
                    WHEN 'preferred' THEN 1
                    WHEN 'alternate' THEN 2
                    ELSE 3
                END,
                guest_profiles.primary_name
        """, (
            group_id,
        )).fetchall()

        approved_bookings_for_matching = conn.execute("""
            SELECT arrival_date, departure_date
            FROM bookings
            WHERE status = 'approved'
        """).fetchall()

        blocked_ranges_for_matching = conn.execute("""
            SELECT *
            FROM blocked_dates
            ORDER BY start_date
        """).fetchall()

        total_rooms_for_matching = conn.execute("""
            SELECT COUNT(*) AS count
            FROM rooms
        """).fetchone()["count"]

        tentative_holds_for_matching = get_coordination_tentative_holds(
            conn,
            exclude_group_id=group_id,
            expand_rooms=True
        )

        match_suggestions = build_coordination_match_suggestions(
            group_date_options,
            list(approved_bookings_for_matching) + tentative_holds_for_matching,
            blocked_ranges_for_matching,
            total_rooms_for_matching
        )

        intersection_suggestions = build_coordination_intersection_suggestions(
            group_date_options,
            list(approved_bookings_for_matching) + tentative_holds_for_matching,
            blocked_ranges_for_matching,
            total_rooms_for_matching
        )

        tentative_adjustments = conn.execute("""
            SELECT *
            FROM coordination_tentative_adjustments
            WHERE coordination_group_id = ?
            ORDER BY created_at DESC
            LIMIT 5
        """, (
            group_id,
        )).fetchall()

        try:
            created_booking_request_rows = conn.execute("""
                SELECT
                    coordination_group_members.id AS member_id,
                    coordination_group_members.converted_request_id,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    booking_requests.status AS request_status,
                    booking_requests.email_status,
                    booking_requests.email_needed_type,
                    booking_requests.additional_names,
                    booking_requests.arrival_date,
                    booking_requests.departure_date,
                    booking_requests.rooms_requested,
                    COUNT(bookings.id) AS approved_booking_count,
                    GROUP_CONCAT(rooms.name, ', ') AS approved_room_names
                FROM coordination_group_members

                JOIN guest_profiles
                    ON coordination_group_members.guest_profile_id = guest_profiles.id

                LEFT JOIN booking_requests
                    ON coordination_group_members.converted_request_id = booking_requests.id

                LEFT JOIN bookings
                    ON booking_requests.id = bookings.request_id
                   AND bookings.status = 'approved'

                LEFT JOIN rooms
                    ON bookings.room_id = rooms.id

                WHERE coordination_group_members.coordination_group_id = ?
                  AND coordination_group_members.converted_request_id IS NOT NULL

                GROUP BY
                    coordination_group_members.id,
                    coordination_group_members.converted_request_id,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    booking_requests.status,
                    booking_requests.email_status,
                    booking_requests.email_needed_type,
                    booking_requests.additional_names,
                    booking_requests.arrival_date,
                    booking_requests.departure_date,
                    booking_requests.rooms_requested

                ORDER BY guest_profiles.primary_name
            """, (
                group_id,
            )).fetchall()

        except Exception:
            # Fallback for live databases missing newer email-status columns.
            # Keeps the Planning/View page loading after Booking Handoff.
            created_booking_request_rows = conn.execute("""
                SELECT
                    coordination_group_members.id AS member_id,
                    coordination_group_members.converted_request_id,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    booking_requests.status AS request_status,
                    '' AS email_status,
                    '' AS email_needed_type,
                    booking_requests.additional_names,
                    booking_requests.arrival_date,
                    booking_requests.departure_date,
                    booking_requests.rooms_requested,
                    COUNT(bookings.id) AS approved_booking_count,
                    GROUP_CONCAT(rooms.name, ', ') AS approved_room_names
                FROM coordination_group_members

                JOIN guest_profiles
                    ON coordination_group_members.guest_profile_id = guest_profiles.id

                LEFT JOIN booking_requests
                    ON coordination_group_members.converted_request_id = booking_requests.id

                LEFT JOIN bookings
                    ON booking_requests.id = bookings.request_id
                   AND bookings.status = 'approved'

                LEFT JOIN rooms
                    ON bookings.room_id = rooms.id

                WHERE coordination_group_members.coordination_group_id = ?
                  AND coordination_group_members.converted_request_id IS NOT NULL

                GROUP BY
                    coordination_group_members.id,
                    coordination_group_members.converted_request_id,
                    guest_profiles.primary_name,
                    guest_profiles.primary_email,
                    booking_requests.status,
                    booking_requests.additional_names,
                    booking_requests.arrival_date,
                    booking_requests.departure_date,
                    booking_requests.rooms_requested

                ORDER BY guest_profiles.primary_name
            """, (
                group_id,
            )).fetchall()

        conn.commit()

        group_room_demand_by_member = {}

        for option in group_date_options:

            member_key = option["member_id"]
            option_rooms = normalize_rooms_requested(
                option["rooms_requested"],
                total_rooms_for_matching
            )

            if member_key not in group_room_demand_by_member:
                group_room_demand_by_member[member_key] = option_rooms
            elif option_rooms > group_room_demand_by_member[member_key]:
                group_room_demand_by_member[member_key] = option_rooms

        total_group_rooms_requested = sum(group_room_demand_by_member.values())

        capacity_warning_html = ""

        if total_group_rooms_requested > total_rooms_for_matching:

            guest_room_rows = ""

            for option in group_date_options:

                if option["member_id"] in group_room_demand_by_member:

                    guest_room_rows += f"""
                    <tr>
                        <td>{safe_text(option['primary_name'])}</td>
                        <td align="center">{group_room_demand_by_member[option['member_id']]}</td>
                    </tr>
                    """

                    del group_room_demand_by_member[option["member_id"]]

            capacity_warning_html = f"""
            <div style="
                background-color: #f8d7da;
                border: 2px solid #dc3545;
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 10px;
                max-width: 900px;
            ">
                <h3 style="margin-top: 0; color: #842029;">Room Capacity Warning</h3>
                <p>
                    <strong>Maximum rooms available:</strong> {total_rooms_for_matching}<br>
                    <strong>Rooms requested by group:</strong> {total_group_rooms_requested}
                </p>
                <p>
                    The group is requesting more rooms than the house has available.
                    No single date option can work for the full group until rooms are reduced,
                    guests are split into separate visits, or the group plan changes.
                </p>
                <table border="1" cellpadding="4" cellspacing="0" style="border-collapse: collapse; font-size: 13px;">
                    <tr style="background-color: #f5f5f5;">
                        <th align="left">Guest</th>
                        <th align="center">Rooms Requested</th>
                    </tr>
                    {guest_room_rows}
                </table>
            </div>
            """

        tentative_dates_html = """
        <p>No tentative group dates selected yet.</p>
        """

        tentative_confirmation_html = """
        <p>No tentative confirmation responses yet.</p>
        """

        tentative_confirmed_count = 0
        tentative_cannot_count = 0
        tentative_discussion_count = 0
        tentative_no_response_count = 0

        all_confirmed_banner = ""

        if safe_text(group["tentative_arrival_date"]) and safe_text(group["tentative_departure_date"]):

            tentative_dates_html = f"""
            <div style="
                border: 2px solid #198754;
                background-color: #e8f7ea;
                padding: 12px;
                margin-bottom: 14px;
                border-radius: 8px;
                max-width: 720px;
            ">
                <h3 style="margin-top: 0;">
                    Tentative Dates That May Work for Everyone
                </h3>

                <p style="font-size: 16px; margin-bottom: 4px;">
                    <strong>{format_date(group['tentative_arrival_date'])}</strong>
                    to
                    <strong>{format_date(group['tentative_departure_date'])}</strong>
                </p>

                <small>
                    Selected: {safe_text(group['tentative_selected_at'])}<br>
                    Calendar status: Tentative Coordination Hold is active until the group is closed, canceled, or tentative dates are changed.
                </small>
            </div>
            """

            response_rows = []

            for member in members:

                response_status = safe_text(
                    member["tentative_response_status"]
                )

                if response_status == "confirmed":
                    tentative_confirmed_count += 1
                elif response_status == "cannot_make":
                    tentative_cannot_count += 1
                elif response_status == "needs_discussion":
                    tentative_discussion_count += 1
                else:
                    tentative_no_response_count += 1

                response_rows.append(f"""
                <tr style="background-color: {tentative_response_color(response_status)};">
                    <td>{safe_text(member['primary_name'])}</td>
                    <td>{coordination_role_badge(member['role'])}</td>
                    <td>{safe_text(member['primary_email'])}</td>
                    <td>{tentative_response_display(response_status)}</td>
                    <td>{safe_text(member['tentative_response_at'])}</td>
                    <td>{safe_text(member['tentative_response_notes'])}</td>
                </tr>
                """)

            tentative_confirmation_html = f"""
            <table border="1"
                   cellpadding="5"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                       margin-top: 8px;
                       margin-bottom: 18px;
                   ">
                <tr style="background-color: #f5f5f5;">
                    <th align="left">Guest</th>
                    <th align="left">Role</th>
                    <th align="left">Email</th>
                    <th align="left">Response</th>
                    <th align="left">Responded At</th>
                    <th align="left">Notes</th>
                </tr>
                {''.join(response_rows)}
            </table>
            """

            if len(members) > 0 and tentative_confirmed_count == len(members):

                final_email_sent_display = safe_text(
                    group["final_coordination_email_sent_at"]
                )

                if not final_email_sent_display:
                    final_email_sent_display = "Not sent yet"

                final_capacity_check = coordination_capacity_check_for_window(
                    conn,
                    group_id,
                    group["tentative_arrival_date"],
                    group["tentative_departure_date"]
                )

                if final_capacity_check["capacity_ok"]:

                    all_confirmed_banner = f"""
                    <div style="
                        background-color: #d4edda;
                        border: 2px solid #198754;
                        padding: 14px;
                        border-radius: 8px;
                        margin-bottom: 18px;
                        max-width: 900px;
                    ">

                        <h2 style="
                            color: #198754;
                            margin-top: 0;
                        ">
                            Round Status: Ready for Final Confirmation
                        </h2>

                        <p>
                            All coordination members have confirmed the tentative dates and capacity still checks out.
                        </p>

                        <p>
                            <strong>Tentative Dates:</strong>
                            {format_date(group['tentative_arrival_date'])}
                            to
                            {format_date(group['tentative_departure_date'])}<br>
                            <strong>Rooms Needed:</strong> {safe_text(final_capacity_check['rooms_needed'])}<br>
                            <strong>Final Coordination Email:</strong> {final_email_sent_display}
                        </p>

                        <p>
                            <a href="/coordination-group/{group_id}/handoff"
                               style="
                                   display: inline-block;
                                   background-color: #198754;
                                   color: white;
                                   padding: 8px 12px;
                                   border-radius: 5px;
                                   text-decoration: none;
                                   font-weight: bold;
                               ">
                                Continue to Booking Handoff / Finalize Group Visit
                            </a>
                        </p>

                        <p style="font-size: 13px; color: #555; margin-bottom: 0;">
                            Use Booking Handoff for guest confirmations, booking requests, room assignments, final confirmation, and closing.
                        </p>

                    </div>
                    """

                else:

                    all_confirmed_banner = f"""
                    <div style="
                        background-color: #fff3cd;
                        border: 2px solid #fd7e14;
                        padding: 14px;
                        border-radius: 8px;
                        margin-bottom: 18px;
                        max-width: 900px;
                    ">

                        <h2 style="
                            color: #856404;
                            margin-top: 0;
                        ">
                            Round Status: Needs Another Round
                        </h2>

                        <p>
                            Guests confirmed the tentative dates, but capacity or availability changed before finalization.
                        </p>

                        <p>
                            <strong>Issue:</strong><br>
                            {safe_text('; '.join(final_capacity_check['notes']))}
                        </p>

                        <p style="font-size: 13px; color: #555; margin-bottom: 0;">
                            Adjust tentative dates or room counts before finalizing the group visit.
                        </p>

                    </div>
                    """

        tentative_management_html = """
        <p>
            First select tentative dates from a best match. Then guests confirm whether those dates work. After that, create booking requests for confirmed guests.
        </p>
        """

        if safe_text(group["tentative_arrival_date"]) and safe_text(group["tentative_departure_date"]):

            due_date_display = safe_text(
                group["tentative_response_due_date"]
            ).strip() or default_coordination_due_date()

            overdue_label = ""

            if coordination_group_is_overdue(group):
                overdue_label = """
                <strong style='color: red;'>Overdue</strong>
                """

            converted_count = 0

            for member in members:

                if member["converted_request_id"]:
                    converted_count += 1

            tentative_management_html = f"""
            <div style="
                border: 1px solid #dee2e6;
                background-color: #f8f9fa;
                padding: 12px;
                margin-bottom: 10px;
                border-radius: 8px;
                max-width: 900px;
            ">
                <h3 style="margin-top: 0;">Planning Complete — Next Step</h3>

                <p>
                    <strong>RSVP Due Date:</strong> {due_date_display} {overdue_label}<br>
                    <strong>Booking Requests Created:</strong> {converted_count} of {len(members)}
                </p>

                <p>
                    Tentative dates have been selected. Use the Booking Handoff page for confirmation emails, booking requests, room assignments, final confirmation, and closing.
                </p>

                <p style="margin-bottom: 0;">
                    <a href="/coordination-group/{group_id}/handoff"
                       style="
                           display: inline-block;
                           background-color: #0d6efd;
                           color: white;
                           padding: 8px 12px;
                           border-radius: 5px;
                           text-decoration: none;
                           font-weight: bold;
                       ">
                        Go to Booking Handoff Page
                    </a>
                </p>
            </div>
            """

        responded_count = 0
        not_responded_names = []

        for member in members:

            if member["date_option_count"]:

                responded_count += 1

            else:

                not_responded_names.append(
                    safe_text(member["primary_name"])
                )

        current_round = coordination_round_number(group)
        round_pending_follow_up_members = []
        round_completed_follow_up_members = []

        for member in members:

            try:
                member_follow_up_round = int(row_value(member, "follow_up_round") or 0)
            except Exception:
                member_follow_up_round = 0

            if member_follow_up_round == current_round and safe_text(row_value(member, "follow_up_sent_at")).strip():

                if safe_text(row_value(member, "follow_up_response_at")).strip():
                    round_completed_follow_up_members.append(member)
                else:
                    round_pending_follow_up_members.append(member)

        round_status_label = "Collecting initial dates"
        round_status_background = "#eef5ff"
        round_waiting_text = "Waiting for initial group responses."

        if current_round > 1:

            if round_pending_follow_up_members:
                round_status_label = "Targeted follow-up in progress"
                round_status_background = "#fff3cd"
                round_waiting_text = "Waiting for: " + safe_text(", ".join([safe_text(member["primary_name"]) for member in round_pending_follow_up_members]))
            else:
                round_status_label = "Follow-up round complete"
                round_status_background = "#e8f7ea"
                round_waiting_text = "All targeted follow-up guests have responded. Review the updated Best Group Option below."

        round_status_html = f"""
        <div style="
            background-color: {round_status_background};
            border: 1px solid #ced4da;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 12px;
            max-width: 1080px;
            font-size: 13px;
        ">
            <strong>Current Planning Round:</strong> Round {current_round} — {round_status_label}<br>
            <strong>Status:</strong> {round_waiting_text}<br>
            <strong>Round Note:</strong> Round 1 starts with organizer setup. Later rounds are used when dates need another pass.
        </div>
        """

        date_options_summary_html = """
        <p>No date options have been submitted yet.</p>
        """

        if group_date_options:

            date_options_summary_html = """
            <table border="1"
                   cellpadding="5"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                       margin-top: 8px;
                   ">
                <tr style="background-color: #f5f5f5;">
                    <th align="left">Guest</th>
                    <th align="left">Role</th>
                    <th align="left">Priority</th>
                    <th align="left">Arrival</th>
                    <th align="left">Departure</th>
                    <th align="center">Nights</th>
                    <th align="center">Rooms</th>
                    <th align="center">Flexibility</th>
                    <th align="left">Notes</th>
                    <th align="left">Request Page</th>
                </tr>
            """

            for option in group_date_options:

                try:

                    nights = (
                        datetime.strptime(
                            option["departure_date"],
                            "%Y-%m-%d"
                        )
                        - datetime.strptime(
                            option["arrival_date"],
                            "%Y-%m-%d"
                        )
                    ).days

                except:

                    nights = ""

                date_options_summary_html += f"""
                <tr>
                    <td>{safe_text(option['primary_name'])}</td>
                    <td>{coordination_role_badge(option['role'])}</td>
                    <td>{safe_text(option['priority']).title()}</td>
                    <td>{format_date(option['arrival_date'])}</td>
                    <td>{format_date(option['departure_date'])}</td>
                    <td align="center">{safe_text(nights)}</td>
                    <td align="center">{safe_text(option['rooms_requested'])}</td>
                    <td align="center">± {safe_text(option['flexibility_days'])} day(s)</td>
                    <td>{safe_text(option['notes'])}</td>
                    <td>
                        <a href="/coordination-group-member/{option['member_id']}/request">
                            Open
                        </a>
                    </td>
                </tr>
                """

            date_options_summary_html += "</table>"

        not_responded_html = """
        <span style="color: green; font-weight: bold;">
            Everyone with a profile in this group has submitted date options.
        </span>
        """

        if not_responded_names:

            not_responded_html = safe_text(
                ", ".join(not_responded_names)
            )

        invitation_sent_count = 0
        invitation_not_sent_count = 0
        invitation_sent_names = []
        invitation_not_sent_names = []

        for member in members:

            member_invitation_status = safe_text(member["invitation_status"]).strip()

            if member_invitation_status in ["sent", "viewed", "responded", "capacity_review"]:

                invitation_sent_count += 1
                invitation_sent_names.append(safe_text(member["primary_name"]))

            else:

                invitation_not_sent_count += 1
                invitation_not_sent_names.append(safe_text(member["primary_name"]))

        planning_invitation_state = "Not Started"
        planning_invitation_icon = "⬜"
        planning_invitation_background = "#f8f9fa"

        if len(members) > 0 and invitation_sent_count == len(members):

            planning_invitation_state = "Done"
            planning_invitation_icon = "✅"
            planning_invitation_background = "#e8f7ea"

        elif invitation_sent_count > 0:

            planning_invitation_state = "Needs Action"
            planning_invitation_icon = "⚠️"
            planning_invitation_background = "#fff3cd"

        response_state = "Not Started"
        response_icon = "⬜"
        response_background = "#f8f9fa"

        if len(members) > 0 and responded_count == len(members):

            response_state = "Done"
            response_icon = "✅"
            response_background = "#e8f7ea"

        elif responded_count > 0 or invitation_sent_count > 0:

            response_state = "Needs Action"
            response_icon = "⚠️"
            response_background = "#fff3cd"

        overlap_state = "Not Started"
        overlap_icon = "⬜"
        overlap_background = "#f8f9fa"

        if group_date_options:

            overlap_state = "Review"
            overlap_icon = "⚠️"
            overlap_background = "#fff3cd"

            if match_suggestions:

                overlap_state = "Ready"
                overlap_icon = "✅"
                overlap_background = "#e8f7ea"

        tentative_state = "Not Started"
        tentative_icon = "⬜"
        tentative_background = "#f8f9fa"

        tentative_selected = bool(
            safe_text(group["tentative_arrival_date"])
            and safe_text(group["tentative_departure_date"])
        )

        planning_ready_for_booking = (
            safe_text(group["status"]) == "ready_for_booking"
            and tentative_selected
        )

        if safe_text(group["tentative_arrival_date"]) and safe_text(group["tentative_departure_date"]):

            tentative_state = "Done"
            tentative_icon = "✅"
            tentative_background = "#e8f7ea"

        outstanding_invitation_display = "None"

        if invitation_not_sent_names:

            outstanding_invitation_display = safe_text(", ".join(invitation_not_sent_names))

        step4_detail = "Pick the best overlap window and ask guests to confirm."
        step4_action = '<a href="#best-match-suggestions">Set Tentative Dates</a>'

        if tentative_selected:
            step4_detail = "Tentative dates selected. Planning is complete; continue on the Booking Handoff page."
            step4_action = f"""
            <a href="/coordination-group/{group_id}/handoff"
               style="display: inline-block; background-color: #198754; color: white; padding: 7px 10px; border-radius: 5px; text-decoration: none; font-weight: bold;">
                Go to Booking Handoff Page
            </a>
            """

        if safe_text(group["status"]) == "capacity_review":
            overlap_state = "Review Date / Capacity Overlap"
            overlap_icon = "⚠️"
            overlap_background = "#fff3cd"
            step4_detail = "Capacity or date overlap needs review before booking handoff. Use the Capacity Status section below to email guests if they need to change dates or bedrooms."
            step4_action = '<a href="#capacity-status">Review Capacity / Email Guests</a>'

        if planning_ready_for_booking:
            overlap_state = "Done"
            overlap_icon = "✅"
            overlap_background = "#e8f7ea"
            tentative_state = "Ready for Booking"
            tentative_icon = "✅"
            tentative_background = "#d4edda"
            step4_detail = "All guests now fit these dates. Planning is closed; use Booking Handoff for the rest."
            step4_action = f"""
            <a href="/coordination-group/{group_id}/handoff"
               style="display: inline-block; background-color: #198754; color: white; padding: 8px 12px; border-radius: 5px; text-decoration: none; font-weight: bold;">
                Go to Booking Handoff Page
            </a>
            """

        organizer_member = None

        for member in members:

            if safe_text(row_value(member, "role")).strip() == "organizer":
                organizer_member = member
                break

        organizer_formation_html = """
        <p>No organizer has been assigned yet.</p>
        """

        if organizer_member:

            organizer_link = organizer_planning_url(coordination_member_row_id(organizer_member))
            organizer_suggested_guests = safe_text(row_value(organizer_member, "organizer_suggested_guests")).strip()
            organizer_suggested_dates_notes = safe_text(row_value(organizer_member, "organizer_suggested_dates_notes")).strip()
            organizer_suggestions_at = safe_text(row_value(organizer_member, "organizer_suggestions_at")).strip()
            organizer_kickoff_sent_at = safe_text(row_value(organizer_member, "organizer_kickoff_sent_at")).strip()

            if not organizer_suggested_guests:
                organizer_suggested_guests = "No suggested guests submitted yet."

            if not organizer_suggested_dates_notes:
                organizer_suggested_dates_notes = "No organizer date notes submitted yet."

            if not organizer_suggestions_at:
                organizer_suggestions_at = "Not submitted yet"

            if not organizer_kickoff_sent_at:
                organizer_kickoff_sent_at = "Not sent yet"

            organizer_formation_html = f"""
            <div style="border:2px solid #0d6efd; background:#eef5ff; border-radius:8px; padding:12px; max-width:980px; margin-bottom:14px; font-size:13px;">
                <h3 style="margin-top:0;">Organizer-Led Formation</h3>
                <p style="margin-bottom:6px;">
                    <strong>Organizer:</strong> {safe_text(organizer_member['primary_name'])} &lt;{safe_text(organizer_member['primary_email'])}&gt;<br>
                    <strong>Kickoff Email:</strong> {safe_text(organizer_kickoff_sent_at)}<br>
                    <strong>Organizer Email Sent / Returned Suggestions:</strong> {safe_text(organizer_suggestions_at)}
                </p>
                <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px; background:white;">
                    <tr style="background:#f5f5f5;">
                        <th align="left">Organizer Link</th>
                        <th align="left">Suggested Guests</th>
                        <th align="left">Initial Date Notes</th>
                    </tr>
                    <tr>
                        <td><a href="{organizer_link}">Open Organizer Setup Page</a></td>
                        <td style="white-space:pre-wrap;">{safe_text(organizer_suggested_guests)}</td>
                        <td style="white-space:pre-wrap;">{safe_text(organizer_suggested_dates_notes)}</td>
                    </tr>
                </table>
                <p style="margin-bottom:0;">
                    <a href="/coordination-group/{group_id}/organizer-kickoff-preview" style="font-weight:bold;">Preview / Send Organizer Setup Email</a>
                </p>
            </div>
            """

        organizer_workflow_state = "Needs Action"
        organizer_workflow_name = "None assigned"
        organizer_workflow_kickoff = "Not sent"
        organizer_workflow_returned = "Not returned"
        organizer_workflow_action = "Assign an Organizer first"

        if organizer_member:
            organizer_workflow_name = safe_text(organizer_member["primary_name"])
            organizer_workflow_kickoff = safe_text(row_value(organizer_member, "organizer_kickoff_sent_at")).strip() or "Not sent"
            organizer_workflow_returned = safe_text(row_value(organizer_member, "organizer_suggestions_at")).strip() or "Not returned"
            organizer_workflow_action = f'<a href="/coordination-group/{group_id}/organizer-kickoff-preview">Preview / Send Organizer Email</a>'

            if organizer_workflow_kickoff != "Not sent" and organizer_workflow_returned != "Not returned":
                organizer_workflow_state = "✅ Complete"

        planning_workflow_html = f"""
        <h2>Action Workflow</h2>

        {round_status_html}

        <div style="
            background-color: #eef5ff;
            border: 2px solid #4a90e2;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 18px;
            max-width: 1080px;
        ">
            <p style="margin-top: 0;">
                Use this section to move the group through date planning before booking handoff.
            </p>

            <table border="1"
                   cellpadding="6"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                       margin-bottom: 14px;
                   ">
                <tr style="background-color: #f5f5f5;">
                    <th align="left">Step</th>
                    <th align="left">Status</th>
                    <th align="left">Details</th>
                    <th align="left">Action</th>
                </tr>

                <tr style="background-color:#fff8e6;">
                    <td><strong>1. Organizer Email Sent / Returned</strong></td>
                    <td>
                        {organizer_workflow_state}
                    </td>
                    <td>
                        Organizer: {organizer_workflow_name}<br>
                        Kickoff Email: {organizer_workflow_kickoff}<br>
                        Organizer Returned: {organizer_workflow_returned}
                    </td>
                    <td>
                        {organizer_workflow_action}
                    </td>
                </tr>

                <tr style="background-color: {planning_invitation_background};">
                    <td><strong>2. Send Coordination Invitations</strong></td>
                    <td>{planning_invitation_icon} {planning_invitation_state}</td>
                    <td>
                        Members Added: {len(members)}<br>
                        Invitations Sent: {invitation_sent_count}<br>
                        Not Sent: {invitation_not_sent_count}<br>
                        <small>Outstanding: {outstanding_invitation_display}</small>
                    </td>
                    <td>
                        <a href="/coordination-group/{group_id}/email-preview">
                            Preview / Send Invitations
                        </a>
                    </td>
                </tr>

                <tr style="background-color: {response_background};">
                    <td><strong>3. Collect Responses</strong></td>
                    <td>{response_icon} {response_state}</td>
                    <td>
                        Responses Received: {responded_count} of {len(members)}<br>
                        Waiting On: {not_responded_html}
                    </td>
                    <td>
                        <a href="/coordination-group/{group_id}/email-preview">
                            Resend / Remind Guests
                        </a>
                    </td>
                </tr>

                <tr style="background-color: {overlap_background};">
                    <td><strong>4. Review Date Overlap</strong></td>
                    <td>{overlap_icon} {overlap_state}</td>
                    <td>Review best match suggestions and unmatched guests below.</td>
                    <td><a href="#best-match-suggestions">View Suggestions</a></td>
                </tr>

                <tr style="background-color: {tentative_background};">
                    <td><strong>5. Select Tentative Dates</strong></td>
                    <td>{tentative_icon} {tentative_state}</td>
                    <td>{step4_detail}</td>
                    <td>{step4_action}</td>
                </tr>
            </table>

            <p style="font-size: 13px; color: #555; margin-bottom: 0;">
                After tentative dates are selected, use the Booking Handoff page for confirmations, booking requests, room assignments, approvals, and closing.
            </p>
        </div>
        """

        created_booking_requests_html = """
        <p>No booking requests have been created from this coordination group yet.</p>
        """

        if created_booking_request_rows:

            created_booking_requests_html = """
            <table border="1"
                   cellpadding="5"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                       margin-bottom: 18px;
                   ">
                <tr style="background-color: #f5f5f5;">
                    <th align="left">Guest</th>
                    <th align="left">Email</th>
                    <th align="left">Additional Guests</th>
                    <th align="left">Request</th>
                    <th align="left">Dates</th>
                    <th align="center">Rooms Requested</th>
                    <th align="left">Request Status</th>
                    <th align="left">Email Status</th>
                    <th align="left">Room Assignment</th>
                    <th align="left">Action</th>
                </tr>
            """

            for created_request in created_booking_request_rows:

                room_assignment_display = "Not assigned yet"

                if row_value(created_request, "approved_room_names"):
                    room_assignment_display = safe_text(
                        row_value(created_request, "approved_room_names")
                    )

                request_status_display_text = safe_text(
                    row_value(created_request, "request_status")
                )

                row_background = "#fff3cd"

                if request_status_display_text == "approved" and (row_value(created_request, "approved_booking_count") or 0) > 0:
                    row_background = "#e8f7ea"

                created_booking_requests_html += f"""
                <tr style="background-color: {row_background};">
                    <td>{safe_text(created_request['primary_name'])}</td>
                    <td>{safe_text(created_request['primary_email'])}</td>
                    <td>{safe_text(created_request['additional_names']) or 'None listed'}</td>
                    <td>
                        <a href="/request/{created_request['converted_request_id']}">
                            Request {created_request['converted_request_id']}
                        </a>
                    </td>
                    <td>
                        {format_date(created_request['arrival_date'])}<br>
                        to {format_date(created_request['departure_date'])}
                    </td>
                    <td align="center">
                        {safe_text(created_request['rooms_requested'])}
                    </td>
                    <td>{request_status_display_text}</td>
                    <td>{email_status_display(row_value(created_request, "email_status"), row_value(created_request, "email_needed_type"), row_value(created_request, "converted_request_id"))}</td>
                    <td>{room_assignment_display}</td>
                    <td>
                        <a href="/room-assignments">
                            Open Room Assignments
                        </a>
                        <br>
                        <small>
                            Request #{created_request['converted_request_id']}
                        </small>
                    </td>
                </tr>
                """

            created_booking_requests_html += "</table>"

        intersection_suggestions_html = """
        <p>No shared overlap yet. Once guests submit date options, Phase 3 will look for the common window where everyone can attend.</p>
        """

        if intersection_suggestions:

            best_intersection = intersection_suggestions[0]

            intersection_capacity_display = "<strong style='color: green;'>Capacity OK</strong>"

            if not best_intersection["capacity_ok"]:
                intersection_capacity_display = "<strong style='color: red;'>Capacity needs review</strong>"

            changed_names_display = "None — all submitted windows match this exact range"

            if best_intersection["changed_range_names"]:
                changed_names_display = safe_text(", ".join(best_intersection["changed_range_names"]))

            flexibility_names_display = "None"

            if best_intersection["flexibility_used_names"]:
                flexibility_names_display = safe_text(", ".join(best_intersection["flexibility_used_names"]))

            other_intersections_html = ""

            if len(intersection_suggestions) > 1:
                other_rows = ""
                rank = 2
                for suggestion in intersection_suggestions[1:5]:
                    other_rows += f"""
                    <tr>
                        <td>{rank}</td>
                        <td>{format_date(suggestion['arrival_date'])} to {format_date(suggestion['departure_date'])}</td>
                        <td align="center">{suggestion['nights']}</td>
                        <td align="center">{suggestion['rooms_needed']} of {suggestion['rooms_available']}</td>
                        <td>{'OK' if suggestion['capacity_ok'] else 'Needs Review'}</td>
                        <td>
                            <form method="POST" action="/coordination-group/{group_id}/set-tentative">
                                <input type="hidden" name="arrival_date" value="{suggestion['arrival_date']}">
                                <input type="hidden" name="departure_date" value="{suggestion['departure_date']}">
                                <button type="submit" style="font-size:12px; padding:4px 8px;">Set Tentative</button>
                            </form>
                        </td>
                    </tr>
                    """
                    rank += 1

                other_intersections_html = f"""
                <h4 style="margin-bottom:6px;">Other Shared Overlap Options</h4>
                <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
                    <tr style="background:#f5f5f5;">
                        <th>Rank</th><th>Dates</th><th>Nights</th><th>Rooms</th><th>Capacity</th><th>Action</th>
                    </tr>
                    {other_rows}
                </table>
                """

            intersection_suggestions_html = f"""
            <div style="background:#e8f7ea; border:2px solid #198754; border-radius:8px; padding:12px; margin-bottom:14px; max-width:1040px;">
                <h3 style="margin-top:0;">Round Shared Overlap Window</h3>
                <p style="font-size:16px; margin:4px 0;">
                    <strong>{format_date(best_intersection['arrival_date'])}</strong>
                    to
                    <strong>{format_date(best_intersection['departure_date'])}</strong>
                    ({best_intersection['nights']} night(s))
                </p>
                <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px; margin-top:8px;">
                    <tr style="background:#f5f5f5;">
                        <th align="left">Guests Included</th>
                        <th align="left">Rooms Needed</th>
                        <th align="left">Capacity</th>
                        <th align="left">Adjusted From Original Dates</th>
                        <th align="left">Flexibility Used</th>
                    </tr>
                    <tr>
                        <td>{best_intersection['matched_count']} of {best_intersection['total_member_count']}</td>
                        <td>{best_intersection['rooms_needed']} of {best_intersection['rooms_available']}</td>
                        <td>{intersection_capacity_display}</td>
                        <td>{changed_names_display}</td>
                        <td>{flexibility_names_display}</td>
                    </tr>
                </table>
                <p style="font-size:13px; color:#555;">
                    This uses the intersection rule: latest usable arrival plus earliest usable departure. It reserves only this shared overlap window, not every guest's full requested range.
                </p>
                <form method="POST" action="/coordination-group/{group_id}/set-tentative" style="margin-top:10px;">
                    <input type="hidden" name="arrival_date" value="{best_intersection['arrival_date']}">
                    <input type="hidden" name="departure_date" value="{best_intersection['departure_date']}">
                    <button type="submit" style="font-size:14px; padding:7px 12px; font-weight:bold;">
                        Set Shared Overlap As Tentative
                    </button>
                </form>
                {other_intersections_html}
            </div>
            """


        tentative_adjustments_html = ""

        if tentative_adjustments:
            adjustment_rows = ""
            for adjustment in tentative_adjustments:
                adjustment_rows += f"""
                <tr>
                    <td>{format_datetime_display(adjustment['created_at'])}</td>
                    <td>{format_date(adjustment['system_arrival_date'])} to {format_date(adjustment['system_departure_date'])}</td>
                    <td>{format_date(adjustment['admin_arrival_date'])} to {format_date(adjustment['admin_departure_date'])}</td>
                    <td>{safe_text(adjustment['rooms_needed'])}</td>
                    <td>{safe_text(adjustment['capacity_status'])}</td>
                    <td>{safe_text(adjustment['adjustment_reason'])}</td>
                </tr>
                """

            tentative_adjustments_html = f"""
            <div style="background:#f8fbff; border:1px solid #d8e6f3; border-radius:8px; padding:10px; margin:10px 0; max-width:1040px;">
                <h3 style="margin:0 0 6px 0;">Admin Tentative Date Adjustment History</h3>
                <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:12px;">
                    <tr style="background:#f5f5f5;">
                        <th>When</th>
                        <th>System Suggested</th>
                        <th>Admin Selected</th>
                        <th>Rooms</th>
                        <th>Capacity</th>
                        <th>Reason</th>
                    </tr>
                    {adjustment_rows}
                </table>
            </div>
            """

        phase4_default_arrival = safe_text(row_value(group, "tentative_arrival_date")).strip()
        phase4_default_departure = safe_text(row_value(group, "tentative_departure_date")).strip()
        phase4_system_arrival = ""
        phase4_system_departure = ""

        if intersection_suggestions:
            phase4_system_arrival = intersection_suggestions[0]["arrival_date"]
            phase4_system_departure = intersection_suggestions[0]["departure_date"]
            if not phase4_default_arrival:
                phase4_default_arrival = phase4_system_arrival
            if not phase4_default_departure:
                phase4_default_departure = phase4_system_departure

        phase4_system_text = "No system shared-overlap suggestion is available yet."
        if phase4_system_arrival and phase4_system_departure:
            phase4_system_text = f"{format_date(phase4_system_arrival)} to {format_date(phase4_system_departure)}"

        phase4_current_text = "No tentative dates currently selected."
        if phase4_default_arrival and phase4_default_departure:
            phase4_current_text = f"{format_date(phase4_default_arrival)} to {format_date(phase4_default_departure)}"

        phase4_admin_override_html = f"""
        <div style="background:#fff8e6; border:2px solid #fd7e14; border-radius:8px; padding:12px; margin:12px 0; max-width:1040px;">
            <h3 style="margin-top:0;">Round Admin Adjust Tentative Dates</h3>
            <p style="margin:4px 0; font-size:13px;">
                <strong>System suggested overlap:</strong> {phase4_system_text}<br>
                <strong>Current tentative dates:</strong> {phase4_current_text}
            </p>

            <form method="POST" action="/coordination-group/{group_id}/set-tentative" style="display:grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap:8px; max-width:720px;">
                <input type="hidden" name="system_arrival_date" value="{safe_text(phase4_system_arrival)}">
                <input type="hidden" name="system_departure_date" value="{safe_text(phase4_system_departure)}">
                <input type="hidden" name="admin_adjustment" value="yes">

                <label>
                    Tentative Arrival<br>
                    <input type="date" name="arrival_date" value="{safe_text(phase4_default_arrival)}" required style="width:100%; padding:6px;">
                </label>

                <label>
                    Tentative Departure<br>
                    <input type="date" name="departure_date" value="{safe_text(phase4_default_departure)}" required style="width:100%; padding:6px;">
                </label>

                <label style="grid-column:1 / -1;">
                    Adjustment reason / planning note<br>
                    <textarea name="adjustment_reason" rows="3" style="width:100%; padding:6px;" placeholder="Example: Better fit for family travel, room pressure, or guest flexibility."></textarea>
                </label>

                <div style="grid-column:1 / -1;">
                    <button type="submit" style="font-weight:bold; padding:7px 12px;">
                        Save Admin Tentative Dates
                    </button>
                    <small style="color:#555; margin-left:8px;">
                        Validates against blocked dates, bookings, partial blocks, and other tentative holds.
                    </small>
                </div>
            </form>
        </div>
        """ + tentative_adjustments_html

        match_suggestions_html = """
        <p>No match suggestions yet. Add date options for at least one group member.</p>
        """

        if match_suggestions:

            best_suggestion = match_suggestions[0]

            best_capacity_display = "<strong style='color: green;'>Capacity OK</strong>"

            if not best_suggestion["capacity_ok"]:
                best_capacity_display = "<strong style='color: red;'>Capacity needs review</strong>"

            all_group_member_names = []

            for member in members:
                all_group_member_names.append(
                    safe_text(member["primary_name"])
                )

            best_unmatched_names = []

            for member_name in all_group_member_names:
                if member_name not in best_suggestion["guest_names"]:
                    best_unmatched_names.append(member_name)

            best_unmatched_display = "None"

            best_unmatched_members = []

            if best_unmatched_names:
                best_unmatched_display = safe_text(", ".join(sorted(best_unmatched_names)))

                for member in members:
                    if safe_text(member["primary_name"]) in best_unmatched_names:
                        best_unmatched_members.append(member)

            targeted_follow_up_html = ""

            if best_unmatched_members:

                targeted_follow_up_rows = ""

                for member in best_unmatched_members:

                    targeted_follow_up_rows += f"""
                    <tr>
                        <td>{safe_text(member['primary_name'])}</td>
                        <td>{safe_text(member['primary_email'])}</td>
                        <td>
                            <a href="/coordination-group-member/{coordination_member_row_id(member)}/request?follow_up=1&suggested_arrival={best_suggestion['arrival_date']}&suggested_departure={best_suggestion['departure_date']}">
                                Open Date Link
                            </a>
                        </td>
                    </tr>
                    """

                targeted_follow_up_html = f"""
                <div style="
                    background-color: #fff3cd;
                    border: 1px solid #f0ad4e;
                    border-radius: 8px;
                    padding: 10px;
                    margin-top: 12px;
                ">
                    <h4 style="margin: 0 0 6px 0;">Targeted Follow-Up</h4>
                    <p style="margin: 0 0 8px 0; font-size: 13px;">
                        This option works for most guests. Start the next round by following up only with the guest(s) who do not match.
                    </p>
                    <table border="1" cellpadding="5" cellspacing="0"
                           style="border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 8px;">
                        <tr style="background-color: #f5f5f5;">
                            <th align="left">Guest</th>
                            <th align="left">Email</th>
                            <th align="left">Date Link</th>
                        </tr>
                        {targeted_follow_up_rows}
                    </table>
                    <p style="margin: 0;">
                        <a href="/coordination-group/{group_id}/follow-up-unmatched?arrival_date={best_suggestion['arrival_date']}&departure_date={best_suggestion['departure_date']}"
                           style="display: inline-block; background-color: #fd7e14; color: white; padding: 7px 10px; border-radius: 5px; text-decoration: none; font-weight: bold;">
                            Email Guest(s) To Update Availability
                        </a>
                    </p>
                </div>
                """

            best_why_html = f"<li>{best_suggestion['matched_count']} of {len(members)} guest(s) can attend</li>"

            for why_item in best_suggestion["why_bullets"]:
                if "guest(s) can attend" in safe_text(why_item):
                    continue
                best_why_html += f"<li>{safe_text(why_item)}</li>"

            nearby_html = ""

            if best_suggestion["nearby_before_names"]:
                nearby_html += f"""
                <li>
                    {format_date(best_suggestion['nearby_before_date'])} works for:
                    {safe_text(', '.join(best_suggestion['nearby_before_names']))}
                </li>
                """

            if best_suggestion["nearby_after_names"]:
                nearby_html += f"""
                <li>
                    {format_date(best_suggestion['nearby_after_date'])} works for:
                    {safe_text(', '.join(best_suggestion['nearby_after_names']))}
                </li>
                """

            if not nearby_html:
                nearby_html = "<li>No nearby fallback dates found from the submitted windows.</li>"

            match_suggestions_html = f"""
            <div style="
                background-color: #e8f7ea;
                border: 2px solid #198754;
                border-radius: 8px;
                padding: 12px;
                margin-bottom: 12px;
                max-width: 1040px;
            ">
                <h3 style="margin-top: 0; margin-bottom: 6px;">
                    Best Group Option
                </h3>

                <p style="font-size: 16px; margin: 4px 0;">
                    <strong>{format_date(best_suggestion['arrival_date'])}</strong>
                    to
                    <strong>{format_date(best_suggestion['departure_date'])}</strong>
                    ({best_suggestion['nights']} night(s))
                </p>

                <table border="1"
                       cellpadding="5"
                       cellspacing="0"
                       style="border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px;">
                    <tr style="background-color: #f5f5f5;">
                        <th align="left">Matched</th>
                        <th align="left">Rooms</th>
                        <th align="left">Preferred / Alternate</th>
                        <th align="left">Capacity</th>
                        <th align="left">Needs Follow-Up</th>
                    </tr>
                    <tr>
                        <td>{best_suggestion['matched_count']} of {len(members)}</td>
                        <td>{best_suggestion['rooms_needed']} of {best_suggestion['rooms_available']}</td>
                        <td>{best_suggestion['preferred_count']} preferred / {best_suggestion['alternate_count']} alternate</td>
                        <td>{best_capacity_display}</td>
                        <td>{best_unmatched_display}</td>
                    </tr>
                </table>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 10px;">
                    <div>
                        <strong>Why this is suggested:</strong>
                        <ul style="margin-top: 4px; margin-bottom: 0; padding-left: 20px;">
                            {best_why_html}
                        </ul>
                    </div>
                    <div>
                        <strong>Nearby dates that almost work:</strong>
                        <ul style="margin-top: 4px; margin-bottom: 0; padding-left: 20px;">
                            {nearby_html}
                        </ul>
                    </div>
                </div>

                <form method="POST"
                      action="/coordination-group/{group_id}/set-tentative"
                      style="margin-top: 12px;">
                    <input type="hidden" name="arrival_date" value="{best_suggestion['arrival_date']}">
                    <input type="hidden" name="departure_date" value="{best_suggestion['departure_date']}">
                    <button type="submit"
                            style="font-size: 14px; padding: 7px 12px; font-weight: bold;">
                        Set Best Option As Tentative
                    </button>
                </form>

                {targeted_follow_up_html}
            </div>
            """

            if len(match_suggestions) > 1:

                match_suggestions_html += """
                <h3 style="margin-bottom: 6px;">Other Possible Options</h3>
                <table border="1"
                       cellpadding="5"
                       cellspacing="0"
                       style="
                           border-collapse: collapse;
                           width: 100%;
                           font-size: 13px;
                           margin-top: 6px;
                       ">
                    <tr style="background-color: #f5f5f5;">
                        <th align="left">Rank</th>
                        <th align="left">Dates</th>
                        <th align="center">Guests</th>
                        <th align="center">Rooms</th>
                        <th align="left">Why / Follow-Up</th>
                        <th align="left">Action</th>
                    </tr>
                """

                rank = 2

                for suggestion in match_suggestions[1:4]:

                    other_capacity_display = "Capacity OK"

                    if not suggestion["capacity_ok"]:
                        other_capacity_display = "Capacity needs review"

                    other_unmatched_names = []

                    for member_name in all_group_member_names:
                        if member_name not in suggestion["guest_names"]:
                            other_unmatched_names.append(member_name)

                    follow_up_display = "No follow-up needed"

                    if other_unmatched_names:
                        follow_up_display = "Follow up with: " + safe_text(", ".join(sorted(other_unmatched_names)))

                    match_suggestions_html += f"""
                    <tr>
                        <td>{rank}</td>
                        <td>
                            <strong>{format_date(suggestion['arrival_date'])}</strong><br>
                            to {format_date(suggestion['departure_date'])}<br>
                            <small>{suggestion['nights']} night(s)</small>
                        </td>
                        <td align="center">{suggestion['matched_count']} of {len(members)}</td>
                        <td align="center">{suggestion['rooms_needed']} of {suggestion['rooms_available']}</td>
                        <td>
                            {suggestion['preferred_count']} preferred / {suggestion['alternate_count']} alternate<br>
                            {other_capacity_display}<br>
                            <small>{follow_up_display}</small>
                        </td>
                        <td>
                            <form method="POST"
                                  action="/coordination-group/{group_id}/set-tentative">
                                <input type="hidden" name="arrival_date" value="{suggestion['arrival_date']}">
                                <input type="hidden" name="departure_date" value="{suggestion['departure_date']}">
                                <button type="submit" style="font-size: 12px; padding: 4px 8px;">
                                    Set Tentative
                                </button>
                            </form>
                        </td>
                    </tr>
                    """

                    rank += 1

                match_suggestions_html += "</table>"


        def workflow_status_row(label, state, detail):

            if state == "done":
                icon = "✅"
                status_text = "Done"
                background = "#e8f7ea"
                border = "#198754"

            elif state == "needs_action":
                icon = "⚠️"
                status_text = "Needs Action"
                background = "#fff3cd"
                border = "#f0ad4e"

            else:
                icon = "⬜"
                status_text = "Not Started"
                background = "#f8f9fa"
                border = "#dee2e6"

            return f"""
            <tr style="background-color: {background};">
                <td style="width: 42px; font-size: 18px; text-align: center;">
                    {icon}
                </td>
                <td>
                    <strong>{label}</strong><br>
                    <small>{detail}</small>
                </td>
                <td style="width: 140px; font-weight: bold; border-left: 4px solid {border};">
                    {status_text}
                </td>
            </tr>
            """

        tentative_selected = bool(
            safe_text(group["tentative_arrival_date"])
            and safe_text(group["tentative_departure_date"])
        )

        all_guests_confirmed = (
            len(members) > 0
            and tentative_confirmed_count == len(members)
        )

        final_coordination_email_sent = bool(
            safe_text(group["final_coordination_email_sent_at"])
        )

        final_visit_confirmation_sent = bool(
            safe_text(group["final_visit_confirmation_sent_at"])
        )

        converted_member_count = 0

        for member in members:

            if member["converted_request_id"]:
                converted_member_count += 1

        booking_requests_created = converted_member_count > 0

        all_confirmed_guests_converted = (
            all_guests_confirmed
            and converted_member_count >= tentative_confirmed_count
        )

        all_created_requests_reviewed = False

        if created_booking_request_rows:

            all_created_requests_reviewed = True

            for created_request in created_booking_request_rows:

                try:
                    rooms_requested_for_check = int(created_request["rooms_requested"] or 1)
                except:
                    rooms_requested_for_check = 1

                if safe_text(created_request["request_status"]) != "approved":
                    all_created_requests_reviewed = False

                if int(created_request["approved_booking_count"] or 0) < rooms_requested_for_check:
                    all_created_requests_reviewed = False

        group_raw_closed = safe_text(group["status"]) in [
            "closed",
            "finalized",
            "archived"
        ]

        group_is_closed = (
            group_raw_closed
            and all_created_requests_reviewed
            and final_visit_confirmation_sent
        )

        tentative_step_state = "not_started"

        if tentative_selected:
            tentative_step_state = "done"
        elif match_suggestions:
            tentative_step_state = "needs_action"

        confirmation_step_state = "not_started"

        if all_guests_confirmed:
            confirmation_step_state = "done"
        elif tentative_selected:
            confirmation_step_state = "needs_action"

        tentative_email_step_state = "not_started"

        if safe_text(group["coordination_reminder_sent_at"]):
            tentative_email_step_state = "done"
        elif tentative_selected:
            tentative_email_step_state = "needs_action"

        final_visit_email_step_state = "not_started"

        if final_visit_confirmation_sent and all_created_requests_reviewed:
            final_visit_email_step_state = "done"
        elif all_created_requests_reviewed:
            final_visit_email_step_state = "needs_action"

        conversion_step_state = "not_started"

        if all_confirmed_guests_converted:
            conversion_step_state = "done"
        elif all_guests_confirmed:
            conversion_step_state = "needs_action"

        room_assignment_step_state = "not_started"

        if all_created_requests_reviewed:
            room_assignment_step_state = "done"
        elif booking_requests_created:
            room_assignment_step_state = "needs_action"

        close_step_state = "not_started"

        if group_is_closed:
            close_step_state = "done"
        elif all_created_requests_reviewed:
            close_step_state = "needs_action"

        workflow_progress_html = f"""
        <div style="
            background-color: #eef5ff;
            border: 2px solid #4a90e2;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 18px;
            max-width: 1080px;
        ">
            <h2 style="margin-top: 0;">
                Workflow Progress
            </h2>

            <p style="margin-top: 0; color: #555;">
                This shows what is done, what needs action, and what has not started yet.
            </p>

            <table border="1"
                   cellpadding="6"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                   ">
                <tr style="background-color: #f5f5f5;">
                    <th></th>
                    <th align="left">Workflow Step</th>
                    <th align="left">Status</th>
                </tr>
                {workflow_status_row(
                    "Pick or review tentative dates",
                    tentative_step_state,
                    "Choose the best overlap window from the suggested matches."
                )}
                {workflow_status_row(
                    "Get guest confirmations",
                    confirmation_step_state,
                    f"Confirmed: {tentative_confirmed_count} of {len(members)}"
                )}
                {workflow_status_row(
                    "Send final visit confirmation email",
                    final_visit_email_step_state,
                    "Send after booking requests are approved and rooms are assigned."
                )}
                {workflow_status_row(
                    "Create booking requests",
                    conversion_step_state,
                    f"Booking requests created: {converted_member_count} of {len(members)}"
                )}
                {workflow_status_row(
                    "Assign rooms and approve requests",
                    room_assignment_step_state,
                    "Use the Booking Handoff section below to review requests and assign rooms."
                )}
                {workflow_status_row(
                    "Close coordination group",
                    close_step_state,
                    "Close only after booking requests have been reviewed and the group is no longer active."
                )}
            </table>
        </div>
        """

        if planning_ready_for_booking:
            match_suggestions_html = f"""
            <div style="
                background-color: #d4edda;
                border: 2px solid #198754;
                border-radius: 8px;
                padding: 14px;
                margin-bottom: 14px;
                max-width: 1040px;
            ">
                <h2 style="margin-top: 0; color: #198754;">
                    Dates Work for Everyone
                </h2>
                <p style="font-size: 16px; margin-bottom: 6px;">
                    <strong>{format_date(group['tentative_arrival_date'])}</strong>
                    to
                    <strong>{format_date(group['tentative_departure_date'])}</strong>
                </p>
                <p>
                    No more planning follow-up is needed. The next step is Booking Handoff.
                </p>
                <p style="margin-bottom: 0;">
                    <a href="/coordination-group/{group_id}/handoff"
                       style="display: inline-block; background-color: #198754; color: white; padding: 8px 12px; border-radius: 5px; text-decoration: none; font-weight: bold;">
                        Go to Booking Handoff Page
                    </a>
                </p>
            </div>
            """

        capacity_dashboard_html = ""
        next_recommended_action = "Invite Guests"

        try:
            requested_rooms = 0
            preferred_rows = ""
            alternate_rows = ""
            seen_member_rooms = {}

            for option in group_date_options:
                member_id_for_option = row_value(option, "member_id") or row_value(option, "coordination_group_member_id")
                option_rooms = row_value(option, "rooms_requested") or 1

                try:
                    option_rooms = int(option_rooms)
                except Exception:
                    option_rooms = 1

                previous_rooms = seen_member_rooms.get(member_id_for_option, 0)
                if option_rooms > previous_rooms:
                    seen_member_rooms[member_id_for_option] = option_rooms

                row_html = f"""
                <tr>
                    <td>{safe_text(option['primary_name'])}</td>
                    <td>{format_date(option['arrival_date'])} to {format_date(option['departure_date'])}</td>
                    <td align="center">{option_rooms}</td>
                    <td>{safe_text(option['created_at'])[:10]}</td>
                </tr>
                """

                if safe_text(option["priority"]).lower() == "alternate":
                    alternate_rows += row_html
                else:
                    preferred_rows += row_html

            for member_row in members:
                member_key = row_value(member_row, "id")
                if member_key not in seen_member_rooms:
                    member_rooms = row_value(member_row, "rooms_requested") or 1

                    try:
                        member_rooms = int(member_rooms)
                    except Exception:
                        member_rooms = 1

                    seen_member_rooms[member_key] = member_rooms

            requested_rooms = sum(seen_member_rooms.values())

            if not preferred_rows:
                preferred_rows = "<tr><td colspan='4'>No preferred dates submitted yet.</td></tr>"

            if not alternate_rows:
                alternate_rows = "<tr><td colspan='4'>No alternate dates submitted yet.</td></tr>"

            available_rooms = total_rooms_for_matching

            try:
                available_rooms = int(available_rooms)
            except Exception:
                available_rooms = 0

            room_delta = requested_rooms - available_rooms

            if room_delta > 0 or safe_text(group["status"]) == "capacity_review":
                next_recommended_action = "Review Date / Capacity Overlap"
            elif not members:
                next_recommended_action = "Invite Guests"
            elif not_responded_names:
                next_recommended_action = "Waiting Responses"
            elif not safe_text(group["tentative_arrival_date"]) or not safe_text(group["tentative_departure_date"]):
                next_recommended_action = "Set Tentative Dates"
            elif safe_text(group["status"]) in ["ready_for_booking", "tentative", "confirmed_coordination"]:
                next_recommended_action = "Booking Handoff"
            elif safe_text(group["status"]) in ["finalized", "closed"]:
                next_recommended_action = "Close Group"

            def next_action_box(label):
                checked = "☑" if label == next_recommended_action else "☐"
                background = "#e8f7ea" if label == next_recommended_action else "#f8f9fa"
                return f"<div style='padding:6px 8px; border:1px solid #dee2e6; background:{background}; border-radius:6px; margin-bottom:4px;'>{checked} {label}</div>"

            next_action_html = f"""
            <div class="coord-card" style="background:#eef5ff; border:2px solid #0f4c81;">
                <h2>Next Recommended Action</h2>
                {next_action_box("Invite Guests")}
                {next_action_box("Waiting Responses")}
                {next_action_box("Review Date / Capacity Overlap")}
                {next_action_box("Set Tentative Dates")}
                {next_action_box("Booking Handoff")}
                {next_action_box("Close Group")}
            </div>
            """

            capacity_dashboard_html = f"""
            {next_action_html}

            <div class="coord-card" style="background:#fff8e1; border:2px solid #fd7e14;">
                <h2 id="capacity-status">Capacity Status</h2>

                <p style="margin-top:0;">
                    <strong>Rooms Requested:</strong> {requested_rooms}<br>
                    <strong>Rooms Available:</strong> {available_rooms}<br>
                    <strong>Difference:</strong> {room_delta:+}
                </p>

                <h3>Your Current Preferred Dates</h3>
                <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
                    <tr style="background:#f5f5f5;">
                        <th align="left">Guest</th>
                        <th align="left">Dates</th>
                        <th align="center">Bedrooms</th>
                        <th align="left">Date Requested</th>
                    </tr>
                    {preferred_rows}
                </table>

                <h3>Alternate Dates</h3>
                <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse; width:100%; font-size:13px;">
                    <tr style="background:#f5f5f5;">
                        <th align="left">Guest</th>
                        <th align="left">Dates</th>
                        <th align="center">Bedrooms</th>
                        <th align="left">Date Requested</th>
                    </tr>
                    {alternate_rows}
                </table>

                <p style="margin-bottom:0;">
                    If Difference is positive, reduce bedrooms, split the group, choose different dates, or start another round.
                </p>

                <form method="POST"
                      action="/coordination-group/{group_id}/capacity-email"
                      onsubmit="return confirm('Send capacity review email to all group members?');"
                      style="margin-top:10px;">
                    <button type="submit"
                            style="background:#fd7e14; color:white; border:0; padding:7px 10px; border-radius:5px; font-weight:bold;">
                        Email Guests to Change Dates / Bedrooms
                    </button>
                </form>

                <p style="margin-top:8px;">
                    <a href="/coordination-group/{group_id}/handoff" style="font-weight:bold;">Booking Handoff</a>
                    |
                    <a href="#best-match-suggestions" style="font-weight:bold;">Review Overlaps</a>
                </p>
            </div>
            """

        except Exception:
            capacity_dashboard_html = """
            <div class="coord-card" style="background:#fff3cd; border:1px solid #fd7e14;">
                <h2>Capacity Status</h2>
                <p>Capacity details could not be summarized, but the rest of the planning page is still available.</p>
            </div>
            """

        html = nav_links() + f"""
        <style>
            .coord-card {{
                border: 1px solid #dee2e6;
                border-radius: 10px;
                padding: 12px;
                margin-bottom: 14px;
                max-width: 1080px;
                background: #ffffff;
            }}
            .coord-card h2 {{
                margin: 0 0 8px 0;
                font-size: 20px;
            }}
            .coord-card h3 {{
                margin: 8px 0 6px 0;
            }}
            .coord-muted {{
                color: #555;
                font-size: 13px;
            }}
            .coord-mini-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
                gap: 8px;
                margin: 8px 0;
            }}
            .coord-mini-stat {{
                background:#f8f9fa;
                border:1px solid #e5e7eb;
                border-radius:8px;
                padding:8px;
                font-size:13px;
            }}
            .coord-mini-stat strong {{
                display:block;
                font-size:17px;
                color:#0f4c81;
            }}
            details.coord-collapse {{
                margin-top: 8px;
            }}
            details.coord-collapse summary {{
                cursor:pointer;
                font-weight:bold;
                color:#0f4c81;
            }}
        </style>

        <h1>{safe_text(group['title'])} — Planning</h1>

        <div class="coord-card" style="background:#f8f9fa;">
            <strong>Coordination Pages:</strong>
            <a href="/coordination-group/{group_id}" style="font-weight:bold; margin-left:8px;">Planning Page</a>
            |
            <a href="/coordination-group/{group_id}/handoff" style="font-weight:bold;">Booking Handoff Page</a>
            <br>
            <small class="coord-muted">Planning is for finding dates. Booking Handoff is for confirmations, booking requests, room assignments, and approvals.</small>
        </div>

        {planning_workflow_html}

        <div class="coord-card">
            <h2>Group Setup</h2>

            <div class="coord-mini-grid">
                <div class="coord-mini-stat"><strong>{safe_text(group['status'])}</strong>Status</div>
                <div class="coord-mini-stat"><strong>{len(members)}</strong>Members</div>
                <div class="coord-mini-stat"><strong>{responded_count}</strong>Responded</div>
                <div class="coord-mini-stat"><strong>Round {current_round}</strong>Current Round</div>
            </div>

            <p class="coord-muted" style="margin-bottom:8px;">
                Target Year: {safe_text(group['target_year'])} |
                Created: {safe_text(group['created_at'])[:10]}
            </p>

            <div style="background:#f8fbff; border:1px solid #d8e6f3; padding:8px; border-radius:8px; margin-bottom:10px;">
                {safe_text(group['description'])}
            </div>

            {organizer_formation_html}

            <details class="coord-collapse">
                <summary>Show submitted date options and waiting list</summary>
                <p>
                    <strong>Waiting On:</strong><br>
                    {not_responded_html}
                </p>
                {date_options_summary_html}
            </details>
        </div>

        <div class="coord-card" id="best-match-suggestions" style="background:#eef7ee;">
            <h2>Date Coordination</h2>

            <div class="coord-mini-grid">
                <div class="coord-mini-stat"><strong>{tentative_confirmed_count}</strong>Confirmed Works</div>
                <div class="coord-mini-stat"><strong>{tentative_cannot_count}</strong>Cannot Make</div>
                <div class="coord-mini-stat"><strong>{tentative_discussion_count}</strong>Need Different Dates</div>
                <div class="coord-mini-stat"><strong>{tentative_no_response_count}</strong>No Response</div>
            </div>

            <h3>Current Tentative Dates</h3>
            {tentative_dates_html}

            <h3>System Overlap / Best Match</h3>
            <p class="coord-muted">Use this section to compare submitted preferred and alternate dates, then select or adjust tentative dates.</p>

            {intersection_suggestions_html}

            {phase4_admin_override_html}

            <details class="coord-collapse">
                <summary>Show additional best-match detail</summary>
                {match_suggestions_html}
            </details>
        </div>

        {capacity_dashboard_html}

        <div class="coord-card">
            <h2>Issues / Exceptions</h2>
            <p>
                <strong>Waiting On:</strong> {len(not_responded_names)} guest(s)<br>
                <strong>Cannot Make Tentative Dates:</strong> {tentative_cannot_count}<br>
                <strong>Need Different Dates / Comments:</strong> {tentative_discussion_count}
            </p>
            <p class="coord-muted">Use this as the quick scan area. If everything is zero, move toward Booking Handoff.</p>
        </div>

        <div class="coord-card" style="background:#fff3cd; border-color:#fd7e14;">
            <h2>Capacity Review / Over-Room Request</h2>
            <p style="margin-top:0;">
                If this round is requesting more rooms than are available, pause here before sending more guest emails.
            </p>
            <ol style="margin-top:4px; line-height:1.35;">
                <li>Review each guest's room count in the Group Members table.</li>
                <li>Ask the organizer which rooms can be reduced, combined, split, or moved to different dates.</li>
                <li>After resolving, start/send another round or continue to Booking Handoff.</li>
            </ol>
            <p class="coord-muted" style="margin-bottom:0;">
                If status is <strong>capacity_review</strong>, this is the next action area.
            </p>
        </div>

        <div class="coord-card">
            <h2>Round History / Testing Visibility</h2>
            <div class="coord-mini-grid">
                <div class="coord-mini-stat"><strong>Round {current_round}</strong>Active Round</div>
                <div class="coord-mini-stat"><strong>{invitation_sent_count}</strong>Emails Sent</div>
                <div class="coord-mini-stat"><strong>{responded_count}</strong>Responses</div>
                <div class="coord-mini-stat"><strong>{len(created_booking_request_rows)}</strong>Booking Requests</div>
            </div>
            <p class="coord-muted" style="margin-bottom:0;">
                Round numbering is display-only in this version. Use it to test multi-round coordination without changing booking logic.
            </p>
        </div>

        <div class="coord-card" style="background:#fff8e6;">
            <h2>Booking / Closeout</h2>
            <p style="margin-top:0;">
                Once guests confirm tentative dates, continue to Booking Handoff for booking requests, room assignment, approval emails, final confirmation, and closing.
            </p>
            <p>
                <a href="/coordination-group/{group_id}/handoff" style="font-weight:bold;">Open Booking Handoff Page</a>
            </p>
        </div>

        <div class="coord-card" style="background:#fff3cd; font-size:13px;">
            <strong>Admin Cleanup:</strong>
            <a href="/coordination-group/{group_id}/delete" style="color:#842029; font-weight:bold; margin-left:8px;">Delete Coordination Process</a>
            <br>
            <small>Deletes the planning/coordination process only. Guest profiles are never deleted. Confirmed bookings block deletion.</small>
        </div>

        <h2>Group Members</h2>

        <div style="background:#eef5ff; border:1px solid #b6d4fe; border-radius:8px; padding:10px; max-width:760px; margin-bottom:10px; font-size:13px;">
            <strong>Coordination Roles:</strong><br>
            <strong>Organizer</strong> = main planning contact / helps suggest group dates.<br>
            <strong>Participant</strong> = guest submitting availability for the group.
        </div>

        <details class="coord-collapse" open>
            <summary>Add / review group members</summary>

        <div style="
            border: 1px solid #dee2e6;
            background-color: #f8f9fa;
            padding: 10px;
            margin-bottom: 12px;
            border-radius: 8px;
            max-width: 760px;
        ">
            <h3 style="
                margin-top: 0;
            ">
                Add Guest Profile to Group
            </h3>
        """

        if not available_profiles:

            html += """
            <p>
                No available guest profiles found to add.
            </p>
            """

        else:

            html += f"""
            <form method="POST"
                  action="/coordination-group/{group_id}/add-member">

                <label>
                    <strong>Guest Profile</strong>
                </label><br>

                <select name="guest_profile_id"
                        required
                        style="width: 420px;">
            """

            for profile in available_profiles:

                html += f"""
                    <option value="{profile['id']}">
                        {safe_text(profile['primary_name'])} — {safe_text(profile['primary_email'])} ({safe_text(profile['status'])})
                    </option>
                """

            html += """
                </select>

                <br>

                <label>
                    <strong>Role</strong>
                </label><br>

                <select name="role">
                    <option value="participant">Participant</option>
                    <option value="organizer">Organizer</option>
                </select>

                <br>

                <button type="submit">
                    Add Guest to Group
                </button>

            </form>
            """

        html += """
        </div>
        """

        if not members:

            html += """
            <p>
                No guest profiles have been added yet.
            </p>

            <p>
                Add existing guest profiles above. Next V10 step after this
                is generating group request links.
            </p>
            """

        else:

            html += """
            <table border="1"
                   cellpadding="5"
                   cellspacing="0"
                   style="
                       border-collapse: collapse;
                       width: 100%;
                       font-size: 13px;
                   ">

                <tr style="background-color: #f5f5f5;">
                    <th align="left">Guest</th>
                    <th align="left">Email</th>
                    <th align="left">Role</th>
                    <th align="left">Invitation Status</th>
                    <th align="left">Last Response</th>
                    <th align="center">Rooms</th>
                    <th align="center">Date Options</th>
                    <th align="left">Request Link</th>
                    <th align="left">Profile</th>
                </tr>
            """

            for member in members:

                html += f"""
                <tr>
                    <td>{safe_text(member['primary_name'])}</td>
                    <td>{safe_text(member['primary_email'])}</td>
                    <td>{coordination_role_badge(member['role'])}</td>
                    <td>{safe_text(member['invitation_status'])}</td>
                    <td>{safe_text(member['last_response_at'])}</td>
                    <td align="center">{safe_text(row_value(member, 'rooms_requested')) or '1'}</td>
                    <td align="center">{member['date_option_count']}</td>
                    <td>
                        <a href="/coordination-group-member/{coordination_member_row_id(member)}/request">
                            Open Request Page
                        </a>
                    </td>
                    <td>
                        <a href="/profile/{member['guest_profile_id']}">
                            View
                        </a>
                    </td>
                </tr>
                """

            html += "</table>"

        html += f"""
        </details>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

        try:
            conn.close()
        except Exception:
            pass

        return html



    except Exception as planning_error:

        try:
            conn.close()
        except Exception:
            pass

        error_text = safe_text(planning_error)

        try:
            navigation_html = nav_links()
        except Exception:
            navigation_html = ""

        return f"""
        {navigation_html}

        <h1>Planning Page Needs Review</h1>

        <div style="
            border: 2px solid #fd7e14;
            background: #fff3cd;
            padding: 14px;
            border-radius: 10px;
            max-width: 820px;
        ">
            <p style="font-weight: bold; margin-top: 0;">
                The detailed Planning view could not fully load, but the app did not lose any saved data.
            </p>

            <p>
                This usually means the group has booking-handoff data that the Planning page could not summarize safely.
                Use Booking Handoff for this group, or go back to Coordination Groups.
            </p>

            <p>
                <strong>Technical detail:</strong><br>
                {error_text}
            </p>

            <p>
                <a href="/coordination-group/{group_id}/handoff"
                   style="font-weight:bold;">
                    Open Booking Handoff
                </a>
                |
                <a href="/coordination-groups"
                   style="font-weight:bold;">
                    Back to Coordination Groups
                </a>
            </p>
        </div>
        """



@app.route("/coordination-group/<int:group_id>/delete", methods=["GET", "POST"])
def coordination_group_delete(group_id):

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if not group:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Coordination Group Not Found</h1>
        <p><a href="/coordination-groups">Back to Coordination Groups</a></p>
        """

    members = conn.execute("""
        SELECT id, converted_request_id
        FROM coordination_group_members
        WHERE coordination_group_id = ?
    """, (group_id,)).fetchall()

    converted_request_ids = [
        safe_text(member["converted_request_id"]).strip()
        for member in members
        if safe_text(member["converted_request_id"]).strip().isdigit()
    ]

    approved_booking_count = 0

    if converted_request_ids:
        placeholders = ",".join(["?"] * len(converted_request_ids))
        approved_booking_count = conn.execute(f"""
            SELECT COUNT(*) AS count
            FROM bookings
            WHERE request_id IN ({placeholders})
              AND status = 'approved'
        """, converted_request_ids).fetchone()["count"]

    if approved_booking_count:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Coordination Process Not Deleted</h1>
        <div style="background:#f8d7da; border:2px solid #dc3545; padding:12px; border-radius:8px; max-width:850px;">
            <p style="font-weight:bold; margin-top:0;">This coordination group has confirmed/approved bookings.</p>
            <p>Cancel or remove those confirmed stays first. This safety check prevents deleting planning history while bookings still exist.</p>
        </div>
        <p><a href="/coordination-group/{group_id}/handoff">Back to Booking Handoff</a></p>
        <p><a href="/coordination-group/{group_id}">Back to Planning Page</a></p>
        """

    if request.method != "POST":
        conn.close()
        return action_confirmation_page(
            "Delete Coordination Process",
            f"Delete coordination group '{safe_text(group['title'])}' and its planning responses/date options? Guest profiles will not be deleted. Converted booking requests without approved bookings will be unlinked, not deleted.",
            f"/coordination-group/{group_id}/delete",
            f"/coordination-group/{group_id}"
        )

    backup_path = create_database_backup(
        f"before_delete_coordination_group_{group_id}"
    )

    try:
        if converted_request_ids:
            placeholders = ",".join(["?"] * len(converted_request_ids))
            conn.execute(f"""
                UPDATE booking_requests
                SET coordination_group_id = NULL,
                    coordination_group_member_id = NULL
                WHERE id IN ({placeholders})
            """, converted_request_ids)

        conn.execute("""
            DELETE FROM coordination_date_options
            WHERE coordination_group_member_id IN (
                SELECT id
                FROM coordination_group_members
                WHERE coordination_group_id = ?
            )
        """, (group_id,))

        conn.execute("""
            DELETE FROM coordination_group_members
            WHERE coordination_group_id = ?
        """, (group_id,))

        conn.execute("""
            DELETE FROM coordination_groups
            WHERE id = ?
        """, (group_id,))

        conn.commit()
        conn.close()

    except Exception as error:
        rollback_and_close(conn)
        return transaction_error_page(
            error,
            f"/coordination-group/{group_id}"
        )

    return f"""
    {nav_links()}
    <h1>Coordination Process Deleted</h1>
    <p>The coordination process was deleted.</p>
    <p>Guest profiles were preserved.</p>
    <p>Backup created before deletion: <code>{safe_text(backup_path)}</code></p>
    <p><a href="/coordination-groups">Back to Coordination Groups</a></p>
    """


@app.route("/coordination-group/<int:group_id>/handoff")
def coordination_group_handoff(group_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            The coordination group could not be found.
        </p>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    default_due_date_value, group = ensure_coordination_due_date(
        conn,
        group_id,
        group
    )

    due_date_fallback_script = f"""
    <script>
        document.addEventListener("DOMContentLoaded", function () {{
            document.querySelectorAll('input[name="tentative_response_due_date"]').forEach(function (field) {{
                if (!field.value || field.value === "No due date set") {{
                    field.value = "{default_due_date_value}";
                }}
            }});
        }});
    </script>
    """

    members = conn.execute("""
        SELECT
            coordination_group_members.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    created_booking_request_rows = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.converted_request_id,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            booking_requests.status AS request_status,
            booking_requests.email_status,
            booking_requests.email_needed_type,
            booking_requests.additional_names,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.rooms_requested,
            COUNT(bookings.id) AS approved_booking_count,
            GROUP_CONCAT(rooms.name, ', ') AS approved_room_names
        FROM coordination_group_members

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        LEFT JOIN booking_requests
            ON coordination_group_members.converted_request_id = booking_requests.id

        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'

        LEFT JOIN rooms
            ON bookings.room_id = rooms.id

        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.converted_request_id IS NOT NULL

        GROUP BY
            coordination_group_members.id,
            coordination_group_members.converted_request_id,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            booking_requests.status,
            booking_requests.email_status,
            booking_requests.email_needed_type,
            booking_requests.additional_names,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.rooms_requested

        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    conn.commit()
    conn.close()

    tentative_selected = bool(
        safe_text(group["tentative_arrival_date"])
        and safe_text(group["tentative_departure_date"])
    )

    tentative_confirmed_count = 0
    tentative_cannot_count = 0
    tentative_discussion_count = 0
    tentative_no_response_count = 0

    for member in members:

        response_status = safe_text(member["tentative_response_status"])

        if response_status == "confirmed":
            tentative_confirmed_count += 1
        elif response_status == "cannot_make":
            tentative_cannot_count += 1
        elif response_status == "needs_discussion":
            tentative_discussion_count += 1
        else:
            tentative_no_response_count += 1

    all_guests_confirmed = (
        len(members) > 0
        and tentative_confirmed_count == len(members)
    )

    final_coordination_email_sent = bool(
        safe_text(group["final_coordination_email_sent_at"])
    )

    final_visit_confirmation_sent = bool(
        safe_text(group["final_visit_confirmation_sent_at"])
    )

    converted_member_count = 0

    for member in members:

        if member["converted_request_id"]:
            converted_member_count += 1

    booking_requests_created = converted_member_count > 0

    all_confirmed_guests_converted = (
        all_guests_confirmed
        and converted_member_count >= tentative_confirmed_count
    )

    all_created_requests_reviewed = False

    if created_booking_request_rows:

        all_created_requests_reviewed = True

        for created_request in created_booking_request_rows:

            try:
                rooms_requested_for_check = int(created_request["rooms_requested"] or 1)
            except:
                rooms_requested_for_check = 1

            if safe_text(created_request["request_status"]) != "approved":
                all_created_requests_reviewed = False

            if int(created_request["approved_booking_count"] or 0) < rooms_requested_for_check:
                all_created_requests_reviewed = False

    group_raw_closed = safe_text(group["status"]) in [
        "closed",
        "finalized",
        "archived"
    ]

    group_is_closed = (
        group_raw_closed
        and all_created_requests_reviewed
        and final_visit_confirmation_sent
    )

    def status_badge(state):

        if state == "done":
            return "<strong style='color: green;'>✅ Done</strong>"

        if state == "needs_action":
            return "<strong style='color: #b26a00;'>⚠️ Needs Action</strong>"

        return "<span style='color: #666;'>⬜ Not Started</span>"

    tentative_email_step_state = "not_started"

    if safe_text(group["coordination_reminder_sent_at"]):
        tentative_email_step_state = "done"
    elif tentative_selected:
        tentative_email_step_state = "needs_action"

    final_visit_email_step_state = "not_started"

    if final_visit_confirmation_sent and all_created_requests_reviewed:
        final_visit_email_step_state = "done"
    elif all_created_requests_reviewed:
        final_visit_email_step_state = "needs_action"

    conversion_step_state = "not_started"

    if all_confirmed_guests_converted:
        conversion_step_state = "done"
    elif all_guests_confirmed:
        conversion_step_state = "needs_action"

    room_assignment_step_state = "not_started"

    if all_created_requests_reviewed:
        room_assignment_step_state = "done"
    elif booking_requests_created:
        room_assignment_step_state = "needs_action"

    close_step_state = "not_started"

    if group_is_closed:
        close_step_state = "done"
    elif all_created_requests_reviewed:
        close_step_state = "needs_action"

    tentative_dates_display = "No tentative dates selected yet."

    if tentative_selected:

        tentative_dates_display = (
            f"{format_date(group['tentative_arrival_date'])} "
            f"to {format_date(group['tentative_departure_date'])}"
        )

    final_visit_email_sent_display = "Not Sent"

    if final_visit_confirmation_sent and all_created_requests_reviewed:
        final_visit_email_sent_display = "Sent " + safe_text(group["final_visit_confirmation_sent_at"])[:10]
    elif final_visit_confirmation_sent and not all_created_requests_reviewed:
        final_visit_email_sent_display = "Needs re-send after pending request is re-approved"

    approval_email_sent_count = 0
    approval_email_needed_count = 0

    for created_request in created_booking_request_rows:

        if safe_text(created_request["email_status"]) == "sent":
            approval_email_sent_count += 1

        elif safe_text(created_request["email_status"]) in ["needs_email", "needs_update"]:
            approval_email_needed_count += 1

    approval_email_summary_display = (
        f"{approval_email_sent_count} sent / "
        f"{len(created_booking_request_rows)} booking request(s)"
    )

    if approval_email_needed_count > 0:
        approval_email_summary_display += (
            f"; {approval_email_needed_count} still need email"
        )

    tentative_confirmation_email_display = "Not Sent"

    if safe_text(group["coordination_reminder_sent_at"]):
        tentative_confirmation_email_display = "Sent " + safe_text(group["coordination_reminder_sent_at"])[:10]

    default_tentative_due_date = safe_text(group["tentative_response_due_date"]).strip()

    if not default_tentative_due_date or default_tentative_due_date.lower() in ["no due date set", "none", "null"]:
        default_tentative_due_date = default_due_date_value

    tentative_confirmation_email_action_html = """
    <p style="color: #666;">
        Select tentative dates before asking guests to confirm.
    </p>
    """

    if tentative_selected:

        if safe_text(group["coordination_reminder_sent_at"]):

            tentative_confirmation_email_action_html = f"""
            <p style="color: green; font-weight: bold; margin-bottom: 4px;">
                Tentative date confirmation emails were sent.
            </p>
            <p style="color: #666; margin-top: 0;">
                Sent on {safe_text(group['coordination_reminder_sent_at'])[:10]}.
            </p>
            <form method="POST"
                  action="/coordination-group/{group_id}/send-reminders"
                  style="margin-bottom: 8px;">
                <label>
                    <strong>Response Due Date</strong>
                </label><br>
                <input type="date"
                       name="tentative_response_due_date"
                       value="{default_tentative_due_date}"
                       data-default-plus-three="1">
                <br>
                <button type="submit">
                    Resend / Remind Guests Still Needing Response
                </button>
            </form>
            """

        else:

            tentative_confirmation_email_action_html = f"""
            <form method="POST"
                  action="/coordination-group/{group_id}/send-reminders"
                  style="margin-bottom: 8px;">
                <label>
                    <strong>Response Due Date</strong>
                </label><br>
                <input type="date"
                       name="tentative_response_due_date"
                       value="{default_tentative_due_date}"
                       data-default-plus-three="1">
                <br>
                <button type="submit"
                        style="
                            background-color: #0d6efd;
                            color: white;
                            padding: 8px 12px;
                            border: 0;
                            border-radius: 5px;
                            font-weight: bold;
                        ">
                    Send Tentative Dates That May Work for Everyone Emails
                </button>
            </form>
            <small style="color: #666;">
                Sends each guest their link to respond: These Dates Work For Me, These Dates Do Not Work, or Need Different Dates.
            </small>
            """

    guest_confirmation_rows = ""

    for member in members:

        guest_confirmation_rows += f"""
        <tr style="background-color: {tentative_response_color(safe_text(member['tentative_response_status']))};">
            <td>{safe_text(member['primary_name'])}</td>
            <td>{safe_text(member['primary_email'])}</td>
            <td>{tentative_response_display(safe_text(member['tentative_response_status']))}</td>
            <td>{safe_text(member['tentative_response_at'])}</td>
            <td>{safe_text(member['tentative_response_notes'])}</td>
            <td>
                <a href="/coordination-group-member/{coordination_member_row_id(member)}/request">
                    Guest Page
                </a>
            </td>
        </tr>
        """

    if not guest_confirmation_rows:

        guest_confirmation_rows = """
        <tr>
            <td colspan="6">No group members yet.</td>
        </tr>
        """

    booking_rows_html = ""

    for created_request in created_booking_request_rows:

        assigned_rooms = safe_text(created_request["approved_room_names"])

        if not assigned_rooms:
            assigned_rooms = "Not Assigned"

        booking_rows_html += f"""
        <tr>
            <td>{safe_text(created_request['primary_name'])}</td>
            <td>{safe_text(created_request['primary_email'])}</td>
            <td>{safe_text(created_request['additional_names']) or 'None listed'}</td>
            <td>
                <a href="/request/{created_request['converted_request_id']}">
                    Request #{created_request['converted_request_id']}
                </a>
            </td>
            <td>{request_status_display(safe_text(created_request['request_status']))}</td>
            <td>{email_status_display(created_request['email_status'], created_request['email_needed_type'], created_request['converted_request_id'])}</td>
            <td align="center">{safe_text(created_request['rooms_requested'])}</td>
            <td>{assigned_rooms}</td>
            <td>
                <a href="/room-assignments">
                    Open Room Assignments
                </a>
                <br>
                <small>
                    Request #{created_request['converted_request_id']}
                </small>
            </td>
        </tr>
        """

    booking_assignment_action_html = "Booking requests have not been created yet."

    if created_booking_request_rows:

        booking_assignment_links = []

        for created_request in created_booking_request_rows:

            if created_request["converted_request_id"]:

                assigned_room_names = safe_text(
                    created_request["approved_room_names"]
                ).strip()

                request_status_value = safe_text(
                    created_request["request_status"]
                ).strip()

                if request_status_value == "approved" and assigned_room_names:

                    assignment_status = "✅ Done"
                    assignment_detail = assigned_room_names

                else:

                    assignment_status = "⚠️ Needs room assignment / approval"
                    assignment_detail = "Not complete"

                booking_assignment_links.append(
                    f"""
                    <div style="margin-bottom: 6px;">
                        <strong>{safe_text(created_request['primary_name'])}</strong>:
                        {assignment_status}
                        <br>
                        <small>{assignment_detail}</small>
                        <br>
                        <a href="/room-assignments">
                            Open Room Assignments
                        </a>
                    </div>
                    """
                )

        if booking_assignment_links:

            booking_assignment_action_html = "".join(booking_assignment_links)

    if not booking_rows_html:

        booking_rows_html = """
        <tr>
            <td colspan="9">
                Booking requests have not been created yet.
            </td>
        </tr>
        """

    final_visit_email_action_html = """
    <p style="color: #666;">
        Final visit confirmation becomes available after booking requests are approved and rooms are assigned.
    </p>
    """

    if final_visit_confirmation_sent:

        final_visit_email_action_html = f"""
        <p style="
            color: green;
            font-weight: bold;
            margin-bottom: 4px;
        ">
            Final visit confirmation email already sent.
        </p>

        <p style="
            color: #666;
            margin-top: 0;
        ">
            Sent on {safe_text(group['final_visit_confirmation_sent_at'])[:10]}.
        </p>
        """

    elif all_created_requests_reviewed:

        final_visit_email_action_html = f"""
        <p style="color: #666; margin-bottom: 8px;">
            This is different from the individual room request approval email.
            It sends one final group confirmation after all rooms are assigned and approved.
        </p>

        <form method="POST"
              action="/coordination-group/{group_id}/close"
              style="margin-bottom: 12px;"
              onsubmit="return confirm('Send final visit confirmation emails and mark this group complete?');">
            <input type="hidden" name="confirm_action" value="yes">
            <button type="submit"
                    style="
                        background-color: #198754;
                        color: white;
                        padding: 8px 12px;
                        border: 0;
                        border-radius: 5px;
                        font-weight: bold;
                    ">
                Send Final Visit Confirmation Emails
            </button>
        </form>
        """

    create_requests_action_html = """
    <p style="color: #666;">
        Booking requests can be created after guests confirm tentative dates.
    </p>
    """

    if all_confirmed_guests_converted:

        create_requests_action_html = """
        <p style="
            color: green;
            font-weight: bold;
            margin-bottom: 4px;
        ">
            Booking requests already created.
        </p>

        <p style="
            color: #666;
            margin-top: 0;
        ">
            This repeat action is blocked to avoid duplicate booking requests.
            Use the booking request links below for room assignment and approval.
        </p>
        """

    elif all_guests_confirmed:

        create_requests_action_html = f"""
        <p style="
            color: #0d6efd;
            font-weight: bold;
            margin-bottom: 4px;
        ">
            All guests confirmed. Booking requests should be created automatically.
        </p>
        <p style="color: #666; margin-top: 0;">
            If this group was confirmed before V24.2, use this safety button once.
        </p>
        <form method="POST"
              action="/coordination-group/{group_id}/convert-confirmed"
              style="margin-bottom: 12px;">
            <button type="submit"
                    style="
                        background-color: #6c757d;
                        color: white;
                        padding: 8px 12px;
                        border: 0;
                        border-radius: 5px;
                        font-weight: bold;
                    ">
                Safety: Create Missing Booking Requests
            </button>
        </form>
        """

    close_group_action_html = """
    <p style="color: #666;">
        Closing is manual and happens with the final group confirmation email below.
        It becomes available after all converted booking requests are approved and rooms are assigned.
    </p>
    """

    if group_is_closed:

        close_group_action_html = """
        <p style="color: green; font-weight: bold;">
            Group closed / finalized.
        </p>
        """

    ready_for_booking_banner_html = ""

    if safe_text(group["status"]) == "ready_for_booking":
        ready_for_booking_banner_html = f"""
        <div style="
            background-color: #d4edda;
            border: 2px solid #198754;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 18px;
            max-width: 1080px;
        ">
            <h2 style="margin: 0 0 6px 0; color: #198754;">
                Dates Work for Everyone
            </h2>
            <p style="margin: 0;">
                Planning is done. Use this page to create booking requests, assign rooms, approve requests, and send the final confirmation.
            </p>
        </div>
        """

    html = nav_links() + f"""
    <h1>{safe_text(group['title'])} — Booking Handoff</h1>

    <div style="
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        padding: 10px;
        border-radius: 8px;
        margin-bottom: 18px;
        max-width: 1080px;
    ">
        <strong>Coordination Pages:</strong>
        <a href="/coordination-group/{group_id}"
           style="font-weight: bold; margin-left: 8px;">
            Planning Page
        </a>
        |
        <a href="/coordination-group/{group_id}/handoff"
           style="font-weight: bold;">
            Booking Handoff Page
        </a>
        <br>
        <small style="color: #555;">
            Planning is for finding dates. Booking Handoff is for confirmations, booking requests, room assignments, and approvals.
        </small>
    </div>

    {ready_for_booking_banner_html}

    <div style="
        background-color: #eef5ff;
        border: 2px solid #4a90e2;
        padding: 14px;
        border-radius: 8px;
        margin-bottom: 18px;
        max-width: 1080px;
    ">
        <h2 style="margin-top: 0;">
            Booking Handoff Workflow
        </h2>

        <table border="1"
               cellpadding="6"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Step</th>
                <th align="left">Status</th>
                <th align="left">Action</th>
            </tr>
            <tr>
                <td><strong>Send tentative date confirmation emails</strong><br><small>{tentative_confirmation_email_display}</small></td>
                <td>{status_badge(tentative_email_step_state)}</td>
                <td>{tentative_confirmation_email_action_html}</td>
            </tr>
            <tr>
                <td><strong>Guest confirmations</strong><br><small>{tentative_confirmed_count} of {len(members)} confirmed</small></td>
                <td>{status_badge('done' if all_guests_confirmed else 'needs_action' if tentative_selected else 'not_started')}</td>
                <td>Use guest confirmation table below.</td>
            </tr>
            <tr>
                <td><strong>Create booking requests</strong><br><small>{converted_member_count} of {len(members)} converted</small></td>
                <td>{status_badge(conversion_step_state)}</td>
                <td>{create_requests_action_html}</td>
            </tr>
            <tr>
                <td><strong>Assign rooms and approve</strong><br><small>Review each created request.</small></td>
                <td>{status_badge(room_assignment_step_state)}</td>
                <td>{booking_assignment_action_html}</td>
            </tr>
            <tr>
                <td><strong>Final group confirmation email</strong><br><small>{final_visit_email_sent_display}</small></td>
                <td>{status_badge(final_visit_email_step_state)}</td>
                <td>{final_visit_email_action_html}</td>
            </tr>
            <tr>
                <td><strong>Close group</strong><br><small>Final step after confirmation email is sent.</small></td>
                <td>{status_badge(close_step_state)}</td>
                <td>{close_group_action_html}</td>
            </tr>
        </table>
    </div>

    {due_date_fallback_script}

    <h2>Current Status</h2>

    <div style="
        background-color: #f8f9fa;
        padding: 14px;
        border-radius: 8px;
        margin-bottom: 18px;
        max-width: 1080px;
        line-height: 1.5;
    ">
        <p style="margin-top: 0;">
            <strong>Group Status:</strong> {safe_text(group['status'])}<br>
            <strong>Tentative Dates:</strong> {tentative_dates_display}<br>
            <strong>Tentative Confirmation Email:</strong> {tentative_confirmation_email_display}<br>
            <strong>Final Group Confirmation Email:</strong> {final_visit_email_sent_display}<br>
            <strong>Individual Room Request Approval Emails:</strong> {approval_email_summary_display}<br>
            <strong>Booking Requests Created:</strong> {converted_member_count} of {len(members)}<br>
            <strong>Confirmed Works:</strong> {tentative_confirmed_count} |
            <strong>Cannot Make:</strong> {tentative_cannot_count} |
            <strong>Needs Discussion:</strong> {tentative_discussion_count} |
            <strong>No Response:</strong> {tentative_no_response_count}
        </p>
    </div>

    <h2>Guest Confirmation Status</h2>

    <table border="1"
           cellpadding="5"
           cellspacing="0"
           style="
               border-collapse: collapse;
               width: 100%;
               font-size: 13px;
               margin-bottom: 18px;
           ">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Guest</th>
            <th align="left">Email</th>
            <th align="left">Response</th>
            <th align="left">Responded At</th>
            <th align="left">Notes</th>
            <th align="left">Action</th>
        </tr>
        {guest_confirmation_rows}
    </table>

    <h2>Booking Requests / Room Assignment</h2>

    <div style="
        border: 1px solid #dee2e6;
        background-color: #ffffff;
        padding: 14px;
        margin-bottom: 18px;
        border-radius: 8px;
        max-width: 1080px;
    ">
        <p style="margin-top: 0;">
            This is where coordination becomes the normal booking workflow.
            Use the Room Assignments page to assign rooms and approve requests. The final confirmed visit email is sent when the coordination group is closed.
        </p>

        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
               ">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Guest</th>
                <th align="left">Email</th>
                <th align="left">Additional Guests</th>
                <th align="left">Request</th>
                <th align="left">Status</th>
                <th align="left">Email Status</th>
                <th align="center">Rooms Requested</th>
                <th align="left">Assigned Rooms</th>
                <th align="left">Action</th>
            </tr>
            {booking_rows_html}
        </table>
    </div>

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Planning Page
        </a>
        |
        <a href="/coordination-groups">
            Back to Coordination Groups
        </a>
    </p>
    """

    return html


@app.route("/coordination-group/<int:group_id>/email-preview")
def coordination_group_email_preview(group_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            The coordination group could not be found.
        </p>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    if safe_text(group["final_coordination_email_sent_at"]):

        conn.close()

        return f"""
        {nav_links()}

        <h1>Final Email Already Sent</h1>

        <p>
            Final coordination email was already sent for this group.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.invitation_status,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        WHERE coordination_group_members.coordination_group_id = ?

        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    draft_members = [
        member
        for member in members
        if safe_text(row_value(member, "invitation_status")).strip() in ("", "draft")
    ]

    # If new/unsent guests exist, send only to them.
    # Otherwise this action is a reminder and should send only to guests who have not responded.
    non_responder_members = [
        member
        for member in members
        if safe_text(row_value(member, "invitation_status")).strip() != "responded"
    ]

    email_target_members = draft_members if draft_members else non_responder_members

    group_date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.invitation_status,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options

        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        WHERE coordination_group_members.coordination_group_id = ?

        ORDER BY
            guest_profiles.primary_name,
            coordination_date_options.priority,
            coordination_date_options.arrival_date
    """, (
        group_id,
    )).fetchall()

    approved_bookings_for_matching = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    blocked_ranges_for_matching = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    total_rooms_for_matching = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    conn.close()

    match_suggestions = build_coordination_match_suggestions(
        group_date_options,
        approved_bookings_for_matching,
        blocked_ranges_for_matching,
        total_rooms_for_matching
    )

    member_count = len(members)

    all_member_names = []

    for member in members:

        all_member_names.append(
            safe_text(member["primary_name"])
        )

    group_member_text = "\n".join([f"- {safe_text(member['primary_name'])} ({coordination_role_display(row_value(member, 'role') or 'participant')})" for member in members])

    if not members:

        return f"""
        {nav_links()}

        <h1>No Group Members</h1>

        <p>
            This coordination group does not have any guest profiles added yet.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    group_member_text = "\n".join([f"- {safe_text(member['primary_name'])} ({coordination_role_display(row_value(member, 'role') or 'participant')})" for member in members])

    suggestion_lines = []

    if match_suggestions:

        option_number = 1

        for suggestion in match_suggestions[:2]:

            unmatched_names = []

            for member_name in all_member_names:

                if member_name not in suggestion["guest_names"]:

                    unmatched_names.append(member_name)

            if unmatched_names:

                unmatched_display = ", ".join(sorted(unmatched_names))

            else:

                unmatched_display = "None"

            matched_display = ", ".join(suggestion["guest_names"])

            capacity_display = "Capacity looks OK"

            if not suggestion["capacity_ok"]:

                capacity_display = "Capacity issue: " + "; ".join(suggestion["capacity_notes"])

            suggestion_lines.append(
                f"Option {option_number}:\n"
                f"{format_date(suggestion['arrival_date'])} to {format_date(suggestion['departure_date'])}\n"
                f"Nights: {suggestion['nights']}\n"
                f"Matches: {matched_display}\n"
                f"Still unmatched: {unmatched_display}\n"
                f"Rooms needed: {suggestion['rooms_needed']}\n"
                f"{capacity_display}"
            )

            option_number += 1

    else:

        suggestion_lines.append(
            "No group overlap suggestion is available yet. Please submit or update your date options."
        )

    suggestion_text = "\n\n".join(suggestion_lines)

    organizer_conn = get_db_connection()
    ensure_coordination_tables(organizer_conn)
    organizer_info = get_coordination_organizer_info(organizer_conn, group_id)
    organizer_conn.close()

    organizer_email_display = ""
    if organizer_info["email"]:
        organizer_email_display = "<" + organizer_info["email"] + ">"

    email_preview_html = ""

    for member in email_target_members:

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request"

        subject = f"Strathmere group date coordination - {safe_text(group['title'])}"

        body = render_email_template(
            "coordination_invitation.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            guest_role=coordination_role_display(row_value(member, "role") or "participant"),
            organizer_name=organizer_info["name"],
            organizer_email=organizer_info["email"],
            organizer_email_display=organizer_email_display,
            group_member_text=group_member_text,
            suggestion_text=suggestion_text,
            request_link=update_link
        )

        email_preview_html += f"""
        <div style="
            border: 1px solid #dee2e6;
            background-color: #ffffff;
            padding: 12px;
            margin-bottom: 16px;
            border-radius: 8px;
            max-width: 900px;
        ">
            <p style="margin-top: 0;">
                <strong>To:</strong> {safe_text(member['primary_name'])} &lt;{safe_text(member['primary_email'])}&gt;<br>
                <strong>Subject:</strong> {safe_text(subject)}
            </p>

            <pre style="
                white-space: pre-wrap;
                background-color: #f8f9fa;
                padding: 10px;
                border: 1px solid #dee2e6;
                font-size: 13px;
            ">{safe_text(body)}</pre>
        </div>
        """

    template_metadata = email_template_metadata_html("coordination_invitation")

    html = nav_links() + f"""
    <h1>Preview Coordination Invitation / Update Email</h1>

    {template_metadata}

    <p>
        <strong>Group:</strong> {safe_text(group['title'])}
    </p>

    <p>
        This will send one email to each unsent/new group member when any exist. If no unsent/new members exist, it sends to the full group.
        It does not approve, book, or change any stay.
    </p>

    <form method="POST"
          action="/coordination-group/{group_id}/send-update-email"
          onsubmit="return confirm('Send this coordination invitation/update to the listed guest(s)?');">

        <button type="submit"
                style="
                    background-color: #198754;
                    color: white;
                    padding: 8px 12px;
                    border: none;
                    border-radius: 5px;
                    font-weight: bold;
                ">
            Send Coordination Invitation / Update Email
        </button>

    </form>

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Planning Page
        </a>
    </p>

    <hr>

    {email_preview_html}
    """

    return html



@app.route("/coordination-group/<int:group_id>/follow-up-unmatched")
def coordination_group_follow_up_unmatched_preview(group_id):

    arrival_date = clean_text(request.args.get("arrival_date"))
    departure_date = clean_text(request.args.get("departure_date"))

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if not group:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Coordination Group Not Found</h1>
        <p><a href="/coordination-groups">Back to Coordination Groups</a></p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (group_id,)).fetchall()

    date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options
        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
    """, (group_id,)).fetchall()

    approved_bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    ensure_house_block_columns(conn)

    blocked_ranges = conn.execute("""
        SELECT *
        FROM blocked_dates
    """).fetchall()

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    conn.close()

    match_suggestions = build_coordination_match_suggestions(
        date_options,
        approved_bookings,
        blocked_ranges,
        total_rooms
    )

    selected_suggestion = None

    for suggestion in match_suggestions:
        if suggestion["arrival_date"] == arrival_date and suggestion["departure_date"] == departure_date:
            selected_suggestion = suggestion
            break

    if not selected_suggestion:
        return f"""
        {nav_links()}
        <h1>Follow-Up Not Available</h1>
        <p>The selected match option could not be found. Please return to the Planning page and choose a current option.</p>
        <p><a href="/coordination-group/{group_id}">Back to Planning Page</a></p>
        """

    matched_names = set(selected_suggestion["guest_names"])
    unmatched_members = []

    for member in members:
        if safe_text(member["primary_name"]) not in matched_names:
            unmatched_members.append(member)

    if not unmatched_members:
        return f"""
        {nav_links()}
        <h1>No Follow-Up Needed</h1>
        <p>All current group members match this option.</p>
        <p><a href="/coordination-group/{group_id}">Back to Planning Page</a></p>
        """

    previews_html = ""

    for member in unmatched_members:
        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request?follow_up=1&suggested_arrival={arrival_date}&suggested_departure={departure_date}"
        subject = f"Strathmere group dates - can you update your availability?"
        body = f"""Hi {safe_text(member['primary_name'])},

We found a possible group date for {safe_text(group['title'])}:

{format_date(arrival_date)} to {format_date(departure_date)}

Right now, your submitted dates do not overlap with this option.

Could you please use the link below to review your dates and either:
- add a new date option,
- increase your flexibility, or
- let us know if these dates will not work for you.

Your update link:
{update_link}

Thanks!

John & Mark
302-521-5401
"""
        previews_html += f"""
        <div style="border: 1px solid #dee2e6; border-radius: 8px; padding: 10px; margin-bottom: 12px; max-width: 900px;">
            <p style="margin-top: 0;">
                <strong>To:</strong> {safe_text(member['primary_name'])} &lt;{safe_text(member['primary_email'])}&gt;<br>
                <strong>Subject:</strong> {safe_text(subject)}
            </p>
            <pre style="white-space: pre-wrap; background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px;">{safe_text(body)}</pre>
        </div>
        """

    return f"""
    {nav_links()}

    <h1>Preview Follow-Up To Unmatched Guest(s)</h1>

    {email_template_metadata_html("coordination_follow_up")}

    <p>
        <strong>Group:</strong> {safe_text(group['title'])}<br>
        <strong>Best group option:</strong> {format_date(arrival_date)} to {format_date(departure_date)}
    </p>

    <p>
        This sends only to the guest(s) who do not match the selected option.
    </p>

    <form method="POST" action="/coordination-group/{group_id}/send-follow-up-unmatched">
        <input type="hidden" name="arrival_date" value="{arrival_date}">
        <input type="hidden" name="departure_date" value="{departure_date}">
        <button type="submit" style="font-weight: bold; padding: 7px 12px;">
            Send Follow-Up To Unmatched Guest(s)
        </button>
        &nbsp;
        <a href="/coordination-group/{group_id}">Cancel / Back</a>
    </form>

    <hr>

    {previews_html}
    """


@app.route("/coordination-group/<int:group_id>/send-follow-up-unmatched", methods=["POST"])
def coordination_group_send_follow_up_unmatched(group_id):

    arrival_date = clean_text(request.form.get("arrival_date"))
    departure_date = clean_text(request.form.get("departure_date"))

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (group_id,)).fetchall()

    date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options
        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
    """, (group_id,)).fetchall()

    approved_bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    ensure_house_block_columns(conn)

    blocked_ranges = conn.execute("""
        SELECT *
        FROM blocked_dates
    """).fetchall()

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    conn.close()

    if not group:
        return f"""
        {nav_links()}
        <h1>Coordination Group Not Found</h1>
        <p><a href="/coordination-groups">Back to Coordination Groups</a></p>
        """

    match_suggestions = build_coordination_match_suggestions(
        date_options,
        approved_bookings,
        blocked_ranges,
        total_rooms
    )

    selected_suggestion = None

    for suggestion in match_suggestions:
        if suggestion["arrival_date"] == arrival_date and suggestion["departure_date"] == departure_date:
            selected_suggestion = suggestion
            break

    if not selected_suggestion:
        return transaction_error_page(
            "Selected match option was not found.",
            f"/coordination-group/{group_id}"
        )

    matched_names = set(selected_suggestion["guest_names"])
    sent_count = 0
    skipped_members = []
    sent_member_ids = []

    current_round = coordination_round_number(group)
    next_round = current_round + 1

    for member in members:
        if safe_text(member["primary_name"]) in matched_names:
            continue

        recipient_email = safe_text(member["primary_email"]).strip()

        if not is_valid_email_address(recipient_email):
            skipped_members.append(safe_text(member["primary_name"]))
            continue

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request?follow_up=1&suggested_arrival={arrival_date}&suggested_departure={departure_date}"
        subject = "Strathmere group dates - can you update your availability?"
        body = render_email_template(
            "coordination_unmatched_follow_up.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            suggested_dates=f"{format_date(arrival_date)} to {format_date(departure_date)}",
            request_link=update_link
        )

        try:
            send_email(recipient_email, subject, body)
            sent_count += 1
            sent_member_ids.append(coordination_member_row_id(member))
        except Exception as error:
            return transaction_error_page(error, f"/coordination-group/{group_id}")

    if sent_member_ids:

        update_conn = get_db_connection()
        ensure_coordination_tables(update_conn)

        try:

            create_database_backup(
                "before_coordination_round_follow_up"
            )

            update_conn.execute("""
                UPDATE coordination_groups
                SET current_round = ?,
                    current_round_started_at = CURRENT_TIMESTAMP,
                    round_status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                next_round,
                "follow_up",
                group_id
            ))

            for sent_member_id in sent_member_ids:

                update_conn.execute("""
                    UPDATE coordination_group_members
                    SET follow_up_round = ?,
                        follow_up_sent_at = CURRENT_TIMESTAMP,
                        follow_up_response_at = NULL,
                        follow_up_suggested_arrival = ?,
                        follow_up_suggested_departure = ?
                    WHERE id = ?
                """, (
                    next_round,
                    arrival_date,
                    departure_date,
                    sent_member_id
                ))

            update_conn.commit()

        except Exception as error:

            rollback_and_close(update_conn)

            return transaction_error_page(
                error,
                f"/coordination-group/{group_id}"
            )

        update_conn.close()

    skipped_html = ""

    if skipped_members:
        skipped_html = f"""
        <p style="color: red;">
            <strong>Skipped invalid email(s):</strong><br>
            {safe_text(', '.join(skipped_members))}
        </p>
        """

    return f"""
    {nav_links()}

    <h1>Round Follow-Up Sent</h1>

    <p style="color: green; font-weight: bold;">
        Started Round {next_round} and sent {sent_count} follow-up email(s) to unmatched guest(s).
    </p>

    {skipped_html}

    <p>
        <a href="/coordination-group/{group_id}">Back to Planning Page</a>
    </p>
    """



@app.route("/coordination-group/<int:group_id>/capacity-email", methods=["POST"])
def coordination_group_capacity_email(group_id):

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    if not group:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Coordination Group Not Found</h1>
        <p><a href="/coordination-groups">Back to Coordination Groups</a></p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (group_id,)).fetchall()

    date_options = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            guest_profiles.primary_name,
            coordination_date_options.priority,
            coordination_date_options.arrival_date,
            coordination_date_options.departure_date,
            coordination_date_options.rooms_requested
        FROM coordination_date_options
        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name,
            CASE coordination_date_options.priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END
    """, (group_id,)).fetchall()

    summary_lines = []

    for option in date_options:
        summary_lines.append(
            safe_text(option["primary_name"]) + " - " +
            safe_text(option["priority"]).title() + ": " +
            format_date(option["arrival_date"]) + " to " +
            format_date(option["departure_date"]) + " / " +
            safe_text(option["rooms_requested"] or 1) + " bedroom(s)"
        )

    if not summary_lines:
        summary_lines.append("No date options have been submitted yet.")

    summary_text = "\n".join(summary_lines)

    sent_count = 0
    skipped = []

    for member in members:
        recipient = safe_text(member["primary_email"]).strip()

        if not is_valid_email_address(recipient):
            skipped.append(safe_text(member["primary_name"]))
            continue

        request_link = BASE_URL.rstrip("/") + f"/coordination-group-member/{member['member_id']}/request"

        body = render_email_template(
            "capacity_review.txt",
            guest_name=safe_text(member["primary_name"]),
            group_name=safe_text(group["title"]),
            group_summary=summary_text,
            request_link=request_link,
            capacity_message=(
                "The group may currently be requesting more bedrooms than are available for these dates. "
                "This email was sent to everyone in the group. Someone may have already updated their request, "
                "so changes may no longer be needed. Click below and check Action Needed to see if updates are still required."
            )
        )

        try:
            send_email(
                recipient,
                "Strathmere group visit - room capacity review",
                body
            )
            sent_count += 1
        except Exception as error:
            skipped.append(safe_text(member["primary_name"]) + " (" + safe_text(error) + ")")

    try:
        conn.execute("""
            UPDATE coordination_groups
            SET status = 'capacity_review',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (group_id,))
        conn.commit()
    except Exception:
        pass

    conn.close()

    skipped_html = ""

    if skipped:
        skipped_html = f"""
        <p><strong>Skipped / failed:</strong> {safe_text(", ".join(skipped))}</p>
        """

    return f"""
    {nav_links()}
    <h1>Capacity Review Email Sent</h1>
    <p style="color:green; font-weight:bold;">Sent {sent_count} capacity review email(s).</p>
    {skipped_html}
    <p><a href="/coordination-group/{group_id}">Back to Planning Page</a></p>
    """



@app.route("/coordination-group/<int:group_id>/send-update-email", methods=["POST"])
def coordination_group_send_update_email(group_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            The coordination group could not be found.
        </p>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.invitation_status,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        WHERE coordination_group_members.coordination_group_id = ?

        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    draft_members = [
        member
        for member in members
        if safe_text(row_value(member, "invitation_status")).strip() in ("", "draft")
    ]

    # If new/unsent guests exist, send only to them.
    # Otherwise this action is a reminder and should send only to guests who have not responded.
    non_responder_members = [
        member
        for member in members
        if safe_text(row_value(member, "invitation_status")).strip() != "responded"
    ]

    email_target_members = draft_members if draft_members else non_responder_members

    group_date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options

        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        WHERE coordination_group_members.coordination_group_id = ?

        ORDER BY
            guest_profiles.primary_name,
            coordination_date_options.priority,
            coordination_date_options.arrival_date
    """, (
        group_id,
    )).fetchall()

    approved_bookings_for_matching = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    blocked_ranges_for_matching = conn.execute("""
        SELECT *
        FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    total_rooms_for_matching = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    conn.close()

    match_suggestions = build_coordination_match_suggestions(
        group_date_options,
        approved_bookings_for_matching,
        blocked_ranges_for_matching,
        total_rooms_for_matching
    )

    all_member_names = []

    for member in members:

        all_member_names.append(
            safe_text(member["primary_name"])
        )

    group_member_text = "\n".join(
        [f"- {safe_text(member['primary_name'])} ({coordination_role_display(row_value(member, 'role') or 'participant')})" for member in members]
    )

    if not group_member_text:
        group_member_text = "No group members listed."

    suggestion_lines = []

    if match_suggestions:

        option_number = 1

        for suggestion in match_suggestions[:2]:

            unmatched_names = []

            for member_name in all_member_names:

                if member_name not in suggestion["guest_names"]:

                    unmatched_names.append(member_name)

            if unmatched_names:

                unmatched_display = ", ".join(sorted(unmatched_names))

            else:

                unmatched_display = "None"

            matched_display = ", ".join(suggestion["guest_names"])

            capacity_display = "Capacity looks OK"

            if not suggestion["capacity_ok"]:

                capacity_display = "Capacity issue: " + "; ".join(suggestion["capacity_notes"])

            suggestion_lines.append(
                f"Option {option_number}:\n"
                f"{format_date(suggestion['arrival_date'])} to {format_date(suggestion['departure_date'])}\n"
                f"Nights: {suggestion['nights']}\n"
                f"Matches: {matched_display}\n"
                f"Still unmatched: {unmatched_display}\n"
                f"Rooms needed: {suggestion['rooms_needed']}\n"
                f"{capacity_display}"
            )

            option_number += 1

    else:

        suggestion_lines.append(
            "No group overlap suggestion is available yet. Please submit or update your date options."
        )

    suggestion_text = "\n\n".join(suggestion_lines)

    organizer_conn = get_db_connection()
    ensure_coordination_tables(organizer_conn)
    organizer_info = get_coordination_organizer_info(organizer_conn, group_id)
    organizer_conn.close()

    organizer_email_display = ""
    if organizer_info["email"]:
        organizer_email_display = "<" + organizer_info["email"] + ">"

    sent_count = 0
    sent_member_ids = []
    skipped_members = []

    for member in email_target_members:

        recipient_email = safe_text(member["primary_email"]).strip()

        if not is_valid_email_address(recipient_email):

            skipped_members.append(
                safe_text(member["primary_name"])
            )

            continue

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request"

        subject = f"Strathmere group date coordination - {safe_text(group['title'])}"

        body = render_email_template(
            "coordination_invitation.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            guest_role=coordination_role_display(row_value(member, "role") or "participant"),
            organizer_name=organizer_info["name"],
            organizer_email=organizer_info["email"],
            organizer_email_display=organizer_email_display,
            group_member_text=group_member_text,
            suggestion_text=suggestion_text,
            request_link=update_link
        )

        try:

            send_email(
                recipient_email,
                subject,
                body
            )

            sent_count += 1
            sent_member_ids.append(coordination_member_row_id(member))

        except Exception as error:

            return transaction_error_page(
                error,
                f"/coordination-group/{group_id}/email-preview"
            )

    if sent_member_ids:

        status_conn = get_db_connection()
        ensure_coordination_tables(status_conn)

        for sent_member_id in sent_member_ids:

            status_conn.execute("""
                UPDATE coordination_group_members
                SET invitation_status = CASE
                    WHEN invitation_status = 'responded' THEN invitation_status
                    ELSE 'sent'
                END
                WHERE id = ?
            """, (
                sent_member_id,
            ))

        status_conn.commit()
        status_conn.close()

    skipped_html = ""

    if skipped_members:

        skipped_html = f"""
        <p style="color: red;">
            <strong>Skipped invalid email(s):</strong><br>
            {safe_text(', '.join(skipped_members))}
        </p>
        """

    return f"""
    {nav_links()}

    <h1>Coordination Invitation / Update Sent</h1>

    <p style="
        color: green;
        font-weight: bold;
    ">
        Sent {sent_count} coordination invitation/update email(s).
    </p>

    {skipped_html}

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Planning Page
        </a>
    </p>
    """


@app.route("/coordination-group-member/<int:member_id>/request")
def coordination_group_member_request(member_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    member = conn.execute("""
        SELECT
            coordination_group_members.*,
            coordination_groups.title AS group_title,
            coordination_groups.description AS group_description,
            coordination_groups.status AS group_status,
            coordination_groups.current_round AS current_round,
            coordination_groups.tentative_arrival_date AS tentative_arrival_date,
            coordination_groups.tentative_departure_date AS tentative_departure_date,
            coordination_groups.tentative_selected_at AS tentative_selected_at,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            guest_profiles.additional_names,
            guest_profiles.pet_notes,
            guest_profiles.food_notes,
            guest_profiles.status AS profile_status
        FROM coordination_group_members
        JOIN coordination_groups
            ON coordination_group_members.coordination_group_id = coordination_groups.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.id = ?
    """, (
        member_id,
    )).fetchone()

    follow_up_mode = clean_text(request.args.get("follow_up")) == "1"
    suggested_arrival = clean_text(request.args.get("suggested_arrival"))
    suggested_departure = clean_text(request.args.get("suggested_departure"))

    if not member:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Request Link Not Found</h1>

        <p>
            This coordination request link could not be found.
        </p>

        <p>
            Please contact John or Mark if you need help.
        </p>
        """

    if member["invitation_status"] == "draft":

        conn.execute("""
            UPDATE coordination_group_members
            SET invitation_status = ?
            WHERE id = ?
        """, (
            "viewed",
            member_id
        ))

        conn.commit()

    selected_year = int(request.args.get("year", date.today().year))
    selected_month = int(request.args.get("month", date.today().month))

    if selected_month < 1:
        selected_month = 12
        selected_year -= 1

    if selected_month > 12:
        selected_month = 1
        selected_year += 1

    ensure_house_block_columns(conn)

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    bookings = conn.execute("""
        SELECT arrival_date, departure_date
        FROM bookings
        WHERE status = 'approved'
    """).fetchall()

    previous_bookings = conn.execute("""
        SELECT
            booking_requests.id AS request_id,
            booking_requests.arrival_date,
            booking_requests.departure_date,
            booking_requests.rooms_requested,
            rooms.name AS room_name
        FROM booking_requests
        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'
        LEFT JOIN rooms
            ON bookings.room_id = rooms.id
        WHERE booking_requests.guest_profile_id = ?
          AND booking_requests.status = 'approved'
        ORDER BY
            booking_requests.arrival_date DESC,
            rooms.name
    """, (
        member["guest_profile_id"],
    )).fetchall()

    total_rooms = conn.execute(
        "SELECT COUNT(*) AS count FROM rooms"
    ).fetchone()["count"]

    saved_date_options = conn.execute("""
        SELECT *
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
        ORDER BY
            CASE priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END,
            arrival_date,
            departure_date
    """, (
        member_id,
    )).fetchall()

    group_date_options = conn.execute("""
        SELECT
            coordination_date_options.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_date_options
        JOIN coordination_group_members
            ON coordination_date_options.coordination_group_member_id = coordination_group_members.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY
            coordination_date_options.arrival_date,
            coordination_date_options.departure_date,
            CASE coordination_date_options.priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END,
            guest_profiles.primary_name
    """, (
        member["coordination_group_id"],
    )).fetchall()

    group_members_for_overlap = conn.execute("""
        SELECT
            coordination_group_members.id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (
        member["coordination_group_id"],
    )).fetchall()

    group_member_list_html = ""

    for group_member in group_members_for_overlap:

        group_member_list_html += f"""
        <li>
            {safe_text(group_member['primary_name'])}
            {coordination_role_badge(group_member['role'])}
            <small style="color: #666;">
                ({safe_text(group_member['primary_email'])})
            </small>
        </li>
        """

    if not group_member_list_html:
        group_member_list_html = "<li>No group members listed yet.</li>"

    other_group_rooms_total = 0

    for group_member in group_members_for_overlap:

        if group_member["id"] == member_id:
            continue

        member_room_count = 1

        for option in group_date_options:

            if option["member_id"] == group_member["id"]:

                try:
                    option_rooms = int(option["rooms_requested"] or 1)
                except:
                    option_rooms = 1

                if option_rooms > member_room_count:
                    member_room_count = option_rooms

        other_group_rooms_total += member_room_count

    conn.close()

    blocked_dates, room_limit_by_date = build_blocked_date_capacity(
        blocked,
        total_rooms
    )

    blocked_list = sorted(blocked_dates)

    first_day = date(selected_year, selected_month, 1)

    if selected_month == 12:
        next_month_date = date(selected_year + 1, 1, 1)
    else:
        next_month_date = date(selected_year, selected_month + 1, 1)

    previous_month = selected_month - 1
    previous_year = selected_year

    if previous_month < 1:
        previous_month = 12
        previous_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:
        next_month = 1
        next_year += 1

    days_in_month = (next_month_date - first_day).days
    start_weekday = (first_day.weekday() + 1) % 7
    month_title = first_day.strftime("%B %Y")

    room_capacity = {}

    current = first_day

    while current < next_month_date:
        rooms_used = 0

        for booking in bookings:
            booking_start = datetime.strptime(
                booking["arrival_date"],
                "%Y-%m-%d"
            ).date()

            booking_end = datetime.strptime(
                booking["departure_date"],
                "%Y-%m-%d"
            ).date()

            if booking_start <= current < booking_end:
                rooms_used += 1

        current_date_key = current.strftime("%Y-%m-%d")
        capacity_limit = room_capacity_limit_for_date(
            room_limit_by_date,
            current_date_key,
            total_rooms
        )

        room_capacity[current_date_key] = max(
            0,
            capacity_limit - rooms_used
        )

        current += timedelta(days=1)

    calendar_html = f"""
    <h2 id="calendar-section" style="margin: 0 0 4px 0;">Choose Dates - {month_title}</h2>

    <p style="margin: 0 0 6px 0; font-size: 13px;">
        <a href="/coordination-group-member/{member_id}/request?year={previous_year}&month={previous_month}#calendar-section">Previous</a>
        |
        <strong>{month_title}</strong>
        |
        <a href="/coordination-group-member/{member_id}/request?year={next_year}&month={next_month}#calendar-section">Next</a>
    </p>

    <table border="1" cellpadding="1" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 520px;">
        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
        <tr>
    """

    for _ in range(start_weekday):
        calendar_html += "<td></td>"

    day_counter = start_weekday

    for day in range(1, days_in_month + 1):
        current_date = date(selected_year, selected_month, day)
        current_date_str = current_date.strftime("%Y-%m-%d")

        today = date.today()
        past_date = current_date < today

        rooms_open = room_capacity.get(current_date_str, total_rooms)

        if past_date:
            background = "#e9ecef"
            display_line_1 = ""
            display_line_2 = "Past"
            click_handler = ""
            cursor = "not-allowed"

        elif current_date_str in blocked_dates:
            background = "#f8d7da"
            display_line_1 = ""
            display_line_2 = "Blocked"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 0:
            background = "#f8d7da"
            display_line_1 = "0 open"
            display_line_2 = "Full"
            click_handler = ""
            cursor = "not-allowed"

        elif rooms_open <= 2:
            background = "#fff3cd"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Almost Full"
            click_handler = f"onclick=\"selectCoordinationDate('{current_date_str}')\""
            cursor = "pointer"

        else:
            background = "#d4edda"
            display_line_1 = f"{rooms_open} open"
            display_line_2 = "Open"
            click_handler = f"onclick=\"selectCoordinationDate('{current_date_str}')\""
            cursor = "pointer"

        calendar_html += f"""
        <td {click_handler}
            data-date="{current_date_str}"
            data-rooms-open="{rooms_open}"
            style="
                background-color: {background};
                vertical-align: top;
                width: 56px;
                height: 44px;
                font-size: 12px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong><br>
            <span style="font-size: 9px; font-weight: normal; line-height: 1.05;">
                {str(rooms_open) + " ROOM" + ("" if rooms_open == 1 else "S") + " OPEN" if (not past_date and current_date_str not in blocked_dates and rooms_open > 0) else ("FULL" if (not past_date and current_date_str not in blocked_dates and rooms_open <= 0) else "")}
            </span>
        </td>
        """

        day_counter += 1

        if day_counter % 7 == 0 and day != days_in_month:
            calendar_html += "</tr><tr>"

    while day_counter % 7 != 0:
        calendar_html += "<td></td>"
        day_counter += 1

    calendar_html += """
        </tr>
    </table>

    <p style="font-size: 12px; margin: 6px 0 0 0;">
        <strong>Legend:</strong>
        <span style="background-color: #d4edda; padding: 3px;">Open</span>
        <span style="background-color: #fff3cd; padding: 3px;">Almost Full</span>
        <span style="background-color: #f8d7da; padding: 3px;">Full / Blocked</span>
        <span style="background-color: #e9ecef; padding: 3px;">Past</span>
    
        <span style="border: 2px dotted #dc3545; padding: 3px;">Tentative Group Dates</span>
    </p>
    """

    previous_html = """
    <p>No previous approved stays found for this guest.</p>
    """

    if previous_bookings:

        previous_html = """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
                   margin-top: 8px;
               ">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Dates</th>
                <th align="left">Rooms</th>
                <th align="left">Room</th>
                <th align="left">View</th>
            </tr>
        """

        for booking in previous_bookings:

            previous_html += f"""
            <tr>
                <td>
                    {format_date(booking['arrival_date'])}<br>
                    to {format_date(booking['departure_date'])}
                </td>
                <td>{booking['rooms_requested'] or 1}</td>
                <td>{display_room_name(booking['room_name'])}</td>
                <td>
                    <a href="/request/{booking['request_id']}">
                        View
                    </a>
                </td>
            </tr>
            """

        previous_html += "</table>"

    saved_options_html = """
    <p style="margin: 4px 0;">No date options have been submitted yet.</p>
    """

    if saved_date_options:

        saved_options_html = """
        <div style="
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 8px;
            margin-top: 4px;
        ">
        """

        for option in saved_date_options:

            saved_options_html += f"""
            <div style="
                border: 1px solid #dee2e6;
                background-color: #ffffff;
                padding: 8px;
                border-radius: 6px;
                font-size: 12px;
            ">
                <strong>{safe_text(option['priority']).title()} Dates</strong><br>
                {format_date(option['arrival_date'])} to {format_date(option['departure_date'])}<br>
                <small>
                    Rooms: {safe_text(option['rooms_requested'])} |
                    Flexibility: ± {safe_text(option['flexibility_days'])} day(s)
                </small>
                {('<br><small>Notes: ' + safe_text(option['notes']) + '</small>') if safe_text(option['notes']) else ''}
            </div>
            """

        saved_options_html += "</div>"

    group_member_display_by_name = {}

    for group_member in group_members_for_overlap:

        group_member_display_by_name[
            safe_text(group_member["primary_name"])
        ] = (
            f"{safe_text(group_member['primary_name'])} "
            f"({safe_text(group_member['primary_email'])})"
        )

    def display_group_member_name(name):

        name = safe_text(name)

        return group_member_display_by_name.get(
            name,
            name
        )

    group_match_suggestions = build_coordination_match_suggestions(
        group_date_options,
        bookings,
        blocked,
        total_rooms
    )

    group_overlap_html = """
    <p>
        No group overlap suggestions yet. Once more date options are submitted,
        this section will show the current best group date matches.
    </p>
    """

    tentative_member_block = """
        <p>No tentative group dates have been selected yet.</p>
    """

    if safe_text(member["tentative_arrival_date"]) and safe_text(member["tentative_departure_date"]):

        if follow_up_mode:
            tentative_member_block = f"""
            <p style="font-size: 16px; margin-bottom: 4px;">
                <strong>{format_date(member['tentative_arrival_date'])}</strong>
                to
                <strong>{format_date(member['tentative_departure_date'])}</strong>
            </p>
            <p style="margin-top: 0; color: #555;">
                Use the follow-up box above to say these dates work, or update your dates below.
            </p>
            """

        else:
            tentative_member_block = f"""
            <p style="font-size: 16px;">
                <strong>{format_date(member['tentative_arrival_date'])}</strong>
                to
                <strong>{format_date(member['tentative_departure_date'])}</strong>
            </p>

            <p>
                <strong>Your current response:</strong>
                {tentative_response_display(member['tentative_response_status'])}
            </p>

            <form method="POST"
                  action="/coordination-group-member/{member_id}/tentative-response">

                <button type="submit"
                        name="response_status"
                        value="confirmed"
                        style="padding: 8px 12px; margin-right: 6px;">
                    These Dates Work For Me
                </button>

                <button type="submit"
                        name="response_status"
                        value="cannot_make"
                        style="padding: 8px 12px; margin-right: 6px;">
                    These Dates Do Not Work
                </button>

                <button type="submit"
                        name="response_status"
                        value="needs_discussion"
                        style="padding: 8px 12px;">
                    Need Different Dates
                </button>

                <br>

                <label>
                    Comments / Notes
                </label><br>

                <textarea name="response_notes"
                          rows="3"
                          style="width: 420px;">{safe_text(member['tentative_response_notes'])}</textarea>
            </form>
            """

    if group_match_suggestions:

        group_overlap_html = """
        <table border="1"
               cellpadding="5"
               cellspacing="0"
               style="
                   border-collapse: collapse;
                   width: 100%;
                   font-size: 13px;
                   margin-top: 8px;
               ">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Rank</th>
                <th align="left">Dates</th>
                <th align="center">Nights</th>
                <th align="center">Group Fit</th>
                <th align="left" style="width: 150px;">Status</th>
                <th align="left">Matched Guests</th>
                <th align="left">Unmatched Guests</th>
                <th align="left">Capacity</th>
            </tr>
        """

        rank = 1

        for suggestion in group_match_suggestions[:2]:

            if not saved_date_options:

                your_status = """
                <span style='color: #6c757d; font-size: 12px;'>Submit your dates to see if this works for you</span>
                """

            elif safe_text(member["primary_name"]) in suggestion["guest_names"]:

                your_status = """
                <strong style='color: green; font-size: 12px;'>You match these date(s)</strong>
                """

            else:

                your_status = """
                <strong style='color: #b45309; font-size: 12px;'>Your submitted dates do not match this option</strong>
                """

            capacity_display = """
            <strong style='color: green;'>Capacity OK</strong>
            """

            if not suggestion["capacity_ok"]:

                capacity_display = """
                <strong style='color: red;'>Capacity issue</strong>
                """

            alternate_note = ""

            if suggestion["alternate_names"]:

                alternate_names_display = []

                for guest_name in suggestion["alternate_names"]:

                    alternate_names_display.append(
                        display_group_member_name(guest_name)
                    )

                alternate_note = f"""
                <br><small>
                    Alternate date fit: {safe_text(", ".join(alternate_names_display))}
                </small>
                """

            matched_names_display = []

            for guest_name in suggestion["guest_names"]:

                matched_names_display.append(
                    display_group_member_name(guest_name)
                )

            unmatched_names_display = []

            for group_member in group_members_for_overlap:

                group_member_name = safe_text(
                    group_member["primary_name"]
                )

                if group_member_name not in suggestion["guest_names"]:

                    unmatched_names_display.append(
                        display_group_member_name(group_member_name)
                    )

            unmatched_display = "None"

            if unmatched_names_display:

                unmatched_display = safe_text(
                    ", ".join(sorted(unmatched_names_display))
                )

            group_overlap_html += f"""
            <tr>
                <td>{rank}</td>
                <td>
                    <strong>{format_date(suggestion['arrival_date'])}</strong><br>
                    to {format_date(suggestion['departure_date'])}
                </td>
                <td align="center">{suggestion['nights']}</td>
                <td align="center">
                    {suggestion['matched_count']} of {len(group_members_for_overlap)}
                </td>
                <td>{your_status}</td>
                <td>
                    {safe_text(", ".join(matched_names_display))}
                    {alternate_note}
                </td>
                <td>{unmatched_display}</td>
                <td>{capacity_display}</td>
            </tr>
            """

            rank += 1

        group_overlap_html += "</table>"

        if len(group_match_suggestions) > 2:
            group_overlap_html += f"""
            <details style="margin-top:6px;">
                <summary style="cursor:pointer; font-weight:bold; color:#0f4c81;">
                    Show {len(group_match_suggestions) - 2} more date option(s)
                </summary>
                <p style="font-size:13px; margin:5px 0 0 0;">
                    The top 2 options are shown above to keep this page simple.
                    John and Mark can still review all options on the admin planning page.
                </p>
            </details>
            """

    follow_up_notice_html = ""
    follow_up_dates_work_button = ""

    if safe_text(member["tentative_arrival_date"]) and safe_text(member["tentative_departure_date"]) and not follow_up_mode:
        capacity_status_text = "✓ Capacity OK"
        action_instruction_text = "Please review the dates below and confirm whether they work for you."

        if safe_text(row_value(member, "status")) == "capacity_review" or safe_text(row_value(member, "group_status")) == "capacity_review" or safe_text(row_value(member, "invitation_status")) == "capacity_review":
            capacity_status_text = "⚠ Capacity Needs Your Review"
            action_instruction_text = "Please review the dates and bedrooms below before responding."

        follow_up_notice_html = f"""
        <div style="
            border: 2px solid #fd7e14;
            background-color: #fff3cd;
            padding: 8px 10px;
            margin-bottom: 6px;
            border-radius: 10px;
            max-width: 1100px;
            font-size: 16px;
            line-height: 1.2;
        ">
            <div style="font-size: 21px; font-weight: bold; color: #856404; margin-bottom: 3px;">
                ⚠ ACTION NEEDED {safe_text(row_value(member, "current_round")) or "1"}
            </div>
            <div style="margin:0;">
                <strong>Dates:</strong> Tentative Dates Posted for Review<br>
                <strong>Capacity:</strong> {capacity_status_text}<br>
                {action_instruction_text}
            </div>
        </div>
        """

    if follow_up_mode:
        suggested_dates_text = ""

        if suggested_arrival and suggested_departure:
            suggested_dates_text = f"""
            <br>Current group option being reviewed: <strong>{format_date(suggested_arrival)}</strong> to <strong>{format_date(suggested_departure)}</strong>.
            """

            follow_up_dates_work_button = f"""
            <form method="POST"
                  action="/coordination-group-member/{member_id}/follow-up-dates-work"
                  style="margin-top: 8px;">
                <input type="hidden" name="suggested_arrival" value="{suggested_arrival}">
                <input type="hidden" name="suggested_departure" value="{suggested_departure}">
                <button type="submit" style="padding: 7px 10px; font-weight: bold;">
                    These Dates Work For Me
                </button>
            </form>
            """

        follow_up_notice_html = f"""
        <div style="
            border: 2px solid #fd7e14;
            background-color: #fff3cd;
            padding: 12px 14px;
            margin-bottom: 8px;
            border-radius: 10px;
            max-width: 1100px;
            font-size: 16px;
            line-height: 1.35;
        ">
            <div style="font-size: 22px; font-weight: bold; color: #856404; margin-bottom: 4px;">
                ⚠ ACTION NEEDED {safe_text(row_value(member, "current_round")) or "1"}
            </div>
            <div style="font-size: 16px; font-weight: bold;">
                Please review the current group date option.
            </div>
            <div style="margin-top: 4px;">
                {suggested_dates_text}
            </div>
            <div style="margin-top: 6px;">
                If these dates work, click <strong>These Dates Work For Me</strong>.
                If not, update your dates below or use <strong>I cannot change any dates</strong>.
            </div>
            {follow_up_dates_work_button}
        </div>
        """

    html = nav_links() + f"""
    <style>
        .shore-admin-nav {{ font-size: 13px; }}
        input, select, textarea, button {{ font-size: 14px; }}
        p {{ line-height: 1.28; }}
        label {{ line-height: 1.25; }}
    </style>
    <h1 style="margin: 0 0 4px 0; font-size: 22px;">Pick / Update Your Dates</h1>

    {follow_up_notice_html}

    <div style="
        border: 1px solid #dee2e6;
        background-color: #f8f9fa;
        padding: 4px 7px;
        margin-bottom: 4px;
        border-radius: 8px;
        max-width: 1100px;
    ">
        <h2 style="
            margin: 0 0 3px 0;
            font-size: 18px;
        ">
            {safe_text(member['group_title'])}
        </h2>

        <p style="
            margin: 0;
            font-size: 13px;
            line-height: 1.25;
        ">
            {safe_text(member['group_description'])}
        </p>
    </div>

    <details style="
        border: 1px solid #dee2e6;
        background-color: #ffffff;
        padding: 6px 8px;
        margin-bottom: 5px;
        border-radius: 8px;
        max-width: 1100px;
        font-size: 13px;
    ">
        <summary style="cursor:pointer; font-weight:bold;">Group Members ({len(group_members_for_overlap)})</summary>
        <ul style="margin: 5px 0 0 16px; font-size: 13px; line-height: 1.25;">
            {group_member_list_html}
        </ul>
    </details>

    <div style="
        border: 1px solid #198754;
        background-color: #e8f7ea;
        padding: 3px 6px;
        margin-bottom: 4px;
        border-radius: 8px;
        max-width: 1100px;
    ">
        <h2 style="margin: 0 0 2px 0; font-size: 15px;">
            Tentative Dates That May Work for Everyone
        </h2>

        {tentative_member_block}
    </div>

    <div style="
        border: 1px solid #dee2e6;
        background-color: #eef7ee;
        padding: 6px 8px;
        margin-bottom: 6px;
        border-radius: 8px;
        max-width: 1100px;
    ">
        <h2 style="margin: 0 0 3px 0; font-size: 17px;">
            Suggested Dates Based on Other Guest Responses
        </h2>

        <p style="margin: 0 0 5px 0; font-size: 13px; line-height: 1.25;">
            These dates appear to work best for the group so far. Select one of these dates if it works for you, or choose dates close to these options to improve the chance of everyone being able to visit together. These are not confirmed bookings.
        </p>

        {group_overlap_html}
    </div>

    <div style="
        display: grid;
        grid-template-columns: minmax(440px, 1.1fr) minmax(340px, 0.9fr);
        gap: 10px;
        align-items: start;
        max-width: 1180px;
    ">
        <div style="
            border: 1px solid #dee2e6;
            background-color: #ffffff;
            padding: 8px;
            border-radius: 8px;
        ">
            {calendar_html}

            <div style="border:1px solid #dee2e6; background:#f8f9fa; border-radius:6px; padding:6px; margin:8px 0 0 0; font-size:13px;">
                <h3 style="margin:0 0 4px 0; font-size:14px;">Previous Approved Stays</h3>
                {previous_html}
            </div>

            <div style="border:1px solid #dee2e6; background:#f8f9fa; border-radius:6px; padding:6px; margin:8px 0 0 0; font-size:13px;">
                <h3 style="margin:0 0 4px 0; font-size:14px;">Guest / Room Notes</h3>
                <p style="margin: 3px 0;"><strong>Additional Guests for Your Room(s):</strong><br>{safe_text(member['additional_names'])}</p>
                <p style="margin: 3px 0;"><strong>Pets:</strong><br>{safe_text(member['pet_notes'])}</p>
                <p style="margin: 3px 0;"><strong>Food Preferences:</strong><br>{safe_text(member['food_notes'])}</p>
            </div>

        </div>

        <div style="
            border: 1px solid #dee2e6;
            background-color: #ffffff;
            padding: 8px;
            border-radius: 8px;
            font-size: 13px;
        ">
            <h2 style="margin:0 0 4px 0; font-size:16px; font-weight:bold;">Your Selection / Save Dates</h2>

            <form method="POST"
                  action="/coordination-group-member/{member_id}/cannot-change-dates"
                  style="margin: 4px 0 8px 0;">
                <button type="submit"
                        style="
                            background-color: #6c757d;
                            color: white;
                            border: none;
                            padding: 7px 10px;
                            border-radius: 5px;
                            font-weight: bold;
                        ">
                    I cannot change any dates
                </button>
            </form>

            {saved_options_html}

            <form method="POST"
                  action="/coordination-group-member/{member_id}/clear-date-options"
                  onsubmit="return confirm('Clear all submitted dates and start over?');"
                  style="margin: 8px 0 10px 0;">
                <button type="submit">
                    Clear Dates and Start Over
                </button>
            </form>

                        <div style="
                            border: 1px solid #dee2e6;
                            background-color: #f8f9fa;
                            padding: 7px;
                            border-radius: 8px;
                            margin-top: 10px;
                        ">
                            <h2 style="margin: 0 0 4px 0; font-size:16px;">
                                Choose Dates → Save → Done
                            </h2>
            
                            <p style="margin: 0 0 4px 0; font-size: 13px;">
                                Pick your preferred dates. Alternate dates are optional but helpful.
                            </p>
            
                            <p style="color: #856404; font-weight: bold; margin: 0 0 6px 0; font-size: 13px;">
                                Save once you are done.
                            </p>
            
                            <form method="POST"
                                  action="/coordination-group-member/{member_id}/date-options"
                                  onsubmit="return validateGroupRoomCapacity();">
            
                                <div style="background-color:#fff3cd; border:1px solid #fd7e14; padding:4px 6px; border-radius:6px; margin-bottom:5px; font-size:13px; font-weight:bold;">
                                    Choose what the next calendar click should fill.
                                </div>
            
                                <div style="
                                    border: 1px solid #dee2e6;
                                    background-color: #ffffff;
                                    padding: 6px;
                                    margin-bottom: 8px;
                                    border-radius: 6px;
                                    font-size: 13px;
                                ">
                                    <strong>Calendar click fills:</strong>
                                    <label>
                                        <input type="radio"
                                               name="calendar_target"
                                               value="preferred_arrival"
                                               checked>
                                        Preferred Arrival
                                    </label>
                                    <label>
                                        <input type="radio"
                                               name="calendar_target"
                                               value="preferred_departure">
                                        Preferred Departure
                                    </label>
                                    <label>
                                        <input type="radio"
                                               name="calendar_target"
                                               value="alternate_arrival">
                                        Alternate Arrival
                                    </label>
                                    <label>
                                        <input type="radio"
                                               name="calendar_target"
                                               value="alternate_departure">
                                        Alternate Departure
                                    </label>
            
                                    <span id="calendar_target_message"
                                          style="
                                              color: #0d6efd;
                                              font-weight: bold;
                                              margin-left: 6px;
                                          ">
                                        Next click will set Preferred Arrival.
                                    </span>
                                </div>
            
                                <div style="
                                    border: 1px solid #0d6efd;
                                    background-color: #f8fbff;
                                    padding: 6px;
                                    border-radius: 8px;
                                    margin-bottom: 6px;
                                    max-width: 420px;
                                ">
                                    <label for="default_rooms" style="font-size: 14px; font-weight: bold;">
                                        Bedrooms needed
                                    </label>
                                    <span style="font-size: 12px; color:#555;">— sleeps up to 2 per room</span><br>
                                    <select id="default_rooms"
                                            onchange="syncDefaultRooms(); validateGroupRoomCapacity(false);">
                                        <option value="1">1 Bedroom</option>
                                        <option value="2">2 Bedrooms</option>
                                        <option value="3">3 Bedrooms</option>
                                        <option value="4">4 Bedrooms</option>
                                    </select>
                                    <p style="font-size: 12px; color: #555; margin: 6px 0 0 0;">
                                        Applies to preferred and alternate dates. Adjust below if needed. The calendar limits choices to visible availability.
                                    </p>
                                </div>
            
                                <div style="
                                    display: grid;
                                    grid-template-columns: repeat(2, minmax(180px, 1fr));
                                    gap: 8px;
                                ">
                                    <div style="
                                        border: 1px solid #dee2e6;
                                        background-color: #ffffff;
                                        padding: 8px;
                                        border-radius: 8px;
                                    ">
                                        <h3 style="margin: 0 0 4px 0;">
                                            Your Current Preferred Dates
                                        </h3>
            
                                        <label><strong>Preferred Arrival</strong></label><br>
                                        <input type="date"
                                               id="preferred_arrival"
                                               name="preferred_arrival"
                                               onchange="setNextDayDeparture('preferred');"
                                               required>
                                        <br>
            
                                        <label><strong>Preferred Departure</strong></label><br>
                                        <input type="date"
                                               id="preferred_departure"
                                               name="preferred_departure"
                                               required>
                                        <br>
            
                                        <label><strong>Number of Guest Bedrooms Needed</strong></label><br>
                                        <select name="preferred_rooms"
                                                id="preferred_rooms"
                                                onchange="validateGroupRoomCapacity(false);">
                                            <option value="1">1 Bedroom</option>
                                            <option value="2">2 Bedrooms</option>
                                            <option value="3">3 Bedrooms</option>
                                            <option value="4">4 Bedrooms</option>
                                        </select>
                                        <br>
            
                                        <label><strong>Flexibility</strong></label><br>
                                        <select name="preferred_flexibility">
                                            <option value="0">Fixed dates</option>
                                            <option value="1">Can shift by 1 day</option>
                                            <option value="2">Can shift by 2 days</option>
                                            <option value="3">Can shift by 3 days</option>
                                        </select>
                                    </div>
            
                                    <div style="
                                        border: 1px solid #dee2e6;
                                        background-color: #ffffff;
                                        padding: 8px;
                                        border-radius: 8px;
                                    ">
                                        <h3 style="margin: 0 0 4px 0;">
                                            Alternate Dates
                                        </h3>
            
                                        <p style="font-size: 12px; margin: 0 0 4px 0;">
                                            Optional, but helpful for finding the best group match.
                                        </p>
            
                                        <label><strong>Alternate Arrival</strong></label><br>
                                        <input type="date"
                                               id="alternate_arrival"
                                               name="alternate_arrival"
                                               onchange="setNextDayDeparture('alternate');">
                                        <br>
            
                                        <label><strong>Alternate Departure</strong></label><br>
                                        <input type="date"
                                               id="alternate_departure"
                                               name="alternate_departure">
                                        <br>
            
                                        <label><strong>Number of Guest Bedrooms Needed</strong></label><br>
                                        <select name="alternate_rooms"
                                                id="alternate_rooms"
                                                onchange="validateGroupRoomCapacity(false);">
                                            <option value="1">1 Bedroom</option>
                                            <option value="2">2 Bedrooms</option>
                                            <option value="3">3 Bedrooms</option>
                                            <option value="4">4 Bedrooms</option>
                                        </select>
                                        <br>
            
                                        <label><strong>Alternate Flexibility</strong></label><br>
                                        <select name="alternate_flexibility">
                                            <option value="0">Fixed dates</option>
                                            <option value="1">Can shift by 1 day</option>
                                            <option value="2">Can shift by 2 days</option>
                                            <option value="3">Can shift by 3 days</option>
                                        </select>
                                    </div>
                                </div>
            
                                <div id="room_capacity_warning"
                                     style="
                                         display: none;
                                         background-color: #f8d7da;
                                         border: 1px solid #dc3545;
                                         padding: 6px;
                                         border-radius: 6px;
                                         margin-top: 8px;
                                         font-weight: bold;
                                         color: #842029;
                                     ">
                                </div>
            
                                <div style="margin-top: 8px;">
                                    <label><strong>Notes</strong></label><br>
                                    <textarea name="notes"
                                              rows="2"
                                              style="width: 100%; max-width: 520px;"></textarea>
                                </div>
            
                                <div style="margin-top: 8px;">
                                    <button type="submit">
            Save My Dates
                                    </button>
                                </div>
                            </form>
                        </div>
            
        </div>
    </div>

    <script>
        const blockedDates = {blocked_list};
        const roomCapacity = {room_capacity};
        const totalRooms = {total_rooms};

        function getCalendarTarget() {{
            const selected = document.querySelector('input[name="calendar_target"]:checked');

            if (!selected) {{
                return "preferred_arrival";
            }}

            return selected.value;
        }}

        function setCalendarTarget(value) {{
            const target = document.querySelector(
                'input[name="calendar_target"][value="' + value + '"]'
            );

            if (target) {{
                target.checked = true;
            }}

            updateCalendarTargetMessage();
        }}

        function targetLabel(value) {{
            if (value === "preferred_arrival") {{
                return "Preferred Arrival";
            }}

            if (value === "preferred_departure") {{
                return "Preferred Departure";
            }}

            if (value === "alternate_arrival") {{
                return "Alternate Arrival";
            }}

            if (value === "alternate_departure") {{
                return "Alternate Departure";
            }}

            return value;
        }}

        function updateCalendarTargetMessage() {{
            const message = document.getElementById("calendar_target_message");

            if (!message) {{
                return;
            }}

            message.innerText = "Next click will set " + targetLabel(getCalendarTarget()) + ".";
        }}

        function clearCoordinationCalendarHighlights() {{
            document.querySelectorAll('[data-date]').forEach(function (cell) {{
                if (cell.dataset.coordOriginalColor) {{
                    cell.style.backgroundColor = cell.dataset.coordOriginalColor;
                }}
                cell.style.outline = "";
            }});
        }}

        function highlightCoordinationSelectedDates() {{
            clearCoordinationCalendarHighlights();

            const selectedFields = [
                ["preferred_arrival", "#9ec5fe", "#0d6efd"],
                ["preferred_departure", "#b6d7a8", "#198754"],
                ["alternate_arrival", "#d7b9ff", "#6f42c1"],
                ["alternate_departure", "#ffe0a3", "#fd7e14"]
            ];

            selectedFields.forEach(function (item) {{
                const field = document.getElementById(item[0]);

                if (!field || !field.value) {{
                    return;
                }}

                const cell = document.querySelector('[data-date="' + field.value + '"]');

                if (!cell) {{
                    return;
                }}

                if (!cell.dataset.coordOriginalColor) {{
                    cell.dataset.coordOriginalColor = cell.style.backgroundColor;
                }}

                cell.style.backgroundColor = item[1];
                cell.style.outline = "3px solid " + item[2];
            }});

            const tentativeFields = [
                ["{safe_text(member['tentative_arrival_date'])}", "#ffe8a1", "#fd7e14"],
                ["{safe_text(member['tentative_departure_date'])}", "#ffe8a1", "#fd7e14"]
            ];

            tentativeFields.forEach(function (item) {{
                if (!item[0]) {{
                    return;
                }}

                const tentativeCell = document.querySelector('[data-date="' + item[0] + '"]');

                if (!tentativeCell) {{
                    return;
                }}

                if (!tentativeCell.dataset.coordOriginalColor) {{
                    tentativeCell.dataset.coordOriginalColor = tentativeCell.style.backgroundColor;
                }}

                tentativeCell.style.backgroundColor = item[1];
                tentativeCell.style.outline = "3px dashed " + item[2];
            }});
        }}

        function formatDateForMessage(dateString) {{
            const parts = dateString.split("-");
            return parts[1] + "/" + parts[2] + "/" + parts[0];
        }}

        function addOneDay(dateString) {{
            const dateValue = new Date(dateString + "T00:00:00");
            dateValue.setDate(dateValue.getDate() + 1);
            return dateValue.toISOString().slice(0, 10);
        }}

        function syncDefaultRooms() {{
            const defaultRooms = document.getElementById("default_rooms");
            const preferredRooms = document.getElementById("preferred_rooms");
            const alternateRooms = document.getElementById("alternate_rooms");

            if (!defaultRooms) {{
                return;
            }}

            if (preferredRooms) {{
                preferredRooms.value = defaultRooms.value;
            }}

            if (alternateRooms) {{
                alternateRooms.value = defaultRooms.value;
            }}
        }}

        function getRequestedRoomsForTarget(target) {{
            const roomField = document.getElementById(target + "_rooms");

            if (!roomField) {{
                return 1;
            }}

            return parseInt(roomField.value);
        }}

        function getRoomsOpen(dateString) {{
            if (roomCapacity[dateString] === undefined) {{
                return totalRooms;
            }}

            return roomCapacity[dateString];
        }}

        function setNextDayDeparture(target) {{
            const arrivalField = document.getElementById(target + "_arrival");
            const departureField = document.getElementById(target + "_departure");

            if (!arrivalField || !departureField || !arrivalField.value) {{
                return;
            }}

            const nextDay = addOneDay(arrivalField.value);

            if (!departureField.value || departureField.value <= arrivalField.value) {{
                departureField.value = nextDay;
            }}
        }}

        function selectCoordinationDate(dateString) {{
            const target = getCalendarTarget();
            const targetGroup = target.indexOf("alternate") === 0 ? "alternate" : "preferred";
            const requestedRooms = getRequestedRoomsForTarget(targetGroup);
            const roomsOpen = getRoomsOpen(dateString);

            if (blockedDates.includes(dateString)) {{
                alert(formatDateForMessage(dateString) + " is blocked.");
                return;
            }}

            if (roomsOpen < requestedRooms) {{
                alert(
                    "Only "
                    + roomsOpen
                    + " bedroom(s) available on "
                    + formatDateForMessage(dateString)
                );

                return;
            }}

            const arrivalField = document.getElementById(targetGroup + "_arrival");
            const departureField = document.getElementById(targetGroup + "_departure");

            if (!arrivalField || !departureField) {{
                return;
            }}

            if (target.indexOf("arrival") > -1) {{

                arrivalField.value = dateString;
                departureField.value = addOneDay(dateString);

                highlightCoordinationSelectedDates();

                setCalendarTarget(targetGroup + "_departure");

                return;
            }}

            if (!arrivalField.value) {{
                alert("Please choose an arrival date first.");
                setCalendarTarget(targetGroup + "_arrival");
                return;
            }}

            if (dateString <= arrivalField.value) {{
                alert("Departure date must be after arrival date.");
                return;
            }}

            departureField.value = dateString;
            highlightCoordinationSelectedDates();
            updateCalendarTargetMessage();
        }}

        document.addEventListener("DOMContentLoaded", function () {{
            document.querySelectorAll("#preferred_arrival, #preferred_departure, #alternate_arrival, #alternate_departure").forEach(function (field) {{
                field.addEventListener("change", highlightCoordinationSelectedDates);
            }});

            highlightCoordinationSelectedDates();
        }});
    </script>
    """

    return html


@app.route("/coordination-group-member/<int:member_id>/date-options", methods=["POST"])
def coordination_group_member_date_options(member_id):

    preferred_arrival = clean_text(
        request.form.get("preferred_arrival")
    )

    preferred_departure = clean_text(
        request.form.get("preferred_departure")
    )

    preferred_rooms = request.form.get("preferred_rooms") or "1"

    preferred_flexibility = request.form.get("preferred_flexibility") or "0"

    alternate_arrival = clean_text(
        request.form.get("alternate_arrival")
    )

    alternate_departure = clean_text(
        request.form.get("alternate_departure")
    )

    alternate_rooms = request.form.get("alternate_rooms") or preferred_rooms

    alternate_flexibility = request.form.get("alternate_flexibility") or "0"

    notes = clean_text(
        request.form.get("notes")
    )

    try:
        preferred_arrival_date = datetime.strptime(
            preferred_arrival,
            "%Y-%m-%d"
        )

        preferred_departure_date = datetime.strptime(
            preferred_departure,
            "%Y-%m-%d"
        )

    except:

        return f"""
        {nav_links()}

        <h1>Date Options Not Saved</h1>

        <p style="
            color: red;
            font-weight: bold;
        ">
            Please enter valid preferred arrival and departure dates.
        </p>

        <p>
            <a href="/coordination-group-member/{member_id}/request">
                Back to Coordination Request
            </a>
        </p>
        """

    if preferred_departure_date <= preferred_arrival_date:

        return f"""
        {nav_links()}

        <h1>Date Options Not Saved</h1>

        <p style="
            color: red;
            font-weight: bold;
        ">
            Preferred departure date must be after preferred arrival date.
        </p>

        <p>
            <a href="/coordination-group-member/{member_id}/request">
                Back to Coordination Request
            </a>
        </p>
        """

    try:
        preferred_rooms = int(preferred_rooms)
    except:
        preferred_rooms = 1

    preferred_rooms = normalize_rooms_requested(
        preferred_rooms,
        4
    )

    try:
        preferred_flexibility = int(preferred_flexibility)
    except:
        preferred_flexibility = 0

    if preferred_flexibility < 0:
        preferred_flexibility = 0

    try:
        alternate_rooms = int(alternate_rooms)
    except:
        alternate_rooms = preferred_rooms

    alternate_rooms = normalize_rooms_requested(
        alternate_rooms,
        4
    )

    try:
        alternate_flexibility = int(alternate_flexibility)
    except:
        alternate_flexibility = 0

    if alternate_flexibility < 0:
        alternate_flexibility = 0

    alternate_is_complete = bool(
        alternate_arrival
        and alternate_departure
    )

    if alternate_arrival or alternate_departure:

        if not alternate_is_complete:

            return f"""
            {nav_links()}

            <h1>Date Options Not Saved</h1>

            <p style="
                color: red;
                font-weight: bold;
            ">
                Please enter both alternate arrival and alternate departure,
                or leave both alternate fields blank.
            </p>

            <p>
                <a href="/coordination-group-member/{member_id}/request">
                    Back to Coordination Request
                </a>
            </p>
            """

        try:
            alternate_arrival_date = datetime.strptime(
                alternate_arrival,
                "%Y-%m-%d"
            )

            alternate_departure_date = datetime.strptime(
                alternate_departure,
                "%Y-%m-%d"
            )

        except:

            return f"""
            {nav_links()}

            <h1>Date Options Not Saved</h1>

            <p style="
                color: red;
                font-weight: bold;
            ">
                Please enter valid alternate arrival and departure dates.
            </p>

            <p>
                <a href="/coordination-group-member/{member_id}/request">
                    Back to Coordination Request
                </a>
            </p>
            """

        if alternate_departure_date <= alternate_arrival_date:

            return f"""
            {nav_links()}

            <h1>Date Options Not Saved</h1>

            <p style="
                color: red;
                font-weight: bold;
            ">
                Alternate departure date must be after alternate arrival date.
            </p>

            <p>
                <a href="/coordination-group-member/{member_id}/request">
                    Back to Coordination Request
                </a>
            </p>
            """

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    member = conn.execute("""
        SELECT *
        FROM coordination_group_members
        WHERE id = ?
    """, (
        member_id,
    )).fetchone()

    if not member:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Member Not Found</h1>

        <p>
            This coordination member could not be found.
        </p>
        """

    total_rooms_row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()

    total_rooms = total_rooms_row["count"] if total_rooms_row else 4

    current_member_rooms = preferred_rooms

    if alternate_is_complete and alternate_rooms > current_member_rooms:
        current_member_rooms = alternate_rooms

    other_members = conn.execute("""
        SELECT
            coordination_group_members.id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            MAX(COALESCE(coordination_date_options.rooms_requested, 1)) AS rooms_requested
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        LEFT JOIN coordination_date_options
            ON coordination_group_members.id = coordination_date_options.coordination_group_member_id
        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.id != ?
        GROUP BY coordination_group_members.id, guest_profiles.primary_name
        ORDER BY guest_profiles.primary_name
    """, (
        member["coordination_group_id"],
        member_id
    )).fetchall()

    total_group_rooms = current_member_rooms
    room_detail_rows = f"""
    <tr>
        <td>This submission</td>
        <td align="center">{current_member_rooms}</td>
    </tr>
    """

    for other_member in other_members:

        other_rooms = other_member["rooms_requested"] or 1

        try:
            other_rooms = int(other_rooms)
        except:
            other_rooms = 1

        total_group_rooms += other_rooms

        room_detail_rows += f"""
        <tr>
            <td>{safe_text(other_member['primary_name'])}</td>
            <td align="center">{other_rooms}</td>
        </tr>
        """

    capacity_review_needed = total_group_rooms > total_rooms

    if capacity_review_needed:

        group_row = conn.execute("""
            SELECT title
            FROM coordination_groups
            WHERE id = ?
        """, (
            member["coordination_group_id"],
        )).fetchone()

        guest_row = conn.execute("""
            SELECT primary_name
            FROM guest_profiles
            JOIN coordination_group_members
                ON guest_profiles.id = coordination_group_members.guest_profile_id
            WHERE coordination_group_members.id = ?
        """, (
            member_id,
        )).fetchone()

        try:
            conn.execute("""
                UPDATE coordination_groups
                SET status = 'capacity_review',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                member["coordination_group_id"],
            ))

            notify_admin(
                "Capacity Review Required",
                (
                    "Group: " + safe_text(group_row["title"] if group_row else member["coordination_group_id"]) + "\n"
                    "Guest: " + safe_text(guest_row["primary_name"] if guest_row else member_id) + "\n"
                    "Requested Rooms: " + safe_text(total_group_rooms) + "\n"
                    "Available Rooms: " + safe_text(total_rooms) + "\n"
                    "Availability changed while the guest was completing the request. The request was saved for review."
                ),
                f"/coordination-group/{member['coordination_group_id']}"
            )

        except Exception:
            pass

    try:

        create_database_backup(
            "before_coordination_date_options"
        )

        conn.execute("""
            DELETE FROM coordination_date_options
            WHERE coordination_group_member_id = ?
        """, (
            member_id,
        ))

        conn.execute("""
            INSERT INTO coordination_date_options
            (
                coordination_group_member_id,
                priority,
                arrival_date,
                departure_date,
                flexibility_days,
                rooms_requested,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            member_id,
            "preferred",
            preferred_arrival,
            preferred_departure,
            preferred_flexibility,
            preferred_rooms,
            notes
        ))

        if alternate_is_complete:

            conn.execute("""
                INSERT INTO coordination_date_options
                (
                    coordination_group_member_id,
                    priority,
                    arrival_date,
                    departure_date,
                    flexibility_days,
                    rooms_requested,
                    notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                member_id,
                "alternate",
                alternate_arrival,
                alternate_departure,
                alternate_flexibility,
                alternate_rooms,
                notes
            ))

        final_invitation_status = "capacity_review" if capacity_review_needed else "responded"

        conn.execute("""
            UPDATE coordination_group_members
            SET invitation_status = ?,
                last_response_at = CURRENT_TIMESTAMP,
                follow_up_response_at = CASE
                    WHEN follow_up_round = (
                        SELECT COALESCE(current_round, 1)
                        FROM coordination_groups
                        WHERE id = coordination_group_members.coordination_group_id
                    )
                    AND follow_up_sent_at IS NOT NULL
                    THEN CURRENT_TIMESTAMP
                    ELSE follow_up_response_at
                END
            WHERE id = ?
        """, (
            final_invitation_status,
            member_id
        ))

        member_notify_row = conn.execute("""
            SELECT
                coordination_group_members.coordination_group_id,
                guest_profiles.primary_name
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.id = ?
        """, (
            member_id,
        )).fetchone()

        if member_notify_row:
            notify_admin_coordination_response(
                conn,
                member_notify_row["coordination_group_id"],
                member_notify_row["primary_name"],
                "Coordination dates submitted"
            )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    conn.close()

    if capacity_review_needed:
        return redirect(
            f"/coordination-group-member/{member_id}/date-options/thanks?capacity_review=1"
        )

    return redirect(
        f"/coordination-group-member/{member_id}/date-options/thanks"
    )


@app.route("/coordination-group-member/<int:member_id>/date-options/thanks")
def coordination_group_member_date_options_thanks(member_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    member = conn.execute("""
        SELECT
            coordination_group_members.*,
            coordination_groups.title AS group_title,
            guest_profiles.primary_name
        FROM coordination_group_members
        JOIN coordination_groups
            ON coordination_group_members.coordination_group_id = coordination_groups.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.id = ?
    """, (
        member_id,
    )).fetchone()

    saved_options = conn.execute("""
        SELECT *
        FROM coordination_date_options
        WHERE coordination_group_member_id = ?
        ORDER BY
            CASE priority
                WHEN 'preferred' THEN 1
                WHEN 'alternate' THEN 2
                ELSE 3
            END,
            arrival_date
    """, (
        member_id,
    )).fetchall()

    conn.close()

    capacity_review_mode = clean_text(request.args.get("capacity_review")) == "1"

    capacity_review_html = ""

    if capacity_review_mode:
        capacity_review_html = """
        <div style="max-width:760px; border:2px solid #fd7e14; background:#fff3cd; padding:14px; border-radius:10px; margin-bottom:12px;">
            <strong>Availability changed while you were completing your request.</strong><br>
            We saved your request. The group may be requesting more bedrooms than are available, so John and Mark will review options and follow up with next steps. If you need all of the bedrooms requested, you may need to choose different dates and another round may be needed.
        </div>
        """

    if not member:

        return f"""
        {nav_links()}
        <h1>Response Saved</h1>
        <p>Your response was saved.</p>
        """

    cannot_change = request.args.get("cannot_change") == "1"

    option_rows = ""

    for option in saved_options:

        option_rows += f"""
        <tr>
            <td>{safe_text(option['priority']).title()}</td>
            <td>{format_date(option['arrival_date'])}</td>
            <td>{format_date(option['departure_date'])}</td>
            <td align="center">{safe_text(option['rooms_requested'])}</td>
            <td align="center">± {safe_text(option['flexibility_days'])} day(s)</td>
        </tr>
        """

    if not option_rows:
        option_rows = """
        <tr><td colspan="5">No date options are currently saved.</td></tr>
        """

    return f"""
    <h1>{"Thanks!" if cannot_change else "Dates Saved!"}</h1>

    <div style="
        background-color: #e8f7ea;
        border: 1px solid #198754;
        padding: 12px 14px;
        border-radius: 8px;
        max-width: 760px;
        margin-bottom: 10px;
        line-height: 1.4;
    ">
        <p style="font-weight: bold; margin-top: 0;">
            {"Got it — we’ll work with the dates you already gave us." if cannot_change else "Your dates have been saved. One step closer to figuring out the beach calendar."}
        </p>
        <p style="margin-bottom: 0;">
            <strong>What happens next?</strong><br>
            {"I’ll use this in the group planning and follow up if we need anything else." if cannot_change else "I’ll compare everyone’s dates and see what works best. If I need anything else, you’ll get an email."}
        </p>
    </div>

    <h2>Your Submitted Date Preferences</h2>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 760px; font-size: 13px;">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Type</th>
            <th align="left">Arrival</th>
            <th align="left">Departure</th>
            <th align="center">Rooms</th>
            <th align="center">Flexibility</th>
        </tr>
        {option_rows}
    </table>

    <p style="font-weight: bold; color: #198754;">
        You can close this page.
    </p>
    """


@app.route("/coordination-group-member/<int:member_id>/cannot-change-dates", methods=["POST"])
def coordination_group_member_cannot_change_dates(member_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    try:

        create_database_backup(
            "before_coordination_cannot_change_dates"
        )

        conn.execute("""
            UPDATE coordination_group_members
            SET invitation_status = ?,
                last_response_at = CURRENT_TIMESTAMP,
                tentative_response_status = ?,
                tentative_response_at = CURRENT_TIMESTAMP,
                tentative_response_notes = ?,
                follow_up_response_at = CASE
                    WHEN follow_up_round = (
                        SELECT COALESCE(current_round, 1)
                        FROM coordination_groups
                        WHERE id = coordination_group_members.coordination_group_id
                    )
                    AND follow_up_sent_at IS NOT NULL
                    THEN CURRENT_TIMESTAMP
                    ELSE follow_up_response_at
                END
            WHERE id = ?
        """, (
            "responded",
            "needs_discussion",
            "Guest selected: I cannot change any dates.",
            member_id
        ))

        member_notify_row = conn.execute("""
            SELECT
                coordination_group_members.coordination_group_id,
                guest_profiles.primary_name
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.id = ?
        """, (
            member_id,
        )).fetchone()

        if member_notify_row:
            notify_admin_coordination_response(
                conn,
                member_notify_row["coordination_group_id"],
                member_notify_row["primary_name"],
                "Cannot change dates response received"
            )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    conn.close()

    return redirect(
        f"/coordination-group-member/{member_id}/date-options/thanks?cannot_change=1"
    )


@app.route("/coordination-group-member/<int:member_id>/clear-date-options", methods=["POST"])
def coordination_group_member_clear_date_options(member_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    try:

        create_database_backup(
            "before_clear_coordination_date_options"
        )

        conn.execute("""
            DELETE FROM coordination_date_options
            WHERE coordination_group_member_id = ?
        """, (
            member_id,
        ))

        conn.execute("""
            UPDATE coordination_group_members
            SET invitation_status = ?,
                last_response_at = NULL
            WHERE id = ?
        """, (
            "sent",
            member_id
        ))

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    conn.close()

    return redirect(
        f"/coordination-group-member/{member_id}/request"
    )


@app.route("/coordination-group/<int:group_id>/set-tentative", methods=["POST"])
def coordination_group_set_tentative(group_id):

    arrival_date = clean_text(request.form.get("arrival_date"))
    departure_date = clean_text(request.form.get("departure_date"))
    system_arrival_date = clean_text(request.form.get("system_arrival_date"))
    system_departure_date = clean_text(request.form.get("system_departure_date"))
    adjustment_reason = clean_text(request.form.get("adjustment_reason"))
    admin_adjustment = clean_text(request.form.get("admin_adjustment")) == "yes"

    try:
        arrival = datetime.strptime(arrival_date, "%Y-%m-%d")
        departure = datetime.strptime(departure_date, "%Y-%m-%d")
        if departure <= arrival:
            raise ValueError("Departure must be after arrival.")
    except Exception as error:
        return transaction_error_page(error, f"/coordination-group/{group_id}")

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    try:
        try:
            capacity_check = coordination_capacity_check_for_window(conn, group_id, arrival_date, departure_date)
        except Exception as capacity_error:
            capacity_check = {
                "capacity_ok": True,
                "rooms_needed": 0,
                "rooms_available": 0,
                "min_rooms_open": 0,
                "notes": ["Capacity check could not complete: " + safe_text(capacity_error)]
            }

        if not capacity_check["capacity_ok"]:
            raise ValueError(
                "Tentative dates are blocked by capacity or unavailable dates: "
                + "; ".join(capacity_check["notes"])
            )

        if admin_adjustment and not adjustment_reason:
            adjustment_reason = "Admin selected/confirmed tentative dates."

        if admin_adjustment and (not system_arrival_date or not system_departure_date):
            system_overlap = latest_coordination_system_overlap(conn, group_id)
            if system_overlap:
                system_arrival_date = system_overlap["arrival_date"]
                system_departure_date = system_overlap["departure_date"]

        conn.execute("""
            UPDATE coordination_groups
            SET tentative_arrival_date = ?,
                tentative_departure_date = ?,
                tentative_selected_at = CURRENT_TIMESTAMP,
                tentative_response_due_date = COALESCE(NULLIF(TRIM(tentative_response_due_date), ''), ?),
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (arrival_date, departure_date, default_coordination_due_date(), "tentative", group_id))

        conn.execute("""
            UPDATE coordination_group_members
            SET tentative_response_status = NULL,
                tentative_response_at = NULL,
                tentative_response_notes = NULL
            WHERE coordination_group_id = ?
        """, (group_id,))

        if admin_adjustment:
            conn.execute("""
                INSERT INTO coordination_tentative_adjustments
                (
                    coordination_group_id,
                    system_arrival_date,
                    system_departure_date,
                    admin_arrival_date,
                    admin_departure_date,
                    adjustment_reason,
                    rooms_needed,
                    capacity_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                group_id,
                system_arrival_date,
                system_departure_date,
                arrival_date,
                departure_date,
                adjustment_reason,
                capacity_check["rooms_needed"],
                "OK" if capacity_check["capacity_ok"] else "Needs Review"
            ))

        conn.commit()

    except Exception as error:
        rollback_and_close(conn)
        return transaction_error_page(error, f"/coordination-group/{group_id}")

    conn.close()
    return redirect(f"/coordination-group/{group_id}")


@app.route("/coordination-group-member/<int:member_id>/follow-up-dates-work", methods=["POST"])
def coordination_group_member_follow_up_dates_work(member_id):

    suggested_arrival = clean_text(
        request.form.get("suggested_arrival")
    )

    suggested_departure = clean_text(
        request.form.get("suggested_departure")
    )

    try:
        arrival = datetime.strptime(
            suggested_arrival,
            "%Y-%m-%d"
        )

        departure = datetime.strptime(
            suggested_departure,
            "%Y-%m-%d"
        )

        if departure <= arrival:
            raise ValueError("Suggested departure must be after arrival.")

    except Exception as error:

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    try:

        create_database_backup(
            "before_follow_up_dates_work"
        )

        member_row = conn.execute("""
            SELECT
                coordination_group_members.coordination_group_id,
                guest_profiles.primary_name
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.id = ?
        """, (
            member_id,
        )).fetchone()

        if not member_row:
            raise ValueError("Coordination member not found.")

        group_id = member_row["coordination_group_id"]

        add_follow_up_acceptance_date_option(
            conn,
            member_id,
            suggested_arrival,
            suggested_departure
        )

        conn.execute("""
            UPDATE coordination_group_members
            SET invitation_status = 'responded',
                last_response_at = CURRENT_TIMESTAMP,
                tentative_response_status = 'confirmed',
                tentative_response_at = CURRENT_TIMESTAMP,
                tentative_response_notes = ?,
                follow_up_response_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            f"Guest confirmed targeted follow-up dates: {suggested_arrival} to {suggested_departure}.",
            member_id
        ))

        all_fit = update_coordination_ready_for_booking_if_all_fit(
            conn,
            group_id,
            suggested_arrival,
            suggested_departure
        )

        notify_admin(
            "Targeted follow-up response received",
            f"Group ID: {group_id}\nGuest: {safe_text(member_row['primary_name'])}\nResponse: These dates work for me\nDates: {suggested_arrival} to {suggested_departure}",
            f"/coordination-group/{group_id}"
        )

        if all_fit:
            notify_admin(
                "Coordination group ready for booking",
                f"Group ID: {group_id}\nAll guests now fit the proposed dates. Booking Handoff is ready.",
                f"/coordination-group/{group_id}/handoff"
            )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    conn.close()

    return redirect(
        f"/coordination-group-member/{member_id}/tentative-response/thanks?status=confirmed&suggested_arrival={suggested_arrival}&suggested_departure={suggested_departure}"
    )



def create_coordination_booking_requests_for_confirmed(conn, group_id):

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:
        return {
            "created_count": 0,
            "skipped_count": 0,
            "group_title": f"Group {group_id}"
        }

    arrival_date = safe_text(group["tentative_arrival_date"]).strip()
    departure_date = safe_text(group["tentative_departure_date"]).strip()

    if not arrival_date or not departure_date:
        return {
            "created_count": 0,
            "skipped_count": 0,
            "group_title": safe_text(group["title"])
        }

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    confirmed_members = conn.execute("""
        SELECT
            coordination_group_members.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            guest_profiles.additional_names,
            guest_profiles.pet_notes,
            guest_profiles.food_notes
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.tentative_response_status = 'confirmed'
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    created_count = 0
    skipped_count = 0

    for member in confirmed_members:

        if member["converted_request_id"]:
            skipped_count += 1
            continue

        existing_request = conn.execute("""
            SELECT id
            FROM booking_requests
            WHERE coordination_group_member_id = ?
            LIMIT 1
        """, (
            member["id"],
        )).fetchone()

        if existing_request:

            conn.execute("""
                UPDATE coordination_group_members
                SET converted_request_id = ?,
                    converted_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                existing_request["id"],
                member["id"]
            ))

            skipped_count += 1
            continue

        rooms_requested = coordination_member_rooms_for_tentative(
            conn,
            member["id"],
            arrival_date,
            departure_date,
            total_rooms
        )

        comments = timestamped_comment_block(
            "Coordination Conversion",
            f"Auto-created after all guests confirmed tentative dates.\nConverted from coordination group: {safe_text(group['title'])}\nTentative dates: {arrival_date} to {departure_date}"
        )

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO booking_requests
            (
                guest_profile_id,
                invitation_id,
                name,
                email,
                additional_names,
                arrival_date,
                departure_date,
                adults,
                children,
                pets,
                food_restrictions,
                comments,
                coordination_notes,
                rooms_requested,
                status,
                email_status,
                email_needed_type,
                coordination_group_id,
                coordination_group_member_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            member["guest_profile_id"],
            None,
            member["primary_name"],
            member["primary_email"],
            member["additional_names"],
            arrival_date,
            departure_date,
            "1",
            "0",
            member["pet_notes"],
            member["food_notes"],
            comments,
            f"Coordination Group: {safe_text(group['title'])}",
            rooms_requested,
            "pending",
            "not_needed",
            "",
            group_id,
            member["id"]
        ))

        request_id = cursor.lastrowid

        conn.execute("""
            UPDATE coordination_group_members
            SET converted_request_id = ?,
                converted_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            request_id,
            member["id"]
        ))

        write_activity_log(
            conn,
            request_id,
            "coordination_auto_converted_to_request",
            "coordination",
            "pending",
            f"Automatically converted from coordination group {group_id} after all guests confirmed tentative dates."
        )

        created_count += 1

    conn.execute("""
        UPDATE coordination_groups
        SET status = 'booking_handoff',
            converted_at = COALESCE(converted_at, CURRENT_TIMESTAMP),
            closed_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        group_id,
    ))

    return {
        "created_count": created_count,
        "skipped_count": skipped_count,
        "group_title": safe_text(group["title"])
    }


@app.route("/coordination-group-member/<int:member_id>/tentative-response", methods=["POST"])
def coordination_group_member_tentative_response(member_id):

    response_status = clean_text(
        request.form.get("response_status")
    )

    response_notes = clean_text(
        request.form.get("response_notes")
    )

    allowed_statuses = [
        "confirmed",
        "cannot_make",
        "needs_discussion"
    ]

    if response_status not in allowed_statuses:

        return request_identity_error_page(
            "Invalid tentative date response.",
            f"/coordination-group-member/{member_id}/request"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    try:

        conn.execute("""
            UPDATE coordination_group_members
            SET tentative_response_status = ?,
                tentative_response_at = CURRENT_TIMESTAMP,
                tentative_response_notes = ?
            WHERE id = ?
        """, (
            response_status,
            response_notes,
            member_id
        ))

        member_notify_row = conn.execute("""
            SELECT
                coordination_group_members.coordination_group_id,
                guest_profiles.primary_name
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.id = ?
        """, (
            member_id,
        )).fetchone()

        if member_notify_row:
            group_id_for_response = member_notify_row["coordination_group_id"]

            notify_admin(
                "Tentative date response submitted",
                f"Group ID: {group_id_for_response}\nGuest: {safe_text(member_notify_row['primary_name'])}\nResponse: {tentative_response_display(response_status)}",
                f"/coordination-group/{group_id_for_response}/handoff"
            )

            tentative_total_count = conn.execute("""
                SELECT COUNT(*) AS count
                FROM coordination_group_members
                WHERE coordination_group_id = ?
            """, (
                group_id_for_response,
            )).fetchone()["count"]

            tentative_confirmed_count = conn.execute("""
                SELECT COUNT(*) AS count
                FROM coordination_group_members
                WHERE coordination_group_id = ?
                  AND tentative_response_status = 'confirmed'
            """, (
                group_id_for_response,
            )).fetchone()["count"]

            if tentative_total_count > 0 and tentative_confirmed_count >= tentative_total_count:

                conversion_result = create_coordination_booking_requests_for_confirmed(
                    conn,
                    group_id_for_response
                )

                notify_admin(
                    f"ADMIN ACTION – {safe_text(conversion_result['group_title'])}",
                    (
                        "Current Status:\n"
                        "All guests confirmed the tentative dates. Booking requests were created automatically.\n\n"
                        "NEXT STEP:\n"
                        "Open Booking Handoff, assign rooms, and approve the created requests.\n\n"
                        f"Booking requests created: {conversion_result['created_count']}\n"
                        f"Already existed / skipped: {conversion_result['skipped_count']}"
                    ),
                    f"/coordination-group/{group_id_for_response}/handoff"
                )

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group-member/{member_id}/request"
        )

    member = conn.execute("""
        SELECT
            coordination_group_members.tentative_response_status,
            coordination_group_members.tentative_response_notes,
            coordination_groups.title AS group_title,
            coordination_groups.tentative_arrival_date,
            coordination_groups.tentative_departure_date,
            coordination_groups.tentative_response_due_date,
            guest_profiles.primary_name
        FROM coordination_group_members
        JOIN coordination_groups
            ON coordination_group_members.coordination_group_id = coordination_groups.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.id = ?
    """, (
        member_id,
    )).fetchone()

    conn.close()

    tentative_dates_display = "Not set"

    if member and safe_text(member["tentative_arrival_date"]) and safe_text(member["tentative_departure_date"]):
        tentative_dates_display = (
            f"{format_date(member['tentative_arrival_date'])} "
            f"to {format_date(member['tentative_departure_date'])}"
        )

    response_display = tentative_response_display(response_status)

    return f"""
    <div style="
        max-width: 760px;
        border: 1px solid #198754;
        background-color: #e8f7ea;
        padding: 14px;
        border-radius: 8px;
    ">
        <h1 style="margin-top: 0;">Perfect!</h1>

        <p style="font-weight: bold;">
            Your response has been saved. Beach logistics are moving along.
        </p>

        <p>
            <strong>Group:</strong><br>
            {safe_text(member['group_title']) if member else ''}
        </p>

        <p>
            <strong>Current Tentative Dates:</strong><br>
            {tentative_dates_display}
        </p>

        <p>
            <strong>Your Response:</strong><br>
            {response_display}
        </p>

        <h2>What Happens Next</h2>

        <p>
            I’ll finish checking the group responses and send final details once everything is lined up.
            No further action needed right now.
        </p>

        <p style="font-weight: bold; color: #198754;">
            You can close this page.
        </p>
    </div>
    """


@app.route("/coordination-group-member/<int:member_id>/tentative-response/thanks")
def coordination_group_member_tentative_response_thanks(member_id):

    response_status = clean_text(
        request.args.get("status")
    ) or "confirmed"

    suggested_arrival = clean_text(
        request.args.get("suggested_arrival")
    )

    suggested_departure = clean_text(
        request.args.get("suggested_departure")
    )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    member = conn.execute("""
        SELECT
            coordination_group_members.*,
            coordination_groups.title AS group_title,
            coordination_groups.tentative_arrival_date,
            coordination_groups.tentative_departure_date,
            coordination_groups.status AS group_status,
            guest_profiles.primary_name
        FROM coordination_group_members
        JOIN coordination_groups
            ON coordination_group_members.coordination_group_id = coordination_groups.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.id = ?
    """, (
        member_id,
    )).fetchone()

    conn.close()

    tentative_dates_display = "Not set"

    if member and safe_text(member["tentative_arrival_date"]) and safe_text(member["tentative_departure_date"]):
        tentative_dates_display = (
            f"{format_date(member['tentative_arrival_date'])} "
            f"to {format_date(member['tentative_departure_date'])}"
        )
    elif suggested_arrival and suggested_departure:
        tentative_dates_display = (
            f"{format_date(suggested_arrival)} "
            f"to {format_date(suggested_departure)}"
        )

    next_text = "I’ll review the group responses and follow up with the next step."

    if member and safe_text(member["group_status"]) == "ready_for_booking":
        next_text = "Everyone now matches these dates. Great news — we’ll move to booking handoff and final details."

    return f"""
    {nav_links()}

    <div style="
        max-width: 760px;
        border: 1px solid #198754;
        background-color: #e8f7ea;
        padding: 14px;
        border-radius: 8px;
    ">
        <h1 style="margin-top: 0;">Perfect!</h1>

        <p style="font-weight: bold;">
            Your response has been saved. Beach logistics are moving along.
        </p>

        <p>
            <strong>Group:</strong><br>
            {safe_text(member['group_title']) if member else ''}
        </p>

        <p>
            <strong>Dates:</strong><br>
            {tentative_dates_display}
        </p>

        <p>
            <strong>Your Response:</strong><br>
            {tentative_response_display(response_status)}
        </p>

        <h2>What Happens Next</h2>

        <p>{next_text}</p>

        <p style="font-weight: bold; color: #198754;">
            You can close this page.
        </p>
    </div>
    """


@app.route("/coordination-group/<int:group_id>/rsvp-due-date", methods=["POST"])
def coordination_group_rsvp_due_date(group_id):

    due_date = clean_text(
        request.form.get("tentative_response_due_date")
    )

    if due_date:

        try:
            datetime.strptime(
                due_date,
                "%Y-%m-%d"
            )
        except:

            return f"""
            {nav_links()}

            <h1>Due Date Not Saved</h1>

            <p style="color: red; font-weight: bold;">
                Please enter a valid due date.
            </p>

            <p>
                <a href="/coordination-group/{group_id}">
                    Back to Coordination Group
                </a>
            </p>
            """

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    conn.execute("""
        UPDATE coordination_groups
        SET tentative_response_due_date = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        due_date,
        group_id
    ))

    conn.commit()
    conn.close()

    return redirect(
        f"/coordination-group/{group_id}"
    )



@app.route("/coordination-group/<int:group_id>/final-email-preview")
def coordination_group_final_email_preview(group_id):

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    if safe_text(group["final_coordination_email_sent_at"]):

        sent_at = safe_text(
            group["final_coordination_email_sent_at"]
        )

        conn.close()

        return f"""
        {nav_links()}

        <h1>Final Coordination Email Already Sent</h1>

        <p style="
            color: green;
            font-weight: bold;
        ">
            The final coordination email was already sent on {sent_at}.
        </p>

        <p>
            To avoid duplicate guest emails, the app will not send this
            final coordination email again.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.tentative_response_status,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    conn.close()

    if not safe_text(group["tentative_arrival_date"]) or not safe_text(group["tentative_departure_date"]):

        return f"""
        {nav_links()}

        <h1>Final Email Not Ready</h1>

        <p>
            Select tentative group dates before sending a final coordination email.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    not_confirmed = []

    for member in members:

        if safe_text(member["tentative_response_status"]) != "confirmed":
            not_confirmed.append(
                safe_text(member["primary_name"])
            )

    if not_confirmed:

        return f"""
        {nav_links()}

        <h1>Final Email Not Ready</h1>

        <p>
            Final coordination email can only be sent after every guest confirms.
        </p>

        <p>
            <strong>Still unresolved:</strong><br>
            {safe_text(', '.join(not_confirmed))}
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    preview_rows = []

    for member in members:

        body = f"""Hi {safe_text(member['primary_name'])},

Great news — the group has successfully coordinated dates for {safe_text(group['title'])}.

Tentative group dates:
{format_date(group['tentative_arrival_date'])} to {format_date(group['tentative_departure_date'])}

Everyone has now confirmed these tentative dates.

The next step is final booking review and room planning. Nothing is fully booked until the normal booking requests are reviewed and approved.

If anything changes before final approvals are completed, please reply as soon as possible.

Thanks everyone for coordinating together.

John & Mark
302-521-5401
"""

        preview_rows.append(f"""
        <tr>
            <td>{safe_text(member['primary_name'])}</td>
            <td>{safe_text(member['primary_email'])}</td>
            <td><pre style="white-space: pre-wrap;">{safe_text(body)}</pre></td>
        </tr>
        """)

    return f"""
    {nav_links()}

    <h1>Preview Final Coordination Email</h1>

    {email_template_metadata_html("final_coordination")}

    <p>
        This sends one email to each confirmed group member.
    </p>

    <form method="POST"
          action="/coordination-group/{group_id}/send-final-email"
          style="margin-bottom: 18px;">
        <button type="submit">
            Send Final Coordination Email
        </button>
    </form>

    <table border="1"
           cellpadding="5"
           cellspacing="0"
           style="border-collapse: collapse; width: 100%; font-size: 13px;">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Guest</th>
            <th align="left">Email</th>
            <th align="left">Preview</th>
        </tr>
        {''.join(preview_rows)}
    </table>

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Coordination Group
        </a>
    </p>
    """


@app.route("/coordination-group/<int:group_id>/send-final-email", methods=["POST"])
def coordination_group_send_final_email(group_id):

    if request.form.get("confirm_action") != "yes":

        return action_confirmation_page(
            "Confirm Final Coordination Email",
            "This will send the final coordination dates email to all confirmed group members. It will also mark the coordination group as final-email sent if at least one email sends successfully.",
            f"/coordination-group/{group_id}/send-final-email",
            f"/coordination-group/{group_id}/handoff"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    if safe_text(group["final_coordination_email_sent_at"]):

        sent_at = safe_text(
            group["final_coordination_email_sent_at"]
        )

        conn.close()

        return f"""
        {nav_links()}

        <h1>Final Coordination Email Already Sent</h1>

        <p style="
            color: green;
            font-weight: bold;
        ">
            The final coordination email was already sent on {sent_at}.
        </p>

        <p>
            To avoid duplicate guest emails, the app stopped this resend.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.tentative_response_status,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    if not safe_text(group["tentative_arrival_date"]) or not safe_text(group["tentative_departure_date"]):

        conn.close()

        return f"""
        {nav_links()}

        <h1>Final Email Not Sent</h1>

        <p>
            Select tentative group dates before sending a final coordination email.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    not_confirmed = []

    for member in members:

        if safe_text(member["tentative_response_status"]) != "confirmed":
            not_confirmed.append(
                safe_text(member["primary_name"])
            )

    if not_confirmed:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Final Email Not Sent</h1>

        <p>
            Final coordination email can only be sent after every guest confirms.
        </p>

        <p>
            <strong>Still unresolved:</strong><br>
            {safe_text(', '.join(not_confirmed))}
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    tentative_response_due_date = clean_text(
        request.form.get("tentative_response_due_date")
    )

    if not tentative_response_due_date:
        tentative_response_due_date = (
            date.today() + timedelta(days=3)
        ).strftime("%Y-%m-%d")

    try:
        datetime.strptime(
            tentative_response_due_date,
            "%Y-%m-%d"
        )
    except:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Tentative Confirmation Emails Not Sent</h1>

        <p style="color: red; font-weight: bold;">
            Please enter a valid response due date.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    sent_count = 0
    failed_recipients = []

    for member in members:

        to_email = safe_text(
            member["primary_email"]
        ).strip()

        if not to_email:
            continue

        subject = f"Strathmere group dates confirmed - {safe_text(group['title'])}"

        body = render_email_template(
            "final_group_ready.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            tentative_dates=f"{format_date(group['tentative_arrival_date'])} to {format_date(group['tentative_departure_date'])}"
        )

        try:
            send_email(
                to_email,
                subject,
                body
            )
            sent_count += 1
        except Exception as error:
            failed_recipients.append(
                f"{safe_text(member['primary_name'])}: {safe_text(error)}"
            )

    if sent_count > 0:

        conn.execute("""
            UPDATE coordination_groups
            SET final_coordination_email_sent_at = CURRENT_TIMESTAMP,
                status = 'confirmed_coordination',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            group_id,
        ))

        conn.commit()

    conn.close()

    failed_html = ""

    if failed_recipients:

        failed_html = f"""
        <p style="color: red; font-weight: bold;">
            Some final coordination emails could not be sent:
        </p>

        <pre>{safe_text(chr(10).join(failed_recipients))}</pre>
        """

    if sent_count == 0:

        return f"""
        {nav_links()}

        <h1>Final Coordination Email Not Sent</h1>

        <p style="color: red; font-weight: bold;">
            No final coordination emails were sent successfully.
        </p>

        {failed_html}

        <p>
            The coordination group was not marked as final-email sent.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    return f"""
    {nav_links()}

    <h1>Final Coordination Email Sent</h1>

    <p>
        Sent {sent_count} final coordination email(s).
    </p>

    {failed_html}

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Planning Page
        </a>
    </p>
    """


@app.route("/coordination-group/<int:group_id>/send-reminders", methods=["POST"])
def coordination_group_send_reminders(group_id):

    tentative_response_due_date = clean_text(
        request.form.get("tentative_response_due_date")
    )

    if not tentative_response_due_date:
        tentative_response_due_date = (
            date.today() + timedelta(days=3)
        ).strftime("%Y-%m-%d")

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    unresolved_members = conn.execute("""
        SELECT
            coordination_group_members.id AS member_id,
            coordination_group_members.tentative_response_status,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
          AND (
                coordination_group_members.tentative_response_status IS NULL
                OR TRIM(coordination_group_members.tentative_response_status) = ''
          )
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    if not safe_text(group["tentative_arrival_date"]) or not safe_text(group["tentative_departure_date"]):

        conn.close()

        return f"""
        {nav_links()}

        <h1>Reminder Not Sent</h1>

        <p>
            Select tentative group dates before sending reminders.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    sent_count = 0
    failed_recipients = []

    for member in unresolved_members:

        to_email = safe_text(
            member["primary_email"]
        ).strip()

        if not to_email:
            continue

        subject = f"Strathmere tentative group dates - {safe_text(group['title'])}"

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request"

        body = render_email_template(
            "tentative_confirmation.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            tentative_dates=f"{format_date(group['tentative_arrival_date'])} to {format_date(group['tentative_departure_date'])}",
            due_date=format_date(tentative_response_due_date),
            request_link=update_link,
            new_request_link=standard_new_request_url(),
            base_url=BASE_URL.rstrip("/")
        )

        try:
            send_email(
                to_email,
                subject,
                body
            )
            sent_count += 1
        except Exception as error:
            failed_recipients.append(
                f"{safe_text(member['primary_name'])}: {safe_text(error)}"
            )

    conn.execute("""
        UPDATE coordination_groups
        SET tentative_response_due_date = ?,
            coordination_reminder_sent_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        tentative_response_due_date,
        group_id,
    ))

    conn.commit()
    conn.close()

    failed_html = ""

    if failed_recipients:

        failed_html = f"""
        <p style="color: red; font-weight: bold;">
            Some reminders could not be sent:
        </p>

        <pre>{safe_text(chr(10).join(failed_recipients))}</pre>
        """

    return f"""
    {nav_links()}

    <h1>Tentative Dates That May Work for Everyone Emails Sent</h1>

    {email_template_metadata_html("tentative_confirmation")}

    <p>
        Sent {sent_count} tentative date confirmation email(s).
    </p>

    {failed_html}

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Coordination Group
        </a>
    </p>
    """


@app.route("/coordination-group/<int:group_id>/convert-confirmed", methods=["POST"])
def coordination_group_convert_confirmed(group_id):

    if request.form.get("confirm_action") != "yes":

        return action_confirmation_page(
            "Confirm Booking Request Creation",
            "This will create pending booking requests for confirmed guests who have not already been converted. It will not approve bookings or assign rooms.",
            f"/coordination-group/{group_id}/convert-confirmed",
            f"/coordination-group/{group_id}/handoff"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    arrival_date = safe_text(
        group["tentative_arrival_date"]
    ).strip()

    departure_date = safe_text(
        group["tentative_departure_date"]
    ).strip()

    if not arrival_date or not departure_date:

        conn.close()

        return f"""
        {nav_links()}

        <h1>No Tentative Dates Selected</h1>

        <p>
            Select tentative group dates before converting guests to booking requests.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    total_rooms = conn.execute("""
        SELECT COUNT(*) AS count
        FROM rooms
    """).fetchone()["count"]

    confirmed_members = conn.execute("""
        SELECT
            coordination_group_members.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            guest_profiles.additional_names,
            guest_profiles.pet_notes,
            guest_profiles.food_notes
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.tentative_response_status = 'confirmed'
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    unconverted_confirmed_count = 0

    for member in confirmed_members:

        if member["converted_request_id"]:
            continue

        existing_request = conn.execute("""
            SELECT id
            FROM booking_requests
            WHERE coordination_group_member_id = ?
            LIMIT 1
        """, (
            member["id"],
        )).fetchone()

        if existing_request:
            continue

        unconverted_confirmed_count += 1

    if confirmed_members and unconverted_confirmed_count == 0:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Booking Requests Already Created</h1>

        <p style="
            color: green;
            font-weight: bold;
        ">
            All confirmed guests already have booking requests.
        </p>

        <p>
            This repeat action is blocked to avoid duplicate booking requests.
            Use the Booking Handoff page to review room assignment and approvals.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    created_count = 0
    skipped_count = 0
    created_links = []

    try:

        create_database_backup(
            "before_coordination_convert_confirmed"
        )

        for member in confirmed_members:

            if member["converted_request_id"]:

                skipped_count += 1
                continue

            existing_request = conn.execute("""
                SELECT id
                FROM booking_requests
                WHERE coordination_group_member_id = ?
                LIMIT 1
            """, (
                member["id"],
            )).fetchone()

            if existing_request:

                conn.execute("""
                    UPDATE coordination_group_members
                    SET converted_request_id = ?,
                        converted_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    existing_request["id"],
                    member["id"]
                ))

                skipped_count += 1
                continue

            rooms_requested = coordination_member_rooms_for_tentative(
                conn,
                member["id"],
                arrival_date,
                departure_date,
                total_rooms
            )

            comments = timestamped_comment_block(
                "Coordination Conversion",
                f"Converted from coordination group: {safe_text(group['title'])}\nTentative dates: {arrival_date} to {departure_date}"
            )

            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO booking_requests
                (
                    guest_profile_id,
                    invitation_id,
                    name,
                    email,
                    additional_names,
                    arrival_date,
                    departure_date,
                    adults,
                    children,
                    pets,
                    food_restrictions,
                    comments,
                    coordination_notes,
                    rooms_requested,
                    status,
                    email_status,
                    email_needed_type,
                    coordination_group_id,
                    coordination_group_member_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                member["guest_profile_id"],
                None,
                member["primary_name"],
                member["primary_email"],
                member["additional_names"],
                arrival_date,
                departure_date,
                "1",
                "0",
                member["pet_notes"],
                member["food_notes"],
                comments,
                f"Coordination Group: {safe_text(group['title'])}",
                rooms_requested,
                "pending",
                "not_needed",
                "",
                group_id,
                member["id"]
            ))

            request_id = cursor.lastrowid

            conn.execute("""
                UPDATE coordination_group_members
                SET converted_request_id = ?,
                    converted_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                request_id,
                member["id"]
            ))

            write_activity_log(
                conn,
                request_id,
                "coordination_converted_to_request",
                "coordination",
                "pending",
                f"Converted from coordination group {group_id}."
            )

            created_count += 1
            created_links.append(
                f"<li><a href='/request/{request_id}'>Request {request_id}</a> - {safe_text(member['primary_name'])}</li>"
            )

        total_members = conn.execute("""
            SELECT COUNT(*) AS count
            FROM coordination_group_members
            WHERE coordination_group_id = ?
        """, (
            group_id,
        )).fetchone()["count"]

        confirmed_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM coordination_group_members
            WHERE coordination_group_id = ?
              AND tentative_response_status = 'confirmed'
        """, (
            group_id,
        )).fetchone()["count"]

        converted_confirmed_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM coordination_group_members
            WHERE coordination_group_id = ?
              AND tentative_response_status = 'confirmed'
              AND converted_request_id IS NOT NULL
        """, (
            group_id,
        )).fetchone()["count"]

        conn.execute("""
            UPDATE coordination_groups
            SET status = 'booking_handoff',
                converted_at = CURRENT_TIMESTAMP,
                closed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            group_id,
        ))

        conn.commit()

    except Exception as error:

        rollback_and_close(conn)

        return transaction_error_page(
            error,
            f"/coordination-group/{group_id}"
        )

    conn.close()

    if created_count > 0:
        notify_admin(
            f"ADMIN ACTION – {safe_text(group['title'])}",
            (
                "Current Status:\n"
                f"Booking requests created: {created_count}\n\n"
                "NEXT STEP:\n"
                "Open Booking Handoff, assign rooms, and approve the created requests."
            ),
            f"/coordination-group/{group_id}/handoff"
        )

    created_html = ""

    if created_links:
        created_html = f"""
        <ul>
            {''.join(created_links)}
        </ul>
        """

    return f"""
    {nav_links()}

    <h1>Booking Requests Created</h1>

    <p>
        Created {created_count} pending booking request(s).<br>
        Skipped {skipped_count} guest(s) that already had booking requests created.
    </p>

    {created_html}

    <p>
        These requests still need normal admin review and approval.
    </p>

    <p>
        <a href="/coordination-group/{group_id}">
            Back to Coordination Group
        </a>
    </p>
    """


@app.route("/coordination-group/<int:group_id>/close", methods=["POST"])
def coordination_group_close(group_id):

    if request.form.get("confirm_action") != "yes":

        return action_confirmation_page(
            "Confirm Final Visit Confirmation",
            "This will send the final visit confirmation email to approved guests, then mark the coordination group as Closed / Finalized. The app will block this if created booking requests still need approval.",
            f"/coordination-group/{group_id}/close",
            f"/coordination-group/{group_id}/handoff"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    open_converted_requests = conn.execute("""
        SELECT COUNT(*) AS count
        FROM coordination_group_members
        JOIN booking_requests
            ON coordination_group_members.converted_request_id = booking_requests.id
        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'
        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.converted_request_id IS NOT NULL
        GROUP BY booking_requests.id, booking_requests.status, booking_requests.rooms_requested
        HAVING booking_requests.status != 'approved'
            OR COUNT(bookings.id) < COALESCE(NULLIF(booking_requests.rooms_requested, ''), 1)
    """, (
        group_id,
    )).fetchall()

    open_converted_requests = len(open_converted_requests)

    if open_converted_requests > 0:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Closed</h1>

        <p style="color: red; font-weight: bold;">
            This group still has booking requests that need room assignment or approval.
        </p>

        <p>
            Finish reviewing the created booking requests before closing the coordination group.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if safe_text(group["final_visit_confirmation_sent_at"]):

        conn.execute("""
            UPDATE coordination_groups
            SET status = 'finalized',
                closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            group_id,
        ))

        conn.commit()
        conn.close()

        return redirect(
            f"/coordination-group/{group_id}/handoff"
        )

    group_member_rows = conn.execute("""
        SELECT
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    group_member_lines = []

    for group_member_row in group_member_rows:
        group_member_lines.append(
            f"- {safe_text(group_member_row['primary_name'])} ({safe_text(group_member_row['primary_email'])})"
        )

    group_member_list_text = "\n".join(group_member_lines)

    if not group_member_list_text:
        group_member_list_text = "No group members listed."

    sent_member_ids = []

    final_visit_requests = conn.execute("""
        SELECT
            booking_requests.*,
            coordination_group_members.id AS member_id,
            coordination_group_members.role,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            GROUP_CONCAT(rooms.name, ', ') AS approved_room_names
        FROM coordination_group_members

        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id

        JOIN booking_requests
            ON coordination_group_members.converted_request_id = booking_requests.id

        LEFT JOIN bookings
            ON booking_requests.id = bookings.request_id
           AND bookings.status = 'approved'

        LEFT JOIN rooms
            ON bookings.room_id = rooms.id

        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.converted_request_id IS NOT NULL
          AND booking_requests.status = 'approved'

        GROUP BY booking_requests.id

        ORDER BY guest_profiles.primary_name
    """, (
        group_id,
    )).fetchall()

    create_database_backup(
        "before_coordination_close"
    )

    sent_count = 0
    failed_sends = []

    for final_request in final_visit_requests:

        recipient_email = resolve_request_recipient_email(
            conn,
            final_request
        )

        if not recipient_email:

            failed_sends.append(
                f"{safe_text(final_request['name'])}: missing email"
            )

            continue

        nights = date_range_nights(
            final_request["arrival_date"],
            final_request["departure_date"]
        )

        room_list = safe_text(
            final_request["approved_room_names"]
        ).strip()

        if not room_list:
            room_list = "Assigned room details will be confirmed separately."

        email_body = render_email_template(
            "final_visit_confirmation.txt",
            guest_name=safe_text(final_request["name"]),
            arrival_date=format_date(final_request["arrival_date"]),
            departure_date=format_date(final_request["departure_date"]),
            nights=safe_text(nights),
            room_list=room_list,
            rooms_requested=safe_text(final_request["rooms_requested"]),
            confirmed_group_members=group_member_list_text,
            food_restrictions=safe_text(final_request["food_restrictions"]) or "None listed",
            pets=safe_text(final_request["pets"]) or "None listed",
            change_link=f"{BASE_URL}/request/{final_request['id']}/change",
            cancel_link=f"{BASE_URL}/request/{final_request['id']}/cancel",
            new_request_link=standard_new_request_url()
        )

        try:
            send_email(
                recipient_email,
                "Strathmere Visit Confirmed",
                email_body
            )
            sent_count += 1
            sent_member_ids.append(final_request["member_id"])

        except Exception as error:

            failed_sends.append(
                f"{safe_text(final_request['name'])}: {safe_text(error)}"
            )

    if final_visit_requests and sent_count == 0:

        conn.close()

        failed_html = "<br>".join(failed_sends)

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Closed</h1>

        <p style="color: red; font-weight: bold;">
            No final visit confirmation emails were sent successfully.
        </p>

        <p>
            The group was not closed so you can fix the email issue and try again.
        </p>

        <p>{failed_html}</p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    if sent_count > 0:

        conn.execute("""
            UPDATE coordination_groups
            SET final_visit_confirmation_sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            group_id,
        ))

    conn.execute("""
        UPDATE coordination_groups
        SET status = 'finalized',
            closed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        group_id,
    ))

    conn.commit()
    conn.close()

    return redirect(
        f"/coordination-group/{group_id}/handoff"
    )


@app.route("/coordination-group/<int:group_id>/add-member", methods=["POST"])
def coordination_group_add_member(group_id):

    guest_profile_id = request.form.get(
        "guest_profile_id"
    )

    role = clean_text(
        request.form.get("role")
    )

    if role not in ["participant", "guest", "organizer"]:
        role = "participant"

    if role == "guest":
        role = "participant"

    try:
        guest_profile_id = int(guest_profile_id)
    except:
        guest_profile_id = None

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (
        group_id,
    )).fetchone()

    if not group:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Coordination Group Not Found</h1>

        <p>
            The coordination group could not be found.
        </p>

        <p>
            <a href="/coordination-groups">
                Back to Coordination Groups
            </a>
        </p>
        """

    if not guest_profile_id:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Guest Not Added</h1>

        <p style="
            color: red;
            font-weight: bold;
        ">
            Please choose a guest profile to add.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    guest_profile = conn.execute("""
        SELECT *
        FROM guest_profiles
        WHERE id = ?
    """, (
        guest_profile_id,
    )).fetchone()

    if not guest_profile:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Guest Not Added</h1>

        <p style="
            color: red;
            font-weight: bold;
        ">
            The selected guest profile could not be found.
        </p>

        <p>
            <a href="/coordination-group/{group_id}/handoff">
                Back to Booking Handoff
            </a>
        </p>
        """

    validation_error = guest_profile_validation_error(
        guest_profile["primary_name"],
        guest_profile["primary_email"]
    )

    if validation_error:

        conn.close()

        return profile_error_page(
            validation_error,
            f"/coordination-group/{group_id}"
        )

    existing_member = conn.execute("""
        SELECT id
        FROM coordination_group_members
        WHERE coordination_group_id = ?
          AND guest_profile_id = ?
    """, (
        group_id,
        guest_profile_id
    )).fetchone()

    if existing_member:

        conn.close()

        return redirect(
            f"/coordination-group/{group_id}"
        )

    cursor = conn.execute("""
        INSERT INTO coordination_group_members
        (
            coordination_group_id,
            guest_profile_id,
            role,
            invitation_status
        )
        VALUES (?, ?, ?, ?)
    """, (
        group_id,
        guest_profile_id,
        role,
        "draft"
    ))

    new_member_id = cursor.lastrowid

    conn.commit()

    # Send the coordination request email immediately when a new guest is added.
    # This keeps the planning workflow moving without requiring a separate manual send step.
    email_send_error = ""

    try:

        members_for_email = conn.execute("""
            SELECT
                coordination_group_members.id AS member_id,
                coordination_group_members.role,
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.coordination_group_id = ?
            ORDER BY guest_profiles.primary_name
        """, (
            group_id,
        )).fetchall()

        group_member_text = "\n".join(
            [
                f"- {safe_text(member['primary_name'])} ({coordination_role_display(row_value(member, 'role') or 'participant')})"
                for member in members_for_email
            ]
        )

        if not group_member_text:
            group_member_text = "No group members listed."

        new_member = conn.execute("""
            SELECT
                coordination_group_members.id AS member_id,
                coordination_group_members.role,
                guest_profiles.primary_name,
                guest_profiles.primary_email
            FROM coordination_group_members
            JOIN guest_profiles
                ON coordination_group_members.guest_profile_id = guest_profiles.id
            WHERE coordination_group_members.id = ?
        """, (
            new_member_id,
        )).fetchone()

        if new_member and is_valid_email_address(new_member["primary_email"]):

            organizer_info = get_coordination_organizer_info(conn, group_id)
            organizer_email_display = ""
            if organizer_info["email"]:
                organizer_email_display = "<" + organizer_info["email"] + ">"

            request_link = f"{BASE_URL}/coordination-group-member/{new_member_id}/request"

            body = render_email_template(
                "coordination_invitation.txt",
                guest_name=safe_text(new_member["primary_name"]),
                group_title=safe_text(group["title"]),
                guest_role=coordination_role_display(row_value(new_member, "role") or "participant"),
                organizer_name=organizer_info["name"],
                organizer_email=organizer_info["email"],
                organizer_email_display=organizer_email_display,
                group_member_text=group_member_text,
                suggestion_text="No group overlap suggestion is available yet. Please submit or update your date options.",
                request_link=request_link
            )

            send_email(
                safe_text(new_member["primary_email"]).strip(),
                f"Strathmere group date coordination - {safe_text(group['title'])}",
                body
            )

            conn.execute("""
                UPDATE coordination_group_members
                SET invitation_status = 'sent'
                WHERE id = ?
            """, (
                new_member_id,
            ))

            conn.commit()

    except Exception as error:
        email_send_error = safe_text(error)

    conn.close()

    if email_send_error:
        return transaction_error_page(
            "Guest was added, but the coordination request email could not be sent: " + email_send_error,
            f"/coordination-group/{group_id}"
        )

    return redirect(
        f"/coordination-group/{group_id}"
    )




@app.route("/coordination-group/<int:group_id>/organizer-kickoff-preview", methods=["GET", "POST"])
def coordination_group_organizer_kickoff_preview(group_id):

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    group = conn.execute("""
        SELECT *
        FROM coordination_groups
        WHERE id = ?
    """, (group_id,)).fetchone()

    organizer = conn.execute("""
        SELECT
            coordination_group_members.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.coordination_group_id = ?
          AND coordination_group_members.role = 'organizer'
        ORDER BY coordination_group_members.id
        LIMIT 1
    """, (group_id,)).fetchone()

    if not group or not organizer:
        conn.close()
        return f"""
        {nav_links()}
        <h1>Organizer Kickoff Not Available</h1>
        <p>This group needs an Organizer before the kickoff email can be sent.</p>
        <p><a href="/coordination-group/{group_id}">Back to Coordination Group</a></p>
        """

    planning_link = organizer_planning_url(coordination_member_row_id(organizer))

    body = render_email_template(
        "organizer_kickoff.txt",
        guest_name=safe_text(organizer["primary_name"]),
        group_title=safe_text(group["title"]),
        planning_link=planning_link
    )

    subject = f"Strathmere group visit planning - {safe_text(group['title'])}"

    if request.method == "POST":

        try:
            send_email(
                safe_text(organizer["primary_email"]),
                subject,
                body
            )

            conn.execute("""
                UPDATE coordination_group_members
                SET organizer_kickoff_sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                coordination_member_row_id(organizer),
            ))

            conn.commit()
            conn.close()

            return redirect(f"/coordination-group/{group_id}")

        except Exception as error:
            conn.close()
            return f"""
            {nav_links()}
            <h1>Organizer Setup Email Failed</h1>
            <p style="color:red; font-weight:bold;">{safe_text(error)}</p>
            <p><a href="/coordination-group/{group_id}">Back to Coordination Group</a></p>
            """

    conn.close()

    template_metadata = email_template_metadata_html("organizer_kickoff")

    return f"""
    {nav_links()}
    <h1>Preview Organizer Setup Email</h1>
    {template_metadata}
    <p><strong>To:</strong> {safe_text(organizer['primary_name'])} &lt;{safe_text(organizer['primary_email'])}&gt;</p>
    <p><strong>Subject:</strong> {safe_text(subject)}</p>
    <pre style="white-space:pre-wrap; background:#f8f9fa; border:1px solid #dee2e6; padding:12px; max-width:900px;">{safe_text(body)}</pre>
    <form method="POST" onsubmit="return confirm('Send organizer kickoff email?');">
        <button type="submit" style="font-weight:bold; padding:8px 12px;">Send Organizer Setup Email</button>
        &nbsp;
        <a href="/coordination-group/{group_id}">Cancel / Back</a>
    </form>
    """


def send_admin_organizer_suggestions_email(group_id, member, suggested_guests, preferred_arrival="", preferred_departure="", rooms_requested="", date_notes=""):

    if not ADMIN_NOTIFICATIONS_ENABLED:
        return

    admin_email = safe_text(ADMIN_NOTIFICATION_EMAIL).strip()

    if not is_valid_email_address(admin_email):
        return

    group_title = safe_text(row_value(member, "title"))
    organizer_request_link = BASE_URL.rstrip() + f"/coordination-group-member/{row_value(member, 'id')}/request"

    body = render_email_template(
        "organizer_suggestions_admin.txt",
        group_title=group_title,
        organizer_name=safe_text(row_value(member, "primary_name")),
        organizer_email=safe_text(row_value(member, "primary_email")),
        suggested_guests=safe_text(suggested_guests) or "No suggested guests provided.",
        preferred_dates="Organizer will submit dates on their Request Visit page.",
        rooms_requested="Not collected on organizer setup.",
        date_notes=safe_text(date_notes) or "No notes provided.",
        group_link=BASE_URL.rstrip("/") + f"/coordination-group/{group_id}",
        guest_profiles_link=BASE_URL.rstrip("/") + "/profiles",
        organizer_request_link=organizer_request_link
    )

    send_email(
        admin_email,
        f"Organizer setup returned - {group_title}",
        body
    )


@app.route("/coordination-group-member/<int:member_id>/organizer-planning", methods=["GET", "POST"])
def coordination_group_member_organizer_planning(member_id):

    conn = get_db_connection()
    ensure_coordination_tables(conn)

    member = conn.execute("""
        SELECT
            coordination_group_members.*,
            coordination_groups.title,
            guest_profiles.primary_name,
            guest_profiles.primary_email
        FROM coordination_group_members
        JOIN coordination_groups
            ON coordination_group_members.coordination_group_id = coordination_groups.id
        JOIN guest_profiles
            ON coordination_group_members.guest_profile_id = guest_profiles.id
        WHERE coordination_group_members.id = ?
    """, (
        member_id,
    )).fetchone()

    if not member or safe_text(row_value(member, "role")).strip() != "organizer":
        conn.close()
        return f"""
        <h1> Not Available</h1>
        <p>This link is only available for the Organizer assigned to this coordination group.</p>
        """

    request_visit_link = f"/coordination-group-member/{member_id}/request"

    if request.method == "POST":

        suggested_guests = clean_text(request.form.get("suggested_guests"))
        date_notes = clean_text(request.form.get("date_notes"))

        conn.execute("""
            UPDATE coordination_group_members
            SET organizer_suggested_guests = ?,
                organizer_suggested_dates_notes = ?,
                organizer_suggestions_at = CURRENT_TIMESTAMP,
                invitation_status = CASE
                    WHEN invitation_status = 'draft' THEN 'viewed'
                    ELSE invitation_status
                END
            WHERE id = ?
        """, (
            suggested_guests,
            date_notes,
            member_id
        ))

        conn.commit()

        admin_email_error = ""

        try:
            send_admin_organizer_suggestions_email(
                row_value(member, "coordination_group_id"),
                member,
                suggested_guests,
                "",
                "",
                "",
                date_notes
            )
        except Exception as error:
            admin_email_error = safe_text(error)

        conn.close()

        admin_note = ""

        if admin_email_error:
            admin_note = "<p style='color:#856404;'>Your setup was saved, but the admin alert email could not be sent automatically. John and Mark can still see it in the app.</p>"

        return f"""
        <div style="max-width:760px; margin:0 auto; font-family:Arial, sans-serif; line-height:1.35;">
            <h1>Group Setup Saved</h1>

            <p>
                Thanks. Your suggested group members and notes have been saved for John and Mark to review.
            </p>

            <div style="border:2px solid #0f4c81; background:#eef5ff; border-radius:10px; padding:14px; margin:12px 0;">
                <h2 style="margin-top:0;">Next Step — Request Visit</h2>
                <p>
                    Please click below to set up the initial dates for this group — yes, organizers get first choice to start the planning process.
                </p>
                <p>
                    <a href="{request_visit_link}"
                       style="display:inline-block; background:#0f4c81; color:white; padding:10px 14px; border-radius:7px; text-decoration:none; font-weight:bold;">
                        Request Visit
                    </a>
                </p>
            </div>

            <p>
                After John and Mark review this information, everyone in the group, including you, will receive individual requests and future rounds to submit or confirm date options.
            </p>

            {admin_note}
        </div>
        """

    current_suggested_guests = safe_text(row_value(member, "organizer_suggested_guests"))
    current_date_notes = safe_text(row_value(member, "organizer_suggested_dates_notes"))

    conn.close()

    return f"""
    <div style="max-width: 860px; margin: 0 auto; font-family: Arial, sans-serif; line-height: 1.35;">
        <h1 style="margin-bottom:6px;">Set Up Your Group Visit</h1>

        <div style="background:#eef5ff; border:1px solid #bfd7f1; border-radius:10px; padding:12px 14px; margin-bottom:12px;">
            <p style="margin:0 0 6px 0;"><strong>Group:</strong> {safe_text(member['title'])}</p>
            <p style="margin:0;"><strong>Your role:</strong> Organizer</p>
        </div>

        <div style="background:#fff3cd; border:1px solid #fd7e14; border-radius:10px; padding:12px; margin-bottom:12px;">
            <strong>Step 1 — Setup Group</strong><br>
            The data on this page will help initially set up the group.
            Please suggest who should be included and then click <strong>Request Visit</strong> after saving to set up the initial dates —
            yes, organizers get first choice to start the planning process.
            <br><br>
            After John and Mark review this information, everyone in the group, including you, will receive individual requests and future rounds to submit or confirm date options.
        </div>

        <div style="display:flex; gap:8px; flex-wrap:wrap; font-size:13px; margin-bottom:12px;">
            <span style="background:#0f4c81; color:white; padding:5px 8px; border-radius:999px;">Step 1 — Setup Group</span>
            <span style="background:#e9ecef; padding:5px 8px; border-radius:999px;">Step 2 — Request Visit</span>
            <span style="background:#e9ecef; padding:5px 8px; border-radius:999px;">Step 3 — Invite Group</span>
            <span style="background:#e9ecef; padding:5px 8px; border-radius:999px;">Step 4 — Review Rounds</span>
            <span style="background:#e9ecef; padding:5px 8px; border-radius:999px;">Step 5 — Confirm Dates</span>
        </div>

        <form method="POST">
            <div style="border:1px solid #ddd; border-radius:10px; padding:12px; margin-bottom:12px; background:#fff;">
                <label><strong>Who should be included?</strong></label>
                <p style="font-size:13px; color:#555; margin:4px 0 8px 0;">
                    Add names and emails if you have them. One person per line works best.
                </p>
                <textarea name="suggested_guests" rows="6" style="width:100%; box-sizing:border-box; font-size:14px;" placeholder="Example:
Kevin Smith - kevin@example.com
Eric Jones - eric@example.com
Judy - email unknown">{safe_text(current_suggested_guests)}</textarea>
            </div>

            <div style="border:1px solid #ddd; border-radius:10px; padding:12px; margin-bottom:12px; background:#fff;">
                <label><strong>Notes for John and Mark</strong></label>
                <p style="font-size:13px; color:#555; margin:4px 0 8px 0;">
                    Include anything helpful, like who may need rooms together, flexible dates, children, or travel constraints.
                </p>
                <textarea name="date_notes" rows="4" style="width:100%; box-sizing:border-box; font-size:14px;">{safe_text(current_date_notes)}</textarea>
            </div>

            <button type="submit" style="font-weight:bold; padding:9px 14px; background:#0f4c81; color:white; border:0; border-radius:7px;">
                Save Group Setup
            </button>
        </form>
    </div>
    """

@app.errorhandler(Exception)
def production_error_handler(error):

    try:
        error_logger.exception("Unhandled exception")
    except Exception:
        pass

    if isinstance(error, HTTPException):

        try:
            error_logger.error(
                "HTTP error on %s %s: %s",
                request.method,
                request.path,
                safe_text(error)
            )
        except Exception:
            pass

        if getattr(error, "code", None) == 404:
            return f"""
            {nav_links()}

            <h1>Page Not Found</h1>

            <p>That link does not match a current Shore Home App page.</p>

            <p><strong>Link tried:</strong> {safe_text(request.path)}</p>

            <p>Use the Dashboard or reply to the email if this came from an older message.</p>

            <p><a href="/dashboard">Back to Dashboard</a></p>
            """, 404

        return f"""
        {nav_links()}

        <h1>Request Error</h1>

        <p>{safe_text(error)}</p>

        <p><a href="/dashboard">Back to Dashboard</a></p>
        """, getattr(error, "code", 500) or 500

    try:
        error_logger.error(
            "Unhandled error on %s %s\n%s",
            request.method,
            request.path,
            traceback.format_exc()
        )
    except Exception:
        pass

    # V27 hardening: keep production errors from exposing a traceback or crashing the response.
    return f"""
    {nav_links()}

    <h1>Something Went Wrong</h1>

    <p>Shore Home App hit an unexpected error, and the details were written to logs/errors.log.</p>

    <pre style="white-space: pre-wrap; background:#f8f9fa; border:1px solid #ccc; padding:10px;">
{safe_text(traceback.format_exc())}
    </pre>
    <p>Nothing on this page should be treated as saved unless the previous action showed a confirmation.</p>

    <p><a href="/dashboard">Back to Dashboard</a></p>
    """, 500


def latest_file_in_folder(folder, suffix=""):

    try:
        files = []
        for filename in os.listdir(folder):
            if suffix and not filename.endswith(suffix):
                continue
            full_path = os.path.join(folder, filename)
            if os.path.isfile(full_path):
                files.append(full_path)

        if not files:
            return ""

        return max(files, key=os.path.getmtime)
    except Exception:
        return ""


def hardening_status_row(label, status, detail):

    if status == "OK":
        background = "#e8f7ea"
        color = "#198754"
    elif status == "Warning":
        background = "#fff3cd"
        color = "#856404"
    else:
        background = "#f8d7da"
        color = "#dc3545"

    return f"""
    <tr style="background-color: {background};">
        <td><strong>{safe_text(label)}</strong></td>
        <td style="color: {color}; font-weight: bold;">{safe_text(status)}</td>
        <td>{safe_text(detail)}</td>
    </tr>
    """


@app.route("/system-health")
def system_health():

    rows = ""

    rows += hardening_status_row(
        "Version",
        "OK",
        APP_VERSION
    )

    route_count = 0
    endpoint_count = 0
    duplicate_endpoint_names = []

    try:
        route_rules = list(app.url_map.iter_rules())
        route_count = len(route_rules)
        endpoint_to_views = {}

        for rule in route_rules:
            endpoint_to_views.setdefault(rule.endpoint, set()).add(
                app.view_functions.get(rule.endpoint)
            )

        endpoint_count = len(endpoint_to_views)
        duplicate_endpoint_names = sorted(
            endpoint
            for endpoint, view_functions in endpoint_to_views.items()
            if endpoint != "static" and len(view_functions) > 1
        )
    except Exception:
        route_count = 0
        endpoint_count = 0
        duplicate_endpoint_names = ["route audit unavailable"]

    rows += hardening_status_row(
        "Route Map",
        "OK" if not duplicate_endpoint_names else "Warning",
        (
            f"{route_count} route rule(s), {endpoint_count} endpoint(s); slash aliases OK"
            if not duplicate_endpoint_names
            else f"Conflicting endpoint names: {', '.join(duplicate_endpoint_names)}"
        )
    )

    route_safety_ok, route_safety_detail = route_safety_diagnostics_summary()

    rows += hardening_status_row(
        "Route Safety",
        "OK" if route_safety_ok else "Error",
        route_safety_detail
    )

    rows += hardening_status_row(
        "Database",
        "OK" if os.path.exists(DATABASE_FILE) else "Error",
        DATABASE_FILE
    )

    conn = None
    required_tables = [
        "booking_requests",
        "bookings",
        "rooms",
        "guest_profiles",
        "invitations",
        "blocked_dates",
        "coordination_groups",
        "coordination_group_members",
        "coordination_date_options"
    ]

    try:
        conn = get_db_connection()
        ensure_coordination_tables(conn)
        existing_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        existing_table_names = set()
        for table in existing_tables:
            existing_table_names.add(table["name"])

        missing_tables = []
        for table_name in required_tables:
            if table_name not in existing_table_names:
                missing_tables.append(table_name)

        rows += hardening_status_row(
            "Required Tables",
            "OK" if not missing_tables else "Error",
            "All required tables found" if not missing_tables else ", ".join(missing_tables)
        )
    except Exception as error:
        rows += hardening_status_row(
            "Required Tables",
            "Error",
            error
        )
    finally:
        if conn:
            conn.close()

    backup_folder = "backups"
    latest_backup = latest_file_in_folder(backup_folder, ".db")
    rows += hardening_status_row(
        "Backup Folder",
        "OK" if os.path.isdir(backup_folder) else "Warning",
        f"Latest backup: {latest_backup}" if latest_backup else "No backup file found"
    )

    rows += hardening_status_row(
        "Email Address",
        "OK" if EMAIL_ADDRESS else "Error",
        EMAIL_ADDRESS or "Missing EMAIL_ADDRESS"
    )

    rows += hardening_status_row(
        "Email App Password",
        "OK" if EMAIL_APP_PASSWORD else "Warning",
        "Configured" if EMAIL_APP_PASSWORD else "Missing EMAIL_APP_PASSWORD; real email sending will fail"
    )

    rows += hardening_status_row(
        "Base URL",
        "OK" if BASE_URL else "Error",
        BASE_URL or "Missing BASE_URL"
    )

    rows += hardening_status_row(
        "Admin Notifications",
        "OK" if ADMIN_NOTIFICATIONS_ENABLED else "Warning",
        f"Enabled to {ADMIN_NOTIFICATION_EMAIL}" if ADMIN_NOTIFICATIONS_ENABLED else "Disabled"
    )

    rows += hardening_status_row(
        "HTML Emails",
        "OK" if HTML_EMAILS_ENABLED else "Warning",
        "Enabled" if HTML_EMAILS_ENABLED else "Disabled"
    )

    rows += hardening_status_row(
        "Error Logs",
        "OK" if os.path.isdir("logs") else "Warning",
        "logs/app.log and logs/errors.log"
    )

    return f"""
    {nav_links()}

    <h1>System Health</h1>

    <p>Production hardening checkpoint for database, email, backups, logs, and configuration.</p>

    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 980px;">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Area</th>
            <th align="left">Status</th>
            <th align="left">Details</th>
        </tr>
        {rows}
    </table>

    <p style="margin-top: 14px;">
        <a href="/email-audit">Open Email Audit</a> |
        <a href="/production-check">Open Production Check</a> |
        <a href="/booking-audit">Open Booking Audit</a>
    </p>
    """


@app.route("/email-audit")
def email_audit():

    audit_path = os.path.join("logs", "email_audit.log")
    rows = ""

    if os.path.exists(audit_path):
        with open(audit_path) as handle:
            lines = handle.readlines()[-100:]

        for line in reversed(lines):
            parts = [part.strip() for part in line.split("|", 4)]
            while len(parts) < 5:
                parts.append("")
            rows += f"""
            <tr>
                <td>{safe_text(parts[0])}</td>
                <td>{safe_text(parts[1])}</td>
                <td>{safe_text(parts[2])}</td>
                <td>{safe_text(parts[3])}</td>
                <td>{safe_text(parts[4])}</td>
            </tr>
            """
    else:
        rows = """
        <tr>
            <td colspan="5">No email audit log exists yet. It will appear after the next email send attempt.</td>
        </tr>
        """

    return f"""
    {nav_links()}

    <h1>Email Audit</h1>

    <p>Last 100 email send attempts.</p>

    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%; font-size: 12px;">
        <tr style="background-color: #f5f5f5;">
            <th align="left">Timestamp</th>
            <th align="left">Status</th>
            <th align="left">Recipient</th>
            <th align="left">Subject</th>
            <th align="left">Detail</th>
        </tr>
        {rows}
    </table>
    """


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, use_reloader=False, debug=False)

# V28_15_GUEST_UX_MENU_FONT_WORDING_ONLY

# V28_15C_EXACT_GUEST_FIELD_FONT_PATCH

# 

# V28_15I_INVITE_PAGE_CONFIRMED_TYPOGRAPHY

# V28_15J_INVITE_PAGE_SIZE_TUNING_ONLY

# V28_15K_PRODUCTION_CHECK_CALENDAR_DIAGNOSTICS_ONLY

# ============================================================
# V29A
# Real email template file foundation.
# Email text can now be edited in:
#   templates/emails/*.txt
# The app keeps DEFAULT_EMAIL_TEMPLATES as fallback only.
# ============================================================

# ============================================================
# V29B
# Invitation email template controls the message body.
# Saved invitations.message is no longer injected into invitation.txt.
# This prevents duplicate old custom message/footer text.
# ============================================================

# ============================================================
# V29C
# Invitation send now regenerates body from templates/emails/invitation.txt.
# The preview textarea is display-only and is never trusted as send source.
# ============================================================


# ============================================================
# V29D
# Invitation template fallback and stale runtime invitation.txt guard.
# If Render has an old invitation.txt containing {{ message }}, the app ignores
# that stale file and uses the current app default invitation template instead.
# ============================================================


# ============================================================
# V29E
# Invitation email never falls back to hardcoded app.py wording.
# If templates/emails/invitation.txt is missing, preview/send stops
# instead of showing or sending text John did not create.
# ============================================================


# ============================================================
# V29F
# Adds a proof/edit page for the actual Render invitation.txt.
# Invitation preview shows the exact template path and first lines.
# This exposes stale Render template files instead of guessing.
# ============================================================


# ============================================================
# V30.0
# Hardened invitation email/template behavior.
# - Email wording lives in templates/emails/*.txt.
# - invitation.txt is never auto-created or overwritten by rebuild.
# - Preview is display-only; send rebuilds from the template at send time.
# - Optional invitations.message is restored, but only appears if
#   invitation.txt explicitly includes {{ message }}.
# ============================================================


# ============================================================
# V30.2
# Production Check template protection relaxed to essential placeholders only.
# Read-only diagnostics; no email preview/send behavior changes.
# ============================================================


# ============================================================
# V30.4
# Email visual header URL is always included; no local file gate.
# above the compact blue banner for all HTML emails.
# Plain-text email body and TXT template rendering are unchanged.
# ============================================================

# ============================================================
# V30.7
# Email header image URL hardening. Uses a public absolute URL
# for shore_home_header.jpeg at repo root and keeps sizing
# from V30.5. No template text, send, preview, or database changes.
# ============================================================


# ============================================================
# V31.6
# Admin Reset Test Data tool.
# Preserves guest_profiles, rooms, blocked_dates.
# Clears operational invitation/request/booking/coordination/log data after automatic backup.
# ============================================================


# V35.2 BUILD TARGETS
# - Calendar availability badge: X ROOMS OPEN
# - Tentative coordination dates consume availability
# - Merge Next Recommended Action into Workflow
# - Capacity action wording: Review Date / Capacity Overlap
# - Group Previous Approved Stays by date
# - Confirmation banner includes request additional names
# - Organizer email cleanup

# V35.2.5b capacity_review email body moved to capacity_review.txt

# Banner wording
CAPACITY_REVIEW_TEXT="⚠ Capacity Needs Your Review"

# ============================================================
# V36.0 HARDENING RELEASE
# Production candidate based on V35.2.5h after TC1-TC6 passed.
# No new workflows. Stability, template consistency, and safety checks only.
# ============================================================

# ============================================================
# V36.1 SECURITY HARDENING
# - Fail-closed production env checks
# - Exact public endpoint allowlist; removed broad /request prefix access
# - Basic CSRF protection for admin POST routes
# ============================================================

# -----------------------------------------------------------------------------
# V36.2 Recovery & Production Hardening
# Production Health Dashboard + Booking Consistency Repair
# -----------------------------------------------------------------------------

def table_exists(conn, table_name):

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,)
    ).fetchone()

    return row is not None


def table_count_safe(conn, table_name):

    try:
        if not table_exists(conn, table_name):
            return None
        return conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"]
    except Exception:
        return "ERROR"


def latest_backup_manifest_summary():

    root = backup_root_folder()
    latest = None

    if os.path.isdir(root):
        for name in os.listdir(root):
            if not name.startswith("ShoreHome_Backup_"):
                continue
            folder = os.path.join(root, name)
            if not os.path.isdir(folder):
                continue
            manifest_path = os.path.join(folder, "manifest.json")
            modified = os.path.getmtime(folder)
            if latest is None or modified > latest.get("modified", 0):
                latest = {
                    "name": name,
                    "folder": folder,
                    "manifest_path": manifest_path,
                    "modified": modified,
                    "manifest": {},
                }

    if latest and os.path.exists(latest["manifest_path"]):
        try:
            import json
            with open(latest["manifest_path"], "r", encoding="utf-8") as handle:
                latest["manifest"] = json.load(handle)
        except Exception:
            latest["manifest"] = {}

    return latest


def production_health_rows():

    rows = []

    def add(section, label, ok, detail):
        rows.append({
            "section": section,
            "label": label,
            "ok": ok,
            "detail": detail,
        })

    add("System", "App Version", True, APP_VERSION)
    add("System", "Database Path", DATABASE_FILE.startswith("/var/data/"), DATABASE_FILE)
    add("System", "Base URL", bool(BASE_URL and not BASE_URL.startswith("http://127.0.0.1")), BASE_URL)
    add("System", "Admin Auth", ADMIN_AUTH_ENABLED, "Configured" if ADMIN_AUTH_ENABLED else "Missing ADMIN_PASSWORD")

    db_exists = os.path.exists(DATABASE_FILE)
    add("Database", "Database File", db_exists, DATABASE_FILE if db_exists else "Missing")

    try:
        conn = get_db_connection()
        for table_name in REQUIRED_BACKUP_TABLES:
            count = table_count_safe(conn, table_name)
            add("Database", table_name, count is not None and count != "ERROR", safe_text(count))
        conn.close()
    except Exception as error:
        add("Database", "Connection", False, safe_text(error))

    templates = template_file_list()
    add("Assets", "Email Templates", len(templates) > 0, str(len(templates)))

    photos = profile_photo_file_list()
    missing_photos = missing_profile_photo_references()
    add("Assets", "Profile Photos", len(photos) > 0, str(len(photos)))
    add("Assets", "Missing Photo References", len(missing_photos) == 0, str(len(missing_photos)))

    latest_backup = latest_backup_manifest_summary()
    if latest_backup:
        manifest = latest_backup.get("manifest", {})
        add("Recovery", "Last Backup", True, safe_text(latest_backup.get("name")))
        add("Recovery", "Last Backup Status", safe_text(manifest.get("status")) == "VERIFIED", safe_text(manifest.get("status", "Unknown")))
        add("Recovery", "Backup Includes Photos", len(manifest.get("copied_profile_photos", [])) > 0, str(len(manifest.get("copied_profile_photos", []))))
    else:
        add("Recovery", "Last Backup", False, "No backup found")

    route_ok, route_detail = route_safety_diagnostics_summary()
    add("Security", "Route Safety", route_ok, route_detail)

    schema_ok, schema_detail = database_schema_diagnostics_summary()
    add("Security", "Database Schema", schema_ok, schema_detail)

    booking_ok, booking_detail = booking_consistency_diagnostics_summary()
    add("Launch Readiness", "Booking Consistency", booking_ok, booking_detail)

    ready = all(row["ok"] for row in rows if row["section"] in {"System", "Database", "Assets", "Recovery", "Security", "Launch Readiness"})
    add("Launch Readiness", "Soft Launch Ready", ready, "READY" if ready else "NOT READY")

    return rows


@app.route("/production-health")
def production_health_dashboard():

    rows = production_health_rows()

    grouped = {}
    for row in rows:
        grouped.setdefault(row["section"], []).append(row)

    sections_html = ""
    for section, section_rows in grouped.items():
        body = "".join(
            production_status_row(
                item["label"],
                item["ok"],
                item["detail"]
            )
            for item in section_rows
        )
        sections_html += f"""
        <h2>{safe_text(section)}</h2>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%; max-width:1050px;">
            <tr style="background:#f5f5f5;"><th align="left">Check</th><th align="left">Status</th><th align="left">Details</th></tr>
            {body}
        </table>
        """

    return f"""
    {nav_links()}

    <h1>Production Health Dashboard</h1>

    <p>This combines production checks, recovery status, asset validation, and launch readiness in one read-only page.</p>

    <p>
        <a href="/admin-backup" style="font-weight:bold;">Backup & Recovery</a> |
        <a href="/booking-consistency-repair" style="font-weight:bold;">Booking Consistency Repair</a>
    </p>

    {sections_html}
    """


def booking_consistency_analysis():

    issues = []
    stats = {}

    try:
        conn = get_db_connection()

        for table_name in REQUIRED_BACKUP_TABLES:
            stats[table_name] = table_count_safe(conn, table_name)

        if table_exists(conn, "bookings"):
            if table_exists(conn, "booking_requests"):
                missing_requests = conn.execute("""
                    SELECT COUNT(*) AS count
                    FROM bookings
                    LEFT JOIN booking_requests
                        ON bookings.request_id = booking_requests.id
                    WHERE booking_requests.id IS NULL
                """).fetchone()["count"]
                if missing_requests:
                    issues.append({"issue": "Bookings without request", "count": missing_requests, "repair": "Manual review required", "risk": "HIGH"})

            if table_exists(conn, "rooms"):
                missing_rooms = conn.execute("""
                    SELECT COUNT(*) AS count
                    FROM bookings
                    LEFT JOIN rooms
                        ON bookings.room_id = rooms.id
                    WHERE rooms.id IS NULL
                """).fetchone()["count"]
                if missing_rooms:
                    issues.append({"issue": "Bookings without room", "count": missing_rooms, "repair": "Manual review required", "risk": "HIGH"})

        if table_exists(conn, "guest_profiles"):
            duplicate_emails = conn.execute("""
                SELECT primary_email, COUNT(*) AS count
                FROM guest_profiles
                WHERE COALESCE(primary_email, '') <> ''
                GROUP BY LOWER(primary_email)
                HAVING COUNT(*) > 1
            """).fetchall()
            if duplicate_emails:
                issues.append({"issue": "Duplicate guest profile emails", "count": len(duplicate_emails), "repair": "Manual merge recommended", "risk": "MEDIUM"})

            missing_photos = missing_profile_photo_references()
            if missing_photos:
                issues.append({"issue": "Broken guest photo references", "count": len(missing_photos), "repair": "Clear broken photo_path values", "risk": "LOW", "repair_key": "clear_missing_photos"})

        conn.close()
    except Exception as error:
        issues.append({"issue": "Analysis failed", "count": 1, "repair": safe_text(error), "risk": "HIGH"})

    return {
        "stats": stats,
        "issues": issues,
    }


def repair_broken_photo_references():

    missing = missing_profile_photo_references()
    if not missing:
        return 0

    conn = get_db_connection()
    repaired = 0

    for item in missing:
        email = safe_text(item.get("primary_email")).strip()
        photo_path = safe_text(item.get("photo_path")).strip()
        if not email or not photo_path:
            continue
        conn.execute("""
            UPDATE guest_profiles
            SET photo_path = NULL
            WHERE LOWER(primary_email) = LOWER(?)
              AND photo_path = ?
        """, (email, photo_path))
        repaired += 1

    conn.commit()
    conn.close()
    return repaired


@app.route("/booking-consistency-repair", methods=["GET", "POST"])
def booking_consistency_repair():

    message = ""

    if request.method == "POST":
        repair_action = safe_text(request.form.get("repair_action")).strip()

        try:
            safety_manifest = create_full_recovery_backup()
            if safe_text(safety_manifest.get("status")) != "VERIFIED":
                raise RuntimeError("Safety backup failed validation. Repair stopped.")

            if repair_action == "clear_missing_photos":
                repaired = repair_broken_photo_references()
                message = f"Created safety backup {safe_text(safety_manifest.get('backup_name'))}. Cleared {repaired} broken photo reference(s)."
            else:
                message = f"Created safety backup {safe_text(safety_manifest.get('backup_name'))}. No repair action selected."

        except Exception as error:
            message = "Repair stopped: " + safe_text(error)

    analysis = booking_consistency_analysis()

    stat_rows = "".join(
        f"<tr><td>{safe_text(table)}</td><td>{safe_text(count)}</td></tr>"
        for table, count in analysis.get("stats", {}).items()
    )

    issue_rows = "".join(
        f"<tr><td>{safe_text(item.get('issue'))}</td><td>{safe_text(item.get('count'))}</td><td>{safe_text(item.get('repair'))}</td><td>{safe_text(item.get('risk'))}</td></tr>"
        for item in analysis.get("issues", [])
    )

    if not issue_rows:
        issue_rows = "<tr><td colspan='4' style='color:green; font-weight:bold;'>No repairable consistency issues found.</td></tr>"

    return f"""
    {nav_links()}

    <h1>Booking Consistency Repair</h1>

    <p>This tool analyzes booking-related data and only performs low-risk repairs after creating a verified safety backup.</p>

    <p style="font-weight:bold; color:#0f4c81;">{safe_text(message)}</p>

    <h2>Table Counts</h2>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; max-width:900px; width:100%;">
        <tr><th align="left">Table</th><th align="left">Rows</th></tr>
        {stat_rows}
    </table>

    <h2>Issues</h2>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; max-width:1100px; width:100%;">
        <tr><th align="left">Issue</th><th align="left">Count</th><th align="left">Repair</th><th align="left">Risk</th></tr>
        {issue_rows}
    </table>

    <h2>Repair Actions</h2>
    <form method="POST">
        {csrf_input()}
        <input type="hidden" name="repair_action" value="clear_missing_photos">
        <button type="submit" style="font-weight:bold; padding:10px 16px; background:#fd7e14; color:white; border:0; border-radius:8px;">
            Create Safety Backup + Clear Broken Photo References
        </button>
    </form>

    <p><a href="/production-health">Back to Production Health</a></p>
    """
