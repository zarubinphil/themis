---
name: themis-update
description: 'Обновляет Themis с GitHub — только логику, данные (cases/, knowledge/) не трогает; git-статус до/после, откат при сбое. Триггеры: «обнови Themis», «проверь обновления», /themis-update.'
---

# Обновление Themis

Обновляется: `.claude/`, `AGENTS.md`, `scripts/`, `cockpit/`, `bin/`, `install.sh`, `README.md`, `docs/`; зависимости/пересборку OCR скрипт делает сам через `install.sh`. НЕ трогается: `cases/`, `knowledge/`, рантайм (кеши, `_baselines/`, `cockpit/.state.json`).

Нет `.git/` → СТОП («не git-клон: склонируй свежий Themis, перенеси `cases/` и `knowledge/`»); нет `scripts/update.sh` → СТОП.

## Алгоритм

1. Снимок ДО (вывод сохранить — сверка на шаге 5):

```bash
OLD=$(git rev-parse --short HEAD)
git status --porcelain -- cases knowledge
git status --porcelain -- .claude AGENTS.md scripts cockpit bin install.sh docs
```

2. Третья команда непуста (локальные правки системных путей) → СПРОСИТЬ: обновление их перезапишет; без явного «да» — СТОП.
3. `bash scripts/update.sh` — код возврата проверить явно, не по виду вывода. «Уже последняя версия» → доложить, конец.
4. Exit ≠ 0 → ОТКАТ: `git checkout "$OLD" -- .claude AGENTS.md scripts cockpit bin install.sh README.md .mcp.json .gitignore LICENSE CONTRIBUTING.md docs` → доложить причину (последние строки вывода) и факт отката. СТОП. Нет сети → `git fetch` упадет до правок, откат не нужен.
5. Успех → повторить `git status --porcelain -- cases knowledge`, сравнить со снимком: совпадает → данные целы; отличается → ЧП: доложить КАЖДОЕ отличие, ничего не удалять без решения.

## Контракт вывода

Ответ (не файл): версии ДО → ПОСЛЕ (или «уже последняя») · список обновленного ≤15 строк · строка «Данные (cases/, knowledge/) целы» — по факту сверки · пересборка OCR, если была · при сбое — причина и факт отката.
