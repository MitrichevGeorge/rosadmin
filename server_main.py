
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import sqlite3
import os
import time
import uuid
from pathlib import Path

DB_PATH = Path('agents.db')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'changeme')  # на Render задайте env var

app = FastAPI()

# простой sqlite helper
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT,
            last_heartbeat INTEGER,
            info TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY,
            target_agent TEXT,
            body TEXT,
            created INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# служебные страницы
@app.get('/', response_class=HTMLResponse)
async def index():
    return HTMLResponse('<h2>Render agent server</h2><p>Use /admin for admin panel</p>')

@app.get('/admin', response_class=HTMLResponse)
async def admin_panel():
    # простая панель — статический HTML с fetch запросами
    html = Path('static/admin.html').read_text(encoding='utf-8')
    return HTMLResponse(html)

# API для агентов: регистрация и heartbeat
@app.post('/api/register')
async def register(data: Request):
    j = await data.json()
    agent_id = j.get('agent_id') or str(uuid.uuid4())
    name = j.get('name', '')
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO agents (id, name, last_heartbeat, info) VALUES (?, ?, ?, ?)',
                (agent_id, name, now, j.get('info', '')))
    conn.commit()
    conn.close()
    return {'agent_id': agent_id}

@app.post('/api/heartbeat')
async def heartbeat(data: Request):
    j = await data.json()
    agent_id = j.get('agent_id')
    token = j.get('token')
    if not agent_id or not token:
        raise HTTPException(400, 'agent_id and token required')
    # токен простая проверка — в админке задавайте один ADMIN_TOKEN
    if token != ADMIN_TOKEN:
        raise HTTPException(403, 'bad token')
    now = int(time.time())
    info = j.get('info', '')
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE agents SET last_heartbeat = ?, info = ? WHERE id = ?', (now, info, agent_id))
    conn.commit()
    # поиск скрипта для этого агента (если есть) — отдаём один и удаляем
    cur.execute('SELECT id, body FROM scripts WHERE target_agent = ? ORDER BY created LIMIT 1', (agent_id,))
    row = cur.fetchone()
    script = None
    if row:
        script = {'id': row[0], 'body': row[1]}
        cur.execute('DELETE FROM scripts WHERE id = ?', (row[0],))
        conn.commit()
    conn.close()
    return {'ok': True, 'script': script}

# Админ API
def check_admin_token(req: Request):
    token = req.headers.get('x-admin-token')
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='bad admin token')

@app.get('/api/agents')
async def list_agents(request: Request):
    check_admin_token(request)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, name, last_heartbeat, info FROM agents')
    rows = cur.fetchall()
    conn.close()
    agents = []
    now = int(time.time())
    for (aid, name, last, info) in rows:
        gap = now - (last or 0)
        agents.append({'id': aid, 'name': name, 'last_heartbeat': last, 'info': info, 'online': gap < 90})
    return JSONResponse(agents)

@app.post('/api/scripts')
async def push_script(request: Request):
    check_admin_token(request)
    j = await request.json()
    target = j.get('target_agent')
    body = j.get('body')
    if not target or not body:
        raise HTTPException(400, 'target_agent and body required')
    sid = str(uuid.uuid4())
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO scripts (id, target_agent, body, created) VALUES (?, ?, ?, ?)', (sid, target, body, now))
    conn.commit()
    conn.close()
    return {'id': sid}

# статические файлы (панель)
if not Path('static').exists():
    Path('static').mkdir()
    # создаём минимальный admin.html
    Path('static/admin.html').write_text('''
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Admin</title></head>
<body>
<h2>Admin panel</h2>
<p>Введите X-Admin-Token в заголовке запросов (тот же что ADMIN_TOKEN на сервере).</p>
<div>
<button onclick="fetchAgents()">Обновить список агентов</button>
<pre id="agents"></pre>
</div>
<div>
<h3>Отправить скрипт</h3>
Agent id: <input id="agt" /><br/>
<textarea id="script" rows="10" cols="60">echo hello from server</textarea><br/>
<button onclick="push()">Send</button>
</div>
<script>
async function fetchAgents(){
  const res = await fetch('/api/agents', {headers: {'x-admin-token': prompt('Админ токен:')}})
  document.getElementById('agents').textContent = await res.text()
}
async function push(){
  const token = prompt('Админ токен:')
  const target = document.getElementById('agt').value
  const body = document.getElementById('script').value
  const res = await fetch('/api/scripts', {method:'POST', headers: {'Content-Type':'application/json','x-admin-token':token}, body: JSON.stringify({target_agent:target, body:body})})
  alert(await res.text())
}
</script>
</body>
</html>
''', encoding='utf-8')

# Запуск: uvicorn server_main:app --host 0.0.0.0 --port 8000

