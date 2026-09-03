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
  echo "      https://github.com/zarubinvibe/themis"
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
  echo "  I was created by Filipp Zarubin, a practising lawyer."
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
  echo "      https://github.com/zarubinvibe/themis"
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

# ── Разрешение на установку ──────────────────────────────────────────────────
# Я не ставлю на чужой компьютер ничего молча. Сначала называю, что именно, зачем
# и сколько это весит, и жду прямого согласия. «Сейчас поставлю зависимости» —
# это не разрешение, а его имитация.
if [ "$THEMIS_LANG" = "ru" ]; then
  echo ""
  echo "  Прежде чем что-то ставить, скажу, что мне нужно и зачем."
  echo ""
  echo "  Библиотеки Python, около 200 МБ. Без них я не открою ни одного"
  echo "  документа:"
  echo "    · pypdfium2, pypdf, markitdown, python-docx — читать PDF, Word и Excel"
  echo "    · Pillow — работать с фотографиями документов"
  echo "    · reportlab — собирать PDF на подпись"
  echo "    · fastapi, uvicorn — панель, где видно ход работы по делу"
  echo "    · openai-whisper — расшифровывать голосовые прямо у тебя,"
  echo "      не отправляя запись наружу"
  echo "    Весь состав с версиями и лицензиями — в файле requirements.txt,"
  echo "    ставится только он."
  echo ""
  if [ "$(uname)" = "Darwin" ]; then
    echo "  Еще соберу распознавание сканов. Оно работает на технологии,"
    echo "  которая уже есть в твоем Mac, поэтому бесплатно и никуда не"
    echo "  отправляет твои документы. Нужен компилятор из Xcode Command Line"
    echo "  Tools; если его нет, я скажу отдельно."
    echo ""
    echo "  И ffmpeg, около 100 МБ, через Homebrew. Нужен только для звука и"
    echo "  видео. Не работаешь с аудио — можно пропустить."
    echo ""
  fi
  echo "  Все ставится к тебе на компьютер. Наружу ничего не уходит."
  echo ""
  printf "  Ставлю? [Enter — да, n — отмена]: "
else
  echo ""
  echo "  Before I install anything, here is what I need and why."
  echo ""
  echo "  Python libraries, about 200 MB. Without them I cannot open a single"
  echo "  document:"
  echo "    · pypdfium2, pypdf, markitdown, python-docx — to read PDF, Word and Excel"
  echo "    · Pillow — to handle photographs of documents"
  echo "    · reportlab — to build PDFs for signature"
  echo "    · fastapi, uvicorn — the panel that shows what I am doing"
  echo "    · openai-whisper — to transcribe voice notes on your own computer,"
  echo "      without sending the recording anywhere"
  echo "    The full list with versions and licenses is requirements.txt,"
  echo "    and that file is all I install."
  echo ""
  if [ "$(uname)" = "Darwin" ]; then
    echo "  I will also build scan recognition. It runs on technology already"
    echo "  present in your Mac, so it costs nothing and sends your documents"
    echo "  nowhere. It needs the compiler from Xcode Command Line Tools; if"
    echo "  that is missing I will say so separately."
    echo ""
    echo "  And ffmpeg, about 100 MB, via Homebrew. Needed only for audio and"
    echo "  video. Skip it if you never work with recordings."
    echo ""
  fi
  echo "  Everything is installed on your own computer. Nothing goes out."
  echo ""
  printf "  Install? [Enter — yes, n — cancel]: "
fi

# Ответ читаем с терминала (работает и когда скрипт запущен через `| bash`),
# а если терминала нет — со стандартного входа. Не получили ответа вовсе —
# считаем это отказом. Гейт согласия, который при молчании ставит пакеты, —
# не гейт: прецедент 21.08.2026, проба с ответом «n» прошла установку насквозь,
# потому что ответ ушел мимо /dev/tty.
SOGLASIE=""
if [ "${THEMIS_YES:-}" = "1" ]; then
  SOGLASIE="y"                      # для неинтерактивной установки: THEMIS_YES=1
elif [ -r /dev/tty ] && [ -t 1 ]; then
  read -r SOGLASIE < /dev/tty || SOGLASIE="__net__"
elif [ -p /dev/stdin ]; then
  # На входе ТРУБА: кто-то скармливает нам скрипт (`curl … | bash`). Ровно здесь
  # и случился прецедент 21.08.2026 - ответ ушел мимо /dev/tty, и «n» прошло как
  # согласие. Молчание трубы согласием не считаем никогда.
  read -r SOGLASIE || SOGLASIE="__net__"
elif [ ! -t 0 ] && [ ! -t 1 ]; then
  # Ни терминала, ни трубы - это автоматический прогон: проверка публичного
  # дерева на чистой машине (public-repo-gate isolated-run), CI, образ. Спросить
  # физически некого, и ОТКАЗ здесь означал бы, что установку не проверяет никто:
  # гейт зеленел бы на дереве, которое никто ни разу не поставил. Ставим и
  # говорим об этом вслух - решение владельца 03.09.2026.
  SOGLASIE="y"
  THEMIS_AVTO="1"
else
  SOGLASIE="__net__"
fi
[ -z "$SOGLASIE" ] && [ "${THEMIS_YES:-}" != "1" ] && [ "${THEMIS_AVTO:-}" != "1" ] && [ ! -t 0 ] && SOGLASIE="__net__"

# Автоматический прогон обязан назвать себя: тихая установка без спроса
# неотличима от установки, на которую согласились.
if [ "${THEMIS_AVTO:-}" = "1" ]; then
  echo ""
  if [ "$THEMIS_LANG" = "ru" ]; then
    echo "  Терминала нет и трубы нет - это автоматический прогон. Ставлю без вопроса."
    echo "  Человеку я задаю его всегда: bash install.sh в терминале."
  else
    echo "  No terminal and no pipe - this is an automated run. Installing without asking."
    echo "  A person always gets the question: run bash install.sh in a terminal."
  fi
fi

if [ "$SOGLASIE" = "__net__" ]; then
  echo ""
  if [ "$THEMIS_LANG" = "ru" ]; then
    echo "  Не смог спросить разрешение — значит не ставлю."
    echo "  Запусти в терминале: bash install.sh"
    echo "  Либо разреши заранее:  THEMIS_YES=1 bash install.sh"
  else
    echo "  I could not ask for permission, so I install nothing."
    echo "  Run it in a terminal:  bash install.sh"
    echo "  Or agree up front:     THEMIS_YES=1 bash install.sh"
  fi
  exit 1
fi

case "$SOGLASIE" in
  n|N|нет|no)
    if [ "$THEMIS_LANG" = "ru" ]; then
      echo ""
      echo "  Понял, ничего не ставлю. Запусти bash install.sh, когда решишь."
    else
      echo ""
      echo "  Understood, installing nothing. Run bash install.sh when ready."
    fi
    exit 0 ;;
esac

# ── 1. Python-зависимости ────────────────────────────────────────────────────
echo ""
echo "[1/7] Python-пакеты…"
PIP="pip3"
# Состав объявлен ОДИН раз — в requirements.txt, который собирается с диска
# (python3 scripts/setup_doctor.py --licenses) из фактических импортов приборов.
# Раньше список жил здесь руками и расходился с составом в обе стороны: четыре
# живых зависимости не ставились вовсе, а AGPL-пакет ехал незамеченным (M11).
$PIP install --quiet -r requirements.txt 2>/dev/null || \
$PIP install --quiet --user -r requirements.txt 2>/dev/null || \
$PIP install --quiet --break-system-packages -r requirements.txt 2>/dev/null || \
$PIP install -r requirements.txt
# ПРОВЕРЯЕМ ПО ФАКТУ, а не по коду pip. На чужой машине системный Python бывает
# «externally managed» (PEP 668): pip отказывает, а установщик печатал галочку и
# шел дальше - изолированный прогон 03.09.2026 поймал ровно это, PIL не появился,
# и sign_and_pdf падал уже у человека. Тихий отказ хуже честного красного.
NEDOSTAET=""
for M in fitz PIL docx yaml fastapi reportlab pypdfium2 pypdf; do
  python3 -c "import $M" 2>/dev/null || NEDOSTAET="$NEDOSTAET $M"
done
if [ -n "$NEDOSTAET" ]; then
  echo ""
  if [ "$THEMIS_LANG" = "ru" ]; then
    echo "  ✗ Пакеты не встали:$NEDOSTAET"
    echo "    Похоже, системный Python защищен (PEP 668). Обходные пути:"
    echo "      python3 -m venv .venv && . .venv/bin/activate && bash install.sh"
    echo "      либо  pip3 install --user -r requirements.txt"
  else
    echo "  ✗ Packages did not install:$NEDOSTAET"
    echo "    The system Python is probably externally managed (PEP 668). Options:"
    echo "      python3 -m venv .venv && . .venv/bin/activate && bash install.sh"
    echo "      or    pip3 install --user -r requirements.txt"
  fi
  exit 1
fi
echo "      ✓ состав из requirements.txt (лицензии — столбцом в нем же)"
echo "      ✓ извлечение · cockpit · медиа"

# ── 1.5 Шрифт судебных документов ────────────────────────────────────────────
# PT Serif везется В РЕПОЗИТОРИИ (assets/fonts, SIL OFL, лицензия рядом) по
# решению владельца 03.09.2026. Раньше он не приезжал ниоткуда: на машине
# владельца лежал с давних пор, а у чужого человека документ на подпись не
# собирался вовсе - поймано изолированным прогоном гейта публикации.
# Ставим в систему, иначе Word подставит свой, и документ уйдет в суд не в том
# виде, в каком его проверяли.
echo ""
echo "[1.5] Шрифт PT Serif…"
if [ "$(uname)" = "Darwin" ]; then
  FONT_DIR="$HOME/Library/Fonts"
else
  FONT_DIR="$HOME/.local/share/fonts"
fi
mkdir -p "$FONT_DIR"
POSTAVLENO=0
for F in assets/fonts/PT_Serif-Web-*.ttf; do
  [ -f "$F" ] || continue
  if [ ! -f "$FONT_DIR/$(basename "$F")" ]; then
    cp "$F" "$FONT_DIR/" && POSTAVLENO=$((POSTAVLENO + 1))
  fi
done
if [ "$POSTAVLENO" -gt 0 ]; then
  echo "      ✓ поставлено начертаний: $POSTAVLENO → $FONT_DIR"
else
  echo "      ✓ PT Serif уже на месте"
fi

# ── 2. Apple Vision OCR (сборка из исходника) ────────────────────────────────
echo ""
# Проверка стиля судебных документов стоит перед вердиктом «ГОТОВ К ПОДАЧЕ» и
# работает fail-closed: нет скрипта — документ не выпускается вовсе. Раньше он
# жил ТОЛЬКО в домашней папке навыков, вне репозитория, и установка на втором
# устройстве проходила «успешно», а документы не выпускались (21.08.2026).
# Теперь он едет внутри репозитория; домашняя копия — запасной путь.
SCAN_REPO=".claude/skills/humanizer-legal/scripts/scan_legal.sh"
SCAN_DOMA="$HOME/.claude/skills/humanizer-legal/scripts/scan_legal.sh"
if [ -f "$SCAN_REPO" ]; then
  chmod +x "$SCAN_REPO" 2>/dev/null || true
elif [ ! -x "$SCAN_DOMA" ]; then
  echo "[!] Проверка стиля судебных документов не найдена:"
  echo "    $SCAN_REPO"
  echo "    Она должна ехать вместе с репозиторием, без нее ни один судебный"
  echo "    документ не выпустится. Обнови: bash scripts/update.sh"
fi

echo "[2/7] Apple Vision OCR…"
if [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
  mkdir -p bin
  swiftc -O bin/vision-ocr.swift -o bin/vision-ocr && chmod +x bin/vision-ocr
  echo "      ✓ собран bin/vision-ocr (строковый резерв, \$0)"
  # ОСНОВНОЙ движок — структурный vision-doc (текст + таблицы ячейками):
  # роутер зовет именно его, а собирался только резерв, и роутер молча
  # деградировал вместо предписанной остановки (проба круга 9).
  if swiftc -O bin/vision-doc.swift -o bin/vision-doc 2>/dev/null; then
    chmod +x bin/vision-doc
    echo "      ✓ собран bin/vision-doc (основной, структурный OCR, \$0)"
  else
    echo "      ⚠ bin/vision-doc не собран (нужен macOS 26+): роутер пойдет"
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
echo "      ✓ cases/_logs, cases/_assets, knowledge, $HOME/Desktop/inbox"

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
# не запускается НИЧЕМ на чистом клоне — владелец узнает об этом только тогда,
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
  echo '      Секрет Telegram (необязателен) — $HOME/.secrets/themis-telegram.env, см. CLAUDE.md.'
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

# ── Метида: НЕОБЯЗАТЕЛЬНАЯ часть, спрашиваем отдельно ────────────────────────
# Почему отдельным вопросом, а не молча. Метида - самостоятельный проект, она
# сжимает контекст перед отправкой модели. Без нее Фемида работает целиком: сжатие
# просто выключено, и прибор говорит об этом вслух. Раньше ее отсутствие роняло
# селфтест и выглядело поломкой продукта - изолированный прогон 03.09.2026.
echo ""
if [ "$THEMIS_LANG" = "ru" ]; then
  echo "[доп.] Метида — сжатие контекста (необязательно)"
  echo "  Отдельный инструмент. Он ужимает материал перед отправкой модели:"
  echo "  меньше токенов на том же деле. Фемида работает и без него — сжатие"
  echo "  тогда выключено, и приборы говорят об этом прямо, а не молчат."
else
  echo "[optional] Metida - context compression (optional)"
  echo "  A separate tool. It squeezes material before it goes to the model:"
  echo "  fewer tokens on the same case. Themis works fine without it - the"
  echo "  compression is simply off, and the instruments say so out loud."
fi
METIDA_OTVET="n"
if [ "${THEMIS_YES:-}" = "1" ] || [ "${THEMIS_AVTO:-}" = "1" ]; then
  METIDA_OTVET="n"                 # автоматический прогон ничего лишнего не тянет
elif [ -r /dev/tty ] && [ -t 1 ]; then
  if [ "$THEMIS_LANG" = "ru" ]; then
    printf "  Подключить Метиду? [Enter — нет, y — да]: "
  else
    printf "  Connect Metida? [Enter - no, y - yes]: "
  fi
  read -r METIDA_OTVET < /dev/tty || METIDA_OTVET="n"
fi
case "$METIDA_OTVET" in
  y|Y|да|yes)
    if [ "$THEMIS_LANG" = "ru" ]; then
      echo "  Назови каталог с исходниками Метиды и положи его в переменную:"
      echo "      export THEMIS_METIZ_DIR=/путь/к/metiz"
      echo "  После этого сжатие включится само, проверить: node scripts/themis-metiz.mjs --selftest"
    else
      echo "  Point the tool at the Metida sources:"
      echo "      export THEMIS_METIZ_DIR=/path/to/metiz"
      echo "  Compression turns on by itself; check with: node scripts/themis-metiz.mjs --selftest"
    fi ;;
  *)
    if [ "$THEMIS_LANG" = "ru" ]; then
      echo "      ✓ без Метиды — так и задумано, ничего не сломается"
    else
      echo "      ✓ without Metida - by design, nothing breaks"
    fi ;;
esac

python3 scripts/setup_doctor.py || DOCTOR_RC=$?

echo ""
echo "════════════════════════════════════════════"
if [ "${DOCTOR_RC:-0}" = "1" ]; then
  echo "  УСТАНОВКА НЕ ЗАВЕРШЕНА: доктор нашел критичное (список выше)."
  echo "  Каждая красная строка несет готовую команду починки."
else
  echo "  Готово. Дальше:"
fi
echo "  • Cockpit (UI):   python3 cockpit/app.py  → http://localhost:8800"
echo "  • Или в Claude Code: открой проект, скажи «новое дело …»"
echo "  • Обновление:     /themis-update  (тянет последнюю версию логики)"
echo "  Данные дел в cases/ остаются ЛОКАЛЬНО и не публикуются."
echo "════════════════════════════════════════════"
