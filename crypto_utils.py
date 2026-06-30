import os
import hashlib
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


_SECRET_STR = os.environ.get("P2P_NETWORK_SECRET", "DEV_FALLBACK_SECRET_CHANGE_ME")
SHARED_KEY  = hashlib.sha256(_SECRET_STR.encode('utf-8')).digest()  # 32-byte AES-256 key

def encrypt_chunk(data: bytes) -> bytes:
    nonce = get_random_bytes(12)
    cipher = AES.new(SHARED_KEY, AES.MODE_GCM, nonce=nonce)
    
    ciphertext, tag = cipher.encrypt_and_digest(data)
    
    return nonce + tag + ciphertext

def decrypt_chunk(data: bytes) -> bytes:
    nonce      = data[:12]
    tag        = data[12:28]
    ciphertext = data[28:]
    
    cipher = AES.new(SHARED_KEY, AES.MODE_GCM, nonce=nonce)
    
    # Decrypt and verify integrity automatically. 
    return cipher.decrypt_and_verify(ciphertext, tag)