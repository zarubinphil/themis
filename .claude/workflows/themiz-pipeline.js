export const meta = {
  name: 'themiz-pipeline',
  description: 'Тонкий проводник конвейера Фемиды. Дает единственное, чего нет ни у одного python-прибора: принудительный порядок фаз и параллельный запуск (карта ∥ охота, три линзы). Ворота НЕ дублирует — зовет их в приборах (themiz_status, document_guard, quality_gate, verdict, хук claude_guard). Аргумент — путь к делу cases/<клиент>/<дело> (строка или {case}).',
  whenToUse: 'Прогон дела по протоколу 0→5. Workflow({ name: "themiz-pipeline", args: "cases/<клиент>/<дело>" })',
  phases: [
    { title: 'Guard', detail: 'themiz_status <дело> + preflight_search — красный код любой команды стопает прогон' },
    { title: 'Census', detail: 'document_guard --rules — перепись требований к документу до начала работы' },
    { title: 'Работа', detail: 'карта → охота (карта первой, реш. владельца 01.09.2026), затем синтез, затем doc-drafter' },
    { title: 'Полнота', detail: 'verdict --preflight прогоняет три прибора и пишет signed-допуск редакции — красный не пускает к рецензии' },
    { title: 'Рецензия', detail: 'три линзы параллельно (форма/нормы/позиция), сводный вердикт один — verdict.py --record' },
    { title: 'Сборка', detail: 'create_docx (DocBuilder) + document_guard docx --md' },
  ],
}

// ── аргумент: путь к делу ────────────────────────────────────────────────
const CASE = (function () {
  if (args && typeof args === 'object' && args.case) return String(args.case).trim()
  const t = String(args || '').trim()
  if (t.charAt(0) === '{') { try { const p = JSON.parse(t); if (p && p.case) return String(p.case).trim() } catch (e) {} }
  return t
})()
if (!CASE) throw new Error('themiz-pipeline: не задан путь к делу (args = "cases/<клиент>/<дело>")')


// ── ворота: команда выполняется КОДОМ, не моделью ────────────────────────
// Гейт, который гоняет команду через агента, — это снова advisory: модель может
// не выполнить, переврать вывод или сочинить код возврата. Урок Мнемозины прямой:
// детерминированные ворота живут в коде. spawnSync стоит 0 токенов и не лжет.
const childProcess = await import('node:child_process')
const fs = await import('node:fs/promises')
const path = await import('node:path')
const crypto = await import('node:crypto')
const gate = function (cmd, label, phase) {
  const r = childProcess.spawnSync('/bin/sh', ['-c', cmd], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })
  const code = r.status == null ? 1 : r.status
  const out = (r.stdout || '') + (r.stderr || '')
  log('[' + phase + '] ' + label + ' → код ' + code)
  if (code !== 0) throw new Error('[' + phase + '] красный код (' + code + '): ' + cmd + '\n' + out.slice(-800))
  return out
}
const budgetGate = function (phase) {
  return gate('python3 scripts/themiz_status.py ' + JSON.stringify(CASE), 'budget/status', phase)
}

// Файл прогона (M01): единственный адрес держит case_paths.py — второго не заводим.
// Проводник стампует свой запуск и код preflight; сторож claude_guard читает их и
// блокирует прямой (мимо проводника) спавн ключевых агентов дела. Ворота в самом
// проводнике не ставим — только отметки; исполнение держит сторож на спавне.
const runSet = function (key, value) {
  childProcess.spawnSync('python3',
    ['scripts/case_paths.py', '--run-set', CASE, key, JSON.stringify(value)],
    { encoding: 'utf8' })
}

const DRAFTS_DIR = path.join(CASE, '.agent', 'drafts')
const OWNER_FILE = path.join(DRAFTS_DIR, '.owner')
const OWNER_TOKEN = crypto.randomUUID()
const acquireDraftsOwner = async function () {
  await fs.mkdir(DRAFTS_DIR, { recursive: true })
  process.env.THEMIZ_DRAFTS_OWNER = OWNER_TOKEN
  try {
    await fs.writeFile(OWNER_FILE, 'themiz-pipeline token=' + OWNER_TOKEN + ' at=' + new Date().toISOString() + '\n',
      { encoding: 'utf8', flag: 'wx' })
  } catch (e) {
    if (e && e.code === 'EEXIST') {
      const who = await fs.readFile(OWNER_FILE, 'utf8').catch(() => '(лок не прочитан)')
      throw new Error('[Работа] черновики уже заперты: ' + who.trim())
    }
    throw e
  }
}
const releaseDraftsOwner = async function () {
  const who = await fs.readFile(OWNER_FILE, 'utf8').catch(() => '')
  if (who.includes('token=' + OWNER_TOKEN)) await fs.rm(OWNER_FILE, { force: true })
  else log('Owner lock не снят: файл уже принадлежит другому процессу')
}

// ═══ Guard ═══ порядок держит themiz_status, доступность сервисов — preflight
phase('Guard')
log('Guard: корпус, расход, дело, сервисы')
// Отметка запуска проводника ложится ПЕРВОЙ и стирает вчерашнее решение по preflight:
// каждый прогон решает заново. Даже если Guard дальше упадет — отметка + код preflight
// уже на диске, и прямой спавн охотника поймается сторожем.
runSet('guide', 'themiz-pipeline')
runSet('preflight_override', null)
budgetGate('Guard')
// Указатель дела ставится ПЕРЕД preflight: его читают preflight, practice_search и
// хук claude_guard. Хук запускается харнессом и среды проводника не видит, поэтому
// переменной окружения тут мало — нужен указатель на диске (M07, 02.09.2026).
childProcess.spawnSync('/bin/sh', ['-c',
  'python3 -c "import sys;sys.path.insert(0,\'scripts\');import channels;channels.zapomnit_delo(sys.argv[1])" ' +
  JSON.stringify(CASE)], { encoding: 'utf8' })
const _pf = childProcess.spawnSync('/bin/sh', ['-c',
  'python3 scripts/preflight_search.py --case ' + JSON.stringify(CASE)],
  { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, env: Object.assign({}, process.env, { THEMIZ_CASE: CASE }) })
const _pfCode = _pf.status == null ? 1 : _pf.status
runSet('preflight_code', _pfCode)
log('[Guard] guard:preflight → код ' + _pfCode)
if (_pfCode !== 0) throw new Error('[Guard] preflight_search красный код (' + _pfCode +
  '): внешних каналов поиска нет.\n' + ((_pf.stdout || '') + (_pf.stderr || '')).slice(-800))

// ═══ Census ═══ прибор печатает свод правил к документу ДО работы
phase('Census')
budgetGate('Census')
log('Census: перепись требований к документу')
// Оба гейта печатают правила до работы: документный формат и механика качества.
const DOC_RULES = gate('python3 scripts/document_guard.py --rules', 'census:doc-rules', 'Census')
const QUALITY_RULES = gate('python3 scripts/quality_gate.py --rules', 'census:quality-rules', 'Census')

// ═══ Работа ═══ КАРТА → затем охота. Решение владельца 01.09.2026 отменило параллель:
// поиск практики бессмыслен, пока нет карты дела (исключений нет). Сторож это же держит
// на спавне охотника; здесь порядок явный, чтобы охотник получил узлы из готовой карты.
phase('Работа')
budgetGate('Работа')
log('Работа: карта → охота → синтез → черновик')
await agent(
  'Построй карту дела для ' + CASE + ' по своему протоколу (FAST: читаешь материалы сам; ' +
  'готовый OCR не перераспознавать). Итог — .agent/context/knowledge-map.md с маркером «## КАРТА ГОТОВА ✓».',
  { label: 'case-mapper', phase: 'Работа', agentType: 'case-mapper' }
)
await agent(
  'Найди судебную практику по процессуальным узлам дела ' + CASE + ' (карта готова — бери узлы из ' +
  '.agent/context/knowledge-map.md). Сначала грепом knowledge/practice_index.md, затем ' +
  'scripts/practice_search.py. Итог — hunter_tactical.md с ≥3 источниками ' +
  '(формат «Постановление ВС РФ от ДД.ММ.ГГГГ № X»).',
  { label: 'hunter-tactical', phase: 'Работа', agentType: 'practice-hunter-tactical', model: 'sonnet' }
)

// синтез practice.md/positions.md — Opus (FAST-синтез Фемиды, маркеры FAST-*)
await agent(
  'Синтез по делу ' + CASE + '. Из knowledge-map.md и hunter_tactical.md собери:\n' +
  '· .agent/context/practice.md с маркером «## FAST-СИНТЕЗ ФЕМИДЫ»\n' +
  '· .agent/context/positions.md с маркером «## FAST-ПОЗИЦИЯ ФЕМИДЫ»\n' +
  'Долю выводи ПО КАЖДОМУ ОБЪЕКТУ отдельно. Совет не созывать, «СОГЛАСОВАНО СОВЕТОМ» не ставить.',
  { label: 'synthesis', phase: 'Работа', model: 'opus' }
)

// составление — doc-drafter (его frontmatter пинит модель; на L1/MICRO звать sonnet явно снаружи)
await acquireDraftsOwner()
try {
let DRAFT = ''
DRAFT = String(await agent(
    'Составь процессуальный документ по делу ' + CASE + ' строго по скиллу doc-drafter ' +
    '(карта, practice.md, positions.md готовы). .docx только через DocBuilder. ' +
    'Каждое правовое утверждение с источником в тексте.\n' +
    '\n[ПРАВИЛА DOCUMENT_GUARD]\n' + DOC_RULES.slice(0, 12000) +
    '\n\n[ПРАВИЛА QUALITY_GATE]\n' + QUALITY_RULES.slice(0, 4000) +
    '\n\n' +
    'ПОСЛЕДНЕЙ строкой ответа напечатай ровно: ___DRAFT:<путь к .md черновику>',
    { label: 'doc-drafter', phase: 'Работа', agentType: 'doc-drafter' }
  ) || '')
const DRAFT_MD = (function () {
  const m = /___DRAFT:\s*(\S+)/.exec(DRAFT)
  if (!m) throw new Error('[Работа] doc-drafter не вернул путь к черновику')
  return m[1]
})()
log('Черновик: ' + DRAFT_MD)

// ═══ Полнота ═══ три прибора + signed-допуск текущей редакции до рецензии
phase('Полнота')
budgetGate('Полнота')
log('Полнота: черновик против приборов — красный не пускает к рецензии')
const Q = JSON.stringify(DRAFT_MD)
gate('python3 scripts/verdict.py --preflight ' + Q, 'complete:preflight', 'Полнота')

// ═══ Рецензия ═══ три линзы параллельно, зоны не пересекаются
phase('Рецензия')
budgetGate('Рецензия')
log('Рецензия: форма ∥ нормы ∥ позиция → один сводный вердикт')
const lenses = await parallel([
  () => agent(
    'ЛИНЗА ФОРМА И ЦИФРЫ. Отвечаешь только за Б1, Б4(числа), Б5, Б6. Читай вывод уже ' +
    'пройденных приборов и при необходимости повтори python3 scripts/document_guard.py --md-only ' +
    Q + '. Верни замечания с местом/правкой/источником или «форма чиста».',
    { label: 'lens:form', phase: 'Рецензия', model: 'haiku' }
  ),
  () => agent(
    'ЛИНЗА НОРМЫ. Отвечаешь только за Б3: дословность цитат, существование ссылок и ' +
    'смысловые границы пересказов. Каждую ссылку и цитату в ' + DRAFT_MD +
    ' сверь с корпусом через python3 scripts/cite.py; пересказ нормы не должен быть шире ' +
    'текста корпуса. Верни точные расхождения или поименный список несверенного.',
    { label: 'lens:norms', phase: 'Рецензия', model: 'sonnet' }
  ),
  () => agent(
    'ЛИНЗА ПОЗИЦИЯ. Отвечаешь за Б2, Б4(смысл), Б7. Сверь ' + DRAFT_MD +
    ' с .agent/context/knowledge-map.md, practice.md и positions.md: закрыты ли факты, требования, ' +
    'доводы и блокеры, нет ли сужения позиции. Верни расхождения или «позиция выдержана».',
    { label: 'lens:position', phase: 'Рецензия', model: 'opus' }
  ),
])
// сводный вердикт один; полный контракт Кони применяет именно doc-reviewer.
// Проводник — санкционированный оркестратор рецензии: открываем сторожу его спавн
// doc-reviewer токеном (тот же механизм env, что и THEMIZ_DRAFTS_OWNER). Прямой
// doc-reviewer из главного потока токена не несет и остается заблокирован.
process.env.THEMIZ_DOC_REVIEWER_OK = '1'
try {
await agent(
  'Сводный вердикт по черновику ' + DRAFT_MD + '. Три линзы вернули:\n\n' +
  '[ФОРМА]\n' + String(lenses[0] || '—') + '\n\n[НОРМЫ]\n' + String(lenses[1] || '—') +
  '\n\n[ПОЗИЦИЯ]\n' + String(lenses[2] || '—') + '\n\n' +
  'Ты Кони/doc-reviewer: примени полный контракт роли, семь блоков Б1-Б7, словарь вердиктов ' +
  'и формат отчета. Сформируй один вердикт и запиши его прибором: посмотри python3 scripts/verdict.py --help, ' +
  'затем вызови verdict.py с --record для ' + DRAFT_MD + '. Лимит раундов держит сам прибор.',
  { label: 'verdict', phase: 'Рецензия', agentType: 'doc-reviewer', model: 'opus' }
)
} finally {
  delete process.env.THEMIZ_DOC_REVIEWER_OK
}

// ═══ Сборка ═══ финальный .docx и его проверка формой
phase('Сборка')
budgetGate('Сборка')
log('Сборка: DocBuilder → document_guard docx --md')
const DOCX = await agent(
  'Собери финальный .docx из ' + DRAFT_MD + ' через DocBuilder (scripts/create_docx.py). ' +
  'ПОСЛЕДНЕЙ строкой напечатай ровно: ___DOCX:<путь к .docx>',
  { label: 'build:docx', phase: 'Сборка', model: 'haiku' }
)
const DOCX_PATH = (function () {
  const m = /___DOCX:\s*(\S+)/.exec(String(DOCX || ''))
  if (!m) throw new Error('[Сборка] create_docx не вернул путь к .docx')
  return m[1]
})()
gate('python3 scripts/document_guard.py ' + JSON.stringify(DOCX_PATH) + ' --md ' + Q, 'build:docx-guard', 'Сборка')

log('Конвейер пройден: ' + DOCX_PATH)
return { case: CASE, draft: DRAFT_MD, docx: DOCX_PATH }
} finally {
  await releaseDraftsOwner()
  delete process.env.THEMIZ_DRAFTS_OWNER
}
