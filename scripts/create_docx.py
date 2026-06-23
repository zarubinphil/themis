#!/usr/bin/env python3
"""
Создание судебного документа по эталону форматирования шаблон.docx.
Используется из doc-drafter. Все параметры верифицированы по XML шаблона.

Использование:
    from scripts.create_docx import DocBuilder
    b = DocBuilder()
    b.add_title("ИСКОВОЕ ЗАЯВЛЕНИЕ")
    b.add_subtitle("о взыскании неосновательного обогащения")
    b.add_section("I. ОБСТОЯТЕЛЬСТВА ДЕЛА")
    b.add_body("Текст абзаца...")
    b.add_proshyu()
    b.add_request_item("1. Взыскать с ответчика...")
    b.add_appendices()
    b.add_appendix_item("1. Договор от 01.01.2024")
    b.add_signature("Иванов Иван Иванович", "27.05.2026")
    b.save("путь/к/файлу.docx")
"""

from docx import Document
from docx.shared import Pt, Cm, Mm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


FONT = "Times New Roman"


def _set_font(run, size_pt, bold=False):
    run.font.name = FONT
    run.font.size = Pt(size_pt)
    run.font.bold = bold


def _set_docdefaults_line_spacing(doc):
    """Устанавливает межстрочный интервал 1.15× в docDefaults (как в шаблоне)."""
    settings = doc.settings.element
    # Найти или создать docDefaults
    docDefaults = settings.find(qn("w:docDefaults"))
    if docDefaults is None:
        # docDefaults должен быть в styles, не settings
        pass

    styles_elem = doc.styles.element
    docDefaults = styles_elem.find(qn("w:docDefaults"))
    if docDefaults is None:
        docDefaults = OxmlElement("w:docDefaults")
        styles_elem.insert(0, docDefaults)

    pPrDefault = docDefaults.find(qn("w:pPrDefault"))
    if pPrDefault is None:
        pPrDefault = OxmlElement("w:pPrDefault")
        docDefaults.append(pPrDefault)

    pPr = pPrDefault.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        pPrDefault.append(pPr)

    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)

    spacing.set(qn("w:line"), "276")
    spacing.set(qn("w:lineRule"), "auto")


class DocBuilder:
    """Построитель судебного документа по эталону шаблон.docx."""

    def __init__(self):
        self.doc = Document()
        section = self.doc.sections[0]
        section.page_width    = Mm(210)
        section.page_height   = Mm(297)
        section.top_margin    = Mm(20)
        section.bottom_margin = Mm(30)   # зона штампа экспедиции суда (эталон DOCX_FORMATTING.md)
        section.left_margin   = Mm(30)
        section.right_margin  = Mm(15)
        _set_docdefaults_line_spacing(self.doc)
        # Удалить дефолтный пустой параграф
        for p in self.doc.paragraphs:
            p._element.getparent().remove(p._element)

    def add_empty(self):
        """Пустой параграф-разделитель."""
        self.doc.add_paragraph()

    def add_title(self, text):
        """Главный заголовок документа: 14pt, bold, CENTER, sb=6, sa=4."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after  = Pt(4)
        _set_font(p.add_run(text), 14, bold=True)
        return p

    def add_subtitle(self, text):
        """Подзаголовок: 13pt, bold, CENTER, sa=4."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(4)
        _set_font(p.add_run(text), 13, bold=True)
        return p

    def add_header_date(self, text):
        """Дата/время шапки: 13pt, bold, CENTER, sa=12."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(12)
        _set_font(p.add_run(text), 13, bold=True)
        return p

    def add_section(self, text):
        """Заголовок раздела I/II/III: 12pt, bold, JUSTIFY, sb=12, sa=6."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after  = Pt(6)
        _set_font(p.add_run(text), 12, bold=True)
        return p

    def add_subsection(self, text):
        """Нумерованный подраздел: 12pt, bold, JUSTIFY, sb=6, sa=3."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after  = Pt(3)
        _set_font(p.add_run(text), 12, bold=True)
        return p

    def add_body(self, parts):
        """
        Основной текст: 12pt, JUSTIFY, fi=1.25cm, sa=6.
        parts: str или list[(text, bold)].
        """
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before       = Pt(0)
        p.paragraph_format.space_after        = Pt(6)
        p.paragraph_format.first_line_indent  = Cm(1.25)
        if isinstance(parts, str):
            _set_font(p.add_run(parts), 12)
        else:
            for text, bold in parts:
                _set_font(p.add_run(text), 12, bold=bold)
        return p

    def add_body_spaced(self, parts):
        """Текст после маркированного списка: sb=6 sa=6 fi=1.25cm."""
        p = self.add_body(parts)
        p.paragraph_format.space_before = Pt(6)
        return p

    def add_bullet(self, text):
        """Маркированный список: List Bullet, li=1.5cm, sb=2, sa=2."""
        p = self.doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after  = Pt(2)
        p.paragraph_format.left_indent  = Cm(1.50)
        _set_font(p.add_run(text), 12)
        return p

    def add_quote(self, text):
        """Блок-цитата (дословная норма/формула): JUSTIFY, отступы 1.25 см
        слева и справа, без первой строки, 11pt, sb=6, sa=6.

        Эталон CONTENT_DESIGN.md: единственный правильный контейнер для
        дословного текста нормы закона, судебного акта или формулы (40+ слов).
        Отступ служит маркером цитаты — кавычки не нужны.
        """
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.left_indent       = Cm(1.25)
        p.paragraph_format.right_indent      = Cm(1.25)
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_before      = Pt(6)
        p.paragraph_format.space_after       = Pt(6)
        _set_font(p.add_run(text), 11)
        return p

    def add_proshyu(self):
        """Заголовок ПРОШУ: CENTER, bold, 12pt, sa=6."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)
        _set_font(p.add_run("ПРОШУ:"), 12, bold=True)
        return p

    def add_request_item(self, text):
        """Пункт просительной части: List Paragraph, JUSTIFY, sb=2, sa=2."""
        p = self.doc.add_paragraph(style="List Paragraph")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after  = Pt(2)
        _set_font(p.add_run(text), 12)
        return p

    def add_appendices(self):
        """Заголовок ПРИЛОЖЕНИЯ: JUSTIFY, bold, 12pt, sb=12, sa=6."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after  = Pt(6)
        _set_font(p.add_run("ПРИЛОЖЕНИЯ:"), 12, bold=True)
        return p

    def add_appendix_item(self, text):
        """Пункт приложений: List Paragraph, JUSTIFY, sa=12."""
        p = self.doc.add_paragraph(style="List Paragraph")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after  = Pt(12)
        _set_font(p.add_run(text), 12)
        return p

    def add_signature(self, name, date, gap_spaces=40):
        """
        Строка подписи: ФИО + пробелы + дата в одном параграфе.
        RIGHT, sb=6, sa=6. (Правило форматирования: подпись справа.)
        """
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after  = Pt(6)
        _set_font(p.add_run(name), 12)
        _set_font(p.add_run(" " * gap_spaces), 12)
        _set_font(p.add_run(date), 12)
        return p

    def add_signature_table(self, role, name, date=None):
        """
        Блок подписи 3-колоночной таблицей без границ (ГОСТ Р 7.0.97-2016 п. 5.22).

        [Роль/Должность]   [пробел для подписи]   [И. О. Фамилия]
        Устойчиво к открытию в LibreOffice и Google Docs (в отличие от подписи
        пробелами). Дата (если задана) — отдельным параграфом под таблицей, LEFT.
        """
        table = self.doc.add_table(rows=1, cols=3)

        tbl = table._tbl
        tblPr = tbl.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            tbl.insert(0, tblPr)
        tblBorders = OxmlElement("w:tblBorders")
        for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "none")
            tblBorders.append(el)
        tblPr.append(tblBorders)

        tblGrid = OxmlElement("w:tblGrid")
        for w in ["5000", "2339", "2000"]:
            col = OxmlElement("w:gridCol"); col.set(qn("w:w"), w)
            tblGrid.append(col)
        tbl.insert(1, tblGrid)

        row = table.rows[0]
        c0 = row.cells[0]
        c0.paragraphs[0].clear()
        p0 = c0.paragraphs[0]
        p0.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _set_font(p0.add_run(role), 12)
        c1 = row.cells[1]
        c1.paragraphs[0].clear()
        c1.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        c2 = row.cells[2]
        c2.paragraphs[0].clear()
        p2 = c2.paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _set_font(p2.add_run(name), 12)

        if date:
            pd = self.doc.add_paragraph()
            pd.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pd.paragraph_format.space_before = Pt(6)
            pd.paragraph_format.space_after  = Pt(6)
            _set_font(pd.add_run(date), 12)
        return table

    def add_final_empty(self):
        """Последний пустой параграф: JUSTIFY, sb=6, sa=0."""
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after  = Pt(0)
        return p

    def add_addressee_table(self, blocks):
        """
        Адресная шапка досудебного документа (КОМУ / ОТ) — плавающая таблица
        в правой части листа, без границ. В отличие от add_header_table не
        создаёт служебных строк суда и дела.

        blocks: list[dict] с ключами:
            label: «КОМУ:» / «ОТ:»
            lines: list[(text, bold)]
        """
        table = self.doc.add_table(rows=len(blocks), cols=2)

        tbl = table._tbl
        tblPr = tbl.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            tbl.insert(0, tblPr)

        tblBorders = OxmlElement("w:tblBorders")
        for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "none")
            tblBorders.append(el)
        tblPr.append(tblBorders)

        tblGrid = OxmlElement("w:tblGrid")
        col1 = OxmlElement("w:gridCol"); col1.set(qn("w:w"), "1400")
        col2 = OxmlElement("w:gridCol"); col2.set(qn("w:w"), "7939")
        tblGrid.append(col1); tblGrid.append(col2)
        tbl.insert(1, tblGrid)

        tblpPr = OxmlElement("w:tblpPr")
        tblpPr.set(qn("w:horzAnchor"), "margin")
        tblpPr.set(qn("w:vertAnchor"), "text")
        tblpPr.set(qn("w:tblpY"), "-325")
        tblPr.insert(0, tblpPr)

        for i, block in enumerate(blocks):
            row = table.rows[i]
            lc = row.cells[0]
            lc.paragraphs[0].clear()
            lp = lc.paragraphs[0]
            lp.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _set_font(lp.add_run(block["label"]), 12, bold=True)
            rc = row.cells[1]
            rc.paragraphs[0].clear()
            first = True
            for text, bold in block["lines"]:
                if first:
                    p = rc.paragraphs[0]
                    first = False
                else:
                    p = rc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                _set_font(p.add_run(text), 12, bold=bold)
        return table

    def add_header_table(self, court_name, court_route, parties, case_number, instance=None):
        """
        Шапка-реквизиты: плавающая таблица 2×N без границ.

        court_name: str — «ВЕРХОВНЫЙ СУД РЕСПУБЛИКИ ТАТАРСТАН»
        court_route: str — «через Вахитовский районный суд г. Казани»
        parties: list[dict] с ключами:
            label: «ИСТЕЦ:» / «ОТВЕТЧИКИ:»
            lines: list[(text, bold)]
        case_number: str — «Дело № 2-5612/2025»
        instance: str — «Суд первой инстанции: ...» (опционально)
        """
        rows_count = 1 + len(parties) + 1  # суд + стороны + дело
        table = self.doc.add_table(rows=rows_count, cols=2)

        # Убрать все границы
        tbl = table._tbl
        tblPr = tbl.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            tbl.insert(0, tblPr)

        tblBorders = OxmlElement("w:tblBorders")
        for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "none")
            tblBorders.append(el)
        tblPr.append(tblBorders)

        # Ширины колонок
        tblGrid = OxmlElement("w:tblGrid")
        col1 = OxmlElement("w:gridCol"); col1.set(qn("w:w"), "3539")
        col2 = OxmlElement("w:gridCol"); col2.set(qn("w:w"), "5800")
        tblGrid.append(col1); tblGrid.append(col2)
        tbl.insert(1, tblGrid)

        # Плавающая позиция (floating)
        tblpPr = OxmlElement("w:tblpPr")
        tblpPr.set(qn("w:horzAnchor"), "margin")
        tblpPr.set(qn("w:vertAnchor"), "text")
        tblpPr.set(qn("w:tblpY"), "-325")
        tblPr.insert(0, tblpPr)

        # Строка 0: суд
        row0 = table.rows[0]
        row0.cells[0].text = ""
        c = row0.cells[1]
        c.paragraphs[0].clear()
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _set_font(p.add_run(court_name), 12, bold=True)
        if court_route:
            p2 = c.add_paragraph()
            p2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _set_font(p2.add_run(court_route), 12)

        # Строки сторон
        for i, party in enumerate(parties):
            row = table.rows[1 + i]
            # Метка слева
            lc = row.cells[0]
            lc.paragraphs[0].clear()
            lp = lc.paragraphs[0]
            lp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _set_font(lp.add_run(party["label"]), 12, bold=True)
            # Реквизиты справа
            rc = row.cells[1]
            rc.paragraphs[0].clear()
            first = True
            for text, bold in party["lines"]:
                if first:
                    p = rc.paragraphs[0]
                    first = False
                else:
                    p = rc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                _set_font(p.add_run(text), 12, bold=bold)

        # Последняя строка: дело
        last_row = table.rows[-1]
        last_row.cells[0].text = ""
        c = last_row.cells[1]
        c.paragraphs[0].clear()
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _set_font(p.add_run(case_number), 12)
        if instance:
            p2 = c.add_paragraph()
            p2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _set_font(p2.add_run(instance), 12)

        return table

    def _strip_yo(self):
        """Заменяет ё→е, Ё→Е во всех текстовых узлах (правило проекта «нет ё»)."""
        for t in self.doc.element.iter(qn("w:t")):
            if t.text and ("ё" in t.text or "Ё" in t.text):
                t.text = t.text.replace("ё", "е").replace("Ё", "Е")

    def save(self, path):
        """Сохранить документ. Перед записью — авто-стрип буквы ё.

        Плюс неизменяемый снимок-черновик в `_baselines/` рядом — база «ДО» для
        самообучения по правкам доверителя (redline). Снимок = последняя выданная
        версия; правки доверителя сравниваются именно с ней.
        """
        self._strip_yo()
        self.doc.save(path)
        print(f"Сохранено: {path}")
        try:
            import shutil
            from pathlib import Path as _P
            p = _P(path)
            # снимок только для реальных документов дел (не тест/tmp), без рекурсии
            if p.parent.name != "_baselines" and ("cases" in p.parts or "03_drafts" in p.parts):
                bdir = p.parent / "_baselines"
                bdir.mkdir(exist_ok=True)
                shutil.copy2(path, bdir / p.name)  # перезапись: baseline = свежая версия
        except OSError:
            pass


if __name__ == "__main__":
    # Тест-пример
    b = DocBuilder()
    b.add_header_table(
        court_name="ВЕРХОВНЫЙ СУД РЕСПУБЛИКИ ТАТАРСТАН",
        court_route="через Вахитовский районный суд г. Казани",
        parties=[
            {
                "label": "ИСТЕЦ:",
                "lines": [("Иванов Иван Иванович", True), ("адрес: г. Казань...", False)]
            },
            {
                "label": "ОТВЕТЧИК:",
                "lines": [("ООО «Ответчик»", True), ("ИНН: 1234567890", False)]
            },
        ],
        case_number="Дело № 2-0001/2026",
        instance="Суд первой инстанции: Вахитовский р.с. г. Казани"
    )
    b.add_empty()
    b.add_title("ИСКОВОЕ ЗАЯВЛЕНИЕ")
    b.add_subtitle("о взыскании неосновательного обогащения")
    b.add_header_date("27 мая 2026 года")
    b.add_empty()
    b.add_section("I. ОБСТОЯТЕЛЬСТВА ДЕЛА")
    b.add_body([("В 2024 году истец ", False), ("передал ответчику денежные средства", True), (" в сумме...", False)])
    b.add_section("II. ПРАВОВОЕ ОБОСНОВАНИЕ")
    b.add_subsection("1. Нормы применимого права")
    b.add_body("В соответствии с положениями гражданского законодательства...")
    b.add_proshyu()
    b.add_request_item("1. Взыскать с ответчика сумму неосновательного обогащения...")
    b.add_appendices()
    b.add_appendix_item("1. Квитанция об уплате государственной пошлины.")
    b.add_signature("Иванов Иван Иванович", "27.05.2026")
    b.add_final_empty()
    b.save("/tmp/test_doc.docx")
