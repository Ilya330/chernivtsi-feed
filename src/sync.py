# -*- coding: utf-8 -*-
"""Синхронизация: «Входні товари» -> «Усі товари».

Алгоритм (см. ТЗ):
  - совпавшие ID: обновить ТОЛЬКО F,G,H,I (цена долл./грн./наличие/кол-во);
  - ID пропал из прайса: H=«Немає в наявності», I=0 (остальное не трогать);
  - новый товар: полный парсинг, дописать строку в конец, A=TRUE (чекбокс);
  - запись батчами; идемпотентность; режим dry_run ничего не пишет.
"""
import argparse
import datetime
import json
import os

import parser_t1
import parser_t2
import sheet_io

FEED_URL = 'https://Ilya330.github.io/chernivtsi-feed/feed.xml'

# индексы колонок «Усі товари» (0-based)
COL = dict(chk=0, id=1, art=2, name=3, short=4, usd=5, uah=6, avail=7, qty=8,
           gname=9, gid=10, parent=11)
CHAR_LABELS = ['Бренд', 'Модель', 'Цвет', 'Материал']


def num_val(x):
    """Число -> ЧИСЛО (int/float), а не строка.

    Строки вроде '3.5' Google Sheets при USER_ENTERED трактует по локали таблицы
    и превращает в дату (3.5 -> 03.05, серийный номер 46145). Числовой JSON-литерал
    записывается как число всегда, независимо от локали.
    """
    if x is None or x == '':
        return ''
    f = float(x)
    return int(f) if f == int(f) else round(f, 2)


def _same_num(cell, value):
    """Численное сравнение ячейки листа со значением (для идемпотентности)."""
    a = sheet_io._num(cell)
    b = None if value in (None, '') else float(value)
    if a is None and b is None:
        return (str(cell).strip() == '')
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-9


# ------------------------------------------------------------------ разбор
def dispatch(record):
    """Разобрать один товар. Вернуть dict полей или None-группу-помечаем.

    Возвращает dict(brand, model, color, material, group, gid, parent, unknown_group).
    """
    raw = record['group_raw']
    if raw is None:
        parser_t2.flags.append((record['code'], record['short'], 'товар вне группы'))
        return dict(brand='', model='', color='', material='',
                    group='Без группы', gid=None, parent='', unknown_group=True)

    if parser_t1.is_t1_group(raw):
        if parser_t1.is_skip(raw):
            # структурный заголовок не должен нести товары
            parser_t1.flags.append((record['code'], record['short'],
                                    f'товар под структурным заголовком «{raw}»'))
            return dict(brand='', model='', color='', material='',
                        group=str(raw), gid=None, parent='', unknown_group=True)
        p = parser_t1.parse(raw, record['code'], record['short'], record['full'])
        if p:
            p['unknown_group'] = False
            return p

    if parser_t2.is_t2_group(raw):
        p = parser_t2.parse(raw, record['code'], record['short'], record['full'])
        if p:
            p['unknown_group'] = False
            return p

    # неизвестная группа -> generic-обработчик (iPhone-подход)
    parser_t2.flags.append((record['code'], record['short'],
                            f'неизвестная группа «{raw}» — generic'))
    parsed = parser_t2.h_iphone_case(record['code'], record['short'], record['full']) or {}
    return dict(brand=parsed.get('brand', ''),
                model=parser_t2.norm_spaces(parsed.get('model', '') or ''),
                color=parsed.get('color', ''), material=parsed.get('material', ''),
                group=parser_t2.norm_spaces(str(raw)), gid=None, parent='',
                unknown_group=True)


def build_char_slots(fields):
    """M..X: 4 фиксированных слота характеристик."""
    values = [fields.get('brand', ''), fields.get('model', ''),
              fields.get('color', ''), fields.get('material', '')]
    out = []
    for label, val in zip(CHAR_LABELS, values):
        if val:
            out.extend([label, '', val])
        else:
            out.extend(['', '', ''])
    return out


# ------------------------------------------------------------------ основной прогон
# защита: доля товаров прайса от числа строк листа, ниже которой синк считается аварийным
MIN_INPUT_RATIO = 0.5


def run(dry_run=False, force=False):
    book = sheet_io.open_book()
    rate, records = sheet_io.read_input(book)
    header, rows, index, raw_rows = sheet_io.read_output(book)

    # карта существующих групп листа: имя (J) -> id (K) — для приоритета (а)
    sheet_group_id = {}
    max_id = 150000
    for row in rows:
        gname = row[COL['gname']].strip()
        gid = row[COL['gid']].strip()
        if gname and gid and gid.isdigit():
            sheet_group_id.setdefault(gname, gid)
            max_id = max(max_id, int(gid))
    # учесть максимум из конфига
    for g in parser_t2._CONFIG['groups']:
        max_id = max(max_id, int(g['id']))
    next_id = max_id + 1

    # ---- предохранитель: пустой/недогруженный прайс не должен обнулять наличие ----
    # Реальный случай: синк по пустому листу снял с наличия 11751 товар.
    if rows and not force:
        ratio = len(records) / len(rows)
        if ratio < MIN_INPUT_RATIO:
            msg = (f'ОСТАНОВЛЕНО: в прайсе {len(records)} товаров против {len(rows)} строк '
                   f'листа ({ratio:.1%}). Похоже, «Входні товари» пуст или ещё загружается. '
                   f'Наличие НЕ тронуто. Если это ожидаемо — запустите с force=true.')
            print('=' * 60)
            print(msg)
            print('=' * 60)
            if not dry_run:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                sheet_io.append_log(book, [
                    now, 'sync-ОСТАНОВЛЕН', '', '', '', '', '', '', FEED_URL, msg,
                ])
            return dict(aborted=True, reason=msg, input_products=len(records),
                        output_rows_before=len(rows), mode='dry-run' if dry_run else 'live')

    price_ids = set()
    new_group_alloc = {}   # имя новой (неизвестной) группы -> назначенный id
    new_groups_report = []
    updates_fi = []        # (a1, values) для F:I
    new_rows = []
    unrecognized = 0
    matched = updated = added = 0

    for rec in records:
        code = rec['code']
        price_ids.add(code)
        f = dispatch(rec)
        if f.get('unknown_group'):
            unrecognized += 1
        usd = rec['usd']
        qty = rec['qty'] or 0
        uah = round((usd or 0) * rate, 2)
        avail = 'В наявності' if (qty and qty > 0) else 'Немає в наявності'

        if code in index:
            # совпавший ID — обновить только F,G,H,I
            matched += 1
            r = index[code]
            cur = rows[r - 2]
            desired = [num_val(usd), num_val(uah), avail, num_val(qty)]
            raw = raw_rows[r - 2]
            numeric_ok = all(raw[i] == '' or isinstance(raw[i], (int, float))
                             for i in (COL['usd'], COL['uah'], COL['qty']))
            same = (numeric_ok
                    and _same_num(cur[COL['usd']], usd)
                    and _same_num(cur[COL['uah']], uah)
                    and cur[COL['avail']].strip() == avail
                    and _same_num(cur[COL['qty']], qty))
            if not same:
                updates_fi.append((f'F{r}:I{r}', [desired]))
                updated += 1
        else:
            # новый товар — определить id группы по приоритету
            gname = f.get('group') or ''
            gid = None
            if gname in sheet_group_id:                     # (а) уже на листе
                gid = sheet_group_id[gname]
            elif f.get('gid'):                              # (б) из конфига/правил
                gid = str(f['gid'])
            else:                                           # (в) новая группа: max+1
                if gname in new_group_alloc:
                    gid = new_group_alloc[gname]
                else:
                    gid = str(next_id)
                    new_group_alloc[gname] = gid
                    new_groups_report.append(f'{gname} -> {gid}')
                    next_id += 1
            new_row = [''] * 24
            new_row[COL['chk']] = 'TRUE'
            new_row[COL['id']] = code
            new_row[COL['art']] = code
            new_row[COL['name']] = rec['full']
            new_row[COL['short']] = rec['short']
            new_row[COL['usd']] = num_val(usd)
            new_row[COL['uah']] = num_val(uah)
            new_row[COL['avail']] = avail
            new_row[COL['qty']] = num_val(qty)
            new_row[COL['gname']] = gname
            new_row[COL['gid']] = gid
            new_row[COL['parent']] = f.get('parent') or ''
            new_row[12:24] = build_char_slots(f)
            new_rows.append(new_row)
            added += 1

    # исчезнувшие из прайса: H=«Немає в наявності», I=0
    removed = 0
    for pid, r in index.items():
        if pid not in price_ids:
            cur = rows[r - 2]
            if not (cur[COL['avail']].strip() == 'Немає в наявності'
                    and _same_num(cur[COL['qty']], 0)):
                updates_fi.append((f'H{r}:I{r}', [['Немає в наявності', 0]]))
                removed += 1

    flags_all = parser_t1.flags + parser_t2.flags
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report = dict(
        timestamp=now, rate=rate, mode='dry-run' if dry_run else 'live',
        input_products=len(records), matched=matched, updated=updated,
        added=added, removed=removed, unrecognized=unrecognized,
        new_groups=new_groups_report, warnings=len(flags_all),
        output_rows_before=len(rows), output_rows_after=len(rows) + len(new_rows),
        feed_url=FEED_URL,
    )

    if dry_run:
        _print_report(report, flags_all)
        return report

    # ---- боевая запись ----
    ws = book.worksheet(sheet_io.OUTPUT_WS)
    sheet_io.batch_update(ws, updates_fi)
    if new_rows:
        first_new = len(rows) + 2
        sheet_io.append_rows(ws, new_rows)
        last_new = first_new + len(new_rows) - 1
        sheet_io.ensure_checkbox_validation(book, ws, first_new, last_new)

    sheet_io.append_log(book, [
        now, 'sync', added, updated, removed, unrecognized,
        '; '.join(new_groups_report), len(flags_all), FEED_URL,
        f'rate={rate}; строк стало {len(rows) + len(new_rows)}',
    ])
    _print_report(report, flags_all)
    return report


def _print_report(report, flags_all):
    print('=' * 60)
    print(f"СИНХРОНИЗАЦИЯ [{report['mode']}]  {report['timestamp']}")
    print('=' * 60)
    print(f"курс USD           : {report['rate']}")
    print(f"товаров в прайсе   : {report['input_products']}")
    print(f"совпавших ID       : {report['matched']}")
    print(f"обновлено (F–I)    : {report['updated']}")
    print(f"добавлено новых    : {report['added']}")
    print(f"снято с наличия    : {report['removed']}")
    print(f"нераспознано       : {report['unrecognized']}")
    print(f"новых групп        : {len(report['new_groups'])}")
    for g in report['new_groups']:
        print(f"    + {g}")
    print(f"предупреждений     : {report['warnings']}")
    print(f"строк было / стало : {report['output_rows_before']} / {report['output_rows_after']}")
    print(f"ссылка на фид      : {report['feed_url']}")
    if flags_all:
        print('-' * 60)
        print('Первые предупреждения:')
        for fl in flags_all[:40]:
            print('  ', fl)
    print('=' * 60)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='обойти защиту от пустого/недогруженного прайса')
    args = ap.parse_args()
    rep = run(dry_run=args.dry_run, force=args.force)
    # для артефакта Actions
    with open('sync_report.json', 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
