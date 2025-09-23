from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import sqlite3
import os
import time
import uuid
from pathlib import Path


DB_PATH = Path('agents.db')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'changeme') # на Render задайте env var


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
''', encoding='utf-8')
