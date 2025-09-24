#!/usr/bin/env python3
"""
Агент для Rosa Linux.
- Регистрируется через /api/register и получает agent_id и secret (секрет хранится локально).
- Подключается по WebSocket к /ws/agent?agent_id=...&secret=...
- Поддерживает heartbeat и выполнение команд, стримит stdout/stderr.
"""
import asyncio
import httpx
import os
import sys
import json
import time
import argparse
import platform
import socket
import subprocess
from pathlib import Path

CONFIG = Path.home() / '.rosa_agent_config.json'
SERVER = os.getenv('SERVER_URL', 'http://localhost:8000')

def read_cfg():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}

def write_cfg(d):
    CONFIG.write_text(json.dumps(d))

async def register(name=None):
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{SERVER}/api/register", json={'name': name or platform.node(), 'info': gather_info() })
        res.raise_for_status()
        j = res.json()
        cfg = read_cfg()
        cfg['agent_id'] = j['agent_id']
        cfg['secret'] = j['secret']
        write_cfg(cfg)
        print('Registered:', j['agent_id'])

def gather_info():
    info = {}
    info['hostname'] = platform.node()
    info['platform'] = ' '.join(platform.uname())
    try:
        info['uptime'] = open('/proc/uptime').read().split()[0]
    except Exception:
        info['uptime']=''
    try:
        info['ip']=socket.gethostbyname(socket.gethostname())
    except Exception:
        info['ip']=''
    return info

async def run_loop():
    cfg = read_cfg()
    agent_id = cfg.get('agent_id')
    secret = cfg.get('secret')
    if not agent_id or not secret:
        print('agent not registered. run --register')
        return
    ws_url = f"{SERVER.replace('http','ws')}/ws/agent?agent_id={agent_id}&secret={secret}"
    async with httpx.AsyncClient() as client:
        try:
            async with client.websocket(ws_url) as ws:
                print('connected ws')
                # send periodic heartbeat and listen for exec
                async def sender():
                    while True:
                        await ws.send_text(json.dumps({'type':'heartbeat','info':gather_info()}))
                        await asyncio.sleep(60)
                async def receiver():
                    async for msg in ws.iter_text():
                        try:
                            j = json.loads(msg)
                        except Exception:
                            continue
                        if j.get('type')=='exec':
                            cmd = j.get('cmd')
                            if cmd:
                                await execute_and_stream(cmd, ws)
                await asyncio.gather(sender(), receiver())
        except Exception as e:
            print('ws error', e)

async def execute_and_stream(cmd, ws):
    # execute command in shell, stream stdout/stderr
    print('exec:', cmd)
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def stream(pipe, tag):
        while True:
            line = await pipe.readline()
            if not line:
                break
            try:
                await ws.send_text(json.dumps({'type':'output','data': line.decode(errors='replace')}))
            except Exception:
                pass
    await asyncio.gather(stream(proc.stdout,'out'), stream(proc.stderr,'err'))
    rc = await proc.wait()
    try:
        await ws.send_text(json.dumps({'type':'exec_result','data': f'Process exited with {rc}
'}))
    except Exception:
        pass

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--name')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.register:
        asyncio.run(register(args.name))
    elif args.run:
        try:
            asyncio.run(run_loop())
        except KeyboardInterrupt:
            pass
    else:
        print('use --register or --run')
