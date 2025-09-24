
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
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'changeme') # задайте на Render
SESSION_COOKIE = 'admin_session'
SESSION_SECRET = os.getenv('SESSION_SECRET', 'session-secret-change')
SESSION_MAX_AGE = 3600 * 8 # 8 часов


signer = TimestampSigner(SESSION_SECRET)


app = FastAPI()


# WebSocket mappings
AGENT_WS = {} # agent_id -> WebSocket
ADMIN_WS = set() # set of WebSocket
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
return JSONResponse(arr)
