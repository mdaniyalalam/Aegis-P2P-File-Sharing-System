import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time
import hmac
from crypto_utils import decrypt_chunk, encrypt_chunk, SHARED_KEY

CHUNK_SIZE     = 4096          # 4 KB plaintext per chunk
DISCOVERY_PORT = 54321
WORK_CHUNK     = 5 * 1024 * 1024  # 5 MB work orders for swarm

class PeerNode:
    def __init__(self, peer_name, host, tcp_port, shared_dir, received_dir):
        self.peer_name    = peer_name
        self.host         = host
        self.tcp_port     = tcp_port
        self.shared_dir   = shared_dir
        self.received_dir = received_dir
        self.transfer_log = []
        self.known_peers  = {}
        self._lock        = threading.Lock()
        self._hash_cache  = {}   # {path: (mtime, sha256_hex)}
        os.makedirs(shared_dir,   exist_ok=True)
        os.makedirs(received_dir, exist_ok=True)

    # ── Discovery ────────────────────────────────────────────────────────
    def start_discovery(self, flask_port, discovery_port=DISCOVERY_PORT):
        self.flask_port = flask_port
        threading.Thread(target=self._broadcast, args=(discovery_port,), daemon=True).start()
        threading.Thread(target=self._listen,    args=(discovery_port,), daemon=True).start()
        print(f"[{self.peer_name}] Discovery active on UDP {discovery_port}")

    def _broadcast(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        msg_dict = {
            "name": self.peer_name,
            "tcp_port": self.tcp_port,
            "flask_port": self.flask_port,
        }
        msg_str = json.dumps(msg_dict)
        
        signature = hmac.new(SHARED_KEY, msg_str.encode(), hashlib.sha256).hexdigest()
        payload = json.dumps({"payload": msg_dict, "sig": signature}).encode()

        while True:
            try:
                s.sendto(payload, ("<broadcast>", port))
            except Exception:
                pass
            time.sleep(5)

    def _listen(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        while True:
            try:
                data, addr = s.recvfrom(2048)
                envelope = json.loads(data.decode())
                
                payload_dict = envelope.get("payload")
                sig = envelope.get("sig")
                
                if not payload_dict or not sig:
                    continue

                # Verify the signature
                msg_str = json.dumps(payload_dict)
                expected_sig = hmac.new(SHARED_KEY, msg_str.encode(), hashlib.sha256).hexdigest()
                
                if not hmac.compare_digest(sig, expected_sig):
                    print(f"[{self.peer_name}] Dropped invalid broadcast signature from {addr[0]}")
                    continue

                name = payload_dict.get("name")
                if name and name != self.peer_name:
                    with self._lock:
                        self.known_peers[name] = {
                            "host":       addr[0],
                            "tcp_port":   payload_dict["tcp_port"],
                            "flask_port": payload_dict["flask_port"],
                            "last_seen":  time.time(),
                        }
            except Exception:
                pass

    def start_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.tcp_port))
        srv.listen(10)
        print(f"[{self.peer_name}] TCP server ready -> {self.host}:{self.tcp_port}")
        while True:
            try:
                conn, addr = srv.accept()
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
            except Exception as e:
                print(f"[{self.peer_name}] Accept error: {e}")

    def _handle_client(self, conn, addr):
        try:
            conn.settimeout(30)
            cmd = self._recv_line(conn)
            if not cmd:
                return
            if cmd == "PING":
                conn.sendall(b"PONG\n")
            elif cmd == "LIST":
                self._serve_list(conn)
            elif cmd.startswith("GET_CHUNK "):
                parts = cmd.split(" ", 3)
                self._serve_chunk(conn, addr, parts[3].strip(), int(parts[1]), int(parts[2]))
            elif cmd.startswith("GET "):
                self._serve_chunk(conn, addr, cmd[4:].strip(), 0, None)
            else:
                conn.sendall(b"ERROR Unknown command\n")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _serve_list(self, conn):
        files = []
        for name in sorted(os.listdir(self.shared_dir)):
            path = os.path.join(self.shared_dir, name)
            if os.path.isfile(path):
                files.append({
                    "name":   name,
                    "size":   os.path.getsize(path),
                    "sha256": self._hash_file(path),  
                })
        payload = json.dumps(files).encode()
        conn.sendall(struct.pack("!I", len(payload)) + payload)

    def _serve_chunk(self, conn, addr, filename, start, end):
        filename = os.path.basename(filename)
        filepath = os.path.join(self.shared_dir, filename)

        if not os.path.isfile(filepath):
            conn.sendall(b"ERROR File not found\n")
            return

        file_size = os.path.getsize(filepath)
        if end is None or end > file_size:
            end = file_size

        span = end - start
        conn.sendall(f"OK {span}\n".encode())

        if self._recv_line(conn) != "ACK":
            return

        with open(filepath, "rb") as fh:
            fh.seek(start)
            sent = 0
            while sent < span:
                chunk = fh.read(min(CHUNK_SIZE, span - sent))
                if not chunk:
                    break
                enc = encrypt_chunk(chunk)
                conn.sendall(struct.pack("!I", len(enc)) + enc)
                sent += len(chunk)

        conn.sendall(struct.pack("!I", 0))  # EOF marker

        # Only log full-file transfers 
        if start == 0 and end == file_size:
            with self._lock:
                self.transfer_log.append({
                    "type": "sent", "file": filename, "peer": addr[0],
                    "size": file_size, "verified": True,
                    "time": time.strftime("%H:%M:%S"),
                })

    
    def ping_peer(self, host, tcp_port, timeout=2.0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, tcp_port))
            s.sendall(b"PING\n")
            ok = self._recv_line(s) == "PONG"
            s.close()
            return ok
        except Exception:
            return False

    def list_peer_files(self, host, tcp_port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(30)
            s.connect((host, tcp_port))
            s.sendall(b"LIST\n")
            raw = self._recv_exact(s, 4)
            if not raw:
                s.close()
                return []
            
            length = struct.unpack("!I", raw)[0]
            if length > 5 * 1024 * 1024:
                print(f"[{self.peer_name}] list_peer_files blocked massive payload: {length} bytes")
                s.close()
                return []

            payload = self._recv_exact(s, length)
            s.close()
            return json.loads(payload.decode()) if payload else []
        except Exception as e:
            print(f"[{self.peer_name}] list_peer_files: {e}")
            return []

    def request_file(self, filename, progress_callback=None):
        available     = []
        expected_hash = None
        file_size     = 0

        with self._lock:
            peers_snapshot = list(self.known_peers.items())

        for _, cfg in peers_snapshot:
            for f in self.list_peer_files(cfg["host"], cfg["tcp_port"]):
                if f["name"] == filename:
                    available.append(cfg)
                    if expected_hash is None:
                        expected_hash = f.get("sha256")   # hash from the peer's LIST
                        file_size     = f["size"]
                    break

        if not available:
            return False, "File not found on any active peer.", False

        safe_filename = os.path.basename(filename)
        save_path = os.path.join(self.received_dir, safe_filename)
        
        # ensure the final path is strictly inside the received_dir
        if not os.path.abspath(save_path).startswith(os.path.abspath(self.received_dir)):
            return False, "Security violation: Path traversal attempt blocked.", False

        with open(save_path, "wb") as f:
            if file_size > 0:
                f.seek(file_size - 1)
                f.write(b'\0')

        wq = queue.Queue()
        for i in range(0, file_size, WORK_CHUNK):
            wq.put((i, min(i + WORK_CHUNK, file_size)))

        total_received = 0

        def worker(cfg):
            nonlocal total_received
            failures = 0
            while True:
                if failures >= 3:
                    break
                try:
                    start, end = wq.get(timeout=2)
                except queue.Empty:
                    break

                ok = False
                got = 0
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(10)
                    s.connect((cfg["host"], cfg["tcp_port"]))
                    s.sendall(f"GET_CHUNK {start} {end} {filename}\n".encode())
                    hdr = self._recv_line(s)

                    if hdr.startswith("OK"):
                        s.sendall(b"ACK\n")
                        with open(save_path, "r+b") as fh:
                            fh.seek(start)
                            while True:
                                raw = self._recv_exact(s, 4)
                                if not raw:
                                    break
                                ln = struct.unpack("!I", raw)[0]
                                if ln == 0:
                                    break
                                
                                if ln > 8192:
                                    ok = False
                                    break

                                enc = self._recv_exact(s, ln)
                                if not enc:
                                    break
                                dec = decrypt_chunk(enc)
                                fh.write(dec)
                                got += len(dec)
                                with self._lock:
                                    total_received += len(dec)
                                    if progress_callback:
                                        progress_callback(total_received, file_size)
                        if got == (end - start):
                            ok = True
                            failures = 0
                    s.close()
                except Exception:
                    pass

                if not ok:
                    failures += 1
                    with self._lock:
                        total_received -= got
                    wq.put((start, end))

                wq.task_done()

        threads = [threading.Thread(target=worker, args=(c,)) for c in available]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not wq.empty():
            try:
                os.remove(save_path)
            except OSError:
                pass
            return False, "Download failed — all peers disconnected.", False

        computed = self._hash_file(save_path)
        verified = (expected_hash is not None) and (computed == expected_hash)

        with self._lock:
            self.transfer_log.append({
                "type":     "received",
                "file":     filename,
                "peer":     f"{len(available)} peer(s)",
                "size":     file_size,
                "verified": verified,
                "time":     time.strftime("%H:%M:%S"),
            })

        if not verified:
            return True, "Downloaded but SHA-256 mismatch — file may be corrupted!", False
        return True, "Download complete — SHA-256 verified.", True

    def get_shared_files(self):
        return [
            {"name": n, "size": os.path.getsize(os.path.join(self.shared_dir, n))}
            for n in sorted(os.listdir(self.shared_dir))
            if os.path.isfile(os.path.join(self.shared_dir, n))
        ]

    def get_received_files(self):
        return [
            {"name": n, "size": os.path.getsize(os.path.join(self.received_dir, n))}
            for n in sorted(os.listdir(self.received_dir))
            if os.path.isfile(os.path.join(self.received_dir, n))
        ]

    def _hash_file(self, path):
        mtime = os.path.getmtime(path)
        cached = self._hash_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]          # file unchanged, reuse hash
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        result = sha.hexdigest()
        self._hash_cache[path] = (mtime, result)
        return result

    def _recv_line(self, sock, max_len=2048):
        buf = b""
        while len(buf) < max_len:
            b = sock.recv(1)
            if not b or b == b"\n":
                break
            buf += b
        return buf.decode("utf-8", errors="ignore").strip()

    def _recv_exact(self, sock, n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data