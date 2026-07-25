---
description: Обновить Themis с GitHub — только логика (агенты, скиллы, скрипты, cockpit), данные дел не трогаются; git-статус до/после, откат при сбое
---

Обнови Themis по скиллу `themis-update` (там полный алгоритм и таблица сбоев). Каркас:

1. ДО запуска: `OLD=$(git rev-parse --short HEAD)`; снять `git status --porcelain -- cases knowledge` (снимок данных) и `git status --porcelain -- .claude AGENTS.md scripts cockpit bin install.sh .mcp.json docs`. Локальные правки системных путей есть → СПРОСИТЬ пользователя (update.sh их перезапишет).
2. `bash scripts/update.sh` — проверить код возврата явно.
3. Сбой (exit ≠ 0) → откат системных путей: `git checkout "$OLD" -- .claude AGENTS.md scripts cockpit bin install.sh README.md .mcp.json docs` → доложить причину и факт отката.
4. Успех → повторить `git status --porcelain -- cases knowledge`, сравнить со снимком из п. 1: совпадает → данные целы; отличается → ЧП, доложить каждое отличие, ничего не удалять.

Доложи кратко: была ли новая версия, что обновилось (список файлов), строка «Данные дел (cases/) и база знаний (knowledge/) целы».
