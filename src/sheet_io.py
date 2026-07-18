# -*- coding: utf-8 -*-
"""Чтение/запись Google Sheets для конвейера «Товари Чернівці».

Раскладка листов (подтверждена на боевой таблице):

  «Входні товари» — данные в колонках A–F:
      A=Код (НФ-XXXXXXXX), B=Наименование, C=Полное наименование,
      D=Свободный остаток, E=единица («шт»), F=опт USD.
      Строки-заголовки групп: A заполнено, B пусто.
      Курс — в одной из верхних ячеек: «Валюта: USD, курс 45,2».

  «Усі товари» — 24 колонки A–X (см. README / шапку строки 1).
"""
import os
import re

import gspread

SHEET_ID = os.environ.get('SHEET_ID', '1mdUY_I0f-qHrkb-vcJbh7BZmjEKLXJCZcqdZZoc0wN0')
INPUT_WS = 'Входні товари'
OUTPUT_WS = 'Усі товари'
LOG_WS = 'Лог'

# служебные заголовки в колонке A, которые не являются товарными группами
_A_NOISE = {'All_Panfilov', 'Код', 'Вхо'}

DEFAULT_RATE = 45.2


# ------------------------------------------------------------------ клиент
def get_client():
    """gspread-клиент из сервисного ключа.

    Ключ: путь в GOOGLE_APPLICATION_CREDENTIALS / GSPREAD_SA_FILE, либо JSON
    в переменной GOOGLE_SERVICE_ACCOUNT_JSON (для GitHub Actions).
    """
    js = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if js:
        import json
        return gspread.service_account_from_dict(json.loads(js))
    path = (os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
            or os.environ.get('GSPREAD_SA_FILE'))
    if path and os.path.exists(path):
        return gspread.service_account(filename=path)
    raise RuntimeError('Не задан сервисный ключ: GOOGLE_SERVICE_ACCOUNT_JSON '
                       'или GOOGLE_APPLICATION_CREDENTIALS/GSPREAD_SA_FILE')


def open_book(gc=None):
    gc = gc or get_client()
    return gc.open_by_key(SHEET_ID)


# ------------------------------------------------------------------ курс
def parse_rate(rows):
    """Достать курс из верхних строк листа. regex «курс 45,2» -> 45.2."""
    for row in rows[:12]:
        for cell in row:
            if cell and 'курс' in str(cell).lower():
                m = re.search(r'курс\s*([\d.,]+)', str(cell))
                if m:
                    try:
                        return float(m.group(1).replace(',', '.'))
                    except ValueError:
                        pass
    return DEFAULT_RATE


def _num(v):
    if v is None or v == '':
        return None
    try:
        return float(str(v).replace(',', '.').replace(' ', ''))
    except ValueError:
        return None


def _cell_to_str(v):
    """Ячейка (UNFORMATTED_VALUE) -> строка с точкой как разделителем.

    Читаем ТОЛЬКО неформатированные значения: иначе локаль таблицы отдаёт
    «3,5» вместо «3.5», а даты — как серийные номера.
    """
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else repr(v)
    if isinstance(v, int):
        return str(v)
    return str(v)


def _get_unformatted(ws):
    """Все значения листа без форматирования, приведённые к строкам."""
    raw = ws.get_values(value_render_option='UNFORMATTED_VALUE')
    return [[_cell_to_str(c) for c in row] for row in raw]


# ------------------------------------------------------------------ вход
def read_input(book):
    """Прочитать «Входні товари». Вернуть (rate, list[record]).

    record = dict(group_raw, code, short, full, qty, usd, row)
    Товары идут в порядке следования; group_raw — имя последнего заголовка группы.
    """
    ws = book.worksheet(INPUT_WS)
    rows = _get_unformatted(ws)
    rate = parse_rate(rows)
    records = []
    current = None
    for i, r in enumerate(rows, 1):
        a = (r[0].strip() if len(r) > 0 and r[0] else '')
        b = (r[1].strip() if len(r) > 1 and r[1] else '')
        # заголовок группы: A заполнено, B пусто
        if a and not b:
            if a == 'All_Panfilov':
                current = None
            else:
                current = a
            continue
        # товарная строка: A=код (НФ-...), B=наименование
        if not (a and b):
            continue
        if a in _A_NOISE or not a.startswith('НФ'):
            continue
        full = (r[2].strip() if len(r) > 2 and r[2] else b)
        qty = _num(r[3]) if len(r) > 3 else None
        usd = _num(r[5]) if len(r) > 5 else None
        records.append(dict(group_raw=current, code=a, short=b, full=full,
                            qty=qty, usd=usd, row=i))
    return rate, records


# ------------------------------------------------------------------ выход (чтение)
def read_output(book):
    """Прочитать «Усі товари». Вернуть (header, rows, index_by_id, raw_rows).

    rows:     list[list[str]] строк данных (без шапки), дополненных до 24 колонок.
    index_by_id: {ID товара (колонка B) -> номер строки на листе (1-based)}.
    raw_rows: те же строки БЕЗ приведения к строкам — чтобы отличать число от
              текста («110.3» текстом должно быть переписано настоящим числом).
    """
    ws = book.worksheet(OUTPUT_WS)
    raw = ws.get_values(value_render_option='UNFORMATTED_VALUE')
    values = [[_cell_to_str(c) for c in row] for row in raw]
    header = values[0] if values else []
    data = values[1:] if len(values) > 1 else []
    raw_data = raw[1:] if len(raw) > 1 else []
    rows = []
    raw_rows = []
    index = {}
    for i, row in enumerate(data, start=2):  # строка 2 — первая с данными
        row = list(row) + [''] * (24 - len(row))
        rows.append(row)
        rr = list(raw_data[i - 2]) if i - 2 < len(raw_data) else []
        raw_rows.append(rr + [''] * (24 - len(rr)))
        pid = row[1].strip()
        if pid:
            index[pid] = i
    return header, rows, index, raw_rows


# ------------------------------------------------------------------ выход (запись)
BATCH_CHUNK = 500


def batch_update(ws, updates):
    """updates: list[(a1_range, [[...values...]])]. Пусто -> ничего не делаем.

    Режем на части: один запрос с тысячами диапазонов упирается в лимиты API.
    """
    if not updates:
        return
    for i in range(0, len(updates), BATCH_CHUNK):
        chunk = updates[i:i + BATCH_CHUNK]
        body = [{'range': rng, 'values': vals} for rng, vals in chunk]
        ws.batch_update(body, value_input_option='USER_ENTERED')


def append_rows(ws, rows):
    if not rows:
        return
    ws.append_rows(rows, value_input_option='USER_ENTERED',
                   insert_data_option='INSERT_ROWS', table_range='A1')


def ensure_checkbox_validation(book, ws, first_row, last_row):
    """Навесить BOOLEAN data-validation (чекбокс) на A{first_row}:A{last_row}."""
    if last_row < first_row:
        return
    req = {
        'setDataValidation': {
            'range': {
                'sheetId': ws.id,
                'startRowIndex': first_row - 1,
                'endRowIndex': last_row,
                'startColumnIndex': 0,
                'endColumnIndex': 1,
            },
            'rule': {
                'condition': {'type': 'BOOLEAN'},
                'strict': True,
                'showCustomUi': True,
            },
        }
    }
    book.batch_update({'requests': [req]})


# ------------------------------------------------------------------ лог
def append_log(book, line_cells):
    """Дописать строку в лист «Лог» (создать при отсутствии)."""
    try:
        ws = book.worksheet(LOG_WS)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=LOG_WS, rows=1000, cols=12)
        ws.append_row(['Дата-время', 'Действие', 'Добавлено', 'Обновлено',
                       'Снято с наличия', 'Нераспознано', 'Новые группы',
                       'Предупреждений', 'Ссылка на фид', 'Примечание'],
                      value_input_option='USER_ENTERED')
    ws.append_row(line_cells, value_input_option='USER_ENTERED')
