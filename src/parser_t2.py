# -*- coding: utf-8 -*-
"""Правила парсинга второй партии (~150 подгрупп) — перенесены из parse_t2.py БЕЗ изменений.

Отличие от оригинала: источник данных — Google Sheets, а иерархия групп (родители/ID)
берётся НЕ из группировки строк xlsx (в Sheets она теряется), а из group_config.json
(source_map_t2 + дерево groups). Сами обработчики, палитры, словари цветов/материалов
и нормализация моделей НЕ меняются — проверены на 8577 товарах.

Экспорт:
    is_t2_group(raw) -> относится ли имя группы ко второй партии (по source_map_t2)
    parse(raw, code, short, full) -> dict(brand, model, color, material, group, gid, parent)
    flags            -> список предупреждений
"""
import os
import json
import re

flags = []

# ---------- палитра для Silicone Case (только Huawei/Samsung/Xiaomi Silicone Case) ----------
SIL_PALETTE = {
    1: 'Red', 2: 'Sea Blue', 3: 'Black', 4: 'Pink', 5: 'Charcoal Gray', 6: 'Lemonade',
    7: 'Ultraviolet', 8: 'Dark Blue', 9: 'White', 10: 'Azure', 11: 'Lilac Cream',
    12: 'Coral', 13: 'Lilac', 14: 'Purple', 15: 'Raspberry', 16: 'Denim Blue',
    17: 'Light Pink', 18: 'Pink Sand', 19: 'Mint', 20: 'Canary Yellow', 21: 'Shiny Pink',
    22: 'Dark Green', 23: 'Marsala', 24: 'Blueberry', 25: 'Army Green', 26: 'Shiny Blue',
    27: 'Shiny Green',
}

# ---------- словарь английских цветов (фраза -> каноническое написание) ----------
BASE_COLORS = ['Black', 'White', 'Red', 'Blue', 'Green', 'Brown', 'Grey', 'Pink', 'Gold',
    'Silver', 'Purple', 'Orange', 'Yellow', 'Beige', 'Violet', 'Mint', 'Lavender', 'Lilac',
    'Crimson', 'Spearmint', 'Graphite', 'Chocolate', 'Pearl', 'Camo', 'Multicolor', 'Clear',
    'Navy', 'Coffee', 'Burgundy', 'Marsala', 'Khaki', 'Olive', 'Turquoise', 'Rose', 'Antique',
    'Pale', 'Titanium', 'Starlight', 'Midnight', 'Powder', 'Sand', 'Champagne', 'Peach',
    'Cream', 'Ivory', 'Coral', 'Teal', 'Cyan', 'Magenta', 'Maroon', 'Sierra', 'Denim',
    'Jeans', 'Rainbow', 'Transparent', 'Salmon', 'Mustard', 'Wine', 'Sky', 'Stone', 'Smoke']
COLOR_CANON = {c.lower(): c for c in BASE_COLORS}
COLOR_CANON.update({'gray': 'Gray', 'biege': 'Beige', 'bordo': 'Burgundy', 'navi': 'Navi',
                    'darkblue': 'Dark Blue', 'rosegold': 'Rose Gold', 'tiffany': 'Tiffany Blue',
                    'tiffani': 'Tiffany Blue', 'viola': 'Viola', 'sapphire': 'Sapphire',
                    'barbie': 'Barbie Pink', 'cactus': 'Cactus', 'fuchsia': 'Fuchsia',
                    'ice': 'Ice', 'golden': 'Golden', 'ash': 'Ash', 'ink': 'Ink',
                    'umber': 'Umber'})
COLOR_MODIFIERS = {'dark', 'light', 'deep', 'bright', 'matte', 'shiny', 'space', 'hot',
                   'forest', 'navy', 'navi', 'sky', 'rose', 'antique', 'desert', 'indigo',
                   'blackish', 'sierra', 'baby', 'ice', 'pale', 'lemon', 'olive', 'army',
                   'charcoal', 'pine', 'wine', 'midnight', 'graphite', 'stone', 'smoke', 'off',
                   'primary'}

def canon_color_phrase(words):
    out = []
    for w in words:
        lw = w.lower()
        out.append(COLOR_CANON.get(lw, w[:1].upper() + w[1:].lower()))
    return ' '.join(out)

def extract_trailing_color(name):
    """Отделяет цветовую фразу в конце строки. Возвращает (rest, color|'')."""
    toks = name.split()
    best = 0
    for n in (3, 2, 1):
        if len(toks) < n: continue
        tail = toks[-n:]
        ok = True
        for i, w in enumerate(tail):
            lw = w.lower().strip(',')
            # составные через / или -
            parts = re.split(r'[/-]', lw)
            if all((p in COLOR_CANON or p in COLOR_MODIFIERS) for p in parts if p) and any(p in COLOR_CANON for p in parts if p):
                continue
            if lw in COLOR_MODIFIERS and i < len(tail) - 1:
                continue
            ok = False
            break
        last = re.split(r'[/-]', tail[-1].lower().strip(','))
        if ok and any(p in COLOR_CANON for p in last if p) and all((p in COLOR_CANON or p in COLOR_MODIFIERS) for p in last if p):
            best = n
            break
    if not best:
        return name, ''
    tail = toks[-best:]
    rest = ' '.join(toks[:-best]).rstrip(' ,')
    canon = []
    for w in tail:
        w = w.strip(',')
        if re.search(r'[/-]', w):
            parts = re.split(r'([/-])', w)
            canon.append(''.join(COLOR_CANON.get(p.lower(), p[:1].upper() + p[1:].lower()) if p not in '/-' else p for p in parts))
        else:
            lw = w.lower()
            canon.append(COLOR_CANON.get(lw, w[:1].upper() + w[1:].lower()) if (lw in COLOR_CANON or lw in COLOR_MODIFIERS) else w)
    return rest, ' '.join(canon)

# ---------- украинские цвета ----------
UA_COLORS = {
    'чорн': 'Black', 'біл': 'White', 'син': 'Blue', 'червон': 'Red', 'зелен': 'Green',
    'рожев': 'Pink', 'розов': 'Pink', 'жовт': 'Yellow', 'фіолетов': 'Purple',
    'бордов': 'Burgundy', 'блакитн': 'Light Blue', 'голуб': 'Light Blue',
    "м'ятн": 'Mint', 'мятн': 'Mint', 'бежев': 'Beige', 'сір': 'Grey', 'коричнев': 'Brown',
    'кавов': 'Coffee', 'шампань': 'Champagne', 'прозор': 'Clear', 'персиков': 'Peach',
    'лавандов': 'Lavender', 'золот': 'Gold', 'срібн': 'Silver', 'фіолет': 'Purple',
    'графітов': 'Graphite', 'шоколад': 'Chocolate', 'барбі': 'Barbie Pink',
    'електрик': 'Electric Blue', 'помаранчев': 'Orange', 'фуксі': 'Fuchsia',
    'марсал': 'Marsala', 'пудров': 'Powder Pink', 'хакі': 'Khaki', 'оливков': 'Olive',
    'бузков': 'Lilac', 'малинов': 'Raspberry', 'ментол': 'Mint',
}
def ua_color(text):
    t = text.lower()
    if 'чорно-зелен' in t: return 'Black/Green'
    for k, v in UA_COLORS.items():
        if k in t: return v
    return ''

# ---------- нормализация моделей ----------
def norm_spaces(s): return re.sub(r'\s+', ' ', s).strip()

def fix_latin(s):
    return s.translate(str.maketrans('АВСЕНКМОРТХ', 'ABCEHKMOPTX'))

def norm_phone(s):
    s = norm_spaces(s.replace(' /', '/').replace('/ ', '/'))
    s = re.sub(r'\b(ProMax|Promax|PROMAX)\b', 'Pro max', s)
    s = re.sub(r'\bPro Max\b', 'Pro max', s, flags=re.I)
    s = re.sub(r'(\d)(ProMax|Promax)\b', r'\1 Pro max', s)
    s = re.sub(r'(\d)(Pro|Plus|Ultra|Max|Lite|Core|Mini)\b', r'\1 \2', s)
    s = re.sub(r'\bMax\b', 'max', s) if ' Pro max' in s else s
    s = re.sub(r'\b(S\d{2})FE\b', r'\1 FE', s)
    s = re.sub(r'\b(A\d{2})([SE])\b', lambda m: m.group(1) + m.group(2).lower(), s)
    s = re.sub(r'\b(M\d{2})S\b', r'\1s', s)
    s = re.sub(r'\bMi(\d)', r'Mi \1', s)
    s = re.sub(r'\bNote(\d)', r'Note \1', s)
    s = re.sub(r'\bPSmartPro\b', 'P Smart Pro', s)
    s = re.sub(r'\bPSmart\b', 'P Smart', s)
    s = re.sub(r'\b(Flip|Fold)(\d)', r'\1 \2', s)
    s = re.sub(r'\b(\d{1,2})\s*PM\b', r'\1 Pro max', s)
    s = re.sub(r'\bXSMax\b', 'Xs max', s)
    s = re.sub(r'\bXsMax\b', 'Xs max', s)
    s = re.sub(r'\bXS\b', 'Xs', s)
    s = re.sub(r'\b(S\d{2})(Fe|FE)\b', r'\1 FE', s)
    return norm_spaces(s)

def iphone_model(tok):
    tok = norm_phone(tok)
    tok = re.sub(r'\b(iPhone|IPhone|Iphone)\b\s*', '', tok).strip()
    return norm_spaces('IPhone ' + tok)

def samsung_model(tok):
    tok = norm_phone(fix_latin(tok))
    tok = re.sub(r'\bSamsung\b\s*', '', tok).strip()
    return norm_spaces('Samsung Galaxy ' + tok)

def hash_num(text):
    m = re.search(r'#\s*(\d+)', text)
    if m: return '#' + str(int(m.group(1)))
    return ''

def route_phone(tok, full=''):
    """Определить бренд по токену модели и полному наименованию."""
    t = norm_phone(fix_latin(tok))
    if re.match(r'^(Xiaomi|Redmi|Poco|Mi\s?\d)', t):
        brand = 'Xiaomi'
        model = t if t.startswith('Xiaomi') else 'Xiaomi ' + t
        return brand, model
    if re.match(r'^(iPhone|IPhone|Iphone|\d{1,2}\s|X[sr]?/|SE\b)', t) or 'iphone' in full.lower():
        return 'Apple', iphone_model(t)
    if re.match(r'^Honor', t):
        return 'Honor', t
    if re.match(r'^Huawei', t):
        return 'Huawei', t
    if re.match(r'^Note\s?\d', t):
        num = int(re.match(r'^Note\s?(\d+)', t).group(1))
        if 'Samsung' in full or num in (8, 9, 10, 20):
            return 'Samsung', 'Samsung Galaxy ' + t
        return 'Xiaomi', 'Xiaomi Redmi ' + t
    if re.match(r'^Moto', t):
        return 'Motorola', 'Motorola ' + t
    return 'Samsung', samsung_model(t)

# ---------- материалы по подгруппам ----------
MATERIAL = {
    'Huawei Silicone Case': 'Силикон', 'Samsung Silicone Case': 'Силикон', 'Xiaomi Silicone Case': 'Силикон',
    'Magic box glass': 'Закаленное стекло', 'Mirror Privacy Glass': 'Закаленное стекло',
    'Monkey King Clear Glass': 'Закаленное стекло', 'Privacy': 'Закаленное стекло',
    'Захисне скло для камери на iPhone': 'Закаленное стекло', 'Захисне скло/модуль на камеру': 'Закаленное стекло',
    'Скло Premium Clear': 'Закаленное стекло', 'OG Purple Huawei': 'Закаленное стекло',
    'OG Purple iPhone': 'Закаленное стекло', 'OG Purple Samsung': 'Закаленное стекло',
    'OG Purple Xiaomi': 'Закаленное стекло',
    'Alca': 'Силикон', 'Leather': 'Кожа', 'Logo': 'Силикон', 'інші': 'Силикон', 'Marble': '',
    'Butterfly Case': 'Силикон', 'Carbon Samsung Case': 'Силикон', 'Clear Ring': 'Силикон',
    'Crystal Cam Case': 'Силикон', 'Flower Print': 'Силикон', 'Gradient Magsafe Case Samsung': 'Силикон',
    'INVISIBLE BRACKET': 'Силикон', 'LV Glass Case': 'Стекло', 'Magnetic Samsung Case': 'Силикон',
    'MagSafe Matte Samsung Case': 'Силикон', 'Molan Cano Jelly Card Case': 'Силикон',
    'Molan Cano Jelly Sparkle': 'Силикон', 'Molan Cano Shockproof': 'Силикон',
    'Shockproof Android Case': 'Силикон', 'Space Capsule': 'Пластик', 'UAG Samsung': 'Пластик',
    'Clear print': 'Силикон', 'Glossy case': 'Силикон', 'Glossy Clear case': 'Силикон',
    'Glossy Clear Chain': 'Силикон',
    'BoB Monster': 'Силикон', 'CapyBara': 'Силикон', 'Hope case': 'Силикон',
    'Kuromi Pocket Case': 'Силикон', 'Minnie Mouse': 'Силикон', 'Mirror Star Crossbody Case': 'Силикон',
    'Pink Rabbit': 'Силикон', 'Teddy Bear': 'Силикон',
    'Light Wings': 'Силикон', 'Light wings Diamond': 'Силикон', 'Light with Magsafe': 'Силикон',
    'Shine TPU Huawei': 'Силикон', 'Shine TPU Samsung': 'Силикон', 'Shine TPU Xiaomi': 'Силикон',
    'Discover innovation case': 'Силикон', 'Magnetic Magsafe Samsung case': 'Силикон',
    'Slide Phone Case with Ring': 'Силикон',
    'Alpine Band': 'Нейлон', 'Apple watch Band AP LV Gucci': '', 'Apple Watch Nike Sport Band\'s': 'Силикон',
    'Apple Watch Sport Band': 'Силикон', 'Leather apple watch band': 'Кожа', 'LV Gucci Hermes': 'Кожа',
    'Metal /Ceramic Apple watch band': 'Металл', 'milanese band': 'Металл', 'Nylon band': 'Нейлон',
    'Solo Loop': 'Силикон', 'Van Cleef': 'Металл',
    '18/24mm Band': '', 'Mi 3/4 Band': 'Силикон', 'Mi 3/4/5/6': 'Текстиль', 'Milanese': 'Металл',
    'Mi 5/6 Band': 'Металл', 'Mi 7 3-Bead': 'Металл', 'Mi 7 Milanes': 'Металл', 'Mi Silicone Band': 'Силикон',
    'Прозорі чохли Pocket': 'Силикон',
    'iVoler Shockproof camera protect clear case iPhone': 'Силикон',
    'iVoler Shockproof camera protect clear case Samsung': 'Силикон',
    'iVoler Shockproof camera protect clear case Xiaomi': 'Силикон',
    'Space Clear': 'Силикон', 'Space collection': 'Силикон', 'Space with Magsafe': 'Силикон',
    '17 Series Style Case': 'Силикон', 'AG Glass': 'Стекло', 'AG-ACRYLICS MagsafeTitanium Case': 'Пластик',
    'AG-ACRYLICS Shine Case': 'Пластик', 'Apple Bracket Lans Case': 'Пластик', 'CrossBody iPhone': '',
    'Cs clear Stand with Magsafe': 'Силикон', 'Electroplate Magsafe Case': 'Силикон',
    'Figura MagSafe': 'Силикон', 'Flower Case': 'Силикон', 'iPhone Beats Silicone Case with Magsafe': 'Силикон',
    'iPhone Clear Case with MagSafe': 'Силикон', 'Magsafe Carbon Case': 'Силикон',
    'Magsafe Matte Case': 'Силикон', 'MCH Glass': 'Стекло', 'Ombre Shine': 'Силикон',
    'Shockproof, matte thin, carbon': '', 'Strap&Stand Case': 'Силикон', 'velure jeans print': 'Велюр',
    'Gradient IPhone': 'Силикон', 'Ribbed Chameleon IPhone': 'Силикон', 'VAWI Vanquish': 'Пластик',
    '3d case': 'Силикон', '3d Squish Cat Paw Fluffy Case': 'Силикон', 'ACRYLICS Shine Case': 'Пластик',
    'Alcantara iPhone': 'Алькантара', 'Anny Shining/ Chameleon': 'Силикон', 'Braid case': 'Силикон',
    'Camshield Case': 'Силикон', 'CCase кросбоді': 'Силикон', 'Clear Print Iphone': 'Силикон',
    'CS Clear case Iphone': 'Силикон', 'CS Clear Stand Case iPhone': 'Силикон',
    'iPhone silicone autofocus case': 'Силикон', 'Karl Lagerfield': '', 'LV Dior Gucci MiuMiu': '',
    'Rabbit Plush Case': 'Плюш', 'Rimowa Case': 'Пластик', 'Shockproof, Clear Ring IPhone': 'Силикон',
    'Winter Down': 'Текстиль',
    'Case.Pro': 'Кожа', 'Colour Splash leather': 'Кожа', 'IPhone Leather': 'Кожа',
    'iPhone Leather Magsafe Case': 'Кожа', 'Metal,Gator and Phyton Skin Case': '',
    'Брендовані шкіряні чохли': 'Кожа',
    'Swarovski Crystaline': '', 'Swarovski with Magsafe': '',
    'UAG APPLE': 'Пластик', 'uag apple': 'Пластик',
    'iPad Smart Case': 'Искусственная кожа', 'iPad Smart Case for Pencil': 'Искусственная кожа',
    'Samsung, Lenovo Smart Case': 'Искусственная кожа',
    'Crossbody Strap': '', 'Magsafe Ring': 'Металл', 'Присоски': '',
    'HardShell for Mac': 'Пластик',
    'Baseus cable': '', 'Baseus Charger': '', 'Baseus mouse': '', 'Baseus TWS': '',
    'Автомобільні аксесуари Baseus': '', 'HOCO car charger': '', 'HOCO Колонки': '',
    'Hoco Навушники': '', 'mouse hoco': '', 'Для ігроманів Hoco': '', 'Кабелі / блоки Hoco': '',
    'Павербанк Hoco/Borofone': '', 'Тримачі/ підставки Hoco': '',
}

# ================= обработчики =================
def h_silicone(group):
    brand_default = {'Huawei Silicone Case': 'Huawei', 'Samsung Silicone Case': 'Samsung',
                     'Xiaomi Silicone Case': 'Xiaomi'}[group]
    def h(code, short, full):
        m = re.match(r'^(?P<phone>.+?)\s+кол[іi]р\s*(?P<num>\d+)\s*$', fix_latin(short), re.I)
        if not m:
            flags.append((code, short, f'{group}: не разобран шаблон')); return {}
        num = int(m.group('num'))
        color = SIL_PALETTE.get(num)
        if not color:
            flags.append((code, short, f'{group}: нет цвета №{num} в палитре — оставлен номер'))
            color = '#' + str(num)
        phone = norm_phone(m.group('phone'))
        if group == 'Samsung Silicone Case':
            brand, model = 'Samsung', samsung_model(phone)
        elif group == 'Xiaomi Silicone Case':
            brand = 'Xiaomi'
            model = phone if phone.startswith('Xiaomi') else 'Xiaomi ' + phone
        else:
            brand = 'Honor' if phone.startswith('Honor') else 'Huawei'
            model = phone if phone.startswith(('Honor', 'Huawei')) else 'Huawei ' + phone
        return dict(brand=brand, model=model, color=color)
    return h

def glass_model_from(text, code, short):
    t = fix_latin(text)
    m = re.search(r'(?:iPhone|IPhone|Iphone)\s+([\w/\.\+ ()-]+?)(?:\s+(?:Magic|Mirror|Monkey|Premium|black|Black)|\s*$)', t)
    if m:
        return 'Apple', iphone_model(m.group(1))
    return None

def h_glass_iphone(code, short, full):
    # модели iPhone в начале или после слова iPhone; бывают и Xiaomi
    s = fix_latin(short)
    s = re.sub(r'^(GA Privacy Glass|Ga Privacy Glass|OG Purple|1:1)\s*', '', s)
    s = re.sub(r'^WK Kingkong [\w-]+\s+Privacy Glass\s*', '', s)
    s = norm_spaces(re.sub(r'\s*(Magic Box Glass|Mirror Privacy|Monkey King Clear Glass|Premium Clear|9H Glass/Aviation aluminium)\s*', ' ', s))
    rest, color = extract_trailing_color(s)
    rest = rest.strip()
    if not rest:
        flags.append((code, short, 'скло: модель не извлечена')); return {}
    if re.match(r'^(Xiaomi|Redmi|Poco|Mi\s?\d|Samsung|Honor|Huawei)', rest):
        brand, model = route_phone(rest, fix_latin(full))
        return dict(brand=brand, model=model, color=color)
    rest = re.sub(r'^(iPhone|IPhone|Iphone)\s*', '', rest).strip()
    return dict(brand='Apple', model=iphone_model(rest), color=color)

def h_glass_camera(code, short, full):
    s = fix_latin(short)
    s = re.sub(r'^(1:1|9H Glass/Aviation aluminium)\s*', '', s)
    s = re.sub(r'\b(Camera Glass|\d+pcs|Natural|Shine|PL)\b', ' ', s)
    s = norm_spaces(s)
    rest, color = extract_trailing_color(s)
    rest = norm_spaces(rest.rstrip(','))
    if re.match(r'^(S\d{2}|A\d{2}|Samsung|Xiaomi|Redmi|Poco|Mi\s?\d)', rest):
        brand, model = route_phone(rest, fix_latin(full))
        return dict(brand=brand, model=model, color=color)
    rest = re.sub(r'^(iPhone|IPhone|Iphone)\s*', '', rest).strip()
    return dict(brand='Apple', model=iphone_model(rest), color=color)

def h_og_huawei(code, short, full):
    s = re.sub(r'^OG Purple\s*', '', fix_latin(short)).strip()
    brand = 'Honor' if s.startswith('Honor') else 'Huawei'
    model = norm_phone(s) if s.startswith(('Honor', 'Huawei')) else 'Huawei ' + norm_phone(s)
    return dict(brand=brand, model=model)

def h_og_samsung(code, short, full):
    s = re.sub(r'^OG Purple\s*', '', fix_latin(short)).strip()
    rest, color = extract_trailing_color(s)
    return dict(brand='Samsung', model=samsung_model(rest), color=color)

def h_og_xiaomi(code, short, full):
    s = re.sub(r'^OG Purple\s*', '', fix_latin(short)).strip()
    rest, color = extract_trailing_color(s)
    model = rest if rest.startswith('Xiaomi') else 'Xiaomi ' + rest
    return dict(brand='Xiaomi', model=norm_phone(model), color=color)

def h_airpods(code, short, full):
    s = fix_latin(short)
    mm = re.search(r'(AirPods|Airpods)\s*(Pro\s*2|Pro2|Pro|1/2|2nd|3rd|[123])?', s, re.I)
    model = 'AirPods'
    if mm and mm.group(2):
        g = mm.group(2).replace('2nd', '2').replace('3rd', '3').replace('Pro2', 'Pro 2')
        model = 'AirPods ' + g
    if 'для Airpods 1/2' in full or 'для AirPods 1/2' in full:
        model = 'AirPods 1/2'
    color = ''
    m = re.search(r'#\d+\s+(.+)$', s)
    if m:
        raw = m.group(1).strip().strip('()').strip()
        # фирменное название цвета после номера — берём как написано
        color = ' '.join(w if not w.islower() else w.capitalize() for w in raw.split())
    else:
        s2 = re.sub(r'\s+[Cc]ase\s*$', '', s)
        _, color = extract_trailing_color(s2)
        if not color:
            # цвет в середине: '... Pink Case', 'AirPods 2 Pink Sand Case'
            cm = re.search(r'\b((?:\w+\s+)?\w+)\s+[Cc]ase\b', s)
            if cm:
                words = cm.group(1).split()
                parts = [w.lower() for w in words]
                if all(p in COLOR_CANON or p in COLOR_MODIFIERS for p in parts) and any(p in COLOR_CANON for p in parts):
                    color = canon_color_phrase(words)
        if not color:
            color = hash_num(s)
    return dict(brand='Apple', model=model, color=color)

def _samsung_case(code, short, full, strip_words, color_from='short'):
    """Общий обработчик Samsung-чехлов: модель из полного наименования 'на Samsung X' либо короткого."""
    s = fix_latin(short)
    for w in strip_words:
        s = re.sub(w, ' ', s, flags=re.I)
    s = norm_spaces(s)
    rest, color = extract_trailing_color(s)
    if not color:
        color = hash_num(s)
        if color: rest = re.sub(r'[#(]?\d+\)?\s*$', '', rest if rest else s).strip()
    if re.search(r'\b(Iphone|iPhone|IPhone)\b', short):
        r = h_iphone_case(code, short, full)
        if color and not r.get('color'): r['color'] = color
        return r
    fm = re.search(r'(?:на|для)\s+(?:мобільний телефон\s+)?Samsung\s+([\w/\.\+ ()-]+?)(?:\s+(?:з|кол|чорн|бiл|біл|син|червон|зелен|рожев|розов|жовт|фіолет|бордов|блакит|голуб|мят|беж|сір|коричн|кавов|шампань|прозор|Molan|Gradient|Magnetic|MagSafe|Light|INVISIBLE|DISCOVER|Slide|Crystal|Butterfly|Shockproof|Clear|Space|BoB|CapyBara|Hope|Kuromi|Minnie|Mirror|Pink|Teddy)\b|\s*$)', fix_latin(full))
    if fm:
        model_tok = re.sub(r'\bSamsung\b', '', fm.group(1)).strip()
        for w in strip_words:
            model_tok = norm_spaces(re.sub(w, ' ', model_tok, flags=re.I))
        model_tok = norm_spaces(re.sub(r'\b(Phone|Case|CASE|with|Ring)\b', ' ', model_tok))
        t2, c2 = extract_trailing_color(model_tok)
        if c2:
            model_tok = t2
            if not color: color = c2
        if not model_tok:
            flags.append((code, short, 'Samsung case: модель не извлечена')); return {}
        brand, model = route_phone(model_tok, fix_latin(full))
        return dict(brand=brand, model=model, color=color)
    # бренд не указан в полном наименовании — маршрутизация по токену
    fm2 = re.search(r'(?:на|для)\s+(?:мобільний телефон\s+)?([A-Za-z][\w/\.\+ -]*?)\s+(?:з\s|кол|чорн|біл|син|червон|зелен|рожев|розов|жовт|фіолет|бордов|блакит|голуб|мят|беж|сір|коричн|кавов|шампань|прозор)', fix_latin(full))
    model_tok = fm2.group(1).strip() if fm2 else rest
    model_tok = re.sub(r'\bSamsung\b', '', model_tok).strip()
    if not model_tok:
        flags.append((code, short, 'Samsung case: модель не извлечена')); return {}
    brand, model = route_phone(model_tok, fix_latin(full))
    return dict(brand=brand, model=model, color=color)

def h_generic_samsung(strip_patterns, extra=None):
    def h(code, short, full):
        r = _samsung_case(code, short, full, strip_patterns)
        if extra and r: r.update(extra)
        return r
    return h

def h_crystal_cam(code, short, full):
    # 'A15 Pearl Marble Crystal Cam Case', 'A16 Light Green Glacier Full Cam Case'
    s = fix_latin(short)
    s = re.sub(r'\s+(Crystal|Full)?\s*Cam Case\s*$', '', s)
    s = re.sub(r'\s+(Marble|Wave|Glacier|Gradient|Shiny|Shine)\s*$', '', s)
    rest, color = extract_trailing_color(s)
    if not color:
        # цвет одним словом перед линейкой
        m = re.match(r'^(?P<model>[\w/ ]+?)\s+(?P<color>\w+)$', s)
        if m:
            rest, color = m.group('model'), canon_color_phrase([m.group('color')])
        else:
            rest = s
    return dict(brand='Samsung', model=samsung_model(rest), color=color)

def h_lv_glass(code, short, full):
    m = re.match(r'^(?P<model>.+?)\s+LV Glass Case\s+(?P<num>\d+)\s*$', fix_latin(short))
    if not m:
        return _samsung_case(code, short, full, [r'LV Glass Case'])
    brand, model = route_phone(m.group('model'), fix_latin(full))
    return dict(brand=brand, model=model, color='#' + str(int(m.group('num'))))

def h_uag_samsung(code, short, full):
    s = fix_latin(short)
    m = re.search(r'Samsung\s+(.+?)\s+(?:Pathfinder|Monarch|Plyo|Civilian|Metropolis|Plasma|Essential|Scout|Lucent)\b[, ]*(.*)$', s)
    if m:
        return dict(brand='Samsung', model=samsung_model(m.group(1)),
                    color=canon_color_phrase(m.group(2).replace(',', ' ').split()) if m.group(2).strip() else '')
    m2 = re.search(r'Samsung\s+([\w/\.\+ ]+)$', s)
    if m2:
        return dict(brand='Samsung', model=samsung_model(m2.group(1)))
    flags.append((code, short, 'UAG Samsung: не разобран')); return {}

def h_glossy(code, short, full):
    m = re.match(r'^Glossy\s+(?P<model>.+?)\s+\((?P<num>\d+)\)$', fix_latin(short))
    if not m:
        return _samsung_case(code, short, full, [r'^Glossy'])
    color = ua_color(full) or ('#' + str(int(m.group('num'))))
    brand, model = route_phone(m.group('model'), fix_latin(full))
    return dict(brand=brand, model=model, color=color)

def h_shine_tpu(brand_name, strip=r'^Shine TPU Case\s*'):
    def h(code, short, full):
        s = re.sub(strip, '', fix_latin(short)).strip()
        rest, color = extract_trailing_color(s)
        if brand_name == 'Samsung':
            rest = re.sub(r'^Samsung\s*', '', rest)
            return dict(brand='Samsung', model=samsung_model(rest), color=color)
        if brand_name == 'Xiaomi':
            model = rest if rest.startswith(('Xiaomi', 'Redmi', 'Poco', 'Mi ')) else 'Xiaomi ' + rest
            if model.startswith(('Redmi', 'Poco', 'Mi ')): model = 'Xiaomi ' + model
            return dict(brand='Xiaomi', model=norm_phone(model), color=color)
        # Huawei
        rest = re.sub(r'^Huawei\s*', '', rest)
        brand = 'Honor' if rest.startswith('Honor') else 'Huawei'
        model = norm_phone(('Huawei ' if brand == 'Huawei' else '') + rest)
        return dict(brand=brand, model=model, color=color)
    return h

def h_flower_print(code, short, full):
    s = re.sub(r'^Flowers Print\s*', '', fix_latin(short)).strip()
    rest, color = extract_trailing_color(s)
    rest = re.sub(r'^Huawei\s*', '', rest)
    brand = 'Honor' if 'Honor' in rest else 'Huawei'
    model = norm_phone(rest if rest.startswith(('Honor', 'Huawei')) else 'Huawei ' + rest)
    return dict(brand=brand, model=model, color=color)

# --- Apple Watch ремешки ---
def watch_size(text):
    m = re.search(r'(\d{2}(?:[/-]\d{2}){0,4}\s*mm)', text)
    return m.group(1).replace(' ', '') if m else ''

def h_watch_band(material_override=None, color_mode='auto'):
    def h(code, short, full):
        s = fix_latin(short)
        size = watch_size(s)
        model = ('Apple Watch ' + size) if size else 'Apple Watch'
        color = ''
        if color_mode in ('auto', 'hash'):
            color = hash_num(s)
            if not color:
                km = re.search(r'кол[іi]р\s*(\d+)', s, re.I)
                if km: color = '#' + str(int(km.group(1)))
        if not color and color_mode != 'hash' and color_mode != 'none':
            s2 = re.sub(r'\s*(Apple\s+)?[Ww]atch\s+[Bb]and\s*$', '', s.rstrip("'"))
            s2 = re.sub(r'\s*[Bb]and\s*$', '', s2)
            _, color = extract_trailing_color(s2)
            if not color:
                m = re.search(r'(\w[\w/-]*)\s+[Bb]and\s*$', s)
                if m:
                    parts = re.split(r'[/-]', m.group(1).lower())
                    if all(p in COLOR_CANON for p in parts if p):
                        color = canon_color_phrase(re.split(r'([/-])', m.group(1)))
            if not color:
                # поиск цветового слова в любом месте
                for w in s.split():
                    if w.lower() in COLOR_CANON:
                        color = COLOR_CANON[w.lower()]; break
        r = dict(brand='Apple', model=model, color=color)
        if material_override is not None: r['material'] = material_override
        return r
    return h

def h_metal_ceramic(code, short, full):
    r = h_watch_band()(code, short, full)
    r['material'] = 'Керамика' if re.search(r'ceramic', short, re.I) else 'Металл'
    return r

def h_18mm_band(code, short, full):
    s = fix_latin(short)
    size = re.search(r'(\d{2})mm', s)
    model = (size.group(1) + 'mm') if size else ''
    rest, color = extract_trailing_color(re.sub(r'Watch Band\s*$', '', s).strip())
    mat = ''
    if re.search(r'leather', s, re.I): mat = 'Кожа'
    elif re.search(r'milan|metal', s, re.I): mat = 'Металл'
    elif re.search(r'silicone', s, re.I): mat = 'Силикон'
    elif re.search(r'nylon', s, re.I): mat = 'Нейлон'
    return dict(brand='', model=model, color=color, material=mat)

def h_mi_band(model_hint=None, color_paren=False):
    def h(code, short, full):
        s = fix_latin(short)
        mm = re.search(r'Mi\s*(?:Band\s*)?([\d/]+)', s)
        model = 'Xiaomi Mi Band ' + mm.group(1) if mm else (model_hint or 'Xiaomi Mi Band')
        fmm = re.search(r'Mi Band\s*([\d/]+)', full)
        if fmm:
            model = 'Xiaomi Mi Band ' + fmm.group(1)
        color = ''
        if color_paren:
            pm = re.search(r'\(([^)]+)\)\s*$', s)
            if pm:
                color = ' '.join(w if not w.islower() else w.capitalize() for w in pm.group(1).split())
        if not color:
            _, color = extract_trailing_color(s)
        if not color:
            m2 = re.search(r'Band\s+(\d+)\s*$', s)
            color = hash_num(s) or ('#' + m2.group(1) if m2 else '')
        return dict(brand='Xiaomi', model=model, color=color)
    return h

# --- Baseus / Hoco аксессуары ---
ACC_BRANDS = ['Baseus', 'Borofone', 'BOROFONE', 'HOCO', 'Hoco', 'hoco', 'ACEFAST', 'Acefast']
def h_accessory(default_brand=''):
    def h(code, short, full):
        s = fix_latin(short)
        brand = default_brand
        for b in ACC_BRANDS:
            if re.search(r'\b' + b + r'\b', s):
                brand = b.capitalize() if b.lower() != 'acefast' else 'Acefast'
                break
        brand = {'Hoco': 'Hoco', 'Baseus': 'Baseus', 'Borofone': 'Borofone', 'Acefast': 'Acefast'}.get(brand, brand)
        _, color = extract_trailing_color(s.rstrip(')'))
        mm = re.search(r'\b([A-Z]{1,3}\d{1,4}[A-Za-z+]{0,7})\b', s)
        model = mm.group(1) if mm else ''
        return dict(brand=brand, model=model, color=color)
    return h

# --- iPhone-чехлы (универсальный) ---
IPHONE_LINE_WORDS = r'(DISCOVER INNOVATION CASE|INVISIBLE BRACKET CASE|Slide Phone Case with Ring|17 Series|AG Glass Case MagSafe|AG-ACRYLICS MagsafeTitanium Case for|AG-ACRYLICS SHINE Magsafe Case for|Bracket Lans Case|CrossBody Case|CS Clear Stand MagSafe Case|ElectroPlate Magsafe Case|Figura MagSafe|Sakura Case with Magsafe|Daisies Case with Magsafe|Flowers Case with Magsafe|iPhone Beats Silicone Case with Magsafe|Clear Case with MagSafe|Carbon MagSafe|Magsafe Matte Case|MCH Glass Case|Ombre Shine Magsafe Case|Carbon Fiber Ring Shell|Strap&Stand Case|Velure|Chocolate Magsafe Full Case|Gradient Magsafe Case|Ribbed Chameleon IPhone Case|VAWI Vanquish|Coffee Shop|Squish Cat Paw Case|Acrylic Shine|Чохол Alcantara|Gold Shine Case|Braid Case|Camshield Case|CCase|Clear Print Case|CS Clear Case|CS Clear Stand Case|silicone autofocus case|Karl Lagerfeld Case|Rabbit|Rimowa Case|Clear Ring case|Winter Down|Case\.Pro|Colour Splash Leather Case|Leather Case|MagSafe Leather Case|Gator Skin|Phyton Skin|Metal Skin|Чохол Swarovski Crystalline на|Swarovski Case with MagSafe на|UAG [\w ]+? для|Transparent Pocket|iVoler Shockproof camera protect clear case|Space with Magsafe|Space|with Magsafe|with MagSafe)'

def h_iphone_case(code, short, full):
    s = fix_latin(short)
    s = re.sub(r'^Чохол\s+', '', s)
    s = re.sub(r'^[A-Z0-9]{6,}/A\s+', '', s)                       # артикулы Apple типа MQTE2FE/A
    s = re.sub(r'\s+nolo\s*$', '', s, flags=re.I)                  # 'nolo' = без логотипа
    s = re.sub(r'(iPhone|IPhone|Iphone)(\d|X)', r'\1 \2', s)       # iPhone11Pro -> iPhone 11Pro
    color = ''
    pm = re.search(r'\(\s*PRODUCT\s*\)\s*RED', s, re.I)
    if pm:
        color = 'Red'; s = s.replace(pm.group(0), ' ')
    if not color:
        pm = re.search(r'\(([\w /-]+)\)\s*$', s)
        if pm:
            parts = [p for p in re.split(r'[ /-]', pm.group(1).lower()) if p]
            if parts and all(p in COLOR_CANON or p in COLOR_MODIFIERS for p in parts) and any(p in COLOR_CANON for p in parts):
                color = canon_color_phrase(re.split(r'([/-])', pm.group(1)))
                s = s[:pm.start()].strip()
    if not color:
        color = hash_num(s)
    rest = s
    if color and '#' in s:
        rest = re.sub(r'#\s*\d+\s*', ' ', rest)
    elif not color:
        rest, color = extract_trailing_color(s)
    rest = re.sub(IPHONE_LINE_WORDS, ' ', rest)
    rest = re.sub(r'\b(iPhone|IPhone|Iphone|Case|case)\b', ' ', rest)
    rest = norm_spaces(re.sub(r'[,]', ' ', rest))
    rest2, c2 = extract_trailing_color(rest)
    if c2 and not color:
        rest, color = rest2, c2
    # Samsung-модели внутри «iPhone»-групп (напр. Acrylic Shine S24)
    sm = re.match(r'^([SAM]\d{2}[\w/ ]*?)\s*$', rest)
    if sm:
        return dict(brand='Samsung', model=samsung_model(sm.group(1)), color=color)
    # выделить модель: цифры/X/Air/SE/mini/Pro/Max/Plus/слэши
    m = re.search(r'\b((?:1[1-9]|[6-9]|X[SRsr]?|SE|Air)[\w/\.\+ ]*?)\s*$', rest)
    if not m or not m.group(1).strip():
        flags.append((code, short, 'iPhone case: модель не извлечена из «%s»' % rest))
        return dict(brand='Apple', model='', color=color)
    return dict(brand='Apple', model=iphone_model(m.group(1)), color=color)

def h_pocket(code, short, full):
    s = re.sub(r'^Transparent Pocket\s*', '', fix_latin(short)).strip()
    rest, color = extract_trailing_color(s)
    if not color: color = 'Clear'
    t = norm_spaces(rest)
    if t.startswith('Samsung'):
        return dict(brand='Samsung', model=samsung_model(t), color=color)
    if re.match(r'^(Redmi|Poco|Mi |Xiaomi|Note)', t):
        return dict(brand='Xiaomi', model=norm_phone('Xiaomi ' + t if not t.startswith('Xiaomi') else t), color=color)
    return dict(brand='Apple', model=iphone_model(re.sub(r'iPhone|IPhone|Iphone', '', t)), color=color)

def h_space_clear(code, short, full):
    s = fix_latin(short)
    s = re.sub(r'^Чохол\s+', '', s).replace('Space', ' ').strip()
    rest, color = extract_trailing_color(s)
    if not color: color = 'Clear'
    fm = re.search(r'на\s+Samsung\s+([\w/\.\+ ]+?)\s+(?:прозор|чорн|біл)', fix_latin(full))
    if fm:
        return dict(brand='Samsung', model=samsung_model(fm.group(1)), color=color)
    t = norm_spaces(rest)
    if t.startswith('Moto'):
        return dict(brand='Motorola', model='Motorola ' + norm_phone(t), color=color)
    if re.match(r'^(Redmi|Poco|Mi |Xiaomi|Note)', t):
        return dict(brand='Xiaomi', model=norm_phone('Xiaomi ' + t if not t.startswith('Xiaomi') else t), color=color)
    if re.match(r'^(iPhone|Iphone|IPhone|\d)', t) or 'phone' in t.lower():
        return dict(brand='Apple', model=iphone_model(re.sub(r'iPhone|IPhone|Iphone', '', t)), color=color)
    if re.match(r'^(A\d|S\d|M\d|J\d|Note \d+$|Fold|Flip)', t):
        return dict(brand='Samsung', model=samsung_model(t), color=color)
    flags.append((code, short, f'Space: бренд не определён «{t}»'))
    return dict(brand='', model=norm_phone(t), color=color)

def h_space_collection(code, short, full):
    s = fix_latin(short)
    m = re.match(r'^(?P<model>.+?)\s+(Iphone|iPhone|IPhone)\s+Space\s*(?P<rest>.*)$', s)
    if m:
        color = canon_color_phrase(m.group('rest').split()) if m.group('rest') else ''
        return dict(brand='Apple', model=iphone_model(m.group('model')), color=color)
    return h_space_clear(code, short, full)

def h_ipad(code, short, full):
    s = fix_latin(short)
    s = re.sub(r'\s+Smart Case|\s+Case for Pencil', ' ', s)
    # убрать украинские скобки-дубли цвета: '(кактус)', '(пайн грін)' — но не '(7,8,9 покоління...)'
    s = re.sub(r'\(([^)\d]*)\)', ' ', s)
    # убрать украинский дубль цвета в конце
    s = re.sub(r'\s+(чорний|синій|сірий|рожевий|червоний|зелений|блакитний|фіолетовий|бордовий|золотий|м\'ятний|бежевий|коричневий|темно-зелений|голубий|малиновий|лавандовий|персиковий|графітовий)\s*$', '', s, flags=re.I)
    rest, color = extract_trailing_color(norm_spaces(s))
    if not color:
        color = ua_color(short) or ua_color(full)
    return dict(brand='Apple', model=norm_spaces(rest), color=color)

def h_tablet_other(code, short, full):
    s = fix_latin(short)
    rest, color = extract_trailing_color(s)
    rest = re.sub(r'\s*(Book Cover|Smart Case)\s*', ' ', rest)
    brand = 'Lenovo' if 'Lenovo' in rest else ('Samsung' if re.search(r'Samsung|Tab [SA]', rest) else '')
    if not color: color = ua_color(full)
    if not brand:
        flags.append((code, short, 'планшет: бренд не определён'))
    return dict(brand=brand, model=norm_spaces(rest), color=color)

def h_crossbody_strap(code, short, full):
    s = fix_latin(short).replace('Crossbody Strap', '').strip()
    length = '(short)' in s
    s = s.replace('(short)', '').strip()
    color = canon_color_phrase(s.split()) if s else ''
    return dict(brand='', model='', color=color)

def h_magsafe_ring(code, short, full):
    s = fix_latin(short).replace('MagSafe Ring', '').strip()
    return dict(brand='', model='', color=canon_color_phrase(s.split()) if s else '')

def h_prisoska(code, short, full):
    return dict(brand='', model='', color=ua_color(short))

def h_mac(code, short, full):
    s = fix_latin(short)
    s = re.sub(r'\s*Фетр\s*$', '', s, flags=re.I)
    color = ''
    if re.search(r'[ТT]емно[- ][СC]ірий', s, flags=re.I):
        color = 'Dark Grey'
        s = re.sub(r'\s*[ТT]емно[- ][СC]ірий\s*', ' ', s, flags=re.I)
    if not color:
        rest, color = extract_trailing_color(s)
        s = rest or s
    if not color:
        color = ua_color(short) or ua_color(full)
    fm = re.search(r'(?:Macbook|MacBook)\s+(.+)$', full)
    model = 'Macbook ' + norm_spaces(fm.group(1)) if fm else norm_spaces(s.replace('HardShell for Mac', 'Macbook'))
    model = re.sub(r'\s+(Темно[- ]?)?([чЧ]орн|[бБ]іл|[сСcC]ір|[пП]розор|[рР]ожев|[сС]ин)[\w\']*', '', model)
    mat = 'Фетр' if re.search(r'фетр', short + full, flags=re.I) else 'Пластик'
    return dict(brand='Apple', model=model, color=color, material=mat)

def h_karl(code, short, full):
    s = fix_latin(short)
    m = re.match(r'^Karl Lagerfeld Case\s+(?P<model>[\w/\. ]+?)\s+(?P<design>.+)$', s)
    if m:
        return dict(brand='Apple', model=iphone_model(m.group('model')), color='')
    return h_iphone_case(code, short, full)

def h_lv_dior(code, short, full):
    s = re.sub(r'\b[Cc]ase\b', ' ', fix_latin(short))
    s = norm_spaces(s)
    rest, color = extract_trailing_color(s)
    m = re.search(r'(?:iPhone|IPhone|Iphone)\s+([\w/\.\+ ]+?)\s*$', rest)
    if m:
        return dict(brand='Apple', model=iphone_model(m.group(1)), color=color)
    flags.append((code, short, 'LV Dior: модель не извлечена'))
    return dict(brand='Apple', model='', color=color)

def h_brand_leather(code, short, full):
    s, _color = extract_trailing_color(fix_latin(short))
    m = re.search(r'(?:iPhone|IPhone|Iphone)\s+([\w/\.\+ ]+?)\s*$', s)
    if m:
        return dict(brand='Apple', model=iphone_model(m.group(1)), color='')
    flags.append((code, short, 'бренд.кожа: модель не извлечена'))
    return dict(brand='Apple', model='', color='')

HANDLERS = {
    'Huawei Silicone Case': h_silicone('Huawei Silicone Case'),
    'Samsung Silicone Case': h_silicone('Samsung Silicone Case'),
    'Xiaomi Silicone Case': h_silicone('Xiaomi Silicone Case'),
    'Magic box glass': h_glass_iphone, 'Mirror Privacy Glass': h_glass_iphone,
    'Monkey King Clear Glass': h_glass_iphone, 'Privacy': h_glass_iphone,
    'Скло Premium Clear': h_glass_iphone,
    'Захисне скло для камери на iPhone': h_glass_camera,
    'Захисне скло/модуль на камеру': h_glass_camera,
    'OG Purple Huawei': h_og_huawei, 'OG Purple iPhone': h_glass_iphone,
    'OG Purple Samsung': h_og_samsung, 'OG Purple Xiaomi': h_og_xiaomi,
    'Alca': h_airpods, 'Leather': h_airpods, 'Logo': h_airpods, 'Marble': h_airpods, 'інші': h_airpods,
    'Butterfly Case': h_generic_samsung([r'Butterfly']),
    'Carbon Samsung Case': h_generic_samsung([r'Carbon MagSafe', r'Samsung']),
    'Clear Ring': h_generic_samsung([r'Clear Ring case', r'Samsung']),
    'Crystal Cam Case': h_crystal_cam,
    'Flower Print': h_flower_print,
    'Gradient Magsafe Case Samsung': h_generic_samsung([r'Gradient Magsafe Case', r'Samsung']),
    'INVISIBLE BRACKET': h_generic_samsung([r'INVISIBLE BRACKET CASE']),
    'LV Glass Case': h_lv_glass,
    'Magnetic Samsung Case': h_generic_samsung([r'^Magnetic']),
    'MagSafe Matte Samsung Case': h_generic_samsung([r'MagSafe Matte']),
    'Molan Cano Jelly Card Case': h_generic_samsung([r'Molan Cano Jelly Card Case']),
    'Molan Cano Jelly Sparkle': h_generic_samsung([r'Molan Cano Jelly Sparkle']),
    'Molan Cano Shockproof': h_generic_samsung([r'Molan Cano Shockproof case']),
    'Shockproof Android Case': h_generic_samsung([r'Shockproof case', r'Samsung']),
    'Space Capsule': h_generic_samsung([r'Space Capsule case', r'Samsung'], extra={'color': 'Clear'}),
    'UAG Samsung': h_uag_samsung,
    'Clear print': h_generic_samsung([r'Clear Print Case', r'Samsung']),
    'Glossy case': h_glossy,
    'Glossy Clear case': h_generic_samsung([r'Glossy Clear']),
    'Glossy Clear Chain': h_generic_samsung([r'Glossy Clear Chain']),
    'BoB Monster': h_generic_samsung([r'BoB Monster Case']),
    'CapyBara': h_generic_samsung([r'CapyBara Case']),
    'Hope case': h_generic_samsung([r'Hope Case']),
    'Kuromi Pocket Case': h_generic_samsung([r'Kuromi Pocket Case']),
    'Minnie Mouse': h_generic_samsung([r'Minnie Mouse Case']),
    'Mirror Star Crossbody Case': h_generic_samsung([r'Mirror Star Crossbody Case']),
    'Pink Rabbit': h_generic_samsung([r'Pink Rabbit Case']),
    'Teddy Bear': h_generic_samsung([r'Teddy Bear Case']),
    'Light Wings': h_generic_samsung([r'Light Wings']),
    'Light wings Diamond': h_generic_samsung([r'Light wings Diamond']),
    'Light with Magsafe': h_generic_samsung([r'Light Wings with Magsafe']),
    'Shine TPU Huawei': h_shine_tpu('Huawei'),
    'Shine TPU Samsung': h_shine_tpu('Samsung'),
    'Shine TPU Xiaomi': h_shine_tpu('Xiaomi'),
    'Discover innovation case': h_generic_samsung([r'DISCOVER INNOVATION CASE']),
    'Magnetic Magsafe Samsung case': h_generic_samsung([r'Magnetic Case']),
    'Slide Phone Case with Ring': h_generic_samsung([r'Slide Phone Case with Ring']),
    'Alpine Band': h_watch_band(color_mode='name'),
    'Apple watch Band AP LV Gucci': h_watch_band(color_mode='none'),
    "Apple Watch Nike Sport Band's": h_watch_band(),
    'Apple Watch Sport Band': h_watch_band(),
    'Leather apple watch band': h_watch_band(color_mode='name'),
    'LV Gucci Hermes': h_watch_band(color_mode='name'),
    'Metal /Ceramic Apple watch band': h_metal_ceramic,
    'milanese band': h_watch_band(color_mode='name'),
    'Nylon band': h_watch_band(color_mode='name'),
    'Solo Loop': h_watch_band(),
    'Van Cleef': h_watch_band(color_mode='name'),
    '18/24mm Band': h_18mm_band,
    'Mi 3/4 Band': h_mi_band(),
    'Mi 3/4/5/6': h_mi_band(color_paren=True),
    'Milanese': h_mi_band(),
    'Mi 5/6 Band': h_mi_band(),
    'Mi 7 3-Bead': h_mi_band(),
    'Mi 7 Milanes': h_mi_band(),
    'Mi Silicone Band': h_mi_band(),
    'Baseus cable': h_accessory('Baseus'), 'Baseus Charger': h_accessory('Baseus'),
    'Baseus mouse': h_accessory('Baseus'), 'Baseus TWS': h_accessory('Baseus'),
    'Автомобільні аксесуари Baseus': h_accessory('Baseus'),
    'HOCO car charger': h_accessory('Hoco'), 'HOCO Колонки': h_accessory('Hoco'),
    'Hoco Навушники': h_accessory('Hoco'), 'mouse hoco': h_accessory('Hoco'),
    'Для ігроманів Hoco': h_accessory('Hoco'), 'Кабелі / блоки Hoco': h_accessory('Hoco'),
    'Павербанк Hoco/Borofone': h_accessory('Hoco'), 'Тримачі/ підставки Hoco': h_accessory('Hoco'),
    'Прозорі чохли Pocket': h_pocket,
    'iVoler Shockproof camera protect clear case iPhone': h_iphone_case,
    'iVoler Shockproof camera protect clear case Samsung': h_generic_samsung([r'iVoler Shockproof camera protect clear case'], extra={'color': 'Clear'}),
    'iVoler Shockproof camera protect clear case Xiaomi': h_shine_tpu('Xiaomi', strip=r'^iVoler Shockproof camera protect clear case\s*'),
    'Space Clear': h_space_clear,
    'Space collection': h_space_collection,
    'Space with Magsafe': h_space_clear,
    'Samsung, Lenovo Smart Case': h_tablet_other,
    'iPad Smart Case': h_ipad, 'iPad Smart Case for Pencil': h_ipad,
    'Crossbody Strap': h_crossbody_strap, 'Magsafe Ring': h_magsafe_ring, 'Присоски': h_prisoska,
    'HardShell for Mac': h_mac,
    'Karl Lagerfield': h_karl,
    'LV Dior Gucci MiuMiu': h_lv_dior,
    'Брендовані шкіряні чохли': h_brand_leather,
    '3d case': h_lv_dior,
}
COLOR_DEFAULT = {
    'iPhone Clear Case with MagSafe': 'Clear',
    'iVoler Shockproof camera protect clear case iPhone': 'Clear',
    'iVoler Shockproof camera protect clear case Xiaomi': 'Clear',
    'Space Capsule': 'Clear',
}

# все остальные iPhone-группы -> универсальный обработчик
IPHONE_GROUPS = ['17 Series Style Case', 'AG Glass', 'AG-ACRYLICS MagsafeTitanium Case',
    'AG-ACRYLICS Shine Case', 'Apple Bracket Lans Case', 'CrossBody iPhone',
    'Cs clear Stand with Magsafe', 'Electroplate Magsafe Case', 'Figura MagSafe', 'Flower Case',
    'iPhone Beats Silicone Case with Magsafe', 'iPhone Clear Case with MagSafe',
    'Magsafe Carbon Case', 'Magsafe Matte Case', 'MCH Glass', 'Ombre Shine',
    'Shockproof, matte thin, carbon', 'Strap&Stand Case', 'velure jeans print',
    'Gradient IPhone', 'Ribbed Chameleon IPhone', 'VAWI Vanquish',
    '3d Squish Cat Paw Fluffy Case', 'ACRYLICS Shine Case', 'Alcantara iPhone',
    'Anny Shining/ Chameleon', 'Braid case', 'Camshield Case', 'CCase кросбоді',
    'Clear Print Iphone', 'CS Clear case Iphone', 'CS Clear Stand Case iPhone',
    'iPhone silicone autofocus case', 'Rabbit Plush Case', 'Rimowa Case',
    'Shockproof, Clear Ring IPhone', 'Winter Down', 'Case.Pro', 'Colour Splash leather',
    'IPhone Leather', 'iPhone Leather Magsafe Case', 'Metal,Gator and Phyton Skin Case',
    'Swarovski Crystaline', 'Swarovski with Magsafe', 'uag apple']
for g in IPHONE_GROUPS:
    HANDLERS.setdefault(g, h_iphone_case)


# ================= резолвер групп (из group_config.json) =================
_CFG_PATH = os.path.join(os.path.dirname(__file__), 'group_config.json')
with open(_CFG_PATH, encoding='utf-8') as _f:
    _CONFIG = json.load(_f)

_GROUPS_BY_ID = {g['id']: g for g in _CONFIG['groups']}
_SOURCE_MAP_T2 = _CONFIG['source_map_t2']
# нормализованные по пробелам ключи для отказоустойчивого сопоставления
_SOURCE_MAP_T2_NORM = {norm_spaces(k): v for k, v in _SOURCE_MAP_T2.items()}
HANDLERS_NORM = {norm_spaces(k): v for k, v in HANDLERS.items()}
MATERIAL_NORM = {norm_spaces(k): v for k, v in MATERIAL.items()}
COLOR_DEFAULT_NORM = {norm_spaces(k): v for k, v in COLOR_DEFAULT.items()}


def _lookup(d, raw):
    if raw in d:
        return d[raw]
    return d.get(norm_spaces(str(raw)))


def is_t2_group(raw):
    return (raw in _SOURCE_MAP_T2) or (norm_spaces(str(raw)) in _SOURCE_MAP_T2_NORM)


def resolve_group_columns(raw):
    """(Назва_групи_сайту, Ідентифікатор_групи, 'PID/Назва родителя') по имени листовой группы."""
    gid = _lookup(_SOURCE_MAP_T2, raw)
    if gid is None:
        return None, None, ''
    g = _GROUPS_BY_ID.get(gid, {})
    name = g.get('name', norm_spaces(str(raw)))
    pid = g.get('parent_id')
    if pid and pid in _GROUPS_BY_ID:
        parent = f"{pid}/{_GROUPS_BY_ID[pid]['name']}"
    else:
        parent = ''
    return name, gid, parent


def parse(raw, code, short, full):
    """Разобрать один товар второй партии. Возвращает поля товара или None (не t2-группа)."""
    if not is_t2_group(raw):
        return None
    short = norm_spaces(str(short))
    full = norm_spaces(str(full)) if full else short
    handler = _lookup(HANDLERS_NORM, raw)
    if handler is None:
        # известная по конфигу группа без обработчика — generic (iPhone-подход) + флаг
        flags.append((code, short, f't2: нет обработчика для группы «{raw}» — generic'))
        parsed = h_iphone_case(code, short, full) or {}
    else:
        parsed = handler(code, short, full) or {}
    name, gid, parent = resolve_group_columns(raw)
    mat = parsed.get('material', _lookup(MATERIAL_NORM, raw) or '')
    color = parsed.get('color', '')
    if not color:
        cd = _lookup(COLOR_DEFAULT_NORM, raw)
        if cd:
            color = cd
    return dict(
        brand=parsed.get('brand', ''), model=norm_spaces(parsed.get('model', '') or ''),
        color=color, material=mat,
        group=name, gid=gid, parent=parent,
    )
