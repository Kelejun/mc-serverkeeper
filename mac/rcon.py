#!/usr/bin/env python3
"""极简 Minecraft RCON 客户端。用法: rcon.py <command>"""
import socket, struct, sys, os

def pack(rid, typ, payload):
    data = struct.pack('<ii', rid, typ) + payload.encode('utf-8') + b'\x00\x00'
    return struct.pack('<i', len(data)) + data

def recv_pkt(s):
    raw = s.recv(4)
    if len(raw) < 4: return None
    (ln,) = struct.unpack('<i', raw)
    buf = b''
    while len(buf) < ln:
        chunk = s.recv(ln - len(buf))
        if not chunk: break
        buf += chunk
    rid, typ = struct.unpack('<ii', buf[:8])
    return rid, typ, buf[8:-2].decode('utf-8', 'replace')

def main():
    host = os.environ.get('RCON_HOST', '127.0.0.1')
    port = int(os.environ.get('RCON_PORT', '25575'))
    base = os.environ.get('MC_BASE') or os.path.expanduser('~/mc-server')
    pw = os.environ.get('RCON_PASS') or open(os.path.join(base, 'bin', 'rcon.pass')).read().strip()
    cmd = ' '.join(sys.argv[1:]) or 'list'
    s = socket.create_connection((host, port), timeout=5)
    s.sendall(pack(1, 3, pw))
    r = recv_pkt(s)
    if not r or r[0] == -1:
        print('AUTH_FAILED'); sys.exit(2)
    s.sendall(pack(2, 2, cmd))
    r = recv_pkt(s)
    print(r[2] if r else 'NO_RESPONSE')
    s.close()

if __name__ == '__main__':
    main()
