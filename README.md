# mc-serverkeeper

Minecraft 服务器的闲置自动关服与网页一键启动工具。

在一台常开的 Mac 上运行一个管家进程，定时检查服务器有没有人在玩，没人就关掉；需要的时候在网页上点一个按钮，服务器自己起来。不用 SSH 登录，也不用在宽带上开放入站端口。

## 功能特点

- **闲置自动关服**：连续三个 30 分钟的时间窗都没人上线，通过 RCON 正常关闭服务端（存盘退出，不是 kill）。
- **网页一键启动**：浏览器或手机上打开页面点按钮，约一分钟服务器就绪，页面状态自动刷新。
- **不需要入站端口**：由 Mac 主动轮询云服务器，不开端口、不做内网穿透，NAT 后面也能用。
- **状态可见**：页面区分未启动、启动中、运行中、关闭中、管家离线五种状态。
- **通用探活接口**：`?action=health` 返回 200 或 503，只反映管家进程是否存活，任何 HTTP 监控都能接入。

## 工作原理

```
   玩家 ──▶ 网页按钮 ──▶ 云服务器 index.php
                              ▲    │
                    每 5 秒轮询 │    │ 下发启动指令
                              │    ▼
                          Mac 管家 ──▶ screen ──▶ Minecraft 服务端
```

云服务器上只放一个 PHP 文件，记录状态、响应网页请求；Mac 上的管家每 5 秒取一次指令，同时定期上报心跳。

## 目录结构

```
mac/
  agent.py                   管家进程
  rcon.py                    RCON 客户端（Python 标准库实现）
  start.sh                   启动服务端
  boot.sh                    launchd 入口
  com.example.mcagent.plist  launchd 配置模板
  cloud.conf.example         云端配置模板
cloud/
  index.php                  网页与状态存储
docs/
  design.md                  设计说明与踩坑记录
```

## 安装说明

环境要求：macOS，Python 3.9 以上（系统自带），服务端需要支持 RCON（原版与 NeoForge/Forge 都支持）。

### 1. 服务端开启 RCON

编辑服务端目录下的 `server.properties`：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=<用 openssl rand -hex 16 生成一个>
broadcast-rcon-to-ops=true
```

密码单独存文件，不要写进脚本：

```bash
umask 077
printf '%s' '刚才生成的密码' > ~/mc-server/bin/rcon.pass
```

### 2. 部署管家

```bash
mkdir -p ~/mc-server/bin ~/mc-server/logs
cp mac/agent.py mac/rcon.py ~/mc-server/bin/
chmod +x ~/mc-server/bin/*.py
chmod +x ~/mc-server/server/run.sh
```

先在前台运行，确认能连上 RCON：

```bash
python3 ~/mc-server/bin/rcon.py list
~/mc-server/bin/agent.py
```

日志出现 `agent 启动` 即正常，`Ctrl+C` 退出。

### 3. 交给 launchd

把 `mac/com.example.mcagent.plist` 里三处 `/Users/YOURNAME/mc-server` 换成真实路径，Label 改成不冲突的名字，然后：

```bash
cp com.example.mcagent.plist ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.mcagent.plist
launchctl kickstart -k gui/$(id -u)/com.example.mcagent
tail -f ~/mc-server/logs/agent.log
```

`KeepAlive=true` 让进程挂掉后自动重启，`ThrottleInterval=10` 避免反复崩溃时空转。

### 4. 部署网页

把 `cloud/index.php` 放到网站根目录（宝塔面板最省事，PHP 7.4 以上即可），改掉文件顶部的 `AGENT_TOKEN`：

```bash
openssl rand -hex 16
```

然后在 Mac 上创建 `~/mc-server/cloud.conf`，权限设为 600：

```json
{"url": "https://你的域名", "token": "与 AGENT_TOKEN 一致"}
```

重启管家，日志出现 `云端=已配置` 即连通。

页面会在同目录下创建 `data/state.json`，确认 Web 用户对该目录有写权限。

## 使用方法

1. 浏览器打开网页，点「启动服务器」。
2. 页面显示「启动中」，约一分钟后变为「运行中」。
3. 需要进服务器控制台执行命令时，在 Mac 上 `screen -r mc`，按 `Ctrl+A` 再按 `D` 分离，服务端不会停止。

## 配置参数

`agent.py` 无需修改，环境变量可以覆盖：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MC_BASE` | `~/mc-server` | 根目录 |
| `MC_SERVER_DIR` | `$MC_BASE/server` | 服务端目录，需包含 `run.sh` |
| `MC_WINDOW_SEC` | `1800` | 单个时间窗长度 |
| `MC_IDLE_K` | `3` | 连续几个空窗后关服，默认合计 1.5 小时 |
| `MC_CHECK_INTERVAL` | `60` | 检查在线人数的间隔 |
| `MC_REPORT_INTERVAL` | `15` | 向云端上报心跳的间隔 |
| `MC_HEALTH_INTERVAL` | `10` | 探测 25565 端口的间隔 |

`index.php` 顶部两个常量：`AGENT_TOKEN` 必须修改；`OFFLINE_SEC` 默认 45 秒，决定多久没心跳判定管家离线。

## 关服规则

时间窗按 30 分钟划分。一个窗口内没有人登录过，且窗口结束时也没有人在线，才算一个空窗。连续累计三个空窗后关服。中间只要有人上线，计数立刻清零，即使只停留一分钟。

关服通过 RCON 发送 `stop`，与玩家在游戏里执行 `/stop` 效果相同，会正常存盘退出。

执行关服前会再查一次在线人数，若此时有人连接则取消，计数一并清零。

服务器关闭后不会自动重新启动，需要在网页上手动点击，避免刚关掉又被拉起。

## 状态说明

```
starting ──▶ running ──▶ stopping ──▶ stopped_by_idle
                 │
                 └──────────────▶ crashed
```

`stopped_by_idle` 与 `crashed` 都不会自动拉起，等待网页上的操作。若端口仍在监听但 RCON 连续三次无响应，判定为 `crashed`，对应进程未退出但主循环卡死的情况。

## HTTP 接口

除网页使用的 `status` 与 `start` 外，`index.php` 提供 `?action=health`：

- 管家 45 秒内有心跳：`200`
- 心跳超时：`503`

该接口只反映管家是否存活，与服务器开没开无关。定时关服属于正常行为，不应触发告警。

因此任何能发送 HTTP 请求的监控系统都可以接入，例如 UptimeKuma 的 HTTP(s) 监控、Zabbix 的 Web 检查，或直接使用 cron 定时 curl：

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/?action=health
```

这样只有机器关机、管家进程退出或家中断网时，监控才会报警。

## 常见问题

**启动后 screen 会话立刻消失，日志为空**：服务端解压后 `run.sh` 通常没有执行权限，先执行 `chmod +x run.sh`。

**25575 端口对外暴露**：Minecraft 的 RCON 强制监听 `0.0.0.0`，配置无法修改。机器有公网 IP 时，应在防火墙上阻止外部访问该端口。

更多问题与设计取舍见 [docs/design.md](docs/design.md)。

## 许可证

MIT License，详见 [LICENSE](LICENSE) 文件。

## 作者

本项目由 DeepSeek 编写。
