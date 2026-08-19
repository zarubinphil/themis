# OWNER-TODO — всё, что требует рук владельца, одним списком

> Составлен 19.08.2026 при закрытии этапа 9. Автономный цикл эти пункты НЕ исполняет:
> каждый либо необратимо трогает `cases/`, либо действует наружу (Telegram, сервер,
> чужие машины, деньги). Сделал пункт — ставь дату и убирай в «Сделано» внизу.

## 1. Уборка под `cases/` (заморожено для цикла, трогает данные дел)

Числа сверены с диском 19.08.2026. Прибор вывоза пишет цикл (`scripts/case_code_gc.py`,
манифест + обратимость, как `render_gc.py`); владелец только запускает.

```bash
# 84 файла кода (.py/.sh) из-под cases/ — вывоз по манифесту, обратимо:
python3 scripts/case_code_gc.py --plan      # показать, что уедет
python3 scripts/case_code_gc.py --apply     # вывезти (манифест в .agent/archive/)

# 28 мёртвых practice_context.md (шаг 4.5 снят):
find cases -name "practice_context.md" -print
python3 scripts/case_code_gc.py --practice-context --apply

# 31 лок Word ~$* — ТОЛЬКО при закрытом Word:
find cases -name '~$*' -delete
```

## 2. Бот Фемида — первый живой запуск (наружу, порядок в `knowledge/bot-protocol.md`)

1. BotFather → создать бота, токен → `~/.secrets/themis-telegram.env`.
2. `python3 scripts/themis_bot.py --chat-probe` → свой `chat_id` в `~/.themis/config.json`.
3. `bot.enabled: true` в `~/.themis/config.json`.
4. Аватар: `python3 scripts/bot_avatar.py` → загрузить в BotFather.
5. `python3 scripts/themis_bot.py --serve` (замок: один опрашивающий на машину).

## 3. Офсайт-копия первички — 20+ ГБ сейчас в двух копиях на ОДНОМ диске

```bash
python3 scripts/intake_backup.py --dest /Volumes/<внешний-диск>
```
Внешних дисков в системе нет — купить/подключить. Напоминать, пока не сделано.

## 4. Выкатка панели (по `knowledge/server-protocol.md`)

Пользователь `themis` на сервере · закрыть открытый DNS-резолвер · выкатить cockpit.

## 5. Живой клон на Windows и Linux

`themis-setup` на чужой машине: проверить, что гейт humanizer-legal (живёт в
`~/.claude/skills/` вне репозитория) честно останавливает, а не молча пропускает.

## 6. Замер этапа 7 на настоящих Codex и Kimi

Всё проверено заглушками; живой вызов чужого CLI — только руками владельца.
`python3 scripts/cli_probe.py` на машине с установленными CLI → реестр подхватит.

## 7. Решение по `/graphify`

README и панель обещают `/graphify`, `graphify-out/` не создавался ни разу.
Первый прогон дорогой. Решить: гнать (`graphify update ~/Проекты/themis`) или
убрать обещание из README/панели (уберёт цикл, если решение «убрать»).

## 8. Вычистка истории публичного репозитория от ПД (найдено 19.08.2026)

Сторож нашёл фамилии доверителей в отслеживаемых файлах; файлы вычищены
коммитом, но ИСТОРИЯ git их помнит (включая прецедент 04.08.2026). Репозиторий
публичный: чистить историю и форс-пушить может только владелец.

```bash
# без установки пакетов: git filter-repo ставится отдельно, СНАЧАЛА полная копия
git clone --mirror <репо> ~/Архив/themis-mirror-do-chistki
# затем: git filter-repo --replace-text <файл замен> && git push --force
```

## Сделано

(пусто)
