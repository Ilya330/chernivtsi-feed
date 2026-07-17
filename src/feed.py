# -*- coding: utf-8 -*-
"""Генерация YML-фида Prom.ua из листа «Усі товари» (только строки с чекбоксом A=TRUE).

Выход: docs/feed.xml (публикуется на GitHub Pages).
"""
import argparse
import datetime
import json
import os
from xml.sax.saxutils import escape, quoteattr

import sheet_io

SHOP_NAME = 'Товари Чернівці'
FEED_URL = 'https://Ilya330.github.io/chernivtsi-feed/feed.xml'
OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'docs', 'feed.xml')

# индексы колонок «Усі товари» (0-based)
C = dict(chk=0, id=1, art=2, name=3, short=4, usd=5, uah=6, avail=7, qty=8,
         gname=9, gid=10, parent=11,
         c1n=12, c1v=14, c2n=15, c2v=17, c3n=18, c3v=20, c4n=21, c4v=23)


def translit_code(code):
    """НФ-00037879 -> NF-00037879 (стабильно и однозначно)."""
    return code.replace('НФ', 'NF').replace('нф', 'nf')


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
        price = row[C['uah']].strip().replace(' ', '').replace(',', '.')

        offer = {
            'id': translit_code(code),
            'available': avail,
            'name': row[C['name']].strip() or row[C['short']].strip(),
            'price': price,
            'categoryId': gid,
            'vendor': row[C['c1v']].strip(),          # Бренд -> <vendor>
            'vendorCode': code,
            'stock_quantity': str(int(qty)) if qty == int(qty) else str(qty),
            'params': [],
            'picture': (row[pic_idx].strip() if pic_idx is not None else ''),
        }
        for name_i, val_i in ((C['c2n'], C['c2v']), (C['c3n'], C['c3v']), (C['c4n'], C['c4v'])):
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


def render_xml(cats, offers):
    date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<yml_catalog date="{date}">')
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
        if o['picture']:
            out.append(f'        <picture>{escape(o["picture"])}</picture>')
        out.append(f'        <price>{escape(o["price"])}</price>')
        out.append('        <currencyId>UAH</currencyId>')
        if o['categoryId']:
            out.append(f'        <categoryId>{escape(o["categoryId"])}</categoryId>')
        if o['vendor']:
            out.append(f'        <vendor>{escape(o["vendor"])}</vendor>')
        out.append(f'        <vendorCode>{escape(o["vendorCode"])}</vendorCode>')
        out.append(f'        <stock_quantity>{escape(o["stock_quantity"])}</stock_quantity>')
        for pname, pval in o['params']:
            out.append(f'        <param name={quoteattr(pname)}>{escape(pval)}</param>')
        out.append('      </offer>')
    out.append('    </offers>')
    out.append('  </shop>')
    out.append('</yml_catalog>')
    return '\n'.join(out) + '\n'


def run(out_path=OUT_PATH):
    book = sheet_io.open_book()
    header, rows, _ = sheet_io.read_output(book)
    cats, offers = build_feed(rows, header)
    xml = render_xml(cats, offers)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(xml)
    avail = sum(1 for o in offers if o['available'] == 'true')
    print(f'feed.xml: {len(offers)} офферов ({avail} в наличии), '
          f'{len(cats)} категорий -> {out_path}')
    print(f'ссылка: {FEED_URL}')
    return len(offers), len(cats)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT_PATH)
    ap.parse_args()
    run()
