
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect, Depends, Response, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import sqlite3
import os
import time
import uuid
import json
from pathlib import Path
from itsdangerous import TimestampSigner, BadSignature
import asyncio

DB_PATH = Path('agents.db')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'changeme')  # задайте на Render
SESSION_COOKIE = 'admin_session'
SESSION_SECRET = os.getenv('SESSION_SECRET', 'session-secret-change')
SESSION_MAX_AGE = 3600 * 8  # 8 часов

signer = TimestampSigner(SESSION_SECRET)

app = FastAPI()

# WebSocket mappings
AGENT_WS = {}  # agent_id -> WebSocket
ADMIN_WS = set()  # set of WebSocket
AGENT_LOCK = asyncio.Lock()

# DB helpers
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            secret TEXT,
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

# Static files
if not Path('static').exists():
    Path('static').mkdir()
# write admin.html if not exists
admin_html_path = Path('static/admin.html')
if not admin_html_path.exists():
    admin_html_path.write_text('''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Admin Console</title>
<style>body{font-family:Arial;margin:12px} #login{max-width:420px} #console{display:none} textarea{width:100%;height:120px}</style>
</head>
<body>
<h2>Admin Console</h2>
<div id="login">
  <p>Введите пароль администратора:</p>
  <input id="pwd" type="password"/>
  <button id="btn">Войти</button>
  <pre id="msg"></pre>
</div>
<div id="panel" style="display:none">
  <button id="list">Обновить список агентов</button>
  <select id="agents"></select>
  <button id="open">Открыть консоль</button>
  <button id="logout">Выйти</button>
  <div id="console" style="margin-top:12px">
    <pre id="out" style="height:300px;overflow:auto;background:#111;color:#0f0;padding:8px"></pre>
    <input id="cmd" style="width:80%" /> <button id="send">Send</button>
  </div>
</div>
<script>
let adminWs = null;
let sessionOk = false;
async function login(){
  const pwd = document.getElementById('pwd').value;
  const res = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({token: pwd}), credentials:'include'});
  if(res.status===200){
    document.getElementById('login').style.display='none';
    document.getElementById('panel').style.display='block';
    connectAdminWS();
  } else {
    document.getElementById('msg').textContent = 'Неверный пароль';
  }
}
async function logout(){
  await fetch('/logout', {method:'POST', credentials:'include'});
  location.reload();
}
async function listAgents(){
  const res = await fetch('/api/agents', {credentials:'include'});
  if(res.status!==200){ alert('Не авторизованы'); return }
  const agents = await res.json();
  const sel = document.getElementById('agents'); sel.innerHTML='';
  for(const a of agents){
    const opt = document.createElement('option'); opt.value=a.id; opt.textContent = `${a.name} ${a.online? '(online)':'(offline)'} ${a.id}`;
    sel.appendChild(opt);
  }
}
function connectAdminWS(){
  adminWs = new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/admin');
  adminWs.onopen = ()=>{ console.log('admin ws open'); }
  adminWs.onmessage = (ev)=>{
    const m = JSON.parse(ev.data);
    if(m.type==='agent_output'){
      const out = document.getElementById('out'); out.textContent += m.data; out.scrollTop = out.scrollHeight;
    }
    if(m.type==='info'){
      console.log('info', m.data);
    }
  }
  adminWs.onclose = ()=>{ console.log('admin ws closed'); }
}
function openConsole(){
  document.getElementById('console').style.display='block';
  document.getElementById('out').textContent='';
}
function sendCmd(){
  const cmd = document.getElementById('cmd').value;
  const agent = document.getElementById('agents').value;
  adminWs.send(JSON.stringify({type:'exec', agent_id: agent, cmd: cmd}));
}
document.getElementById('btn').onclick = login;
document.getElementById('list').onclick = listAgents;
document.getElementById('open').onclick = ()=>{ openConsole() };
document.getElementById('send').onclick = sendCmd;
document.getElementById('logout').onclick = logout;
</script>
</body>
</html>
''', encoding='utf-8')

app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get('/', response_class=HTMLResponse)
async def root():
    return HTMLResponse('<html><body><a href="/static/admin.html">Admin panel</a></body></html>')

# Authentication endpoints (server keeps ADMIN_TOKEN only)
@app.post('/login')
async def login_endpoint(req: Request):
    j = await req.json()
    token = j.get('token')
    if token != ADMIN_TOKEN:
        return PlainTextResponse('bad token', status_code=403)
    # create signed session value
    sid = str(uuid.uuid4())
    signed = signer.sign(sid.encode()).decode()
    res = PlainTextResponse('ok')
    res.set_cookie(SESSION_COOKIE, signed, max_age=SESSION_MAX_AGE, httponly=True)
    return res

@app.post('/logout')
async def logout_endpoint(response: Response):
    res = PlainTextResponse('logged out')
    res.delete_cookie(SESSION_COOKIE)
    return res

def check_session(cookie_val: str = Cookie(None)):
    if not cookie_val:
        raise HTTPException(403, 'not authenticated')
    try:
        unsigned = signer.unsign(cookie_val, max_age=SESSION_MAX_AGE)
        return True
    except BadSignature:
        raise HTTPException(403, 'bad session')

# API: registration returns agent_id and secret (agent stores secret locally)
@app.post('/api/register')
async def api_register(req: Request):
    j = await req.json()
    name = j.get('name', '')
    agent_id = str(uuid.uuid4())
    secret = str(uuid.uuid4())
    now = int(time.time())
    info_str = json.dumps(j.get('info', {}), ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO agents (id, secret, name, last_heartbeat, info) VALUES (?, ?, ?, ?, ?)', (agent_id, secret, name, now, info_str))
    conn.commit(); conn.close()
    return JSONResponse({'agent_id': agent_id, 'secret': secret})

# WebSocket for agents
@app.websocket('/ws/agent')
async def ws_agent(ws: WebSocket):
    # query params: ?agent_id=...&secret=...
    await ws.accept()
    params = ws.query_params
    agent_id = params.get('agent_id')
    secret = params.get('secret')
    if not agent_id or not secret:
        await ws.send_json({'type':'error','msg':'agent_id+secret required'})
        await ws.close()
        return
    # validate secret
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT secret FROM agents WHERE id=?', (agent_id,))
    row = cur.fetchone(); conn.close()
    if not row or row[0] != secret:
        await ws.send_json({'type':'error','msg':'bad secret'})
        await ws.close()
        return
    # register websocket
    async with AGENT_LOCK:
        AGENT_WS[agent_id] = ws
    try:
        await ws.send_json({'type':'info','msg':'connected'})
        while True:
            msg = await ws.receive_text()
            # expect JSON messages from agent: {type:'heartbeat'|'output', ...}
            try:
                j = json.loads(msg)
            except Exception:
                continue
            if j.get('type') == 'heartbeat':
                now = int(time.time())
                info = j.get('info', {})
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute('UPDATE agents SET last_heartbeat=?, info=? WHERE id=?', (now, json.dumps(info, ensure_ascii=False), agent_id))
                conn.commit(); conn.close()
            elif j.get('type') == 'output':
                data = j.get('data','')
                # forward to all connected admins
                tosend = json.dumps({'type':'agent_output','agent_id':agent_id,'data':data})
                await broadcast_admin(tosend)
            elif j.get('type') == 'exec_result':
                # final result
                tosend = json.dumps({'type':'agent_output','agent_id':agent_id,'data':j.get('data','')})
                await broadcast_admin(tosend)
    except WebSocketDisconnect:
        pass
    finally:
        async with AGENT_LOCK:
            if AGENT_WS.get(agent_id) is ws:
                del AGENT_WS[agent_id]

# WebSocket for admin
@app.websocket('/ws/admin')
async def ws_admin(ws: WebSocket, cookie: str = Cookie(None)):
    # validate session cookie
    try:
        signer.unsign(cookie, max_age=SESSION_MAX_AGE)
    except Exception:
        await ws.close(code=1008)
        return
    await ws.accept()
    ADMIN_WS.add(ws)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                j = json.loads(msg)
            except Exception:
                continue
            # admin can send: {type:'exec', agent_id:'...', cmd:'...'}
            if j.get('type') == 'exec':
                aid = j.get('agent_id')
                cmd = j.get('cmd')
                if not aid or not cmd:
                    continue
                # forward to agent if connected
                async with AGENT_LOCK:
                    agent_ws = AGENT_WS.get(aid)
                if agent_ws:
                    try:
                        await agent_ws.send_text(json.dumps({'type':'exec','cmd':cmd}))
                    except Exception:
                        await ws.send_text(json.dumps({'type':'info','data':'agent send error'}))
                else:
                    await ws.send_text(json.dumps({'type':'info','data':'agent offline'}))
            elif j.get('type') == 'list_agents':
                # send list
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute('SELECT id,name,last_heartbeat,info FROM agents')
                rows = cur.fetchall(); conn.close()
                now = int(time.time())
                arr = []
                for (aid,name,last,info) in rows:
                    gap = now - (last or 0)
                    arr.append({'id':aid,'name':name,'online': gap<90, 'info': json.loads(info) if info else {}})
                await ws.send_text(json.dumps({'type':'agents_list','data':arr}))
    except WebSocketDisconnect:
        pass
    finally:
        try:
            ADMIN_WS.remove(ws)
        except KeyError:
            pass

async def broadcast_admin(message: str):
    remove = []
    for w in list(ADMIN_WS):
        try:
            await w.send_text(message)
        except Exception:
            remove.append(w)
    for r in remove:
        try: ADMIN_WS.remove(r)
        except: pass

# API endpoint to get agents (for non-ws fallback)
@app.get('/api/agents')
async def api_agents(session_ok: bool = Depends(check_session)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id,name,last_heartbeat,info FROM agents')
    rows = cur.fetchall(); conn.close()
    now = int(time.time())
    arr = []
    for (aid,name,last,info) in rows:
        gap = now - (last or 0)
        try:
            info_obj = json.loads(info) if info else {}
        except Exception:
            info_obj = {}
        arr.append({'id':aid,'name':name,'online': gap<90, 'info': info_obj})
    return JSONResponse(arr)

# Run with: uvicorn server_main:app --host 0.0.0.0 --port $PORT
