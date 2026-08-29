#!/bin/bash
cd "$(dirname "$0")"
# Бьём старый сервер по ПОРТУ, не по имени: cmdline = "python3 app.py" (cwd=cockpit),
# паттерн "cockpit/app.py" не совпадал → старый процесс с устаревшим кодом держал порт.
OLD=$(lsof -nP -iTCP:8800 -sTCP:LISTEN -t 2>/dev/null)
[ -n "$OLD" ] && kill -9 $OLD 2>/dev/null
pkill -f "app.py" 2>/dev/null; sleep 1
python3 app.py >/tmp/cockpit.log 2>&1 &
sleep 3; open http://localhost:8800
echo "Femida Cockpit → http://localhost:8800"
