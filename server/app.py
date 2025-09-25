import os
import time
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

# Env var для пароля
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'default_pass')  # Задайте на Render!

# Хранение клиентов в памяти: {client_id: {'name': str, 'last_heartbeat': ts, 'info': dict, 'ws_sid': str}}
clients = {}
clients_lock = True  # Для простоты, в prod используйте threading.Lock()

def is_online(client_id):
    return time.time() - clients.get(client_id, {}).get('last_heartbeat', 0) < 60

@app.route('/')
def index():
    if 'auth' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == ADMIN_PASSWORD:
            session['auth'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid password')
    return render_template('login.html')

@app.route('/clients', methods=['GET', 'POST'])
def clients_list():
    if 'auth' not in session:
        abort(401)
    if request.method == 'POST':
        action = request.form.get('action')
        client_id = request.form.get('client_id')
        if action == 'rename' and client_id in clients:
            clients[client_id]['name'] = request.form.get('new_name', f'Client {client_id[:8]}')
            return jsonify({'success': True})
        elif action == 'execute' and client_id in clients:
            script = request.form.get('script')
            # Отправляем команду клиенту via WS или queue (здесь упрощённо: emit to WS if connected)
            if clients[client_id].get('ws_sid'):
                socketio.emit('execute_script', {'script': script}, room=client_id)
            return jsonify({'success': True})
    # GET: список клиентов
    client_list = []
    for cid, data in clients.items():
        client_list.append({
            'id': cid,
            'name': data.get('name', f'Client {cid[:8]}'),
            'online': is_online(cid),
            'last_seen': datetime.fromtimestamp(data.get('last_heartbeat', 0)).strftime('%Y-%m-%d %H:%M:%S'),
            'info': data.get('info', {})
        })
    return render_template('clients.html', clients=client_list)

@app.route('/terminal/<client_id>', methods=['GET'])
def terminal(client_id):
    if 'auth' not in session or client_id not in clients:
        abort(401)
    return render_template('terminal.html', client_id=client_id)

# WebSocket events
@socketio.on('connect')
def handle_connect(auth):
    if 'auth' not in session:
        return False
    emit('connected', {'data': 'Connected'})

@socketio.on('heartbeat')
def handle_heartbeat(data):
    client_id = data['client_id']
    clients[client_id] = {
        'last_heartbeat': time.time(),
        'info': data['info'],
        'ws_sid': request.sid,  # Для emit
        'name': clients.get(client_id, {}).get('name', f'Client {client_id[:8]}')
    }
    emit('status', {'online': True})

@socketio.on('join_client')
def join_client(data):
    client_id = data['client_id']
    join_room(client_id)
    clients[data['client_id']]['ws_sid'] = request.sid

@socketio.on('terminal_input')
def handle_terminal_input(data):
    client_id = data['client_id']
    input_data = data['input']
    # Forward to client WS
    emit('terminal_output', {'data': input_data, 'from': 'server'}, room=client_id)

@socketio.on('terminal_output')
def handle_terminal_output(data):
    client_id = data['client_id']
    output = data['output']
    # Forward to admin WS (room=client_id, but since admin joined, emit to room)
    emit('terminal_input', {'data': output}, room=client_id)

@socketio.on('execute_result')
def handle_execute_result(data):
    client_id = data['client_id']
    result = data['result']
    # Можно emit to admin, но для простоты храним в clients[client_id]['last_result']
    if client_id in clients:
        clients[client_id]['last_result'] = result

@socketio.on('disconnect')
def handle_disconnect():
    # Cleanup WS sid if needed
    for cid in list(clients.keys()):
        if clients[cid].get('ws_sid') == request.sid:
            clients[cid]['ws_sid'] = None
            break

if __name__ == '__main__':
    socketio.run(app, debug=True)
else:
    # Для gunicorn
    socketio.init_app(app)