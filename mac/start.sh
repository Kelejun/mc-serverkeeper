#!/bin/zsh
MC_DIR="${MC_DIR:-$HOME/mc-server}"
cd "$MC_DIR" || exit 1
exec screen -DmS mc ./run.sh nogui
