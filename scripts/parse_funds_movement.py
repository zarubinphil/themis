#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse T-Bank funds movement statement from markitdown cache."""

import re
from decimal import Decimal
from pathlib import Path


def parse_amount(s: str) -> Decimal:
    s_clean = s.replace('₽', '').replace(' ', '').replace(',', '.').replace('+', '').replace('−', '-')
    return Decimal(s_clean)


def is_date(s: str) -> bool:
    return bool(re.match(r'^\d{2}\.\d{2}\.\d{4}$', s))


def clean_desc(s: str) -> str:
    s = re.sub(r'\s+\d{4}$', '', s)
    s = re.sub(r'\bоперации\s+карты\b', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def split_plain_line(line: str) -> list[str]:
    """Split a plain-text line containing multiple operations into individual strings."""
    amount_re = r'[+-]?\s*[\d\s]+[\,\.]\d{2}'
    op_start_re = re.compile(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}\.\d{2}\.\d{4}\s+' + amount_re + r'\s*₽\s+' + amount_re + r'\s*₽')
    starts = [m.start() for m in op_start_re.finditer(line)]
    if not starts:
        return [line] if line.strip() else []
    parts = []
    for i in range(len(starts)):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else len(line)
        parts.append(line[start:end].strip())
    return parts


def normalize_statement(path: str) -> list[str]:
    """Return a list of markdown-style rows ready for uniform parsing."""
    raw_lines = Path(path).read_text(encoding='utf-8').splitlines()
    normalized: list[str] = []

    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith('АО «ТБанк»') or s.startswith('БИК ') or s.startswith('С уважением') or s.startswith('Руководитель'):
            continue
        if re.match(r'^\d+$', s):
            continue
        if any(h in s for h in ['Дата и время', 'Дата Сумма в валюте', 'Сумма операции Описание Номер', 'операции   списания', 'операции списания операции']):
            continue
        if re.match(r'^\|?\s*[-:]+\s*\|', s):
            continue

        # Phone number lines pass through unchanged (will be handled later)
        if re.match(r'^\+7\d{10}$', s):
            normalized.append('__PHONE__' + s)
            continue

        # Plain text line that contains operations -> convert each to markdown row
        if not s.startswith('|'):
            if re.match(r'^\d{2}\.\d{2}\.\d{4}\s+\d{2}\.\d{2}\.\d{4}\s+[+-]?\s*[\d\s]+[\,\.]\d{2}\s*₽', s):
                amount_re = r'([+-]?\s*[\d\s]+[\,\.]\d{2})\s*₽'
                op_re = re.compile(
                    r'^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}\.\d{2}\.\d{4})\s+' + amount_re + r'\s+' + amount_re + r'\s+(.*)$'
                )
                for op_str in split_plain_line(s):
                    m = op_re.match(op_str)
                    if not m:
                        continue
                    op_date, debit_date, amount1, amount2, desc = m.groups()
                    row = f"| {op_date} | {debit_date} | {amount1} ₽ | {amount2} ₽ | {clean_desc(desc)} | |"
                    normalized.append(row)
                continue
            # Non-operation plain text continuation
            normalized.append('__CONT__' + s)
            continue

        # Markdown table line passes through
        normalized.append(s)

    return normalized


def parse_statement(path: str) -> list[dict]:
    lines = normalize_statement(path)
    operations: list[dict] = []
    current: dict | None = None

    def flush_current():
        nonlocal current
        if current:
            operations.append(current)
            current = None

    for s in lines:
        if s.startswith('__PHONE__'):
            phone = s[len('__PHONE__'):]
            if current:
                current['phone'] = phone
            continue

        if s.startswith('__CONT__'):
            text = s[len('__CONT__'):]
            if current:
                current['description'] = (current['description'] + ' ' + text).strip()
            continue

        # Markdown table line
        cells = [c.strip() for c in s[1:-1].split('|')]
        while len(cells) < 6:
            cells.append('')
        op_date, debit_date, amount1, amount2, desc, card = cells[:6]

        amount_str = amount1 or amount2
        has_amount = amount_str and '₽' in amount_str

        if has_amount:
            flush_current()
            current = {
                'op_date': op_date if is_date(op_date) else '',
                'debit_date': debit_date if is_date(debit_date) else '',
                'amount': parse_amount(amount_str),
                'description': clean_desc(desc.strip()),
                'phone': None,
                'card': card.strip()
            }
        elif current:
            if is_date(op_date) and current['op_date'] == '':
                current['op_date'] = op_date
                current['debit_date'] = debit_date
            if desc:
                current['description'] = (current['description'] + ' ' + clean_desc(desc)).strip()
            if card and not re.match(r'^\d{4}$', card):
                current['description'] = (current['description'] + ' ' + clean_desc(card)).strip()

    flush_current()
    return operations


if __name__ == '__main__':
    import sys
    ops = parse_statement(sys.argv[1])
    income = sum(o['amount'] for o in ops if o['amount'] > 0)
    expense = sum(o['amount'] for o in ops if o['amount'] < 0)
    print(f'Total operations: {len(ops)}')
    print(f'Income: {income}')
    print(f'Expense: {expense}')
