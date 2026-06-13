from flask import Flask, request, redirect, render_template, render_template_string, session
from datetime import date, datetime, timedelta
from database import get_db_connection, DATABASE_FILE
import smtplib
from email.message import EmailMessage
import os
import shutil
import html as html_escape_module
import logging
import traceback
import re
import hmac
from werkzeug.exceptions import HTTPException


app = Flask(__name__)
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
    "app_V28_15D"
)

BASE_URL = os.environ.get(
    "BASE_URL",
    "http://127.0.0.1:5000"
).rstrip("/")

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
Additional Guests for Your Room(s): {additional_names}

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

Additional Guests for Your Room(s): {additional_names}
{coordinating_with_section}{optional_admin_message}If anything does not look right, just reply to this email.

Looking forward to seeing everyone at the shore!

Need to make a change?

Change Request:
{{ change_link }}

Cancel Visit:
{{ cancel_link }}

Start a New Request:
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

Additional Guests for Your Room(s): {additional_names}
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
    }
}



EMAIL_TEMPLATE_FOLDER = os.path.join("templates", "emails")

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

Additional Guests: {{ additional_names }}
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
Additional Guests: {{ additional_names }}

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

Additional Guests: {{ additional_names }}
{{ coordinating_with_section }}{{ optional_admin_message }}If anything does not look right, just reply to this email.

{{ change_links_section }}

Looking forward to seeing everyone at the shore!

John & Mark
302-521-5401
""",
    "invitation.txt": """Hi {{ guest_name }},

{{ message }}

Please use the request link below to submit your visit request:

{{ request_link }}

You can still use regular email or a phone call at any point if that’s easier.

Looking forward to hopefully seeing everyone down at the shore.

John & Mark
302-521-5401
""",
    "coordination_invitation.txt": """Hi {{ guest_name }},

We are starting a group date coordination process for {{ group_title }}.

Your role in this group: {{ guest_role }}

The goal is to collect preferred and alternate dates from everyone, compare overlap, and then propose tentative dates for the group to confirm. Nothing is confirmed or booked yet.

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

Change / Review Dates:
{{ request_link }}

Cancel / Cannot Make These Dates:
{{ request_link }}

Start a New Request:
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

Additional Guests: {{ additional_names }}

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
}


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

        if not os.path.exists(template_path):

            with open(template_path, "w", encoding="utf-8") as handle:
                handle.write(template_text)


def load_email_template(template_name):

    ensure_email_template_files()

    template_path = os.path.join(
        EMAIL_TEMPLATE_FOLDER,
        template_name
    )

    if os.path.exists(template_path):

        with open(template_path, "r", encoding="utf-8") as handle:
            return handle.read()

    return DEFAULT_EMAIL_TEMPLATES.get(template_name, "")


ensure_email_template_files()


def rebuild_email_template_files():

    os.makedirs(
        EMAIL_TEMPLATE_FOLDER,
        exist_ok=True
    )

    for template_name, template_text in DEFAULT_EMAIL_TEMPLATES.items():

        template_path = os.path.join(
            EMAIL_TEMPLATE_FOLDER,
            template_name
        )

        with open(template_path, "w", encoding="utf-8") as handle:
            handle.write(template_text)


@app.route("/admin/rebuild-email-templates")
def admin_rebuild_email_templates():

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
        context["base_url"] = BASE_URL

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

    # Keep TXT templates as the source of truth.
    # HTML email cleans up presentation by moving detail rows into one card
    # and URL lines into buttons, so those sections do not repeat below.
    url_pattern = re.compile(r"(https?://[^\s<]+)")

    def action_label_for_url(url, nearby_text=""):

        nearby_lower = safe_text(nearby_text).lower()

        if "change" in nearby_lower or "/change" in url:
            return "Change Request"

        if "cancel" in nearby_lower or "/cancel" in url:
            return "Cancel Visit"

        if "coordination" in nearby_lower:
            return "Open Coordination Link"

        if url.rstrip("/") == BASE_URL.rstrip("/"):
            return "Open New Request"

        if "/new-request" in url:
            return "Open New Request"

        if "request" in nearby_lower or "/invite" in url:
            return "Open New Request"

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
        "Group members:",
        "Current proposed dates:"
    ]

    link_label_lines = [
        "Change Request:",
        "Cancel Visit:",
        "Cancel Request:",
        "Start a New Request:",
        "Change / Review Dates:",
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
        "Start a New Request"
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

    # De-duplicate buttons while preserving order.
    seen_urls = set()
    final_buttons = []

    for button in action_buttons:

        button_url = button["url"]

        if button_url.rstrip("/") == BASE_URL.rstrip("/"):
            button_url = BASE_URL.rstrip("/") + "/new-request"

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

                <div style="background:#0f4c81; color:white; padding:20px 22px;">
                    <div style="font-size:13px; letter-spacing:.08em; text-transform:uppercase; opacity:.9; margin-bottom:6px;">
                        Shore Home
                    </div>
                    <div style="font-size:22px; font-weight:bold; line-height:1.25;">
                        Strathmere Visit Coordination
                    </div>
                    <div style="font-size:14px; opacity:.92; margin-top:8px; line-height:1.4;">
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

        if html_body is None:
            html_body = plain_text_to_html_email(
                subject,
                body
            )

        msg.add_alternative(
            html_body,
            subtype="html"
        )

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

    body = f"""Action needed in Shore Home App

Action: {safe_text(action_title)}

{safe_text(details)}

Review:
{review_url}
"""

    try:
        send_email(
            admin_email,
            f"Shore Home App: {safe_text(action_title)}",
            body
        )
    except Exception as error:
        print("ADMIN NOTIFICATION FAILED:", safe_text(error))


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

@app.route("/test-email")
def test_email():
    send_email(
        EMAIL_ADDRESS,
        "Test email from Shore Home App",
        "This is a test email from the Shore Home App."
    )

    return """
    <h2>Test email sent.</h2>
    <p><a href="/dashboard">Back to Dashboard</a></p>
    """

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
    "static",
    "invite_request",
    "invitation_request_alias",
    "invitation_request",
    "guest_invitation_request",
    "request_form",
    "new_request",
    "public_request",
    "guest_request",
    "submit",
    "request_submitted_review",
    "request_submitted_complete",
    "change_request",
    "change_request_bad_link",
    "cancel_request",
    "coordination_group_member_request",
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
    path = safe_text(request.path)

    guest_public_prefixes = (
        "/invite/",
        "/new-request",
        "/request/",
        "/coordination-member/",
        "/coordination-group-member/"
    )

    if endpoint in PUBLIC_ENDPOINTS:
        return None

    if endpoint.startswith("static"):
        return None

    if any(path.startswith(prefix) for prefix in guest_public_prefixes):
        # Guest-facing email links must stay public. Admin-only request review pages
        # are still protected by their own non-guest routes and dashboard links.
        if "/email-preview" not in path and "/approve" not in path and "/decline" not in path:
            return None

    if not ADMIN_AUTH_ENABLED:
        return None

    if admin_is_logged_in():
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
    ):
        return ""

    return f"""
    <div style="font-size: 14px; line-height: 1.8;">
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
        <a href="/admin-backup">Admin Backup</a> |
        <a href="/production-check">Production Check</a> |
        <a href="/system-health">System Health</a> |\n        <a href="/admin-logout">Logout</a>
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


def row_value(row, *keys):

    for key in keys:

        try:
            value = row[key]
        except Exception:
            value = None

        if value is not None:
            return value

    return ""


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


def build_coordination_match_suggestions(date_options, approved_bookings, blocked_ranges, total_rooms):

    if not date_options:
        return []

    blocked_dates = set()

    for block in blocked_ranges:

        try:
            current = datetime.strptime(
                block["start_date"],
                "%Y-%m-%d"
            ).date()

            end = datetime.strptime(
                block["end_date"],
                "%Y-%m-%d"
            ).date()

        except:
            continue

        while current <= end:

            blocked_dates.add(
                current.strftime("%Y-%m-%d")
            )

            current += timedelta(days=1)

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

                rooms_open = total_rooms - approved_by_date.get(date_string, 0)

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

def request_change_links(request_id):

    request_id_text = safe_text(request_id).strip()

    if request_id_text.isdigit():
        change_url = f"{BASE_URL}/request/{request_id_text}/change"
        cancel_url = f"{BASE_URL}/request/{request_id_text}/cancel"
    else:
        change_url = BASE_URL
        cancel_url = ""

    cancel_block = ""

    if cancel_url:
        cancel_block = f"""
Cancel Visit:
{cancel_url}
"""

    return f"""

━━━━━━━━━━━━━━━━━━

Need to make a change?

Change Request:
{change_url}
{cancel_block}
Start a New Request:
{BASE_URL}

━━━━━━━━━━━━━━━━━━
"""


def ensure_guest_change_links(body, request_id):

    body = safe_text(body)

    if "Need to make a change?" in body:
        return body

    return body + request_change_links(request_id)

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
            action_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


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
            role TEXT NOT NULL DEFAULT 'guest',
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
        "follow_up_suggested_departure TEXT"
    ]

    for column_definition in coordination_member_columns:

        try:
            conn.execute(
                f"ALTER TABLE coordination_group_members ADD COLUMN {column_definition}"
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
        WHERE status != 'archived'
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

    blocked_dates = set()
    blocked_reasons_by_date = {}

    for block in blocked_ranges:

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
            blocked_dates.add(current_date_key)

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

        rooms_open = total_rooms - rooms_used - tentative_hold_rooms

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
        "New requests waiting for review, room assignment, approval, or decline.",
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


@app.route("/admin-backup")
def admin_backup():

    import shutil

    backup_folder = "backups"

    os.makedirs(
        backup_folder,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y_%m_%d__%H_%M_%S"
    )

    backup_filename = (
        f"shore_backup_{timestamp}.db"
    )

    source_db = DATABASE_FILE

    backup_path = os.path.join(
        backup_folder,
        backup_filename
    )

    shutil.copy2(
        source_db,
        backup_path
    )

    html = nav_links() + f"""

    <h1>Admin Backup</h1>

    <p style="
        color: green;
        font-weight: bold;
    ">
        Backup created successfully.
    </p>

    <p>
        <strong>Backup File:</strong><br>
        {backup_filename}
    </p>

    <p>
        <strong>Location:</strong><br>
        {backup_path}
    </p>

    <p>
        <a href="/">
            Return Home
        </a>
    </p>
    """

    return html

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

    blocked_dates = set()

    for b in blocked:
        start = datetime.strptime(b["start_date"], "%Y-%m-%d")
        end = datetime.strptime(b["end_date"], "%Y-%m-%d")

        current = start

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

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

        room_capacity[current.strftime("%Y-%m-%d")] = max(
            0,
            total_rooms - rooms_used - tentative_rooms_held
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
        <a href="{calendar_base_path}?year={previous_year}&month={previous_month}">
            Previous Month
        </a>
        |
        <strong>{month_title}</strong>
        |
        <a href="{calendar_base_path}?year={next_year}&month={next_month}">
            Next Month
        </a>
    </p>

    <table border="1" cellpadding="3" cellspacing="0" style="border-collapse: collapse;">
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
                width: 42px;
                height: 32px;
                font-size: 11px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong><br>
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
    <h1 style="margin-bottom: 6px;">Request a Shore Visit</h1>

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
                onclick="resetDateSelection();"
                style="padding: 12px 18px; font-size: 22px; font-weight: bold; background-color: #0d6efd; color: white; border: none; border-radius: 8px;">
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
            <strong style="font-size: 32px;">Additional Guest Name(s) for Your Room(s)</strong>
        </label><br>

        <small style="font-size: 22px;">
            Please include everyone expected to stay.
        </small><br>

        <textarea name="additional_names"
                  rows="2"
                  style="width: 100%; max-width: 980px; font-size: 24px; padding: 10px; line-height: 1.35;"></textarea>

        <br>

        <label>
            <strong style="font-size: 32px;">Bringing a pet?</strong>
        </label><br>

        <select name="pets"
                style="font-size: 24px; padding: 10px; min-width: 120px;">
            <option value="No">No</option>
            <option value="Yes">Yes</option>
        </select>

        <br>

        <label>
            <strong style="font-size: 32px;">Food Restrictions or Preferences</strong>
        </label><br>

        <small style="font-size: 22px;">
            Optional — dietary restrictions, allergies, etc.
        </small><br>

        <textarea name="food_restrictions"
                  rows="3"
                  style="width: 100%; max-width: 980px; font-size: 24px; padding: 10px; line-height: 1.35;"></textarea>

        <br>

        <label>
            <strong style="font-size: 24px;">Comments or Notes</strong>
        </label><br>

        <textarea name="comments"
                  rows="2"
                  style="width: 100%; max-width: 980px; font-size: 24px; padding: 10px; line-height: 1.35;"></textarea>

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
               style="padding: 12px 18px; font-size: 22px; font-weight: bold; background-color: #0d6efd; color: white; border: none; border-radius: 8px;">

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
            }}
        }}

        document.getElementById("rooms_requested")
            .addEventListener("change", function () {{
                resetDateSelection();
                updateNightsMessage();
            }});

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

        resetDateSelection();
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

    blocked_dates = set()

    for b in blocked:
        start = datetime.strptime(b["start_date"], "%Y-%m-%d")
        end = datetime.strptime(b["end_date"], "%Y-%m-%d")

        current = start

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

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

        room_capacity[current.strftime("%Y-%m-%d")] = max(
            0,
            total_rooms - rooms_used - tentative_rooms_held
        )

        current += timedelta(days=1)

    calendar_html = f"""
    <h2 id="calendar-section">Capacity Calendar - {month_title}</h2>

    <p>
        <a href="/invite/{invitation_id}?year={previous_year}&month={previous_month}#calendar-section">
            Previous Month
        </a>
        |
        <strong>{month_title}</strong>
        |
        <a href="/invite/{invitation_id}?year={next_year}&month={next_month}#calendar-section">
            Next Month
        </a>
    </p>

    <table border="1" cellpadding="3" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 760px;">
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
                width: 42px;
                height: 32px;
                font-size: 11px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong><br>
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
                <td>{safe_text(booking['room_name'])}</td>
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

    <h1>Standard Visit Request</h1>

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
                    You are responding to:
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

                {calendar_html}

                <h3 style="font-size: 28px; margin-bottom: 8px;">Selected Stay</h3>

                <p id="date_selection_message"
                   style="
                       font-weight: bold;
                       color: #0d6efd;
                   ">
                   No dates selected yet.
                </p>

                <p id="nights_message"
                   style="
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

                <label>
                    <strong style="font-size: 32px;">Additional Guest Name(s) for Your Room(s)</strong>
                </label><br>

                <small>
                    Please include everyone expected to stay.
                </small><br>

                <textarea name="additional_names"
                          rows="2"
                          style="width: 100%;">{profile_additional_names}</textarea>

                <br>

                <label>
                    <strong style="font-size: 32px;">Bringing a pet?</strong>
                </label><br>

                <select name="pets">
                    <option value="No" {pet_no_selected}>No</option>
                    <option value="Yes" {pet_yes_selected}>Yes</option>
                </select>

                <br>

                <label>
                    <strong style="font-size: 32px;">Food Restrictions or Preferences</strong>
                </label><br>

                <small>
                    Optional — dietary restrictions, allergies, etc.
                </small><br>

                <textarea name="food_restrictions"
                          rows="3"
                          style="width: 100%;">{profile_food_notes}</textarea>

                <br>

                <label>
                    <strong style="font-size: 24px;">Comments or Notes</strong>
                </label><br>

                <textarea name="comments"
                          rows="2"
                          style="width: 100%;"></textarea>

                <br>

                <input type="submit"
                       value="Submit Visit Request">

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

        function resetDateSelection() {{
            document.getElementById("arrival_date").value = "";
            document.getElementById("departure_date").value = "";

            document.getElementById("date_selection_message").innerText =
                "No dates selected yet.";

            document.getElementById("nights_message").innerText = "";

            clearSelectedCellColors();

            nextDateField = "arrival";
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
            }}
        }}

        document.getElementById("rooms_requested")
            .addEventListener("change", function () {{
                resetDateSelection();
                updateNightsMessage();
            }});

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

        resetDateSelection();
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
        another_link = "/"

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
            Your request is in. The beach planning machine is officially warming up.
        </p>
        <p style="margin-bottom: 0;">
            <strong>What happens next?</strong><br>
            I’ll review the dates and room space. If everything works, you’ll get a confirmation.
            If not, I’ll follow up and we’ll figure it out.
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
            Submit an Additional Request
        </a>
    </p>

    <p>
        <a href="/request/{request_id}/edit?return_to=submitted">
            Review or Edit This Request
        </a>
    </p>

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
                — <a href="/request/{pending_request['id']}/edit?return_to=submitted">Edit</a>
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

    blocked = conn.execute("""
        SELECT * FROM blocked_dates
        ORDER BY start_date
    """).fetchall()

    blocked_dates = set()

    for b in blocked:
        start = datetime.strptime(b["start_date"], "%Y-%m-%d")
        end = datetime.strptime(b["end_date"], "%Y-%m-%d")

        current = start

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

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

        rooms_open = total_rooms - rooms_used

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
        "New request submitted",
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

    blocked_conflict = conn.execute("""
        SELECT *
        FROM blocked_dates
        WHERE start_date < ?
          AND end_date > ?
    """, (
        request_row["departure_date"],
        request_row["arrival_date"]
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
            request_row["departure_date"],
            request_row["arrival_date"]
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

        if is_coordination_converted_request:
            approval_email_status = "not_needed"
            approval_email_needed_type = ""

        conn.execute("""
            UPDATE booking_requests
            SET status = ?,
                response_message = ?,
                email_status = ?,
                email_needed_type = ?
            WHERE id = ?
        """, (
            "approved",
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
                request_row["arrival_date"],
                request_row["departure_date"],
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
            "request_approved",
            request_row["status"],
            "approved",
            f"Rooms assigned: {', '.join(selected_room_names)}. Backup: {backup_path}"
        )

        conn.commit()

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

    conn.close()

    if is_coordination_converted_request:

        coordination_group_id = ""

        try:
            coordination_group_id = request_row["coordination_group_id"]
        except:
            coordination_group_id = ""

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
            request_row["departure_date"],
            "%Y-%m-%d"
        )
        -
        datetime.strptime(
            request_row["arrival_date"],
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

    additional_names = safe_text(
        request_row["additional_names"]
    ).strip()

    if not additional_names:
        additional_names = "None listed"

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

    approval_email_body = render_email_template(
        "approval.txt",
        guest_name=safe_text(request_row["name"]),
        arrival_date=format_date(
            request_row["arrival_date"]
        ),
        departure_date=format_date(
            request_row["departure_date"]
        ),
        nights=nights,
        rooms_requested=rooms_requested,
        additional_names=additional_names,
        room_list=room_list,
        coordinating_with_section=coordinating_with_section,
        optional_admin_message=optional_admin_message
    )

    approval_email_body += (
        "\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Need to Change or Cancel?\n\n"
        "If your plans change, please use one of the links below.\n\n"
        f"Request a change:\n"
        f"{BASE_URL}/request/{request_id}/change\n\n"
        f"Cancel this visit:\n"
        f"{BASE_URL}/request/{request_id}/cancel\n\n"
        "Changes are not automatic. "
        "We will review any requested changes "
        "and follow up by email.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    while "\n\n\n" in approval_email_body:
        approval_email_body = approval_email_body.replace(
            "\n\n\n",
            "\n\n"
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
            <td>{safe_text(booking['room_name'])}</td>
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
                    <td colspan="12"
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

            else:

                guest_name_html = ""

                email_html = ""

                additional_guest_html = ""

                planning_details_display = ""

                email_status_html = ""

                view_html = ""

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

        try:
            conn.execute("""
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

            conn.commit()

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

    html = nav_links() + f"""
    <h1>Guest Profiles</h1>

    <p>
        <a href="/profiles">All</a> |
        <a href="/profiles?filter=active">Active</a> |
        <a href="/profiles?filter=needs_review">Needs Review</a> |
        <a href="/profiles?filter=archived">Archived</a>
    </p>

    <h2>Add Guest Profile</h2>

    <form method="POST" action="/profiles">
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
        </select><br>

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

                <td>
                    <a href="/profile/{profile['id']}">View</a> |
                    <a href="/profile/{profile['id']}/edit">Edit</a>
                </td>

                <td>{action_links}</td>
            </tr>
            """

        html += "</table>"

    return html

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

            elif status == "responded" or status == "replied":

                status_display = """
                <strong style='color: purple; font-size: 14px;'>
                    RESPONDED
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


@app.route("/invitation/<int:invitation_id>/status/<new_status>")
def update_invitation_status(invitation_id, new_status):

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
            request_link = f"{BASE_URL}/"

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
            request_id
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

    blocked_conflict = conn.execute("""
        SELECT *
        FROM blocked_dates
        WHERE start_date < ?
          AND end_date > ?
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

@app.route("/approve-cancel/<int:request_id>")
def approve_cancel(request_id):

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

    conn.close()

    rooms_requested = int(req["rooms_requested"] or 1)

    nights = (
        datetime.strptime(req["departure_date"], "%Y-%m-%d")
        - datetime.strptime(req["arrival_date"], "%Y-%m-%d")
    ).days

    additional_names = safe_text(req["additional_names"]).strip()

    if not additional_names:
        additional_names = "None listed"

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
            change_links_section=request_change_links(request_id)
        )

        body = ensure_guest_change_links(
            body,
            request_id
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
            request_link = f"{BASE_URL}/"

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
            request_id
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
            change_links_section=request_change_links(request_id)
        )

        body = ensure_guest_change_links(
            body,
            request_id
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

        blocked_conflict = conn.execute("""
            SELECT *
            FROM blocked_dates
            WHERE start_date < ?
              AND end_date > ?
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

        <label>Children:</label><br>

        <input type="number"
               name="children"
               value="{req['children']}"><br>

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

    <p>
        <a href="/request/{request_id}">
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

    for booking in existing_bookings:
        request_id = booking["request_id"]

        if request_id not in assigned_rooms_by_request:
            assigned_rooms_by_request[request_id] = []

        assigned_rooms_by_request[request_id].append(booking["room_name"])

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

            if assigned_rooms:
                room_display = "<ul style='margin: 0; padding-left: 16px;'>"

                for assigned_room in assigned_rooms:
                    room_display += f"<li>{assigned_room}</li>"

                room_display += "</ul>"
            else:
                room_display = "None"

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
    error_message = ""

    if request.method == "POST":
        start_date = clean_text(request.form.get("start_date"))
        end_date = clean_text(request.form.get("end_date"))
        reason = clean_text(request.form.get("reason"))

        if not valid_date_range(start_date, end_date):
            error_message = "Start date and end date are required, and end date cannot be before start date."
        else:
            conn.execute("""
                INSERT INTO blocked_dates
                (start_date, end_date, reason)
                VALUES (?, ?, ?)
            """, (
                start_date,
                end_date,
                reason
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

    html = nav_links() + f"""
    <h1>House Blocks</h1>

    {error_html}

    <h2>Add House Block</h2>

    <form method="POST" action="/blocked">
        <label>Start Date:</label><br>
        <input type="date" name="start_date" required><br>

        <label>End Date:</label><br>
        <input type="date" name="end_date" required><br>

        <label>Reason:</label><br>
        <input type="text" name="reason"><br>

        <button type="submit">Add Block</button>
    </form>

    <h2>Current House Blocks</h2>
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

            invalid_note = ""

            if not parsed_start or not parsed_end:
                invalid_note = "<br><strong style='color:red;'>Needs repair</strong>"

            html += f"""
            <tr>
                <td>{safe_text(start_short)}</td>
                <td>{safe_text(end_short)}</td>
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

        if not valid_date_range(start_date, end_date):
            error_message = "Start date and end date are required, and end date cannot be before start date."
        else:
            conn.execute("""
                UPDATE blocked_dates
                SET start_date = ?,
                    end_date = ?,
                    reason = ?
                WHERE id = ?
            """, (
                start_date,
                end_date,
                reason,
                block_id
            ))

            conn.commit()
            conn.close()

            return redirect("/blocked")

    start_value = safe_text(block["start_date"]).strip()
    end_value = safe_text(block["end_date"]).strip()
    reason_value = safe_text(block["reason"]).strip()

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

    conn.close()

    if not invite:

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

    body = render_email_template(
        "invitation.txt",
        guest_name=safe_text(invite["primary_name"]),
        message=safe_text(message),
        request_link=request_link,
        coordination_link=coordination_link
    )

    template_metadata = email_template_metadata_html("invitation")

    html = nav_links() + f"""
    <h1>
        Invitation Email Preview
    </h1>

    {template_metadata}

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

        <textarea name="body"
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
    body = request.form.get("body")

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
           cellpadding="3"
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

    <p><a href="/">Start a New Request</a></p>
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

    blocked_dates = set()

    for b in blocked:
        start = datetime.strptime(b["start_date"], "%Y-%m-%d")
        end = datetime.strptime(b["end_date"], "%Y-%m-%d")

        current = start

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

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

        room_capacity[current.strftime("%Y-%m-%d")] = max(
            0,
            total_rooms - rooms_used - tentative_rooms_held
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

    <table border="1" cellpadding="3" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 920px; font-size: 12px;">
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
            <span style="font-size: 11px;">{display_line_1}</span><br>
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

    <h3>
        Current Approved Details
    </h3>

    <p>
        <strong>
            Guest:
        </strong>

        {request_row['name']}
    </p>

    <p>
        <strong>
            Email:
        </strong>

        {request_row['email']}
    </p>

    <p>
        <strong>
            Current Arrival:
        </strong>

        {format_date(request_row['arrival_date'])}
    </p>

    <p>
        <strong>
            Current Departure:
        </strong>

        {format_date(request_row['departure_date'])}
    </p>

    <p>
        <strong>
            Current Rooms Requested:
        </strong>

        {current_rooms}
    </p>

    <hr>

    <form method="POST">

        <h3>
            Requested Change
        </h3>

        <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px 12px; border-radius: 8px; max-width: 760px; margin-bottom: 12px;">
            <strong>Use the calendar before submitting.</strong><br>
            This shows blocked dates, full dates, and coordination holds so you are not guessing.
        </div>

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
            Rooms Requested
        </label><br>

        <select id="rooms_requested"
                name="rooms_requested"
                style="
                    width: 160px;
                    padding: 8px;
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

    new_request_link = "/"

    if request_row["invitation_id"]:
        new_request_link = f"/invite/{request_row['invitation_id']}"

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

                cancellation_body = f"""Hi {request_row['name']},

Your Strathmere visit has been cancelled.

Cancelled Visit Details:
- Arrival: {format_date(request_row['arrival_date'])}
- Departure: {format_date(request_row['departure_date'])}
- Nights: {nights}
- Rooms: {rooms_requested}

If you would like to request different dates, please use this link:
{BASE_URL}{new_request_link}

Thanks for letting us know.

John & Mark
302-521-5401
"""

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
            elif status_display == "finalized":
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
            status = "planning"

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

        conn.commit()
        conn.close()

        return redirect(
            f"/coordination-group/{group_id}"
        )

    current_year = date.today().year

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
            coordination_group_members.*,
            guest_profiles.primary_name,
            guest_profiles.primary_email,
            COUNT(coordination_date_options.id) AS date_option_count
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
        SELECT start_date, end_date
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
                Tentative Group Dates
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
                    All Guests Confirmed Tentative Dates
                </h2>

                <p>
                    This coordination group is ready for final communication
                    and booking request creation.
                </p>

                <p>
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
                        Go to Booking Handoff
                    </a>
                </p>

                <p style="font-size: 13px; color: #555; margin-bottom: 0;">
                    Planning is complete. Use Booking Handoff for guest confirmations, booking requests, room assignments, final confirmation, and closing.
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
        )

        if not due_date_display:
            due_date_display = "No due date set"

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
        <strong>Current Round:</strong> Round {current_round} — {round_status_label}<br>
        <strong>Status:</strong> {round_waiting_text}
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

        if member_invitation_status in ["sent", "viewed", "responded"]:

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

    planning_workflow_html = f"""
    <h2>Planning Workflow</h2>

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

            <tr style="background-color: {planning_invitation_background};">
                <td><strong>1. Send Coordination Invitations</strong></td>
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
                <td><strong>2. Collect Responses</strong></td>
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
                <td><strong>3. Review Date Overlap</strong></td>
                <td>{overlap_icon} {overlap_state}</td>
                <td>Review best match suggestions and unmatched guests below.</td>
                <td><a href="#best-match-suggestions">View Suggestions</a></td>
            </tr>

            <tr style="background-color: {tentative_background};">
                <td><strong>4. Select Tentative Dates</strong></td>
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

            if created_request["approved_room_names"]:
                room_assignment_display = safe_text(
                    created_request["approved_room_names"]
                )

            request_status_display_text = safe_text(
                created_request["request_status"]
            )

            row_background = "#fff3cd"

            if request_status_display_text == "approved" and created_request["approved_booking_count"] > 0:
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
                <td>{email_status_display(created_request['email_status'], created_request['email_needed_type'], created_request['converted_request_id'])}</td>
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

    html = nav_links() + f"""
    <h1>{safe_text(group['title'])} — Planning</h1>

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

    {planning_workflow_html}

    <h2>Current Coordination Status</h2>

    <div style="
        background-color: #f8f9fa;
        padding: 14px;
        border-radius: 8px;
        margin-bottom: 18px;
        max-width: 1080px;
        line-height: 1.5;
    ">
        <p style="margin-top: 0;">
            <strong>Status:</strong> {safe_text(group['status'])}<br>
            <strong>Target Year:</strong> {safe_text(group['target_year'])}<br>
            <strong>Created:</strong> {safe_text(group['created_at'])[:10]}<br>
            <strong>Confirmed Works:</strong> {tentative_confirmed_count} |
            <strong>Cannot Make:</strong> {tentative_cannot_count} |
            <strong>Need Different Dates (Add Comments):</strong> {tentative_discussion_count} |
            <strong>No Response:</strong> {tentative_no_response_count}
        </p>

        <div style="
            background-color: #ffffff;
            border: 1px solid #dee2e6;
            padding: 10px;
            border-radius: 6px;
            margin-bottom: 12px;
        ">
            {safe_text(group['description'])}
        </div>

        <h3>Tentative Group Dates</h3>

        {tentative_dates_html}
    </div>

    <h2 id="best-match-suggestions">Best Match Suggestions</h2>

    <div style="
        border: 1px solid #dee2e6;
        background-color: #eef7ee;
        padding: 14px;
        margin-bottom: 18px;
        border-radius: 8px;
        max-width: 1080px;
    ">
        <p style="margin-top: 0;">
            These suggestions compare submitted preferred and alternate dates. Use them to pick tentative dates. After tentative dates are picked, this becomes reference information.
        </p>

        {match_suggestions_html}
    </div>

    <h2>Submitted Date Options — Reference Only</h2>

    <div style="
        border: 1px solid #dee2e6;
        background-color: #ffffff;
        padding: 14px;
        margin-bottom: 18px;
        border-radius: 8px;
        max-width: 980px;
    ">
        <p>
            <strong>Members:</strong> {len(members)}<br>
            <strong>Responded:</strong> {responded_count}<br>
            <strong>Not Responded:</strong> {len(not_responded_names)}
        </p>

        <p>
            <strong>Waiting On:</strong><br>
            {not_responded_html}
        </p>

        {date_options_summary_html}
    </div>

    <h2>Group Members</h2>

    <div style="
        border: 1px solid #dee2e6;
        background-color: #f8f9fa;
        padding: 14px;
        margin-bottom: 18px;
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
                <option value="guest">Guest</option>
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
                <td>{safe_text(member['role'])}</td>
                <td>{safe_text(member['invitation_status'])}</td>
                <td>{safe_text(member['last_response_at'])}</td>
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
    <p>
        <a href="/coordination-groups">
            Back to Coordination Groups
        </a>
    </p>
    """

    return html




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

    if not default_tentative_due_date:
        default_tentative_due_date = (
            date.today() + timedelta(days=3)
        ).strftime("%Y-%m-%d")

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
                       value="{default_tentative_due_date}">
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
                       value="{default_tentative_due_date}">
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
                    Send Tentative Date Confirmation Emails
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
        SELECT start_date, end_date
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

    group_member_text = "\n".join([f"- {safe_text(member['primary_name'])} ({safe_text(row_value(member, 'role') or 'guest')})" for member in members])

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

    group_member_text = "\n".join([f"- {safe_text(member['primary_name'])} ({safe_text(row_value(member, 'role') or 'guest')})" for member in members])

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

    email_preview_html = ""

    for member in email_target_members:

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request"

        subject = f"Strathmere group date coordination - {safe_text(group['title'])}"

        body = render_email_template(
            "coordination_invitation.txt",
            guest_name=safe_text(member["primary_name"]),
            group_title=safe_text(group["title"]),
            guest_role=safe_text(row_value(member, "role") or "guest"),
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

    blocked_ranges = conn.execute("""
        SELECT start_date, end_date
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

    blocked_ranges = conn.execute("""
        SELECT start_date, end_date
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
        SELECT start_date, end_date
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
        [f"- {safe_text(member['primary_name'])} ({safe_text(row_value(member, 'role') or 'guest')})" for member in members]
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
            guest_role=safe_text(row_value(member, "role") or "guest"),
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

    blocked_dates = set()

    for b in blocked:
        start = datetime.strptime(b["start_date"], "%Y-%m-%d")
        end = datetime.strptime(b["end_date"], "%Y-%m-%d")

        current = start

        while current <= end:
            blocked_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

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

        room_capacity[current.strftime("%Y-%m-%d")] = total_rooms - rooms_used

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
                width: 42px;
                height: 32px;
                font-size: 11px;
                text-align: center;
                cursor: {cursor};
                padding: 2px;
            " title="{display_line_1} {display_line_2}">
            <strong>{day}</strong>
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
                <td>{safe_text(booking['room_name'])}</td>
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

        for suggestion in group_match_suggestions[:5]:

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

    follow_up_notice_html = ""
    follow_up_dates_work_button = ""

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
            border: 1px solid #f0ad4e;
            background-color: #fff3cd;
            padding: 8px 10px;
            margin-bottom: 6px;
            border-radius: 8px;
            max-width: 1100px;
            font-size: 13px;
            line-height: 1.3;
        ">
            <strong>Quick favor — can you take another look?</strong>
            {suggested_dates_text}
            <br>If these dates work, click that button. If not, update what you can or use <strong>I cannot change any dates</strong>.
            {follow_up_dates_work_button}
        </div>
        """

    html = nav_links() + f"""
    <h1 style="margin: 0 0 4px 0; font-size: 22px;">Pick / Update Your Dates</h1>

    {follow_up_notice_html}

    <div style="
        border: 1px solid #dee2e6;
        background-color: #f8f9fa;
        padding: 5px 8px;
        margin-bottom: 5px;
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

    <div style="
        border: 1px solid #dee2e6;
        background-color: #ffffff;
        padding: 5px 8px;
        margin-bottom: 5px;
        border-radius: 8px;
        max-width: 1100px;
    ">
        <h2 style="margin: 0 0 3px 0; font-size: 17px;">Group Members</h2>
        <ul style="margin: 0; font-size: 13px; line-height: 1.25;">
            {group_member_list_html}
        </ul>
    </div>

    <div style="
        border: 1px solid #198754;
        background-color: #e8f7ea;
        padding: 5px 8px;
        margin-bottom: 5px;
        border-radius: 8px;
        max-width: 1100px;
    ">
        <h2 style="margin: 0 0 3px 0; font-size: 17px;">
            Tentative Group Dates
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
            Current Best Group Dates
        </h2>

        <p style="margin: 0 0 5px 0; font-size: 13px; line-height: 1.25;">
            Current best overlap options based on group date choices. These are not confirmed bookings.
        </p>

        {group_overlap_html}
    </div>

    <div style="
        display: grid;
        grid-template-columns: minmax(320px, 0.9fr) minmax(420px, 1.1fr);
        gap: 10px;
        align-items: start;
        max-width: 1180px;
    ">
        <div style="
            border: 1px solid #dee2e6;
            background-color: #ffffff;
            padding: 10px;
            border-radius: 8px;
            font-size: 14px;
        ">
            <h2 style="
                margin: 0 0 6px 0;
                font-size: 22px;
                font-weight: bold;
            ">
                Your Submitted Dates
            </h2>

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

            <h3 style="margin: 10px 0 4px 0;">Previous Approved Stays</h3>
            {previous_html}

            <h3 style="margin: 10px 0 4px 0;">Guest / Room Notes</h3>

            <p style="margin: 4px 0;">
                <strong>Additional Guests for Your Room(s):</strong><br>
                {safe_text(member['additional_names'])}
            </p>

            <p style="margin: 4px 0;">
                <strong>Pets:</strong><br>
                {safe_text(member['pet_notes'])}
            </p>

            <p style="margin: 4px 0;">
                <strong>Food Preferences:</strong><br>
                {safe_text(member['food_notes'])}
            </p>
        </div>

        <div style="
            border: 1px solid #dee2e6;
            background-color: #ffffff;
            padding: 8px;
            border-radius: 8px;
        ">
            {calendar_html}

            <div style="
                border: 1px solid #dee2e6;
                background-color: #f8f9fa;
                padding: 8px;
                border-radius: 8px;
                margin-top: 8px;
            ">
                <h2 style="margin: 0 0 4px 0;">
                    Submit / Update Date Options
                </h2>

                <p style="margin: 0 0 4px 0; font-size: 13px;">
                    Add the dates that work best for you. Preferred is your first choice; alternate is your backup plan.
                </p>

                <p style="color: #856404; font-weight: bold; margin: 0 0 6px 0; font-size: 13px;">
                    After saving, you’ll see a quick confirmation page. No beach paperwork, promise.
                </p>

                <form method="POST"
                      action="/coordination-group-member/{member_id}/date-options"
                      onsubmit="return validateGroupRoomCapacity();">

                    <div style="
                        background-color: #fff3cd;
                        border: 1px solid #fd7e14;
                        padding: 6px;
                        border-radius: 6px;
                        margin-bottom: 6px;
                        font-size: 13px;
                        font-weight: bold;
                        white-space: normal;
                        overflow-wrap: anywhere;
                    ">
                        Choose what the calendar click should fill: preferred arrival, preferred departure, alternate arrival, or alternate departure.
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
                        border: 2px solid #0d6efd;
                        background-color: #f8fbff;
                        padding: 10px;
                        border-radius: 8px;
                        margin-bottom: 10px;
                        max-width: 520px;
                    ">
                        <label for="default_rooms" style="font-size: 18px; font-weight: bold;">
                            How many guest bedrooms do you need?
                        </label><br>
                        <span style="font-size: 15px; font-weight: bold;">
                            Each bedroom sleeps up to 2 guests.
                        </span><br>
                        <select id="default_rooms"
                                onchange="syncDefaultRooms(); validateGroupRoomCapacity(false);">
                            <option value="1">1 Bedroom</option>
                            <option value="2">2 Bedrooms</option>
                            <option value="3">3 Bedrooms</option>
                            <option value="4">4 Bedrooms</option>
                        </select>
                        <p style="font-size: 12px; color: #555; margin: 6px 0 0 0;">
                            This fills the bedroom count for both preferred and alternate dates. You can still adjust each date option below if needed.
                        </p>
                    </div>

                    <div style="
                        display: grid;
                        grid-template-columns: repeat(2, minmax(240px, 1fr));
                        gap: 8px;
                    ">
                        <div style="
                            border: 1px solid #dee2e6;
                            background-color: #ffffff;
                            padding: 8px;
                            border-radius: 8px;
                        ">
                            <h3 style="margin: 0 0 4px 0;">
                                Preferred Dates
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
            updateCalendarTargetMessage();
        }}
    </script>

    <p>
        <a href="/coordination-group/{member['coordination_group_id']}">
            Back to Coordination Group
        </a>
    </p>
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

    if total_group_rooms > total_rooms:

        conn.close()

        return f"""
        {nav_links()}

        <h1>Date Options Not Saved</h1>

        <p style="color: red; font-weight: bold;">
            The group is requesting {total_group_rooms} room(s), but only {total_rooms} room(s) are available.
        </p>

        <p>
            No single date range can work for the full group until the room count is reduced,
            the group is split, or the plan is changed.
        </p>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; max-width: 520px;">
            <tr style="background-color: #f5f5f5;">
                <th align="left">Guest</th>
                <th align="center">Rooms</th>
            </tr>
            {room_detail_rows}
            <tr style="background-color: #fff3cd; font-weight: bold;">
                <td>Total Requested</td>
                <td align="center">{total_group_rooms}</td>
            </tr>
        </table>

        <p>
            <a href="/coordination-group-member/{member_id}/request">
                Back to Coordination Request
            </a>
        </p>
        """

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
            "responded",
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

    arrival_date = clean_text(
        request.form.get("arrival_date")
    )

    departure_date = clean_text(
        request.form.get("departure_date")
    )

    try:
        arrival = datetime.strptime(
            arrival_date,
            "%Y-%m-%d"
        )

        departure = datetime.strptime(
            departure_date,
            "%Y-%m-%d"
        )

        if departure <= arrival:
            raise ValueError("Departure must be after arrival.")

    except Exception as error:

        return transaction_error_page(
            error,
            f"/coordination-group/{group_id}"
        )

    conn = get_db_connection()

    ensure_coordination_tables(conn)

    try:

        conn.execute("""
            UPDATE coordination_groups
            SET tentative_arrival_date = ?,
                tentative_departure_date = ?,
                tentative_selected_at = CURRENT_TIMESTAMP,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            arrival_date,
            departure_date,
            "tentative",
            group_id
        ))

        conn.execute("""
            UPDATE coordination_group_members
            SET tentative_response_status = NULL,
                tentative_response_at = NULL,
                tentative_response_notes = NULL
            WHERE coordination_group_id = ?
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

    return redirect(
        f"/coordination-group/{group_id}"
    )


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

        subject = f"Strathmere tentative dates reminder - {safe_text(group['title'])}"

        update_link = f"{BASE_URL}/coordination-group-member/{coordination_member_row_id(member)}/request"

        body = f"""Hi {safe_text(member['primary_name'])},

Just a reminder that we are trying to confirm tentative dates for {safe_text(group['title'])}.

Tentative dates:
{format_date(group['tentative_arrival_date'])} to {format_date(group['tentative_departure_date'])}

Current response due date: {format_date(tentative_response_due_date)}

Please use your link below to confirm whether these dates work, cannot work, or need discussion:

{update_link}

Need to make a change?

Change / Review Dates:
{update_link}

Cancel / Cannot Make These Dates:
{update_link}

Start a New Request:
{BASE_URL}

Nothing is fully booked yet. This just helps us coordinate the group before final approvals.

John & Mark
302-521-5401
"""

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

    <h1>Tentative Date Confirmation Emails Sent</h1>

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

        email_body = f"""
Hi {safe_text(final_request['name'])},

Your Strathmere visit is confirmed.

VISIT DETAILS:
- Arrival: {format_date(final_request['arrival_date'])}
- Departure: {format_date(final_request['departure_date'])}
- Nights: {nights}
- Room(s): {room_list}
- Rooms Requested: {safe_text(final_request['rooms_requested'])}

Confirmed Group Members:
{group_member_list_text}

Additional Guests for Your Room(s):
{safe_text(final_request['additional_names']) or 'None listed'}

Food Preferences / Restrictions:
{safe_text(final_request['food_restrictions']) or 'None listed'}

Pets:
{safe_text(final_request['pets']) or 'None listed'}

Need to change or cancel this visit?
Change request: {BASE_URL}/request/{final_request['id']}/change
Cancel request: {BASE_URL}/request/{final_request['id']}/cancel
New request: {BASE_URL}

If anything does not look right, just reply to this email.

Looking forward to seeing everyone at the shore!

John & Mark
302-521-5401
"""

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

    if role not in ["guest", "organizer"]:
        role = "guest"

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

    conn.execute("""
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

    conn.commit()
    conn.close()

    return redirect(
        f"/coordination-group/{group_id}"
    )


@app.errorhandler(Exception)
def production_error_handler(error):

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

# V28_15D_EXACT_GUEST_SECTION_UNIFORM_FONT_PATCH
