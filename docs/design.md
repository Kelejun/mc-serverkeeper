# 设计说明与踩坑记录

README 讲怎么装怎么用，这里写当初为什么这么选，以及踩过的坑。

## 需求

私服跑在自家机器上，只有一两个朋友偶尔玩。要解决两件具体的事：

1. 每 30 分钟看一次，连续 2~4 个时间段没有玩家登录记录，就 `stop` 关服
2. 网页上一个启动按钮，服务器没开的时候点一下就能开起来

约束：不能要求玩家装任何东西；不能让云端主动连进家里（NAT）；关服必须走正常存盘流程，不能 `kill -9`。

## 空闲判定为什么是「窗口内无登录 + 窗口末无人在线」

三种口径比较过：

| 口径 | 问题 |
| --- | --- |
| 只看当前在线人数 | 玩家挂着挂机 AFK 也算在线，永远不关；反过来刚退出就关，太急 |
| 只看持续无人在线的秒数 | 有人玩了三分钟被判定为空闲，因为「此刻」确实没人 |
| **窗口末无人在线 + 窗口内无登录** | 只有整段时间真的没人碰过才算空闲 |

选第三种。实现上每 60 秒采样一次 `list`，任一次采样有玩家就把本窗口标记为「有过人」，窗口结束时再确认一次人数。默认 30 分钟一个窗口，连续 3 个空闲窗口（1.5 小时）才关。

## 关服流程

```
idle_windows >= K
   └─▶ status = stopping
        └─▶ 再 list 一次，>0 就取消（有人刚连进来）
             └─▶ RCON stop（等同玩家在游戏里打 /stop，触发正常存盘）
                  └─▶ 最多等 60 秒，端口关掉
                       └─▶ status = stopped_by_idle
```

不用 `kill`，因为 Minecraft 的 `stop` 会走完整的 `ServerStoppingEvent`、保存所有区块、写 `session.lock` 释放。直接杀进程会丢存档，严重时 world 打不开。

关服前不发全服广播：触发的前提就是没人，广播没人看得到，白等 60 秒。有人在线的时候本来就不会走到这一步。

## 托管链路：launchd → screen → java

要同时满足三个条件：开机自启、挂了自动重启、随时能进控制台敲命令。

| 方案 | 结论 |
| --- | --- |
| 直接 `nohup java ... &` | 起得来，但没法再进控制台，改配置只能重启 |
| tmux | macOS 不自带，要装 |
| **launchd + screen** | launchd 负责拉起，screen 负责保留控制台 |

关键验证：launchd 的 job 退出**不会**杀掉 detach 出来的 screen 会话（用临时 job 实测过），所以管家进程可以放心重启，服务器不受影响。

## 踩过的坑

### 1. `run.sh` 没有执行权限

```
./run.sh: Permission denied
```

screen 会话启动后立刻消失，日志里什么都没有。因为 `run.sh` 跑不起来，java 根本没启动，而错误输出被 screen 的 detach 吃掉了。

**新下载的服务端压缩包解压后经常缺执行位，第一件事就是 `chmod +x run.sh`。**

### 2. 用 `screen -ls` 判断是否在跑，有竞态

最初的启动脚本这么写：

```bash
if screen -ls | grep -q "\.mc[[:space:]]"; then
  echo "already running"; exit 0
fi
```

从服务器里退出时 socket 文件会残留一瞬间，这时候再点按钮，脚本会误判「已经在跑了」然后直接退出，什么都不做。

改成查端口最可靠：

```bash
if lsof -nP -iTCP:25565 -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0
fi
```

### 3. 心跳间隔必须小于离线阈值

云端页面 45 秒没收到上报就显示「电脑不在线」。管家一开始每 60 秒上报一次，结果每轮都会有 15 秒页面变成「离线」再跳回来，看着像在抽风。

改成 15 秒心跳，留三倍余量。

### 4. 关服瞬间会被误判成「运行中」

Minecraft 关闭时，25565 先释放，25575（RCON）晚几百毫秒才断。如果只根据 RCON 能不能连来判断服务器状态，这几百毫秒里 RCON 还活着，状态就会被标回 `running`，页面显示「运行中」，按钮点不动。

采样逻辑里加了端口检查：端口没监听就直接 `return`，不改状态。

### 5. 关服后页面要 3 分钟才更新

原来只有一种检测：每 60 秒 RCON `list` 一次，连续 3 次失败才判死。玩家在游戏里 `/stop` 之后，最坏要等 3 分钟页面才变。

加了独立的 `health_check()`：每 10 秒直接探 25565 端口，连续 2 次不通（20 秒）就判 `crashed`。RCON 三次的判定留着兜底 —— 应付「端口还开着但服务器主循环卡死」这种空壳。

### 6. 点启动按钮，状态会闪一下「未启动」

时序：

```
t=0     点按钮 → pending_start = true，页面显示「请求已发出」
t=3     管家轮询取走指令，pending_start = false
t=3~15  页面读到 pending=false，但状态还是旧的（stopped）→ 退回「未启动」
t=18    管家心跳上报 starting → 页面才显示「启动中」
```

两头都要改：

- 管家收到启动指令后，**立刻**上报一次状态，不等心跳
- 页面记住「用户点过启动」，两分钟内强制显示「启动中」，直到真的变成 running

### 7. 本地没有 PHP，`php -l` 都做不了

Mac 上没装 PHP，写好的 `index.php` 没法本地做语法检查。只能人工过一遍再传上去。如果云端是宝塔，传上去访问一下就知道有没有 `500`。

### 8. RCON 监听地址改不了

`server.properties` 里没有 `rcon.bind` 这类配置，RCON 永远 listen `0.0.0.0:25575`。如果机器有公网 IP，一定要在防火墙层面挡掉这个端口。只靠内网穿透转发 25565 的部署方式天然安全，因为 25575 根本没暴露出去。

## 安全清单

- [ ] `rcon.pass` 权限 600，不进版本库
- [ ] `cloud.conf` 权限 600，不进版本库
- [ ] `AGENT_TOKEN` 用 `openssl rand -hex 16` 生成，不要用弱口令
- [ ] 有公网 IP 的话，防火墙挡掉 25575
- [ ] 云端站点建议开 HTTPS（token 在 query string 里传）

## 附：PCL 启动器主页

如果玩家用 PCL 启动器，可以顺手把「服务器状态 / 一键启动」按钮塞进它的自定义主页，省得开浏览器。PCL 的自定义主页是 XAML 片段，文件扩展名是 `.htm`，结构大致是：

```xml
<local:MyCard Title="开始游戏">
  <StackPanel Orientation="Horizontal" HorizontalAlignment="Center">
    <local:MyButton Text="开始游戏" EventType="启动游戏" Width="240" .../>
    <local:MyButton Text="服务器状态" EventType="打开网页" EventData="https://mc.example.com" Width="240" .../>
  </StackPanel>
</local:MyCard>
```

安装：PCL → 设置 → 启动器 → 自定义主页，选这个文件。`local:` 前缀是必须的，按钮想居中得套一层 `StackPanel HorizontalAlignment="Center"`，直接在 `MyButton` 上写 `HorizontalAlignment` 不生效。
