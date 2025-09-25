import os
import time
import socket
import uuid
import threading
import json
import requests
from datetime import datetime
import psutil
import daemonize
from ptyprocess import PtyProcess
import websocket
import select
import sys

# Конфиг: замените на URL вашего Render сервера
SERVER_URL = 'https://rosadmin.onrender.com'  # Замените!
HEARTBEAT_INTERVAL = 60  # сек

# Client ID: MAC + hostname
hostname = socket.gethostname()
mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0, 48, 8)][::-1])
CLIENT_ID = f"{mac}_{hostname}"

# Device info func (без sudo)
def get_device_info():
    return {
        'hostname': hostname,
        'os': os.uname().sysname + ' ' + os.uname().release,
        'cpu_percent': psutil.cpu_percent(),
        'ram_percent': psutil.virtual_memory().percent,
        'uptime': time.time() - psutil.boot_time(),
        'local_ip': socket.gethostbyname(hostname),
        'mac': mac
    }

# Heartbeat thread
def heartbeat_loop():
    while True:
        info = get_device_info()
        try:
            requests.post(f'{SERVER_URL}/heartbeat', json={'client_id': CLIENT_ID, 'info': info}, timeout=10)
        except Exception as e:
            print(f"Heartbeat failed: {e}", file=sys.stderr)
        time.sleep(HEARTBEAT_INTERVAL)

# Execute script func
def execute_script(script):
    try:
        result = os.popen(script).read()
        return {'success': True, 'output': result}
    except Exception as e:
        return {'success': False, 'error': str(e)}

# Terminal WS handler
class TerminalWS:
    def __init__(self):
        self.ws = None
        self.pty = None
        self.shell = '/bin/bash'  # Или sh

    def on_open(self, ws):
        self.pty = PtyProcess.spawn(self.shell)
        threading.Thread(target=self.read_pty, daemon=True).start()
        ws.send(json.dumps({'type': 'ready', 'client_id': CLIENT_ID}))

    def on_message(self, ws, message):
        data = json.loads(message)
        if data['type'] == 'input':
            self.pty.write(data['data'].encode())

    def on_close(self, ws, *args):
        if self.pty:
            self.pty.terminate()

    def read_pty(self):
        while self.pty.isalive():
            try:
                output = self.pty.read(1024).decode(errors='ignore')
                if output:
                    self.ws.send(json.dumps({'type': 'output', 'data': output, 'client_id': CLIENT_ID}))
            except:
                break

def terminal_loop():
    def on_message(ws, message):
        data = json.loads(message)
        if data['type'] == 'input':
            term.on_message(ws, json.dumps({'type': 'input', 'data': data['data']}))

    def on_open(ws):
        term.on_open(ws)

    ws_url = f"{SERVER_URL.replace('https://', 'wss://')}/socket.io/?EIO=4&transport=websocket"
    # Для SocketIO, используйте websocket-client с subprotocol, но упрощённо: используем raw WS (адаптируйте под socketio-client если нужно)
    # Здесь placeholder: используйте pip install socketio-client для full
    import socketio
    sio = socketio.Client()
    sio.connect(SERVER_URL)
    sio.emit('join_client', {'client_id': CLIENT_ID})
    @sio.on('terminal_input')
    def on_terminal_input(data):
        # Forward to pty
        term.pty.write(data['data'].encode() if term.pty else b'')

    # Output from pty to sio
    def pty_to_sio():
        while term.pty and term.pty.isalive():
            output = term.pty.read(1024).decode(errors='ignore')
            if output:
                sio.emit('terminal_output', {'output': output, 'client_id': CLIENT_ID})

    threading.Thread(target=pty_to_sio, daemon=True).start()
    sio.wait()

# Main
if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'run'
    if action == 'start':
        daemon = daemonize.Daemonize(app="remote_client", pid="/tmp/remote_client.pid", action=lambda: main())
        daemon.start()
    elif action == 'stop':
        with open('/tmp/remote_client.pid', 'r') as f:
            os.kill(int(f.read()), 15)
    else:
        main()

def main():
    # Start heartbeat
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    
    # Start terminal WS
    global term
    term = TerminalWS()
    terminal_loop()  # Blocks, but daemon ok

    # Listen for execute (poll WS or HTTP, here via sio in terminal_loop)
    # Для execute: in sio.on('execute_script')
    # @sio.on('execute_script')
    # def on_execute(data):
    #     result = execute_script(data['script'])
    #     sio.emit('execute_result', {'result': result, 'client_id': CLIENT_ID})