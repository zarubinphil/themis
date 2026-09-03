# scripts/sreda.sh — переходный период имени в переменных окружения (пара к sreda.py).
#
# Новое имя читается первым, прежнее принимается запасным и печатает
# предупреждение. Подключать после `cd` в корень проекта:
#     . scripts/sreda.sh
# Файл только подключают (`source`), запускать его отдельно незачем.
sreda_perenos() {
  # Прежний префикс — из нового заменой буквы, литералом не пишем: массовая
  # замена имени по дереву не должна съесть запасной путь.
  sreda_prezhnij=$(printf '%s' 'THEMIZ_' | tr 'Z' 'S')
  sreda_perenesli=''
  for sreda_imya in $(env | sed -n "s/^\\(${sreda_prezhnij}[A-Z0-9_]*\\)=.*/\\1/p"); do
    # Имя из вывода env может оказаться подделкой: значение чужой переменной
    # с переводом строки внутри выглядит как отдельная строка «ИМЯ=...».
    # Берем только то, что и правда задано в окружении.
    eval "sreda_est=\${$sreda_imya+x}"
    [ "$sreda_est" = x ] || continue
    sreda_hvost=${sreda_imya#"$sreda_prezhnij"}
    # Признак заданности, а не служебное значение: THEMIZ_X со значением
    # «__net__» задан так же по-настоящему, как любой другой.
    eval "sreda_novoe=\${THEMIZ_$sreda_hvost+x}"
    [ "$sreda_novoe" = x ] && continue
    eval "sreda_znachenie=\$$sreda_imya"
    export "THEMIZ_$sreda_hvost=$sreda_znachenie"
    sreda_perenesli="$sreda_perenesli $sreda_imya"
  done
  if [ -n "$sreda_perenesli" ]; then
    # Печать не имеет права уронить вызывающий скрипт под set -e.
    printf '%s\n' "предупреждение: прежние имена переменных приняты запасным путем:$sreda_perenesli — новые имена начинаются с THEMIZ_" >&2 || true
  fi
  unset sreda_prezhnij sreda_perenesli sreda_imya sreda_hvost sreda_novoe sreda_znachenie sreda_est
  return 0
}
sreda_perenos
