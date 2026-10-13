#!/bin/sh
set -eu
# Resolve a ~/.local/bin/wechat symlink without depending on Python or GNU readlink.
ENTRY=$0
while [ -L "$ENTRY" ]; do
  PARENT=$(CDPATH= cd -- "$(dirname -- "$ENTRY")" && pwd)
  LINK=$(readlink "$ENTRY")
  case "$LINK" in /*) ENTRY=$LINK ;; *) ENTRY=$PARENT/$LINK ;; esac
done
ROOT=$(CDPATH= cd -- "$(dirname -- "$ENTRY")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
  echo "此入口用于 macOS；其他平台使用 python boot.py。" >&2
  exit 1
fi
if [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then
  exec /usr/bin/arch -arm64 /bin/sh "$ENTRY" "$@"
fi
case "$(uname -m)" in
  arm64) TARGET=macos-arm64 ;;
  *) echo "当前 Mac 版要求 Apple Silicon。" >&2; exit 1 ;;
esac
if [ -x "$ROOT/runtime/$TARGET/bin/python3.12" ]; then
  PYTHON=$ROOT/runtime/$TARGET/bin/python3.12
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON=$ROOT/.venv/bin/python
else
  echo "未找到运行环境。源码仓库请先执行：./scripts/setup_macos.sh" >&2
  exit 1
fi
exec "$PYTHON" -X utf8 "$ROOT/boot.py" "$@"
