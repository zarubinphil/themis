#!/usr/bin/env bash
# scan_legal.sh - детерминированная часть аудита юридического текста на следы AI-генерации.
# Bash + grep + awk; блокеры читает из DocBuilder. Ничего не устанавливает и не меняет.
# Использование:  scan_legal.sh ФАЙЛ            - отчет по файлу
#                 cat f.md | scan_legal.sh -    - отчет по stdin
#                 scan_legal.sh --selftest      - самопроверка скрипта
# Регистр задан явными классами вида [Дд]; UTF-8 locale фиксируется ниже.

set -uo pipefail

# ponytail: три штатных имени покрывают машину владельца и обычные клоны;
# новый вариант добавлять только по факту провала setup_doctor.
UTF8_LOCALE=""
for candidate in C.UTF-8 en_US.UTF-8 ru_RU.UTF-8; do
  if [ "$(LC_ALL="$candidate" locale charmap 2>/dev/null)" = "UTF-8" ]; then
    UTF8_LOCALE="$candidate"
    break
  fi
done
if [ -z "$UTF8_LOCALE" ]; then
  echo "ОШИБКА: UTF-8 locale недоступна — scan_legal не запущен" >&2
  exit 2
fi
export LC_ALL="$UTF8_LOCALE"

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/../../../.." && pwd)"
if [ ! -f "$PROJECT_ROOT/scripts/create_docx.py" ]; then
  PROJECT_ROOT="${THEMIZ_PROJECT_ROOT:-$PWD}"
fi
HUMANIZER_BLOCKERS=""
SCAN_RC=0

check_copy_drift() {
  local owner_home
  local repo="$PROJECT_ROOT/.claude/skills/humanizer-legal/scripts/scan_legal.sh"
  if ! owner_home="$(python3 -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)' 2>/dev/null)" \
      || [ -z "$owner_home" ]; then
    echo "ОШИБКА: домашний каталог владельца не определен; запуск остановлен." >&2
    return 2
  fi
  local home="$owner_home/.claude/skills/humanizer-legal/scripts/scan_legal.sh"
  if [ -f "$repo" ] && [ -f "$home" ] && ! cmp -s "$repo" "$home"; then
    echo "ОШИБКА: резервная копия $home отличается от канона $repo; запуск остановлен." >&2
    return 2
  fi
  return 0
}

load_blockers() {
  local loaded
  if ! loaded="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from create_docx import DocBuilder; print(*DocBuilder.HUMANIZER_BLOCKERS, sep="\n")' "$PROJECT_ROOT/scripts")"; then
    echo "ОШИБКА: DocBuilder.HUMANIZER_BLOCKERS не прочитан" >&2
    return 2
  fi
  [ -n "$loaded" ] || { echo "ОШИБКА: DocBuilder.HUMANIZER_BLOCKERS пуст" >&2; return 2; }
  HUMANIZER_BLOCKERS="$loaded"
}

is_blocker() {
  printf '%s\n' "$HUMANIZER_BLOCKERS" | grep -Fqx -- "$1"
}

# --- словари маркеров ------------------------------------------------------
YO_LOWER="$(printf '\321\221')"
YO_UPPER="$(printf '\320\201')"
DIAERESIS="$(printf '\314\210')"
RE_HARDBAN="[Вв] современном мире|[Вв] эпоху|[Сс]тоит отметить|[Вв]ажно понимать|[Нн]еобходимо подчеркнуть|(^|[^а-яА-Яе${YO_LOWER}${YO_UPPER}])[Дд]анн(ый|ая|ое|ые|ого|ой|ым|ых)|[Нн]е просто [^,]{2,40}, а |[Нн]е только [^,]{2,60}, но и |[Тт]аким образом|[Пп]одводя итог|[Ии]грает (важн|ключев)|[Ии]меет (важное|огромное) значение|невозможно переоценить|[Кк]омплексн(ый|ая|ое) (подход|решение|мер)|[Вв] связи с этим|[Вв] этой связи|[Вв] определенном смысле|[Вв] той или иной степени|[Оо]ткрывает нов(ые|ых) (горизонт|перспектив)|[Дд]авайте (разберемся|рассмотрим)|[Пп]огрузимся"
RE_EVAL='плеяд|чудесным образом|прекрасно организован|решительн(ое|ый|ого) должностн|вопиющ|беспрецедентн|поразительн|блестящ|[Уу]вы[,.]|[Кк] сожалению|[Кк] счастью|шокирующ|ужасающ|грубейш|циничн'
RE_HEDGE='может (свидетельствовать|указывать|повлиять|являться|быть расценено)|представляется (возможным|целесообразным|обоснованным)|[Вв] определенной степени|способен (обеспечить|повлиять|привести)|призван (решить|обеспечить)|полагаем возможным|как представляется'
RE_EMPTYREF='[Сс]уд рассмотрел аналогичн|[Сс]ложившаяся судебная практика|[Мм]ногочисленн(ые|ых) разъяснени|[Сс]удебная практика (подтверждает|исходит)|[Аа]налогичн(ая|ый) (позиция|подход) (изложен|содержится)[^№]{0,60}$|[Сс]огласно устоявшейся практике|практика по данной категории дел'
RE_PSEUDO='[Вв] рамках настоящего (исследования|анализа)|[Вv] контексте рассматриваемой|[Нн]астоящая работа|[Дд]анное исследование|[Сс]ледует констатировать, что'
RE_NOMIN='[Оо]существлени[ея] (поставк|оплат|платеж|передач|действий|деятельности)|[Пп]роизводство оплаты|[Рр]еализаци[яи] (права|прав|обязанност)'
RE_PASTE=':contentReference|oaicite|oai_citation|utm_source=(chatgpt|openai)|【[0-9]+†|\[citation:[0-9]+\]|sandbox:/mnt/data|</think>|citeturn|turn[0-9]+(search|file)[0-9]+|grok_card://'
# Подчеркивания НЕ ловим: «___» __________ 20__ г. и линия подписи - штатные бланки документа.
RE_PLACEHOLDER='\[[^]]*(уточнить|уточн[а-я]*|сверить|указать|вставить|TODO|ЗАПОЛНИТЬ|подставить)[^]]*\]|XXXX|ХХХХ|\{\{[^}]*\}\}'
RE_INVISIBLE=$'​|‌|‍|﻿|  '
# латиница внутри кириллического слова и наоборот
RE_MIXED='[а-яА-Я][a-zA-Z]|[a-zA-Z][а-яА-Я]'
# дата не в формате ДД.ММ.ГГГГ: 15-11-2022, 2022-11-15, 15/11/2022
# Границы (^|[^0-9-]) нужны, чтобы не ловить куски УИД вида 16RS0050-01-2025-011876-74.
RE_DATEFMT='(^|[^0-9-])([0-9]{1,2}-[0-9]{1,2}-[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})([^0-9-]|$)'
RE_YO="${YO_LOWER}|${YO_UPPER}|е${DIAERESIS}|Е${DIAERESIS}"

# --- вывод -----------------------------------------------------------------
# Нормализация: каждый абзац в одну строку. Иначе маркер, разорванный жестким
# переносом строк, построчным грепом не виден. Номера в отчете = номера абзацев.
normalize() { # infile outfile
  awk '{ if ($0 ~ /^[[:space:]]*$/) { if (buf!="") { print buf; buf="" } }
         else { buf = (buf=="" ? $0 : buf " " $0) } }
       END { if (buf!="") print buf }' "$1" > "$2"
}

report_cat() { # name regex file
  local name="$1" re="$2" f="$3" hits n
  hits="$(grep -nE "$re" "$f" 2>/dev/null | head -60)"
  n="$(printf '%s' "$hits" | grep -c . )"
  printf '%4s  %s\n' "$n" "$name"
  [ "$n" -gt 0 ] && printf '%s\n' "$hits" | head -3 | sed 's/^/      /' | cut -c1-160
  if [ "$n" -gt 0 ] && is_blocker "$name"; then SCAN_RC=1; fi
  return 0
}

count_cat() { # file regex -> число срабатываний по нормализованному тексту
  local t; t="$(mktemp)"; normalize "$1" "$t"
  grep -oE "$2" "$t" 2>/dev/null | grep -c .
  rm -f "$t"
}

metrics() { # file
  awk '
    { line[NR]=$0; all = all " " $0; words += NF
      if ($0 ~ /^[[:space:]]*$/) { if (pw>0) { np++; if (pw>130) longp++ ; if (pw>maxp) maxp=pw } ; pw=0 }
      else pw += NF }
    END {
      if (pw>0) { np++; if (pw>130) longp++; if (pw>maxp) maxp=pw }
      n=split(all, s, /[.!?]+[[:space:]]/)
      cnt=0
      for (i=1;i<=n;i++) { w=split(s[i], t, /[[:space:]]+/); if (w>1) { cnt++; len[cnt]=w; sum+=w } }
      if (cnt>0) { mean=sum/cnt
        for (i=1;i<=cnt;i++) { d=len[i]-mean; ss+=d*d }
        sd=sqrt(ss/cnt)
        for (i=1;i<=cnt;i++) { if (len[i]<=6) shortn++; if (len[i]>=25) longn++ }
      }
      printf "слов: %d | предложений: %d | абзацев: %d\n", words, cnt, np
      printf "средняя длина предложения: %.1f сл. | разброс (sd): %.1f\n", mean, sd
      printf "коротких (<=6 сл.): %d | длинных (>=25 сл.): %d\n", shortn+0, longn+0
      printf "абзацев свыше 130 слов: %d | самый длинный: %d сл.\n", longp+0, maxp+0
      if (sd < 5.0) print "!! РИТМ: разброс длин ниже 5 - машинная ровность, нужна дисперсия"
      if (longp > 0) print "!! L6: есть абзацы свыше 130 слов - пустая массивность"
      if (cnt>8 && shortn==0) print "!! РИТМ: нет ни одного короткого предложения"
    }' "$1"
}

scan_file() { # file label
  local src="$1" f fq
  SCAN_RC=0
  load_blockers || return 2
  f="$(mktemp)"; normalize "$src" "$f"
  # Дословная цитата нормы — чужой текст, автором не сочиненный: стилевые
  # детекторы к нему неприменимы. Законодатель пишет «достаточных данных»
  # (ч. 3 ст. 11 УПК РФ), и слово «данн*» из HARD BANS браковало документ за
  # точность цитирования — притом что конституция дела требует «дословно или
  # не цитировать». Прецедент 04.08.2026: заявление о преступлении не
  # сохранялось, пока из него не убрали дословную норму. Блок-цитаты (строки
  # markdown с «>») исключаются из АВТОРСКИХ категорий; технические (копипаста,
  # невидимые символы, плейсхолдеры, латиница, формат дат) проверяются везде —
  # там мусор остается мусором и внутри цитаты.
  fq="$(mktemp)"; grep -v '^[[:space:]]*>' "$f" > "$fq" 2>/dev/null || cp "$f" "$fq"
  echo "=== scan_legal: $2 ==="
  echo "-- маркеры (категория, число срабатываний, первые примеры; N = номер абзаца) --"
  report_cat "HARD BANS"                  "$RE_HARDBAN"     "$fq"
  report_cat "L1 оценочно-художественное" "$RE_EVAL"        "$fq"
  report_cat "L3 псевдоакадемизм"         "$RE_PSEUDO"      "$fq"
  report_cat "L4 номинализации"           "$RE_NOMIN"       "$fq"
  report_cat "L13 хеджирование"           "$RE_HEDGE"       "$fq"
  report_cat "L14 ссылка-пустышка"        "$RE_EMPTYREF"    "$fq"
  report_cat "L18 артефакт копипасты"     "$RE_PASTE"       "$f"
  report_cat "L18 плейсхолдер"            "$RE_PLACEHOLDER" "$f"
  report_cat "L18 невидимые символы"      "$RE_INVISIBLE"   "$f"
  report_cat "L18 латиница в кириллице"   "$RE_MIXED"       "$f"
  report_cat "формат даты не ДД.ММ.ГГГГ"  "$RE_DATEFMT"     "$f"
  report_cat "буква ${YO_LOWER}"           "$RE_YO"          "$fq"
  echo "-- ритм и объем --"
  metrics "$src"
  rm -f "$f" "$fq"
  echo "-- напоминание --"
  echo "Скрипт не видит: логические разрывы (L8), квалификацию без факта (L12),"
  echo "галлюцинированные реквизиты (L17). Проверять вручную по каталогу SKILL.md."
  return "$SCAN_RC"
}

selftest() {
  local d rc=0 scan_rc n
  d="$(mktemp -d)"
  # d локальна: фиксируем проверенный mktemp-путь до выхода из функции.
  # shellcheck disable=SC2064
  trap "rm -rf '$d'" EXIT
  cat > "$d/dirty.txt" <<'EOF'
В современном мире данный вопрос играет ключевую роль. Стоит отметить, что целая
плеяда защитников чудесным образом участвовала в проверках, что может
свидетельствовать о нарушении. Сложившаяся судебная практика подтверждает нашу
позицию. Осуществление оплаты произведено с задержкой :contentReference[oaicite:1].
Договор от [уточнить дата] подписан сторонами. Таким образом, требования обоснованы.
EOF
  cat > "$d/clean.txt" <<'EOF'
Ответчик получил 6 000 000 рублей по Договору от 15.03.2024 № 47 и не поставил товар.
Срок поставки истек 30.04.2024. Товар не поставлен до настоящего времени.
Обязательство должно исполняться надлежащим образом [ст. 309 ГК РФ]. Односторонний
отказ не допускается [ст. 310 ГК РФ]. Следовательно, нарушение установлено.
EOF
  cat > "$d/soft.txt" <<'EOF'
Это обстоятельство может свидетельствовать о нарушении.
EOF
  printf '%s\n' 'Решение вынесено судом.' > "$d/ordinary-e.txt"
  printf '\321\221 \320\201 \320\265\314\210 \320\225\314\210\n' > "$d/yo.txt"
  printf '%s\n' 'Данный документ подготовлен.' > "$d/block-hard.txt"
  printf '%s\n' 'Срок: [уточнить дату].' > "$d/block-placeholder.txt"
  printf '%s\n' 'Источник :contentReference[oaicite:1].' > "$d/block-paste.txt"
  printf '\320\242\320\265\320\272\321\201\321\202\342\200\213.\n' > "$d/block-invisible.txt"
  printf '%s\n' 'догoвор заключен.' > "$d/block-mixed.txt"
  local checks=("HARD BANS:$RE_HARDBAN" "L1:$RE_EVAL" "L13:$RE_HEDGE" "L14:$RE_EMPTYREF" "L18-paste:$RE_PASTE" "L18-ph:$RE_PLACEHOLDER" "L4:$RE_NOMIN")
  for c in "${checks[@]}"; do
    local name="${c%%:*}" re="${c#*:}" n
    n="$(count_cat "$d/dirty.txt" "$re")"
    if [ "$n" -lt 1 ]; then echo "FAIL: $name не сработал на грязном образце"; rc=1
    else echo "ok: $name -> $n"; fi
    n="$(count_cat "$d/clean.txt" "$re")"
    if [ "$n" -gt 0 ]; then echo "FAIL: $name ложное срабатывание на чистом образце ($n)"; rc=1; fi
  done

  scan_file "$d/dirty.txt" "selftest-dirty" > "$d/dirty.out" 2>&1; scan_rc=$?
  if [ "$scan_rc" -ne 1 ]; then echo "FAIL: блокирующая категория дала код $scan_rc вместо 1"; rc=1
  else echo "ok: блокирующая категория -> код 1"; fi
  local blocker_file
  for blocker_file in "$d"/block-*.txt; do
    scan_file "$blocker_file" "selftest-blocker" > "$d/blocker.out" 2>&1; scan_rc=$?
    if [ "$scan_rc" -ne 1 ]; then
      echo "FAIL: $(basename "$blocker_file") дала код $scan_rc вместо 1"; rc=1
    fi
  done
  scan_file "$d/clean.txt" "selftest-clean" > "$d/clean.out" 2>&1; scan_rc=$?
  if [ "$scan_rc" -ne 0 ]; then echo "FAIL: чистый текст дал код $scan_rc"; rc=1
  else echo "ok: чистый текст -> код 0"; fi
  scan_file "$d/soft.txt" "selftest-soft" > "$d/soft.out" 2>&1; scan_rc=$?
  if [ "$scan_rc" -ne 0 ]; then echo "FAIL: неблокирующая категория дала код $scan_rc"; rc=1
  else echo "ok: неблокирующая категория -> код 0"; fi
  n="$(count_cat "$d/ordinary-e.txt" "$RE_YO")"
  if [ "$n" -ne 0 ]; then echo "FAIL: маркер буквы с точками сработал на обычной е ($n)"; rc=1
  else echo "ok: обычная е не сработала"; fi
  n="$(count_cat "$d/yo.txt" "$RE_YO")"
  if [ "$n" -ne 4 ]; then echo "FAIL: NFC/NFD-маркер сработал $n раз вместо 4"; rc=1
  else echo "ok: NFC/NFD-маркер -> 4"; fi
  if [ $rc -eq 0 ]; then echo "SELFTEST OK"; else echo "SELFTEST FAILED"; fi
  return $rc
}

# --- точка входа -----------------------------------------------------------
if [ -n "${1:-}" ] && [ "$1" != "--selftest" ]; then
  check_copy_drift || exit $?
fi
case "${1:-}" in
  --selftest) selftest; exit $? ;;
  "" ) echo "usage: scan_legal.sh ФАЙЛ | - | --selftest" >&2; exit 2 ;;
  - ) tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT; cat > "$tmp"; scan_file "$tmp" "stdin" ;;
  * ) [ -f "$1" ] || { echo "нет файла: $1" >&2; exit 2; }; scan_file "$1" "$1" ;;
esac
