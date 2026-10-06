import os
import sys
import sqlite3
import json
import re
import time
import threading
from datetime import datetime, timedelta
import urllib.request
import urllib.error
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'brain.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('physical', 'digital', 'service')),
            price REAL NOT NULL DEFAULT 0.0,
            description TEXT,
            stock_quantity INTEGER,
            document_link TEXT
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            zalo TEXT,
            email TEXT,
            registration_date TEXT NOT NULL DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            affiliate_code TEXT,
            order_date TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS order_reminders (
            order_id INTEGER PRIMARY KEY,
            reminder_1h_sent INTEGER DEFAULT 0,
            reminder_1d_sent INTEGER DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS affiliates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            email TEXT,
            code TEXT UNIQUE NOT NULL,
            commission_rate REAL NOT NULL DEFAULT 15.0,
            bank_name TEXT,
            bank_account TEXT,
            bank_owner TEXT,
            created_at TEXT NOT NULL DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS affiliate_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL,
            commission_amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (affiliate_id) REFERENCES affiliates(id),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    
    # Auto-add email column to customers if missing in older schema
    c.execute("PRAGMA table_info(customers)")
    cols = [r[1] for r in c.fetchall()]
    if 'email' not in cols:
        c.execute("ALTER TABLE customers ADD COLUMN email TEXT")

    # Auto-add document_link column to products if missing in older schema
    c.execute("PRAGMA table_info(products)")
    pcols = [r[1] for r in c.fetchall()]
    if 'document_link' not in pcols:
        c.execute("ALTER TABLE products ADD COLUMN document_link TEXT")

    # Auto-add affiliate_code column to orders if missing
    c.execute("PRAGMA table_info(orders)")
    ocols = [r[1] for r in c.fetchall()]
    if 'affiliate_code' not in ocols:
        c.execute("ALTER TABLE orders ADD COLUMN affiliate_code TEXT")

    # Insert default sample affiliate if empty
    c.execute("SELECT COUNT(*) FROM affiliates")
    if c.fetchone()[0] == 0:
        sample_affiliates = [
            ('Nguyễn Văn Nam (CTV VIP)', '0901112233', 'nam.ctv@gmail.com', 'NAM88', 15.0, 'Vietcombank', '9901112233', 'NGUYEN VAN NAM'),
            ('Trần Thị Thảo (CTV TOP)', '0988776655', 'thao.ctv@gmail.com', 'THAO20', 20.0, 'MBBank', '0988776655', 'TRAN THI THAO')
        ]
        c.executemany('''
            INSERT OR IGNORE INTO affiliates (name, phone, email, code, commission_rate, bank_name, bank_account, bank_owner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', sample_affiliates)

    # Insert default settings if empty
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        default_settings = [
            ('site_name', 'Học Tiếng Anh LTN'),
            ('site_slogan', 'Thư viện bài học & Thử thách rèn luyện tiếng Anh LTN'),
            ('contact_email', 'huynhthuthao1954@gmail.com'),
            ('contact_phone', '0947789782'),
            ('free_chapters', '10'),
            ('featured_slides', '10'),
            ('bank_name', 'VietinBank'),
            ('bank_account', '103886879460'),
            ('bank_owner', 'LA THI BICH LAM'),
            ('transfer_syntax', 'G{goi} L{level} {phone}'),
            ('sepay_token', 're_' + '7uAqbaLP_' + '3utT7mUtVENSokgmHw4S68Nc'),
            ('resend_api_key', 're_' + '7uAqbaLP_' + '3utT7mUtVENSokgmHw4S68Nc'),
            ('membership_vip_price', '299000'),
            ('membership_perks', 'Truy cập trọn bộ tài liệu 4 Level + Giáo viên đồng hành'),
            ('ai_system_prompt', 'Bạn là trợ lý AI thông minh cho LTN Speaking, hỗ trợ phụ huynh & học sinh.'),
            ('ai_model', 'gpt-4o-mini')
        ]
        c.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", default_settings)
        
    conn.commit()
    ensure_customer_orders(conn)
    conn.close()

def ensure_customer_orders(conn):
    try:
        c = conn.cursor()
        unlinked = c.execute('''
            SELECT c.id FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id
            WHERE o.id IS NULL
        ''').fetchall()
        if unlinked:
            prod = c.execute("SELECT id, price FROM products ORDER BY id ASC LIMIT 1").fetchone()
            prod_id = prod['id'] if prod else 1
            price = float(prod['price']) if prod and prod['price'] else 99000.0
            for row in unlinked:
                c.execute('''
                    INSERT INTO orders (customer_id, product_id, quantity, amount, status, order_date)
                    VALUES (?, ?, 1, ?, 'pending', datetime('now', 'localtime'))
                ''', (row['id'], prod_id, price))
                oid = c.lastrowid
                c.execute("INSERT OR IGNORE INTO order_reminders (order_id, reminder_1h_sent, reminder_1d_sent) VALUES (?, 0, 0)", (oid,))
            conn.commit()
    except Exception as e:
        print(f"[ensure_customer_orders error]: {e}")

init_db()

# STATUS MAPPER
STATUS_MAP = {
    'all': 'Tất cả',
    'pending': 'Đang chờ xác nhận',
    'completed': 'Hoàn tất',
    'refunded': 'Đã hoàn tiền',
    'disputed': 'Đang khiếu nại'
}

# HELPER: GET RESEND API KEY
def get_resend_api_key():
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT value FROM settings WHERE key IN ('resend_api_key', 'sepay_token') AND value LIKE 're_%' LIMIT 1").fetchone()
    conn.close()
    return row['value'] if row and row['value'] else ('re_' + '7uAqbaLP_' + '3utT7mUtVENSokgmHw4S68Nc')

# HELPER: AFFILIATE REFERRAL RECORDING
def record_affiliate_referral(order_id, affiliate_code, order_amount, order_status):
    if not affiliate_code or not order_id:
        return

    conn = get_db()
    c = conn.cursor()
    
    # Lookup affiliate by code
    aff = c.execute("SELECT id, commission_rate FROM affiliates WHERE UPPER(code) = UPPER(?)", (affiliate_code.strip(),)).fetchone()
    if not aff:
        conn.close()
        return

    aff_id = aff['id']
    comm_rate = float(aff['commission_rate'] or 15.0)
    comm_amount = float(order_amount or 0) * (comm_rate / 100.0)
    ref_status = 'paid' if order_status == 'completed' else 'pending'

    # Check if referral record exists
    existing = c.execute("SELECT id FROM affiliate_referrals WHERE order_id = ?", (order_id,)).fetchone()
    if existing:
        c.execute('''
            UPDATE affiliate_referrals
            SET affiliate_id = ?, commission_amount = ?, status = ?
            WHERE order_id = ?
        ''', (aff_id, comm_amount, ref_status, order_id))
    else:
        c.execute('''
            INSERT INTO affiliate_referrals (affiliate_id, order_id, commission_amount, status)
            VALUES (?, ?, ?, ?)
        ''', (aff_id, order_id, comm_amount, ref_status))

    # Also update order's affiliate_code
    c.execute("UPDATE orders SET affiliate_code = ? WHERE id = ?", (affiliate_code.strip().upper(), order_id))
    conn.commit()
    conn.close()

# HELPER 1: EMAIL DISPATCHER FOR COMPLETED DOCUMENT DELIVERY VIA RESEND API
def send_document_email(customer_email, customer_name, product_name, document_link, order_id):
    if not customer_email or not document_link:
        return False, "Thiếu địa chỉ Email khách hàng hoặc Link tài liệu sản phẩm Drive."

    api_key = get_resend_api_key()

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
        .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 20px; padding: 36px; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.05); }}
        .header {{ text-align: center; border-bottom: 2px solid #f1f5f9; padding-bottom: 24px; margin-bottom: 24px; }}
        .brand {{ color: #d97706; font-size: 14px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; margin: 0 0 6px 0; }}
        .title {{ color: #0f172a; font-size: 22px; font-weight: 900; margin: 0; line-height: 1.3; }}
        .content {{ color: #334155; font-size: 15px; line-height: 1.6; }}
        .order-box {{ background: #fffbeb; border: 1px solid #fde68a; border-radius: 14px; padding: 20px; margin: 24px 0; }}
        .btn {{ display: inline-block; background-color: #d97706; color: #ffffff !important; text-decoration: none; padding: 15px 32px; border-radius: 12px; font-weight: 900; font-size: 15px; text-align: center; margin: 20px 0; box-shadow: 0 4px 14px rgba(217, 119, 6, 0.35); transition: all 0.2s; }}
        .footer {{ text-align: center; margin-top: 32px; font-size: 12px; color: #94a3b8; border-top: 1px solid #f1f5f9; padding-top: 20px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <p class="brand">🧠 Học Tiếng Anh LTN Speaking</p>
          <h1 class="title">Tài Liệu Khóa Học Đã Được Tự Động Gửi Cho Bạn!</h1>
        </div>
        <div class="content">
          <p>Xin chào <strong>{customer_name}</strong>,</p>
          <p>Hệ thống LTN Speaking xác nhận bạn đã thanh toán thành công cho <strong>Đơn hàng #{order_id}</strong>.</p>
          
          <div class="order-box">
            <p style="margin:0 0 8px 0; font-size:14px;">📦 <strong>Tên sản phẩm / Gói:</strong> <span style="color:#0f172a; font-weight:bold;">{product_name}</span></p>
            <p style="margin:0 0 8px 0; font-size:14px;">🧾 <strong>Mã đơn hàng:</strong> #{order_id}</p>
            <p style="margin:0; font-size:14px;">✅ <strong>Trạng thái thanh toán:</strong> <span style="color:#059669; font-weight:extrabold;">Hoàn tất (Completed)</span></p>
          </div>

          <p>Dưới đây là liên kết Google Drive chứa trọn bộ tài liệu, giáo trình và bài học video/audio cho gói của bạn:</p>

          <div style="text-align: center;">
            <a href="{document_link}" class="btn" target="_blank">📂 MỞ THƯ MỤC GOOGLE DRIVE TÀI LIỆU</a>
          </div>

          <p style="margin-top: 24px; font-size: 13px; color: #64748b; background-color: #f1f5f9; padding: 12px; border-radius: 8px;">
            💡 <strong>Mẹo học tập:</strong> Bạn có thể lưu link Google Drive này vào Bookmark hoặc tải về máy để luyện tập Shadowing hàng ngày. Nếu cần hỗ trợ 1:1, xin liên hệ Hotline/Zalo: <strong>0947789782</strong>.
          </p>
        </div>
        <div class="footer">
          <p>© 2026 LTN Speaking — Đánh thức sự tự tin nói tiếng Anh</p>
        </div>
      </div>
    </body>
    </html>
    """

    payload = json.dumps({
        "from": "LTN Speaking <onboarding@resend.dev>",
        "to": [customer_email],
        "subject": f"🎯 [LTN Speaking] Gửi link Google Drive tài liệu: {product_name} (Đơn #{order_id})",
        "html": html_body
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Resend-Python/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            msg_id = res_data.get('id', '')
            print(f"[RESEND DOCUMENT SUCCESS] Sent to {customer_email} (Msg ID: {msg_id})")
            return True, f"Đã gửi link Google Drive qua email {customer_email}! (Resend ID: {msg_id})"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8') if e.fp else str(e)
        print(f"[RESEND HTTP ERROR] {e.code} - {err_body}")
        return False, f"Lỗi Resend API HTTP {e.code}: {err_body}"
    except Exception as e:
        print(f"[RESEND ERROR] {str(e)}")
        return False, f"Lỗi gửi email: {str(e)}"

# HELPER 2: EMAIL DISPATCHER FOR PENDING ORDER REMINDERS (1 HOUR & 1 DAY)
def send_reminder_email(customer_email, customer_name, product_name, amount, order_id, bank_info, reminder_type='1h'):
    if not customer_email:
        return False, "Khách hàng chưa có địa chỉ Email"

    api_key = get_resend_api_key()

    amount_formatted = f"{int(amount):,}đ".replace(',', '.')
    b_name = bank_info.get('bank_name', 'VietinBank')
    b_acc = bank_info.get('bank_account', '103886879460')
    b_owner = bank_info.get('bank_owner', 'LA THI BICH LAM')

    if reminder_type == '1h':
        subject = f"⏳ [LTN Speaking] Nhắc nhở: Đơn hàng #{order_id} của bạn đang chờ hoàn tất thanh toán"
        title = "Bạn Ơi, Đơn Hàng Của Bạn Đang Chờ Hoàn Tất Thanh Toán!"
        intro = f"Hệ thống ghi nhận bạn đang đăng ký <strong>{product_name}</strong> nhưng đơn hàng <strong>#{order_id}</strong> vẫn chưa hoàn tất thanh toán."
        callout = "Nếu bạn gặp khó khăn khi chuyển khoản hoặc cần tư vấn thêm, hãy liên hệ ngay cho LTN qua Hotline/Zalo: <strong>0947789782</strong> nhé!"
        bg_accent = "#fffbeb"
        border_accent = "#fde68a"
    else:
        subject = f"🎁 [LTN Speaking] Đừng bỏ lỡ: Giữ suất ưu đãi tài liệu cho Đơn hàng #{order_id}"
        title = "Giữ Suất Ưu Đãi Khóa Học & Tài Liệu Cho Bạn!"
        intro = f"Đã 24h trôi qua kể từ khi bạn đăng ký <strong>{product_name}</strong> (Đơn hàng <strong>#{order_id}</strong>). Hãy hoàn tất thanh toán để nhận ngay link Google Drive bài học và hỗ trợ từ giáo viên!"
        callout = "🔥 <strong>Lưu ý:</strong> Sau 48h đơn hàng chưa thanh toán sẽ tự động hủy để dành suất ưu đãi cho học sinh khác."
        bg_accent = "#fef2f2"
        border_accent = "#fecaca"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
        .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 20px; padding: 36px; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.05); }}
        .header {{ text-align: center; border-bottom: 2px solid #f1f5f9; padding-bottom: 24px; margin-bottom: 24px; }}
        .brand {{ color: #d97706; font-size: 14px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; margin: 0 0 6px 0; }}
        .title {{ color: #0f172a; font-size: 20px; font-weight: 900; margin: 0; line-height: 1.3; }}
        .content {{ color: #334155; font-size: 15px; line-height: 1.6; }}
        .order-box {{ background: {bg_accent}; border: 1px solid {border_accent}; border-radius: 14px; padding: 20px; margin: 24px 0; }}
        .bank-box {{ background: #f8fafc; border: 1px border-dashed #cbd5e1; border-radius: 12px; padding: 16px; margin: 16px 0; }}
        .footer {{ text-align: center; margin-top: 32px; font-size: 12px; color: #94a3b8; border-top: 1px solid #f1f5f9; padding-top: 20px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <p class="brand">🧠 Học Tiếng Anh LTN Speaking</p>
          <h1 class="title">{title}</h1>
        </div>
        <div class="content">
          <p>Xin chào <strong>{customer_name}</strong>,</p>
          <p>{intro}</p>
          
          <div class="order-box">
            <p style="margin:0 0 8px 0; font-size:14px;">🧾 <strong>Mã đơn hàng:</strong> #{order_id}</p>
            <p style="margin:0 0 8px 0; font-size:14px;">📦 <strong>Sản phẩm:</strong> {product_name}</p>
            <p style="margin:0; font-size:14px;">💰 <strong>Số tiền cần thanh toán:</strong> <span style="color:#d97706; font-weight:bold;">{amount_formatted}</span></p>
          </div>

          <p><strong>Thông tin chuyển khoản ngân hàng:</strong></p>
          <div class="bank-box">
            <p style="margin:0 0 6px 0; font-size:13px;">🏛️ <strong>Ngân hàng:</strong> {b_name}</p>
            <p style="margin:0 0 6px 0; font-size:13px;">💳 <strong>Số tài khoản:</strong> <span style="font-family:monospace; font-weight:bold; font-size:15px; color:#0f172a;">{b_acc}</span></p>
            <p style="margin:0 0 6px 0; font-size:13px;">👤 <strong>Chủ tài khoản:</strong> {b_owner}</p>
            <p style="margin:0; font-size:13px;">✏️ <strong>Nội dung chuyển khoản:</strong> <span style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-weight:bold;">{order_id}</span></p>
          </div>

          <p style="margin-top: 20px; font-size: 13px; color: #475569;">
            {callout}
          </p>
        </div>
        <div class="footer">
          <p>© 2026 LTN Speaking — Đánh thức sự tự tin nói tiếng Anh</p>
        </div>
      </div>
    </body>
    </html>
    """

    payload = json.dumps({
        "from": "LTN Speaking <onboarding@resend.dev>",
        "to": [customer_email],
        "subject": subject,
        "html": html_body
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Resend-Python/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            msg_id = res_data.get('id', '')
            print(f"[RESEND REMINDER {reminder_type.upper()} SUCCESS] Sent to {customer_email} (Msg ID: {msg_id})")
            return True, f"Đã gửi email nhắc nhở ({reminder_type}) tới {customer_email}! (Resend ID: {msg_id})"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8') if e.fp else str(e)
        print(f"[RESEND REMINDER HTTP ERROR] {e.code} - {err_body}")
        return False, f"Lỗi Resend API HTTP {e.code}: {err_body}"
    except Exception as e:
        print(f"[RESEND REMINDER ERROR] {str(e)}")
        return False, f"Lỗi gửi email: {str(e)}"

# BACKGROUND THREAD WORKER: AUTOMATIC PERIODIC REMINDER DISPATCHER
def reminder_background_worker():
    print("[REMINDER WORKER] Background thread started! Checking pending orders for 1h and 1d reminders...")
    while True:
        try:
            conn = get_db()
            c = conn.cursor()

            # 1. Sync tracking rows for all pending orders
            c.execute('''
                INSERT OR IGNORE INTO order_reminders (order_id, reminder_1h_sent, reminder_1d_sent)
                SELECT id, 0, 0 FROM orders WHERE status = 'pending'
            ''')
            conn.commit()

            # 2. Query pending orders with customer email
            query = '''
                SELECT 
                    o.id AS order_id, o.amount, o.order_date,
                    c.name AS customer_name, c.email AS customer_email,
                    p.name AS product_name,
                    r.reminder_1h_sent, r.reminder_1d_sent
                FROM orders o
                JOIN customers c ON o.customer_id = c.id
                JOIN products p ON o.product_id = p.id
                JOIN order_reminders r ON o.id = r.order_id
                WHERE o.status = 'pending' AND c.email IS NOT NULL AND c.email != ''
            '''
            rows = c.execute(query).fetchall()

            # Fetch bank details settings
            b_name = c.execute("SELECT value FROM settings WHERE key = 'bank_name'").fetchone()
            b_acc = c.execute("SELECT value FROM settings WHERE key = 'bank_account'").fetchone()
            b_owner = c.execute("SELECT value FROM settings WHERE key = 'bank_owner'").fetchone()
            bank_info = {
                'bank_name': b_name['value'] if b_name else 'VietinBank',
                'bank_account': b_acc['value'] if b_acc else '103886879460',
                'bank_owner': b_owner['value'] if b_owner else 'LA THI BICH LAM'
            }

            now = datetime.now()

            for row in rows:
                oid = row['order_id']
                c_email = row['customer_email']
                c_name = row['customer_name']
                p_name = row['product_name']
                amount = row['amount']
                order_date_str = row['order_date']

                try:
                    odate = datetime.strptime(order_date_str, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    continue

                diff_seconds = (now - odate).total_seconds()

                if diff_seconds >= 3600 and row['reminder_1h_sent'] == 0:
                    ok, msg = send_reminder_email(c_email, c_name, p_name, amount, oid, bank_info, reminder_type='1h')
                    if ok:
                        c.execute("UPDATE order_reminders SET reminder_1h_sent = 1 WHERE order_id = ?", (oid,))
                        conn.commit()

                if diff_seconds >= 86400 and row['reminder_1d_sent'] == 0:
                    ok, msg = send_reminder_email(c_email, c_name, p_name, amount, oid, bank_info, reminder_type='1d')
                    if ok:
                        c.execute("UPDATE order_reminders SET reminder_1d_sent = 1 WHERE order_id = ?", (oid,))
                        conn.commit()

            conn.close()
        except Exception as e:
            print(f"[REMINDER WORKER EXCEPTION] {e}")

        time.sleep(180)

threading.Thread(target=reminder_background_worker, daemon=True).start()

# API ROUTES

@app.route('/admin/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    c = conn.cursor()
    
    total_products = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    total_customers = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    total_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    total_revenue = c.execute("SELECT SUM(amount) FROM orders WHERE status = 'completed'").fetchone()[0] or 0.0
    
    conn.close()
    return jsonify({
        'total_products': total_products,
        'total_customers': total_customers,
        'total_orders': total_orders,
        'total_revenue': total_revenue
    })

# PRODUCTS API
@app.route('/admin/api/products', methods=['GET'])
def list_products():
    conn = get_db()
    c = conn.cursor()
    products = [dict(row) for row in c.execute("SELECT * FROM products ORDER BY id DESC").fetchall()]
    conn.close()
    return jsonify(products)

@app.route('/admin/api/products', methods=['POST'])
def add_product():
    data = request.json
    name = data.get('name', '').strip()
    ptype = data.get('type', 'digital')
    price = float(data.get('price', 0))
    description = data.get('description', '').strip()
    stock = int(data.get('stock_quantity')) if data.get('stock_quantity') is not None and data.get('stock_quantity') != '' else None
    doc_link = data.get('document_link', '').strip()

    if not name:
        return jsonify({'success': False, 'message': 'Tên sản phẩm không được để trống!'}), 400
        
    if ptype == 'physical' and stock is None:
        stock = 0

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO products (name, type, price, description, stock_quantity, document_link)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (name, ptype, price, description, stock, doc_link))
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': pid, 'message': 'Thêm sản phẩm thành công!'})

@app.route('/admin/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    data = request.json
    name = data.get('name', '').strip()
    ptype = data.get('type', 'digital')
    price = float(data.get('price', 0))
    description = data.get('description', '').strip()
    stock = int(data.get('stock_quantity')) if data.get('stock_quantity') is not None and data.get('stock_quantity') != '' else None
    doc_link = data.get('document_link', '').strip()

    if ptype == 'physical' and stock is None:
        stock = 0

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        UPDATE products
        SET name = ?, type = ?, price = ?, description = ?, stock_quantity = ?, document_link = ?
        WHERE id = ?
    ''', (name, ptype, price, description, stock, doc_link, pid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Cập nhật sản phẩm thành công!'})

@app.route('/admin/api/products/<int:pid>', methods=['DELETE'])
def delete_product(pid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Đã xóa sản phẩm!'})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# PUBLIC LEADS REGISTRATION API (CREATES CUSTOMER + PENDING ORDER)
@app.route('/api/leads', methods=['POST'])
@app.route('/admin/api/leads', methods=['POST'])
def register_lead():
    data = request.json or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip() or None
    level = data.get('level', '1')
    goi = data.get('goi', '1')

    if not name or not phone:
        return jsonify({'success': False, 'message': 'Tên và Số điện thoại là bắt buộc!'}), 400

    conn = get_db()
    c = conn.cursor()

    # 1. Create or Update Customer
    existing_cust = c.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    if existing_cust:
        customer_id = existing_cust['id']
        c.execute('''
            UPDATE customers
            SET name = ?, email = COALESCE(?, email), zalo = COALESCE(?, zalo)
            WHERE id = ?
        ''', (name, email, phone, customer_id))
    else:
        c.execute('''
            INSERT INTO customers (name, phone, zalo, email, registration_date)
            VALUES (?, ?, ?, ?, date('now'))
        ''', (name, phone, phone, email))
        customer_id = c.lastrowid

    # 2. Match Product ID based on goi & level
    goi_num = str(goi).replace('Gói', '').replace('G', '').strip()
    level_num = str(level).replace('Level', '').replace('L', '').strip()
    
    prod_row = c.execute(
        "SELECT id, price FROM products WHERE name LIKE ? AND name LIKE ?",
        (f"%Gói {goi_num}%", f"%Level {level_num}%")
    ).fetchone()

    if not prod_row:
        prod_row = c.execute("SELECT id, price FROM products WHERE type = 'digital' ORDER BY id ASC LIMIT 1").fetchone()

    product_id = prod_row['id'] if prod_row else 1
    amount = float(prod_row['price']) if prod_row and prod_row['price'] else 99000.0

    # 3. Create Pending Order
    c.execute('''
        INSERT INTO orders (customer_id, product_id, quantity, amount, status, order_date)
        VALUES (?, ?, 1, ?, 'pending', datetime('now', 'localtime'))
    ''', (customer_id, product_id, amount))
    order_id = c.lastrowid

    # 4. Init Reminder Tracker
    c.execute("INSERT OR IGNORE INTO order_reminders (order_id, reminder_1h_sent, reminder_1d_sent) VALUES (?, 0, 0)", (order_id,))

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'customer_id': customer_id,
        'order_id': order_id,
        'message': f'Đã tự động tạo Khách hàng #{customer_id} và Đơn hàng chờ #{order_id}!'
    })

# CUSTOMERS API
@app.route('/admin/api/customers', methods=['GET'])
def list_customers():
    conn = get_db()
    c = conn.cursor()
    customers = [dict(row) for row in c.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()]
    conn.close()
    return jsonify(customers)

@app.route('/admin/api/customers', methods=['POST'])
def add_customer():
    data = request.json or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    zalo = data.get('zalo', '').strip() or None
    email = data.get('email', '').strip() or None

    if not name or not phone:
        return jsonify({'success': False, 'message': 'Tên và Số điện thoại là bắt buộc!'}), 400

    conn = get_db()
    c = conn.cursor()
    
    existing = c.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    if existing:
        cid = existing['id']
        c.execute('''
            UPDATE customers
            SET name = ?, email = COALESCE(?, email), zalo = COALESCE(?, zalo)
            WHERE id = ?
        ''', (name, email, zalo, cid))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': cid, 'updated': True, 'message': 'Đã cập nhật thông tin khách hàng!'})

    c.execute('''
        INSERT INTO customers (name, phone, zalo, email, registration_date)
        VALUES (?, ?, ?, ?, date('now'))
    ''', (name, phone, zalo, email))
    cid = c.lastrowid
    conn.commit()
    ensure_customer_orders(conn)
    conn.close()
    return jsonify({'success': True, 'id': cid, 'message': 'Thêm khách hàng thành công!'})

@app.route('/admin/api/customers/<int:cid>', methods=['PUT'])
def update_customer(cid):
    data = request.json
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    zalo = data.get('zalo', '').strip() or None
    email = data.get('email', '').strip() or None

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''
            UPDATE customers
            SET name = ?, phone = ?, zalo = ?, email = ?
            WHERE id = ?
        ''', (name, phone, zalo, email, cid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': 'Số điện thoại bị trùng với khách hàng khác!'}), 400
    conn.close()
    return jsonify({'success': True, 'message': 'Cập nhật khách hàng thành công!'})

@app.route('/admin/api/customers/<int:cid>', methods=['DELETE'])
def delete_customer(cid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM customers WHERE id = ?", (cid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Đã xóa khách hàng!'})

# AFFILIATE (CỘNG TÁC VIÊN) API ROUTES
@app.route('/admin/api/affiliates', methods=['GET'])
def list_affiliates():
    conn = get_db()
    c = conn.cursor()
    query = '''
        SELECT 
            a.id, a.name, a.phone, a.email, a.code, a.commission_rate,
            a.bank_name, a.bank_account, a.bank_owner, a.created_at,
            COUNT(r.id) AS total_referrals,
            COALESCE(SUM(r.commission_amount), 0) AS total_earnings,
            COALESCE(SUM(CASE WHEN r.status = 'pending' THEN r.commission_amount ELSE 0 END), 0) AS pending_payout,
            COALESCE(SUM(CASE WHEN r.status = 'paid' THEN r.commission_amount ELSE 0 END), 0) AS paid_payout
        FROM affiliates a
        LEFT JOIN affiliate_referrals r ON a.id = r.affiliate_id
        GROUP BY a.id
        ORDER BY a.id DESC
    '''
    affs = [dict(row) for row in c.execute(query).fetchall()]
    conn.close()
    return jsonify(affs)

@app.route('/admin/api/affiliates', methods=['POST'])
def add_affiliate():
    data = request.json
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip() or None
    code = data.get('code', '').strip().upper()
    rate = float(data.get('commission_rate', 15.0))
    b_name = data.get('bank_name', '').strip() or None
    b_acc = data.get('bank_account', '').strip() or None
    b_owner = data.get('bank_owner', '').strip() or None

    if not name or not phone:
        return jsonify({'success': False, 'message': 'Họ tên và Số điện thoại là bắt buộc!'}), 400

    if not code:
        code = 'CTV' + phone[-4:]

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO affiliates (name, phone, email, code, commission_rate, bank_name, bank_account, bank_owner, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now'))
        ''', (name, phone, email, code, rate, b_name, b_acc, b_owner))
        aid = c.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': 'Số điện thoại hoặc Mã CTV này đã tồn tại!'}), 400
    conn.close()
    return jsonify({'success': True, 'id': aid, 'code': code, 'message': f'Thêm Cộng tác viên {name} (Mã: {code}) thành công!'})

@app.route('/admin/api/affiliates/<int:aid>', methods=['PUT'])
def update_affiliate(aid):
    data = request.json
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip() or None
    code = data.get('code', '').strip().upper()
    rate = float(data.get('commission_rate', 15.0))
    b_name = data.get('bank_name', '').strip() or None
    b_acc = data.get('bank_account', '').strip() or None
    b_owner = data.get('bank_owner', '').strip() or None

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''
            UPDATE affiliates
            SET name = ?, phone = ?, email = ?, code = ?, commission_rate = ?, bank_name = ?, bank_account = ?, bank_owner = ?
            WHERE id = ?
        ''', (name, phone, email, code, rate, b_name, b_acc, b_owner, aid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': 'Số điện thoại hoặc Mã CTV bị trùng!'}), 400
    conn.close()
    return jsonify({'success': True, 'message': 'Cập nhật Cộng tác viên thành công!'})

@app.route('/admin/api/affiliates/<int:aid>', methods=['DELETE'])
def delete_affiliate(aid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM affiliates WHERE id = ?", (aid,))
    c.execute("DELETE FROM affiliate_referrals WHERE affiliate_id = ?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Đã xóa Cộng tác viên!'})

@app.route('/admin/api/affiliates/referrals', methods=['GET'])
def list_affiliate_referrals():
    conn = get_db()
    c = conn.cursor()
    query = '''
        SELECT 
            r.id, r.affiliate_id, r.order_id, r.commission_amount, r.status, r.created_at,
            a.name AS affiliate_name, a.code AS affiliate_code, a.phone AS affiliate_phone,
            o.amount AS order_amount, o.status AS order_status, o.order_date,
            c.name AS customer_name, c.phone AS customer_phone
        FROM affiliate_referrals r
        JOIN affiliates a ON r.affiliate_id = a.id
        JOIN orders o ON r.order_id = o.id
        JOIN customers c ON o.customer_id = c.id
        ORDER BY r.id DESC
    '''
    refs = [dict(row) for row in c.execute(query).fetchall()]
    conn.close()
    return jsonify(refs)

@app.route('/admin/api/affiliates/referrals/<int:rid>/payout', methods=['POST'])
def payout_affiliate_referral(rid):
    data = request.json or {}
    new_status = data.get('status', 'paid')

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE affiliate_referrals SET status = ? WHERE id = ?", (new_status, rid))
    conn.commit()
    conn.close()
    status_label = "Đã thanh toán" if new_status == 'paid' else "Chờ duyệt"
    return jsonify({'success': True, 'message': f'Cập nhật trạng thái hoa hồng đơn #{rid}: {status_label}!'})

# ORDERS API (WITH AFFILIATE CODE TRACKING)
@app.route('/admin/api/orders', methods=['GET'])
def list_orders():
    status_filter = request.args.get('status', 'all')
    conn = get_db()
    ensure_customer_orders(conn)
    c = conn.cursor()
    
    query = '''
        SELECT 
            o.id, o.customer_id, o.product_id, o.quantity, o.amount, o.status, o.affiliate_code, o.order_date,
            c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email,
            p.name AS product_name, p.type AS product_type, p.stock_quantity, p.document_link,
            COALESCE(r.reminder_1h_sent, 0) AS reminder_1h_sent,
            COALESCE(r.reminder_1d_sent, 0) AS reminder_1d_sent
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        JOIN products p ON o.product_id = p.id
        LEFT JOIN order_reminders r ON o.id = r.order_id
    '''
    params = []
    if status_filter != 'all':
        query += ' WHERE o.status = ?'
        params.append(status_filter)
        
    query += ' ORDER BY o.id DESC'
    
    orders = [dict(row) for row in c.execute(query, params).fetchall()]
    conn.close()
    return jsonify(orders)

@app.route('/admin/api/orders', methods=['POST'])
def add_order():
    data = request.json
    customer_id = data.get('customer_id')
    product_id = data.get('product_id')
    quantity = int(data.get('quantity', 1))
    status = data.get('status', 'completed')
    custom_amount = data.get('amount')
    affiliate_code = data.get('affiliate_code', '').strip().upper() or None

    if not customer_id or not product_id:
        return jsonify({'success': False, 'message': 'Vui lòng chọn khách hàng và sản phẩm!'}), 400

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT id, name, type, price, stock_quantity, document_link FROM products WHERE id = ?", (product_id,))
    prod = c.fetchone()
    if not prod:
        conn.close()
        return jsonify({'success': False, 'message': 'Không tìm thấy sản phẩm chọn!'}), 404

    c.execute("SELECT name, email FROM customers WHERE id = ?", (customer_id,))
    cust = c.fetchone()
    c_name = cust['name'] if cust else 'Khách hàng'
    c_email = cust['email'] if cust else None

    p_id, p_name, p_type, p_price, p_stock, doc_link = prod['id'], prod['name'], prod['type'], prod['price'], prod['stock_quantity'], prod['document_link']

    amount = float(custom_amount) if custom_amount is not None and custom_amount != '' else float(p_price) * quantity

    inventory_deducted = False
    if p_type == 'physical':
        current_stock = p_stock if p_stock is not None else 0
        if current_stock < quantity:
            conn.close()
            return jsonify({
                'success': False,
                'message': f'Sản phẩm vật lý "{p_name}" chỉ còn {current_stock} trong kho, không đủ số lượng {quantity}!'
            }), 400
        
        c.execute("UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?", (quantity, product_id))
        inventory_deducted = True

    c.execute('''
        INSERT INTO orders (customer_id, product_id, quantity, amount, status, affiliate_code, order_date)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ''', (customer_id, product_id, quantity, amount, status, affiliate_code))

    oid = c.lastrowid

    c.execute("INSERT OR IGNORE INTO order_reminders (order_id, reminder_1h_sent, reminder_1d_sent) VALUES (?, 0, 0)", (oid,))
    conn.commit()
    conn.close()

    # Record affiliate commission if code provided
    if affiliate_code:
        record_affiliate_referral(oid, affiliate_code, amount, status)

    email_status_msg = ""
    if status == 'completed' and c_email and doc_link:
        ok, emsg = send_document_email(c_email, c_name, p_name, doc_link, oid)
        email_status_msg = f" | {emsg}"
    elif status == 'pending':
        email_status_msg = " | (Đơn hàng ở trạng thái Chờ xác nhận - Tự động lên lịch nhắc nhở sau 1h & 1 ngày)"

    type_msg = "Sản phẩm vật lý: Đã tự động trừ kho!" if inventory_deducted else "Sản phẩm số / Dịch vụ: Không trừ kho."
    return jsonify({
        'success': True,
        'id': oid,
        'inventory_deducted': inventory_deducted,
        'message': f'Thêm đơn hàng #{oid} thành công! ({type_msg}){email_status_msg}'
    })

@app.route('/admin/api/orders/<int:oid>', methods=['PUT'])
def update_order(oid):
    data = request.json
    new_status = data.get('status')
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, oid))
    
    order_row = c.execute("SELECT amount, affiliate_code FROM orders WHERE id = ?", (oid,)).fetchone()
    conn.commit()

    if order_row and order_row['affiliate_code']:
        record_affiliate_referral(oid, order_row['affiliate_code'], order_row['amount'], new_status)

    email_msg = ""
    if new_status == 'completed':
        query = '''
            SELECT o.id, c.name AS customer_name, c.email AS customer_email, p.name AS product_name, p.document_link
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            JOIN products p ON o.product_id = p.id
            WHERE o.id = ?
        '''
        row = c.execute(query, (oid,)).fetchone()
        if row and row['customer_email'] and row['document_link']:
            ok, emsg = send_document_email(row['customer_email'], row['customer_name'], row['product_name'], row['document_link'], oid)
            email_msg = f" | {emsg}"

    conn.close()
    return jsonify({'success': True, 'message': f'Cập nhật trạng thái đơn hàng #{oid} thành công!{email_msg}'})

@app.route('/admin/api/orders/<int:oid>/send-email', methods=['POST'])
def send_order_email_route(oid):
    conn = get_db()
    c = conn.cursor()
    query = '''
        SELECT o.id, c.name AS customer_name, c.email AS customer_email, p.name AS product_name, p.document_link
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        JOIN products p ON o.product_id = p.id
        WHERE o.id = ?
    '''
    row = c.execute(query, (oid,)).fetchone()
    conn.close()

    if not row:
        return jsonify({'success': False, 'message': 'Không tìm thấy đơn hàng!'}), 404

    c_name, c_email, p_name, doc_link = row['customer_name'], row['customer_email'], row['product_name'], row['document_link']

    if not c_email:
        return jsonify({'success': False, 'message': 'Khách hàng này chưa cập nhật địa chỉ Email!'}), 400
    if not doc_link:
        return jsonify({'success': False, 'message': 'Sản phẩm này chưa được cấu hình Link Drive tài liệu!'}), 400

    ok, msg = send_document_email(c_email, c_name, p_name, doc_link, oid)
    return jsonify({'success': ok, 'message': msg})

@app.route('/admin/api/orders/<int:oid>/send-reminder', methods=['POST'])
def send_reminder_manual_route(oid):
    data = request.json or {}
    reminder_type = data.get('type', '1h')

    conn = get_db()
    c = conn.cursor()
    query = '''
        SELECT o.id, o.amount, c.name AS customer_name, c.email AS customer_email, p.name AS product_name
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        JOIN products p ON o.product_id = p.id
        WHERE o.id = ?
    '''
    row = c.execute(query, (oid,)).fetchone()

    if not row:
        conn.close()
        return jsonify({'success': False, 'message': 'Không tìm thấy đơn hàng!'}), 404

    b_name = c.execute("SELECT value FROM settings WHERE key = 'bank_name'").fetchone()
    b_acc = c.execute("SELECT value FROM settings WHERE key = 'bank_account'").fetchone()
    b_owner = c.execute("SELECT value FROM settings WHERE key = 'bank_owner'").fetchone()
    bank_info = {
        'bank_name': b_name['value'] if b_name else 'VietinBank',
        'bank_account': b_acc['value'] if b_acc else '103886879460',
        'bank_owner': b_owner['value'] if b_owner else 'LA THI BICH LAM'
    }

    ok, msg = send_reminder_email(
        row['customer_email'], row['customer_name'], row['product_name'], row['amount'], oid, bank_info, reminder_type=reminder_type
    )

    if ok:
        col = 'reminder_1h_sent' if reminder_type == '1h' else 'reminder_1d_sent'
        c.execute(f"INSERT OR REPLACE INTO order_reminders (order_id, {col}) VALUES (?, 1)", (oid,))
        conn.commit()

    conn.close()
    return jsonify({'success': ok, 'message': msg})

@app.route('/admin/api/orders/<int:oid>', methods=['DELETE'])
def delete_order(oid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE id = ?", (oid,))
    c.execute("DELETE FROM order_reminders WHERE order_id = ?", (oid,))
    c.execute("DELETE FROM affiliate_referrals WHERE order_id = ?", (oid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Đã xóa đơn hàng!'})

# SEPAY AUTOMATED BANK PAYMENT WEBHOOK (WITH AUTOMATIC PENDING ORDER MATCHING & AFFILIATE TRACKING)
@app.route('/api/webhooks/sepay', methods=['POST'])
def sepay_webhook():
    data = request.json or {}
    print(f"[SEPAY WEBHOOK RECEIVED]: {json.dumps(data, ensure_ascii=False)}")

    # 1. Parse transfer parameters
    content = str(data.get('content', '') or data.get('description', '') or data.get('transferContent', ''))
    amount = float(data.get('transferAmount', 0) or data.get('amountIn', 0) or data.get('amount', 0))

    phone_match = re.search(r'0[3|5|7|8|9][0-9]{8}', content)
    phone = phone_match.group(0) if phone_match else None

    goi_match = re.search(r'G(?:oi)?\s*([1-3])', content, re.IGNORECASE)
    level_match = re.search(r'L(?:evel)?\s*([1-4])', content, re.IGNORECASE)

    conn = get_db()
    c = conn.cursor()

    # Match affiliate code in transfer content
    aff_code = None
    all_codes = [r['code'] for r in c.execute("SELECT code FROM affiliates").fetchall()]
    for code_item in all_codes:
        if code_item.lower() in content.lower():
            aff_code = code_item
            break

    # Resolve target product_id
    product_id = None
    if goi_match and level_match:
        goi_num = goi_match.group(1)
        level_num = level_match.group(1)
        prod_row = c.execute(
            "SELECT id FROM products WHERE name LIKE ? AND name LIKE ?",
            (f"%Gói {goi_num}%", f"%Level {level_num}%")
        ).fetchone()
        if prod_row:
            product_id = prod_row['id']

    if not product_id:
        prod_row = c.execute("SELECT id FROM products WHERE type = 'digital' ORDER BY id ASC LIMIT 1").fetchone()
        if prod_row:
            product_id = prod_row['id']

    # Resolve customer_id
    customer_id = None
    if phone:
        cust_row = c.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
        if cust_row:
            customer_id = cust_row['id']

    # --- AUTOMATIC ORDER MATCHING & STATUS UPDATE ---
    pending_order = None
    if customer_id:
        pending_order = c.execute('''
            SELECT id, product_id FROM orders 
            WHERE customer_id = ? AND status IN ('pending', 'Chờ xác nhận', 'Chờ chuyển khoản')
            ORDER BY id DESC LIMIT 1
        ''', (customer_id,)).fetchone()

    if not pending_order and phone:
        pending_order = c.execute('''
            SELECT o.id, o.product_id FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE c.phone = ? AND o.status IN ('pending', 'Chờ xác nhận', 'Chờ chuyển khoản')
            ORDER BY o.id DESC LIMIT 1
        ''', (phone,)).fetchone()

    if pending_order:
        oid = pending_order['id']
        target_product_id = product_id or pending_order['product_id']
        c.execute('''
            UPDATE orders 
            SET status = 'completed', amount = ?, product_id = ?, affiliate_code = COALESCE(?, affiliate_code)
            WHERE id = ?
        ''', (amount if amount > 0 else 99000, target_product_id, aff_code, oid))
        conn.commit()
        print(f"[SEPAY MATCH SUCCESS]: Updated existing Pending Order #{oid} to COMPLETED!")
    else:
        if not customer_id:
            cust_phone = phone or f"09{int(data.get('id', 123456))%100000000:08d}"
            c.execute("INSERT OR IGNORE INTO customers (name, phone, registration_date) VALUES (?, ?, date('now'))",
                      (f"Khách SePay {cust_phone[-4:]}", cust_phone))
            conn.commit()
            cust_row = c.execute("SELECT id FROM customers WHERE phone = ?", (cust_phone,)).fetchone()
            if cust_row:
                customer_id = cust_row['id']

        c.execute('''
            INSERT INTO orders (customer_id, product_id, quantity, amount, status, affiliate_code, order_date)
            VALUES (?, ?, 1, ?, 'completed', ?, datetime('now', 'localtime'))
        ''', (customer_id, product_id, amount if amount > 0 else 99000, aff_code))
        oid = c.lastrowid
        conn.commit()
        print(f"[SEPAY NEW ORDER CREATED]: Inserted new Order #{oid} as COMPLETED!")

    if aff_code:
        record_affiliate_referral(oid, aff_code, amount if amount > 0 else 99000, 'completed')

    # Automatically send Google Drive document link to customer email
    query = '''
        SELECT c.name AS customer_name, c.email AS customer_email, p.name AS product_name, p.document_link
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        JOIN products p ON o.product_id = p.id
        WHERE o.id = ?
    '''
    info = c.execute(query, (oid,)).fetchone()
    conn.close()

    email_sent = False
    if info and info['customer_email'] and info['document_link']:
        email_sent, mail_msg = send_document_email(info['customer_email'], info['customer_name'], info['product_name'], info['document_link'], oid)
        print(f"[SEPAY AUTO EMAIL SENT]: Order #{oid} -> Email: {info['customer_email']} | Sent: {email_sent} | Info: {mail_msg}")
    else:
        print(f"[SEPAY EMAIL SKIPPED]: Order #{oid} missing email or document link. Info: {dict(info) if info else None}")

    return jsonify({
        'success': True,
        'order_id': oid,
        'email_sent': email_sent,
        'affiliate_code': aff_code,
        'message': f'Đã tự động xác nhận thanh toán SePay và hoàn tất đơn #{oid}'
    })

# SETTINGS API
@app.route('/admin/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    c = conn.cursor()
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    settings = {row['key']: row['value'] for row in rows}
    conn.close()
    return jsonify(settings)

@app.route('/admin/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    for k, v in data.items():
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Lưu cài đặt thành công!'})

# MAIN ADMIN PAGE ROUTE
ADMIN_HTML = '''
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LTN Admin Panel — Quản Trị Hệ Thống</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        creamBg: '#FBF9F4',
                        creamSidebar: '#F3EFE6',
                        creamCard: '#FFFFFF',
                        creamBorder: '#E5DFD3',
                        creamHover: '#F5F0E4',
                        accentGold: '#D97706',
                        accentGoldHover: '#B45309'
                    }
                }
            }
        }
    </script>
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #FBF9F4; color: #1E293B; }
        .tab-active { background-color: #D97706 !important; color: #FFFFFF !important; font-weight: 800; shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .filter-pill-active { background-color: #FEF3C7 !important; border-color: #D97706 !important; color: #92400E !important; font-weight: 800; }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #FBF9F4; }
        ::-webkit-scrollbar-thumb { background: #E5DFD3; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #D97706; }
    </style>
</head>
<body class="min-h-screen flex flex-col sm:flex-row antialiased bg-[#FBF9F4]">

    <!-- LEFT SIDEBAR -->
    <aside class="w-full sm:w-64 bg-[#F3EFE6] border-r border-[#E5DFD3] flex flex-col justify-between p-5 flex-shrink-0">
        <div>
            <!-- LOGO BRAND -->
            <div class="flex items-center gap-3 mb-8 px-2">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-amber-600 to-yellow-500 flex items-center justify-center text-white font-black text-xl shadow-md">
                    🧠
                </div>
                <div>
                    <h1 class="font-black text-base text-slate-900 tracking-tight leading-none">LTN Admin</h1>
                    <span class="text-[11px] text-amber-700 font-bold uppercase tracking-wider">brain.db connected</span>
                </div>
            </div>

            <!-- SIDEBAR NAV TABS -->
            <nav class="space-y-1.5">
                <button onclick="switchTab('products')" id="nav-products" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-bold text-slate-700 hover:bg-[#EAE4D7] hover:text-slate-900 transition-all text-left">
                    <span class="text-base">📦</span>
                    <span>Sản phẩm</span>
                </button>
                <button onclick="switchTab('customers')" id="nav-customers" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-bold text-slate-700 hover:bg-[#EAE4D7] hover:text-slate-900 transition-all text-left">
                    <span class="text-base">👥</span>
                    <span>Khách hàng</span>
                </button>
                <button onclick="switchTab('orders')" id="nav-orders" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-bold text-slate-700 hover:bg-[#EAE4D7] hover:text-slate-900 transition-all text-left">
                    <span class="text-base">🛒</span>
                    <span>Đơn hàng</span>
                </button>
                <button onclick="switchTab('affiliates')" id="nav-affiliates" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-bold text-slate-700 hover:bg-[#EAE4D7] hover:text-slate-900 transition-all text-left">
                    <span class="text-base">🤝</span>
                    <span>Cộng tác viên</span>
                </button>
                <button onclick="switchTab('settings')" id="nav-settings" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-bold text-slate-700 hover:bg-[#EAE4D7] hover:text-slate-900 transition-all text-left">
                    <span class="text-base">⚙️</span>
                    <span>Cài đặt</span>
                </button>
            </nav>
        </div>

        <!-- FOOTER STATUS -->
        <div class="pt-6 border-t border-[#E5DFD3] px-2 text-xs">
            <div class="flex items-center gap-2 text-emerald-700 font-bold">
                <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                <span>Affiliate System Active</span>
            </div>
            <p class="text-[11px] text-slate-500 mt-1 font-medium">Auto-track & Payout Engine</p>
        </div>
    </aside>

    <!-- MAIN CONTENT AREA -->
    <main class="flex-1 p-4 sm:p-8 overflow-y-auto max-w-7xl mx-auto w-full">

        <!-- ALERT TOAST CONTAINER -->
        <div id="toast-box" class="fixed top-5 right-5 z-50 flex flex-col gap-2 pointer-events-none"></div>

        <!-- STATS DASHBOARD SUMMARY -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                <span class="text-xs font-bold text-slate-500 block mb-1">Tổng sản phẩm</span>
                <span id="stat-products" class="text-2xl font-black text-slate-900">0</span>
            </div>
            <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                <span class="text-xs font-bold text-slate-500 block mb-1">Tổng khách hàng</span>
                <span id="stat-customers" class="text-2xl font-black text-slate-900">0</span>
            </div>
            <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                <span class="text-xs font-bold text-slate-500 block mb-1">Tổng đơn hàng</span>
                <span id="stat-orders" class="text-2xl font-black text-slate-900">0</span>
            </div>
            <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                <span class="text-xs font-bold text-slate-500 block mb-1">Doanh thu hoàn tất</span>
                <span id="stat-revenue" class="text-xl font-black text-amber-700">0đ</span>
            </div>
        </div>

        <!-- TAB 1: SẢN PHẨM -->
        <section id="tab-products-content" class="tab-panel hidden">
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                <div>
                    <h2 class="text-2xl font-black text-slate-900 tracking-tight">Sản phẩm</h2>
                    <p class="text-xs text-slate-500 font-medium">Quản lý kho hàng, link Google Drive tài liệu cho từng gói & phân loại tồn kho.</p>
                </div>
                <button onclick="openProductModal()" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-4 py-2.5 rounded-xl text-xs flex items-center gap-2 shadow-md transition-all">
                    <span>+ Thêm sản phẩm mới</span>
                </button>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-3 mb-4 bg-white p-3 rounded-2xl border border-[#E5DFD3] shadow-sm">
                <input type="text" id="search-products" oninput="renderProducts()" placeholder="🔍 Tìm theo tên hoặc mô tả sản phẩm..." class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2 rounded-xl w-full sm:w-72 outline-none focus:border-amber-600 focus:bg-white">
                <div class="flex items-center gap-2 text-xs">
                    <span class="text-slate-600 font-bold">Phân loại:</span>
                    <select id="filter-product-type" onchange="renderProducts()" class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3 py-2 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        <option value="all">Tất cả sản phẩm</option>
                        <option value="physical">📦 Vật lý (Trừ tồn kho)</option>
                        <option value="digital">⚡ Sản phẩm số (Link Drive)</option>
                        <option value="service">🛠️ Dịch vụ (Không trừ kho)</option>
                    </select>
                </div>
            </div>

            <div class="bg-white border border-[#E5DFD3] rounded-2xl overflow-hidden shadow-sm">
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-xs">
                        <thead class="bg-[#F5F0E6] text-slate-700 font-bold border-b border-[#E5DFD3]">
                            <tr>
                                <th class="p-3.5">ID</th>
                                <th class="p-3.5">Tên sản phẩm</th>
                                <th class="p-3.5">Loại sản phẩm</th>
                                <th class="p-3.5">Giá bán</th>
                                <th class="p-3.5">Tồn kho</th>
                                <th class="p-3.5">Drive Link Tài Liệu</th>
                                <th class="p-3.5 text-right">Thao tác</th>
                            </tr>
                        </thead>
                        <tbody id="products-tbody" class="divide-y divide-[#EFECE6] text-slate-800"></tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 2: KHÁCH HÀNG -->
        <section id="tab-customers-content" class="tab-panel hidden">
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                <div>
                    <h2 class="text-2xl font-black text-slate-900 tracking-tight">Khách hàng</h2>
                    <p class="text-xs text-slate-500 font-medium">Danh sách khách hàng, Email nhận tài liệu & nhắc nhở đơn hàng.</p>
                </div>
                <button onclick="openCustomerModal()" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-4 py-2.5 rounded-xl text-xs flex items-center gap-2 shadow-md transition-all">
                    <span>+ Thêm khách hàng mới</span>
                </button>
            </div>

            <div class="mb-4 bg-white p-3 rounded-2xl border border-[#E5DFD3] shadow-sm">
                <input type="text" id="search-customers" oninput="renderCustomers()" placeholder="🔍 Tìm theo họ tên, email, số điện thoại hoặc Zalo..." class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2 rounded-xl w-full sm:w-80 outline-none focus:border-amber-600 focus:bg-white">
            </div>

            <div class="bg-white border border-[#E5DFD3] rounded-2xl overflow-hidden shadow-sm">
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-xs">
                        <thead class="bg-[#F5F0E6] text-slate-700 font-bold border-b border-[#E5DFD3]">
                            <tr>
                                <th class="p-3.5">ID</th>
                                <th class="p-3.5">Họ và tên</th>
                                <th class="p-3.5">Email nhận tài liệu</th>
                                <th class="p-3.5">Số điện thoại</th>
                                <th class="p-3.5">Zalo</th>
                                <th class="p-3.5">Ngày đăng ký</th>
                                <th class="p-3.5 text-right">Thao tác</th>
                            </tr>
                        </thead>
                        <tbody id="customers-tbody" class="divide-y divide-[#EFECE6] text-slate-800"></tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 3: ĐƠN HÀNG -->
        <section id="tab-orders-content" class="tab-panel hidden">
            <div class="mb-6">
                <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-2">
                    <h2 class="text-3xl font-serif text-slate-900 tracking-wide">Đơn hàng</h2>
                    <button onclick="openOrderModal()" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-5 py-2.5 rounded-xl text-xs shadow-md transition-all">
                        + Tạo đơn hàng mới
                    </button>
                </div>
                <p class="text-xs text-slate-500 font-medium">Tự động gửi link Google Drive khi hoàn tất & tự động ghi nhận mã CTV giới thiệu.</p>
            </div>

            <div class="flex flex-wrap gap-2 mb-6">
                <button onclick="filterOrders('all')" id="filter-order-all" class="px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-700 hover:border-amber-600 transition-all">Tất cả</button>
                <button onclick="filterOrders('pending')" id="filter-order-pending" class="px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-700 hover:border-amber-600 transition-all">Đang chờ xác nhận</button>
                <button onclick="filterOrders('completed')" id="filter-order-completed" class="px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-700 hover:border-amber-600 transition-all">Hoàn tất</button>
                <button onclick="filterOrders('refunded')" id="filter-order-refunded" class="px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-700 hover:border-amber-600 transition-all">Đã hoàn tiền</button>
                <button onclick="filterOrders('disputed')" id="filter-order-disputed" class="px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-700 hover:border-amber-600 transition-all">Đang khiếu nại</button>
            </div>

            <div class="bg-white border border-[#E5DFD3] rounded-2xl overflow-hidden shadow-sm">
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-xs">
                        <thead class="bg-[#F5F0E6] text-slate-700 font-bold border-b border-[#E5DFD3]">
                            <tr>
                                <th class="p-3.5">Mã ĐH</th>
                                <th class="p-3.5">Khách hàng & Email</th>
                                <th class="p-3.5">Sản phẩm / Link Drive</th>
                                <th class="p-3.5">Mã CTV</th>
                                <th class="p-3.5">Tổng tiền</th>
                                <th class="p-3.5">Trạng thái & Nhắc nhở</th>
                                <th class="p-3.5">Ngày đặt</th>
                                <th class="p-3.5 text-right">Thao tác</th>
                            </tr>
                        </thead>
                        <tbody id="orders-tbody" class="divide-y divide-[#EFECE6] text-slate-800"></tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 4: CỘNG TÁC VIÊN (AFFILIATE MANAGEMENT) -->
        <section id="tab-affiliates-content" class="tab-panel hidden">
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                <div>
                    <h2 class="text-3xl font-serif text-slate-900 tracking-wide">Cộng tác viên (Affiliate)</h2>
                    <p class="text-xs text-slate-500 font-medium">Quản lý mạng lưới CTV chia sẻ Salepage, theo dõi mã giới thiệu, tính % hoa hồng và chi trả.</p>
                </div>
                <button onclick="openAffiliateModal()" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-4 py-2.5 rounded-xl text-xs flex items-center gap-2 shadow-md transition-all">
                    <span>+ Thêm Cộng Tác Viên Mới</span>
                </button>
            </div>

            <!-- AFFILIATE STATS KPI CARDS -->
            <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                    <span class="text-xs font-bold text-slate-500 block mb-1">Tổng CTV hoạt động</span>
                    <span id="affstat-total-ctv" class="text-2xl font-black text-slate-900">0</span>
                </div>
                <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                    <span class="text-xs font-bold text-slate-500 block mb-1">Tổng đơn giới thiệu</span>
                    <span id="affstat-total-referrals" class="text-2xl font-black text-slate-900">0</span>
                </div>
                <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                    <span class="text-xs font-bold text-slate-500 block mb-1">Hoa hồng chờ duyệt chi</span>
                    <span id="affstat-pending-payout" class="text-xl font-black text-amber-700">0đ</span>
                </div>
                <div class="bg-white border border-[#E5DFD3] p-4 rounded-2xl shadow-sm">
                    <span class="text-xs font-bold text-slate-500 block mb-1">Hoa hồng đã chi trả</span>
                    <span id="affstat-paid-payout" class="text-xl font-black text-emerald-700">0đ</span>
                </div>
            </div>

            <!-- AFFILIATE SUB-TABS NAVIGATION -->
            <div class="flex border-b border-[#E5DFD3] gap-6 text-xs font-bold mb-6">
                <button onclick="switchAffSubTab('list')" id="afftab-list" class="pb-3 text-amber-700 border-b-2 border-amber-700">Danh sách CTV & Link Salepage</button>
                <button onclick="switchAffSubTab('referrals')" id="afftab-referrals" class="pb-3 text-slate-500 hover:text-slate-900">Lịch sử Đơn Giới Thiệu & Duyệt Chi</button>
            </div>

            <!-- SUB-PANEL 1: AFFILIATES LIST -->
            <div id="affpanel-list" class="space-y-4">
                <div class="bg-white p-3 rounded-2xl border border-[#E5DFD3] shadow-sm">
                    <input type="text" id="search-affiliates" oninput="renderAffiliates()" placeholder="🔍 Tìm CTV theo tên, mã giới thiệu, SĐT hoặc Email..." class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2 rounded-xl w-full sm:w-80 outline-none focus:border-amber-600">
                </div>

                <div class="bg-white border border-[#E5DFD3] rounded-2xl overflow-hidden shadow-sm">
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs">
                            <thead class="bg-[#F5F0E6] text-slate-700 font-bold border-b border-[#E5DFD3]">
                                <tr>
                                    <th class="p-3.5">ID</th>
                                    <th class="p-3.5">Họ tên CTV</th>
                                    <th class="p-3.5">Mã CTV</th>
                                    <th class="p-3.5">% Hoa Hồng</th>
                                    <th class="p-3.5">Link Chia Sẻ Salepage</th>
                                    <th class="p-3.5">SĐT & Ngân Hàng</th>
                                    <th class="p-3.5">Tổng Thu Nhập</th>
                                    <th class="p-3.5 text-right">Thao tác</th>
                                </tr>
                            </thead>
                            <tbody id="affiliates-tbody" class="divide-y divide-[#EFECE6] text-slate-800"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- SUB-PANEL 2: REFERRALS HISTORY -->
            <div id="affpanel-referrals" class="hidden space-y-4">
                <div class="bg-white border border-[#E5DFD3] rounded-2xl overflow-hidden shadow-sm">
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs">
                            <thead class="bg-[#F5F0E6] text-slate-700 font-bold border-b border-[#E5DFD3]">
                                <tr>
                                    <th class="p-3.5">Mã Đơn</th>
                                    <th class="p-3.5">CTV Giới Thiệu</th>
                                    <th class="p-3.5">Khách Mua</th>
                                    <th class="p-3.5">Giá Trị Đơn</th>
                                    <th class="p-3.5">Hoa Hồng (VNĐ)</th>
                                    <th class="p-3.5">Trạng Thái HH</th>
                                    <th class="p-3.5">Ngày Giới Thiệu</th>
                                    <th class="p-3.5 text-right">Thao Tác Duyệt Chi</th>
                                </tr>
                            </thead>
                            <tbody id="referrals-tbody" class="divide-y divide-[#EFECE6] text-slate-800"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </section>

        <!-- TAB 5: CÀI ĐẶT -->
        <section id="tab-settings-content" class="tab-panel hidden">
            <div class="mb-6">
                <h2 class="text-3xl font-serif text-slate-900 tracking-wide mb-1">Cài đặt</h2>
                <p class="text-xs text-slate-500 font-medium">Cấu hình thông tin chung, Resend API gửi email & phương thức thanh toán.</p>
            </div>

            <div class="flex border-b border-[#E5DFD3] gap-6 text-xs font-bold mb-6">
                <button onclick="switchSettingSubTab('general')" id="settab-general" class="pb-3 text-amber-700 border-b-2 border-amber-700">Cài đặt tổng quan</button>
                <button onclick="switchSettingSubTab('payment')" id="settab-payment" class="pb-3 text-slate-500 hover:text-slate-900">Thanh toán & Resend API</button>
                <button onclick="switchSettingSubTab('membership')" id="settab-membership" class="pb-3 text-slate-500 hover:text-slate-900">Gói hội viên</button>
                <button onclick="switchSettingSubTab('ai')" id="settab-ai" class="pb-3 text-slate-500 hover:text-slate-900">Chatbot AI</button>
            </div>

            <form id="settings-form" onsubmit="saveSettings(event)" class="space-y-6">
                <div id="setting-panel-general" class="setting-panel bg-white border border-[#E5DFD3] p-6 rounded-2xl shadow-sm">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Tên website</label>
                            <input type="text" id="setting_site_name" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Khẩu hiệu / mô tả ngắn</label>
                            <input type="text" id="setting_site_slogan" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Email liên hệ</label>
                            <input type="email" id="setting_contact_email" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Số điện thoại hỗ trợ</label>
                            <input type="text" id="setting_contact_phone" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                    </div>
                </div>

                <div id="setting-panel-payment" class="setting-panel hidden bg-white border border-[#E5DFD3] p-6 rounded-2xl shadow-sm">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Tên ngân hàng nhận chuyển khoản</label>
                            <input type="text" id="setting_bank_name" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Số tài khoản</label>
                            <input type="text" id="setting_bank_account" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Chủ tài khoản</label>
                            <input type="text" id="setting_bank_owner" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Resend API Key (Gửi Email tự động & Nhắc nhở)</label>
                            <input type="text" id="setting_resend_api_key" placeholder="re_..." class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">SePay / VietQR Webhook Token</label>
                            <input type="text" id="setting_sepay_token" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                    </div>
                </div>

                <div id="setting-panel-membership" class="setting-panel hidden bg-white border border-[#E5DFD3] p-6 rounded-2xl shadow-sm">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">Giá gói hội viên VIP (VNĐ)</label>
                            <input type="number" id="setting_membership_vip_price" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white">
                        </div>
                    </div>
                </div>

                <div id="setting-panel-ai" class="setting-panel hidden bg-white border border-[#E5DFD3] p-6 rounded-2xl shadow-sm">
                    <div class="space-y-4">
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 mb-1">System Prompt trợ lý AI</label>
                            <textarea id="setting_ai_system_prompt" rows="3" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 p-3.5 rounded-xl outline-none focus:border-amber-600 focus:bg-white"></textarea>
                        </div>
                    </div>
                </div>

                <button type="submit" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-6 py-3 rounded-full text-xs shadow-md transition-all">
                    Lưu thay đổi
                </button>
            </form>
        </section>

    </main>

    <!-- MODAL: ADD / EDIT PRODUCT -->
    <div id="modal-product" class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white border border-[#E5DFD3] rounded-3xl max-w-lg w-full p-6 relative shadow-2xl">
            <button onclick="closeProductModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-800 text-xl font-bold">&times;</button>
            <h3 id="modal-product-title" class="text-lg font-black text-slate-900 mb-4">Thêm sản phẩm mới</h3>
            <form onsubmit="saveProduct(event)" class="space-y-3">
                <input type="hidden" id="prod-id">
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Tên sản phẩm *</label>
                    <input type="text" id="prod-name" required placeholder="Ví dụ: Gói 1 - Level 1: Bộ Mẫu Câu Shadowing" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Loại sản phẩm *</label>
                        <select id="prod-type" onchange="toggleStockField()" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                            <option value="digital">⚡ Sản phẩm số (Link Drive)</option>
                            <option value="service">🛠️ Dịch vụ (Không trừ kho)</option>
                            <option value="physical">📦 Vật lý (Trừ tồn kho)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Giá bán (VNĐ) *</label>
                        <input type="number" id="prod-price" required step="1000" placeholder="Ví dụ: 150000" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                </div>

                <div id="stock-field-box" class="bg-amber-50 border border-amber-200 p-3 rounded-xl hidden">
                    <label class="block text-[11px] font-bold text-amber-900 mb-1">Tồn kho ban đầu (Số lượng vật lý) *</label>
                    <input type="number" id="prod-stock" min="0" placeholder="Ví dụ: 50" class="w-full bg-white border border-amber-300 text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>

                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Mô tả sản phẩm</label>
                    <textarea id="prod-desc" rows="2" placeholder="Mô tả chi tiết nội dung sản phẩm..." class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 p-3 rounded-xl outline-none focus:border-amber-600"></textarea>
                </div>
                <div>
                    <label class="block text-[11px] font-bold text-amber-900 mb-1">📂 Link Google Drive Tài Liệu (Dành cho Email tự động)</label>
                    <input type="url" id="prod-doc" placeholder="https://drive.google.com/drive/folders/..." class="w-full bg-[#FBF9F4] border border-amber-400 text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>

                <div class="pt-2 flex justify-end gap-2">
                    <button type="button" onclick="closeProductModal()" class="px-4 py-2 rounded-xl text-xs font-bold text-slate-600 hover:bg-[#F3EFE6]">Hủy</button>
                    <button type="submit" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-5 py-2 rounded-xl text-xs shadow-md">Lưu sản phẩm</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT CUSTOMER -->
    <div id="modal-customer" class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white border border-[#E5DFD3] rounded-3xl max-w-md w-full p-6 relative shadow-2xl">
            <button onclick="closeCustomerModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-800 text-xl font-bold">&times;</button>
            <h3 id="modal-customer-title" class="text-lg font-black text-slate-900 mb-4">Thêm khách hàng mới</h3>
            <form onsubmit="saveCustomer(event)" class="space-y-3">
                <input type="hidden" id="cust-id">
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Họ tên khách hàng *</label>
                    <input type="text" id="cust-name" required placeholder="Ví dụ: Nguyễn Văn A" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div>
                    <label class="block text-[11px] font-bold text-amber-900 mb-1">📧 Email nhận tài liệu & nhắc nhở *</label>
                    <input type="email" id="cust-email" required placeholder="Ví dụ: nguyenvana@gmail.com" class="w-full bg-[#FBF9F4] border border-amber-400 text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Số điện thoại *</label>
                    <input type="tel" id="cust-phone" required placeholder="Ví dụ: 0901234567" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Số Zalo (Nếu khác SĐT)</label>
                    <input type="tel" id="cust-zalo" placeholder="Ví dụ: 0901234567" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div class="pt-2 flex justify-end gap-2">
                    <button type="button" onclick="closeCustomerModal()" class="px-4 py-2 rounded-xl text-xs font-bold text-slate-600 hover:bg-[#F3EFE6]">Hủy</button>
                    <button type="submit" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-5 py-2 rounded-xl text-xs shadow-md">Lưu khách hàng</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD ORDER -->
    <div id="modal-order" class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white border border-[#E5DFD3] rounded-3xl max-w-md w-full p-6 relative shadow-2xl">
            <button onclick="closeOrderModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-800 text-xl font-bold">&times;</button>
            <h3 class="text-lg font-black text-slate-900 mb-4">Tạo đơn hàng mới</h3>
            <form onsubmit="saveOrder(event)" class="space-y-4">
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Chọn khách hàng *</label>
                    <select id="ord-customer" required class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600"></select>
                </div>
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Chọn sản phẩm *</label>
                    <select id="ord-product" required onchange="onOrderProductChange()" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600"></select>
                </div>

                <div id="ord-inventory-notice" class="p-3 rounded-xl text-xs font-medium border transition-all"></div>

                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Số lượng mua *</label>
                        <input type="number" id="ord-quantity" min="1" value="1" oninput="recalcOrderAmount()" required class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Tổng tiền (VNĐ)</label>
                        <input type="number" id="ord-amount" step="1000" placeholder="Tự động tính" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-amber-800 font-bold px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                </div>

                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Mã CTV Giới Thiệu (Nếu có)</label>
                    <input type="text" id="ord-affiliate" placeholder="Ví dụ: NAM88 hoặc THAO20" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 uppercase font-mono px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>

                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Trạng thái đơn hàng</label>
                    <select id="ord-status" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                        <option value="pending">Đang chờ xác nhận (Pending - Tự động nhắc sau 1h & 1 ngày)</option>
                        <option value="completed">Hoàn tất (Completed - Tự động gửi Email Drive Link)</option>
                        <option value="refunded">Đã hoàn tiền (Refunded)</option>
                        <option value="disputed">Đang khiếu nại (Disputed)</option>
                    </select>
                </div>

                <div class="pt-2 flex justify-end gap-2">
                    <button type="button" onclick="closeOrderModal()" class="px-4 py-2 rounded-xl text-xs font-bold text-slate-600 hover:bg-[#F3EFE6]">Hủy</button>
                    <button type="submit" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-5 py-2 rounded-xl text-xs shadow-md">Xác nhận tạo đơn</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT AFFILIATE (CTV) -->
    <div id="modal-affiliate" class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white border border-[#E5DFD3] rounded-3xl max-w-md w-full p-6 relative shadow-2xl">
            <button onclick="closeAffiliateModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-800 text-xl font-bold">&times;</button>
            <h3 id="modal-affiliate-title" class="text-lg font-black text-slate-900 mb-4">Thêm Cộng Tác Viên mới</h3>
            <form onsubmit="saveAffiliate(event)" class="space-y-3">
                <input type="hidden" id="aff-id">
                <div>
                    <label class="block text-[11px] font-bold text-slate-600 mb-1">Họ tên CTV *</label>
                    <input type="text" id="aff-name" required placeholder="Ví dụ: Nguyễn Văn A" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Mã giới thiệu (Code) *</label>
                        <input type="text" id="aff-code" placeholder="Ví dụ: NAM88" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 uppercase font-mono px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                    <div>
                        <label class="block text-[11px] font-bold text-amber-900 mb-1">% Hoa hồng *</label>
                        <input type="number" id="aff-rate" min="1" max="100" value="15" required class="w-full bg-[#FBF9F4] border border-amber-400 text-xs text-slate-900 font-bold px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Số điện thoại *</label>
                        <input type="tel" id="aff-phone" required placeholder="0901234567" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                    <div>
                        <label class="block text-[11px] font-bold text-slate-600 mb-1">Email CTV</label>
                        <input type="email" id="aff-email" placeholder="ctv@gmail.com" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs text-slate-900 px-3.5 py-2.5 rounded-xl outline-none focus:border-amber-600">
                    </div>
                </div>
                <div class="border-t border-[#E5DFD3] pt-2 mt-2">
                    <span class="text-[11px] font-bold text-amber-800 block mb-2">💳 Tài Khoản Ngân Hàng Nhận Hoa Hồng</span>
                    <div class="grid grid-cols-2 gap-2 mb-2">
                        <input type="text" id="aff-bank-name" placeholder="Tên ngân hàng (e.g. MBBank)" class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs p-2 rounded-lg outline-none">
                        <input type="text" id="aff-bank-account" placeholder="Số tài khoản" class="bg-[#FBF9F4] border border-[#E5DFD3] text-xs p-2 rounded-lg outline-none">
                    </div>
                    <input type="text" id="aff-bank-owner" placeholder="Tên chủ tài khoản (viết hoa)" class="w-full bg-[#FBF9F4] border border-[#E5DFD3] text-xs p-2 rounded-lg outline-none uppercase">
                </div>

                <div class="pt-2 flex justify-end gap-2">
                    <button type="button" onclick="closeAffiliateModal()" class="px-4 py-2 rounded-xl text-xs font-bold text-slate-600 hover:bg-[#F3EFE6]">Hủy</button>
                    <button type="submit" class="bg-[#D97706] hover:bg-[#B45309] text-white font-black px-5 py-2 rounded-xl text-xs shadow-md">Lưu CTV</button>
                </div>
            </form>
        </div>
    </div>

    <!-- JAVASCRIPT APP LOGIC -->
    <script>
        let currentTab = 'products';
        let currentOrderFilter = 'all';
        let currentAffSubTab = 'list';
        let productsData = [];
        let customersData = [];
        let ordersData = [];
        let affiliatesData = [];
        let referralsData = [];

        // TOAST NOTIFICATIONS
        function showToast(msg, type = 'success') {
            const toastBox = document.getElementById('toast-box');
            const el = document.createElement('div');
            const bgClass = type === 'success' ? 'bg-emerald-700 text-white' : 'bg-rose-700 text-white';
            el.className = `${bgClass} px-4 py-3 rounded-xl shadow-2xl text-xs font-bold pointer-events-auto transition-all transform duration-300 flex items-center gap-2 max-w-md`;
            el.innerHTML = `<span>${type === 'success' ? '✅' : '⚠️'}</span><span>${msg}</span>`;
            toastBox.appendChild(el);
            setTimeout(() => {
                el.classList.add('opacity-0', 'translate-y-[-10px]');
                setTimeout(() => el.remove(), 300);
            }, 4500);
        }

        // TAB SWITCHING
        function switchTab(tabId) {
            currentTab = tabId;
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
            document.querySelectorAll('aside nav button').forEach(b => b.classList.remove('tab-active'));

            document.getElementById(`tab-${tabId}-content`).classList.remove('hidden');
            const navBtn = document.getElementById(`nav-${tabId}`);
            if (navBtn) navBtn.classList.add('tab-active');

            if (tabId === 'products') loadProducts();
            if (tabId === 'customers') loadCustomers();
            if (tabId === 'orders') loadOrders();
            if (tabId === 'affiliates') loadAffiliates();
            if (tabId === 'settings') loadSettings();
            loadStats();
        }

        // STATS
        async function loadStats() {
            try {
                const res = await fetch('/admin/api/stats');
                const data = await res.json();
                document.getElementById('stat-products').innerText = data.total_products;
                document.getElementById('stat-customers').innerText = data.total_customers;
                document.getElementById('stat-orders').innerText = data.total_orders;
                document.getElementById('stat-revenue').innerText = Number(data.total_revenue).toLocaleString('vi-VN') + 'đ';
            } catch (err) {}
        }

        // PRODUCTS TAB LOGIC
        async function loadProducts() {
            const res = await fetch('/admin/api/products');
            productsData = await res.json();
            renderProducts();
        }

        function renderProducts() {
            const search = document.getElementById('search-products').value.toLowerCase();
            const typeFilter = document.getElementById('filter-product-type').value;

            const filtered = productsData.filter(p => {
                const matchSearch = p.name.toLowerCase().includes(search) || (p.description && p.description.toLowerCase().includes(search));
                const matchType = typeFilter === 'all' || p.type === typeFilter;
                return matchSearch && matchType;
            });

            const tbody = document.getElementById('products-tbody');
            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="p-8 text-center text-slate-500 font-medium">Không tìm thấy sản phẩm nào!</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map(p => {
                let typeBadge = '';
                if (p.type === 'physical') typeBadge = `<span class="bg-blue-100 text-blue-900 border border-blue-300 px-2.5 py-1 rounded-full text-[11px] font-black">📦 Vật lý</span>`;
                else if (p.type === 'digital') typeBadge = `<span class="bg-amber-100 text-amber-950 border border-amber-300 px-2.5 py-1 rounded-full text-[11px] font-black">⚡ Sản phẩm số</span>`;
                else typeBadge = `<span class="bg-purple-100 text-purple-950 border border-purple-300 px-2.5 py-1 rounded-full text-[11px] font-black">🛠️ Dịch vụ</span>`;

                let stockDisplay = '';
                if (p.type === 'physical') {
                    const count = p.stock_quantity !== null ? p.stock_quantity : 0;
                    const stockClass = count < 5 ? 'text-rose-700 font-black' : 'text-emerald-800 font-bold';
                    stockDisplay = `<span class="${stockClass}">${count} sản phẩm</span>`;
                } else {
                    stockDisplay = `<span class="text-slate-700 font-semibold italic">∞ Không trừ kho</span>`;
                }

                let driveDisplay = p.document_link ? 
                    `<a href="${p.document_link}" target="_blank" class="inline-flex items-center gap-1 bg-amber-50 text-amber-900 border border-amber-300 px-2.5 py-1 rounded-lg text-[11px] font-bold hover:bg-amber-100 transition-colors">📂 Drive Link</a>` : 
                    `<span class="text-slate-400 italic font-medium">Chưa có link</span>`;

                return `
                    <tr class="hover:bg-[#F5EFE6] transition-colors border-b border-[#EFEBE4]">
                        <td class="p-3.5 font-mono text-slate-900 font-extrabold">#${p.id}</td>
                        <td class="p-3.5 font-black text-slate-950 text-sm">${p.name}</td>
                        <td class="p-3.5">${typeBadge}</td>
                        <td class="p-3.5 font-black text-amber-900 text-sm">${Number(p.price).toLocaleString('vi-VN')}đ</td>
                        <td class="p-3.5">${stockDisplay}</td>
                        <td class="p-3.5">${driveDisplay}</td>
                        <td class="p-3.5 text-right space-x-2">
                            <button onclick="editProduct(${p.id})" class="text-amber-800 hover:text-amber-950 font-black underline decoration-amber-600">Sửa</button>
                            <button onclick="deleteProduct(${p.id})" class="text-rose-700 hover:text-rose-950 font-black underline decoration-rose-500">Xóa</button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function toggleStockField() {
            const type = document.getElementById('prod-type').value;
            const box = document.getElementById('stock-field-box');
            if (type === 'physical') {
                box.classList.remove('hidden');
                document.getElementById('prod-stock').required = true;
            } else {
                box.classList.add('hidden');
                document.getElementById('prod-stock').required = false;
            }
        }

        function openProductModal(p = null) {
            document.getElementById('modal-product').classList.remove('hidden');
            if (p) {
                document.getElementById('modal-product-title').innerText = 'Chỉnh sửa sản phẩm #' + p.id;
                document.getElementById('prod-id').value = p.id;
                document.getElementById('prod-name').value = p.name;
                document.getElementById('prod-type').value = p.type;
                document.getElementById('prod-price').value = p.price;
                document.getElementById('prod-stock').value = p.stock_quantity !== null ? p.stock_quantity : '';
                document.getElementById('prod-desc').value = p.description || '';
                document.getElementById('prod-doc').value = p.document_link || '';
            } else {
                document.getElementById('modal-product-title').innerText = 'Thêm sản phẩm mới';
                document.getElementById('prod-id').value = '';
                document.getElementById('prod-name').value = '';
                document.getElementById('prod-type').value = 'digital';
                document.getElementById('prod-price').value = '';
                document.getElementById('prod-stock').value = '50';
                document.getElementById('prod-desc').value = '';
                document.getElementById('prod-doc').value = '';
            }
            toggleStockField();
        }

        function closeProductModal() {
            document.getElementById('modal-product').classList.add('hidden');
        }

        function editProduct(pid) {
            const p = productsData.find(x => x.id === pid);
            if (p) openProductModal(p);
        }

        async function saveProduct(e) {
            e.preventDefault();
            const pid = document.getElementById('prod-id').value;
            const body = {
                name: document.getElementById('prod-name').value,
                type: document.getElementById('prod-type').value,
                price: document.getElementById('prod-price').value,
                stock_quantity: document.getElementById('prod-stock').value,
                description: document.getElementById('prod-desc').value,
                document_link: document.getElementById('prod-doc').value
            };

            const url = pid ? `/admin/api/products/${pid}` : '/admin/api/products';
            const method = pid ? 'PUT' : 'POST';

            const res = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const result = await res.json();
            if (result.success) {
                showToast(result.message);
                closeProductModal();
                loadProducts();
            } else {
                showToast(result.message, 'error');
            }
        }

        async function deleteProduct(pid) {
            if (!confirm(`Bạn có chắc chắn muốn xóa sản phẩm #${pid}?`)) return;
            const res = await fetch(`/admin/api/products/${pid}`, { method: 'DELETE' });
            const result = await res.json();
            showToast(result.message);
            loadProducts();
        }

        // CUSTOMERS TAB LOGIC
        async function loadCustomers() {
            const res = await fetch('/admin/api/customers');
            customersData = await res.json();
            renderCustomers();
        }

        function renderCustomers() {
            const search = document.getElementById('search-customers').value.toLowerCase();
            const filtered = customersData.filter(c => 
                c.name.toLowerCase().includes(search) || 
                (c.email && c.email.toLowerCase().includes(search)) ||
                c.phone.includes(search) || 
                (c.zalo && c.zalo.includes(search))
            );

            const tbody = document.getElementById('customers-tbody');
            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="p-8 text-center text-slate-500 font-medium">Không tìm thấy khách hàng nào!</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map(c => `
                <tr class="hover:bg-[#F5EFE6] transition-colors border-b border-[#EFEBE4]">
                    <td class="p-3.5 font-mono text-slate-900 font-extrabold">#${c.id}</td>
                    <td class="p-3.5 font-black text-slate-950 text-sm">${c.name}</td>
                    <td class="p-3.5 font-bold text-amber-900">
                        ${c.email ? `<span class="bg-amber-100 text-amber-950 px-2 py-0.5 rounded-lg border border-amber-300 font-mono text-[11px]">${c.email}</span>` : `<span class="text-rose-600 font-semibold italic text-[11px]">Chưa nhập email</span>`}
                    </td>
                    <td class="p-3.5 font-black text-slate-900">${c.phone}</td>
                    <td class="p-3.5 text-slate-800 font-medium">${c.zalo || '—'}</td>
                    <td class="p-3.5 text-slate-700 font-semibold text-[11px]">${c.registration_date || '—'}</td>
                    <td class="p-3.5 text-right space-x-2">
                        <button onclick="editCustomer(${c.id})" class="text-amber-800 hover:text-amber-950 font-black underline decoration-amber-600">Sửa</button>
                        <button onclick="deleteCustomer(${c.id})" class="text-rose-700 hover:text-rose-950 font-black underline decoration-rose-500">Xóa</button>
                    </td>
                </tr>
            `).join('');
        }

        function openCustomerModal(c = null) {
            document.getElementById('modal-customer').classList.remove('hidden');
            if (c) {
                document.getElementById('modal-customer-title').innerText = 'Chỉnh sửa khách hàng #' + c.id;
                document.getElementById('cust-id').value = c.id;
                document.getElementById('cust-name').value = c.name;
                document.getElementById('cust-email').value = c.email || '';
                document.getElementById('cust-phone').value = c.phone;
                document.getElementById('cust-zalo').value = c.zalo || '';
            } else {
                document.getElementById('modal-customer-title').innerText = 'Thêm khách hàng mới';
                document.getElementById('cust-id').value = '';
                document.getElementById('cust-name').value = '';
                document.getElementById('cust-email').value = '';
                document.getElementById('cust-phone').value = '';
                document.getElementById('cust-zalo').value = '';
            }
        }

        function closeCustomerModal() {
            document.getElementById('modal-customer').classList.add('hidden');
        }

        function editCustomer(cid) {
            const c = customersData.find(x => x.id === cid);
            if (c) openCustomerModal(c);
        }

        async function saveCustomer(e) {
            e.preventDefault();
            const cid = document.getElementById('cust-id').value;
            const body = {
                name: document.getElementById('cust-name').value,
                email: document.getElementById('cust-email').value,
                phone: document.getElementById('cust-phone').value,
                zalo: document.getElementById('cust-zalo').value
            };

            const url = cid ? `/admin/api/customers/${cid}` : '/admin/api/customers';
            const method = cid ? 'PUT' : 'POST';

            const res = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const result = await res.json();
            if (result.success) {
                showToast(result.message);
                closeCustomerModal();
                loadCustomers();
            } else {
                showToast(result.message, 'error');
            }
        }

        async function deleteCustomer(cid) {
            if (!confirm(`Bạn có chắc chắn muốn xóa khách hàng #${cid}?`)) return;
            const res = await fetch(`/admin/api/customers/${cid}`, { method: 'DELETE' });
            const result = await res.json();
            showToast(result.message);
            loadCustomers();
        }

        // AFFILIATES TAB LOGIC
        async function loadAffiliates() {
            const [affRes, refRes] = await Promise.all([
                fetch('/admin/api/affiliates'),
                fetch('/admin/api/affiliates/referrals')
            ]);
            affiliatesData = await affRes.json();
            referralsData = await refRes.json();

            // Calculate KPIs
            document.getElementById('affstat-total-ctv').innerText = affiliatesData.length;
            document.getElementById('affstat-total-referrals').innerText = referralsData.length;
            
            const pendingTotal = referralsData.filter(r => r.status === 'pending').reduce((sum, r) => sum + r.commission_amount, 0);
            const paidTotal = referralsData.filter(r => r.status === 'paid').reduce((sum, r) => sum + r.commission_amount, 0);

            document.getElementById('affstat-pending-payout').innerText = Number(pendingTotal).toLocaleString('vi-VN') + 'đ';
            document.getElementById('affstat-paid-payout').innerText = Number(paidTotal).toLocaleString('vi-VN') + 'đ';

            renderAffiliates();
            renderReferrals();
        }

        function switchAffSubTab(subTab) {
            currentAffSubTab = subTab;
            document.getElementById('affpanel-list').classList.add('hidden');
            document.getElementById('affpanel-referrals').classList.add('hidden');
            document.getElementById('afftab-list').className = 'pb-3 text-slate-500 hover:text-slate-900';
            document.getElementById('afftab-referrals').className = 'pb-3 text-slate-500 hover:text-slate-900';

            document.getElementById(`affpanel-${subTab}`).classList.remove('hidden');
            document.getElementById(`afftab-${subTab}`).className = 'pb-3 text-amber-700 border-b-2 border-amber-700';
        }

        function renderAffiliates() {
            const search = document.getElementById('search-affiliates').value.toLowerCase();
            const filtered = affiliatesData.filter(a => 
                a.name.toLowerCase().includes(search) || 
                a.code.toLowerCase().includes(search) ||
                a.phone.includes(search)
            );

            const tbody = document.getElementById('affiliates-tbody');
            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="p-8 text-center text-slate-500 font-medium">Chưa có Cộng tác viên nào!</td></tr>`;
                return;
            }

            const hostname = window.location.origin;

            tbody.innerHTML = filtered.map(a => {
                const shareLink = `${hostname}/?ref=${a.code}`;
                const bankInfo = a.bank_name ? `${a.bank_name} - ${a.bank_account}<br><span class="text-[10px] text-slate-500">${a.bank_owner || ''}</span>` : 'Chưa cập nhật STK';

                return `
                    <tr class="hover:bg-[#F5EFE6] transition-colors border-b border-[#EFEBE4]">
                        <td class="p-3.5 font-mono text-slate-900 font-extrabold">#${a.id}</td>
                        <td class="p-3.5">
                            <strong class="text-slate-950 font-black block">${a.name}</strong>
                            <span class="text-slate-500 text-[11px]">${a.phone}</span>
                        </td>
                        <td class="p-3.5">
                            <span class="bg-amber-100 text-amber-950 border border-amber-300 font-mono font-black px-2.5 py-1 rounded-lg text-xs">${a.code}</span>
                        </td>
                        <td class="p-3.5 font-black text-amber-900 text-sm">${a.commission_rate}%</td>
                        <td class="p-3.5">
                            <div class="flex items-center gap-1">
                                <input type="text" readonly value="${shareLink}" id="link-ctv-${a.id}" class="bg-[#FBF9F4] border border-[#E5DFD3] text-[10px] font-mono text-slate-700 px-2 py-1 rounded-md w-44 truncate">
                                <button onclick="copyAffiliateLink('${shareLink}')" class="bg-amber-700 hover:bg-amber-800 text-white font-bold px-2 py-1 rounded-md text-[10px]">📋 Copy Link</button>
                            </div>
                        </td>
                        <td class="p-3.5 font-bold text-slate-900 text-[11px]">${bankInfo}</td>
                        <td class="p-3.5">
                            <strong class="text-emerald-700 font-black text-sm block">${Number(a.total_earnings).toLocaleString('vi-VN')}đ</strong>
                            <span class="text-[10px] text-slate-500">(${a.total_referrals} đơn)</span>
                        </td>
                        <td class="p-3.5 text-right space-x-2">
                            <button onclick="editAffiliate(${a.id})" class="text-amber-800 hover:text-amber-950 font-black underline decoration-amber-600">Sửa</button>
                            <button onclick="deleteAffiliate(${a.id})" class="text-rose-700 hover:text-rose-950 font-black underline decoration-rose-500">Xóa</button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function copyAffiliateLink(url) {
            navigator.clipboard.writeText(url);
            showToast(`Đã sao chép Link giới thiệu CTV: ${url}`);
        }

        function renderReferrals() {
            const tbody = document.getElementById('referrals-tbody');
            if (referralsData.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="p-8 text-center text-slate-500 font-medium">Chưa có lịch sử đơn hàng giới thiệu từ CTV!</td></tr>`;
                return;
            }

            tbody.innerHTML = referralsData.map(r => {
                let statusBadge = r.status === 'paid' ? 
                    `<span class="bg-emerald-100 text-emerald-950 border border-emerald-300 px-2 py-0.5 rounded-full text-[10px] font-black">✅ Đã duyệt chi</span>` :
                    `<span class="bg-amber-100 text-amber-950 border border-amber-300 px-2 py-0.5 rounded-full text-[10px] font-black">⏳ Chờ duyệt</span>`;

                let payoutButton = r.status === 'pending' ?
                    `<button onclick="togglePayoutStatus(${r.id}, 'paid')" class="bg-emerald-700 hover:bg-emerald-800 text-white font-black px-2.5 py-1 rounded-lg text-[10px] shadow-sm">💵 Duyệt Chi</button>` :
                    `<button onclick="togglePayoutStatus(${r.id}, 'pending')" class="bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold px-2 py-1 rounded-lg text-[10px]">Đổi Chờ Duyệt</button>`;

                return `
                    <tr class="hover:bg-[#F5EFE6] transition-colors border-b border-[#EFEBE4]">
                        <td class="p-3.5 font-mono text-amber-900 font-extrabold">#${r.order_id}</td>
                        <td class="p-3.5">
                            <strong class="text-slate-950 font-black block">${r.affiliate_name}</strong>
                            <span class="bg-amber-100 text-amber-900 px-1.5 py-0.5 rounded font-mono text-[10px] font-bold">${r.affiliate_code}</span>
                        </td>
                        <td class="p-3.5">
                            <strong class="text-slate-900 block font-bold">${r.customer_name}</strong>
                            <span class="text-slate-500 text-[10px]">${r.customer_phone}</span>
                        </td>
                        <td class="p-3.5 font-bold text-slate-900">${Number(r.order_amount).toLocaleString('vi-VN')}đ</td>
                        <td class="p-3.5 font-black text-amber-800 text-sm">${Number(r.commission_amount).toLocaleString('vi-VN')}đ</td>
                        <td class="p-3.5">${statusBadge}</td>
                        <td class="p-3.5 text-slate-700 font-semibold text-[11px]">${r.created_at}</td>
                        <td class="p-3.5 text-right">${payoutButton}</td>
                    </tr>
                `;
            }).join('');
        }

        function openAffiliateModal(a = null) {
            document.getElementById('modal-affiliate').classList.remove('hidden');
            if (a) {
                document.getElementById('modal-affiliate-title').innerText = 'Chỉnh sửa Cộng Tác Viên #' + a.id;
                document.getElementById('aff-id').value = a.id;
                document.getElementById('aff-name').value = a.name;
                document.getElementById('aff-code').value = a.code;
                document.getElementById('aff-rate').value = a.commission_rate;
                document.getElementById('aff-phone').value = a.phone;
                document.getElementById('aff-email').value = a.email || '';
                document.getElementById('aff-bank-name').value = a.bank_name || '';
                document.getElementById('aff-bank-account').value = a.bank_account || '';
                document.getElementById('aff-bank-owner').value = a.bank_owner || '';
            } else {
                document.getElementById('modal-affiliate-title').innerText = 'Thêm Cộng Tác Viên Mới';
                document.getElementById('aff-id').value = '';
                document.getElementById('aff-name').value = '';
                document.getElementById('aff-code').value = '';
                document.getElementById('aff-rate').value = '15';
                document.getElementById('aff-phone').value = '';
                document.getElementById('aff-email').value = '';
                document.getElementById('aff-bank-name').value = '';
                document.getElementById('aff-bank-account').value = '';
                document.getElementById('aff-bank-owner').value = '';
            }
        }

        function closeAffiliateModal() {
            document.getElementById('modal-affiliate').classList.add('hidden');
        }

        function editAffiliate(aid) {
            const a = affiliatesData.find(x => x.id === aid);
            if (a) openAffiliateModal(a);
        }

        async function saveAffiliate(e) {
            e.preventDefault();
            const aid = document.getElementById('aff-id').value;
            const body = {
                name: document.getElementById('aff-name').value,
                code: document.getElementById('aff-code').value,
                commission_rate: document.getElementById('aff-rate').value,
                phone: document.getElementById('aff-phone').value,
                email: document.getElementById('aff-email').value,
                bank_name: document.getElementById('aff-bank-name').value,
                bank_account: document.getElementById('aff-bank-account').value,
                bank_owner: document.getElementById('aff-bank-owner').value
            };

            const url = aid ? `/admin/api/affiliates/${aid}` : '/admin/api/affiliates';
            const method = aid ? 'PUT' : 'POST';

            const res = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const result = await res.json();
            if (result.success) {
                showToast(result.message);
                closeAffiliateModal();
                loadAffiliates();
            } else {
                showToast(result.message, 'error');
            }
        }

        async function deleteAffiliate(aid) {
            if (!confirm(`Bạn có chắc chắn muốn xóa Cộng tác viên #${aid}?`)) return;
            const res = await fetch(`/admin/api/affiliates/${aid}`, { method: 'DELETE' });
            const result = await res.json();
            showToast(result.message);
            loadAffiliates();
        }

        async function togglePayoutStatus(rid, newStatus) {
            const res = await fetch(`/admin/api/affiliates/referrals/${rid}/payout`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: newStatus })
            });
            const result = await res.json();
            showToast(result.message);
            loadAffiliates();
        }

        // ORDERS TAB LOGIC
        async function loadOrders() {
            const res = await fetch(`/admin/api/orders?status=${currentOrderFilter}`);
            ordersData = await res.json();
            renderOrders();
        }

        function filterOrders(status) {
            currentOrderFilter = status;
            document.querySelectorAll('#tab-orders-content button[id^="filter-order-"]').forEach(b => {
                b.className = 'px-4 py-2 rounded-full border border-[#D5CEC0] bg-white text-xs font-bold text-slate-800 hover:border-amber-700 transition-all';
            });
            const activePill = document.getElementById(`filter-order-${status}`);
            if (activePill) activePill.className = 'filter-pill-active px-4 py-2 rounded-full border text-xs font-bold transition-all';
            loadOrders();
        }

        function renderOrders() {
            const tbody = document.getElementById('orders-tbody');
            if (ordersData.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="p-8 text-center text-slate-500 font-medium">Không có đơn hàng nào trong mục này!</td></tr>`;
                return;
            }

            tbody.innerHTML = ordersData.map(o => {
                let typeBadge = '';
                if (o.product_type === 'physical') typeBadge = `<span class="bg-blue-100 text-blue-900 border border-blue-300 px-2 py-0.5 rounded-full text-[10px] font-black">📦 Vật lý</span>`;
                else if (o.product_type === 'digital') typeBadge = `<span class="bg-amber-100 text-amber-950 border border-amber-300 px-2 py-0.5 rounded-full text-[10px] font-black">⚡ Sản phẩm số</span>`;
                else typeBadge = `<span class="bg-purple-100 text-purple-950 border border-purple-300 px-2 py-0.5 rounded-full text-[10px] font-black">🛠️ Dịch vụ</span>`;

                let statusBadge = '';
                let reminderBadges = '';
                if (o.status === 'completed') {
                    statusBadge = `<span class="bg-emerald-100 text-emerald-950 border border-emerald-300 px-2.5 py-1 rounded-full text-[11px] font-black">✓ Hoàn tất</span>`;
                } else if (o.status === 'pending') {
                    statusBadge = `<span class="bg-amber-100 text-amber-950 border border-amber-300 px-2.5 py-1 rounded-full text-[11px] font-black">⏳ Đang chờ xác nhận</span>`;
                    
                    const tag1h = o.reminder_1h_sent ? `<span class="bg-emerald-50 text-emerald-700 border border-emerald-200 px-1.5 py-0.5 rounded text-[10px] font-bold">✓ Nhắc 1h</span>` : `<span class="bg-slate-100 text-slate-500 border border-slate-200 px-1.5 py-0.5 rounded text-[10px]">Chờ nhắc 1h</span>`;
                    const tag1d = o.reminder_1d_sent ? `<span class="bg-emerald-50 text-emerald-700 border border-emerald-200 px-1.5 py-0.5 rounded text-[10px] font-bold">✓ Nhắc 1 ngày</span>` : `<span class="bg-slate-100 text-slate-500 border border-slate-200 px-1.5 py-0.5 rounded text-[10px]">Chờ nhắc 1d</span>`;
                    reminderBadges = `<div class="flex items-center gap-1 mt-1">${tag1h} ${tag1d}</div>`;
                } else if (o.status === 'refunded') {
                    statusBadge = `<span class="bg-rose-100 text-rose-950 border border-rose-300 px-2.5 py-1 rounded-full text-[11px] font-black">↩️ Đã hoàn tiền</span>`;
                } else {
                    statusBadge = `<span class="bg-orange-100 text-orange-950 border border-orange-300 px-2.5 py-1 rounded-full text-[11px] font-black">⚠️ Đang khiếu nại</span>`;
                }

                let driveDisplay = o.document_link ? 
                    `<a href="${o.document_link}" target="_blank" class="text-amber-800 hover:text-amber-950 font-bold block text-[11px] underline">📂 Drive Link</a>` : '';

                let affDisplay = o.affiliate_code ?
                    `<span class="bg-amber-100 text-amber-950 border border-amber-300 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold">🤝 ${o.affiliate_code}</span>` :
                    `<span class="text-slate-400 italic text-[10px]">Trực tiếp</span>`;

                let actionButtons = '';
                if (o.status === 'completed') {
                    actionButtons = `<button onclick="resendOrderEmail(${o.id})" class="bg-amber-100 hover:bg-amber-200 text-amber-950 border border-amber-300 font-black px-2 py-1 rounded-lg text-[10px]">📩 Gửi Tài Liệu</button>`;
                } else if (o.status === 'pending') {
                    actionButtons = `
                        <button onclick="sendManualReminder(${o.id}, '1h')" class="bg-blue-50 hover:bg-blue-100 text-blue-900 border border-blue-300 font-bold px-1.5 py-1 rounded-lg text-[10px]" title="Gửi email nhắc 1h ngay">⏳ Nhắc 1h</button>
                        <button onclick="sendManualReminder(${o.id}, '1d')" class="bg-purple-50 hover:bg-purple-100 text-purple-900 border border-purple-300 font-bold px-1.5 py-1 rounded-lg text-[10px]" title="Gửi email nhắc 1 ngày ngay">📅 Nhắc 1d</button>
                    `;
                }

                return `
                    <tr class="hover:bg-[#F5EFE6] transition-colors border-b border-[#EFEBE4]">
                        <td class="p-3.5 font-mono text-amber-900 font-extrabold">#${o.id}</td>
                        <td class="p-3.5">
                            <strong class="text-slate-950 font-black block">${o.customer_name}</strong>
                            <span class="text-amber-900 font-bold text-[11px] block">${o.customer_email || 'Chưa có email'}</span>
                            <span class="text-slate-500 font-medium text-[10px]">${o.customer_phone}</span>
                        </td>
                        <td class="p-3.5">
                            <span class="text-slate-950 font-black block mb-0.5">${o.product_name}</span>
                            <div class="flex items-center gap-1.5">${typeBadge} ${driveDisplay}</div>
                        </td>
                        <td class="p-3.5">${affDisplay}</td>
                        <td class="p-3.5 font-black text-amber-900 text-sm">${Number(o.amount).toLocaleString('vi-VN')}đ</td>
                        <td class="p-3.5">
                            ${statusBadge}
                            ${reminderBadges}
                        </td>
                        <td class="p-3.5 text-slate-700 font-semibold text-[11px]">${o.order_date}</td>
                        <td class="p-3.5 text-right space-x-1">
                            ${actionButtons}
                            <select onchange="updateOrderStatus(${o.id}, this.value)" class="bg-[#FBF9F4] border-2 border-[#DCD5C7] text-[11px] text-slate-950 font-bold px-2 py-1 rounded-lg outline-none">
                                <option value="completed" ${o.status === 'completed' ? 'selected' : ''}>Hoàn tất</option>
                                <option value="pending" ${o.status === 'pending' ? 'selected' : ''}>Chờ xác nhận</option>
                                <option value="refunded" ${o.status === 'refunded' ? 'selected' : ''}>Hoàn tiền</option>
                                <option value="disputed" ${o.status === 'disputed' ? 'selected' : ''}>Khiếu nại</option>
                            </select>
                            <button onclick="deleteOrder(${o.id})" class="text-rose-700 hover:text-rose-950 font-black underline decoration-rose-500">Xóa</button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function openOrderModal() {
            const [cRes, pRes] = await Promise.all([
                fetch('/admin/api/customers'),
                fetch('/admin/api/products')
            ]);
            customersData = await cRes.json();
            productsData = await pRes.json();

            const cSelect = document.getElementById('ord-customer');
            cSelect.innerHTML = customersData.map(c => `<option value="${c.id}">${c.name} (${c.email || 'Chưa email'} - ${c.phone})</option>`).join('');

            const pSelect = document.getElementById('ord-product');
            pSelect.innerHTML = productsData.map(p => {
                const stockText = p.type === 'physical' ? ` [Stock: ${p.stock_quantity}]` : ` [Link Drive]`;
                return `<option value="${p.id}" data-type="${p.type}" data-price="${p.price}" data-stock="${p.stock_quantity}">${p.name} - ${Number(p.price).toLocaleString('vi-VN')}đ ${stockText}</option>`;
            }).join('');

            document.getElementById('ord-quantity').value = 1;
            onOrderProductChange();
            document.getElementById('modal-order').classList.remove('hidden');
        }

        function closeOrderModal() {
            document.getElementById('modal-order').classList.add('hidden');
        }

        function onOrderProductChange() {
            const pSelect = document.getElementById('ord-product');
            const selectedOpt = pSelect.options[pSelect.selectedIndex];
            if (!selectedOpt) return;

            const ptype = selectedOpt.getAttribute('data-type');
            const pstock = selectedOpt.getAttribute('data-stock');

            const noticeBox = document.getElementById('ord-inventory-notice');
            if (ptype === 'physical') {
                noticeBox.className = 'p-3 rounded-xl text-xs font-medium border bg-blue-500/10 border-blue-500/30 text-blue-900';
                noticeBox.innerHTML = `📦 <strong>SẢN PHẨM VẬT LÝ:</strong> Kho hiện có <strong>${pstock}</strong> sản phẩm. Tạo đơn sẽ <span class="text-amber-800 font-bold">TỰ ĐỘNG TRỪ TỒN KHO</span>.`;
            } else {
                noticeBox.className = 'p-3 rounded-xl text-xs font-medium border bg-amber-500/10 border-amber-500/30 text-amber-900';
                noticeBox.innerHTML = `⚡ <strong>SẢN PHẨM SỐ / DỊCH VỤ:</strong> Đơn hàng Chờ xác nhận sẽ <span class="text-amber-800 font-bold">TỰ ĐỘNG NHẮC NHỞ THANH TOÁN SAU 1H VÀ 1 NGÀY</span> qua Email.`;
            }

            recalcOrderAmount();
        }

        function recalcOrderAmount() {
            const pSelect = document.getElementById('ord-product');
            const selectedOpt = pSelect.options[pSelect.selectedIndex];
            if (!selectedOpt) return;

            const price = parseFloat(selectedOpt.getAttribute('data-price') || 0);
            const qty = parseInt(document.getElementById('ord-quantity').value || 1);
            document.getElementById('ord-amount').value = price * qty;
        }

        async function saveOrder(e) {
            e.preventDefault();
            const body = {
                customer_id: document.getElementById('ord-customer').value,
                product_id: document.getElementById('ord-product').value,
                quantity: document.getElementById('ord-quantity').value,
                amount: document.getElementById('ord-amount').value,
                affiliate_code: document.getElementById('ord-affiliate').value,
                status: document.getElementById('ord-status').value
            };

            const res = await fetch('/admin/api/orders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const result = await res.json();
            if (result.success) {
                showToast(result.message);
                closeOrderModal();
                loadOrders();
                loadProducts();
            } else {
                showToast(result.message, 'error');
            }
        }

        async function updateOrderStatus(oid, newStatus) {
            const res = await fetch(`/admin/api/orders/${oid}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: newStatus })
            });
            const result = await res.json();
            showToast(result.message);
            loadOrders();
        }

        async function resendOrderEmail(oid) {
            showToast(`Đang gửi tài liệu qua Email cho đơn #${oid}...`);
            const res = await fetch(`/admin/api/orders/${oid}/send-email`, { method: 'POST' });
            const result = await res.json();
            showToast(result.message, result.success ? 'success' : 'error');
        }

        async function sendManualReminder(oid, type) {
            const label = type === '1h' ? '1 giờ' : '1 ngày';
            showToast(`Đang gửi email nhắc nhở ${label} cho đơn #${oid}...`);
            const res = await fetch(`/admin/api/orders/${oid}/send-reminder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: type })
            });
            const result = await res.json();
            showToast(result.message, result.success ? 'success' : 'error');
            loadOrders();
        }

        async function deleteOrder(oid) {
            if (!confirm(`Bạn có chắc chắn muốn xóa đơn hàng #${oid}?`)) return;
            const res = await fetch(`/admin/api/orders/${oid}`, { method: 'DELETE' });
            const result = await res.json();
            showToast(result.message);
            loadOrders();
        }

        // SETTINGS TAB LOGIC
        async function loadSettings() {
            const res = await fetch('/admin/api/settings');
            const settings = await res.json();
            for (const [k, v] of Object.entries(settings)) {
                const input = document.getElementById(`setting_${k}`);
                if (input) input.value = v;
            }
        }

        function switchSettingSubTab(subTab) {
            document.querySelectorAll('.setting-panel').forEach(p => p.classList.add('hidden'));
            document.querySelectorAll('button[id^="settab-"]').forEach(b => {
                b.className = 'pb-3 text-slate-500 hover:text-slate-900 font-bold';
            });
            document.getElementById(`setting-panel-${subTab}`).classList.remove('hidden');
            const activeSub = document.getElementById(`settab-${subTab}`);
            if (activeSub) activeSub.className = 'pb-3 text-amber-700 border-b-2 border-amber-700 font-bold';
        }

        async function saveSettings(e) {
            e.preventDefault();
            const inputs = document.querySelectorAll('input[id^="setting_"], textarea[id^="setting_"]');
            const body = {};
            inputs.forEach(i => {
                const key = i.id.replace('setting_', '');
                body[key] = i.value;
            });

            const res = await fetch('/admin/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const result = await res.json();
            if (result.success) {
                showToast(result.message);
            }
        }

        // INITIAL LOAD
        switchTab('products');
    </script>
</body>
</html>
'''

@app.route('/admin', methods=['GET'])
def admin_panel():
    return render_template_string(ADMIN_HTML)

if __name__ == '__main__':
    port = 5000
    print(f"[LTN Admin] Running on http://127.0.0.1:{port}/admin")
    app.run(host='0.0.0.0', port=port, debug=False)
