# -*- coding: utf-8 -*-
"""Правила парсинга первой партии групп (перенесены из parse_products.py БЕЗ изменений).

Источник данных изменён (Google Sheets вместо xlsx) — сами правила (регексы, палитры,
словари цветов/материалов, нормализация моделей) НЕ меняются: проверены на 3190 товарах.

Экспорт:
    is_t1_group(raw)  -> относится ли имя группы к первой партии (или её skip-заголовкам)
    is_skip(raw)      -> структурный заголовок без товаров
    parse(raw, code, short, full) -> dict(brand, model, color, material, group, gid, parent)
    flags             -> список предупреждений (code, short, reason)
"""
import re

# ---------- справочники (эталон, не менять) ----------
SIL_CODE_COLORS = {
    '01': 'Red', '02': 'Turquoise', '03': 'Black', '04': 'Pink', '05': 'Dark Grey',
    '06': 'Pistachio', '07': 'Dark Purple', '08': 'Dark Blue', '09': 'White',
    '10': 'Sky Blue', '11': 'Light Blue', '12': 'Bright Pink', '13': 'Lavender',
    '14': 'Grape', '15': 'Camellia', '16': 'Blue', '17': 'Light Pink',
    '18': 'Powder Pink', '19': 'Olive', '20': 'Yellow', '21': 'Barbie Pink',
    '22': 'Dark Green', '23': 'Marsala', '24': 'Lilac', '25': 'Khaki',
    '26': 'Electric Blue', '27': 'Light Green',
}
EN_COLOR_FIX = {
    'biege': 'Beige', 'beige': 'Beige', 'black': 'Black', 'blue': 'Blue', 'brown': 'Brown',
    'grey': 'Grey', 'gray': 'Grey', 'pink': 'Pink', 'red': 'Red', 'green': 'Green',
    'gold': 'Gold', 'silver': 'Silver', 'purple': 'Purple', 'orange': 'Orange',
    'yellow': 'Yellow', 'white': 'White', 'electric': 'Electric Blue',
    'tiffani': 'Tiffany Blue', 'coffee': 'Coffee', 'bordo': 'Burgundy',
    'burgundy': 'Burgundy', 'violet': 'Violet', 'mint': 'Mint', 'navy': 'Navy Blue',
    'rose': 'Rose Gold', 'lavender': 'Lavender', 'darkblue': 'Dark Blue',
    'lightblue': 'Light Blue', 'flowers': 'Flowers',
}
GROUPS = {  # Назва_групи_сайту: (ID, батьківська)
    'Snake Case':        (150002, '150001/Чохли на телефон'),
    'WaterProof Case':   (150003, '150001/Чохли на телефон'),
    'Book Wallet Cover': (150004, '150001/Чохли на телефон'),
    'Nubuk Flip':        (150005, '150001/Чохли на телефон'),
    'Книжки Molancano':  (150006, '150001/Чохли на телефон'),
    'NoLo':              (150007, '150001/Чохли на телефон'),
    'NeLo':              (150008, '150001/Чохли на телефон'),
    'Автотовари':        (150009, ''),
}
LEATHER = 'Искусственная кожа'

flags = []   # (код, короткое имя, причина)


def norm_spaces(s):
    return re.sub(r'\s+', ' ', s).strip()

def fix_latin(s):
    # кириллические А/В/С/Е/... внутри латинских моделей -> латиница
    return s.translate(str.maketrans('АВСЕНКМОРТХ', 'ABCEHKMOPTX'))

def norm_model_tokens(s):
    s = norm_spaces(s.replace(' /', '/').replace('/ ', '/'))
    s = re.sub(r'\bNote(\d)', r'Note \1', s)              # Note13 -> Note 13
    s = re.sub(r'\b([A-Z]\d+)(Pro|Ultra|Plus)\b', r'\1 \2', s)  # M6Pro -> M6 Pro, X5Pro -> X5 Pro
    s = re.sub(r'\s*\+\s*', '+ ', s)                      # 'Pro + 5G' -> 'Pro+ 5G'
    return s.strip()

def normalize_final_model(s):
    """Приведение разнобоя написаний моделей к единому виду."""
    s = re.sub(r'\b(S\d{2})FE\b', r'\1 FE', s)          # S23FE -> S23 FE
    s = re.sub(r'\bs(\d{2})\b', r'S\1', s)              # s21 -> S21
    s = re.sub(r'\b(S\d{2})\+', r'\1 Plus', s)          # S10+ -> S10 Plus
    s = re.sub(r'\b(A\d{2})([SE])\b', lambda m: m.group(1) + m.group(2).lower(), s)  # A04E->A04e, A03S->A03s
    s = re.sub(r'\b(M\d{2})S\b', r'\1s', s)             # M31S -> M31s
    s = re.sub(r'\bMi(\d)', r'Mi \1', s)                # Mi11T -> Mi 11T
    s = re.sub(r'\bNote (\d+)Pro\b', r'Note \1 Pro', s) # Note 10Pro -> Note 10 Pro
    s = re.sub(r'\bPro Plus\b', 'Pro+', s)              # Pro Plus 5G -> Pro+ 5G
    s = re.sub(r'\b(Flip|Fold)(\d)', r'\1 \2', s)       # Flip3 -> Flip 3
    return norm_spaces(s)

def strip_brand_words(s):
    return norm_spaces(re.sub(r'\b(Xiaomi|Samsung|Galaxy|Huawei|Honor)\b', '', s, flags=re.I))

def apple_model(tok):
    tok = norm_model_tokens(tok)
    tok = re.sub(r'\bProMax\b', 'Pro max', tok)
    tok = re.sub(r'\bPro Max\b', 'Pro max', tok)
    return 'IPhone ' + tok

def color_from_en(word, code, name):
    w = word.lower().strip()
    if w in EN_COLOR_FIX:
        return EN_COLOR_FIX[w]
    flags.append((code, name, f'не распознан цвет "{word}"'))
    return word.capitalize()

# ---------- разбор одной строки по группам ----------
def parse_snake(code, short, full):
    m = re.match(r'^(?P<model>.+?)\s+Snake(?:\s+Case)?\s+(?P<color>\w+)$', fix_latin(short))
    if not m:
        flags.append((code, short, 'Snake: не разобран шаблон')); return None
    color = color_from_en(m.group('color'), code, short)
    tok = m.group('model')
    if 'Samsung' in full:
        brand, model = 'Samsung', 'Samsung Galaxy ' + norm_model_tokens(tok)
    elif 'Xiaomi' in full or tok.startswith('Note'):
        brand, model = 'Xiaomi', 'Xiaomi Redmi ' + norm_model_tokens(tok)
        # сверка коротк./полн. наименования
        full_tok = re.search(r'на Xiaomi Redmi\s+(.+?)\s+\S+$', full)
        if full_tok and norm_model_tokens(full_tok.group(1)) != norm_model_tokens(tok):
            flags.append((code, short, f'несовпадение модели с полным наименованием ({full_tok.group(1)}) — взята модель из короткого'))
    else:
        brand, model = 'Apple', apple_model(tok)
    return dict(brand=brand, model=model, color=color, material=LEATHER, group='Snake Case')

def parse_waterproof(code, short, full):
    m = re.match(r'^WaterProof bag\s+(\w+)$', short, re.I)
    if not m:
        flags.append((code, short, 'WaterProof: не разобран шаблон')); return None
    color = color_from_en(m.group(1), code, short)
    return dict(brand='', model='', color=color, material='ПВХ', group='WaterProof Case')

def parse_auto(code, short, full):
    brand = ''
    for b in ('WK', 'Borofone', 'Yujiso', 'Hoco', 'Baseus'):
        if re.search(r'\b' + b + r'\b', short, re.I):
            brand = b; break
    mm = re.search(r'\b(WP-U\d+\w*|BH\d+\w*|W\d+-C\d+\w*|H-[A-Z]+\d+\w*)\b', short)
    model = mm.group(1) if mm else ''
    cm = re.search(r'\b(black|silver|white|red|blue|grey|gray)\b', short, re.I)
    color = EN_COLOR_FIX[cm.group(1).lower()] if cm else ''
    return dict(brand=brand, model=model, color=color, material='', group='Автотовари')

def parse_bookwallet(code, short, full):
    m = re.match(r'^Book Wallet Cover\s+(?P<model>.+?)\s+(?P<color>[a-zA-Z]+)$', fix_latin(short))
    if not m:
        flags.append((code, short, 'BookWallet: не разобран шаблон')); return None
    color = color_from_en(m.group('color'), code, short)
    tok = norm_model_tokens(m.group('model'))
    if tok.startswith('IPhone') or tok.startswith('iPhone'):
        brand, model = 'Apple', 'IPhone ' + norm_model_tokens(tok.replace('IPhone', '').replace('iPhone', ''))
    elif tok.startswith('Redmi') or tok.startswith('Xiaomi') or tok.startswith('Poco'):
        brand = 'Xiaomi'
        model = 'Xiaomi ' + tok if not tok.startswith('Xiaomi') else tok
    elif tok.startswith('Samsung'):
        brand, model = 'Samsung', 'Samsung Galaxy ' + norm_spaces(tok.replace('Samsung', ''))
    else:
        # группа Samsung? проверим полное
        if 'Samsung' in full:
            brand, model = 'Samsung', 'Samsung Galaxy ' + tok
        else:
            flags.append((code, short, f'BookWallet: не определён бренд для "{tok}"'))
            brand, model = '', tok
    return dict(brand=brand, model=model, color=color, material=LEATHER, group='Book Wallet Cover')

def parse_nubuk(code, short, full):
    cm = re.match(r'^(?P<model>.+?)\s+Nubuk\s+(?P<color>\w+)$', fix_latin(short))
    if not cm:
        flags.append((code, short, 'Nubuk: не разобран шаблон')); return None
    color = color_from_en(cm.group('color'), code, short)
    fm = re.search(r'(?:Nubuk|Нубук) на\s+(.+?)\s+[\w\-]+\s+кольору', full, re.I)
    if fm:
        phone = norm_model_tokens(fix_latin(fm.group(1)))
        brand = phone.split()[0]
        if brand == 'Samsung' and 'Galaxy' not in phone:
            phone = phone.replace('Samsung', 'Samsung Galaxy')
        model = phone
    else:
        flags.append((code, short, 'Nubuk: модель не извлечена из полного наименования'))
        brand, model = '', norm_model_tokens(cm.group('model'))
    return dict(brand=brand, model=model, color=color, material=LEATHER, group='Nubuk Flip')

def parse_molancano(code, short, full):
    if short.startswith('Caruso'):
        color_word = short.split()[-1]
        color = '' if color_word.lower() == 'flowers' else color_from_en(color_word, code, short)
        if not color:
            flags.append((code, short, 'Caruso Flowers: узор, цвет не задан'))
        return dict(brand='', model='', color=color, material=LEATHER, group='Книжки Molancano')
    m = re.match(r'^Molancano ISSUE dairy case\s+(?P<rest>.+)$', fix_latin(short))
    if not m:
        flags.append((code, short, 'Molancano: не разобран шаблон')); return None
    parts = m.group('rest').split()
    last = parts[-1]
    if last.lower() not in EN_COLOR_FIX:
        # слитное написание модели и цвета, напр. "9/10XCoffee"
        m2 = re.match(r'^(.*?)(Coffee|Black|Blue|Red|Green|Pink|Gold|Brown|Grey|Gray|Purple|Bordo|White|Beige|Biege)$', last, re.I)
        if m2 and m2.group(1):
            parts = parts[:-1] + [m2.group(1)]
            last = m2.group(2)
            flags.append((code, short, f'слитное написание "{m2.group(0)}" — исправлено на "{m2.group(1)} {last}"'))
        else:
            parts = parts + ['']
    color = color_from_en(last, code, short)
    # модель берём из полного наименования (там бренд написан явно)
    fm = re.search(r'на\s+(.+?)\s+[А-Яа-яІіЇїЄє\-]+\s*$', full)
    tok = norm_model_tokens(fix_latin(fm.group(1))) if fm else norm_model_tokens(' '.join(parts[:-1]))
    if 'Samsung' in full:
        brand = 'Samsung'
        model = tok.replace('Samsung', 'Samsung Galaxy') if 'Galaxy' not in tok else tok
        if not model.startswith('Samsung'):
            model = 'Samsung Galaxy ' + model
    elif 'Xiaomi' in full or 'Redmi' in full:
        brand = 'Xiaomi'
        model = tok if tok.startswith('Xiaomi') else 'Xiaomi ' + tok
    else:
        flags.append((code, short, f'Molancano: бренд не определён (полное: {full[:60]})'))
        brand, model = '', tok
    return dict(brand=brand, model=model, color=color, material=LEATHER, group='Книжки Molancano')

def parse_silicone(code, short, full):
    m = re.match(r'^(?P<line>NoLo|NeLo)\s+SC\s+(?P<model>.+?)\s+#(?P<cc>\d+)$', fix_latin(short))
    if not m:
        flags.append((code, short, 'Силикон: не разобран шаблон')); return None
    cc = m.group('cc').zfill(2)
    color = SIL_CODE_COLORS.get(cc)
    if not color:
        flags.append((code, short, f'Силикон: неизвестный код цвета #{cc}')); color = ''
    fm = re.search(r'чохол на\s+(.+?)\s*#', full)
    if not fm:
        fm = re.search(r'чохол на\s+(.+?)\s+[\w\-]+\s+кольору\s*$', full)
    short_phone = norm_model_tokens(fix_latin(m.group('model')))
    if fm:
        phone = norm_model_tokens(fix_latin(fm.group(1)))
        # сверка с коротким наименованием: при расхождении верим короткому
        a = normalize_final_model(strip_brand_words(phone)).lower()
        b = normalize_final_model(strip_brand_words(short_phone)).lower()
        if a != b:
            brand_word = phone.split()[0] if phone.split()[0] in ('Samsung', 'Xiaomi', 'Huawei', 'Honor') else ''
            flags.append((code, short, f'модель в полном наименовании ("{phone}") расходится с коротким — взята из короткого'))
            phone = norm_spaces(brand_word + ' ' + short_phone) if brand_word and not short_phone.startswith(brand_word) else short_phone
    else:
        phone = short_phone
        flags.append((code, short, 'Силикон: модель взята из короткого наименования'))
    first = phone.split()[0]
    if first == 'Samsung':
        brand = 'Samsung'
        model = phone.replace('Samsung', 'Samsung Galaxy') if 'Galaxy' not in phone else phone
    elif first in ('Xiaomi',):
        brand, model = 'Xiaomi', phone
    elif first in ('Redmi', 'Mi', 'Poco'):
        brand, model = 'Xiaomi', 'Xiaomi ' + phone
    elif first == 'Huawei':
        brand, model = 'Huawei', phone
    elif first == 'Honor':
        brand, model = 'Honor', phone
    else:
        flags.append((code, short, f'Силикон: бренд не определён "{phone}"'))
        brand, model = '', phone
    return dict(brand=brand, model=model, color=color, material='Силикон', group=m.group('line'))

PARSERS = {
    'Snake Case': parse_snake,
    'WaterProof Case': parse_waterproof,
    'Авто товари': parse_auto,
    'Book Wallet Cover': parse_bookwallet,
    'Nubuk Flip': parse_nubuk,
    'Книжки Molancano': parse_molancano,
    'Cиліконові чохли на  Huawei': parse_silicone,
    'Cиліконові чохли на Samsung': parse_silicone,
    'Cиліконові чохли на Xiaomi': parse_silicone,
}
SKIP_GROUPS = {'Книжки для телефонів', '!Cиліконові чохли', 'All_Panfilov', 'Вхо'}

# ---------- диспетчеризация с нормализацией пробелов ----------
# В Google Sheets имена групп могут отличаться числом пробелов от эталонных ключей
# (напр. «Cиліконові чохли на Huawei» с одним пробелом против ключа с двумя).
PARSERS_NORM = {norm_spaces(k): v for k, v in PARSERS.items()}
SKIP_NORM = {norm_spaces(k) for k in SKIP_GROUPS}


def is_t1_group(raw):
    n = norm_spaces(str(raw))
    return n in PARSERS_NORM or n in SKIP_NORM

def is_skip(raw):
    return norm_spaces(str(raw)) in SKIP_NORM


def parse(raw, code, short, full):
    """Разобрать один товар первой партии. Возвращает поля товара или None (не t1-группа)."""
    n = norm_spaces(str(raw))
    handler = PARSERS_NORM.get(n)
    if handler is None:
        return None
    short = norm_spaces(str(short))
    full = norm_spaces(str(full))
    p = handler(code, short, full)
    if p is None:
        p = dict(brand='', model='', color='', material='', group=None)
    site = p.get('group')
    if site not in GROUPS:
        # не должно случаться для валидной t1-группы; помечаем
        flags.append((code, short, f't1: сайт-группа «{site}» отсутствует в GROUPS'))
        gid, parent = None, ''
    else:
        gid, parent = GROUPS[site]
    if site != 'Автотовари':
        p['model'] = normalize_final_model(p.get('model', '') or '')
    return dict(
        brand=p.get('brand', ''), model=p.get('model', ''),
        color=p.get('color', ''), material=p.get('material', ''),
        group=site, gid=gid, parent=parent,
    )
