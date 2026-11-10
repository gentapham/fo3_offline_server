"""Tay cam XInput trong game: them ten thiet bi vao buttonDataXenon.ini.

Game (engine FIFA) nhan tay cam theo TEN DirectInput: buttonDataXenon.ini (so do nut Xbox) chi ap cho cac ten
co trong danh sach AddAlias; ten la -> game bo qua tay cam. Tay cam doi moi (Flydigi, 8BitDo, GameSir, PDP...)
o che do X-input van hien trong DirectInput nhung voi ten rieng, vd Flydigi VADER4 doi ten 045E:028E thanh
"Controller (Flydigi VADER4)" (registry Joystick\\OEM\\...\\OEMName) -> game khong nhan (2026-11-10).

ensure_aliases(): doc ten cac tay cam XInput tung cam vao may (registry OEM cua DirectInput) + danh sach ten
pho bien, them nhung ten con thieu vao bo "My XBox 360 Controller" cua buttonDataXenon.ini (giu nguyen phan con lai).
Goi luc may chu bat va ngay truoc khi launcher mo game (qua i18n.apply_game).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
XENON = os.path.join(GAME, "buttonDataXenon.ini")

# Ten DirectInput thuong gap cua tay cam X-input doi moi (them san cho may chua cam tay cam lan nao)
KNOWN = [
    "Controller (Xbox One For Windows)", "Controller (Xbox Series X For Windows)", "Controller (Xbox Wireless Controller)",
    "Xbox Wireless Controller", "Xbox One Controller", "Xbox 360 Controller", "Xbox Series X Controller",
    "Bluetooth XINPUT compatible input device", "Controller (XBOX 360 For Windows) 0", "XINPUT Gamepad",
    "Controller (Flydigi VADER4)", "Controller (Flydigi VADER3)", "Controller (Flydigi VADER 3)",
    "Controller (Flydigi VADER 4)", "Controller (Flydigi APEX 4)", "Controller (Flydigi APEX 3)",
    "Controller (Flydigi Direwolf)", "Controller (Flydigi DIREWOLF 2)",
    "Controller (8BitDo Ultimate)", "Controller (8BitDo Pro 2)", "Controller (GameSir T4 Pro)", "Controller (GameSir G7)",
    "Controller (PDP Xbox Controller)", "Controller (PowerA Xbox Series X Controller)",
    "Controller (Gamepad F310)", "Controller (Wireless Gamepad F710)", "Controller (Rock Candy Gamepad for Xbox 360)",
]
# VID/PID ma tay cam X-input (va phan mem gia lap) hay dung: Microsoft Xbox 360 / One / Series
XINPUT_IDS = {"VID_045E&PID_028E", "VID_045E&PID_028F", "VID_045E&PID_0291", "VID_045E&PID_0719", "VID_045E&PID_02D1",
              "VID_045E&PID_02DD", "VID_045E&PID_02E3", "VID_045E&PID_02EA", "VID_045E&PID_02FD", "VID_045E&PID_02FF",
              "VID_045E&PID_0B00", "VID_045E&PID_0B05", "VID_045E&PID_0B12", "VID_045E&PID_0B13", "VID_045E&PID_0B20",
              "VID_045E&PID_0B22"}


def installed_names():
    """Ten DirectInput (OEMName) cua cac tay cam X-input da tung cam vao may nay."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    key = r"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            k = winreg.OpenKey(root, key)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        name = str(winreg.QueryValueEx(s, "OEMName")[0]).strip()
                except OSError:
                    continue
                if not name:
                    continue
                # ten do driver X-input dat ("Controller (...)") hoac ma Xbox -> dung so do nut Xbox
                if sub.upper() in XINPUT_IDS or name.startswith("Controller (") or "XINPUT" in name.upper():
                    if name not in out:
                        out.append(name)
    return out


def ensure_aliases(path=XENON):
    """Them ten tay cam con thieu vao bo dieu khien Xbox dau tien. Tra ve danh sach ten da them."""
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("latin-1")
    m = re.search(r'^AddController[^\r\n]*\r?\n', text, re.M)
    if not m:
        return []
    have = {a.lower() for a in re.findall(r'AddAlias\s+"([^"]*)"', text)}
    want = []
    for n in installed_names() + KNOWN:
        if n.lower() not in have and '"' not in n and all(ord(c) < 256 for c in n):
            want.append(n); have.add(n.lower())
    if not want:
        return []
    nl = "\r\n" if "\r\n" in text else "\n"
    add = "".join(f'    AddAlias "{n}"{nl}' for n in want)
    text = text[:m.end()] + add + text[m.end():]
    if not os.path.isfile(path + ".goc"):  # sao luu ban goc 1 lan
        with open(path + ".goc", "wb") as f:
            f.write(raw)
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass
    with open(path + ".tmp", "wb") as f:
        f.write(text.encode("latin-1"))
    os.replace(path + ".tmp", path)
    return want


if __name__ == "__main__":
    print("Tay cam X-input tren may:", installed_names())
    print("Da them:", ensure_aliases())
