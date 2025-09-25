import os
import sqlite3
import uuid
import time
from flask import Flask, request, g, render_template, abort, jsonify

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN","changeme")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET","devsecret")

# --- База ---
def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    # таблица агентов
    db.execute("""
    CREATE TABLE IF NOT EXISTS agents(
        id TEXT PRIMARY KEY,
        hostname TEXT,
        info TEXT,
        last_seen INTEGER,
        token TEXT
    )
    """)
    # таблица задач
    db.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY,
        name TEXT,
        script TEXT,
        target_agent TEXT,
        status TEXT,
        result TEXT,
        created_at INTEGER
    )
    """)
    db.commit()

@app.before_first_request
def setup():
    init_db()

@app.teardown_appcontext
def close_conn(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()

# --- Helpers ---
def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if token != ADMIN_TOKEN:
        abort(401)

# --- Админка ---
@app.route("/admin")
def admin_index():
    require_admin()
    db = get_db()
    agents = db.execute("SELECT * FROM agents").fetchall()
    tasks = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    return render_template("admin.html", agents=agents, tasks=tasks, token=ADMIN_TOKEN)

@app.route("/admin/create_task", methods=["POST"])
def admin_create_task():
    require_admin()
    name = request.form.get("name","task")
    script = request.form.get("script","")
    target = request.form.get("target_agent","")
    tid = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO tasks(id,name,script,target_agent,status,result,created_at) VALUES (?,?,?,?,?,?,?)",
               (tid,name,script,target,"pending","", int(time.time())))
    db.commit()
    return "", 204

# --- API для агентов ---
@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    hostname = data.get("hostname","unknown")
    info = data.get("info","")
    aid = str(uuid.uuid4())
    token = str(uuid.uuid4())
    now = int(time.time())
    db = get_db()
    db.execute("INSERT INTO agents(id,hostname,info,last_seen,token) VALUES (?,?,?,?,?)",
               (aid, hostname, info, now, token))
    db.commit()
    return jsonify({"agent_id": aid, "agent_token": token})

@app.route("/api/poll", methods=["POST"])
def api_poll():
    data = request.get_json() or {}
    aid = data.get("agent_id")
    token = data.get("agent_token")
    now = int(time.time())
    db = get_db()
    agent = db.execute("SELECT * FROM agents WHERE id=? AND token=?", (aid, token)).fetchone()
    if not agent:
        return jsonify({"error":"unknown agent"}), 403
    # обновление инфо и heartbeat
    info = data.get("info")
    if info:
        db.execute("UPDATE agents SET info=? WHERE id=?", (info, aid))
    db.execute("UPDATE agents SET last_seen=? WHERE id=?", (now, aid))
    db.commit()

    # берем первую задачу для агента или общую
    task = db.execute("SELECT * FROM tasks WHERE status='pending' AND (target_agent=? OR target_agent='') ORDER BY created_at LIMIT 1",
                      (aid,)).fetchone()
    if not task:
        return jsonify({"task": None})
    db.execute("UPDATE tasks SET status='running' WHERE id=?", (task["id"],))
    db.commit()
    return jsonify({"task": {"id": task["id"], "name":task["name"], "script":task["script"]}})

@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json() or {}
    aid = data.get("agent_id")
    token = data.get("agent_token")
    tid = data.get("task_id")
    status = data.get("status","done")
    result = data.get("result","")
    db = get_db()
    agent = db.execute("SELECT * FROM agents WHERE id=? AND token=?", (aid, token)).fetchone()
    if not agent:
        return jsonify({"error":"unknown agent"}), 403
    db.execute("UPDATE tasks SET status=?, result=? WHERE id=?", (status, result, tid))
    db.commit()
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
