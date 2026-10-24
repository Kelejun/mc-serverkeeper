# mc-idle-keeper

Minecraft Java 服务器「闲置自动关服 + 网页一键开机」小工具。

A tiny self-hosted kit for auto-suspending an idle Minecraft server and waking it up from a web page — no SSH, no public inbound port, no extra runtime on the cloud side.

---

## 它是干什么的

私服一般就跑在一台家用机器上，24 小时开着太费电，关了又得本人回去开。

这套东西解决两件事：

1. **没人就自己关。** 管家程序按固定长度的时间窗统计在线人数，连续若干个窗口都没人登录、且窗口结束时也没人在线，就向服务器发 `stop`。
2. **想玩点一下就开。** 网页上只有一个按钮。点下去，家里的机器收到指令把服务器拉起来，页面自动变成「运行中」。

手机、别人的电脑、任何浏览器都能点，不需要你回家，也不需要开路由器的入站端口。

## 架构

```
                  ┌──────────────────────────────┐
   玩家 ──────────▶│  云服务器  index.php         │
                  │  ?action=start   ← 按钮      │
                  │  ?action=poll    → 下发指令  │
                  │  ?action=report  ← 状态上报  │
                  │  ?action=health  → Kuma 探活 │
                  └──────────▲───────────────────┘
                             │  Mac 每 5 秒主动来问「有事吗」
                             │  （不开入站端口，NAT 后面也能用）
                  ┌──────────┴───────────────────┐
                  │  Mac  agent.py               │
                  │   · 每 60 秒 RCON 查在线人数 │
                  │   · 连续 K 个空闲窗口 → stop │
                  │   · 收到指令 → screen 拉起   │
                  └──────────┬───────────────────┘
                             ▼
                  screen -DmS mc ./run.sh nogui
                             │
                        Minecraft 服务器
```

为什么是轮询而不是 webhook：家庭网络在 NAT 后面，云端主动连进屋需要内网穿透或端口映射，而轮询只要家里能上网就行。

## 组件

| 路径 | 作用 |
| --- | --- |
| `mac/agent.py` | 管家本体。空闲判定、状态机、和云端通信 |
| `mac/rcon.py` | 极简 RCON 客户端，用来 `list` / `stop` / `say` |
| `mac/boot.sh` | 幂等启动：已在监听就不重复起，顺带清理残留 screen |
| `mac/com.example.mcagent.plist` | launchd 守护，开机自启 + 挂了自动重启 |
| `cloud/index.php` | 单文件站点：启动按钮 + 状态 API + 探活接口 |
| `docs/design.md` | 参数怎么定的、坑在哪 |

Python 只用标准库，不需要 pip。PHP 只用一个文件，不需要框架和数据库。

## 快速开始

### 0. 前置

- macOS（Linux 也行，把 launchd 换成 systemd 即可）
- `screen`（macOS 自带）
- `python3`（macOS 自带的 3.9 够）
- 云服务器一台，装好 PHP 7.4+（宝塔面板最省事）

目录约定：

```
~/mc-server/
├── server/         # 服务器目录，里面有 run.sh
├── bin/            # agent.py rcon.py rcon.pass
├── logs/
├── cloud.conf      # 可选，没它就只做本地空闲关服
└── state.json      # 管家自己写
```

### 1. 打开 RCON

编辑 `server/server.properties`：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=<openssl rand -hex 16 生成>
broadcast-rcon-to-ops=true
```

**RCON 固定监听 `0.0.0.0`，改不了。** 如果你有公网 IP，务必用防火墙挡掉 25575，只留 127.0.0.1。只靠内网穿透暴露 25565 的话，25575 本来就不通。

密码存到文件里，别写进脚本：

```bash
umask 077
printf '%s' '你的rcon密码' > ~/mc-server/bin/rcon.pass
```

### 2. 跑起来

```bash
chmod +x ~/mc-server/server/run.sh      # 新下载的服务端常常没有执行位，踩过
cp mac/agent.py mac/rcon.py ~/mc-server/bin/
chmod +x ~/mc-server/bin/*.py
~/mc-server/bin/agent.py                # 前台先试跑，看到「agent 启动」再继续
```

确认能查在线人数：

```bash
python3 ~/mc-server/bin/rcon.py list
# There are 0 of a max of 20 players online:
```

### 3. 交给 launchd

把 `mac/com.example.mcagent.plist` 里的 `/Users/YOURNAME/mc-server` 换成真实路径，改个不冲突的 Label，然后：

```bash
cp com.example.mcagent.plist ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl kickstart -k gui/$(id -u)/com.example.mcagent
tail -f ~/mc-server/logs/agent.log
```

`KeepAlive=true` 让它挂了自动重启；`ThrottleInterval=10` 防止崩溃时空转。

想进服务器控制台看看：`screen -r mc`，退出按 `Ctrl+A` 再按 `D`（detach，不会关服）。

### 4. 云端

把 `cloud/index.php` 丢进网站根目录，改掉第 13 行的 `AGENT_TOKEN`：

```bash
openssl rand -hex 16
```

然后在 Mac 上写 `~/mc-server/cloud.conf`（权限 600）：

```json
{"url": "https://mc.example.com", "token": "和 AGENT_TOKEN 一致"}
```

重启管家，日志出现 `云端=已配置` 就成了。打开网页点一下按钮，大约一分钟服务器起来。

> `index.php` 会在自己旁边建 `data/state.json`，确认 Web 用户对该目录有写权限。

### 5. （可选）接 UptimeKuma

别直接监控 25565 —— 定时关服是正常行为，会被判成故障。

`index.php` 有个 `?action=health`：管家 45 秒内有心跳返回 `200`，没心跳返回 `503`，跟服务器开没开无关。

UptimeKuma 里建 **HTTP(s)** 监控打 `https://mc.example.com/?action=health`，间隔 60s、重试 2 次。这样只有机器关机、管家进程死了、或者断网才会红。

## 配置项

`agent.py` 全部可用环境变量覆盖：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MC_BASE` | `~/mc-server` | 根目录 |
| `MC_SERVER_DIR` | `$MC_BASE/server` | 服务器目录（含 run.sh） |
| `MC_WINDOW_SEC` | `1800` | 单个时间窗长度（秒） |
| `MC_IDLE_K` | `3` | 连续多少个空闲窗口后关服 → 默认 1.5 小时 |
| `MC_CHECK_INTERVAL` | `60` | 多久查一次在线人数 |
| `MC_REPORT_INTERVAL` | `15` | 多久向云端报一次心跳 |
| `MC_HEALTH_INTERVAL` | `10` | 多久探一次 25565 端口 |

`index.php` 顶部：

| 常量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_TOKEN` | 必改 | 和 `cloud.conf` 里的 token 一致 |
| `OFFLINE_SEC` | `45` | 多久没心跳算管家离线 |

## 空闲是怎么判定的

口径：**窗口内没有玩家登录过，并且窗口结束时也没人在线**，这个窗口才算「空闲」。

- 窗口中途有人进来，哪怕只待了一分钟，这个窗口直接作废
- 连续累计 `K` 个空闲窗口 → 关服，中间任何一次有人上线都会把计数清零
- 关服前会再 `list` 一次，万一有人正连进来就取消

默认 30 分钟 × 3 = 1.5 小时无人，才会关。

## 状态机

```
starting ──▶ running ──▶ stopping ──▶ stopped_by_idle
                 │
                 └──────────────▶ crashed
```

- `stopped_by_idle` / `crashed` 不会自动拉起，等你在网页上点按钮（避免关掉又被吵醒）
- 端口还在但 RCON 连续 3 次不通 → `crashed`（服务器卡死但没退出的空壳情况）
- 端口连续 2 次（20 秒）不通 → `crashed`，页面 20 秒内就能看到，不用等下一次采样

## 一些设计取舍

- **为什么用 screen 而不是 nohup**：需要随时能进去敲命令，`screen -r mc` 一条命令搞定；nohup 起来就进不去了。
- **为什么查端口而不是只查 RCON**：关服时 25565 先关、25575 晚几百毫秒，只看 RCON 会把已经停掉的服务器判成「运行中」，按钮点不动。
- **为什么心跳 15 秒**：页面 45 秒判离线。上报间隔必须明显小于离线阈值，否则每轮都会闪一下「离线」。
- **为什么关服不发全服广播**：没人的时候广播没人看，多等 60 秒纯属浪费。有人在线本来就不会触发关服。

更多踩坑记录见 [docs/design.md](docs/design.md)。

## License

MIT
