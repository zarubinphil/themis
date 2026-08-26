# Фемида

Фемида - локальный многоагентный помощник для российской судебной практики: читает материалы, считает сроки и суммы, собирает позицию и проверяет себя перед юристом.

[English](README.md)

<p align="center">
  <img src="docs/assets/pantheon/hero.png" width="820" alt="Фемида Pantheon: статуя Фемиды с весами, мечом, правовыми документами, карточками агентной проверки и общей мраморной колонной">
  <br>
  <img src="docs/assets/pantheon/emblem.png" width="220" alt="Эмблема Фемиды Pantheon: Фемида, весы, меч и колонна">
</p>

## Что умеет

- Читать PDF, Word, Excel, изображения и сканы, когда локальное распознавание доступно.
- Строить карту дела: стороны, суммы, даты, документы, риски и процессуальные шаги.
- Считать проценты, госпошлину, сроки и контрольные реквизиты программно.
- Отдавать позицию через несколько независимых агентов и отдельную проверку.
- Готовить проекты документов, которые юрист проверяет и принимает сам.

## Быстрый старт

```bash
git clone https://github.com/zarubinvibe/themis.git
cd themis
bash install.sh
python3 cockpit/app.py
```

Панель откроется на `http://localhost:8800`. Обновление существующего клона:

```bash
bash scripts/update.sh
```

## Примеры

- Загрузить сканы дела и получить карту фактов.
- Проверить процессуальные сроки перед подачей документа.
- Собрать черновик позиции и прогнать её через совет проверяющих.

## Документация

- [Как я работаю](docs/HOW-IT-WORKS.ru.md)
- [Правила публикации и приватности](knowledge/server-protocol.md)
- [Полная лицензия](LICENSE.ru.md)

## Безопасность и приватность

Материалы дел не публикуются. Папки доверителей, локальные секреты, токены, runtime-состояние и персональные данные должны оставаться вне git. Перед публикацией запускается `scripts/pd_guard.py`, а публичный релиз проверяется `public-repo-gate`.

## Статус

Проект предназначен для работы вместе с юристом. Он не заменяет юридическое решение, не отправляет документы без проверки человека и не должен использоваться как единственный источник правовой позиции.

<!-- pantheon-family:start -->
## Семья Pantheon

Этот репозиторий входит в [семью проектов Pantheon](https://github.com/zarubinvibe?tab=repositories). Для каждого публичного проекта даны прямые ссылки на репозиторий и ZIP с исходниками.

| Тип | Название | Что внутри | Скачать |
|---|---|---|---|
| проект | Athena | Переносимая агентная ОС: разворачивает рабочую среду Claude и Codex на новом Mac. | [Репозиторий](https://github.com/zarubinvibe/athena) · [ZIP](https://github.com/zarubinvibe/athena/archive/refs/heads/main.zip) |
| проект | Claude Code Setup OS | Bootstrap-скилл для экономной среды Claude Code и локальной LLM-вики. | [Репозиторий](https://github.com/zarubinvibe/claude-code-setup-os) · [ZIP](https://github.com/zarubinvibe/claude-code-setup-os/archive/refs/heads/main.zip) |
| проект | Helioz | Конвейер работы агентов 24/7 с проверяемыми отметками готовности и ночными решениями по цели владельца. | [Репозиторий](https://github.com/zarubinvibe/helioz) · [ZIP](https://github.com/zarubinvibe/helioz/archive/refs/heads/main.zip) |
| проект | Humanizer | Агентный скилл, который убирает типичные следы AI из английского текста. | [Репозиторий](https://github.com/zarubinvibe/humanizer) · [ZIP](https://github.com/zarubinvibe/humanizer/archive/refs/heads/main.zip) |
| проект | Humanizer RU | Русский редакторский скилл: находит и убирает 58 типичных следов AI-текста. | [Репозиторий](https://github.com/zarubinvibe/humanizer-ru) · [ZIP](https://github.com/zarubinvibe/humanizer-ru/archive/refs/heads/main.zip) |
| проект | Mnemazine | Локальная система памяти: превращает сырьё в проверенные знания для повторного использования. | [Репозиторий](https://github.com/zarubinvibe/mnemazine) · [ZIP](https://github.com/zarubinvibe/mnemazine/archive/refs/heads/main.zip) |
| проект | Smltlk | Приложение для строки меню macOS: чинит раскладку, распознаёт речь офлайн и превращает голос в промпт. | [Репозиторий](https://github.com/zarubinvibe/smltlk) · [ZIP](https://github.com/zarubinvibe/smltlk/archive/refs/heads/main.zip) |
| проект | Themis | Многоагентный помощник по российским судебным делам с локальным OCR и советом из пяти юристов. | [Репозиторий](https://github.com/zarubinvibe/themis) · [ZIP](https://github.com/zarubinvibe/themis/archive/refs/heads/main.zip) |
| проект | Zeuz | Фабрика многоагентных workflow: собирает систему с правилами, гейтами, наблюдаемостью и replay. | [Репозиторий](https://github.com/zarubinvibe/zeuz) · [ZIP](https://github.com/zarubinvibe/zeuz/archive/refs/heads/main.zip) |
<!-- pantheon-family:end -->

## Лицензия

Частному юристу бесплатно. Организации - по договору. Полный текст: [LICENSE.ru.md](LICENSE.ru.md) и [LICENSE](LICENSE).
