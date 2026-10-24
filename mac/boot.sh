#!/bin/zsh
# 已在监听就不重复启动
if lsof -nP -iTCP:25565 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "mc already listening on 25565"
  exit 0
fi
# 清理可能残留的 screen 会话
if screen -ls 2>/dev/null | grep -q "\.mc[[:space:]]"; then
  screen -S mc -X quit >/dev/null 2>&1 || true
  sleep 2
fi
MC_DIR="${MC_DIR:-$HOME/mc-server}"
cd "$MC_DIR" || exit 1
exec screen -DmS mc ./run.sh nogui
