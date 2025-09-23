
#!/usr/bin/env python3
"""
Агент для Rosa Linux — не требует sudo. Собирает базовую информацию и отправляет heartbeat.
Хранит токен в файле config.json и запускается в фоне пользователем (nohup/tmux/crontab @reboot).
"""
import os
import sys
import json
import time
import argparse
import platform
import socket
import subprocess
from pathlib import Path
import shutil
import urllib.parse
import httpx
import resource

CONFIG = Path.home() / '.rosa_agent_config.json'
SERVER = 'https://your-render-server.example'  # замените на URL сервера

# ограничения на выполнение скрипта
def limit_resources():
    # CPU time (сек)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    # address space (bytes) — 200MB
    resource.setrlimit(resource.RLIMIT_AS, (200*1024*1024, 200*1024*1024))

def read_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}

def write_config(d):
    CONFIG.write_text(json.dumps(d))

def gather_info():
    info = {}
    info['hostname'] = platform.node()
    info['platform'] = ' '.join(platform.uname())
    try:
        info['uptime'] = open('/proc/uptime').read().split()[0]
    except Exception:
        info['uptime'] = ''
    try:
        mem = {}
        for line in open('/proc/meminfo'):
            k,v = line.split(':',1)
            mem[k.strip()] = v.strip()
        info['mem_total'] = mem.get('MemTotal','')
    except Exception:
        info['mem_total'] = ''
    # disk
    try:
        st = shutil.disk_usage('/')
        info['disk_total'] = st.total
        info['disk_free'] = st.free
    except Exception:
        pass
    # IP
    try:
        info['ip'] = socket.gethostbyname(socket.gethostname())
    except Exception:
        info['ip'] = ''
    return info

async def register(agent_name=None):
    cfg = read_config()
    async with httpx.AsyncClient() as client:
        res = await client.post(urllib.parse.urljoin(SERVER, '/api/register'), json={'name': agent_name or platform.node(), 'info': gather_info()})
        res.raise_for_status()
        j = res.json()
        cfg['agent_id'] = j['agent_id']
        write_config(cfg)
        print('Registered agent_id=', j['agent_id'])

async def loop(token):
    cfg = read_config()
    agent_id = cfg.get('agent_id')
    if not agent_id:
        print('agent not registered. run --register first')
        return
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            info = gather_info()
            try:
                res = await client.post(urllib.parse.urljoin(SERVER, '/api/heartbeat'), json={'agent_id': agent_id, 'token': token, 'info': info})
                if res.status_code == 200:
                    j = res.json()
                    script = j.get('script')
                    if script and script.get('body'):
                        print('Got script:', script['id'])
                        run_script(script['body'])
                else:
                    print('heartbeat bad:', res.status_code, await res.text())
            except Exception as e:
                print('heartbeat error', e)
            time.sleep(60)


def run_script(body: str):
    # выполняем в ограниченной среде
    workdir = Path.home() / '.rosa_agent_work'
    workdir.mkdir(exist_ok=True)
    script_file = workdir / 'script.sh'
    script_file.write_text(body)
    script_file.chmod(0o700)
    try:
        pid = os.fork()
        if pid == 0:
            # child
            os.chdir(str(workdir))
            limit_resources()
            # drop privileges are not possible without root; предполагаем пользователь не root
            subprocess.run(['/bin/sh', str(script_file)], timeout=25)
            os._exit(0)
        else:
            # parent waits a bit and returns
            os.waitpid(pid, 0)
    except AttributeError:
        # Windows or systems without fork — fallback
        try:
            limit_resources()
        except Exception:
            pass
        subprocess.run(['/bin/sh', str(script_file)], timeout=25)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--name')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--token')
    args = parser.parse_args()
    if os.geteuid() == 0:
        print('Не запускайте агент под root!')
        sys.exit(1)
    if args.register:
        import asyncio
        asyncio.run(register(args.name))
        sys.exit(0)
    if args.run:
        if not args.token:
            print('Укажите --token (admin token)')
            sys.exit(1)
        # сохраняем токен в конфиг — осторожно
        cfg = read_config()
        cfg['token'] = args.token
        write_config(cfg)
        import asyncio
        asyncio.run(loop(args.token))
