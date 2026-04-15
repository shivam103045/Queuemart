from flask import Flask, render_template, request, jsonify, redirect, url_for
import sqlite3, os, random, io, base64, smtplib, json
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import threading
from PIL import Image, ImageDraw


app = Flask(__name__)
app.secret_key = "queuesmart-secret-2024"
DB = os.path.join(os.path.dirname(__file__), "queue.db")

# ── NOTIFICATION CONFIG — fill these to enable SMS/Email ────────────────────
NOTIFY_CFG = {
    # Email (Gmail SMTP) — set SMTP_USER and SMTP_PASS to enable
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": os.environ.get("SMTP_USER", ""),   # your Gmail address
    "smtp_pass": os.environ.get("SMTP_PASS", ""),   # Gmail App Password

    # SMS via Twilio — set TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM to enable
    "twilio_sid":   os.environ.get("TWILIO_SID",   ""),
    "twilio_token": os.environ.get("TWILIO_TOKEN", ""),
    "twilio_from":  os.environ.get("TWILIO_FROM",  ""),  # e.g. +1234567890
}

# In-memory push subscriptions store: {token_num: [subscription_json, ...]}
PUSH_SUBS = {}

def send_email_notification(to_email, name, token, service_label):
    cfg = NOTIFY_CFG
    if not cfg["smtp_user"] or not cfg["smtp_pass"]:
        print(f"[Email] Skipped — SMTP not configured. Would send to: {to_email}")
        return False
    try:
        msg = MIMEText(
            f"Hello {name},\n\n"
            f"Your token {token} for {service_label} is now being called.\n"
            f"Please proceed to the counter immediately.\n\n"
            f"— QueueSmart"
        )
        msg["Subject"] = f"🔔 Your turn is here — Token {token}"
        msg["From"]    = cfg["smtp_user"]
        msg["To"]      = to_email
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as s:
            s.starttls(); s.login(cfg["smtp_user"], cfg["smtp_pass"]); s.send_message(msg)
        print(f"[Email] Sent to {to_email} for {token}")
        return True
    except Exception as e:
        print(f"[Email] Failed: {e}"); return False

def send_sms_notification(phone, name, token, service_label):
    cfg = NOTIFY_CFG
    if not cfg["twilio_sid"] or not cfg["twilio_token"] or not cfg["twilio_from"]:
        print(f"[SMS] Skipped — Twilio not configured. Would send to: {phone}")
        return False
    try:
        import urllib.request, urllib.parse
        body = f"QueueSmart: Hi {name}, your token {token} ({service_label}) is now being called. Please go to the counter!"
        data = urllib.parse.urlencode({"To": phone, "From": cfg["twilio_from"], "Body": body}).encode()
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{cfg['twilio_sid']}/Messages.json"
        req  = urllib.request.Request(url, data=data, method="POST")
        creds = base64.b64encode(f"{cfg['twilio_sid']}:{cfg['twilio_token']}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        urllib.request.urlopen(req, timeout=8)
        print(f"[SMS] Sent to {phone} for {token}")
        return True
    except Exception as e:
        print(f"[SMS] Failed: {e}"); return False

def fire_notifications(user, svc_label):
    """Send all enabled notifications for a user whose token was just called."""
    threading.Thread(target=lambda: (
        send_email_notification(user["email"], user["name"], user["token_num"], svc_label),
        send_sms_notification(user["phone"], user["name"], user["token_num"], svc_label)
    ), daemon=True).start()


# ── DB ──────────────────────────────────────────────────────────────────────
def get_db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            phone TEXT NOT NULL, email TEXT NOT NULL, service TEXT NOT NULL,
            priority TEXT DEFAULT 'normal', token_num TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'waiting', created_at TEXT NOT NULL, served_at TEXT);
        CREATE TABLE IF NOT EXISTS queue_state (
            id INTEGER PRIMARY KEY DEFAULT 1, current_token INTEGER DEFAULT 0,
            total_issued INTEGER DEFAULT 0, is_open INTEGER DEFAULT 1, updated_at TEXT);
        INSERT OR IGNORE INTO queue_state (id,current_token,total_issued,updated_at)
        VALUES (1,0,0,datetime('now'));
        """)
        c.execute("DELETE FROM users")
        c.execute("UPDATE queue_state SET current_token=0,total_issued=0,updated_at=datetime('now') WHERE id=1")
    print("✅ Database initialized — queue is empty and ready")

init_db()

# ── QR ──────────────────────────────────────────────────────────────────────
def generate_qr_image(data):
    cell, grid = 10, 21
    sz = grid * cell + 40
    img = Image.new("RGB", (sz, sz), "white")
    draw = ImageDraw.Draw(img)
    rng = random.Random(sum(ord(c)*(i+1) for i,c in enumerate(data)))
    def finder(x, y):
        for r in range(7):
            for c in range(7):
                if r in (0,6) or c in (0,6) or (1<r<5 and 1<c<5):
                    draw.rectangle([x+c*cell+20, y+r*cell+20, x+c*cell+20+cell-1, y+r*cell+20+cell-1], fill="black")
    finder(0,0); finder((grid-7)*cell,0); finder(0,(grid-7)*cell)
    reserved = {(r,c) for r in range(7) for c in range(7)} | {(r,grid-7+c) for r in range(7) for c in range(7)} | {(grid-7+r,c) for r in range(7) for c in range(7)}
    for r in range(grid):
        for c in range(grid):
            if (r,c) not in reserved and rng.random()>0.5:
                draw.rectangle([c*cell+20, r*cell+20, c*cell+20+cell-1, r*cell+20+cell-1], fill="black")
    draw.rectangle([0,0,sz-1,sz-1], outline="black", width=2)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

# ── QUEUE ANALYTICS ─────────────────────────────────────────────────────────
def get_queue_analytics():
    """Return real analytics derived from actual queue data."""
    with get_db() as c:
        waiting = c.execute("SELECT service, priority, created_at FROM users WHERE status='waiting'").fetchall()
        served  = c.execute("SELECT service, created_at, served_at FROM users WHERE status='served' AND served_at IS NOT NULL").fetchall()

    total_waiting = len(waiting)

    # Avg real service time from served records
    real_times = []
    for r in served:
        try:
            s = datetime.fromisoformat(r["created_at"]); e = datetime.fromisoformat(r["served_at"])
            diff = (e - s).total_seconds() / 60
            if 0 < diff < 120: real_times.append(diff)
        except: pass
    avg_service_min = round(sum(real_times) / len(real_times), 1) if real_times else None

    by_service = {}
    for r in waiting:
        by_service[r["service"]] = by_service.get(r["service"], 0) + 1

    priority_counts = {}
    for r in waiting:
        priority_counts[r["priority"]] = priority_counts.get(r["priority"], 0) + 1

    return {
        "total_waiting": total_waiting,
        "by_service": by_service,
        "priority_counts": priority_counts,
        "avg_real_service_min": avg_service_min,
        "total_served": len(served),
    }

# ── HELPERS ─────────────────────────────────────────────────────────────────
SVC = {
    "hospital":{"icon":"🏥","label":"Hospital / Clinic","speed":1.5},
    "bank":    {"icon":"🏦","label":"Bank / Finance","speed":2.0},
    "event":   {"icon":"🎵","label":"Concert / Event","speed":0.8},
    "govt":    {"icon":"🏢","label":"Government Office","speed":3.0},
    "retail":  {"icon":"🛒","label":"Retail / Store","speed":1.0},
    "other":   {"icon":"📋","label":"Other Service","speed":1.5},
}
BOOST = {"normal":0,"senior":-5,"disabled":-8,"pregnant":-10}

def get_state():
    with get_db() as c: row = c.execute("SELECT * FROM queue_state WHERE id=1").fetchone()
    return dict(row) if row else {}

def next_token():
    with get_db() as c:
        c.execute("UPDATE queue_state SET total_issued=total_issued+1,updated_at=datetime('now') WHERE id=1")
        return c.execute("SELECT total_issued FROM queue_state WHERE id=1").fetchone()["total_issued"]

def fmt(n): return f"T-{n:03d}"

def estimate_wait(n, service, priority):
    st = get_state(); cur = st.get("current_token",0)
    speed = SVC.get(service, SVC["other"])["speed"]
    ahead = max(0, n - cur - 1 + BOOST.get(priority, 0))
    # Even if ahead=0 (you're next), you still wait at least one service slot
    wait_minutes = round((ahead + 1) * speed)
    is_next = (ahead == 0) and (cur > 0 or n == 1)
    return {
        "ahead": ahead,
        "wait_minutes": wait_minutes,
        "current_serving": fmt(cur) if cur > 0 else "—",
        "is_next": is_next,
        "position": ahead + 1,
    }



# ── PAGE ROUTES ──────────────────────────────────────────────────────────────
@app.route("/")
def home():
    st = get_state()
    with get_db() as c:
        w = c.execute("SELECT COUNT(*) as c FROM users WHERE status='waiting'").fetchone()["c"]
        s = c.execute("SELECT COUNT(*) as c FROM users WHERE status='served'").fetchone()["c"]
    return render_template("index.html", waiting=w, served=s, current=fmt(st.get("current_token",0)), is_open=st.get("is_open",1))

@app.route("/register")
def register_page():
    return render_template("register.html", service=request.args.get("service","hospital"), services=SVC)

@app.route("/token/<tok>")
def token_page(tok):
    with get_db() as c: user = c.execute("SELECT * FROM users WHERE token_num=?", (tok,)).fetchone()
    if not user: return redirect(url_for("home"))
    user = dict(user)
    wait = estimate_wait(int(tok.replace("T-","")), user["service"], user["priority"])
    return render_template("token.html", user=user, wait=wait, svc_info=SVC.get(user["service"],SVC["other"]), qr_b64=generate_qr_image(f"http://localhost:5000/token/{tok}"), state=get_state())

@app.route("/dashboard")
def dashboard():
    with get_db() as c:
        wu = c.execute("SELECT * FROM users WHERE status='waiting' ORDER BY id ASC LIMIT 20").fetchall()
        sd = c.execute("SELECT COUNT(*) as c FROM users WHERE status='served'").fetchone()["c"]
        ti = c.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    return render_template("dashboard.html", waiting_users=[dict(u) for u in wu], served_today=sd, total_issued=ti, state=get_state(), services=SVC)

# ── API ROUTES ───────────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.get_json()
    for f in ["name","phone","email","service"]:
        if not d.get(f,"").strip(): return jsonify({"ok":False,"error":f"{f} is required"}),400
    svc = d["service"] if d["service"] in SVC else "other"
    pri = d.get("priority","normal")
    n   = next_token(); tok = fmt(n); now = datetime.now().isoformat()
    with get_db() as c:
        c.execute("INSERT INTO users (name,phone,email,service,priority,token_num,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (d["name"].strip(),d["phone"].strip(),d["email"].strip(),svc,pri,tok,"waiting",now))
    w = estimate_wait(n, svc, pri)
    return jsonify({"ok":True,"token":tok,"name":d["name"].strip(),"service":svc,"service_label":SVC[svc]["label"],"service_icon":SVC[svc]["icon"],"priority":pri,"ahead":w["ahead"],"wait_minutes":w["wait_minutes"],"current_serving":w["current_serving"],"is_next":w["is_next"],"position":w["position"],"qr_b64":generate_qr_image(f"Token:{tok}|{d['name']}|{SVC[svc]['label']}"),"created_at":now})

@app.route("/api/status/<tok>")
def api_status(tok):
    with get_db() as c:
        user = c.execute("SELECT * FROM users WHERE token_num=?", (tok,)).fetchone()
        if not user: return jsonify({"ok":False,"error":"Token not found"}),404
        total = c.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    user = dict(user)
    w = estimate_wait(int(tok.replace("T-","")), user["service"], user["priority"])
    return jsonify({"ok":True,"status":user["status"],"token":tok,"name":user["name"],"service":user["service"],"ahead":w["ahead"],"wait_minutes":w["wait_minutes"],"current_serving":w["current_serving"],"is_next":w["is_next"],"position":w["position"],"total_issued":total,"current_token_num":get_state().get("current_token",0)})

@app.route("/api/crowd")
def api_crowd():
    analytics = get_queue_analytics()
    total = analytics["total_waiting"]
    MAX_CAPACITY = 50
    by_svc = analytics["by_service"]
    # Map real queue data to meaningful zone breakdown
    zones = [
        {"zone": "waiting",  "label": "Waiting Area", "count": total,                      "capacity": MAX_CAPACITY},
        {"zone": "counter1", "label": "Counter 1",     "count": by_svc.get("bank", 0) + by_svc.get("govt", 0),  "capacity": 10},
        {"zone": "counter2", "label": "Counter 2",     "count": by_svc.get("hospital", 0) + by_svc.get("retail", 0), "capacity": 10},
        {"zone": "other",    "label": "Other Services","count": by_svc.get("event", 0) + by_svc.get("other", 0), "capacity": 10},
    ]
    results = []
    for z in zones:
        pct = min(round((z["count"] / z["capacity"]) * 100, 1), 100) if z["capacity"] else 0
        status = "Low" if pct < 40 else "Moderate" if pct < 70 else "High"
        color  = "#4ade80" if pct < 40 else "#fbbf24" if pct < 70 else "#f87171"
        results.append({"zone": z["zone"], "label": z["label"], "count": z["count"],
                         "capacity": z["capacity"], "density_pct": pct, "status": status, "color": color,
                         "timestamp": datetime.now().strftime("%H:%M:%S")})
    return jsonify({"ok": True, "zones": results, "total_waiting": total, "timestamp": datetime.now().strftime("%H:%M:%S")})

@app.route("/api/queue")
def api_queue():
    st = get_state()
    with get_db() as c:
        waiting = c.execute("SELECT * FROM users WHERE status='waiting' ORDER BY id ASC LIMIT 15").fetchall()
        sc = c.execute("SELECT COUNT(*) as c FROM users WHERE status='served'").fetchone()["c"]
        total = c.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    queue = [{"token":u["token_num"],"name":u["name"],"service":u["service"],"service_icon":SVC.get(u["service"],SVC["other"])["icon"],"service_label":SVC.get(u["service"],SVC["other"])["label"],"priority":u["priority"],"position":i+1,"is_next":i==0} for i,u in enumerate([dict(u) for u in waiting])]
    avg_speed = sum(SVC[u["service"]]["speed"] if u["service"] in SVC else 1.5 for u in [dict(u) for u in waiting]) / max(len(waiting), 1)
    return jsonify({"ok":True,"queue":queue,"current_serving":fmt(st.get("current_token",0)) if st.get("current_token",0)>0 else "—","waiting_count":len(queue),"served_today":sc,"total_issued":total,"avg_wait_min":round(len(queue)*avg_speed)})

@app.route("/api/next", methods=["POST"])
def api_next():
    with get_db() as c:
        nxt = c.execute("SELECT * FROM users WHERE status='waiting' ORDER BY id ASC LIMIT 1").fetchone()
        if not nxt: return jsonify({"ok":False,"error":"Queue is empty"})
        nxt = dict(nxt); n = int(nxt["token_num"].replace("T-",""))
        c.execute("UPDATE users SET status='served',served_at=datetime('now') WHERE token_num=?",(nxt["token_num"],))
        c.execute("UPDATE queue_state SET current_token=?,updated_at=datetime('now') WHERE id=1",(n,))
    svc = SVC.get(nxt["service"],SVC["other"])
    fire_notifications(nxt, svc["label"])
    return jsonify({"ok":True,"called":nxt["token_num"],"name":nxt["name"],"service_label":svc["label"],"service_icon":svc["icon"]})

@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    d = request.get_json()
    tok = d.get("token"); sub = d.get("subscription")
    if not tok or not sub: return jsonify({"ok":False,"error":"Missing token or subscription"}),400
    PUSH_SUBS.setdefault(tok, []).append(sub)
    print(f"[Push] Subscription saved for {tok}")
    return jsonify({"ok":True,"message":"Push subscription saved"})

@app.route("/api/push/status")
def push_status():
    """Let frontend check if push is supported and SMTP/SMS are configured."""
    return jsonify({
        "ok": True,
        "email_configured": bool(NOTIFY_CFG["smtp_user"] and NOTIFY_CFG["smtp_pass"]),
        "sms_configured":   bool(NOTIFY_CFG["twilio_sid"] and NOTIFY_CFG["twilio_token"]),
    })

@app.route("/api/cancel/<tok>", methods=["POST"])
def api_cancel(tok):
    with get_db() as c:
        user = c.execute("SELECT * FROM users WHERE token_num=?", (tok,)).fetchone()
        if not user: return jsonify({"ok":False,"error":"Token not found"}),404
        user = dict(user)
        if user["status"] != "waiting": return jsonify({"ok":False,"error":f"Token is already '{user['status']}' and cannot be cancelled"}),400
        c.execute("UPDATE users SET status='cancelled',served_at=datetime('now') WHERE token_num=?",(tok,))
    return jsonify({"ok":True,"token":tok,"name":user["name"],"service_label":SVC.get(user["service"],SVC["other"])["label"],"message":f"Token {tok} cancelled successfully"})

@app.route("/api/crowd/history")
def api_crowd_history():
    """Return real queue activity over the last 30 minutes bucketed by 2-min intervals."""
    with get_db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT created_at as timestamp, 'joined' as event FROM users WHERE created_at >= datetime('now','-30 minutes') "
            "UNION ALL "
            "SELECT served_at as timestamp, 'served' as event FROM users WHERE status='served' AND served_at >= datetime('now','-30 minutes') "
            "ORDER BY timestamp ASC"
        ).fetchall()]
    buckets = {}
    for r in rows:
        try:
            t = datetime.fromisoformat(r["timestamp"])
            t = t.replace(second=0, microsecond=0, minute=(t.minute // 2) * 2)
            k = t.strftime("%H:%M")
            buckets.setdefault(k, 0)
            buckets[k] += 1
        except: pass
    # Fill any gaps in the last 30 min with 0
    now = datetime.now()
    data = []
    for i in range(15):
        t = now - timedelta(minutes=28 - i*2)
        k = t.replace(second=0, microsecond=0, minute=(t.minute // 2) * 2).strftime("%H:%M")
        data.append({"time": k, "count": buckets.get(k, 0)})
    return jsonify({"ok": True, "history": data, "source": "real_queue_activity"})

@app.route("/api/stats")
def api_stats():
    st = get_state()
    with get_db() as c:
        w = c.execute("SELECT COUNT(*) as c FROM users WHERE status='waiting'").fetchone()["c"]
        s = c.execute("SELECT COUNT(*) as c FROM users WHERE status='served'").fetchone()["c"]
        total = c.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        by_svc = {r["service"]:r["c"] for r in c.execute("SELECT service,COUNT(*) as c FROM users WHERE status='waiting' GROUP BY service").fetchall()}
    avg_wait = round(sum(by_svc.get(s, 0) * SVC.get(s, SVC["other"])["speed"] for s in SVC) / max(sum(by_svc.values()), 1) * w)
    analytics = get_queue_analytics()
    return jsonify({"ok":True,"waiting":w,"served":s,"total":total,"current_serving":fmt(st.get("current_token",0)) if st.get("current_token",0)>0 else "—","avg_wait":avg_wait,"by_service":by_svc,"priority_counts":analytics["priority_counts"],"avg_real_service_min":analytics["avg_real_service_min"]})



if __name__ == "__main__":
    print("\n🎯 QueueSmart | http://localhost:5000  |  /dashboard  |  /register\n")
    app.run(debug=True, use_reloader=False, port=5000)
