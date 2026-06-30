import argparse
import logging
import os
import sys
import socket
import threading
import time
import uuid
import webbrowser
from flask import Flask, jsonify, render_template, request
from peer_node import PeerNode
from werkzeug.utils import secure_filename

if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    app = Flask(__name__, template_folder=template_folder)
else:
    app = Flask(__name__)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

parser = argparse.ArgumentParser(description="P2P File Sharing Node")
parser.add_argument("--name",  default=socket.gethostname(), help="Peer name")
parser.add_argument("--tcp",   type=int, default=0, help="TCP port")
parser.add_argument("--flask", type=int, default=0, help="Web UI port")
args = parser.parse_args()

PEER_NAME     = args.name
ACTUAL_LAN_IP = get_lan_ip()
TCP_PORT      = args.tcp if args.tcp != 0 else get_free_port()
FLASK_PORT    = args.flask if args.flask != 0 else get_free_port()
BASE_DIR      = f"peer_{PEER_NAME}"
SHARED_DIR    = os.path.join(BASE_DIR, "shared_files")
RECEIVED_DIR  = os.path.join(BASE_DIR, "received_files")

node = PeerNode(
    peer_name=PEER_NAME, host="0.0.0.0", tcp_port=TCP_PORT,
    shared_dir=SHARED_DIR, received_dir=RECEIVED_DIR,
)
threading.Thread(target=node.start_server, daemon=True).start()
node.start_discovery(flask_port=FLASK_PORT)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

LAST_HEARTBEAT = time.time()

@app.route("/api/heartbeat")
def heartbeat():
    global LAST_HEARTBEAT
    LAST_HEARTBEAT = time.time()
    return jsonify({"status": "ok"})

def check_heartbeat():
    while True:
        time.sleep(2)
        
        if time.time() - LAST_HEARTBEAT > 5:
            print("\n[System] Browser tab closed. Shutting down P2P node...")
            os._exit(0) 

threading.Thread(target=check_heartbeat, daemon=True).start()

def open_browser():
    time.sleep(1.5) 
    webbrowser.open(f"http://127.0.0.1:{FLASK_PORT}")

threading.Thread(target=open_browser, daemon=True).start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/info")
def api_info():
    return jsonify({"name": PEER_NAME, "host": ACTUAL_LAN_IP, "tcp_port": TCP_PORT, "flask_port": FLASK_PORT})

@app.route("/api/peers")
def api_peers():
    now = time.time()
    result = []
    with node._lock:
        stale = [n for n, c in node.known_peers.items() if now - c["last_seen"] > 15]
        for n in stale:
            del node.known_peers[n]
        result = [
            {"name": n, "host": c["host"], "tcp_port": c["tcp_port"], "flask_port": c["flask_port"]}
            for n, c in node.known_peers.items()
        ]
    return jsonify(result)

@app.route("/api/peers/<peer_name>/ping")
def api_peer_ping(peer_name):
    with node._lock:
        cfg = node.known_peers.get(peer_name)
    if not cfg:
        return jsonify({"online": False, "error": "Unknown peer"}), 404
    return jsonify({"online": node.ping_peer(cfg["host"], cfg["tcp_port"])})

@app.route("/api/peers/<peer_name>/files")
def api_peer_files(peer_name):
    with node._lock:
        cfg = node.known_peers.get(peer_name)
    if not cfg:
        return jsonify({"error": "Unknown peer"}), 404
    return jsonify(node.list_peer_files(cfg["host"], cfg["tcp_port"]))

@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").lower()
    if not query:
        return jsonify([])

    results, results_lock = [], threading.Lock()
    def _check(pname, pcfg):
        for f in node.list_peer_files(pcfg["host"], pcfg["tcp_port"]):
            if query in f["name"].lower():
                with results_lock:
                    results.append({"peer": pname, "name": f["name"], "size": f["size"]})

    with node._lock:
        peers_snapshot = list(node.known_peers.items())

    threads = [threading.Thread(target=_check, args=(n, c)) for n, c in peers_snapshot]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return jsonify(results)

@app.route("/api/files/shared")
def api_files_shared():
    return jsonify(node.get_shared_files())

@app.route("/api/files/received")
def api_files_received():
    return jsonify(node.get_received_files())

@app.route("/api/files/upload", methods=["POST"])
def api_files_upload():
    filename     = request.form.get("filename")
    chunk_index  = int(request.form.get("chunk_index", 0))
    total_chunks = int(request.form.get("total_chunks", 1)) 
    file_chunk   = request.files.get("file")

    if not filename or not file_chunk:
        return jsonify({"success": False, "error": "Missing filename or file data"}), 400

    filename   = secure_filename(filename)
    # Write to a temporary .part file so the swarm doesn't see it yet
    temp_path  = os.path.join(SHARED_DIR, filename + ".part")
    final_path = os.path.join(SHARED_DIR, filename)

    try:
        mode = "wb" if chunk_index == 0 else "ab"
        with open(temp_path, mode) as f:
            f.write(file_chunk.read())
            
        if chunk_index == total_chunks - 1:
            if os.path.exists(final_path):
                os.remove(final_path) 
            os.rename(temp_path, final_path)
            
            with node._lock:
                if final_path in node._hash_cache:
                    del node._hash_cache[final_path]

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True})

@app.route("/api/files/delete", methods=["POST"])
def api_files_delete():
    data = request.get_json(force=True)
    folder = data.get("folder")
    filename = data.get("filename")

    if not filename or folder not in ["shared", "received"]:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    safe_name = secure_filename(filename)
    target_dir = SHARED_DIR if folder == "shared" else RECEIVED_DIR
    target_path = os.path.join(target_dir, safe_name)

    try:
        if os.path.exists(target_path):
            os.remove(target_path)
        
            with node._lock:
                if target_path in node._hash_cache:
                    del node._hash_cache[target_path]
                    
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "File not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/transfer/request", methods=["POST"])
def api_transfer_request():
    data         = request.get_json(force=True)
    peer_name    = data.get("peer_name", "").strip()
    raw_filename = data.get("filename",  "").strip()

    if not peer_name or not raw_filename:
        return jsonify({"success": False, "error": "peer_name and filename are required"})

    # Defuse path traversal attempts from the API level
    filename = secure_filename(raw_filename)
    if not filename:
        return jsonify({"success": False, "error": "Invalid filename format."})

    with node._lock:
        if peer_name not in node.known_peers:
            return jsonify({"success": False, "error": f"Unknown peer: {peer_name}"})

    job_id     = str(uuid.uuid4())[:8]
    start_time = time.time()

    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "message": f"Downloading '{filename}'...",
                         "verified": None, "progress": 0, "speed": 0}

    def _progress_cb(received, total):
        elapsed = time.time() - start_time
        speed   = received / elapsed if elapsed > 0 else 0
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["progress"] = int((received / total) * 100)
                _jobs[job_id]["speed"]    = speed

    def _run():
        success, msg, verified = node.request_file(filename, progress_callback=_progress_cb)
        with _jobs_lock:
            _jobs[job_id].update({
                "status":   "success" if success else "error",
                "message":  msg,
                "verified": verified,
                "progress": 100 if success else _jobs[job_id].get("progress", 0),
                "speed":    0,
            })

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/transfer/status/<job_id>")
def api_transfer_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id, {"status": "not_found", "message": "Unknown job ID", "verified": None})
    return jsonify(job)

@app.route("/api/transfer/log")
def api_transfer_log():
    return jsonify(list(reversed(node.transfer_log[-50:])))

if __name__ == "__main__":
    print(f"""
{'='*58}
  Aegis - Secure P2P File Sharing System  |  Muhammad Daniyal Alam  |  Hassan Arif
  {'─'*50}
  Peer        :  {PEER_NAME}
  TCP Socket  :  {ACTUAL_LAN_IP}:{TCP_PORT}
  Web GUI     :  http://127.0.0.1:{FLASK_PORT}
  Shared      :  {SHARED_DIR}/
  Received    :  {RECEIVED_DIR}/
{'='*58}
""")
    app.run(host="127.0.0.1", port=FLASK_PORT, debug=False, threaded=True)
