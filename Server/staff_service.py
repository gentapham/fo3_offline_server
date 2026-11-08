"""Offline staff recruitment and assignment endpoints."""
import json
import os
import random
import re
import time

hc = None
PACKS = (
    {"recruitid": 1, "icon": "broall", "recruittype": "normal", "staffpack_name": "Chiêu mộ HLV - Cơ bản",
     "staffpack_desc": "Chiêu mộ 1 nhân viên (1-2 sao). Miễn phí.", "ep": 0, "item": 0, "grades": (2, 3, 4)},
    {"recruitid": 2, "icon": "silall", "recruittype": "normal", "staffpack_name": "Chiêu mộ HLV - Nâng cao",
     "staffpack_desc": "Chiêu mộ 1 nhân viên (3-4 sao). 50.000 EP.", "ep": 50000, "item": 0, "grades": (6, 7, 8)},
    {"recruitid": 3, "icon": "goldall", "recruittype": "normal", "staffpack_name": "Chiêu mộ HLV - Tinh anh",
     "staffpack_desc": "Chiêu mộ 1 nhân viên (5 sao). 150.000 EP.", "ep": 150000, "item": 0, "grades": (10,)},
)
# Names, portrait IDs and roles are sourced from the bundled catalog.
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'gamedata', 'staff_catalog.json'), encoding='utf-8') as stream:
    CATALOG = json.load(stream)['staff']
# HLV tuy chinh (GM Tool > HLV > Tao HLV tuy chinh): file rieng, khong nam trong goi cap nhat
# (make_update PROTECTED) -> cap nhat ban va khong xoa mat. Gop vao CATALOG khi chay.
CUSTOM_FILE = os.path.join(HERE, 'gamedata', 'custom_staff.json')


def _custom_load():
    try:
        with open(CUSTOM_FILE, encoding='utf-8') as stream:
            data = json.load(stream)
        staff = {str(k): v for k, v in (data.get('staff') or {}).items() if isinstance(v, dict)}
        return {'staff': staff, 'deleted': [int(x) for x in data.get('deleted') or []]}
    except (OSError, ValueError, AttributeError, TypeError):
        return {'staff': {}, 'deleted': []}


CUSTOM = _custom_load()
CATALOG.update(CUSTOM['staff'])
TYPE_LABELS = (('FRONT', 'HLV kỹ thuật'), ('PREMIUM', 'HLV trưởng'),
               ('MEDICAL', 'Y tế'), ('FW', 'FW'), ('MF', 'MF'), ('DF', 'DF'), ('GK', 'GK'))


def _build_types():
    return tuple((typ, label, tuple(int(pid) for pid, info in CATALOG.items()
                                    if info['position'] == typ and not info['material'] and not info.get('hidden')), ())
                 for typ, label in TYPE_LABELS)


TYPES = _build_types()
STAT_KEYS = dict(zip(
    ('Acceleration|Aggresion|Agility|Balance|Ball Control|Cross|Dribble|Finishing|Free Kick|GK Diving|GK Goal Kick|GK Handling|GK Positioning|GK Reaction|Heading|Interception|Jump|Long Pass|Long Shot|Marking|Overall Rating|Penalty Kick|Positioning|Reaction|Shoot Curve|Shoot Power|Short Pass|Sliding Tackle|Sprint Speed|Stamina|Strength|Tackle|Vision|Volley').split('|'),
    ('acceleration|aggression|agility|balance|ballcontrol|crossing|dribbling|finishing|freekickaccuracy|gkdiving|gkkicking|gkhandling|gkpositioning|gkreflexes|headingaccuracy|interceptions|jumping|longpassing|longshots|marking|ovr|penalties|positioning|reactions|curve|shotpower|shortpassing|slidingtackle|sprintspeed|stamina|strength|standingtackle|vision|volleys').split('|')))

def used_effects(staff, idx):
    """Chi so cua cac dong khac (khac effectidx idx) cua 1 HLV: 3 dong khong duoc trung nhau."""
    return {e.get('effect') for e in staff.get('effect') or [] if e.get('effectidx') != idx}


def recruit_effects(info, grade):
    # Option groups are sourced; numeric values remain offline balancing values.
    effects = []
    for index, group in enumerate(info['boost_groups'][:min(3, grade)], 1):
        choices = [STAT_KEYS[label] for label in group if label in STAT_KEYS]
        taken = {e['effect'] for e in effects}
        choices = [c for c in choices if c not in taken] or choices  # 3 dong khong trung (nhom chi 1 chi so trung thi danh chiu)
        if choices:
            # HLV mod (power): muc cong rieng theo dang cap HLV (+15..+95), tang dan theo so sao
            power = info.get('power')
            amount = max(1, round(power * grade / MAX_GRADE)) if power else max(1, (grade + 1) // 2)
            effects.append({'effectidx': index, 'effect': random.choice(choices),
                            'amount': amount, 'grade': 1, 'open': True})
    return effects

SLOT_TYPES = {i + 1: row[0] for i, row in enumerate(TYPES)}
TYPE_BY_PORTRAIT = {pid: row[0] for row in TYPES for pid in row[2]}
ALL_PACKS = list(PACKS)
for i, (typ, label, portraits, effects) in enumerate(TYPES, 1):
    for tier, base in enumerate(PACKS, 1):
        icon = ('bro' + typ.lower()) if tier == 1 and typ in ('FW', 'MF', 'DF', 'GK') else base['icon']
        ALL_PACKS.append({**base, 'recruitid': 100 + i * 10 + tier, 'position': typ,
                          'icon': icon, 'staffpack_name': label + ' - ' + ('Cơ bản', 'Nâng cao', 'Tinh anh')[tier - 1]})
PACKS = tuple(ALL_PACKS)
for pack in PACKS:
    pack['icon'] = 'staffpack/' + pack['icon']


MAX_STAFF = 200


def normalize_team(team):
    """Migrate legacy ALL staff and relocate appointments without losing owned cards."""
    staffs = team.setdefault('staffs', [])
    team['maxstaff'] = max(int(team.get('maxstaff') or 0), MAX_STAFF)  # suc chua HLV (tuyen toi da 50 lan/luot)
    for staff in staffs:
        info = CATALOG.get(str(staff.get('stfid')))
        if info is not None:
            staff['commonname'] = info['name']
            staff['position'] = info['position']
            staff['material'] = info['material']
            # tran moi (HLV custom +5..+10): ha cac chi so cu dang vuot tran
            cap = amount_cap(staff)
            for eff in staff.get('effect') or []:
                if isinstance(eff.get('amount'), (int, float)) and eff['amount'] > cap:
                    eff['amount'] = cap
        elif staff.get('position') not in SLOT_TYPES.values():
            staff['position'] = 'FRONT'
        if str(staff.get('stfid')) in ('9502', '9505'):
            staff['commonname'] = 'HLV #' + str(staff['stfid']) + ' (chưa xác minh)'
    by_id = {p['_id']: p for p in staffs}
    old = team.get('staffroles') or []
    roles = [{'roleidx': 1, 'slotidx': i, 'open': True, 'staffid': None} for i in SLOT_TYPES]
    for previous in old:
        staff = by_id.get(previous.get('staffid'))
        if staff is None:
            continue
        target = next(r for r in roles if SLOT_TYPES[r['slotidx']] == staff['position'])
        if target['staffid'] is None:
            target['staffid'] = staff['_id']
    active = {r['staffid'] for r in roles if r['staffid']}
    for staff in staffs:
        if staff.get('status') != 'studying':
            staff['status'] = 'appointed' if staff['_id'] in active else 'idle'
        if not isinstance(staff.get('study'), dict):
            staff['study'] = {'history': {}}
    team['staffroles'] = roles
    team['staffroleidx'] = 1
    team['maxstaff'] = max(30, team.get('maxstaff') or 0)


def team_state(st):
    slot = hc._default_slot(st)
    team = st["teams"][str(slot)]
    normalize_team(team)
    return slot, team


def client_staff(staff):
    """Ban gui client: study.boost/prevboost.effectidx dem tu 0 (TeamManagementStaffStudyTabPage doc
    staff.effect[boost.effectidx] - mang effect chi so 0..2); server luu 1..3 -> dong 3 loi #1010 khi "Nhan lai"."""
    st = staff.get('study')
    if not isinstance(st, dict):
        return staff
    out = dict(staff, study=dict(st))
    for k in ('boost', 'prevboost'):
        b = st.get(k)
        if isinstance(b, dict) and isinstance(b.get('effectidx'), int):
            out['study'][k] = dict(b, effectidx=max(0, b['effectidx'] - 1))
    return out


def reply(st, slot, **extra):
    return hc._json({"result": True, **extra, "resources": hc.coach_me_resources(st, slot)})


def fail(reason):
    return hc._json({"result": False, "reason": reason})


MAX_GRADE = 10  # game ve sao = grade / 2 (10 = 5 sao, so le = nua sao)


def make_staff(stfid, grade):
    """The bang HLV moi (dung cho tuyen trong game va GM Tool)."""
    info = CATALOG[str(stfid)]
    grade = max(1, min(MAX_GRADE, int(grade)))
    return {"_id": hc.new_iid(), "stfid": int(stfid), "commonname": info['name'],
            "position": info['position'], "grade": grade, "exp": 0, "gradeupto": 1000 * grade,
            "life": 100, "status": "idle", "effect": recruit_effects(info, grade), "study": None,
            "material": bool(info.get('material')), "contractfee": 0, "new": True}


def set_grade(staff, grade):
    """Doi so sao: mo them nhom chi so (toi da 3) va tang muc cong, giu chi so da chon."""
    info = CATALOG.get(str(staff.get('stfid')))
    grade = max(1, min(MAX_GRADE, int(grade)))
    staff['grade'] = grade
    staff['gradeupto'] = 1000 * grade
    if info is None:
        return
    have = {e.get('effectidx'): e for e in staff.get('effect') or []}
    fresh = recruit_effects(info, grade)
    out = []
    for e in fresh:
        old = have.get(e['effectidx'])
        if old:
            old['amount'] = max(old.get('amount', 1), e['amount'])
            out.append(old)
        else:
            out.append(e)
    staff['effect'] = out


def enhance(staffid, query, body):
    """PUT /staff/enhance/<id> {materials: [_id...]}: dung HLV khac lam nguyen lieu de tang kinh nghiem / sao."""
    req = json.loads(body or b"{}")
    mats = [m for m in req.get("materials") or [] if isinstance(m, str)]
    st = hc._state()
    slot, team = team_state(st)
    staff = next((x for x in team["staffs"] if x["_id"] == staffid), None)
    if staff is None:
        return fail("INVALID_STAFF")
    used = [x for x in team["staffs"] if x["_id"] in mats and x["_id"] != staffid and x.get("status") != "appointed"]
    if not used:
        return fail("INVALID_STAFF_MATERIAL")
    before = staff.get("grade", 1)
    exp = staff.get("exp", 0) + sum(500 * max(1, x.get("grade", 1)) for x in used)
    grade = before
    while grade < MAX_GRADE and exp >= 1000 * grade:
        exp -= 1000 * grade
        grade += 1
    if grade >= MAX_GRADE:
        exp = 0
    set_grade(staff, grade)
    staff["exp"] = exp
    ids = {x["_id"] for x in used}
    team["staffs"] = [x for x in team["staffs"] if x["_id"] not in ids]
    hc._save(st)
    return reply(st, slot, staff=staff, success=True, gradeup=grade > before)


def recruit_list(query, body):
    return hc._json([{k: v for k, v in p.items() if k != "grades"} for p in PACKS])


def recruit(recruitid, query, body):
    pack = next((p for p in PACKS if p["recruitid"] == int(recruitid)), None)
    if pack is None:
        return fail("INVALID_STAFF_PACK")
    req = json.loads(body or b"{}")
    if req.get("feetype", "ep") != "ep" or req.get("eventtype"):
        return fail("INVALID_STAFF_PAYMENT")
    st = hc._state()
    slot, team = team_state(st)
    if len(team["staffs"]) >= team["maxstaff"]:
        return fail("STAFF_LIMIT_EXCEEDED")
    coach = hc.my_coach(st)
    if coach.get("ep", hc.START_EP) < pack["ep"]:
        return fail("STAFF_EP_NOTENOUGH")
    category = next((r for r in TYPES if r[0] == pack.get('position')), None) or random.choice(TYPES)
    typ, label, pool, effects_pool = category
    portraits = [p for p in pool if os.path.isfile(os.path.join(hc.ASSETS, "externalAssets", "staffs", str(p) + ".png"))]
    if not portraits:
        return fail("STAFF_PORTRAITS_MISSING")
    staff = make_staff(random.choice(portraits), random.choice(pack["grades"]))
    team["staffs"].append(staff)
    coach["ep"] = coach.get("ep", hc.START_EP) - pack["ep"]
    hc._save(st)
    return reply(st, slot, staff=staff)


def appoint(staffid, query, body):
    req = json.loads(body or b"{}")
    idx, role = req.get("slot"), req.get("staffrole", 1)
    if type(idx) is not int or not 1 <= idx <= 7 or type(role) is not int or role != 1:
        return fail("INVALID_STAFF_SLOT")
    st = hc._state()
    slot, team = team_state(st)
    staff = next((p for p in team["staffs"] if p["_id"] == staffid), None)
    if staff is None or staff.get("life", 0) <= 0 or staff.get("status") == "studying":
        return fail("INVALID_STAFF")
    target = next((r for r in team["staffroles"] if r["roleidx"] == role and r["slotidx"] == idx), None)
    if target is None or not target.get("open"):
        return fail("INVALID_STAFF_SLOT")
    if staff['position'] != SLOT_TYPES[idx]:
        return fail('STAFF_POSITION_MISMATCH')
    previous_id = target.get('staffid')
    for r in team["staffroles"]:
        if r.get("staffid") == staffid:
            r["staffid"] = None
    target["staffid"] = staffid
    active = {r.get("staffid") for r in team["staffroles"] if r.get("open")}
    for p in team["staffs"]:
        p["status"] = "appointed" if p["_id"] in active else "idle"
    hc._save(st)
    previous = next((p for p in team['staffs'] if p['_id'] == previous_id), None)
    return reply(st, slot, appointed={'appointedstaff': staff, 'prevstaff': previous})


def unappoint(staffid, query, body):
    st = hc._state()
    slot, team = team_state(st)
    staff = next((p for p in team["staffs"] if p["_id"] == staffid), None)
    if staff is None:
        return fail("INVALID_STAFF")
    for r in team["staffroles"]:
        if r.get("staffid") == staffid:
            r["staffid"] = None
    staff["status"] = "idle"
    hc._save(st)
    return reply(st, slot)


# Offline Study rules: each location targets one existing boost group.
STUDY = {str(i): {'location': 'LOCATION_NAME_%02d' % i, 'grade': 1,
                 'position': [t[0] for t in TYPE_LABELS], 'matchcount': 1,
                 'fee': 1000 if i <= 3 else 2000,
                 'effectstring': 'Đổi một chỉ số thưởng trong cùng nhóm (nhóm chỉ có 1 chỉ số: đổi sang chỉ số khác).' if i <= 3 else 'Tăng một chỉ số thưởng thêm +1 (tối đa +5 so với mức gốc của HLV).',
                 'effectidx': (i - 1) % 3 + 1,
                 'boosttype': 'change' if i <= 3 else 'enhance'} for i in range(1, 7)}
STUDY_MAX_FEE = 100000

STUDY_EXTRA = 5  # tap huan cong them toi da +5 so voi muc goc (HLV 5 sao da o muc goc ngay khi tuyen)


def amount_cap(staff):
    """Tran muc cong khi tap huan: muc goc (+5 HLV goc / suc manh HLV mod, tuy chinh) + STUDY_EXTRA."""
    info = CATALOG.get(str(staff.get('stfid'))) or {}
    # gm_cap: muc GM Tool tu dat cho HLV nay (Chinh tung chi so) - khong bi normalize ha xuong
    return max(max(5, int(info.get('power') or 0)) + STUDY_EXTRA, int(staff.get('gm_cap') or 0))


def study_boost(staff, rule):
    idx = rule['effectidx']
    current = next((e for e in staff.get('effect', []) if e.get('effectidx') == idx and e.get('open')), None)
    if current is None:
        return None
    if rule['boosttype'] == 'enhance':
        if current.get('amount', 0) >= amount_cap(staff):
            return None
        return {'effectidx': idx, 'boosttype': 'enhance', 'effect': current['effect'], 'inc': 1}
    info = CATALOG.get(str(staff.get('stfid')))
    if info is None:
        return None
    taken = used_effects(staff, idx)  # khong doi sang chi so dong khac dang co
    choices = [STAT_KEYS[label] for label in info['boost_groups'][idx - 1]
               if label in STAT_KEYS and STAT_KEYS[label] != current['effect'] and STAT_KEYS[label] not in taken]
    if not choices and current['effect'] != 'ovr':
        # nhom chi co 1 chi so (vd Van Gaal: Toc do) -> doi sang chi so cung loai (thu mon / cau thu thuong)
        gk = current['effect'].startswith('gk')
        choices = [STAT_KEYS[k] for k in STAT_KEYS if k != 'Overall Rating' and k.startswith('GK ') == gk
                   and STAT_KEYS[k] != current['effect'] and STAT_KEYS[k] not in taken]
    if not choices:
        return None
    return {'effectidx': idx, 'boosttype': 'change', 'effect': random.choice(choices), 'inc': 0}

def study_start(lid, query, body):
    rule = STUDY.get(lid)
    if rule is None:
        return fail('ITEM_USE_ERROR')
    req = json.loads(body or b'{}')
    ids = req.get('staffs')
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str):
        return fail('INVALID_STAFF')
    st = hc._state()
    slot, team = team_state(st)
    if any(p.get('status') == 'studying' and (p.get('study') or {}).get('lid') == lid for p in team['staffs']):
        return fail('Đã có HLV đang tập huấn ở nơi này. Chờ xong hoặc chọn nơi khác.')
    staff = next((p for p in team['staffs'] if p['_id'] == ids[0]), None)
    if staff is None or staff.get('status') != 'idle' or staff.get('material') or staff.get('life', 0) <= 0:
        return fail('INVALID_STAFF')
    if staff.get('grade', 0) < rule['grade'] or staff.get('position') not in rule['position']:
        return fail('INVALID_STAFF')
    boost = study_boost(staff, rule)
    if boost is None:  # bao ro ly do (truoc: ITEM_USE_ERROR "Loi dung do" kho hieu)
        idx = rule['effectidx']
        cur = next((e for e in staff.get('effect', []) if e.get('effectidx') == idx and e.get('open')), None)
        if cur is None:
            return fail(f'HLV này chưa mở chỉ số thưởng thứ {idx} nên không tập huấn ở đây được.')
        if rule['boosttype'] == 'enhance':
            return fail(f'Chỉ số thưởng thứ {idx} đã đạt tối đa +{amount_cap(staff)}. Hãy chọn nơi tập huấn khác.')
        return fail(f'Chỉ số thưởng thứ {idx} (Tất cả chỉ số) không đổi được. Hãy chọn nơi tập huấn khác.')
    history = (staff.get('study') or {}).get('history') or {}
    count = len(history.get(lid, []))
    fee = min(STUDY_MAX_FEE, rule['fee'] * 2 ** min(count, 16))
    coach = hc.my_coach(st)
    if coach.get('ep', hc.START_EP) < fee:
        return fail('STAFF_EP_NOTENOUGH')
    coach['ep'] = coach.get('ep', hc.START_EP) - fee
    staff['status'] = 'studying'
    staff['study'] = {'lid': lid, 'mcdate': int(team.get('staffsmc') or 0),
                      'history': history, 'boost': boost}
    hc._save(st)
    return reply(st, slot, staffs={p['_id']: client_staff(p) for p in team['staffs']})

def study_complete(lid, query, body):
    rule = STUDY.get(lid)
    if rule is None:
        return fail('ITEM_USE_ERROR')
    st = hc._state()
    slot, team = team_state(st)
    staff = next((p for p in team['staffs'] if p.get('status') == 'studying'
                  and (p.get('study') or {}).get('lid') == lid), None)
    if staff is None:
        return fail('INVALID_STAFF')
    study = staff['study']
    if int(team.get('staffsmc') or 0) - study['mcdate'] < rule['matchcount']:
        return fail('ITEM_USE_ERROR')
    boost = study['boost']
    effect = next((e for e in staff.get('effect', []) if e.get('effectidx') == boost['effectidx']), None)
    if effect is None:
        return fail('INVALID_STAFF')
    if boost['boosttype'] == 'change':
        effect['effect'] = boost['effect']
    else:
        # khong bao gio ha muc cong (HLV mod +15..+95); tang toi tran cua HLV
        effect['amount'] = max(effect['amount'], min(amount_cap(staff), effect['amount'] + boost['inc']))
    study['history'].setdefault(lid, []).append(dict(boost))
    staff['status'] = 'idle'
    # Keep the result for the client's return popup, while freeing the location.
    study['lid'] = None
    hc._save(st)
    # TeamManagementStaffStudyTabPage dung staffs cua phan hoi de dung lai danh sach tung khu vuc: chi gui HLV vua
    # nhan -> khu vuc khac mat studyInfo.staffs -> "Nhan lai" HLV thu 2 loi #1010. Gui du moi HLV.
    return reply(st, slot, staffs={p['_id']: client_staff(p) for p in team['staffs']})


# --------------------------------------------------------------------------- #
# HLV tuy chinh: ten + vi tri + anh rieng (GM Tool). Anh game doc tu externalAssets/staffs/<stfid>.png
# (128x128); ten/vi tri tu staff object (commonname/position) va bang /static/db/STAFF stafflist.
# Ma moi: <so dau theo vi tri>7NN (FW 17NN, MF 27NN, DF 37NN, GK 47NN, Y te 57NN, HLV truong /
# ky thuat 97NN) -> khong dung ma goc nao (cao nhat 9663) va make_update bo qua staffs/?7??.png.
# --------------------------------------------------------------------------- #
IMG_SIZE = 128
IMG_MAX_BYTES = 2 * 1024 * 1024
POS_PREFIX = {'FW': 1, 'MF': 2, 'DF': 3, 'GK': 4, 'MEDICAL': 5, 'FRONT': 9, 'PREMIUM': 9}
STAT_POOL = [k for k in STAT_KEYS if not k.startswith('GK ') and k != 'Overall Rating']


def image_info(data):
    """(kind 'png'|'jpg', width, height) tu header file anh; ValueError (tieng Viet) neu khong hop le."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError('Chưa chọn ảnh.')
    if len(data) > IMG_MAX_BYTES:
        raise ValueError(f'Ảnh quá lớn ({len(data) / 1048576:.1f} MB). Tối đa 2 MB.')
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        if len(data) < 33 or data[12:16] != b'IHDR':
            raise ValueError('File PNG bị hỏng (không đọc được kích thước).')
        w, h = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
        return 'png', w, h
    if data[:3] == b'\xff\xd8\xff':
        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker == 0xFF:  # byte dem
                i += 1
                continue
            if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker in (0xD9, 0xDA):  # het anh / bat dau du lieu ma truoc khi gap SOF
                break
            seg = int.from_bytes(data[i + 2:i + 4], 'big')
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 9 > len(data):
                    break
                h, w = int.from_bytes(data[i + 5:i + 7], 'big'), int.from_bytes(data[i + 7:i + 9], 'big')
                return 'jpg', w, h
            i += 2 + seg
        raise ValueError('File JPG bị hỏng (không đọc được kích thước).')
    raise ValueError('Chỉ nhận ảnh PNG hoặc JPG.')


def check_image(data):
    kind, w, h = image_info(data)
    if (w, h) != (IMG_SIZE, IMG_SIZE):
        raise ValueError(f'Ảnh đang là {w}x{h} px. Game cần đúng {IMG_SIZE}x{IMG_SIZE} px (ảnh vuông). '
                         'Hãy cắt/thu nhỏ ảnh về 128x128 (nút "Resize ảnh online") rồi chọn lại.')
    return kind


def staff_dirs():
    """Thu muc anh HLV: kho CDN cua server (ASSETS) + ban sao trong _cache/cdn (neu co)."""
    main = os.path.join(hc.ASSETS, 'externalAssets', 'staffs')
    mirror = os.path.normpath(os.path.join(hc.ASSETS, '..', '..', 'cdn', 'externalAssets', 'staffs'))
    out = [main]
    if mirror != os.path.normpath(main) and os.path.isdir(os.path.dirname(mirror)):
        out.append(mirror)
    return out


def _static_stafflist():
    try:
        with open(os.path.join(hc.RESPONSES, 'static_db_STAFF.json'), encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


# Ma HLV tuy chinh: <tien to vi tri><tram><2 so>. Giu nguyen hang nghin (= tien to vi tri nhu ma goc), chi
# dung cac hang tram con trong (ma goc dung x0xx, x1xx, 49xx, 95xx, 96xx) -> 600..800 ma / tien to
# (truoc chi x7xx = toi da 99). FRONT va PREMIUM chung tien to 9.
CUSTOM_HUNDREDS = (7, 8, 2, 3, 4, 5, 6, 9)
CUSTOM_MAX_POWER = 50


def new_custom_id(position):
    prefix = POS_PREFIX[position]
    listed = set((_static_stafflist() or {}).get('stafflist') or {})
    used_h = {(int(k) % 1000) // 100 for k in listed if str(k).isdigit() and int(k) // 1000 == prefix
              and not (CATALOG.get(str(k)) or {}).get('custom')}
    for h in CUSTOM_HUNDREDS:
        if h in used_h:
            continue
        for n in range(1, 100):
            sid = prefix * 1000 + h * 100 + n
            if (str(sid) in CATALOG or str(sid) in listed or sid in CUSTOM['deleted']
                    or any(os.path.exists(os.path.join(d, f'{sid}.png')) for d in staff_dirs())):
                continue
            return sid
    raise ValueError('Đã hết mã cho HLV tùy chỉnh ở vị trí này.')


def custom_groups(groups):
    """Chi so tu chon tu GM Tool: 3 nhom, moi nhom 1..5 chi so (nhan tieng Anh nhu STAT_KEYS)."""
    if not isinstance(groups, list) or len(groups) != 3:
        raise ValueError('Cần chọn đủ 3 chỉ số hỗ trợ.')
    out = []
    for g in groups:
        g = [g] if isinstance(g, str) else g
        if not isinstance(g, list) or not 1 <= len(g) <= 5 or any(x not in STAT_KEYS for x in g):
            raise ValueError('Chỉ số hỗ trợ không hợp lệ.')
        out.append(list(dict.fromkeys(g)))
    singles = [g[0] for g in out if len(g) == 1]
    if len(singles) != len(set(singles)):
        raise ValueError('3 chỉ số hỗ trợ phải khác nhau.')
    return out


def random_boost_groups(position):
    """Nhom chi so ngau nhien: HLV truong theo mau HLV noi tieng (1 chi so / 5 chi so / Tat ca chi so);
    vi tri khac lay nhom chi so cua mot HLV goc cung vi tri (GK ra chi so thu mon, Y te ra the luc...)."""
    if position == 'PREMIUM':
        first = random.choice(STAT_POOL)
        return [[first], random.sample([k for k in STAT_POOL if k != first], 5), ['Overall Rating']]
    pool = [info['boost_groups'] for info in CATALOG.values()
            if info.get('position') == position and not info.get('material') and not info.get('hidden')
            and not info.get('custom') and any(info.get('boost_groups') or [])]
    if not pool:
        first = random.choice(STAT_POOL)
        rest = [k for k in STAT_POOL if k != first]
        return [[first], random.sample(rest, 5), random.sample(rest, 5)]
    return [list(g) for g in random.choice(pool)]


def _custom_save():
    tmp = CUSTOM_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(CUSTOM, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, CUSTOM_FILE)


def _rebuild():
    global TYPES, TYPE_BY_PORTRAIT
    TYPES = _build_types()
    TYPE_BY_PORTRAIT = {pid: row[0] for row in TYPES for pid in row[2]}


def custom_create(name, position, data, groups=None, power=None):
    """Tao loai HLV moi: kiem tra anh truoc, roi ghi anh (ca 2 thu muc) + custom_staff.json."""
    name = ' '.join(str(name or '').split())
    if not 2 <= len(name) <= 30:
        raise ValueError('Tên HLV phải dài 2–30 ký tự.')
    if any(c in name for c in '<>"\\{}'):
        raise ValueError('Tên HLV không được chứa ký tự < > " \\ { }.')
    if any(str(info.get('name', '')).lower() == name.lower() for info in CATALOG.values()):
        raise ValueError('Đã có HLV trùng tên này. Hãy đặt tên khác.')
    if position not in POS_PREFIX:
        raise ValueError('Vị trí HLV không hợp lệ.')
    if power is not None:
        if type(power) is not int or not 1 <= power <= CUSTOM_MAX_POWER:
            raise ValueError(f'Mức cộng tối đa phải là số nguyên từ 1 đến {CUSTOM_MAX_POWER}.')
    groups = custom_groups(groups) if groups is not None else None
    check_image(data)
    sid = new_custom_id(position)
    info = {'name': name, 'position': position, 'boost_groups': groups or random_boost_groups(position),
            'power': power if power is not None else random.randint(5, 10), 'material': False, 'verified': False, 'custom': True,
            'created': time.strftime('%Y-%m-%d %H:%M:%S')}
    written = []
    try:
        for d in staff_dirs():
            os.makedirs(d, exist_ok=True)
            f = os.path.join(d, f'{sid}.png')  # game luon xin <id>.png; JPG cung doc duoc theo noi dung
            with open(f + '.part', 'wb') as stream:
                stream.write(data)
            os.replace(f + '.part', f)
            written.append(f)
        CUSTOM['staff'][str(sid)] = info
        _custom_save()
    except Exception:
        CUSTOM['staff'].pop(str(sid), None)
        for f in written:  # chua luu duoc catalog -> go anh vua ghi (dua vao backup, khong xoa han)
            _to_backup(f, sid)
        raise
    CATALOG[str(sid)] = info
    _rebuild()
    return sid, info


def backup_dir():
    return os.path.join(HERE, 'gamedata', 'backup', 'custom_staff')


def _to_backup(path, sid):
    if not os.path.isfile(path):
        return None
    os.makedirs(backup_dir(), exist_ok=True)
    tag = 'cdn' if os.sep + 'cdn' + os.sep in path else 'assets'
    dst = os.path.join(backup_dir(), f"{sid}_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png")
    n = 1
    while os.path.exists(dst):
        dst = dst[:-4] + f'_{n}.png'
        n += 1
    os.replace(path, dst)
    return dst


def custom_delete(sid):
    """Bo loai HLV tuy chinh: anh chuyen vao gamedata/backup/custom_staff (khong xoa han)."""
    key = str(sid)
    if key not in CUSTOM['staff']:
        raise ValueError('Đây không phải HLV tùy chỉnh (HLV gốc không xoá được).')
    moved = [m for m in (_to_backup(os.path.join(d, f'{key}.png'), key) for d in staff_dirs()) if m]
    info = CUSTOM['staff'].pop(key)
    if int(key) not in CUSTOM['deleted']:
        CUSTOM['deleted'].append(int(key))  # khong cap lai ma nay (thu cu / goi cu khong nham sang HLV khac)
    CUSTOM.setdefault('removed', {})[key] = info
    _custom_save()
    CATALOG.pop(key, None)
    _rebuild()
    return moved


def static_staff():
    """/static/db/STAFF: bang goc trong responses/ + moi HLV trong CATALOG chua co (HLV tuy chinh)."""
    data = _static_stafflist()
    if data is None:
        return None
    lst = data.setdefault('stafflist', {})
    for sid, info in CATALOG.items():
        if sid not in lst:
            lst[sid] = {'stfid': int(sid), 'commonname': info['name'], 'material': bool(info.get('material')),
                        'position': info['position']}
    return hc._json(data)


def install(module):
    global hc
    hc = module
    _from_file = hc.from_file

    def from_file(path):  # bang STAFF tinh + HLV tuy chinh (tao tren GM Tool, khong can khoi dong lai)
        if path == '/static/db/STAFF':
            return static_staff() or _from_file(path)
        return _from_file(path)
    hc.from_file = from_file
    hc.ENDPOINTS["/staff/recruit"] = recruit_list
    hc.ROUTES[:0] = [
        (re.compile(r"/staff/study/(\d+)/complete"), study_complete),
        (re.compile(r"/staff/study/(\d+)"), study_start),
        (re.compile(r"/staff/recruit/(\d+)"), recruit),
        (re.compile(r"/staff/enhance/([0-9a-f]{24})"), enhance),
        (re.compile(r"/staff/appoint/([0-9a-f]{24})"), appoint),
        (re.compile(r"/staff/unappoint/([0-9a-f]{24})"), unappoint),
    ]
