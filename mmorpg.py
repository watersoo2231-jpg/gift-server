from flask import Flask, render_template, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import time
import math
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'mmorpg-secret-key-2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ─── 게임 설정 ───────────────────────────────────────────────
MAP_WIDTH = 3200
MAP_HEIGHT = 2400
TICK_RATE = 0.05  # 50ms

# 몬스터 종류
MONSTERS = {
    'slime': {'name': '슬라임', 'hp': 30, 'max_hp': 30, 'atk': 5, 'def': 2, 'exp': 10, 'gold': 5, 'speed': 40, 'range': 50, 'color': '#4CAF50', 'size': 18},
    'goblin': {'name': '고블린', 'hp': 60, 'max_hp': 60, 'atk': 12, 'def': 5, 'exp': 25, 'gold': 12, 'speed': 60, 'range': 55, 'color': '#FF9800', 'size': 20},
    'orc': {'name': '오크', 'hp': 120, 'max_hp': 120, 'atk': 22, 'def': 12, 'exp': 55, 'gold': 25, 'speed': 45, 'range': 60, 'color': '#795548', 'size': 28},
    'skeleton': {'name': '스켈레톤', 'hp': 80, 'max_hp': 80, 'atk': 18, 'def': 8, 'exp': 40, 'gold': 18, 'speed': 50, 'range': 65, 'color': '#E0E0E0', 'size': 22},
    'dragon': {'name': '드래곤', 'hp': 500, 'max_hp': 500, 'atk': 60, 'def': 30, 'exp': 300, 'gold': 150, 'speed': 55, 'range': 90, 'color': '#F44336', 'size': 45},
}

# 캐릭터 클래스 기본 스탯
CLASSES = {
    '기사': {
        'color': '#1565C0',
        'hp': 200, 'mp': 80,
        'atk': 25, 'def': 20, 'spd': 80,
        'skills': [
            {'name': '방패치기', 'damage': 35, 'mp_cost': 10, 'range': 70, 'cooldown': 3, 'color': '#42A5F5'},
            {'name': '돌격', 'damage': 50, 'mp_cost': 20, 'range': 120, 'cooldown': 6, 'color': '#1E88E5'},
        ],
        'description': '높은 방어력과 체력을 가진 전사'
    },
    '요정': {
        'color': '#F06292',
        'hp': 100, 'mp': 200,
        'atk': 15, 'def': 8, 'spd': 120,
        'skills': [
            {'name': '빛의 화살', 'damage': 30, 'mp_cost': 12, 'range': 180, 'cooldown': 2, 'color': '#FCE4EC'},
            {'name': '치유의 빛', 'damage': -60, 'mp_cost': 30, 'range': 100, 'cooldown': 8, 'color': '#F48FB1'},
        ],
        'description': '빠른 이동속도와 원거리 마법 특화'
    },
    '마법사': {
        'color': '#7B1FA2',
        'hp': 110, 'mp': 250,
        'atk': 18, 'def': 6, 'spd': 85,
        'skills': [
            {'name': '파이어볼', 'damage': 65, 'mp_cost': 25, 'range': 200, 'cooldown': 3, 'color': '#FF5722'},
            {'name': '블리자드', 'damage': 90, 'mp_cost': 45, 'range': 160, 'cooldown': 8, 'color': '#80DEEA'},
        ],
        'description': '강력한 마법 공격 특화 클래스'
    },
    '군주': {
        'color': '#F9A825',
        'hp': 160, 'mp': 160,
        'atk': 30, 'def': 15, 'spd': 90,
        'skills': [
            {'name': '왕의 분노', 'damage': 55, 'mp_cost': 20, 'range': 90, 'cooldown': 4, 'color': '#FFF176'},
            {'name': '영역 지배', 'damage': 40, 'mp_cost': 35, 'range': 140, 'cooldown': 7, 'color': '#FFD54F'},
        ],
        'description': '균형 잡힌 강력한 전투 클래스'
    },
    '다크엘프': {
        'color': '#4A148C',
        'hp': 130, 'mp': 180,
        'atk': 35, 'def': 10, 'spd': 110,
        'skills': [
            {'name': '어둠의 화살', 'damage': 50, 'mp_cost': 18, 'range': 190, 'cooldown': 2, 'color': '#CE93D8'},
            {'name': '암흑 폭발', 'damage': 80, 'mp_cost': 40, 'range': 130, 'cooldown': 7, 'color': '#6A1B9A'},
        ],
        'description': '고공격력과 빠른 속도의 어둠 마법사'
    },
}

# ─── 게임 상태 ───────────────────────────────────────────────
players = {}      # sid -> player data
monsters = {}     # mid -> monster data
projectiles = {}  # pid -> projectile data
chat_log = []
monster_id_counter = 0
projectile_id_counter = 0

def spawn_monsters():
    global monster_id_counter
    spawn_zones = [
        (400, 400), (800, 300), (1200, 600), (1600, 400),
        (2000, 700), (2400, 300), (2800, 500),
        (400, 1000), (900, 1200), (1400, 900),
        (1800, 1300), (2200, 1000), (2700, 1100),
        (600, 1800), (1100, 1600), (1600, 1900),
        (2000, 1700), (2500, 1800), (2900, 1500),
        (1500, 1200),  # 드래곤 영역
    ]
    monster_types = list(MONSTERS.keys())
    weights = [0.30, 0.28, 0.20, 0.17, 0.05]

    for i, (bx, by) in enumerate(spawn_zones):
        for _ in range(3 if i < 15 else 1):
            monster_id_counter += 1
            mid = f'mob_{monster_id_counter}'
            mtype = random.choices(monster_types, weights=weights)[0]
            if i == 19:
                mtype = 'dragon'
            tpl = MONSTERS[mtype].copy()
            monsters[mid] = {
                'id': mid,
                'type': mtype,
                **tpl,
                'x': bx + random.randint(-80, 80),
                'y': by + random.randint(-80, 80),
                'spawn_x': bx,
                'spawn_y': by,
                'target': None,
                'last_attack': 0,
                'attack_speed': 1.5,
                'state': 'idle',
            }

spawn_monsters()

# ─── 유틸리티 ────────────────────────────────────────────────
def dist(a, b):
    return math.sqrt((a['x'] - b['x'])**2 + (a['y'] - b['y'])**2)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def level_exp(level):
    return int(100 * (level ** 1.5))

def level_up(player):
    while player['exp'] >= player['exp_max']:
        player['exp'] -= player['exp_max']
        player['level'] += 1
        player['exp_max'] = level_exp(player['level'])
        cls = CLASSES[player['class']]
        player['max_hp'] = cls['hp'] + player['level'] * 20
        player['max_mp'] = cls['mp'] + player['level'] * 10
        player['hp'] = player['max_hp']
        player['mp'] = player['max_mp']
        player['atk'] = cls['atk'] + player['level'] * 3
        player['def'] = cls['def'] + player['level'] * 2
        socketio.emit('level_up', {'level': player['level']}, room=player['sid'])

# ─── 게임 루프 ───────────────────────────────────────────────
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
            # 타겟 탐색
            closest = None
            closest_d = 250  # 탐지 범위
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
                # 이동
                ddx = p['x'] - mob['x']
                ddy = p['y'] - mob['y']
                dd = math.sqrt(ddx**2 + ddy**2) or 1
                if dd > mob['range']:
                    spd = mob['speed'] * dt
                    mob['x'] += (ddx / dd) * spd
                    mob['y'] += (ddy / dd) * spd
                else:
                    # 공격
                    if now - mob['last_attack'] >= mob['attack_speed']:
                        mob['last_attack'] = now
                        dmg = max(1, mob['atk'] - p['def'] // 2)
                        p['hp'] = max(0, p['hp'] - dmg)
                        socketio.emit('player_hit', {
                            'hp': p['hp'], 'max_hp': p['max_hp'], 'dmg': dmg
                        }, room=p['sid'])
                        if p['hp'] <= 0:
                            socketio.emit('player_dead', {}, room=p['sid'])
            else:
                mob['state'] = 'idle'
                # 복귀
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
                            # 경험치/골드 보상
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
                'target': None, 'last_attack': 0,
                'attack_speed': 1.5, 'state': 'idle',
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
        }
        socketio.emit('game_state', state)

# ─── SocketIO 이벤트 ─────────────────────────────────────────
@socketio.on('join_game')
def on_join(data):
    sid = request.sid
    cls_name = data.get('class', '기사')
    name = data.get('name', '용사')[:12]
    cls = CLASSES.get(cls_name, CLASSES['기사'])

    players[sid] = {
        'sid': sid,
        'name': name,
        'class': cls_name,
        'color': cls['color'],
        'x': random.randint(300, MAP_WIDTH - 300),
        'y': random.randint(300, MAP_HEIGHT - 300),
        'hp': cls['hp'],
        'max_hp': cls['hp'],
        'mp': cls['mp'],
        'max_mp': cls['mp'],
        'atk': cls['atk'],
        'def': cls['def'],
        'spd': cls['spd'],
        'level': 1,
        'exp': 0,
        'exp_max': level_exp(1),
        'gold': 0,
        'skills': cls['skills'],
        'skill_cooldowns': [0, 0],
        'last_attack': 0,
    }

    emit('joined', {
        'sid': sid,
        'x': players[sid]['x'],
        'y': players[sid]['y'],
        'class': cls_name,
        'skills': cls['skills'],
        'map_width': MAP_WIDTH,
        'map_height': MAP_HEIGHT,
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
    spd = p['spd'] * TICK_RATE * 3.5
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
    spd = 380
    projectiles[f'p_{projectile_id_counter}'] = {
        'x': p['x'], 'y': p['y'],
        'vx': (dx / d) * spd, 'vy': (dy / d) * spd,
        'damage': p['atk'],
        'owner': sid,
        'type': 'player',
        'color': p['color'],
        'size': 7,
        'life': 1.5,
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

    # 힐 스킬 (요정)
    if skill['damage'] < 0:
        p['hp'] = min(p['max_hp'], p['hp'] + abs(skill['damage']))
        emit('healed', {'hp': p['hp'], 'max_hp': p['max_hp'], 'amount': abs(skill['damage'])})
        socketio.emit('hit_effect', {'x': p['x'], 'y': p['y'], 'dmg': -abs(skill['damage']), 'color': '#4CAF50'})
        return

    dx, dy = tx - p['x'], ty - p['y']
    d = math.sqrt(dx**2 + dy**2) or 1

    # 특수 패턴
    if '블리자드' in skill['name'] or '영역' in skill['name']:
        # 범위 스킬 - 여러 방향 발사
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
    else:
        projectile_id_counter += 1
        spd = 500
        projectiles[f'p_{projectile_id_counter}'] = {
            'x': p['x'], 'y': p['y'],
            'vx': (dx / d) * spd, 'vy': (dy / d) * spd,
            'damage': skill['damage'],
            'owner': sid, 'type': 'player',
            'color': skill['color'],
            'size': 14,
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
    cls = CLASSES[p['class']]
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
        msg = f"[시스템] {name}({cls_name})님이 퇴장하셨습니다."
        socketio.emit('chat', {'text': msg, 'type': 'system'})

@app.route('/mmorpg')
def mmorpg():
    return send_from_directory('.', 'mmorpg.html')

@app.route('/health')
def health():
    return 'ok'

# request 임포트 추가
from flask import request

if __name__ == '__main__':
    socketio.start_background_task(game_loop)
    port = int(os.environ.get('PORT', 5001))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
