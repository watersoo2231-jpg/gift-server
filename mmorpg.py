from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit
import time
import math
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'mmorpg-secret-key-2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

MAP_WIDTH = 3200
MAP_HEIGHT = 2400
TICK_RATE = 0.033

# ─── 몬스터 종류 ────────────────────────────────────────────
MONSTERS = {
    'drone':      {'name': '정찰 드론',   'hp': 30,  'max_hp': 30,  'atk': 6,  'def': 1,  'exp': 10,  'gold': 5,   'speed': 70, 'range': 50, 'color': '#00E5FF', 'size': 16, 'drop_rate': 0.20},
    'combat_bot': {'name': '전투 로봇',   'hp': 65,  'max_hp': 65,  'atk': 14, 'def': 6,  'exp': 28,  'gold': 14,  'speed': 55, 'range': 55, 'color': '#FF6D00', 'size': 22, 'drop_rate': 0.30},
    'cyborg':     {'name': '사이보그',    'hp': 130, 'max_hp': 130, 'atk': 24, 'def': 13, 'exp': 60,  'gold': 28,  'speed': 48, 'range': 62, 'color': '#76FF03', 'size': 26, 'drop_rate': 0.40},
    'mech_guard': {'name': '메카 가디언', 'hp': 85,  'max_hp': 85,  'atk': 20, 'def': 10, 'exp': 45,  'gold': 20,  'speed': 42, 'range': 65, 'color': '#EA80FC', 'size': 24, 'drop_rate': 0.35},
    'neo_boss':   {'name': '네오 보스',   'hp': 550, 'max_hp': 550, 'atk': 65, 'def': 32, 'exp': 350, 'gold': 180, 'speed': 50, 'range': 90, 'color': '#FF1744', 'size': 46, 'drop_rate': 0.95},
}

# ─── 아이템 희귀도 ────────────────────────────────────────
RARITY_COLORS = {
    'normal': '#aaaaaa',
    'rare':   '#4CAF50',
    'epic':   '#2196F3',
    'hero':   '#9C27B0',
    'legend': '#FF9800',
}

# ─── 장비 아이템 정의 ─────────────────────────────────────
EQUIPMENT_ITEMS = {
    # 건슬링거 무기
    '글록-17':       {'slot': 'weapon', 'for_class': '건슬링거', 'atk': 12, 'rarity': 'normal'},
    '데저트이글':    {'slot': 'weapon', 'for_class': '건슬링거', 'atk': 24, 'rarity': 'rare'},
    '플라즈마 라이플':{'slot':'weapon', 'for_class': '건슬링거', 'atk': 42, 'rarity': 'epic'},
    # 메카닉 무기
    '드릴 암':       {'slot': 'weapon', 'for_class': '메카닉', 'atk': 10, 'def': 5, 'rarity': 'normal'},
    '나노 해머':     {'slot': 'weapon', 'for_class': '메카닉', 'atk': 20, 'def': 10, 'rarity': 'rare'},
    '퀀텀 드릴':     {'slot': 'weapon', 'for_class': '메카닉', 'atk': 34, 'def': 18, 'rarity': 'epic'},
    # 해커 무기
    '해킹 장치':     {'slot': 'weapon', 'for_class': '해커', 'atk': 10, 'mp': 25, 'rarity': 'normal'},
    '양자 인터페이스':{'slot':'weapon', 'for_class': '해커', 'atk': 20, 'mp': 50, 'rarity': 'rare'},
    '뉴럴 링크':     {'slot': 'weapon', 'for_class': '해커', 'atk': 34, 'mp': 85, 'rarity': 'epic'},
    # 닌자 무기
    '단분자 표창':   {'slot': 'weapon', 'for_class': '닌자', 'atk': 14, 'spd': 10, 'rarity': 'normal'},
    '나노 카타나':   {'slot': 'weapon', 'for_class': '닌자', 'atk': 26, 'spd': 18, 'rarity': 'rare'},
    '그림자 도':     {'slot': 'weapon', 'for_class': '닌자', 'atk': 42, 'spd': 30, 'rarity': 'epic'},
    # 헤비건너 무기
    'RPG-X':         {'slot': 'weapon', 'for_class': '헤비건너', 'atk': 16, 'rarity': 'normal'},
    '미니건 MK2':    {'slot': 'weapon', 'for_class': '헤비건너', 'atk': 28, 'rarity': 'rare'},
    '레일건 프로토': {'slot': 'weapon', 'for_class': '헤비건너', 'atk': 46, 'rarity': 'epic'},
    # 공용 투구
    '탄소 투구':     {'slot': 'helmet', 'def': 5,  'hp': 25,  'rarity': 'normal'},
    '네오 바이저':   {'slot': 'helmet', 'def': 10, 'hp': 50,  'rarity': 'rare'},
    '사이버 헬름':   {'slot': 'helmet', 'def': 18, 'hp': 85,  'rarity': 'epic'},
    '제노 헬멧':     {'slot': 'helmet', 'def': 30, 'hp': 130, 'rarity': 'hero'},
    # 공용 갑옷
    '전술 조끼':     {'slot': 'armor', 'def': 8,  'hp': 35,  'rarity': 'normal'},
    '메카 흉갑':     {'slot': 'armor', 'def': 15, 'hp': 70,  'rarity': 'rare'},
    '나노 슈트':     {'slot': 'armor', 'def': 26, 'hp': 115, 'rarity': 'epic'},
    '퀀텀 아머':     {'slot': 'armor', 'def': 42, 'hp': 175, 'rarity': 'hero'},
    # 공용 장갑
    '강화 장갑':     {'slot': 'gloves', 'atk': 5,  'def': 3,  'rarity': 'normal'},
    '사이버 글러브': {'slot': 'gloves', 'atk': 10, 'def': 7,  'rarity': 'rare'},
    '나노 글러브':   {'slot': 'gloves', 'atk': 18, 'def': 12, 'rarity': 'epic'},
    # 공용 신발
    '전술 부츠':     {'slot': 'boots', 'spd': 12, 'def': 3,  'rarity': 'normal'},
    '제트 부츠':     {'slot': 'boots', 'spd': 22, 'def': 6,  'rarity': 'rare'},
    '퀀텀 부츠':     {'slot': 'boots', 'spd': 38, 'def': 10, 'rarity': 'epic'},
}

# ─── 클래스 (스킬 4개) ────────────────────────────────────
CLASSES = {
    '건슬링거': {
        'color': '#FF6D00',
        'hp': 150, 'mp': 120,
        'atk': 32, 'def': 10, 'spd': 100,
        'skills': [
            {'name': '연사',       'damage': 28, 'mp_cost': 12, 'range': 200, 'cooldown': 2, 'color': '#FFAB40', 'key': 'Q'},
            {'name': '스나이퍼샷', 'damage': 95, 'mp_cost': 30, 'range': 350, 'cooldown': 7, 'color': '#FF6D00', 'key': 'W'},
            {'name': '섬광탄',     'damage': 20, 'mp_cost': 18, 'range': 140, 'cooldown': 5, 'color': '#FFFF00', 'key': 'E'},
            {'name': '도탄사격',   'damage': 35, 'mp_cost': 22, 'range': 180, 'cooldown': 4, 'color': '#FF8F00', 'key': 'R'},
        ],
        'description': '빠른 연사와 강력한 저격이 특기인 원거리 딜러'
    },
    '메카닉': {
        'color': '#00BFA5',
        'hp': 210, 'mp': 90,
        'atk': 22, 'def': 22, 'spd': 75,
        'skills': [
            {'name': '드론 폭탄', 'damage': 55, 'mp_cost': 20, 'range': 150, 'cooldown': 4, 'color': '#64FFDA', 'key': 'Q'},
            {'name': '방어막',    'damage': -80,'mp_cost': 35, 'range': 100, 'cooldown': 9, 'color': '#00BFA5', 'key': 'W'},
            {'name': '터렛 설치', 'damage': 40, 'mp_cost': 28, 'range': 130, 'cooldown': 6, 'color': '#1DE9B6', 'key': 'E'},
            {'name': '오버히트',  'damage': 70, 'mp_cost': 40, 'range': 90,  'cooldown': 8, 'color': '#00E5FF', 'key': 'R'},
        ],
        'description': '드론과 기계를 다루는 방어 특화 탱커'
    },
    '해커': {
        'color': '#00E5FF',
        'hp': 115, 'mp': 260,
        'atk': 20, 'def': 7, 'spd': 88,
        'skills': [
            {'name': '해킹 펄스',   'damage': 60, 'mp_cost': 22, 'range': 210, 'cooldown': 3, 'color': '#00E5FF', 'key': 'Q'},
            {'name': 'EMP 폭발',    'damage': 85, 'mp_cost': 48, 'range': 170, 'cooldown': 8, 'color': '#18FFFF', 'key': 'W'},
            {'name': '바이러스 주입','damage': 45,'mp_cost': 30, 'range': 160, 'cooldown': 5, 'color': '#76FF03', 'key': 'E'},
            {'name': '시스템 잠금', 'damage': 30, 'mp_cost': 35, 'range': 200, 'cooldown': 7, 'color': '#EA80FC', 'key': 'R'},
        ],
        'description': '사이버 공격과 EMP로 적 시스템을 마비시키는 마법사'
    },
    '닌자': {
        'color': '#E040FB',
        'hp': 135, 'mp': 185,
        'atk': 38, 'def': 11, 'spd': 130,
        'skills': [
            {'name': '표창 연격',  'damage': 45, 'mp_cost': 15, 'range': 195, 'cooldown': 2, 'color': '#EA80FC', 'key': 'Q'},
            {'name': '그림자 폭발','damage': 75, 'mp_cost': 38, 'range': 135, 'cooldown': 7, 'color': '#7C4DFF', 'key': 'W'},
            {'name': '순간이동',   'damage': 50, 'mp_cost': 25, 'range': 200, 'cooldown': 5, 'color': '#CE93D8', 'key': 'E'},
            {'name': '연막탄',     'damage': 35, 'mp_cost': 20, 'range': 120, 'cooldown': 4, 'color': '#B39DDB', 'key': 'R'},
        ],
        'description': '초고속 이동과 표창 연격의 암살 특화 클래스'
    },
    '헤비건너': {
        'color': '#F9A825',
        'hp': 170, 'mp': 155,
        'atk': 28, 'def': 17, 'spd': 82,
        'skills': [
            {'name': '미사일 발사', 'damage': 58, 'mp_cost': 22, 'range': 95,  'cooldown': 4, 'color': '#FFD740', 'key': 'Q'},
            {'name': '위성 포격',   'damage': 42, 'mp_cost': 38, 'range': 145, 'cooldown': 7, 'color': '#FFAB00', 'key': 'W'},
            {'name': '연막 폭격',   'damage': 55, 'mp_cost': 32, 'range': 160, 'cooldown': 6, 'color': '#FF6F00', 'key': 'E'},
            {'name': '쉴드 파괴',   'damage': 80, 'mp_cost': 45, 'range': 100, 'cooldown': 9, 'color': '#FF3D00', 'key': 'R'},
        ],
        'description': '중화기로 넓은 범위를 폭격하는 전략가'
    },
}

# ─── 게임 상태 ────────────────────────────────────────────
players = {}
monsters = {}
projectiles = {}
items = {}
chat_log = []
monster_id_counter = 0
projectile_id_counter = 0
item_id_counter = 0

# ─── 유틸리티 ─────────────────────────────────────────────
def dist(a, b):
    return math.sqrt((a['x'] - b['x'])**2 + (a['y'] - b['y'])**2)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def level_exp(level):
    return int(100 * (level ** 1.5))

def recalc_stats(player):
    cls = CLASSES[player['class']]
    lv = player['level'] - 1
    base_atk = cls['atk'] + lv * 3
    base_def = cls['def'] + lv * 2
    base_spd = cls['spd']
    bonus_hp = 0
    bonus_mp = 0
    for item in player['equipment'].values():
        if item:
            s = item.get('stats', {})
            base_atk += s.get('atk', 0)
            base_def += s.get('def', 0)
            base_spd += s.get('spd', 0)
            bonus_hp += s.get('hp', 0)
            bonus_mp += s.get('mp', 0)
    player['atk'] = base_atk
    player['def'] = base_def
    player['spd'] = min(base_spd, 220)
    player['max_hp'] = cls['hp'] + lv * 20 + bonus_hp
    player['max_mp'] = cls['mp'] + lv * 10 + bonus_mp

def level_up(player):
    while player['exp'] >= player['exp_max']:
        player['exp'] -= player['exp_max']
        player['level'] += 1
        player['exp_max'] = level_exp(player['level'])
        old_hp = player['max_hp']
        recalc_stats(player)
        player['hp'] = min(player['hp'] + (player['max_hp'] - old_hp), player['max_hp'])
        player['mp'] = player['max_mp']
        socketio.emit('level_up', {'level': player['level']}, room=player['sid'])

def try_drop_item(mob, x, y):
    global item_id_counter
    if random.random() > mob.get('drop_rate', 0.25):
        return
    # 무기는 클래스 랜덤, 방어구는 공용
    weapon_keys = [k for k, v in EQUIPMENT_ITEMS.items() if v['slot'] == 'weapon']
    armor_keys  = [k for k, v in EQUIPMENT_ITEMS.items() if v['slot'] != 'weapon']
    pool = armor_keys + (weapon_keys if random.random() < 0.4 else [])
    if not pool:
        return
    item_name = random.choice(pool)
    item_data = EQUIPMENT_ITEMS[item_name]
    slot = item_data['slot']
    rarity = item_data.get('rarity', 'normal')
    stats = {k: v for k, v in item_data.items() if k not in ('slot', 'for_class', 'rarity')}
    item_id_counter += 1
    iid = f'item_{item_id_counter}'
    items[iid] = {
        'id': iid,
        'name': item_name,
        'x': x + random.randint(-25, 25),
        'y': y + random.randint(-25, 25),
        'slot': slot,
        'for_class': item_data.get('for_class'),
        'rarity': rarity,
        'rarity_color': RARITY_COLORS.get(rarity, '#aaa'),
        'stats': stats,
    }

def spawn_monsters():
    global monster_id_counter
    spawn_zones = [
        (400, 400), (800, 300), (1200, 600), (1600, 400),
        (2000, 700), (2400, 300), (2800, 500),
        (400, 1000), (900, 1200), (1400, 900),
        (1800, 1300), (2200, 1000), (2700, 1100),
        (600, 1800), (1100, 1600), (1600, 1900),
        (2000, 1700), (2500, 1800), (2900, 1500),
        (1500, 1200),
    ]
    monster_types = list(MONSTERS.keys())
    weights = [0.30, 0.28, 0.20, 0.17, 0.05]
    for i, (bx, by) in enumerate(spawn_zones):
        for _ in range(3 if i < 15 else 1):
            monster_id_counter += 1
            mid = f'mob_{monster_id_counter}'
            mtype = random.choices(monster_types, weights=weights)[0]
            if i == 19:
                mtype = 'neo_boss'
            tpl = MONSTERS[mtype].copy()
            monsters[mid] = {
                'id': mid, 'type': mtype, **tpl,
                'x': bx + random.randint(-80, 80),
                'y': by + random.randint(-80, 80),
                'spawn_x': bx, 'spawn_y': by,
                'target': None, 'last_attack': 0,
                'attack_speed': 1.5, 'state': 'idle',
            }

spawn_monsters()

# ─── 게임 루프 ────────────────────────────────────────────
last_tick = time.time()

def game_loop():
    global last_tick, monster_id_counter
    while True:
        socketio.sleep(TICK_RATE)
        now = time.time()
        dt = now - last_tick
        last_tick = now

        if not players:
            continue

        # 몬스터 AI
        dead_mobs = []
        for mid, mob in list(monsters.items()):
            closest, closest_d = None, 250
            for sid, p in players.items():
                if p['hp'] <= 0:
                    continue
                d = dist(mob, p)
                if d < closest_d:
                    closest_d = d
                    closest = sid
            mob['target'] = closest
            if closest:
                p = players[closest]
                mob['state'] = 'chase'
                ddx = p['x'] - mob['x']
                ddy = p['y'] - mob['y']
                dd = math.sqrt(ddx**2 + ddy**2) or 1
                if dd > mob['range']:
                    spd = mob['speed'] * dt
                    mob['x'] += (ddx / dd) * spd
                    mob['y'] += (ddy / dd) * spd
                else:
                    if now - mob['last_attack'] >= mob['attack_speed']:
                        mob['last_attack'] = now
                        dmg = max(1, mob['atk'] - p['def'] // 2)
                        p['hp'] = max(0, p['hp'] - dmg)
                        socketio.emit('player_hit', {'hp': p['hp'], 'max_hp': p['max_hp'], 'dmg': dmg}, room=p['sid'])
                        if p['hp'] <= 0:
                            socketio.emit('player_dead', {}, room=p['sid'])
            else:
                mob['state'] = 'idle'
                dx = mob['spawn_x'] - mob['x']
                dy = mob['spawn_y'] - mob['y']
                d = math.sqrt(dx**2 + dy**2) or 1
                if d > 30:
                    spd = mob['speed'] * 0.5 * dt
                    mob['x'] += (dx / d) * spd
                    mob['y'] += (dy / d) * spd

        # 투사체 업데이트
        dead_projs = []
        for pid, proj in list(projectiles.items()):
            proj['x'] += proj['vx'] * dt
            proj['y'] += proj['vy'] * dt
            proj['life'] -= dt
            hit = False
            if proj['type'] == 'player':
                for mid, mob in list(monsters.items()):
                    if dist(proj, mob) < mob['size'] + 6:
                        raw_dmg = proj['damage']
                        dmg = max(1, raw_dmg - mob['def'] // 2)
                        mob['hp'] -= dmg
                        socketio.emit('hit_effect', {'x': mob['x'], 'y': mob['y'], 'dmg': dmg, 'color': proj['color']})
                        if mob['hp'] <= 0:
                            owner_sid = proj.get('owner')
                            if owner_sid and owner_sid in players:
                                p = players[owner_sid]
                                p['exp'] += mob['exp']
                                p['gold'] += mob['gold']
                                level_up(p)
                                socketio.emit('kill_reward', {
                                    'exp': mob['exp'], 'gold': mob['gold'],
                                    'total_exp': p['exp'], 'exp_max': p['exp_max'],
                                    'total_gold': p['gold'], 'level': p['level']
                                }, room=owner_sid)
                            try_drop_item(mob, mob['x'], mob['y'])
                            dead_mobs.append(mid)
                        hit = True
                        break
            if proj['life'] <= 0 or hit or \
               proj['x'] < 0 or proj['x'] > MAP_WIDTH or \
               proj['y'] < 0 or proj['y'] > MAP_HEIGHT:
                dead_projs.append(pid)

        for pid in dead_projs:
            projectiles.pop(pid, None)
        for mid in set(dead_mobs):
            monsters.pop(mid, None)

        # 아이템 자동 픽업
        for iid in list(items.keys()):
            item = items.get(iid)
            if not item:
                continue
            for sid, p in players.items():
                if p['hp'] <= 0:
                    continue
                d = math.sqrt((p['x'] - item['x'])**2 + (p['y'] - item['y'])**2)
                if d < 32:
                    for_class = item.get('for_class')
                    if for_class and for_class != p['class']:
                        continue
                    slot = item['slot']
                    old_item = p['equipment'].get(slot)
                    p['equipment'][slot] = item
                    recalc_stats(p)
                    # HP/MP를 넘지 않도록 조정
                    p['hp'] = min(p['hp'], p['max_hp'])
                    p['mp'] = min(p['mp'], p['max_mp'])
                    socketio.emit('item_equipped', {
                        'item': {'name': item['name'], 'slot': slot, 'rarity': item['rarity'],
                                 'rarity_color': item['rarity_color'], 'stats': item['stats']},
                        'old_item': old_item['name'] if old_item else None,
                        'new_stats': {'atk': p['atk'], 'def': p['def'], 'spd': p['spd'],
                                      'max_hp': p['max_hp'], 'max_mp': p['max_mp']},
                    }, room=p['sid'])
                    items.pop(iid, None)
                    break

        # 몬스터 리스폰
        if len(monsters) < 40:
            monster_id_counter += 1
            mid = f'mob_{monster_id_counter}'
            mtype = random.choices(list(MONSTERS.keys()), weights=[0.30, 0.28, 0.20, 0.17, 0.05])[0]
            tpl = MONSTERS[mtype].copy()
            bx = random.randint(200, MAP_WIDTH - 200)
            by = random.randint(200, MAP_HEIGHT - 200)
            monsters[mid] = {
                'id': mid, 'type': mtype, **tpl,
                'x': bx, 'y': by, 'spawn_x': bx, 'spawn_y': by,
                'target': None, 'last_attack': 0, 'attack_speed': 1.5, 'state': 'idle',
            }

        # MP 자연 회복
        for p in players.values():
            if p['hp'] > 0 and p['mp'] < p['max_mp']:
                p['mp'] = min(p['max_mp'], p['mp'] + 5 * dt)

        # 전체 상태 브로드캐스트
        state = {
            'players': {sid: {
                'x': p['x'], 'y': p['y'], 'hp': p['hp'], 'max_hp': p['max_hp'],
                'mp': round(p['mp']), 'max_mp': p['max_mp'],
                'name': p['name'], 'class': p['class'], 'level': p['level'],
                'color': p['color'], 'sid': sid,
                'equipment': {slot: (item['name'] if item else None)
                              for slot, item in p['equipment'].items()},
            } for sid, p in players.items()},
            'monsters': {mid: {
                'x': round(mob['x']), 'y': round(mob['y']),
                'hp': mob['hp'], 'max_hp': mob['max_hp'],
                'type': mob['type'], 'name': mob['name'],
                'color': mob['color'], 'size': mob['size'],
            } for mid, mob in monsters.items()},
            'projectiles': {pid: {
                'x': round(proj['x']), 'y': round(proj['y']),
                'color': proj['color'], 'size': proj.get('size', 6),
            } for pid, proj in projectiles.items()},
            'items': {iid: {
                'x': round(item['x']), 'y': round(item['y']),
                'name': item['name'], 'slot': item['slot'],
                'rarity_color': item['rarity_color'],
            } for iid, item in items.items()},
        }
        socketio.emit('game_state', state)

# ─── SocketIO 이벤트 ──────────────────────────────────────
@socketio.on('join_game')
def on_join(data):
    sid = request.sid
    cls_name = data.get('class', '건슬링거')
    name = data.get('name', '용병')[:12]
    cls = CLASSES.get(cls_name, CLASSES['건슬링거'])

    players[sid] = {
        'sid': sid, 'name': name, 'class': cls_name,
        'color': cls['color'],
        'x': random.randint(300, MAP_WIDTH - 300),
        'y': random.randint(300, MAP_HEIGHT - 300),
        'hp': cls['hp'], 'max_hp': cls['hp'],
        'mp': cls['mp'], 'max_mp': cls['mp'],
        'atk': cls['atk'], 'def': cls['def'], 'spd': cls['spd'],
        'level': 1, 'exp': 0, 'exp_max': level_exp(1), 'gold': 0,
        'skills': cls['skills'],
        'skill_cooldowns': [0, 0, 0, 0],
        'last_attack': 0,
        'equipment': {'weapon': None, 'helmet': None, 'armor': None, 'gloves': None, 'boots': None},
    }

    emit('joined', {
        'sid': sid,
        'x': players[sid]['x'], 'y': players[sid]['y'],
        'class': cls_name, 'skills': cls['skills'],
        'map_width': MAP_WIDTH, 'map_height': MAP_HEIGHT,
    })

    msg = f"[시스템] {name}({cls_name})님이 접속하셨습니다!"
    chat_log.append({'text': msg, 'type': 'system'})
    socketio.emit('chat', {'text': msg, 'type': 'system'})

@socketio.on('move')
def on_move(data):
    sid = request.sid
    if sid not in players:
        return
    p = players[sid]
    if p['hp'] <= 0:
        return
    spd = p['spd'] * TICK_RATE * 5.0
    dx, dy = data.get('dx', 0), data.get('dy', 0)
    if dx or dy:
        length = math.sqrt(dx**2 + dy**2) or 1
        p['x'] = clamp(p['x'] + (dx / length) * spd, 20, MAP_WIDTH - 20)
        p['y'] = clamp(p['y'] + (dy / length) * spd, 20, MAP_HEIGHT - 20)

@socketio.on('attack')
def on_attack(data):
    global projectile_id_counter
    sid = request.sid
    if sid not in players:
        return
    p = players[sid]
    if p['hp'] <= 0:
        return
    now = time.time()
    if now - p['last_attack'] < 0.4:
        return
    p['last_attack'] = now
    tx, ty = data.get('tx', p['x']), data.get('ty', p['y'])
    dx, dy = tx - p['x'], ty - p['y']
    d = math.sqrt(dx**2 + dy**2) or 1
    projectile_id_counter += 1
    projectiles[f'p_{projectile_id_counter}'] = {
        'x': p['x'], 'y': p['y'],
        'vx': (dx / d) * 380, 'vy': (dy / d) * 380,
        'damage': p['atk'], 'owner': sid,
        'type': 'player', 'color': p['color'],
        'size': 7, 'life': 1.5,
    }

@socketio.on('skill')
def on_skill(data):
    global projectile_id_counter
    sid = request.sid
    if sid not in players:
        return
    p = players[sid]
    if p['hp'] <= 0:
        return
    skill_idx = data.get('index', 0)
    if skill_idx >= len(p['skills']):
        return
    skill = p['skills'][skill_idx]
    now = time.time()
    cd = p['skill_cooldowns'][skill_idx]
    if now - cd < skill['cooldown']:
        remaining = skill['cooldown'] - (now - cd)
        emit('skill_cooldown', {'index': skill_idx, 'remaining': round(remaining, 1)})
        return
    if p['mp'] < skill['mp_cost']:
        emit('no_mp', {})
        return
    p['mp'] -= skill['mp_cost']
    p['skill_cooldowns'][skill_idx] = now
    tx, ty = data.get('tx', p['x']), data.get('ty', p['y'])

    if skill['damage'] < 0:
        p['hp'] = min(p['max_hp'], p['hp'] + abs(skill['damage']))
        emit('healed', {'hp': p['hp'], 'max_hp': p['max_hp'], 'amount': abs(skill['damage'])})
        socketio.emit('hit_effect', {'x': p['x'], 'y': p['y'], 'dmg': -abs(skill['damage']), 'color': '#4CAF50'})
        return

    dx, dy = tx - p['x'], ty - p['y']
    d = math.sqrt(dx**2 + dy**2) or 1

    if 'EMP' in skill['name'] or '위성' in skill['name'] or '터렛' in skill['name'] or '연막 폭격' in skill['name']:
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            projectile_id_counter += 1
            projectiles[f'p_{projectile_id_counter}'] = {
                'x': p['x'], 'y': p['y'],
                'vx': math.cos(rad) * 280,
                'vy': math.sin(rad) * 280,
                'damage': skill['damage'] // 2,
                'owner': sid, 'type': 'player',
                'color': skill['color'], 'size': 12,
                'life': skill['range'] / 280,
            }
    elif '연사' in skill['name']:
        for spread in [-0.15, 0, 0.15]:
            angle = math.atan2(dy, dx) + spread
            projectile_id_counter += 1
            projectiles[f'p_{projectile_id_counter}'] = {
                'x': p['x'], 'y': p['y'],
                'vx': math.cos(angle) * 500, 'vy': math.sin(angle) * 500,
                'damage': skill['damage'],
                'owner': sid, 'type': 'player',
                'color': skill['color'], 'size': 7,
                'life': skill['range'] / 500,
            }
    else:
        spd = 520
        projectile_id_counter += 1
        projectiles[f'p_{projectile_id_counter}'] = {
            'x': p['x'], 'y': p['y'],
            'vx': (dx / d) * spd, 'vy': (dy / d) * spd,
            'damage': skill['damage'],
            'owner': sid, 'type': 'player',
            'color': skill['color'], 'size': 14,
            'life': skill['range'] / spd,
        }

@socketio.on('chat')
def on_chat(data):
    sid = request.sid
    if sid not in players:
        return
    p = players[sid]
    text = data.get('text', '')[:80]
    if not text:
        return
    msg = f"[{p['name']}] {text}"
    entry = {'text': msg, 'type': 'chat', 'class': p['class']}
    chat_log.append(entry)
    if len(chat_log) > 50:
        chat_log.pop(0)
    socketio.emit('chat', entry)

@socketio.on('respawn')
def on_respawn():
    sid = request.sid
    if sid not in players:
        return
    p = players[sid]
    p['hp'] = p['max_hp']
    p['mp'] = p['max_mp']
    p['x'] = random.randint(300, MAP_WIDTH - 300)
    p['y'] = random.randint(300, MAP_HEIGHT - 300)
    emit('respawned', {'x': p['x'], 'y': p['y'], 'hp': p['hp'], 'mp': p['mp']})

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    if sid in players:
        name = players[sid]['name']
        cls_name = players[sid]['class']
        del players[sid]
        socketio.emit('chat', {'text': f"[시스템] {name}({cls_name})님이 퇴장하셨습니다.", 'type': 'system'})

@app.route('/mmorpg')
def mmorpg():
    return send_from_directory('.', 'mmorpg.html')

@app.route('/health')
def health():
    return 'ok'

if __name__ == '__main__':
    socketio.start_background_task(game_loop)
    port = int(os.environ.get('PORT', 5001))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
