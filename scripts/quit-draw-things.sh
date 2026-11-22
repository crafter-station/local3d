#!/bin/sh
set -eu

# Opt in only for a dedicated Draw Things instance. macOS may ask for Automation access.
if /usr/bin/pgrep -x 'Draw Things' >/dev/null; then
    /usr/bin/osascript -e 'tell application id "com.liuliu.draw-things" to quit'
fi
attempt=0
while /usr/bin/pgrep -x 'Draw Things' >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 45 ]; then
        printf '%s\n' 'Draw Things did not exit within 45 seconds.' >&2
        exit 1
    fi
    /bin/sleep 1
done
