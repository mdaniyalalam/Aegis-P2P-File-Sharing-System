# 🛰️ Aegis — Secure P2P File Sharing System

**Internal backend reference:** Aegis  
**Authors:** Muhammad Daniyal Alam, Hassan Arif  
**University:** National University of Computer and Emerging Sciences, Karachi Campus  
**Course:** Computer Networks (CS3001)  
**Instructor:** Sir Shoaib Raza

---

## 📖 Project Overview

Centralized file-sharing services are convenient, but they introduce single points of failure, privacy vulnerabilities, and bandwidth bottlenecks that are fundamentally at odds with the open nature of the Internet.

Aegis is a **fully decentralized, serverless P2P file-sharing application** written in Python and Flask. Every instance of the application acts as both a server and a client simultaneously — there is no central coordinator, no dedicated tracker, and no single point of failure.

By combining:

- **UDP-based peer discovery**
- **Authenticated symmetric encryption**
- **Swarm-style parallel downloading**

…the project demonstrates how the core principles of modern P2P networks (BitTorrent, IPFS, Napster) can be implemented from the socket layer up, with security built in rather than bolted on.

**Objectives:**

- Build a zero-configuration, serverless peer discovery mechanism
- Demonstrate authenticated, chunk-level encrypted file transfer
- Implement multi-source swarm downloading with automatic failover
- Guarantee end-to-end file integrity via cryptographic verification

---

## ⚙️ System Architecture

The project is divided into **two conceptual layers**, demonstrating modular software design and a clean separation between the network engine and the control plane:

### 1️⃣ Networking & Cryptographic Engine (Python)

The core peer engine, responsible for all wire-level behavior:

- UDP broadcast discovery with **HMAC-SHA256 signed** announcements
- Raw TCP socket server for file listing and chunked transfer
- **AES-256-GCM** authenticated encryption applied per chunk
- SHA-256 hashing and integrity verification
- Multi-threaded swarm download orchestration

### 2️⃣ Control Plane (Flask + Web UI)

A REST API layer and single-page dark-themed frontend that exposes the peer engine to the browser:

- Live peer discovery and ping status
- Shared / received file management with chunked upload
- Cross-peer parallel file search
- Real-time download progress and speed indicators
- Reverse-chronological transfer log with verification status

> 📌 The Flask server binds to localhost only and acts purely as a control surface — all actual data transfer happens over raw peer-to-peer TCP sockets.

---

## 🚀 Getting Started

### Installation

```bash
git clone https://github.com/<your-username>/aegis-p2p-file-sharing.git
cd aegis-p2p-file-sharing
pip install -r requirements.txt
```

### Running a Peer

```bash
python app.py --name PeerA
```

This launches the peer, opens the web UI in your default browser, and starts broadcasting on the LAN. To run a second peer on the same machine for local testing, open another terminal and give it a distinct name and ports:

```bash
python app.py --name PeerB --tcp 6002 --flask 5002
```

| Argument | Description | Default |
|----------|-------------|---------|
| `--name` | Peer identifier shown to other nodes | system hostname |
| `--tcp` | TCP port for the file-transfer server | auto-assigned free port |
| `--flask` | Port for the local web UI | auto-assigned free port |

Once two or more peers are running on the same LAN (or the same machine), they'll discover each other automatically — no manual IP configuration required.

### Configuration

Aegis derives its AES-256 key and HMAC signing key from a shared secret. **Set this before running any peers**, otherwise all peers fall back to a publicly known development value and the encryption/authentication provides no real protection:

```bash
export P2P_NETWORK_SECRET="your-own-secret-passphrase"   # macOS/Linux
set P2P_NETWORK_SECRET=your-own-secret-passphrase         # Windows (cmd)
```

Every peer that should be able to talk to each other must be started with the **same** secret.

---

## 🔍 Core Concepts Demonstrated

### 🔐 Authenticated Encryption

- **AES-256-GCM** for per-chunk encryption
- Random 12-byte nonce per chunk with a built-in 16-byte authentication tag
- Tampered or corrupted chunks fail decryption instead of being silently accepted

### 🧾 Data Integrity

- SHA-256 hashing of complete files
- Hash announced by the seeding peer is verified against the locally computed hash after every download

### 📡 Secure Discovery

- UDP broadcast discovery on a fixed port, fully zero-configuration
- Every broadcast is **HMAC-SHA256 signed**; peers drop announcements with an invalid signature instead of trusting them blindly
- Stale peers are automatically evicted from the registry

### 🌐 Swarm Downloading

- Files are split into 5 MB work orders distributed across a thread-safe queue
- One worker thread per available peer, with automatic re-queueing of failed chunks
- Download throughput scales with the number of seeding peers

### 🛡️ Network Hardening

- Filenames sanitized and resolved-path checks prevent path traversal on both upload and download
- List payloads and chunk sizes are capped to prevent memory-exhaustion from a misbehaving peer
- Browser heartbeat keeps the node's lifecycle tied to the UI tab, shutting the node down automatically when the tab is closed

---

## 🔄 Execution Flow

The simulation models the full lifecycle of a peer-to-peer transfer:

### 1️⃣ Discovery Phase

- Peer broadcasts its identity (name, TCP port, Flask port) every 5 seconds, signed with HMAC
- Listening peers verify the signature and update their known-peers registry
- Stale entries (not seen in 15 seconds) are evicted automatically

### 2️⃣ Listing Phase

- Requesting peer queries each known peer's `LIST` command over TCP
- Each peer responds with its shared files, sizes, and SHA-256 hashes

### 3️⃣ Swarm Download Phase

- File is split into 5 MB work orders across a shared queue
- Worker threads (one per peer) pull chunks concurrently and encrypt/decrypt them with AES-256-GCM
- Failed chunks are automatically re-queued to a different peer

### 4️⃣ Verification Phase

- Once all chunks are received, the SHA-256 hash of the assembled file is computed
- Hash is compared against the value announced by the seeding peer
- Transfer is marked **VERIFIED** or flagged as a mismatch in the transfer log

### 5️⃣ Control Plane Phase

- Flask UI reflects live peer status, transfer progress, and verification results in real time
- All operations (upload, delete, search, transfer) are exposed via a JSON REST API

---

**File Descriptions:**

- `app.py` – Flask web server and REST API layer. Exposes peer operations as JSON endpoints with async job tracking for downloads.
- `peer_node.py` – Core networking engine. Manages UDP discovery, TCP server, swarm download orchestration, encryption, hashing, and the transfer log.
- `crypto_utils.py` – Cryptographic utilities. Derives the AES-256 key from a shared secret and provides `encrypt_chunk()` / `decrypt_chunk()` using AES-256-GCM.
- `templates/index.html` – Single-page dark-themed web UI communicating with the Flask backend via REST calls.

---

## 🧪 Educational Objectives

This project was developed to:

- Apply TCP/UDP socket programming at the application layer
- Demonstrate authenticated encryption in a real network protocol
- Implement concurrent, fault-tolerant swarm downloading
- Emphasize security-by-design over security-as-an-afterthought
- Showcase modular separation between networking, cryptography, and UI layers

---

## 🏫 Academic Context

Developed as part of the Computer Networks (CS3001) course requirement.

This project demonstrates applied knowledge of:

- TCP/IP Socket Programming
- UDP Broadcast & Service Discovery
- Applied Cryptography
- Concurrent / Threaded Systems Design
- Network Security & Hardening

---

## 📜 License

**MIT License** – Use strictly for educational and research purposes.
