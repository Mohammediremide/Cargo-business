import os
from dotenv import load_dotenv
load_dotenv() # Load variables from .env file
import json
import psycopg
from psycopg.types.json import Json
import uuid
import hmac
import hashlib
import requests
import shutil
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import random

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)

# Config
KORA_SECRET_KEY = (
    os.environ.get('KORA_SECRET_KEY')
    or os.environ.get('KORAPAY_SECRET_KEY')
    or os.environ.get('KORA_API_KEY')
)
KORA_PUBLIC_KEY = os.environ.get('KORA_PUBLIC_KEY') or os.environ.get('KORAPAY_PUBLIC_KEY')
KORA_WEBHOOK_SECRET = os.environ.get('KORA_WEBHOOK_SECRET') or KORA_SECRET_KEY
KORA_AMOUNT_MULTIPLIER = float(os.environ.get('KORA_AMOUNT_MULTIPLIER', '1'))
KORA_CURRENCY = os.environ.get('KORA_CURRENCY', 'NGN')
KORA_CHANNELS = [
    ch.strip().lower()
    for ch in os.environ.get('KORA_CHANNELS', 'bank_transfer').split(',')
    if ch.strip()
]
if not KORA_CHANNELS:
    KORA_CHANNELS = ['bank_transfer']
KORA_API_BASE = 'https://api.korapay.com/merchant/api/v1'
KORA_REDIRECT_URL = os.environ.get('KORA_REDIRECT_URL')
KORA_WEBHOOK_URL = os.environ.get('KORA_WEBHOOK_URL')

# Email Config (Brevo REST API)
BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
GEONAMES_USERNAME = os.environ.get('GEONAMES_USERNAME', 'MohMik')
EMAIL_FROM = os.environ.get('EMAIL_FROM', 'odewunmimohammed@gmail.com')
EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME', 'CargoFish')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'odewunmimohammed@gmail.com')

# Admin Credentials from Environment
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'Moh')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '123456')

if not KORA_SECRET_KEY:
    print("\n[!] WARNING: Kora API secret key is missing.")
    print("Please set KORA_SECRET_KEY (or KORAPAY_SECRET_KEY / KORA_API_KEY) environment variables.\n")

if not BREVO_API_KEY:
    print("\n[!] WARNING: BREVO_API_KEY is missing. Emails will not be sent.\n")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_VERCEL = os.environ.get('VERCEL') == '1'
DATA_DIR = os.path.join('/tmp', 'cargo_fish_data') if IS_VERCEL else BASE_DIR

# Database (Neon/Postgres)
DATABASE_URL = (
    os.environ.get('DATABASE_URL')
    or os.environ.get('POSTGRES_URL')
    or os.environ.get('POSTGRES_PRISMA_URL')
    or os.environ.get('NEON_DATABASE_URL')
)
USE_DB = bool(DATABASE_URL)
_DB_READY = False

def _db_connect():
    return psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5)

def _db_ensure():
    global _DB_READY
    if _DB_READY or not USE_DB:
        return
    with _db_connect() as conn:
        conn.execute(
            """
            create table if not exists kv_store (
                key text primary key,
                value jsonb not null,
                updated_at timestamptz not null default now()
            )
            """
        )
    _DB_READY = True

if IS_VERCEL:
    os.makedirs(DATA_DIR, exist_ok=True)


def data_path(filename):
    path = os.path.join(DATA_DIR, filename)
    if IS_VERCEL and not os.path.exists(path):
        seed = os.path.join(BASE_DIR, filename)
        if os.path.exists(seed):
            shutil.copy(seed, path)
    return path

def _data_key(filename):
    return os.path.basename(filename)


DATA_FILE = data_path('bookings.json')
PENDING_FILE = data_path('pending_payments.json')
WITHDRAWAL_FILE = data_path('withdrawals.json')
DELIVERED_FILE = data_path('delivered_bookings.json')
CONFIG_FILE = data_path('config.json')
USER_FILE = data_path('users.json')
NOTIFICATION_FILE = data_path('notifications.json')
CHAT_FILE = data_path('chat_messages.json')

DEFAULT_PRICING = {
    "Frozen Fish (Bulk)": 150,
    "Live Seafood (Tank)": 250,
    "Canned Goods": 100,
    "General Perishables": 120
}

# Helper Functions
def load_json(filename, default=None):
    if USE_DB:
        _db_ensure()
        key = _data_key(filename)
        with _db_connect() as conn:
            row = conn.execute(
                "select value from kv_store where key = %s",
                (key,)
            ).fetchone()
        if row is not None:
            value = row[0]
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return default if default is not None else {}
            return value
        # Seed from existing local file if present
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = default if default is not None else {}
            save_json(filename, data)
            return data
        return default if default is not None else {}

    if not os.path.exists(filename):
        return default if default is not None else {}
    with open(filename, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default if default is not None else {}

def save_json(filename, data):
    if USE_DB:
        _db_ensure()
        key = _data_key(filename)
        with _db_connect() as conn:
            conn.execute(
                """
                insert into kv_store (key, value, updated_at)
                values (%s, %s, now())
                on conflict (key)
                do update set value = excluded.value, updated_at = now()
                """,
                (key, Json(data))
            )
        return
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

def load_config(): return load_json(CONFIG_FILE, DEFAULT_PRICING)
def save_config(config): save_json(CONFIG_FILE, config)

def load_bookings(): return load_json(DATA_FILE)
def load_delivered(): return load_json(DELIVERED_FILE)
def load_notifications(): return load_json(NOTIFICATION_FILE)
def save_notifications(data): save_json(NOTIFICATION_FILE, data)

def load_pending_payments(): return load_json(PENDING_FILE, {})
def save_pending_payments(data): save_json(PENDING_FILE, data)

def add_pending_payment(reference, data):
    pending = load_pending_payments()
    pending[reference] = data
    save_pending_payments(pending)

def pop_pending_payment(reference):
    pending = load_pending_payments()
    data = pending.pop(reference, None)
    save_pending_payments(pending)
    return data

def remove_pending_payment(reference):
    pending = load_pending_payments()
    if reference in pending:
        pending.pop(reference, None)
        save_pending_payments(pending)

def add_notification(user_id, title, message):
    notifs = load_notifications()
    if user_id not in notifs:
        notifs[user_id] = []
    
    new_notif = {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "message": message,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "read": False
    }
    
    notifs[user_id].insert(0, new_notif) # Newest first
    notifs[user_id] = notifs[user_id][:50] # Keep last 50
    save_notifications(notifs)
def save_booking(booking_data):
    bookings = load_bookings()
    bookings[booking_data['id']] = booking_data
    save_json(DATA_FILE, bookings)

def get_booking_by_reference(reference):
    bookings = load_bookings()
    for booking in bookings.values():
        if booking.get('payment_ref') == reference:
            return booking
    return None

def calculate_total_from_items(items, pricing_config):
    total = 0.0
    for item in items:
        cargo_type = item.get('type') or item.get('cargoType') or item.get('cargo_type')
        try:
            weight = float(item.get('weight', 0))
        except (TypeError, ValueError):
            weight = 0.0
        if not cargo_type or weight <= 0:
            continue
        rate = float(pricing_config.get(cargo_type, 0) or 0)
        total += rate * weight
    return round(total, 2)

def load_withdrawals(): return load_json(WITHDRAWAL_FILE, [])
def save_withdrawal(withdrawal):
    withdrawals = load_withdrawals()
    withdrawals.append(withdrawal)
    save_json(WITHDRAWAL_FILE, withdrawals)

def load_users(): return load_json(USER_FILE)
def save_user(user_data):
    users = load_users()
    users[user_data['username']] = user_data
    save_json(USER_FILE, users)

def load_chats(): return load_json(CHAT_FILE, {})
def save_chats(data): save_json(CHAT_FILE, data)

TRACKING_STEPS = [
    ("Processing", "Order Received"),
    ("Accepted", "Booking Accepted"),
    ("In Transit", "Shipment In Transit"),
    ("Near Destination", "Near Destination"),
    ("Delivered", "Delivered")
]

STATUS_TO_INDEX = {step: idx for idx, (step, _) in enumerate(TRACKING_STEPS)}
ETA_DAYS_BY_STATUS = {
    "Processing": 5,
    "Accepted": 4,
    "In Transit": 2,
    "Near Destination": 1
}


def parse_booking_datetime(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def build_tracking_details(booking):
    status = booking.get('status', 'Processing')
    is_cancelled = status == 'Cancelled'
    is_delivered = status == 'Delivered'
    current_index = STATUS_TO_INDEX.get(status, 0)

    if is_cancelled:
        progress_percent = 0
    elif is_delivered:
        progress_percent = 100
    else:
        progress_percent = int((current_index / (len(TRACKING_STEPS) - 1)) * 100)

    timeline = []
    for idx, (key, title) in enumerate(TRACKING_STEPS):
        completed = (not is_cancelled) and (idx < current_index or (is_delivered and idx <= current_index))
        active = (not is_cancelled) and (idx == current_index) and not is_delivered
        timeline.append({
            "key": key,
            "title": title,
            "completed": completed,
            "active": active
        })

    booking_date = parse_booking_datetime(booking.get('date'))
    eta_date = None

    if not is_cancelled and not is_delivered and booking_date:
        remaining_days = ETA_DAYS_BY_STATUS.get(status, 2)
        eta_date = booking_date + timedelta(days=remaining_days)

    if is_cancelled:
        eta_label = "Shipment Cancelled"
        eta_subtext = "This shipment was cancelled and will not be delivered."
    elif is_delivered:
        eta_label = "Delivered"
        eta_subtext = "Shipment has arrived at destination."
    elif eta_date:
        eta_label = eta_date.strftime("%d %b %Y")
        eta_subtext = f"Estimated delivery in about {max((eta_date.date() - datetime.now().date()).days, 0)} day(s)."
    else:
        eta_label = "Calculating..."
        eta_subtext = "ETA will appear once shipment details are available."

    status_badge_class = "bg-slate-100 text-slate-600 border border-slate-200"
    if status == 'Delivered':
        status_badge_class = "bg-green-500/20 text-green-400 border border-green-500/30"
    elif status == 'Cancelled':
        status_badge_class = "bg-red-500/20 text-red-300 border border-red-500/30"
    elif status in ('In Transit', 'Near Destination'):
        status_badge_class = "bg-amber-500/20 text-amber-300 border border-amber-500/30"
    elif status in ('Accepted', 'Processing'):
        status_badge_class = "bg-sky-500/20 text-sky-300 border border-sky-500/30"

    return {
        "status": status,
        "is_cancelled": is_cancelled,
        "is_delivered": is_delivered,
        "progress_percent": progress_percent,
        "timeline": timeline,
        "eta_label": eta_label,
        "eta_subtext": eta_subtext,
        "status_badge_class": status_badge_class
    }

def send_email(to_email, subject, body_html):
    if not BREVO_API_KEY:
        print("[!] Email not sent: BREVO_API_KEY is missing.")
        return False
    try:
        print(f"[*] Sending email to {to_email} via Brevo API...")
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "sender": {"name": EMAIL_FROM_NAME, "email": EMAIL_FROM},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": body_html
            },
            timeout=10
        )
        if response.status_code in (200, 201):
            print(f"[+] Email sent to {to_email}")
            return True
        else:
            print(f"[!] Brevo API error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"[!] Failed to send email to {to_email}: {e}")
        return False



# Authentication Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def user_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # For API/XHR requests, return JSON so frontend can show the real error.
            wants_json = (
                request.path.startswith('/kora/')
                or request.is_json
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in request.headers.get('Accept', '')
            )
            if wants_json:
                return jsonify({"status": "error", "message": "Please log in to continue."}), 401
            flash("Please log in to continue.", "error")
            return redirect(url_for('user_login'))
        return f(*args, **kwargs)
    return decorated_function

def chat_access_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session and 'admin_logged_in' not in session:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def update_last_active():
    if 'user' in session:
        username = session['user']['username']
        users = load_users()
        if username in users:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            users[username]['last_active'] = now
            save_user(users[username])
            # Update session too so it's available without re-loading
            session['user']['last_active'] = now
def geonames_request(endpoint, params):
    if not GEONAMES_USERNAME:
        return None
    params = dict(params or {})
    params['username'] = GEONAMES_USERNAME
    try:
        resp = requests.get(endpoint, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


@app.route('/api/geo/countries')
def geo_countries():
    data = geonames_request('https://secure.geonames.org/countryInfoJSON', {})
    if not data:
        return jsonify({"error": "GeoNames unavailable. Check username or network."}), 502
    if isinstance(data, dict) and data.get('status'):
        return jsonify({"error": data.get('status', {}).get('message', 'GeoNames error')}), 502
    countries = []
    for c in data.get('geonames', []):
        countries.append({
            'name': c.get('countryName'),
            'code': c.get('countryCode'),
            'geonameId': c.get('geonameId')
        })
    countries = [c for c in countries if c.get('name') and c.get('code')]
    countries.sort(key=lambda x: x['name'])
    return jsonify(countries)


@app.route('/api/geo/states')
def geo_states():
    country = (request.args.get('country') or '').strip().upper()
    if not country:
        return jsonify([])
    info = geonames_request('https://secure.geonames.org/countryInfoJSON', {'country': country})
    if not info:
        return jsonify({"error": "GeoNames unavailable. Check username or network."}), 502
    if isinstance(info, dict) and info.get('status'):
        return jsonify({"error": info.get('status', {}).get('message', 'GeoNames error')}), 502
    if not info.get('geonames'):
        return jsonify([])
    geoname_id = info['geonames'][0].get('geonameId')
    if not geoname_id:
        return jsonify([])
    children = geonames_request('https://secure.geonames.org/childrenJSON', {'geonameId': geoname_id})
    if not children:
        return jsonify({"error": "GeoNames unavailable. Check username or network."}), 502
    if isinstance(children, dict) and children.get('status'):
        return jsonify({"error": children.get('status', {}).get('message', 'GeoNames error')}), 502
    states = []
    for s in children.get('geonames', []):
        if s.get('fcode') == 'ADM1':
            states.append({
                'name': s.get('name'),
                'code': s.get('adminCode1')
            })
    states = [s for s in states if s.get('name')]
    states.sort(key=lambda x: x['name'])
    return jsonify(states)


# Notification API
@app.route('/api/notifications')
@user_login_required
def get_notifications():
    user_id = session['user']['username']
    notifs = load_notifications().get(user_id, [])
    return jsonify(notifs)

@app.route('/api/notifications/admin')
@login_required
def get_admin_notifications():
    notifs = load_notifications().get('admin', [])
    return jsonify(notifs)

@app.route('/api/notifications/read', methods=['POST'])
@user_login_required
def mark_notif_read():
    user_id = session['user']['username']
    notif_id = request.json.get('id')
    notifs = load_notifications()
    if user_id in notifs:
        for n in notifs[user_id]:
            if n['id'] == notif_id:
                n['read'] = True
                break
        save_notifications(notifs)
    return jsonify({"status": "success"})

@app.route('/api/notifications/admin/read', methods=['POST'])
@login_required
def mark_admin_notif_read():
    notif_id = request.json.get('id')
    notifs = load_notifications()
    if 'admin' in notifs:
        for n in notifs['admin']:
            if n['id'] == notif_id:
                n['read'] = True
                break
        save_notifications(notifs)
    return jsonify({"status": "success"})


@app.route('/api/chat/conversations')
@login_required
def chat_conversations():
    users = load_users()
    chats = load_chats()
    conversations = []

    for username, user in users.items():
        messages = chats.get(username, [])
        last_message = messages[-1] if messages else None
        unread_for_admin = len([m for m in messages if m.get('recipient') == 'admin' and not m.get('read', False)])
        conversations.append({
            "username": username,
            "full_name": user.get('full_name', username),
            "has_messages": len(messages) > 0,
            "last_message": last_message.get('message') if last_message else "",
            "last_date": last_message.get('date') if last_message else "",
            "unread": unread_for_admin
        })

    conversations = sorted(
        conversations,
        key=lambda x: (x.get('last_date') or '', x.get('username') or ''),
        reverse=True
    )
    return jsonify(conversations)

@app.route('/api/chat/messages')
@chat_access_required
def chat_messages():
    users = load_users()
    chats = load_chats()

    if 'user' in session:
        conversation_user = session['user']['username']
        read_target = conversation_user
    else:
        conversation_user = (request.args.get('username') or '').strip()
        if not conversation_user:
            return jsonify([])
        if conversation_user not in users:
            return jsonify({"status": "error", "message": "User not found"}), 404
        read_target = 'admin'

    messages = chats.get(conversation_user, [])

    changed = False
    for msg in messages:
        if msg.get('recipient') == read_target and not msg.get('read', False):
            msg['read'] = True
            changed = True

    if changed:
        chats[conversation_user] = messages
        save_chats(chats)

    return jsonify(messages)

@app.route('/api/chat/send', methods=['POST'])
@chat_access_required
def chat_send():
    data = request.json or {}
    message_text = (data.get('message') or '').strip()

    if not message_text:
        return jsonify({"status": "error", "message": "Message cannot be empty"}), 400

    users = load_users()

    if 'user' in session:
        conversation_user = session['user']['username']
        sender = conversation_user
        recipient = 'admin'
    else:
        conversation_user = (data.get('username') or '').strip()
        if not conversation_user:
            return jsonify({"status": "error", "message": "Select a user first"}), 400
        if conversation_user not in users:
            return jsonify({"status": "error", "message": "User not found"}), 404
        sender = 'admin'
        recipient = conversation_user

    message_data = {
        "id": uuid.uuid4().hex[:8],
        "sender": sender,
        "recipient": recipient,
        "message": message_text[:1000],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "read": False
    }

    chats = load_chats()
    if conversation_user not in chats:
        chats[conversation_user] = []
    chats[conversation_user].append(message_data)
    chats[conversation_user] = chats[conversation_user][-200:]
    save_chats(chats)

    if sender == 'admin':
        add_notification(conversation_user, "New Admin Message", message_text[:120])
    else:
        add_notification("admin", f"Message from {conversation_user}", message_text[:120])

    return jsonify({"status": "success", "message": message_data})
@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('user_signup'))
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def user_signup():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        country_code = request.form.get('country_code')
        country_name = request.form.get('country_name')

        if not country_code or not country_name:
            flash('Please select your country.', 'error')
            return render_template('signup.html')

        if not password or len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('signup.html')

        users = load_users()
        if any(u.get('email', '').lower() == (email or '').lower() for u in users.values()):
            flash('Email already exists. Please use another email.', 'error')
            return render_template('signup.html')

        if username in users:
            flash("Username already exists.", "error")
            return render_template('signup.html')

        user_data = {
            "full_name": full_name,
            "username": username,
            "email": email,
            "password": password, # In a real app, use hashing!
            "is_verified": False,
            "is_admin": False,
            "country_code": country_code,
            "country_name": country_name,
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_user(user_data)

        otp = str(random.randint(100000, 999999))
        session['pending_user'] = username
        session['login_otp'] = otp
        session['signup_otp_sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Send Welcome & OTP Email
        subject = "Welcome to CargoFish - Your Verification Code"
        html = f"<h2>Welcome, {full_name}!</h2><p>Thank you for joining CargoFish. Your authentication code is: <b style='font-size: 24px; letter-spacing: 2px;'>{otp}</b></p><p>Please enter this code to complete your registration and log in.</p>"
        send_email(email, subject, html)

        # Send Admin Alert
        admin_subject = f"New User Signup: {full_name}"
        admin_html = f"<h3>New Customer!</h3><p><b>Name:</b> {full_name}<br><b>Username:</b> {username}<br><b>Email:</b> {email}</p>"
        send_email(ADMIN_EMAIL, admin_subject, admin_html)

        # Add In-App Notifications
        add_notification(username, "Welcome!", f"Welcome to CargoFish, {full_name}! Thanks for joining us.")
        add_notification("admin", "New Registration", f"User {username} ({full_name}) has joined the platform.")

        flash("Registration successful! An authentication code has been sent to your email.", "success")
        return redirect(url_for('verify_otp'))
    return render_template('signup.html')

    users = load_users()
    if username in users:
        flash("Username already exists.", "error")
        return render_template('signup.html')
            
        user_data = {
            "full_name": full_name,
            "username": username,
            "email": email,
            "password": password, # In a real app, use hashing!
            "is_verified": False,
            "is_admin": False,
            "country_code": country_code,
            "country_name": country_name,
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_user(user_data)
        
        otp = str(random.randint(100000, 999999))
        session['pending_user'] = username
        session['login_otp'] = otp
        session['signup_otp_sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Send Welcome & OTP Email
        subject = "Welcome to CargoFish - Your Verification Code"
        html = f"<h2>Welcome, {full_name}!</h2><p>Thank you for joining CargoFish. Your authentication code is: <b style='font-size: 24px; letter-spacing: 2px;'>{otp}</b></p><p>Please enter this code to complete your registration and log in.</p>"
        send_email(email, subject, html)
        
        # Send Admin Alert
        admin_subject = f"New User Signup: {full_name}"
        admin_html = f"<h3>New Customer!</h3><p><b>Name:</b> {full_name}<br><b>Username:</b> {username}<br><b>Email:</b> {email}</p>"
        send_email(ADMIN_EMAIL, admin_subject, admin_html)
        
        # Add In-App Notifications
        add_notification(username, "Welcome!", f"Welcome to CargoFish, {full_name}! Thanks for joining us.")
        add_notification("admin", "New Registration", f"User {username} ({full_name}) has joined the platform.")
        
        flash("Registration successful! An authentication code has been sent to your email.", "success")
        return redirect(url_for('verify_otp'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        users = load_users()
        user = users.get(username)
        if user and user['password'] == password:
            user['last_active'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_user(user)
            session['user'] = user
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for('index'))

        flash("Invalid username or password.", "error")
    return render_template('login.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        step = (request.form.get('step') or 'request').strip().lower()

        if step == 'request':
            username = (request.form.get('username') or '').strip()
            email = (request.form.get('email') or '').strip().lower()

            if not username or not email:
                flash("Username and email are required.", "error")
                return render_template('forgot_password.html', step='request')

            users = load_users()
            user = users.get(username)
            if not user or (user.get('email', '').lower() != email):
                flash("Username and email do not match our records.", "error")
                return render_template('forgot_password.html', step='request')

            otp = str(random.randint(100000, 999999))
            session['reset_username'] = username
            session['reset_email'] = email
            session['reset_otp'] = otp
            session['reset_otp_expires'] = (datetime.now() + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
            session['reset_otp_sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            subject = "CargoFish Password Reset OTP"
            html = f"<h2>Password Reset</h2><p>Your password reset code is: <b style='font-size:24px;letter-spacing:2px;'>{otp}</b></p><p>This code expires in 10 minutes.</p>"
            send_email(email, subject, html)

            flash("OTP sent to your email. Enter it below to reset your password.", "success")
            return render_template('forgot_password.html', step='verify', username=username, email=email)

        if step == 'verify':
            username = (request.form.get('username') or '').strip()
            email = (request.form.get('email') or '').strip().lower()
            action = (request.form.get('action') or '').strip().lower()

            session_user = session.get('reset_username')
            session_email = session.get('reset_email')
            session_otp = session.get('reset_otp')
            session_expiry = session.get('reset_otp_expires')
            sent_at = session.get('reset_otp_sent_at')

            if not session_user or not session_email or not session_otp or not session_expiry:
                flash("Reset session expired. Request a new OTP.", "error")
                return render_template('forgot_password.html', step='request')

            if username != session_user or email != session_email:
                flash("Reset details mismatch. Request a new OTP.", "error")
                return render_template('forgot_password.html', step='request')

            if action == 'resend':
                cooldown = 60
                if sent_at:
                    try:
                        last_sent = datetime.strptime(sent_at, '%Y-%m-%d %H:%M:%S')
                        elapsed = (datetime.now() - last_sent).total_seconds()
                        if elapsed < cooldown:
                            remaining = int(cooldown - elapsed)
                            flash(f"Please wait {remaining}s before requesting another OTP.", "error")
                            return render_template('forgot_password.html', step='verify', username=username, email=email, resend_in=remaining)
                    except Exception:
                        pass

                otp = str(random.randint(100000, 999999))
                session['reset_otp'] = otp
                session['reset_otp_expires'] = (datetime.now() + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
                session['reset_otp_sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                subject = "CargoFish Password Reset OTP"
                html = f"<h2>Password Reset</h2><p>Your password reset code is: <b style='font-size:24px;letter-spacing:2px;'>{otp}</b></p><p>This code expires in 10 minutes.</p>"
                send_email(email, subject, html)
                flash("A new OTP has been sent to your email.", "success")
                return render_template('forgot_password.html', step='verify', username=username, email=email)

            entered_otp = (request.form.get('otp') or '').strip()
            new_password = request.form.get('new_password') or ''
            confirm_password = request.form.get('confirm_password') or ''

            if not entered_otp or not new_password or not confirm_password:
                flash("All fields are required.", "error")
                return render_template('forgot_password.html', step='verify', username=username, email=email)

            if new_password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template('forgot_password.html', step='verify', username=username, email=email)

            if len(new_password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return render_template('forgot_password.html', step='verify', username=username, email=email)

            try:
                expiry_dt = datetime.strptime(session_expiry, '%Y-%m-%d %H:%M:%S')
            except Exception:
                expiry_dt = datetime.now() - timedelta(seconds=1)

            if datetime.now() > expiry_dt:
                session.pop('reset_username', None)
                session.pop('reset_email', None)
                session.pop('reset_otp', None)
                session.pop('reset_otp_expires', None)
                session.pop('reset_otp_sent_at', None)
                flash("OTP has expired. Request a new one.", "error")
                return render_template('forgot_password.html', step='request')

            if entered_otp != session_otp:
                flash("Invalid OTP.", "error")
                return render_template('forgot_password.html', step='verify', username=username, email=email)

            users = load_users()
            user = users.get(username)
            if not user or (user.get('email', '').lower() != email):
                flash("Account not found. Request a new OTP.", "error")
                return render_template('forgot_password.html', step='request')

            user['password'] = new_password
            save_user(user)

            session.pop('reset_username', None)
            session.pop('reset_email', None)
            session.pop('reset_otp', None)
            session.pop('reset_otp_expires', None)
            session.pop('reset_otp_sent_at', None)

            subject = "CargoFish Password Reset Successful"
            html = "<h3>Password Updated</h3><p>Your CargoFish password has been changed successfully. If this was not you, contact support immediately.</p>"
            send_email(user.get('email'), subject, html)
            add_notification(username, "Password Changed", "Your account password was updated successfully.")

            flash("Password reset successful. Please log in.", "success")
            return redirect(url_for('user_login'))

        flash("Invalid request.", "error")
        return render_template('forgot_password.html', step='request')

    return render_template('forgot_password.html', step='request')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if 'pending_user' not in session or 'login_otp' not in session:
        flash("Session expired. Please sign up again.", "error")
        return redirect(url_for('user_signup'))

    username = session.get('pending_user')
    users = load_users()
    user = users.get(username) if username else None

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()

        if action == 'resend':
            cooldown = 60
            sent_at = session.get('signup_otp_sent_at')
            if sent_at:
                try:
                    last_sent = datetime.strptime(sent_at, '%Y-%m-%d %H:%M:%S')
                    elapsed = (datetime.now() - last_sent).total_seconds()
                    if elapsed < cooldown:
                        remaining = int(cooldown - elapsed)
                        flash(f"Please wait {remaining}s before requesting another code.", "error")
                        return render_template('verify_otp.html', resend_in=remaining)
                except Exception:
                    pass

            if not user:
                flash("User not found. Please sign up again.", "error")
                return redirect(url_for('user_signup'))

            otp = str(random.randint(100000, 999999))
            session['login_otp'] = otp
            session['signup_otp_sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            subject = "CargoFish Signup Verification Code"
            html = f"<h2>Signup Verification</h2><p>Your authentication code is: <b style='font-size: 24px; letter-spacing: 2px;'>{otp}</b></p><p>Please enter this code to complete your registration.</p>"
            send_email(user.get('email'), subject, html)
            flash("A new verification code has been sent to your email.", "success")
            return render_template('verify_otp.html')

        entered_otp = (request.form.get('otp') or '').strip()
        if entered_otp == session.get('login_otp'):
            username = session.pop('pending_user')
            session.pop('login_otp', None)
            session.pop('signup_otp_sent_at', None)

            users = load_users()
            user = users.get(username)
            if user:
                session['user'] = user
                flash(f"Welcome, {user['full_name']}!", "success")
                return redirect(url_for('index'))

            flash("User not found.", "error")
            return redirect(url_for('user_signup'))

        flash("Invalid authentication code.", "error")

    return render_template('verify_otp.html')

@app.route('/logout')
def user_logout():
    session.pop('user', None)
    flash("You have been logged out.", "success")
    return redirect(url_for('user_login'))

@app.route('/profile/update_country', methods=['POST'])
@user_login_required
def update_profile_country():
    country_code = (request.form.get('country_code') or '').strip().upper()
    country_name = (request.form.get('country_name') or '').strip()
    if not country_code or not country_name:
        flash('Please select a valid country.', 'error')
        return redirect(url_for('profile'))

    users = load_users()
    username = session['user']['username']
    if username in users:
        users[username]['country_code'] = country_code
        users[username]['country_name'] = country_name
        save_user(users[username])
        session['user'] = users[username]
        flash('Country updated successfully.', 'success')
    else:
        flash('User not found.', 'error')
    return redirect(url_for('profile'))


@app.route('/profile')
@user_login_required
def profile():
    # Refresh user session to ensure they get the latest verified/admin status
    users = load_users()
    current_user = users.get(session['user']['username'])
    if current_user:
        session['user'] = current_user
        
    bookings = load_bookings()
    user_bookings = [b for b in bookings.values() if b.get('username') == session['user']['username']]
    user_bookings = sorted(user_bookings, key=lambda x: x['date'], reverse=True)
    return render_template('profile.html', user=session['user'], bookings=user_bookings)

@app.route('/booking')
@user_login_required
def booking():
    pricing_config = load_config()
    
    # Refresh user session to ensure they get latest verified status
    users = load_users()
    current_user = users.get(session['user']['username'])
    if current_user:
        session['user'] = current_user
        
    return render_template('booking.html', pricing_config=pricing_config, user=session['user'])

def verify_kora_charge(reference):
    if not KORA_SECRET_KEY:
        return {"status": False, "message": "Kora API key is not configured."}

    headers = {
        "Authorization": f"Bearer {KORA_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{KORA_API_BASE}/charges/{reference}"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        return response.json()
    except Exception as exc:
        return {"status": False, "message": str(exc)}


def finalize_kora_booking(reference, charge_data=None):
    existing = get_booking_by_reference(reference)
    if existing:
        return existing.get('id')

    pending = pop_pending_payment(reference)
    if not pending:
        return None

    booking_data = dict(pending)
    booking_data["payment_ref"] = reference
    booking_data["payment_method"] = "Kora Checkout Redirect"
    save_booking(booking_data)
    send_booking_emails(booking_data)
    return booking_data["id"]


@app.route('/kora/initialize', methods=['POST'])
@user_login_required
def kora_initialize():
    # Refresh verification status
    users = load_users()
    current_user = users.get(session['user']['username'])
    if current_user and not current_user.get('is_verified', False):
        return jsonify({"status": "error", "message": "Your account is pending verification. Please wait for an admin to verify your account before making a booking."}), 403

    if not KORA_SECRET_KEY:
        return jsonify({"status": "error", "message": "Kora API key is not configured."}), 500

    data = request.json or {}
    items = data.get('items', [])
    if not isinstance(items, list) or not items:
        return jsonify({"status": "error", "message": "Please add at least one cargo item."}), 400

    pricing_config = load_config()
    total = calculate_total_from_items(items, pricing_config)
    if total <= 0:
        return jsonify({"status": "error", "message": "Invalid booking total. Please review your items."}), 400

    origin = (data.get('origin') or '').strip()
    destination = (data.get('destination') or '').strip()

    booking_id = f"CF-{uuid.uuid4().hex[:8].upper()}"
    reference = f"KORA-{uuid.uuid4().hex[:12].upper()}"

    customer_email = (session['user'].get('email') or '').strip()
    if not customer_email:
        return jsonify({"status": "error", "message": "Your account email is missing. Please update your profile before paying."}), 400

    customer_name = session['user'].get('full_name') or session['user'].get('username') or 'Customer'

    pending_data = {
        "id": booking_id,
        "items": items,
        "total_price": round(total, 2),
        "origin": origin,
        "destination": destination,
        "customer_name": customer_name,
        "username": session['user']['username'],
        "email": customer_email,
        "status": "Processing",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    for key in (
        "origin_country_code",
        "origin_country_name",
        "origin_state",
        "destination_country_code",
        "destination_country_name",
        "destination_state",
        "destination_address"
    ):
        if key in data:
            pending_data[key] = data.get(key)

    add_pending_payment(reference, pending_data)
    # In-app notifications right after booking is created
    add_notification(session['user']['username'], "Booking Initiated", f"Your booking {booking_id} has been created. Complete payment to confirm.")
    add_notification("admin", "New Booking Initiated", f"Booking {booking_id} started by {session['user']['username']} (NGN {pending_data['total_price']}).")

    amount_for_kora = int(round(total * KORA_AMOUNT_MULTIPLIER))
    if amount_for_kora <= 0:
        remove_pending_payment(reference)
        return jsonify({"status": "error", "message": "Invalid payment amount."}), 400

    channels_for_request = list(KORA_CHANNELS)
    if (
        KORA_CURRENCY.upper() == 'NGN'
        and 'bank_transfer' in channels_for_request
        and not (100 <= amount_for_kora <= 50000)
    ):
        if len(channels_for_request) > 1:
            channels_for_request = [c for c in channels_for_request if c != 'bank_transfer']
        else:
            remove_pending_payment(reference)
            return jsonify({
                "status": "error",
                "message": "Bank transfer amount must be between NGN100 and NGN50000. Reduce amount or enable card channel in Kora and set KORA_CHANNELS."
            }), 400

    payload = {
        "amount": amount_for_kora,
        "currency": KORA_CURRENCY,
        "reference": reference,
        "redirect_url": KORA_REDIRECT_URL or url_for('kora_redirect', _external=True),
        "notification_url": KORA_WEBHOOK_URL or url_for('kora_webhook', _external=True),
        "channels": channels_for_request,
        "customer": {
            "name": customer_name,
            "email": customer_email
        },
        "metadata": {
            "booking-id": booking_id,
            "username": session['user']['username']
        }
    }

    headers = {
        "Authorization": f"Bearer {KORA_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(f"{KORA_API_BASE}/charges/initialize", json=payload, headers=headers, timeout=20)
        res_data = response.json()
        print(f"[KORA INIT] Status {response.status_code}: {res_data}")
    except Exception as exc:
        remove_pending_payment(reference)
        return jsonify({"status": "error", "message": str(exc)}), 400

    if not res_data.get('status'):
        remove_pending_payment(reference)
        error_message = res_data.get('message') or res_data.get('error') or "Unable to initialize payment."
        
        # Extract specific validation errors from Kora's nested 'data' object
        data_obj = res_data.get('data')
        if isinstance(data_obj, dict):
            error_details = []
            for k, v in data_obj.items():
                if isinstance(v, dict) and 'message' in v:
                    error_details.append(f"{k}: {v['message']}")
            if error_details:
                error_message = error_message + " (" + "; ".join(error_details) + ")"
                
        return jsonify({"status": "error", "message": error_message}), 400

    checkout_url = res_data.get('data', {}).get('checkout_url')
    if not checkout_url:
        remove_pending_payment(reference)
        return jsonify({"status": "error", "message": "Payment link not available. Please try again."}), 400

    return jsonify({"status": "success", "checkout_url": checkout_url})


@app.route('/kora/redirect')
def kora_redirect():
    reference = request.args.get('reference')
    if not reference:
        flash("Missing payment reference.", "error")
        return redirect(url_for('booking' if 'user' in session else 'index'))

    verify_data = verify_kora_charge(reference)
    if not verify_data.get('status'):
        flash(verify_data.get('message') or "Payment verification failed.", "error")
        return redirect(url_for('booking' if 'user' in session else 'index'))

    charge_data = verify_data.get('data', {})
    charge_status = (charge_data.get('status') or '').lower()

    if charge_status == 'success':
        booking_id = finalize_kora_booking(reference, charge_data)
        if booking_id:
            flash("Payment confirmed. Your shipment has been scheduled.", "success")
            return redirect(url_for('receipt', booking_id=booking_id))
        flash("Payment confirmed, but booking data was missing. Please contact support.", "error")
        return redirect(url_for('booking' if 'user' in session else 'index'))

    if charge_status in ('pending', 'processing'):
        flash("Payment is pending. We'll confirm it as soon as the bank completes the transfer.", "success")
        return redirect(url_for('payment_pending', reference=reference))

    flash(f"Payment not completed (status: {charge_status or 'unknown'}).", "error")
    return redirect(url_for('booking' if 'user' in session else 'index'))

@app.route('/payment/pending')
@user_login_required
def payment_pending():
    reference = request.args.get('reference')
    if not reference:
        flash("Missing payment reference.", "error")
        return redirect(url_for('booking'))
    return render_template('payment_pending.html', reference=reference, user=session['user'])

@app.route('/kora/status')
@user_login_required
def kora_status():
    reference = request.args.get('reference')
    if not reference:
        return jsonify({"status": "error", "message": "Missing payment reference."}), 400

    existing = get_booking_by_reference(reference)
    if existing:
        return jsonify({
            "status": "success",
            "charge_status": "success",
            "booking_id": existing.get('id')
        })

    verify_data = verify_kora_charge(reference)
    if not verify_data.get('status'):
        return jsonify({
            "status": "error",
            "message": verify_data.get('message') or "Payment verification failed."
        }), 400

    charge_data = verify_data.get('data', {})
    charge_status = (charge_data.get('status') or '').lower()

    if charge_status == 'success':
        booking_id = finalize_kora_booking(reference, charge_data)
        if booking_id:
            return jsonify({
                "status": "success",
                "charge_status": "success",
                "booking_id": booking_id
            })
        return jsonify({
            "status": "error",
            "message": "Payment confirmed, but booking data was missing."
        }), 500

    if charge_status in ('pending', 'processing'):
        return jsonify({"status": "pending", "charge_status": charge_status})

    return jsonify({"status": "failed", "charge_status": charge_status or "unknown"})


@app.route('/kora/webhook', methods=['POST'])
def kora_webhook():
    if not KORA_WEBHOOK_SECRET:
        return "Missing webhook secret", 400

    signature = request.headers.get('x-korapay-signature')
    payload = request.get_json(silent=True) or {}
    data_obj = payload.get('data')

    if not signature or data_obj is None:
        return "Invalid payload", 400

    expected = hmac.new(
        KORA_WEBHOOK_SECRET.encode(),
        json.dumps(data_obj, separators=(',', ':'), ensure_ascii=False).encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return "Invalid signature", 401

    if payload.get('event') != 'charge.success':
        return "", 200

    reference = data_obj.get('reference')
    status = (data_obj.get('status') or '').lower()
    if reference and status == 'success':
        finalize_kora_booking(reference, data_obj)

    return "", 200


@app.route('/kora/webhook/test', methods=['GET'])
def kora_webhook_test():
    return jsonify({
        "status": "ok",
        "message": "Webhook endpoint is reachable."
    }), 200


def send_booking_emails(booking_data):
    # Send Booking Receipt Email
    subject = f"Booking Confirmation - {booking_data['id']}"
    html = f"<h3>Payment Received!</h3><p>Your shipment from {booking_data['origin']} to {booking_data['destination']} is being processed.<br><b>Tracking ID:</b> {booking_data['id']}</p>"
    send_email(booking_data['email'], subject, html)
    
    # Send Admin Alert for New Booking
    admin_sub = f"New Order: {booking_data['id']} - {booking_data['customer_name']}"
    admin_body = f"<h3>New Booking Received!</h3><p><b>ID:</b> {booking_data['id']}<br><b>Customer:</b> {booking_data['customer_name']}<br><b>Route:</b> {booking_data['origin']} to {booking_data['destination']}<br><b>Total:</b> NGN {booking_data['total_price']}</p>"
    send_email(ADMIN_EMAIL, admin_sub, admin_body)

    # In-App Notifications
    add_notification(booking_data['username'], "Booking Confirmed", f"Shipment {booking_data['id']} from {booking_data['origin']} to {booking_data['destination']} has been booked.")
    add_notification("admin", "New Paid Booking", f"Booking {booking_data['id']} received from {booking_data['username']} (NGN {booking_data['total_price']})")

@app.route('/track', methods=['GET', 'POST'])
def track():
    booking = None
    tracking = None
    error = None
    tracking_id = request.args.get('tracking_id') or (request.form.get('tracking_id') if request.method == 'POST' else None)

    if tracking_id:
        bookings = load_bookings()
        booking = bookings.get(tracking_id.strip())
        if not booking:
            error = "Tracking ID not found. Please check and try again."
        else:
            tracking = build_tracking_details(booking)

    return render_template('tracking.html', booking=booking, tracking=tracking, error=error, tracking_id=tracking_id)

@app.route('/history', methods=['POST'])
def history():
    email = request.form.get('email', '').strip().lower()
    if not email:
        return redirect(url_for('index'))
    
    bookings = load_bookings()
    user_bookings = [b for b in bookings.values() if b.get('email', '').lower() == email]
    user_bookings = sorted(user_bookings, key=lambda x: x['date'], reverse=True)
    
    return render_template('history.html', bookings=user_bookings, email=email)

@app.route('/receipt/<booking_id>')
def receipt(booking_id):
    bookings = load_bookings()
    booking = bookings.get(booking_id)
    if not booking:
        flash("Receipt not found.", "error")
        return redirect(url_for('index'))
    return render_template('receipt.html', booking=booking)

# Admin Routes
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error="Invalid credentials")
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    bookings = load_bookings()
    withdrawals = load_withdrawals()
    pricing_config = load_config()
    users = load_users()
    
    # Calculate balance
    # Total Revenue should only include non-cancelled orders (Active + Delivered)
    total_revenue = sum(b['total_price'] for b in bookings.values() if b.get('status') != 'Cancelled')
    
    total_withdrawn = sum(w['amount'] for w in withdrawals if w['status'] in ['Approved', 'Pending', 'success', 'Approved (Manual)', 'Processing'])
    available_balance = total_revenue - total_withdrawn
    
    # Pre-calculate counts for the dashboard
    in_progress_count = len([b for b in bookings.values() if b.get('status') not in ['Delivered', 'Cancelled']])
    completed_count = len([b for b in bookings.values() if b.get('status') == 'Delivered'])
    
    # Sort bookings by date descending
    sorted_bookings = sorted(bookings.values(), key=lambda x: x['date'], reverse=True)
    
    # In this version, we show all bookings as requested by the user
    visible_bookings = sorted_bookings
    
    # Sort withdrawals by date descending
    sorted_withdrawals = sorted(withdrawals, key=lambda x: x['date'], reverse=True)
    
    return render_template('admin_dashboard.html', 
                           bookings=visible_bookings, 
                           withdrawals=sorted_withdrawals,
                           pricing_config=pricing_config,
                           users=users,
                           total_revenue=total_revenue,
                           available_balance=available_balance,
                           in_progress_count=in_progress_count,
                           completed_count=completed_count)

@app.route('/admin/transactions')
@login_required
def admin_transactions():
    bookings = load_bookings()
    withdrawals = load_withdrawals()
    
    transactions = []
    
    for b in bookings.values():
        transactions.append({
            'id': b.get('id', 'N/A'),
            'type': 'Incoming',
            'amount': float(b.get('total_price', 0) or 0),
            'date': b.get('date', ''),
            'status': b.get('status', 'Unknown'),
            'description': f"Booking from {b.get('origin', 'N/A')} to {b.get('destination', 'N/A')}",
            'customer': b.get('customer_name', 'Unknown')
        })
        
    for w in withdrawals:
        t_id_str = str(w.get('date', '')).replace(' ', '').replace(':', '').replace('-', '')[:10]
        t_id = w.get('id', f"WD-{t_id_str}")
        transactions.append({
            'id': t_id,
            'type': 'Outgoing',
            'amount': float(w.get('amount', 0) or 0),
            'date': w.get('date', ''),
            'status': w.get('status', 'Unknown'),
            'description': f"Bank: {w.get('bank', 'N/A')} Acct: {w.get('account_number', 'N/A')}",
            'customer': 'Admin'
        })
        
    transactions.sort(key=lambda x: str(x.get('date', '')), reverse=True)
    
    total_incoming = sum(t['amount'] for t in transactions if t['type'] == 'Incoming' and t['status'] not in ['Cancelled', 'Failed'])
    total_outgoing = sum(t['amount'] for t in transactions if t['type'] == 'Outgoing' and t['status'] in ['Approved', 'Pending', 'success', 'Approved (Manual)', 'Processing'])
    
    return render_template('admin_transactions.html', 
                          transactions=transactions,
                          total_incoming=total_incoming,
                          total_outgoing=total_outgoing)



@app.route('/admin/user/<username>')
@login_required
def admin_user_history(username):
    users = load_users()
    user = users.get(username)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('admin_dashboard'))

    bookings = load_bookings()
    user_email = (user.get('email') or '').lower()
    user_bookings = [
        b for b in bookings.values()
        if b.get('username') == username or b.get('email', '').lower() == user_email
    ]
    user_bookings = sorted(user_bookings, key=lambda x: x['date'], reverse=True)

    total_paid = sum(float(b.get('total_price', 0) or 0) for b in user_bookings if b.get('status') != 'Cancelled')
    delivered_count = len([b for b in user_bookings if b.get('status') == 'Delivered'])
    cancelled_count = len([b for b in user_bookings if b.get('status') == 'Cancelled'])

    joined_at = user.get('joined_at')
    if not joined_at and user_bookings:
        joined_at = min((b.get('date') for b in user_bookings if b.get('date')), default=None)
    if not joined_at:
        joined_at = "Unknown"

    return render_template(
        'admin_user_history.html',
        user=user,
        user_bookings=user_bookings,
        joined_at=joined_at,
        total_paid=total_paid,
        delivered_count=delivered_count,
        cancelled_count=cancelled_count
    )
@app.route('/admin/update_pricing', methods=['POST'])
@login_required
def update_pricing():
    new_pricing = request.json
    if not new_pricing:
        return jsonify({"status": "error", "message": "Invalid data"}), 400
    save_config(new_pricing)
    return jsonify({"status": "success", "message": "Pricing updated successfully"})

@app.route('/admin/withdraw', methods=['POST'])
@login_required
def admin_withdraw():
    data = request.json or {}
    try:
        amount = float(data.get('amount', 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    bank_code = data.get('bank_code')
    account_number = data.get('account_number')

    if not bank_code or not account_number:
        return jsonify({"status": "error", "message": "Bank details are required"}), 400

    # Simple check for available balance (ignoring cancelled)
    bookings = load_bookings()
    withdrawals = load_withdrawals()
    total_revenue = sum(b['total_price'] for b in bookings.values() if b.get('status') != 'Cancelled')
    total_withdrawn = sum(w['amount'] for w in withdrawals if w['status'] in ['Approved', 'Pending', 'success', 'Approved (Manual)', 'Processing'])
    available_balance = total_revenue - total_withdrawn

    if amount < 1000:
        return jsonify({"status": "error", "message": "Minimum withdrawal amount is NGN 1,000"}), 400

    if amount > available_balance:
        return jsonify({"status": "error", "message": "Insufficient available balance"}), 400

    withdrawal_id = f"WD-{uuid.uuid4().hex[:8].upper()}"

    # Prepare Kora Disbursement Payload
    payload = {
        "reference": withdrawal_id,
        "destination": {
            "type": "bank_account",
            "amount": amount,
            "currency": KORA_CURRENCY,
            "narration": "CargoFish Admin Withdrawal",
            "bank_account": {
                "bank": bank_code,
                "account": account_number
            },
            "customer": {
                "name": "CargoFish Admin",
                "email": ADMIN_EMAIL
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {KORA_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(f"{KORA_API_BASE}/transactions/disburse", json=payload, headers=headers, timeout=20)
        res_data = resp.json()
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Connection error: {str(exc)}"}), 500

    if not res_data.get('status'):
        # Extract potential validation errors
        error_msg = res_data.get('message') or res_data.get('error') or "Withdrawal failed."
        data_err = res_data.get('data')
        if isinstance(data_err, dict) and 'message' in data_err:
            error_msg += f" ({data_err['message']})"
            
        return jsonify({"status": "error", "message": error_msg}), 400

    kora_data = res_data.get('data', {})
    actual_status = kora_data.get('status', 'Pending').capitalize()

    withdrawal = {
        "id": withdrawal_id,
        "amount": amount,
        "bank": bank_code,
        "account_number": account_number,
        "status": actual_status,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_withdrawal(withdrawal)
    return jsonify({"status": "success", "message": "Withdrawal processed successfully."})


@app.route('/admin/update_status', methods=['POST'])
@login_required
def update_status():
    data = request.json
    booking_id = data.get('booking_id')
    new_status = data.get('status')
    
    bookings = load_bookings()
    if booking_id in bookings:
        booking = bookings[booking_id]
        
        if new_status == 'Delivered':
            # Just mark as delivered, don't archive (per user's latest request)
            bookings[booking_id]['status'] = 'Delivered'
            save_json(DATA_FILE, bookings)
            
            # Send Final Delivery Email
            subject = f"Shipment Delivered! - {booking_id}"
            html = f"<h3>Your shipment has arrived!</h3><p>Your package from {booking['origin']} has been successfully delivered to {booking['destination']}. Thank you for choosing CargoFish!</p>"
            send_email(booking['email'], subject, html)
            
            # In-App Notification
            add_notification(booking['username'], "Shipment Delivered!", f"Your shipment {booking_id} has been successfully delivered. Thank you!")
            
            return jsonify({"status": "success", "message": "Shipment marked as delivered."})
        
        # Regular status update
        bookings[booking_id]['status'] = new_status
        save_json(DATA_FILE, bookings)
        
        # Send Status Update Email
        subject = f"Shipment Update: {booking_id}"
        html = f"<h3>Status Updated!</h3><p>Your shipment to {booking['destination']} status is now: <b>{new_status}</b>.</p>"
        send_email(booking['email'], subject, html)
        
        # In-App Notification
        add_notification(booking['username'], "Shipment Update", f"Your shipment {booking_id} status is now: {new_status}")
        
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Booking not found"}), 404

@app.route('/admin/verify_user', methods=['POST'])
@login_required
def verify_user():
    data = request.json
    username = data.get('username')
    users = load_users()
    
    if username in users:
        users[username]['is_verified'] = True
        save_user(users[username])
        
        # Notify the user
        user_email = users[username].get('email')
        subject = "Account Verified!"
        html = f"<h3>Congratulations, {users[username].get('full_name')}!</h3><p>Your account has been officially verified by the CargoFish administration.</p>"
        send_email(user_email, subject, html)
        add_notification(username, "Account Verified", "Your account has been successfully verified by our team.")
        
        return jsonify({"status": "success", "message": "User verified successfully."})
    return jsonify({"status": "error", "message": "User not found."}), 404

@app.route('/admin/make_admin', methods=['POST'])
@login_required
def make_admin():
    data = request.json
    username = data.get('username')
    users = load_users()
    
    if username in users:
        users[username]['is_admin'] = True
        save_user(users[username])
        
        # Notify the user
        user_email = users[username].get('email')
        subject = "Admin Privileges Granted!"
        html = f"<h3>Hello, {users[username].get('full_name')}!</h3><p>You have been granted Administrator privileges on CargoFish.</p>"
        send_email(user_email, subject, html)
        add_notification(username, "Admin Access Granted", "You have been granted administrator privileges.")
        
        return jsonify({"status": "success", "message": "User upgraded to admin successfully."})
    return jsonify({"status": "error", "message": "User not found."}), 404

@app.route('/admin/export/bookings')
@login_required
def export_bookings():
    import csv
    import io
    from flask import Response
    
    bookings = load_bookings()
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow(['ID', 'Customer', 'Email', 'Origin', 'Destination', 'Status', 'Price (NGN)', 'Date'])
    
    for b_id, b in bookings.items():
        writer.writerow([
            b_id,
            b.get('customer_name', ''),
            b.get('email', ''),
            b.get('origin', ''),
            b.get('destination', ''),
            b.get('status', ''),
            b.get('total_price', 0),
            b.get('date', '')
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=cargofish_bookings.csv"}
    )

@app.route('/admin/export/users')
@login_required
def export_users():
    import csv
    import io
    from flask import Response
    
    users = load_users()
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow(['Username', 'Full Name', 'Email', 'Verified', 'Admin', 'Joined At', 'Last Active'])
    
    for username, u in users.items():
        writer.writerow([
            username,
            u.get('full_name', ''),
            u.get('email', ''),
            u.get('is_verified', False),
            u.get('is_admin', False),
            u.get('joined_at', ''),
            u.get('last_active', '')
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=cargofish_users.csv"}
    )

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('images/logo.png')

@app.route('/sw.js')
def sw():
    return app.send_static_file('sw.js')

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)

















