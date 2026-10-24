# mc-serverkeeper

家里的 Mac 跑着一个 Minecraft 私服。开着费电，关了自己又懒得回去开，所以写了这个小东西。

它管两件事：没人的时候自动关服；想玩的时候，在网页上点一下按钮就能起来。手机上能点，别人的电脑上也能点，不用 SSH 登录，也不用给路由器开放入站端口。

## 它是怎么跑起来的

```
   玩家 ──▶ 网页上的按钮 ──▶ 云服务器 index.php
                                  ▲      │
                        每 5 秒问一次    │ 下发「启动」
                                  │      ▼
                              Mac 上的管家 ──▶ screen ──▶ Minecraft
```

家里那台机器上跑一个管家进程，每 5 秒去问一次云服务器「有事吗」，收到启动指令就把服务器拉起来。云服务器上只有一个 PHP 文件，记状态、给网页提供数据。

家里主动去问，而不是让云端反向连进来，是因为大多数家用宽带都在 NAT 后面，反过来连得靠打洞或者内网穿透。主动轮询只要机器能上网就行。

## 装

### 1. 服务端打开 RCON

编辑 `server/server.properties`：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=<openssl rand -hex 16 生成一个>
broadcast-rcon-to-ops=true
```

密码单独存文件，别写在脚本里：

```bash
umask 077
printf '%s' '刚才生成的密码' > ~/mc-server/bin/rcon.pass
```

### 2. 起管家

```bash
chmod +x ~/mc-server/server/run.sh
cp mac/agent.py mac/rcon.py ~/mc-server/bin/
chmod +x ~/mc-server/bin/*.py
```

先在前台跑一下看看日志，出现「agent 启动」就说明通了：

```bash
~/mc-server/bin/agent.py
```

顺手确认 RCON 能用：

```bash
python3 ~/mc-server/bin/rcon.py list
```

### 3. 交给 launchd

`mac/com.example.mcagent.plist` 里三处 `/Users/YOURNAME/mc-server` 换成真实路径，Label 换个不冲突的名字，然后：

```bash
cp com.example.mcagent.plist ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl kickstart -k gui/$(id -u)/com.example.mcagent
tail -f ~/mc-server/logs/agent.log
```

`KeepAlive=true` 让它挂了自动重启，`ThrottleInterval=10` 防止它反复崩溃时空转。

想进游戏控制台敲命令的话：`screen -r mc`，出来按 `Ctrl+A` 再按 `D`，这样是 detach，服务器不会停。

### 4. 网页

`cloud/index.php` 丢到网站根目录（宝塔最省事，PHP 7.4 以上就行），改掉第 13 行的 `AGENT_TOKEN`：

```bash
openssl rand -hex 16
```

然后在 Mac 上写 `~/mc-server/cloud.conf`，权限 600：

```json
{"url": "https://mc.example.com", "token": "和 AGENT_TOKEN 一样"}
```

重启管家，日志里出现 `云端=已配置` 就通了。打开网页点按钮，大概一分钟服务器起来。

> 页面会在自己旁边建 `data/state.json`，确认 Web 用户对那个目录有写权限。

## 参数

`agent.py` 都不用改，环境变量能覆盖：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MC_BASE` | `~/mc-server` | 根目录 |
| `MC_SERVER_DIR` | `$MC_BASE/server` | 服务器目录，里面要有 run.sh |
| `MC_WINDOW_SEC` | `1800` | 一个时间窗多长 |
| `MC_IDLE_K` | `3` | 连续几个空窗后关服，默认一共 1.5 小时 |
| `MC_CHECK_INTERVAL` | `60` | 多久查一次在线人数 |
| `MC_REPORT_INTERVAL` | `15` | 多久给云端报一次心跳 |
| `MC_HEALTH_INTERVAL` | `10` | 多久探一次 25565 端口 |

`index.php` 顶上两个常量：`AGENT_TOKEN` 必须改，`OFFLINE_SEC`（默认 45 秒）决定多久没心跳算管家离线。

## 几条规矩

空窗是这么算的：30 分钟一个窗口，窗口里没人登录过、而且窗口结束时正好也没人在线，才算一个空窗。连着攒够 3 个就关服。中间只要有人上线，计数立刻清零，哪怕他只待了一分钟。

关服走的是 RCON 的 `stop`，跟玩家在游戏里打 `/stop` 一样，会正常存盘退出。不用 kill。

真到关服那一步会再查一次人数，万一有人正连进来就取消，计数也一并清零。

服务器停了不会自己再起来，得去网页上点。不然刚关掉又被拉起来，来回折腾。

## 状态

```
starting ──▶ running ──▶ stopping ──▶ stopped_by_idle
                 │
                 └──────────────▶ crashed
```

`stopped_by_idle` 和 `crashed` 都不会自动拉起，等你在网页上点。端口还开着但 RCON 连续 3 次不通，会判成 `crashed`，应付那种进程没退但主循环卡死的情况。

## HTTP 接口

除了给网页用的 `status` / `start`，`index.php` 还有一个 `?action=health`：

- 管家 45 秒内有心跳 → `200`
- 没心跳了 → `503`

它只反映管家活没活着，跟服务器开没开无关。定时关服是正常行为，不该报警。

所以任何能发 HTTP 请求的监控系统都能接：UptimeKuma 建个 HTTP(s) 监控指过来，Zabbix 加个 web 检查，或者干脆 cron 里 curl 一下。

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://mc.example.com/?action=health
```

这样只有机器关机、管家进程死了、或者家里断网，监控才会红。

## 踩过的坑

详细的写在 [docs/design.md](docs/design.md)。最要紧的两条先放这儿：

服务端压缩包解压出来，`run.sh` 经常没有执行权限。这时候 screen 拉起来会瞬间退出，日志里一个字都没有，能查很久。第一件事先 `chmod +x`。

RCON 强制监听 `0.0.0.0`，配置里改不了。机器有公网 IP 的话，记得在防火墙上把 25575 挡掉。

## License

MIT

## 作者

Kit T.（呆猫）
