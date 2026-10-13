# Public synthetic encrypted fixtures

These files contain only a generated contact list and one generated message. They
contain no personal account data or real account credentials.

`scripts/generate_cipher_fixture.py` uses APSW / SQLite to generate 4096-byte pages
with 80 reserved bytes, then encodes the SQLCipher 4 layout with AES-256-CBC,
PBKDF2-HMAC-SHA512 and page HMAC-SHA512. Each database uses a different salt.
The public test key is derived from a labelled constant in the generator.

This validates the application's encrypted-file path with synthetic data; it is
not a substitute for testing a real WeChat account or proving every client schema.

Generation dependency: APSW 3.53.0.0. Normal tests do not require APSW.
APSW reserve-byte API: https://rogerbinns.github.io/apsw/connection.html#apsw.Connection.reserve_bytes
