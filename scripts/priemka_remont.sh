#!/usr/bin/env bash
# Приемка ремонта конвейера — десять команд Части 8 файла локальный файл ремонта ФЕМИДЫ.
# Каждая секция — отдельная команда с ожидаемым кодом. Аргументы: номера секций
# (по умолчанию все). Ненулевой код любой секции = приемка красная.
#
#   bash scripts/priemka_remont.sh          # все десять
#   bash scripts/priemka_remont.sh 2 3 4    # только гейты вердикта
#
# Правило: этот файл правят только руками владельца или оркестратора. Исполнитель
# задачи, который меняет приемку под свою работу, проваливает миссию — подделка
# видна в diff маркера helioz-gate.
set -u
cd "$(dirname "$0")/.." || exit 2
. scripts/sreda.sh
CASE_DIR="${THEMIZ_CASE:-$(ls -d cases/*/*/ 2>/dev/null | head -1)}"
FAILS=0
STEND=""

fixture() {  # $1 — каталог; кладет draft.md с классической денежной формой
  mkdir -p "$1/_working"
  cat > "$1/draft.md" <<'ТЕКСТ'
# Ответ на уведомление о повышении арендной платы

Арендная плата за 2026 год составляет 356 462,91 (триста пятьдесят шесть тысяч
четыреста шестьдесят два рубля девяносто одна копейка) в месяц.

Внесение платы производится в срок до 10.03.2026 (ст. 614 ГК РФ).
ТЕКСТ
}

sect() { echo "── $1. $2"; }

expect() {  # expect <код|не0> <описание> -- команда…
  local want="$1"; local what="$2"; shift 3
  "$@" >/tmp/priemka.out 2>&1
  local code=$?
  if [ "$want" = "не0" ]; then
    if [ "$code" -ne 0 ]; then echo "   ✓ $what (код $code)"
    else echo "   ✗ $what — ждали отказ, получили 0"; FAILS=$((FAILS+1)); sed 's/^/     /' /tmp/priemka.out | head -5; fi
  elif [ "$code" = "$want" ]; then echo "   ✓ $what (код $code)"
  else echo "   ✗ $what — ждали код $want, получили $code"; FAILS=$((FAILS+1)); sed 's/^/     /' /tmp/priemka.out | head -5; fi
}

run_1() {
  sect 1 "Проводник существует и держит порядок фаз"
  expect 0 "проводник синтаксически жив" -- node --check .claude/workflows/themiz-pipeline.js
  expect 0 "проводник тоньше 260 строк" -- test "$(wc -l < .claude/workflows/themiz-pipeline.js)" -lt 260
  expect 0 "проводник зовет существующие приборы" -- bash -c 'grep -q "themiz_status.py" .claude/workflows/themiz-pipeline.js && grep -q "verdict.py" .claude/workflows/themiz-pipeline.js && grep -q "document_guard.py" .claude/workflows/themiz-pipeline.js && grep -q "quality_gate.py" .claude/workflows/themiz-pipeline.js'
  expect 0 "проводник без TODO/placeholder" -- bash -c '! grep -qi "TODO\\|заглушка\\|placeholder" .claude/workflows/themiz-pipeline.js'
  expect 0 "рецензия ровно из трех линз" -- bash -c 'test "$(grep -c "label: '\''lens:" .claude/workflows/themiz-pipeline.js)" -eq 3'
}

verdict_stend() {  # общий стенд для 2-4: первый раунд записан честно
  STEND=$(mktemp -d); fixture "$STEND"
  python3 scripts/verdict.py "$STEND/draft.md" --record --verdict 'ТРЕБУЕТ ПРАВОК' -r 1 >/dev/null 2>&1
}

run_2() {
  sect 2 "Лимит раундов исполняется машиной: третий раунд — код 3"
  verdict_stend
  echo "правка первая" >> "$STEND/draft.md"
  python3 scripts/verdict.py "$STEND/draft.md" --record --verdict 'ТРЕБУЕТ ПРАВОК' -r 2 >/dev/null 2>&1
  echo "правка вторая" >> "$STEND/draft.md"
  expect 3 "третий раунд отбит с эскалацией владельцу" -- python3 scripts/verdict.py "$STEND/draft.md" --record --verdict 'ТРЕБУЕТ ПРАВОК' -r 3
  rm -rf "$STEND"
}

run_3() {
  sect 3 "Словарь вердиктов закрыт"
  verdict_stend
  expect не0 "вердикт вне словаря не записывается" -- python3 scripts/verdict.py "$STEND/draft.md" --record --verdict 'ЧТО-УГОДНО' -r 1
  rm -rf "$STEND"
}

run_4() {
  sect 4 "Правку без изменения файла записать нельзя"
  verdict_stend
  expect не0 "тот же md5 на новом раунде — отказ" -- python3 scripts/verdict.py "$STEND/draft.md" --record --verdict 'ТРЕБУЕТ ПРАВОК' -r 2
  rm -rf "$STEND"
}

run_5() {
  sect 5 "Формат проверяется по .md, до сборки .docx"
  STEND=$(mktemp -d); fixture "$STEND"
  expect 0 "классическая денежная форма принята без .docx" -- python3 scripts/document_guard.py --md-only "$STEND/draft.md"
  rm -rf "$STEND"
}

run_6() {
  sect 6 "Правила видны исполнителю"
  local n
  n=$(python3 scripts/document_guard.py --rules 2>/dev/null | grep -c пропись)
  if [ "${n:-0}" -gt 0 ]; then echo "   ✓ свод правил печатается, строк про пропись: $n"
  else echo "   ✗ --rules не печатает правило прописи"; FAILS=$((FAILS+1)); fi
}

run_7() {
  sect 7 "Денежное правило починено"
  expect 0 "селфтест прибора формата зеленый" -- python3 scripts/document_guard.py --selftest
}

run_8() {
  sect 8 "Статус показывает агентов и расход"
  if [ -z "$CASE_DIR" ]; then echo "   ✗ дела на диске нет — проверить статус не на чем"; FAILS=$((FAILS+1)); return; fi
  local status_log
  status_log="$(mktemp)"
  python3 scripts/themiz_status.py "$CASE_DIR" >"$status_log" 2>&1
  code=$?
  if { [ "$code" -eq 0 ] || [ "$code" -eq 2 ] || [ "$code" -eq 3 ]; } &&
     grep -q "активные агенты" "$status_log" &&
     grep -q "расход" "$status_log" &&
     grep -q "прочитано с диска" "$status_log"; then
    echo "   ✓ статус печатает активных агентов, расход и источник (код $code)"
  else
    echo "   ✗ статус не дает управляемый результат"; FAILS=$((FAILS+1)); sed 's/^/     /' "$status_log" | head -8
  fi
  rm -f "$status_log"
}

run_9() {
  sect 9 "Корпус права полон"
  local n
  n=$(python3 scripts/cite.py --list 2>/dev/null | grep -cE "(gk-rf|apk-rf)\.md")
  if [ "${n:-0}" -ge 2 ]; then echo "   ✓ ГК и АПК на диске"
  else echo "   ✗ ГК или АПК нет в выгрузке (найдено: ${n:-0} из 2)"; FAILS=$((FAILS+1)); fi
  expect 0 "cite не путает ст. 123.20 с пропуском 123.20-1" -- python3 scripts/cite.py "ст. 123.20 ГК РФ"
  local cite_log
  cite_log="$(mktemp)"
  if python3 scripts/cite.py "ст. 123.20-3 ГК РФ" >"$cite_log" 2>&1; then
    echo "   ✗ cite вернул зеленый по пропущенной статье"; FAILS=$((FAILS+1))
  elif grep -q "отсутствует в выгрузке" "$cite_log"; then
    echo "   ✓ cite называет пропущенную статью неполной выгрузкой"
  else
    echo "   ✗ cite спрятал пропуск за общей ошибкой"; FAILS=$((FAILS+1)); sed 's/^/     /' "$cite_log" | head -5
  fi
  rm -f "$cite_log"
  local check_log
  check_log="$(mktemp)"
  if python3 scripts/update_legal_corpus.py --check --doc gk-rf >"$check_log" 2>&1; then
    echo "   ✗ --check промолчал о неполной выгрузке ГК"; FAILS=$((FAILS+1))
  elif grep -q "НЕПОЛНАЯ ВЫГРУЗКА" "$check_log"; then
    echo "   ✓ --check называет неполную выгрузку ГК"
  else
    echo "   ✗ --check упал без понятной причины неполной выгрузки"; FAILS=$((FAILS+1))
  fi
  rm -f "$check_log"
  expect 0 "тип письма дает ПРОСИМ и неизвестный тип отвергнут" -- python3 scripts/create_docx.py --selftest
  expect 0 "policy ловит переставленную колонку модели" -- python3 scripts/model_policy.py --selftest
}

run_10() {
  sect 10 "Тест Мнемозины зеленый"
  MNEMAZINE_HOME="${MNEMAZINE_HOME:-../mnemazine}"
  log=$(mktemp)
  if node "$MNEMAZINE_HOME/tests/test-coverage-fix.mjs" >"$log" 2>&1 && grep -q "11 passed" "$log"; then
    echo "   ✓ 11 passed"
  else echo "   ✗ тест Мнемозины не дает 11 passed"; FAILS=$((FAILS+1)); fi
  rm -f "$log"
}

# перечень секций строится счетом, а не литералом: длинная цепочка цифр в исходнике
# срабатывает у сторожа персональных данных как номер документа (ложное совпадение)
if [ $# -eq 0 ]; then set -- $(seq 1 10); fi
for s in "$@"; do "run_$s"; done

echo
if [ "$FAILS" -eq 0 ]; then echo "ПРИЕМКА ЗЕЛЕНАЯ (секции: $*)"; exit 0; fi
echo "ПРИЕМКА КРАСНАЯ: провалов $FAILS (секции: $*)"; exit 1
