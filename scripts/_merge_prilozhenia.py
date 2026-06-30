#!/usr/bin/env python3
"""
Сводный PDF приложений по делу Рахимкуловой — дело № 2-1057/2026.
Порядок: сначала опись (путеводитель), затем каждое приложение.

Для 185226.pdf (68 стр, содержит прил. 5,6,7,9,10,11) — страницы в порядке приложений.
Для 182715.pdf (2 стр, содержит прил. 8 и 12) — разбиты по номерам.
Для 184553.pdf (70 стр, прил. 14) — все страницы.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import pypdf
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

INTAKE = "cases/rakhimkulova/alimenty-izmenenie-2026/00_intake"
DRAFTS = "cases/rakhimkulova/alimenty-izmenenie-2026/03_drafts"
OUT = f"{DRAFTS}/20260630_prilozhenia_svodny.pdf"

# Пытаемся зарегистрировать системный шрифт с поддержкой кириллицы
CYRILLIC_OK = False
for font_path in [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]:
    if os.path.exists(font_path) and font_path.endswith('.ttf'):
        try:
            pdfmetrics.registerFont(TTFont("CyrFont", font_path))
            CYRILLIC_OK = True
            break
        except Exception:
            pass

FONT_BODY = "CyrFont" if CYRILLIC_OK else "Helvetica"


def make_tab_page(number: int, title_ru: str, amount_str: str, source_file: str) -> bytes:
    """Создает одну страницу-разделитель для приложения. Возвращает bytes PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    # Фон — светло-серый
    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(0, 0, w, h, fill=1, stroke=0)

    # Верхняя полоса
    c.setFillColorRGB(0.2, 0.35, 0.6)
    c.rect(0, h - 100, w, 100, fill=1, stroke=0)

    # Номер приложения в полосе
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 36)
    c.drawString(50, h - 68, f"PRILOZHENIE {number}")

    # Заголовок (кириллица если доступно)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont(FONT_BODY, 16)
    # Wrap title_ru at 70 chars per line
    words = title_ru.split()
    lines = []
    cur = ""
    for w_word in words:
        if len(cur) + len(w_word) + 1 > 68:
            lines.append(cur.strip())
            cur = w_word
        else:
            cur += (" " if cur else "") + w_word
    if cur:
        lines.append(cur.strip())

    y = h - 160
    for line in lines:
        c.drawString(50, y, line)
        y -= 24

    # Сумма
    if amount_str:
        c.setFont("Helvetica-Bold", 14)
        c.setFillColorRGB(0.15, 0.45, 0.15)
        c.drawString(50, y - 20, amount_str)

    # Источник
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(50, 60, f"Source: {source_file}")
    c.drawString(50, 44, "Delo 2-1057/2026 | Rakhimkulova A.S. | 30.06.2026")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def tab_reader(tab_bytes: bytes) -> pypdf.PdfReader:
    return pypdf.PdfReader(io.BytesIO(tab_bytes))


def add_tab(writer: pypdf.PdfWriter, number: int, title: str, amount: str, src: str):
    tab = make_tab_page(number, title, amount, src)
    rdr = tab_reader(tab)
    for page in rdr.pages:
        writer.add_page(page)


def add_pages(writer: pypdf.PdfWriter, path: str, pages_1indexed: list | None = None):
    """Добавляет страницы из PDF. pages_1indexed=None → все страницы."""
    rdr = pypdf.PdfReader(path)
    total = len(rdr.pages)
    if pages_1indexed is None:
        idxs = range(total)
    else:
        idxs = [p - 1 for p in pages_1indexed if 1 <= p <= total]
    for i in idxs:
        writer.add_page(rdr.pages[i])


def main():
    w = pypdf.PdfWriter()

    # === ОПИСЬ (путеводитель) ===
    opis_path = f"{DRAFTS}/20260630_prilozhenia_opis.pdf"
    if os.path.exists(opis_path):
        print("Добавляю опись...")
        add_pages(w, opis_path)

    print("Собираю приложения...")

    # === ПРИЛОЖЕНИЕ 1 — Выписка ТБанк ===
    add_tab(w, 1, "Spravka o dvizhenii sredstv AO TBank (01.01.2024-31.01.2025)",
            "673 650,00 rub.", "funds_movement.pdf")
    add_pages(w, f"{INTAKE}/funds_movement.pdf")

    # === ПРИЛОЖЕНИЕ 2 — Договор АНО «Особые дети» ===
    add_tab(w, 2, "Dogovor No 16/24 ot 01.09.2024 ANO Osobye deti Tatarstan",
            "94 000,00 rub./mes.", "20260601183138.pdf")
    add_pages(w, f"{INTAKE}/20260601183138.pdf")

    # === ПРИЛОЖЕНИЕ 3 — Справка + договор «Детки-Конфетки» ===
    add_tab(w, 3, "Spravka + Dogovor Detki-Konfetki (IP Sirazeva G.R.) ot 07.05.2026",
            "1 800,00 rub./chas", "20260601181333.pdf")
    add_pages(w, f"{INTAKE}/20260601181333.pdf")

    # === ПРИЛОЖЕНИЕ 4 — 12 квитанций серии АА ===
    add_tab(w, 4, "12 kvitanciy seriya AA (IP Sirazeva G.R.) - oplata zanyatiy",
            "512 680,00 rub.", "20260601181836.pdf")
    add_pages(w, f"{INTAKE}/20260601181836.pdf")

    # === ПРИЛОЖЕНИЕ 5 — Акт Прогноз 31.05.2025 (диагностика, 27 700 руб.) ===
    # Файл 185226.pdf: стр. 1-12
    # стр. 1-6: консультационные отчёты (Прогноз, 31.05.2025)
    # стр. 7-12: Договор №2384/25 + Акт от 31.05.2025
    add_tab(w, 5, "Akt OOO Prognoz ot 31.05.2025 (diagnostika, Dogovor No 2384/25)",
            "27 700,00 rub.", "20260601185226.pdf [pp.1-12]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(1, 13)))

    # === ПРИЛОЖЕНИЕ 6 — Акты Прогноз, Курс 2 (27.08–10.09.2025) ===
    # стр. 25-37: Договор №3629/25 + 3 акта + кассовые чеки
    add_tab(w, 6, "Akty OOO Prognoz po Dogovoru No 3629/25 (kurs 2: 27.08-10.09.2025)",
            "91 300,00 rub. (3 akta)", "20260601185226.pdf [pp.25-37]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(25, 38)))

    # === ПРИЛОЖЕНИЕ 7 — Акт Логопрогноз 10.09.2025 (160 000 руб.) ===
    # стр. 13-24: Договор №937/25 + Приложение №1 + Акт от 10.09.2025
    add_tab(w, 7, "Akt OOO Logoprognoz ot 10.09.2025 (Dogovor No 937/25 ot 27.08.2025)",
            "160 000,00 rub.", "20260601185226.pdf [pp.13-24]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(13, 25)))

    # === ПРИЛОЖЕНИЕ 8 — Справка Прогноз 10.09.2025 ===
    # 182715.pdf стр. 1
    add_tab(w, 8, "Spravka OOO Prognoz ot 10.09.2025 (kurs reabilitacii 27.08-10.09.2025)",
            "—", "20260601182715.pdf [p.1]")
    add_pages(w, f"{INTAKE}/20260601182715.pdf", [1])

    # === ПРИЛОЖЕНИЕ 9 — Акт Прогноз 15.02.2026 (диагностика, 27 700 руб.) ===
    # стр. 57-58 (акт + кассовый чек) — только 2 страницы, далее идёт App 10
    add_tab(w, 9, "Akt OOO Prognoz ot 15.02.2026 (diagnostika, Dogovor No 5837/26)",
            "27 700,00 rub.", "20260601185226.pdf [pp.57-58]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(57, 59)))

    # === ПРИЛОЖЕНИЕ 10 — Акт Прогноз 02.03.2026 (аудиотерапия + ФБМ, 107 200 руб.) ===
    # стр. 59-68 (перекрытие с прил. 9 по стр. 60-62; договор №5837 на стр. 67-68)
    add_tab(w, 10, "Akt OOO Prognoz ot 02.03.2026 (audioterapiya + FBM, Dogovor No 5837/26)",
            "107 200,00 rub.", "20260601185226.pdf [pp.59-68]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(59, 69)))

    # === ПРИЛОЖЕНИЕ 11 — Акт Логопрогноз 02.03.2026 (144 000 руб.) ===
    # стр. 38-56: Договор №1522/26 (16.02.2026) + Акт от 02.03.2026
    add_tab(w, 11, "Akt OOO Logoprognoz ot 02.03.2026 (Dogovor No 1522/26 ot 16.02.2026)",
            "144 000,00 rub.", "20260601185226.pdf [pp.38-56]")
    add_pages(w, f"{INTAKE}/20260601185226.pdf", list(range(38, 57)))

    # === ПРИЛОЖЕНИЕ 12 — Справка Логопрогноз 02.03.2026 ===
    # 182715.pdf стр. 2
    add_tab(w, 12, "Spravka OOO Logoprognoz ot 02.03.2026 (kurs 14.02-02.03.2026)",
            "—", "20260601182715.pdf [p.2]")
    add_pages(w, f"{INTAKE}/20260601182715.pdf", [2])

    # === ПРИЛОЖЕНИЕ 13 — Авиабилеты (8 штук) ===
    add_tab(w, 13, "Aviabilety (8 sht.) - 3 poezdki Kazan-Sankt-Peterburg-Kazan",
            "72 585,00 rub.", "20260601182413.pdf")
    add_pages(w, f"{INTAKE}/20260601182413.pdf")

    # === ПРИЛОЖЕНИЕ 14 — Медицинские документы ООО «Здоровье Семьи» ===
    add_tab(w, 14, "Medicinskie dokumenty OOO Zdorovye Semyi (diagnozy F84.8, G93.4)",
            "—", "20260601184553.pdf [all 70 pp.]")
    add_pages(w, f"{INTAKE}/20260601184553.pdf")

    # Сохраняем
    os.makedirs(DRAFTS, exist_ok=True)
    with open(OUT, "wb") as f:
        w.write(f)

    size_mb = os.path.getsize(OUT) / 1024 / 1024
    total_pages = sum(1 for _ in pypdf.PdfReader(OUT).pages)
    print(f"\nГотово: {OUT}")
    print(f"Страниц: {total_pages}, размер: {size_mb:.1f} МБ")


if __name__ == "__main__":
    main()
