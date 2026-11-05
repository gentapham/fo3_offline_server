"""Offline staff recruitment and assignment endpoints."""
import json
import os
import random
import re

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
with open(os.path.join(os.path.dirname(__file__), 'gamedata', 'staff_catalog.json'), encoding='utf-8') as stream:
    CATALOG = json.load(stream)['staff']
TYPE_LABELS = (('FRONT', 'HLV kỹ thuật'), ('PREMIUM', 'HLV trưởng'),
               ('MEDICAL', 'Y tế'), ('FW', 'FW'), ('MF', 'MF'), ('DF', 'DF'), ('GK', 'GK'))
TYPES = tuple((typ, label, tuple(int(pid) for pid, info in CATALOG.items()
                               if info['position'] == typ and not info['material']), ())
              for typ, label in TYPE_LABELS)
STAT_KEYS = dict(zip(
    ('Acceleration|Aggresion|Agility|Balance|Ball Control|Cross|Dribble|Finishing|Free Kick|GK Diving|GK Goal Kick|GK Handling|GK Positioning|GK Reaction|Heading|Interception|Jump|Long Pass|Long Shot|Marking|Overall Rating|Penalty Kick|Positioning|Reaction|Shoot Curve|Shoot Power|Short Pass|Sliding Tackle|Sprint Speed|Stamina|Strength|Tackle|Vision|Volley').split('|'),
    ('acceleration|aggression|agility|balance|ballcontrol|crossing|dribbling|finishing|freekickaccuracy|gkdiving|gkkicking|gkhandling|gkpositioning|gkreflexes|headingaccuracy|interceptions|jumping|longpassing|longshots|marking|ovr|penalties|positioning|reactions|curve|shotpower|shortpassing|slidingtackle|sprintspeed|stamina|strength|standingtackle|vision|volleys').split('|')))

def recruit_effects(info, grade):
    # Option groups are sourced; numeric values remain offline balancing values.
    effects = []
    for index, group in enumerate(info['boost_groups'][:min(3, grade)], 1):
        choices = [STAT_KEYS[label] for label in group if label in STAT_KEYS]
        if choices:
            effects.append({'effectidx': index, 'effect': random.choice(choices),
                            'amount': max(1, (grade + 1) // 2), 'grade': 1, 'open': True})
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


def normalize_team(team):
    """Migrate legacy ALL staff and relocate appointments without losing owned cards."""
    staffs = team.setdefault('staffs', [])
    for staff in staffs:
        info = CATALOG.get(str(staff.get('stfid')))
        if info is not None:
            staff['commonname'] = info['name']
            staff['position'] = info['position']
            staff['material'] = info['material']
        elif staff.get('position') not in SLOT_TYPES.values():
            staff['position'] = 'FRONT'
        if str(staff.get('stfid')) in ('9502', '9505'):
            staff['commonname'] = 'Staff #' + str(staff['stfid']) + ' (unverified)'
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
                 'effectstring': 'Đổi một chỉ số thưởng trong cùng nhóm.' if i <= 3 else 'Tăng một chỉ số thưởng thêm +1 (tối đa +5).',
                 'effectidx': (i - 1) % 3 + 1,
                 'boosttype': 'change' if i <= 3 else 'enhance'} for i in range(1, 7)}
STUDY_MAX_FEE = 100000

def study_boost(staff, rule):
    idx = rule['effectidx']
    current = next((e for e in staff.get('effect', []) if e.get('effectidx') == idx and e.get('open')), None)
    if current is None:
        return None
    if rule['boosttype'] == 'enhance':
        if current.get('amount', 0) >= 5:
            return None
        return {'effectidx': idx, 'boosttype': 'enhance', 'effect': current['effect'], 'inc': 1}
    info = CATALOG.get(str(staff.get('stfid')))
    if info is None:
        return None
    choices = [STAT_KEYS[label] for label in info['boost_groups'][idx - 1]
               if label in STAT_KEYS and STAT_KEYS[label] != current['effect']]
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
        return fail('ITEM_USE_ERROR')
    staff = next((p for p in team['staffs'] if p['_id'] == ids[0]), None)
    if staff is None or staff.get('status') != 'idle' or staff.get('material') or staff.get('life', 0) <= 0:
        return fail('INVALID_STAFF')
    if staff.get('grade', 0) < rule['grade'] or staff.get('position') not in rule['position']:
        return fail('INVALID_STAFF')
    boost = study_boost(staff, rule)
    if boost is None:
        return fail('ITEM_USE_ERROR')
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
    return reply(st, slot, staffs={staff['_id']: staff})

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
        effect['amount'] = min(5, effect['amount'] + boost['inc'])
    study['history'].setdefault(lid, []).append(dict(boost))
    staff['status'] = 'idle'
    # Keep the result for the client's return popup, while freeing the location.
    study['lid'] = None
    hc._save(st)
    return reply(st, slot, staffs={staff['_id']: staff})


def install(module):
    global hc
    hc = module
    hc.ENDPOINTS["/staff/recruit"] = recruit_list
    hc.ROUTES[:0] = [
        (re.compile(r"/staff/study/(\d+)/complete"), study_complete),
        (re.compile(r"/staff/study/(\d+)"), study_start),
        (re.compile(r"/staff/recruit/(\d+)"), recruit),
        (re.compile(r"/staff/enhance/([0-9a-f]{24})"), enhance),
        (re.compile(r"/staff/appoint/([0-9a-f]{24})"), appoint),
        (re.compile(r"/staff/unappoint/([0-9a-f]{24})"), unappoint),
    ]
