#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MC 服务器常驻管家：空闲自动关服 + 云端轮询（一键启动）

云端配置写在 ~/mc-server/cloud.conf:
{"url": "https://你的域名", "token": "和 index.php 里的一致"}
没有这个文件时只做本地监控。
"""
import json
import logging
import os
import re
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request

BASE = os.environ.get('MC_BASE') or os.path.expanduser('~/mc-server')
SERVER_DIR = os.environ.get('MC_SERVER_DIR') or os.path.join(BASE, 'server')
STATE_FILE = os.path.join(BASE, 'state.json')
LOG_FILE = os.path.join(BASE, 'logs', 'agent.log')
PASS_FILE = os.path.join(BASE, 'bin', 'rcon.pass')
CLOUD_CONF = os.path.join(BASE, 'cloud.conf')

RCON_PORT = 25575
WINDOW_SEC = int(os.environ.get('MC_WINDOW_SEC', 30 * 60))
IDLE_K = int(os.environ.get('MC_IDLE_K', 3))
CHECK_INTERVAL = int(os.environ.get('MC_CHECK_INTERVAL', 60))
LOOP_SEC = 5
REPORT_INTERVAL = int(os.environ.get('MC_REPORT_INTERVAL', 15))
HEALTH_INTERVAL = int(os.environ.get('MC_HEALTH_INTERVAL', 10))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()],
)
log = logging.getLogger('mcagent')


def pack(rid, typ, payload):
    data = struct.pack('<ii', rid, typ) + payload.encode('utf-8') + b'\x00\x00'
    return struct.pack('<i', len(data)) + data


def recv_pkt(s):
    raw = s.recv(4)
    if len(raw) < 4:
        return None
    (ln,) = struct.unpack('<i', raw)
    buf = b''
    while len(buf) < ln:
        chunk = s.recv(ln - len(buf))
        if not chunk:
            break
        buf += chunk
    rid, typ = struct.unpack('<ii', buf[:8])
    return rid, typ, buf[8:-2].decode('utf-8', 'replace')


def rcon(cmd, timeout=8):
    try:
        s = socket.create_connection(('127.0.0.1', RCON_PORT), timeout=timeout)
        pw = open(PASS_FILE, encoding='utf-8').read().strip()
        s.sendall(pack(1, 3, pw))
        r = recv_pkt(s)
        if not r or r[0] == -1:
            s.close()
            return None
        s.sendall(pack(2, 2, cmd))
        r = recv_pkt(s)
        s.close()
        return r[2] if r else None
    except Exception:
        return None


def online_count():
    raw = rcon('list')
    if raw is None:
        return None
    m = re.search(r'There are (\d+) of a max', raw)
    return int(m.group(1)) if m else None


def port_open():
    try:
        s = socket.create_connection(('127.0.0.1', 25565), timeout=3)
        s.close()
        return True
    except Exception:
        return False


def start_mc():
    if port_open():
        log.info('启动跳过：25565 已在监听')
        return False
    cmd = ['screen', '-DmS', 'mc', './run.sh', 'nogui']
    env = dict(os.environ, TERM='xterm-256color')
    subprocess.Popen(cmd, cwd=SERVER_DIR, env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    log.info('已发出启动命令（screen 会话 mc）')
    return True


def save_state(st):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_cloud():
    try:
        with open(CLOUD_CONF, encoding='utf-8') as f:
            d = json.load(f)
        if d.get('url') and d.get('token'):
            return d
    except Exception:
        pass
    return None


class Agent:
    def __init__(self):
        self.st = load_state()
        self.status = self.st.get('status', 'unknown')
        self.idle_windows = self.st.get('idle_windows', 0)
        self.players = 0
        self.window_start = time.time()
        self.window_had_player = False
        self.fail_count = 0
        self.dead_count = 0
        self.shutting_down = False
        self.cloud = load_cloud()

        if not self.st:
            self.status = 'starting'
            start_mc()
        elif self.status in ('stopped_by_idle', 'crashed'):
            log.info('上次状态 %s，不自动拉起（等云端指令或人工处理）', self.status)
        elif not port_open():
            self.status = 'starting'
            start_mc()
        self.flush()

    def flush(self):
        self.st.update({
            'status': self.status,
            'players': self.players,
            'idle_windows': self.idle_windows,
            'idle_k': IDLE_K,
            'window_sec': WINDOW_SEC,
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'rcon_port': RCON_PORT,
        })
        save_state(self.st)

    def health_check(self):
        """每 10 秒看一次端口，连续 2 次不通就认定服务器没了（20 秒内反映到页面）"""
        if self.status != 'running':
            self.dead_count = 0
            return
        if port_open():
            self.dead_count = 0
            return
        self.dead_count += 1
        if self.dead_count >= 2:
            log.warning('端口 25565 连续 %d 次不通，判定服务器已停止', self.dead_count)
            self.status = 'crashed'
            self.players = 0
            self.dead_count = 0
            self.flush()

    def tick(self):
        n = online_count()
        if n is None:
            self.fail_count += 1
            if self.fail_count >= 3 and self.status in ('running', 'starting'):
                log.warning('连续 %d 次 RCON 不可达，判定服务器异常', self.fail_count)
                self.status = 'crashed'
                self.flush()
            return
        self.fail_count = 0
        self.players = n
        if not port_open():
            return  # 25565 未监听（正在关闭中），别急着标 running
        if self.status != 'running':
            log.info('服务器已就绪，状态 running（在线 %d）', n)
            self.status = 'running'
            self.flush()

        if n > 0:
            self.window_had_player = True

        now = time.time()
        if now - self.window_start >= WINDOW_SEC:
            if not self.window_had_player and n == 0:
                self.idle_windows += 1
                log.info('空闲窗口 +1 → %d/%d（无人登录且末次无人在线）', self.idle_windows, IDLE_K)
            else:
                if self.idle_windows:
                    log.info('窗口内有人，空闲计数清零（原 %d）', self.idle_windows)
                self.idle_windows = 0
            self.window_start = now
            self.window_had_player = False
            self.flush()

        if self.idle_windows >= IDLE_K and not self.shutting_down:
            self.shutdown_sequence()

    def shutdown_sequence(self):
        self.shutting_down = True
        self.status = 'stopping'
        self.flush()
        log.info('空闲达到 %d 个窗口，开始关服', IDLE_K)
        n = online_count()
        if n and n > 0:
            log.info('关服取消：%d 名玩家在线', n)
            self.shutting_down = False
            self.idle_windows = 0
            self.window_start = time.time()
            self.status = 'running'
            self.flush()
            return
        rcon('stop')
        for _ in range(30):
            if not port_open():
                break
            time.sleep(2)
        self.status = 'stopped_by_idle'
        self.idle_windows = 0
        self.players = 0
        self.window_had_player = False
        self.window_start = time.time()
        self.shutting_down = False
        self.flush()
        log.info('服务器已关闭（空闲自动）')

    def cloud_poll(self):
        if not self.cloud:
            return
        url = '%s/?action=poll&token=%s' % (
            self.cloud['url'].rstrip('/'), urllib.parse.quote(self.cloud['token']))
        try:
            with urllib.request.urlopen(url, timeout=6) as r:
                d = json.loads(r.read().decode('utf-8'))
        except Exception as e:
            log.debug('poll 失败: %s', e)
            return
        if d.get('cmd') == 'start':
            log.info('收到云端启动指令')
            if port_open():
                log.info('已在运行，忽略启动指令')
                if self.status != 'running':
                    self.status = 'running'
                    self.flush()
            else:
                self.status = 'starting'
                self.idle_windows = 0
                self.window_start = time.time()
                self.window_had_player = False
                self.flush()
                start_mc()
                self.cloud_report()

    def cloud_report(self):
        if not self.cloud:
            return
        url = '%s/?action=report&token=%s' % (
            self.cloud['url'].rstrip('/'), urllib.parse.quote(self.cloud['token']))
        payload = json.dumps({
            'status': self.status,
            'players': self.players,
            'idle_windows': self.idle_windows,
            'idle_k': IDLE_K,
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                r.read()
        except Exception as e:
            log.debug('report 失败: %s', e)

    def run(self):
        log.info('agent 启动，状态=%s 空闲=%d/%d 窗口=%ds 云端=%s',
                 self.status, self.idle_windows, IDLE_K, WINDOW_SEC,
                 '已配置' if self.cloud else '未配置')
        last_sample = 0
        last_report = 0
        last_health = 0
        while True:
            try:
                self.cloud_poll()
                now = time.time()
                if now - last_sample >= CHECK_INTERVAL:
                    self.tick()
                    last_sample = now
                if now - last_health >= HEALTH_INTERVAL:
                    self.health_check()
                    last_health = now
                if now - last_report >= REPORT_INTERVAL:
                    self.cloud_report()
                    last_report = now
            except Exception as e:
                log.exception('loop 异常: %s', e)
            time.sleep(LOOP_SEC)


if __name__ == '__main__':
    Agent().run()
