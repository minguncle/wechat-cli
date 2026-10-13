#!/bin/sh
# Install the source checkout's runtime without touching account data or WeChat.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$(uname -s)" != Darwin ]; then
  echo "安装脚本要求 macOS。" >&2; exit 1
fi
if [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then
  exec /usr/bin/arch -arm64 /bin/sh "$ROOT/scripts/setup_macos.sh" "$@"
fi
if [ "$(uname -m)" != arm64 ]; then
  echo "当前 Mac 版要求 Apple Silicon。" >&2; exit 1
fi
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != --link ]; }; then
  echo "用法：./scripts/setup_macos.sh [--link]" >&2; exit 2
fi
cd "$ROOT"
if command -v uv >/dev/null 2>&1; then
  uv --cache-dir "$ROOT/.build/uv-cache" venv --python 3.12 --allow-existing "$ROOT/.venv"
  uv --cache-dir "$ROOT/.build/uv-cache" pip install --python "$ROOT/.venv/bin/python" -e '.[dev]' -c requirements-macos.lock
else
  PYTHON=${NOVUS_WECHAT_PYTHON:-python3.12}
  if ! "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
    echo "请先安装 uv 或 Python 3.12；也可用 NOVUS_WECHAT_PYTHON 指定解释器。" >&2; exit 1
  fi
  "$PYTHON" -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install -e '.[dev]' -c requirements-macos.lock
fi
chmod +x "$ROOT/launch.command"
if [ "${1:-}" = --link ]; then
  BIN_DIR=$HOME/.local/bin
  mkdir -p "$BIN_DIR"
  if [ -e "$BIN_DIR/wechat" ] || [ -L "$BIN_DIR/wechat" ]; then
    if [ "$(readlink "$BIN_DIR/wechat" || true)" != "$ROOT/launch.command" ]; then
      echo "$BIN_DIR/wechat 已存在，未覆盖。请使用 $ROOT/launch.command。" >&2; exit 1
    fi
  else
    ln -s "$ROOT/launch.command" "$BIN_DIR/wechat"
  fi
  echo "已安装 $BIN_DIR/wechat；请确保该目录在 PATH 中。"
fi
"$ROOT/launch.command" doctor
