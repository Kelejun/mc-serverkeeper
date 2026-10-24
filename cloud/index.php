<?php
declare(strict_types=1);

/* ===========================================================
 * MC 服务器一键启动页（单文件）
 * 部署：宝塔 → 网站 → 添加站点 → 把本文件放进网站根目录
 * 依赖：PHP 7.4+，无需数据库、无需额外扩展
 *
 * 需要改的只有下面两行：
 *   AGENT_TOKEN 和 Mac 上 ~/mc-server/cloud.conf 里的 token 保持一致
 * =========================================================== */

define('AGENT_TOKEN', 'CHANGE_ME_用 openssl rand -hex 16 生成');
define('OFFLINE_SEC', 45);              // Mac 多久没上报算离线
define('DATA_DIR', __DIR__ . '/data');
define('STATE_FILE', DATA_DIR . '/state.json');

/* ---------------- 状态读写 ---------------- */

function ensure_state(): void
{
    if (!is_dir(DATA_DIR)) {
        @mkdir(DATA_DIR, 0775, true);
    }
    if (!file_exists(STATE_FILE)) {
        @file_put_contents(STATE_FILE, json_encode([
            'mc_status'        => 'unknown',
            'players'          => 0,
            'idle_windows'     => 0,
            'idle_k'           => 3,
            'last_report'      => 0,
            'pending_start'    => false,
            'start_requested_at' => 0,
        ], JSON_UNESCAPED_UNICODE));
    }
}

function read_state(): array
{
    ensure_state();
    $fp = @fopen(STATE_FILE, 'c+');
    if (!$fp) {
        return [];
    }
    flock($fp, LOCK_SH);
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $d = json_decode($raw ?: '[]', true);
    return is_array($d) ? $d : [];
}

function write_state(array $d): void
{
    ensure_state();
    $fp = @fopen(STATE_FILE, 'c+');
    if (!$fp) {
        return;
    }
    flock($fp, LOCK_EX);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($d, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
}

function json_out(array $d, int $code = 200): void
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($d, JSON_UNESCAPED_UNICODE);
    exit;
}

function check_token(): void
{
    $t = $_GET['token'] ?? ($_POST['token'] ?? '');
    if (!hash_equals(AGENT_TOKEN, (string)$t)) {
        json_out(['ok' => false, 'error' => 'bad token'], 403);
    }
}

/* ---------------- 路由 ---------------- */

$action = $_GET['action'] ?? '';

// Mac 端轮询：是否有启动请求
if ($action === 'poll') {
    check_token();
    $s = read_state();
    $cmd = 'none';
    if (!empty($s['pending_start'])) {
        $cmd = 'start';
        $s['pending_start'] = false;
        $s['start_requested_at'] = time();
        write_state($s);
    }
    json_out(['ok' => true, 'cmd' => $cmd, 'server_time' => time()]);
}

// Mac 端上报状态
if ($action === 'report') {
    check_token();
    $body = json_decode((string)file_get_contents('php://input'), true);
    if (!is_array($body)) {
        $body = [];
    }
    $s = read_state();
    $s['mc_status']    = (string)($body['status'] ?? $s['mc_status'] ?? 'unknown');
    $s['players']      = (int)($body['players'] ?? 0);
    $s['idle_windows'] = (int)($body['idle_windows'] ?? 0);
    $s['idle_k']       = (int)($body['idle_k'] ?? 3);
    $s['last_report']  = time();
    write_state($s);
    json_out(['ok' => true]);
}

// 健康检查：给 UptimeKuma 用。管家程序活着就 200，掉了才 503
// 注意这只反映「管家在线」，不反映 MC 开没开——定时关服时依然是 200
if ($action === 'health') {
    $s   = read_state();
    $age = time() - (int)($s['last_report'] ?? 0);
    $up  = $age <= OFFLINE_SEC;
    json_out([
        'ok'        => $up,
        'mc_status' => $up ? (string)($s['mc_status'] ?? 'unknown') : 'mac_offline',
        'players'   => (int)($s['players'] ?? 0),
        'age'       => $age,
    ], $up ? 200 : 503);
}

// 用户点按钮
if ($action === 'start') {
    $s = read_state();
    $macOnline = (time() - (int)($s['last_report'] ?? 0)) <= OFFLINE_SEC;
    $st = (string)($s['mc_status'] ?? 'unknown');
    $already = in_array($st, ['running', 'starting', 'stopping'], true);
    if ($macOnline && !$already) {
        $s['pending_start'] = true;
        $s['start_requested_at'] = time();
        write_state($s);
    }
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?'), true, 303);
    exit;
}

// 前端轮询状态
if ($action === 'status') {
    $s = read_state();
    $now = time();
    $online = ($now - (int)($s['last_report'] ?? 0)) <= OFFLINE_SEC;
    $st = $online ? (string)($s['mc_status'] ?? 'unknown') : 'mac_offline';
    json_out([
        'ok'          => true,
        'state'       => $st,
        'mac_online'  => $online,
        'players'     => (int)($s['players'] ?? 0),
        'idle_windows' => (int)($s['idle_windows'] ?? 0),
        'idle_k'      => (int)($s['idle_k'] ?? 3),
        'pending'     => !empty($s['pending_start']),
        'last_report' => (int)($s['last_report'] ?? 0),
        'now'         => $now,
    ]);
}

/* ---------------- 页面 ---------------- */

$s0        = read_state();
$macOnline0 = (time() - (int)($s0['last_report'] ?? 0)) <= OFFLINE_SEC;
$st0       = $macOnline0 ? (string)($s0['mc_status'] ?? 'unknown') : 'mac_offline';
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Minecraft 服务器</title>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: radial-gradient(circle at 50% 20%, #23324a 0%, #10151f 70%);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #e8edf6; padding: 24px;
  }
  .card {
    width: 100%; max-width: 380px; background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.08); border-radius: 24px;
    padding: 32px 24px 28px; text-align: center;
    box-shadow: 0 20px 60px rgba(0,0,0,.45);
    backdrop-filter: blur(12px);
  }
  .dot { width: 14px; height: 14px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: -1px; }
  .dot.on   { background: #3ddc84; box-shadow: 0 0 12px #3ddc84; }
  .dot.off  { background: #556074; }
  .dot.wait { background: #f5c542; box-shadow: 0 0 12px #f5c542; animation: pulse 1.2s infinite; }
  @keyframes pulse { 50% { opacity: .35; } }
  .status { font-size: 15px; color: #a9b6cc; letter-spacing: .5px; }
  .title { font-size: 28px; font-weight: 700; margin: 10px 0 4px; }
  .sub { font-size: 13px; color: #7d8aa0; min-height: 18px; }
  button {
    margin-top: 28px; width: 100%; padding: 16px; border: 0; border-radius: 16px;
    font-size: 17px; font-weight: 600; color: #fff; cursor: pointer;
    background: linear-gradient(135deg, #2f7bf6, #1a5cd8);
    box-shadow: 0 10px 24px rgba(47,123,246,.35);
    transition: transform .1s, opacity .2s;
  }
  button:active { transform: scale(.98); }
  button:disabled { opacity: .35; box-shadow: none; cursor: default; }
  .hint { margin-top: 16px; font-size: 12px; color: #5d6a7e; }
  .players { margin-top: 18px; font-size: 13px; color: #8b98ad; }
</style>
</head>
<body>
<div class="card">
  <div class="status"><span id="dot" class="dot <?= $st0 === 'running' ? 'on' : ($st0 === 'starting' || $st0 === 'stopping' ? 'wait' : 'off') ?>"></span><span id="stateText">读取中…</span></div>
  <div class="title" id="title">请稍后...</div>
  <div class="sub" id="sub"></div>

  <button id="btn" onclick="doStart()">启动服务器</button>
  <div class="players" id="players"></div>
  <div class="hint" id="hint"></div>
</div>

<script>
const TEXT = {
  mac_offline: { s: '服务器不在线', t: '服务器没开机', sub: '管家程序没有响应，去看看那台机器' },
  running:     { s: '运行中',     t: '服务器在线', sub: '' },
  starting:    { s: '启动中',     t: '正在启动…', sub: '大概要一分钟' },
  stopping:    { s: '正在关闭',   t: '正在保存世界…', sub: '' },
  offline:     { s: '已关闭',     t: '服务器已关闭', sub: '点下面按钮开机' },
};

let busy = false;
let startAt = 0;

async function refresh() {
  try {
    const r = await fetch('?action=status&t=' + Date.now(), { cache: 'no-store' });
    const d = await r.json();
    render(d);
  } catch (e) {
    render({ state: 'mac_offline' });
  }
}

function render(d) {
  let st = d.state === 'mac_offline' ? 'mac_offline'
          : (d.state === 'running' ? 'running'
          : (d.state === 'starting' ? 'starting'
          : (d.state === 'stopping' ? 'stopping' : 'offline')));

  // 请求被 agent 取走后别立刻退回「已关闭」：一直显示启动中，直到真的 running 或超时
  if (st === 'running') startAt = 0;
  if (startAt && st !== 'running' && st !== 'stopping' && Date.now() - startAt < 120000) st = 'starting';
  const info = TEXT[st];
  const dot = document.getElementById('dot');
  dot.className = 'dot ' + (st === 'running' ? 'on' : (st === 'starting' || st === 'stopping' ? 'wait' : 'off'));
  document.getElementById('stateText').textContent = info.s;
  document.getElementById('title').textContent = info.t;
  document.getElementById('sub').textContent = info.sub || '';

  const btn = document.getElementById('btn');
  const canStart = (st === 'offline') && !d.pending;
  btn.disabled = !canStart || busy;
  btn.textContent = d.pending ? '启动请求已发出…' : (st === 'running' ? '服务器已在运行' : (st === 'starting' ? '启动中…' : '启动服务器'));

  document.getElementById('players').textContent =
    (st === 'running' && d.players > 0) ? ('现在 ' + d.players + ' 人在玩') : '';
  document.getElementById('hint').textContent =
    d.idle_k > 0 ? ('无人 ' + (d.idle_k * 30) + ' 分钟会自动关机（已累计 ' + (d.idle_windows || 0) + '/' + d.idle_k + '个30分钟）') : '';
}

async function doStart() {
  busy = true;
  startAt = Date.now();
  const btn = document.getElementById('btn');
  btn.disabled = true;
  btn.textContent = '请求中…';
  try {
    await fetch('?action=start', { method: 'POST' });
  } catch (e) {}
  setTimeout(() => { busy = false; refresh(); }, 800);
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
