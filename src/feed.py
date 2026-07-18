# -*- coding: utf-8 -*-
"""Генерация YML-фида Prom.ua из листа «Усі товари» (только строки с чекбоксом A=TRUE).

Выход: docs/feed.xml (публикуется на GitHub Pages).
"""
import argparse
import datetime
import json
import os
import time
from xml.sax.saxutils import escape, quoteattr

import sheet_io

SHOP_NAME = 'Товари Чернівці'
FEED_URL = 'https://Ilya330.github.io/chernivtsi-feed/feed.xml'
OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'docs', 'feed.xml')
REPORT_PATH = os.path.join(os.path.dirname(__file__), 'feed_report.json')

# сколько ждём, пока GitHub Pages реально отдаст новый файл по ссылке
PUBLISH_TIMEOUT = 420
PUBLISH_POLL = 5

# индексы колонок «Усі товари» (0-based)
C = dict(chk=0, id=1, art=2, name=3, short=4, usd=5, uah=6, avail=7, qty=8,
         gname=9, gid=10, parent=11,
         c1n=12, c1v=14, c2n=15, c2v=17, c3n=18, c3v=20, c4n=21, c4v=23)


def fmt_out(v):
    """Значение ячейки -> число строкой с ТОЧКОЙ, без хвостовых нулей."""
    n = sheet_io._num(v)
    if n is None:
        return ''
    if n == int(n):
        return str(int(n))
    return ('%.2f' % n).rstrip('0').rstrip('.')


def load_category_tree():
    """id -> (name, parent_id) из group_config.json (полное дерево категорий)."""
    path = os.path.join(os.path.dirname(__file__), 'group_config.json')
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    tree = {}
    for g in cfg['groups']:
        tree[str(g['id'])] = (g['name'], str(g['parent_id']) if g.get('parent_id') else None)
    return tree


def _num(v):
    n = sheet_io._num(v)
    return n if n is not None else 0


def build_feed(rows, header):
    tree = load_category_tree()

    # опциональная колонка с картинкой
    pic_idx = None
    for i, h in enumerate(header):
        if h.strip().lower() in ('посилання_зображення', 'посилання зображення',
                                 'зображення', 'picture', 'image'):
            pic_idx = i
            break

    used_cat = {}      # id -> name (по строкам листа, приоритет над деревом)
    parent_from_sheet = {}
    offers = []

    for row in rows:
        row = list(row) + [''] * (24 - len(row))
        if str(row[C['chk']]).strip().upper() not in ('TRUE', '1', 'ИСТИНА', 'ІСТИНА'):
            continue
        code = row[C['id']].strip()
        if not code:
            continue
        gid = row[C['gid']].strip()
        gname = row[C['gname']].strip()
        if gid:
            used_cat.setdefault(gid, gname or gid)
            # родитель из колонки L (формат "pid/name"), если id нет в дереве
            l = row[C['parent']].strip()
            if l and '/' in l:
                pid = l.split('/', 1)[0].strip()
                if pid.isdigit():
                    parent_from_sheet[gid] = pid

        qty = _num(row[C['qty']])
        avail = 'true' if (row[C['avail']].strip() == 'В наявності' and qty > 0) else 'false'

        offer = {
            'id': code,                                # оригинальный код, без транслитерации
            'available': avail,
            'name': row[C['name']].strip() or row[C['short']].strip(),
            'name_ua': row[C['short']].strip(),         # Название_короткое
            'price': fmt_out(row[C['uah']]),
            'vendorprice_doll': fmt_out(row[C['usd']]),  # Ціна долл.
            'categoryId': gid,
            'vendorCode': code,
            'stock_quantity': fmt_out(row[C['qty']]),
            'params': [],
            'picture': (row[pic_idx].strip() if pic_idx is not None else ''),
        }
        # Бренд — обычная характеристика (не <vendor>)
        for name_i, val_i in ((C['c1n'], C['c1v']), (C['c2n'], C['c2v']),
                              (C['c3n'], C['c3v']), (C['c4n'], C['c4v'])):
            pname = row[name_i].strip()
            pval = row[val_i].strip()
            if pval:
                offer['params'].append((pname or 'Характеристика', pval))
        offers.append(offer)

    # собрать все категории + цепочку родителей
    cats = {}   # id -> (name, parent_id)

    def add_cat(cid):
        if cid in cats or not cid:
            return
        if cid in tree:
            name, pid = tree[cid]
        else:
            name = used_cat.get(cid, cid)
            pid = parent_from_sheet.get(cid)
        cats[cid] = (name, pid)
        if pid:
            add_cat(pid)

    for cid in used_cat:
        add_cat(cid)

    return cats, offers


def render_xml(cats, offers, build_id):
    date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<yml_catalog date="{date}">')
    # Метка сборки: по ней проверяем, что Pages уже отдаёт именно этот файл.
    # XML-комментарий парсерами игнорируется и на импорт в Prom не влияет.
    out.append(f'  <!-- build {build_id} -->')
    out.append('  <shop>')
    out.append(f'    <name>{escape(SHOP_NAME)}</name>')
    out.append('    <currencies><currency id="UAH" rate="1"/></currencies>')
    out.append('    <categories>')
    for cid in sorted(cats, key=lambda x: int(x) if x.isdigit() else 0):
        name, pid = cats[cid]
        if pid:
            out.append(f'      <category id={quoteattr(cid)} parentId={quoteattr(pid)}>{escape(name)}</category>')
        else:
            out.append(f'      <category id={quoteattr(cid)}>{escape(name)}</category>')
    out.append('    </categories>')
    out.append('    <offers>')
    for o in offers:
        out.append(f'      <offer id={quoteattr(o["id"])} available={quoteattr(o["available"])}>')
        out.append(f'        <name>{escape(o["name"])}</name>')
        if o['name_ua']:
            out.append(f'        <name_ua>{escape(o["name_ua"])}</name_ua>')
        if o['picture']:
            out.append(f'        <picture>{escape(o["picture"])}</picture>')
        out.append(f'        <price>{escape(o["price"])}</price>')
        if o['vendorprice_doll']:
            out.append(f'        <vendorprice_doll>{escape(o["vendorprice_doll"])}</vendorprice_doll>')
        out.append('        <currencyId>UAH</currencyId>')
        if o['categoryId']:
            out.append(f'        <categoryId>{escape(o["categoryId"])}</categoryId>')
        out.append(f'        <vendorCode>{escape(o["vendorCode"])}</vendorCode>')
        out.append(f'        <stock_quantity>{escape(o["stock_quantity"])}</stock_quantity>')
        for pname, pval in o['params']:
            out.append(f'        <param name={quoteattr(pname)}>{escape(pval)}</param>')
        out.append('      </offer>')
    out.append('    </offers>')
    out.append('  </shop>')
    out.append('</yml_catalog>')
    return '\n'.join(out) + '\n'


def run(out_path=OUT_PATH, write_log=False):
    """Собрать feed.xml. Лог по умолчанию НЕ пишем: строка в «Лог» должна
    появляться только после реальной публикации (см. confirm_and_log)."""
    book = sheet_io.open_book()
    header, rows, _idx, _raw = sheet_io.read_output(book)
    cats, offers = build_feed(rows, header)
    build_id = datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    xml = render_xml(cats, offers, build_id)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(xml)
    avail = sum(1 for o in offers if o['available'] == 'true')
    size_mb = round(os.path.getsize(out_path) / (1024 * 1024), 2)
    report = dict(build_id=build_id, offers=len(offers), available=avail,
                  unavailable=len(offers) - avail, categories=len(cats),
                  size_mb=size_mb)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'feed.xml: {len(offers)} офферов ({avail} в наличии), '
          f'{len(cats)} категорий -> {out_path}')
    print(f'build {build_id} | ссылка: {FEED_URL}')

    if write_log:
        _append_feed_log(book, report, note_suffix='')
    return len(offers), len(cats)


def _append_feed_log(book, report, note_suffix=''):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sheet_io.append_log(book, [
        now, 'feed', '', '', '', '', '', '', FEED_URL,
        f'фід оновлено за посиланням: офферів {report["offers"]} '
        f'(в наявності {report["available"]}, немає {report["unavailable"]}), '
        f'категорій {report["categories"]}, {report["size_mb"]} МБ{note_suffix}',
    ])


def _fetch_head(url, nbytes=800):
    """Первые байты файла по ссылке, в обход кэша CDN.

    requests приходит вместе с gspread и берёт корневые сертификаты из certifi
    (у системного python на macOS их может не быть).
    """
    import requests
    bust = f'{url}?t={int(time.time() * 1000)}'
    r = requests.get(bust, timeout=30, stream=True, headers={
        'Range': f'bytes=0-{nbytes}',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    })
    r.raise_for_status()
    try:
        return r.raw.read(nbytes, decode_content=True).decode('utf-8', 'replace')
    finally:
        r.close()


def confirm_and_log(timeout=PUBLISH_TIMEOUT, poll=PUBLISH_POLL, skip_wait=False):
    """Дождаться, пока по ссылке реально появится собранный файл, и только
    ПОСЛЕ этого записать строку в «Лог»."""
    with open(REPORT_PATH, encoding='utf-8') as f:
        report = json.load(f)
    marker = f'build {report["build_id"]}'
    book = sheet_io.open_book()

    if skip_wait:
        _append_feed_log(book, report, note_suffix='')
        print('лог записан без ожидания (файл не менялся)')
        return True

    started = time.time()
    published = False
    while time.time() - started < timeout:
        try:
            head = _fetch_head(FEED_URL)
            if marker in head:
                published = True
                break
        except Exception as e:                      # сеть/404/Range — просто пробуем ещё
            print(f'  ожидание публикации: {e}')
        time.sleep(poll)

    waited = int(time.time() - started)
    if published:
        print(f'публикация подтверждена за {waited} с — пишу лог')
        _append_feed_log(book, report, note_suffix=f'; опубліковано за {waited} с')
    else:
        print(f'ВНИМАНИЕ: за {waited} с публикация не подтвердилась')
        _append_feed_log(
            book, report,
            note_suffix=f'; ⚠ публікацію не підтверджено за {waited} с — '
                        f'посилання може оновитись із затримкою')
    return published


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT_PATH)
    ap.add_argument('--log-now', action='store_true',
                    help='записать лог сразу после сборки, не дожидаясь публикации')
    ap.add_argument('--confirm-log', action='store_true',
                    help='НЕ собирать фид: дождаться появления файла по ссылке '
                         'и записать строку в «Лог»')
    ap.add_argument('--skip-wait', action='store_true',
                    help='с --confirm-log: писать лог без ожидания (файл не менялся)')
    ap.add_argument('--timeout', type=int, default=PUBLISH_TIMEOUT)
    args = ap.parse_args()
    if args.confirm_log:
        confirm_and_log(timeout=args.timeout, skip_wait=args.skip_wait)
    else:
        run(out_path=args.out, write_log=args.log_now)
