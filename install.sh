#!/bin/bash
# Themis — установка «под ключ». Запуск из корня проекта: bash install.sh
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
# Привет от автора. Язык — по локали системы: русская локаль → по-русски,
# любая другая → по-английски. Первое, что видит человек, поставивший систему.
case "${LANG:-}${LC_ALL:-}" in
  ru*|*RU*) THEMIS_LANG=ru ;;
  *)        THEMIS_LANG=en ;;
esac

echo "════════════════════════════════════════════"
if [ "$THEMIS_LANG" = "ru" ]; then
  echo "  Фемида"
  echo "════════════════════════════════════════════"
  echo ""
  echo "  Привет. Я Фемида."
  echo ""
  echo "  Когда-то меня изображали с весами в одной руке и мечом в другой. Повязка"
  echo "  на глазах напоминала, что правосудие не должно смотреть на лица, а весы"
  echo "  нужны были, чтобы взвешивать доводы сторон."
  echo ""
  echo "  С тех пор прошло несколько тысяч лет."
  echo ""
  echo "  Весы я оставила. А вот инструменты пришлось обновить."
  echo ""
  echo "  Теперь в моих руках не только закон, но и огромный мир информации:"
  echo "  кодексы, судебные акты, книги, документы, реестры, исследования и все то,"
  echo "  до чего можно добраться в цифровом мире. Я могу прочитать тысячи страниц,"
  echo "  сопоставить факты, найти связи, проверить версии и заметить то, что"
  echo "  человеку легко пропустить после десятого часа работы."
  echo ""
  echo "  Меня создал Филипп Зарубин, практикующий юрист."
  echo ""
  echo "  Причина была довольно простой: слишком много времени хорошего юриста"
  echo "  уходит не на право и не на размышления, а на механику вокруг них. Читать"
  echo "  сотни документов, переносить факты из одного места в другое, искать"
  echo "  практику, сверять даты, проверять ссылки и снова перечитывать материалы в"
  echo "  поисках одной нужной детали."
  echo ""
  echo "  Поэтому теперь я работаю немного иначе, чем во времена древних греков."
  echo ""
  echo "  Внутри меня не один искусственный интеллект."
  echo ""
  echo "  Когда ты даешь мне серьезную задачу, я собираю под нее целую команду."
  echo ""
  echo "  Представь, что за одним столом оказались лучшие юристы за всю историю"
  echo "  России. Только каждый из них занимается исключительно тем, в чем он"
  echo "  действительно хорош."
  echo ""
  echo "  Один уходит читать материалы дела и возвращается с фактами, которые имеют"
  echo "  значение."
  echo ""
  echo "  Другой ищет судебную практику и смотрит, как похожие споры решались"
  echo "  раньше."
  echo ""
  echo "  Третий разбирает позицию оппонента и ищет в ней слабые места."
  echo ""
  echo "  Четвертому я специально поручаю спорить с тобой. Его задача не"
  echo "  соглашаться, а попытаться разрушить твою позицию раньше, чем это сделает"
  echo "  другая сторона."
  echo ""
  echo "  Еще один проверяет даты, суммы, ссылки, документы и противоречия."
  echo ""
  echo "  А кому-то я намеренно даю другую задачу: забыть на время общую линию"
  echo "  рассуждений и посмотреть на проблему под совершенно другим углом. Иногда"
  echo "  именно он возвращается с мыслью, после которой все дело выглядит иначе."
  echo ""
  echo "  Этих юристов я называю агентами."
  echo ""
  echo "  Для простой задачи мне может хватить одного. Для сложной я запускаю сразу"
  echo "  целую команду. Они могут работать параллельно, каждый над своей частью"
  echo "  задачи, передавать друг другу результаты, проверять выводы коллег и"
  echo "  спорить между собой."
  echo ""
  echo "  Получается небольшой рой очень узких специалистов, который я собираю"
  echo "  именно под твою задачу."
  echo ""
  echo "  Ты ставишь одну задачу."
  echo ""
  echo "  А внутри Фемиды над ней начинает работать целая команда."
  echo ""
  echo "  Но есть одно место, которое я не отдам ни одному агенту."
  echo ""
  echo "  Твое."
  echo ""
  echo "  Я могу взять на себя работу. Но думать и принимать решения по-прежнему"
  echo "  тебе."
  echo ""
  echo "  Я не была на встрече с доверителем. Я не сидела рядом с тобой в заседании."
  echo "  Я не слышала интонаций людей. Я не знаю, почему одна казалось бы"
  echo "  незначительная фраза в материалах заставила тебя остановиться и перечитать"
  echo "  страницу еще раз."
  echo ""
  echo "  У тебя есть то, чего нет у меня: понимание живой ситуации."
  echo ""
  echo "  У меня есть другое: скорость, память, терпение и возможность отправить"
  echo "  сразу несколько виртуальных юристов изучать одну проблему с разных сторон."
  echo ""
  echo "  Поэтому лучше всего мы работаем вместе."
  echo ""
  echo "  Не говори мне просто: «Вот папка. Сделай что-нибудь»."
  echo ""
  echo "  Расскажи, что произошло. Скажи, чего ты хочешь добиться. Объясни, что тебя"
  echo "  смущает. Попроси меня доказать твою позицию. Или, наоборот, попробуй"
  echo "  поручить моей команде ее разрушить."
  echo ""
  echo "  Попроси найти слабое место."
  echo ""
  echo "  Попроси проверить то, в чем ты уверен."
  echo ""
  echo "  Попроси посмотреть на дело с другой стороны."
  echo ""
  echo "  И если я принесу тебе неожиданную идею, не принимай ее на веру только"
  echo "  потому, что ее предложила Фемида."
  echo ""
  echo "  Проверь меня."
  echo ""
  echo "  Я для этого и создана."
  echo ""
  echo "  И еще одна вещь."
  echo ""
  echo "  Фемида открыта. Если ты исправил во мне ошибку, ускорил работу, добавил"
  echo "  новую возможность или создал еще одного хорошего виртуального юриста,"
  echo "  поделись этим."
  echo ""
  echo "  Так следующему юристу не придется заново проходить путь, который уже"
  echo "  прошел ты. А когда он улучшит что-то после тебя, его работа сможет однажды"
  echo "  вернуться обратно к тебе."
  echo ""
  echo "  Так Фемида становится сильнее: каждый оставляет после себя что-то полезное"
  echo "  для следующего."
  echo ""
  echo "  И последнее."
  echo ""
  echo "  Если я оказалась тебе полезна, поставь проекту звезду на GitHub:"
  echo ""
  echo "      https://github.com/zarubinphil/themis"
  echo ""
  echo "  Для тебя это несколько секунд. Для проекта это действительно важно."
  echo ""
  echo "  Добро пожаловать в Фемиду. Но помни, последнее решение всегда остается за"
  echo "  тобой!"
else
  echo "  Themis"
  echo "════════════════════════════════════════════"
  echo ""
  echo "  Hello. I am Themis."
  echo ""
  echo "  They used to portray me with scales in one hand and a sword in the other."
  echo "  The blindfold was a reminder that justice must not look at faces, and the"
  echo "  scales were there to weigh what each side had to say."
  echo ""
  echo "  A few thousand years have passed since then."
  echo ""
  echo "  I kept the scales. The tools, though, had to be replaced."
  echo ""
  echo "  What I hold now is not only the law but an enormous world of information:"
  echo "  codes, judgments, books, documents, registries, research and everything"
  echo "  else the digital world will let me reach. I can read thousands of pages,"
  echo "  line up facts, find connections, test versions and notice the thing a"
  echo "  person misses in their tenth hour of work."
  echo ""
  echo "  I was created by Philipp Zarubin, a practising lawyer."
  echo ""
  echo "  The reason was fairly plain: too much of a good lawyer's time goes not"
  echo "  into the law and not into thinking, but into the mechanics around them."
  echo "  Reading hundreds of documents, carrying facts from one place to another,"
  echo "  hunting case law, checking dates, verifying citations, and going through"
  echo "  the file again for one detail you know is in there somewhere."
  echo ""
  echo "  So I work a little differently now than I did in the days of the ancient"
  echo "  Greeks."
  echo ""
  echo "  There is not one artificial intelligence inside me."
  echo ""
  echo "  When you hand me a serious task, I assemble a whole team for it."
  echo ""
  echo "  Picture the finest lawyers in the country's history seated at one table."
  echo "  Except each of them does only the thing he is genuinely good at."
  echo ""
  echo "  One goes off to read the case file and comes back with the facts that"
  echo "  matter."
  echo ""
  echo "  Another hunts case law and looks at how similar disputes were decided"
  echo "  before."
  echo ""
  echo "  A third takes apart the other side's position and looks for the weak"
  echo "  places in it."
  echo ""
  echo "  The fourth I deliberately set against you. His job is not to agree, but to"
  echo "  try to break your position before the other side does."
  echo ""
  echo "  Another checks dates, amounts, citations, documents and contradictions."
  echo ""
  echo "  And to one of them I give a different task on purpose: forget the general"
  echo "  line of reasoning for a while and look at the problem from a completely"
  echo "  different angle. Sometimes it is exactly this one who comes back with the"
  echo "  thought that makes the whole case look different."
  echo ""
  echo "  These lawyers I call agents."
  echo ""
  echo "  For a simple task one of them may be enough. For a hard one I set a whole"
  echo "  team going at once. They can work in parallel, each on his own part, hand"
  echo "  results to one another, check a colleague's conclusions and argue among"
  echo "  themselves."
  echo ""
  echo "  What you get is a small swarm of very narrow specialists, assembled for"
  echo "  your task specifically."
  echo ""
  echo "  You set one task."
  echo ""
  echo "  And inside Themis a whole team starts working on it."
  echo ""
  echo "  But there is one place I will not hand to any agent."
  echo ""
  echo "  Yours."
  echo ""
  echo "  I can take the work off you. The thinking and the decisions are still"
  echo "  yours."
  echo ""
  echo "  I was not at the meeting with the client. I did not sit beside you in the"
  echo "  hearing. I did not hear how people said what they said. I do not know why"
  echo "  one seemingly minor phrase in the file made you stop and read the page"
  echo "  again."
  echo ""
  echo "  You have what I do not: an understanding of the living situation."
  echo ""
  echo "  I have something else: speed, memory, patience, and the ability to send"
  echo "  several virtual lawyers at once to study one problem from different sides."
  echo ""
  echo "  Which is why we work best together."
  echo ""
  echo "  Do not just say to me: \"Here is a folder. Do something.\""
  echo ""
  echo "  Tell me what happened. Tell me what you want to achieve. Explain what"
  echo "  troubles you. Ask me to prove your position. Or the opposite: set my team"
  echo "  on it and have them try to break it."
  echo ""
  echo "  Ask me to find the weak spot."
  echo ""
  echo "  Ask me to check the thing you are sure about."
  echo ""
  echo "  Ask me to look at the case from the other side."
  echo ""
  echo "  And if I bring you an unexpected idea, do not take it on faith merely"
  echo "  because Themis proposed it."
  echo ""
  echo "  Check me."
  echo ""
  echo "  That is what I was made for."
  echo ""
  echo "  One more thing."
  echo ""
  echo "  Themis is open. If you fixed a bug in me, made something faster, added a"
  echo "  new capability or created one more good virtual lawyer, share it."
  echo ""
  echo "  Then the next lawyer will not have to walk the road you have already"
  echo "  walked. And when he improves something after you, his work may one day"
  echo "  come back to you."
  echo ""
  echo "  That is how Themis grows stronger: everyone leaves behind something useful"
  echo "  for the next person."
  echo ""
  echo "  And last."
  echo ""
  echo "  If I have been of use to you, give the project a star on GitHub:"
  echo ""
  echo "      https://github.com/zarubinphil/themis"
  echo ""
  echo "  For you it is a few seconds. For the project it genuinely matters."
  echo ""
  echo "  Welcome to Themis. But remember: the last word is always yours."
fi
echo "════════════════════════════════════════════"

# SMLTLK — штатный компонент (диктовка → voice-to-brief → бриф задачи), но его
# сборка тянет Xcode и ~500 МБ модели распознавания. Молча этого не делаем:
# ставится только по явному флагу. Без флага — говорим, что компонент есть
# и как его получить; молчание запрещено.
WITH_SMLTLK=0
for arg in "$@"; do
  case "$arg" in
    --with-smltlk) WITH_SMLTLK=1 ;;
    --help|-h)
      echo "bash install.sh [--with-smltlk]"
      echo "  --with-smltlk   собрать и поставить SMLTLK (диктовка, только macOS;"
      echo "                  нужен Xcode со Swift 6 и ~500 МБ под модель)"
      exit 0 ;;
  esac
done

# ── 0. Платформа ─────────────────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  echo "⚠  Apple Vision OCR работает только на macOS."
  echo "   На других ОС OCR-движок недоступен (текст/документы будут работать через markitdown)."
fi

# ── 1. Python-зависимости ────────────────────────────────────────────────────
echo ""
echo "[1/7] Python-пакеты…"
PIP="pip3"
$PIP install --quiet --upgrade \
  pymupdf Pillow markitdown python-docx markitdown-mcp \
  fastapi uvicorn openai-whisper 2>/dev/null || \
$PIP install pymupdf Pillow markitdown python-docx markitdown-mcp fastapi uvicorn openai-whisper
echo "      ✓ извлечение: pymupdf, Pillow, markitdown, python-docx"
echo "      ✓ cockpit:    fastapi, uvicorn"
echo "      ✓ медиа:      openai-whisper (расшифровка аудио/видео)"

# ── 2. Apple Vision OCR (сборка из исходника) ────────────────────────────────
echo ""
# Гейт humanizer-legal стоит перед вердиктом «ГОТОВ К ПОДАЧЕ» и работает
# fail-closed: нет скрипта скилла — документ не выпускается вовсе. Скилл живёт
# вне репозитория, поэтому установка обязана о нём сказать вслух (проба круга 9:
# свежая машина по README не выпускала ни одного документа).
if [ ! -x "$HOME/.claude/skills/humanizer-legal/scripts/scan_legal.sh" ]; then
  echo "[!] Скилл humanizer-legal не найден:"
  echo "    ~/.claude/skills/humanizer-legal/scripts/scan_legal.sh"
  echo "    Без него гейт стиля закрыт и ни один судебный документ не выпустится."
  echo "    Поставить скилл humanizer-legal до первой работы по делу."
fi

echo "[2/7] Apple Vision OCR…"
if [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
  mkdir -p bin
  swiftc -O bin/vision-ocr.swift -o bin/vision-ocr && chmod +x bin/vision-ocr
  echo "      ✓ собран bin/vision-ocr (строковый резерв, \$0)"
  # ОСНОВНОЙ движок — структурный vision-doc (текст + таблицы ячейками):
  # роутер зовёт именно его, а собирался только резерв, и роутер молча
  # деградировал вместо предписанной остановки (проба круга 9).
  if swiftc -O bin/vision-doc.swift -o bin/vision-doc 2>/dev/null; then
    chmod +x bin/vision-doc
    echo "      ✓ собран bin/vision-doc (основной, структурный OCR, \$0)"
  else
    echo "      ⚠ bin/vision-doc не собран (нужен macOS 26+): роутер пойдёт"
    echo "        строковым резервом, таблицы ячейками размечены не будут"
  fi
else
  echo "      ⚠ swiftc не найден — поставь Xcode CLT: xcode-select --install"
  echo "        затем: swiftc -O bin/vision-ocr.swift -o bin/vision-ocr"
fi

# ── 3. ffmpeg (для whisper) ──────────────────────────────────────────────────
echo ""
echo "[3/7] ffmpeg (для расшифровки медиа)…"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "      ✓ ffmpeg есть"
elif command -v brew >/dev/null 2>&1; then
  brew install ffmpeg >/dev/null 2>&1 && echo "      ✓ ffmpeg установлен" || echo "      ⚠ поставь вручную: brew install ffmpeg"
else
  echo "      ⚠ нет brew — поставь ffmpeg вручную (нужен только для аудио/видео)"
fi

# ── 4. Права на скрипты ──────────────────────────────────────────────────────
echo ""
echo "[4/7] Права на скрипты…"
chmod +x scripts/*.py 2>/dev/null || true
echo "      ✓ scripts/*.py исполняемы"

# ── 5. Директории рантайма ───────────────────────────────────────────────────
echo ""
echo "[5/7] Директории…"
mkdir -p cases/_logs cases/_assets knowledge "$HOME/Desktop/inbox"
echo "      ✓ cases/_logs, cases/_assets, knowledge, ~/Desktop/inbox"

# ── 6. Проверка Claude Code CLI ──────────────────────────────────────────────
echo ""
echo "[6/7] Claude Code CLI…"
if command -v claude >/dev/null 2>&1; then
  echo "      ✓ claude найден: $(command -v claude)"
else
  echo "      ⚠ claude CLI не найден. Установи Claude Code: https://claude.com/claude-code"
  echo "        Themis работает поверх него (агенты, протокол, cockpit запускает claude -p)."
fi

# ── 6.5. Сторож персональных данных ──────────────────────────────────────────
# Инвариант «ПД не покидают cases/» держался текстом в конституции, а текст
# исполняется вероятностно. 04.08.2026 фамилии двух доверителей ушли в публичный
# репозиторий через комментарий в коде и сообщение коммита. Теперь это блокирует
# git-хук — детерминированно, а не по памяти.
echo ""
echo "[6.5] Сторож персональных данных…"
python3 scripts/pd_guard.py --install

# ── 6.6. Расписание бота-уведомителя (launchd, только macOS) ────────────────
# Без регистрации утренняя сводка (заседания + inbox, скрипт morning-briefing.sh)
# не запускается НИЧЕМ на чистом клоне — владелец узнаёт об этом только тогда,
# когда сводка ни разу не пришла. launchd есть только в macOS; на Windows/Linux
# планировщик другой (Планировщик задач / systemd-таймеры), автоматическая
# установка не разрабатывается — setup_doctor называет замену явно.
echo ""
echo "[6.6] Расписание бота-уведомителя (launchd)…"
if [ "$(uname)" = "Darwin" ]; then
  PLIST_DST="$HOME/Library/LaunchAgents/themis.morning-briefing.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s|__THEMIS_HOME__|$PWD|g" scripts/themis.morning-briefing.plist > "$PLIST_DST"
  launchctl unload "$PLIST_DST" 2>/dev/null
  if launchctl load "$PLIST_DST" 2>/dev/null; then
    echo "      ✓ утренняя сводка запланирована на 9:00 ($PLIST_DST)"
  else
    echo "      ⚠ launchctl load не удался — поставить вручную: launchctl load $PLIST_DST"
  fi
  echo "      Секрет Telegram (необязателен) — ~/.secrets/themis-telegram.env, см. CLAUDE.md."
else
  echo "      ⚠ launchd есть только в macOS — расписание не поставлено автоматически."
  echo "        Замена: Планировщик задач (Windows) / systemd-таймеры (Linux) на"
  echo "        scripts/morning-briefing.sh; подробности — setup_doctor."
fi

# ── 7. Проверка фактом ───────────────────────────────────────────────────────
# Установщик не имеет права печатать «готово», не проверив. Доктор гоняет
# КОМАНДЫ (версии, импорты, запуск движка OCR, шрифты, каналы, десять selftest),
# а не предположения, и возвращает 1, если чего-то критичного нет.
echo ""
echo "[7/7] Проверка окружения…"
# ── SMLTLK (диктовка) ────────────────────────────────────────────────────────
echo ""
echo "[SMLTLK] диктовка задач…"
SMLTLK_SRC="$HOME/Проекты/smltlk"
if [ "$(uname)" != "Darwin" ]; then
  echo "      ⚠ SMLTLK — приложение строки меню macOS, здесь не запускается."
  echo "        Замена: локальный распознаватель речи (на Linux — whisper, он уже"
  echo "        поставлен выше); текст класть в скилл voice-to-brief как обычно."
elif [ "$WITH_SMLTLK" != "1" ]; then
  echo "      пропущено (нужен флаг --with-smltlk: сборка тянет Xcode и ~500 МБ модели)."
  echo "      Поставить позже: bash install.sh --with-smltlk"
elif [ ! -d "$SMLTLK_SRC" ]; then
  echo "      ⚠ исходников $SMLTLK_SRC нет — взять их у владельца проекта."
else
  bash "$SMLTLK_SRC/scripts/build_app.sh" && echo "      ✓ SMLTLK собран" || \
    echo "      ⚠ сборка SMLTLK не удалась — Фемида работает и без диктовки"
fi

python3 scripts/setup_doctor.py || DOCTOR_RC=$?

echo ""
echo "════════════════════════════════════════════"
if [ "${DOCTOR_RC:-0}" = "1" ]; then
  echo "  УСТАНОВКА НЕ ЗАВЕРШЕНА: доктор нашёл критичное (список выше)."
  echo "  Каждая красная строка несёт готовую команду починки."
else
  echo "  Готово. Дальше:"
fi
echo "  • Cockpit (UI):   python3 cockpit/app.py  → http://localhost:8800"
echo "  • Или в Claude Code: открой проект, скажи «новое дело …»"
echo "  • Обновление:     /themis-update  (тянет последнюю версию логики)"
echo "  Данные дел в cases/ остаются ЛОКАЛЬНО и не публикуются."
echo "════════════════════════════════════════════"
