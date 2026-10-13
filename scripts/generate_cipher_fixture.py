"""Generate public synthetic SQLCipher-4-layout samples, never user account data.

One-time generator requires APSW; normal tests use the committed sample files.
Run with APSW on PYTHONPATH. Uses SQLite itself to construct reserve=80 pages.
"""
import hashlib
import hmac
import struct
from pathlib import Path

import apsw
from Crypto.Cipher import AES

output = Path(__file__).resolve().parent.parent / "tests/fixtures/cipher"
output.mkdir(parents=True, exist_ok=True)
raw = hashlib.sha256(b"novus-public-synthetic-test-key-not-an-account-key").digest()
table = "Msg_" + hashlib.md5(b"test@chatroom").hexdigest()
schemas = {
    "contact": [
        "CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,alias TEXT,local_type INTEGER)",
        "INSERT INTO contact VALUES('wxid_self','我','','',0),('test@chatroom','加密样本群','','',0),('wxid_alice','Alice','小林','',0)",
    ],
    "message_10": [
        "CREATE TABLE Name2Id(user_name TEXT)",
        "INSERT INTO Name2Id VALUES('wxid_self'),('test@chatroom'),('wxid_alice')",
        f"CREATE TABLE {table}(local_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,server_id TEXT)",
        f"INSERT INTO {table} VALUES(1,1,1789450000,3,'wxid_alice:\n加密数据库验证记录',0,'public-test-1')",
    ],
}
for name, statements in schemas.items():
    db = apsw.Connection(":memory:")
    db.pragma("page_size", 4096)
    db.reserve_bytes(reserve=80)
    db.execute("VACUUM")
    for sql in statements:
        db.execute(sql)
    plain = db.serialize("main")
    db.close()
    assert plain[20] == 80
    salt = hashlib.sha256(name.encode()).digest()[:16]
    enc_key = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, bytes(x ^ 0x3a for x in salt), 2, 32)
    result = bytearray()
    for offset in range(0, len(plain), 4096):
        number = offset // 4096 + 1
        iv = hashlib.sha256(f"{name}:{number}".encode()).digest()[:16]
        data = plain[offset + (16 if number == 1 else 0):offset + 4016]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv).encrypt(data)
        signature = hmac.new(mac_key, cipher + iv + struct.pack("<I", number), hashlib.sha512).digest()
        result.extend((salt if number == 1 else b"") + cipher + iv + signature)
    (output / (name + ".enc")).write_bytes(result)
    print(name, len(result), hashlib.sha256(result).hexdigest())
