"""Tay cam trong game: tu nhan dang tay cam X-input va D-input, them ten vao bo so do nut cua game.

Game (engine FIFA) nhan tay cam theo TEN DirectInput, khong theo kieu ket noi:
  - buttonDataXenon.ini: so do nut Xbox, ap cho cac ten trong danh sach AddAlias cua "My XBox 360 Controller".
  - buttonData.ini: 72 bo so do cua tay cam D-input doi cu, moi bo mot danh sach ten.
Ten khong co trong danh sach nao -> game bo qua tay cam. Tay cam doi moi mang ten rieng, vd Flydigi VADER4 o
che do X-input la "Controller (Flydigi VADER4)", o che do D-input la "Flydigi VADER4" (2026-11-10).

ensure_aliases(): lay ten cac tay cam dang cam (HID game pad / joystick) va tung cam (registry Joystick\\OEM cua
DirectInput), roi phan loai:
  - X-input (ten "Controller (...)" do driver Xbox dat, co chu Xbox/XINPUT, hoac ma Microsoft Xbox) -> buttonDataXenon.ini.
    May choi cam tay (GPD Win, OneXPlayer, ROG Ally...) dung tay cam X-input dang Xbox 360 -> nhom nay.
  - Con lai la D-input -> bo so do D-input chuan "Controller_025" cua buttonData.ini (nut 1-4 = X A B Y,
    5-8 = LB RB LT RT, 11/12 = L3 R3, can trai X/Y, can phai Z/Rz, D-pad = POV: kieu Logitech Dual Action / DualShock).
Ten da co trong bat ky danh sach nao duoc giu nguyen (khong doi so do san co). Lan dau sua, file goc sao luu .goc.
Goi luc may chu bat va ngay truoc khi launcher mo game (qua i18n.apply_game).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
XENON = os.path.join(GAME, "buttonDataXenon.ini")
DINPUT = os.path.join(GAME, "buttonData.ini")
DINPUT_PROFILE = "Controller_025"

# Ten DirectInput thuong gap (them san cho may chua cam tay cam do lan nao)
KNOWN_X = [
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
KNOWN_D = [
    "Wireless Controller",  # PS4 DualShock 4
    "DualSense Wireless Controller", "DualSense Edge Wireless Controller",  # PS5
    "Flydigi VADER4", "Flydigi VADER3", "Flydigi VADER 4", "Flydigi VADER 3", "Flydigi APEX 4",
    "8BitDo Ultimate", "8BitDo Pro 2", "8BitDo SN30 Pro", "GameSir-T4 Pro", "GameSir T4 Pro",
    "USB Gamepad", "Twin USB Joystick", "Twin USB Gamepad", "PC Game Controller", "Gamepad", "Generic USB Gamepad",
    "USB Joystick", "PS3/PC Gamepad", "PC/PS3/Android", "Wireless Gamepad",
]
# Ma tay cam Xbox cua Microsoft (va phan mem gia lap dung ma nay)
XINPUT_IDS = {"VID_045E&PID_028E", "VID_045E&PID_028F", "VID_045E&PID_0291", "VID_045E&PID_0719", "VID_045E&PID_02D1",
              "VID_045E&PID_02DD", "VID_045E&PID_02E3", "VID_045E&PID_02EA", "VID_045E&PID_02FD", "VID_045E&PID_02FF",
              "VID_045E&PID_0B00", "VID_045E&PID_0B05", "VID_045E&PID_0B12", "VID_045E&PID_0B13", "VID_045E&PID_0B20",
              "VID_045E&PID_0B22"}
OEM_KEY = r"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM"


def is_xinput(vidpid, name):
    n = name.upper()
    return (vidpid or "").upper() in XINPUT_IDS or name.startswith("Controller (") or "XINPUT" in n or "XBOX" in n


def registry_names():
    """{VID_xxxx&PID_yyyy: OEMName} - ten DirectInput cua cac thiet bi tung cam (OEMName ghi de ten HID)."""
    out = {}
    try:
        import winreg
    except ImportError:
        return out
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):  # HKCU sau -> uu tien
        try:
            k = winreg.OpenKey(root, OEM_KEY)
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
                if name:
                    out[sub.upper()] = name
    return out


def connected_controllers():
    """[(VID_xxxx&PID_yyyy, ten HID)] cua cac tay cam / joystick dang cam (HID usage page 1, usage 4 hoac 5)."""
    out = []
    try:
        import ctypes
        import ctypes.wintypes as w
        setupapi = ctypes.WinDLL("setupapi")
        hid = ctypes.WinDLL("hid")
        k32 = ctypes.WinDLL("kernel32")
    except (ImportError, OSError, AttributeError):
        return out

    class GUID(ctypes.Structure):
        _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]

    class IFD(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("guid", GUID), ("flags", w.DWORD), ("res", ctypes.c_void_p)]

    class ATTR(ctypes.Structure):
        _fields_ = [("Size", w.ULONG), ("VendorID", w.USHORT), ("ProductID", w.USHORT), ("Ver", w.USHORT)]

    class CAPS(ctypes.Structure):
        _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT)] + [("x%d" % i, w.USHORT) for i in range(30)]

    vp = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.restype = vp
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [vp, vp, ctypes.POINTER(GUID), w.DWORD, ctypes.POINTER(IFD)]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [vp, ctypes.POINTER(IFD), vp, w.DWORD, ctypes.POINTER(w.DWORD), vp]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [vp]
    k32.CreateFileW.restype = vp
    k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, vp, w.DWORD, w.DWORD, vp]
    k32.CloseHandle.argtypes = [vp]
    hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    hid.HidD_GetAttributes.argtypes = [vp, ctypes.POINTER(ATTR)]
    hid.HidD_GetProductString.argtypes = [vp, vp, w.ULONG]
    hid.HidD_GetPreparsedData.argtypes = [vp, ctypes.POINTER(vp)]
    hid.HidD_FreePreparsedData.argtypes = [vp]
    hid.HidP_GetCaps.argtypes = [vp, ctypes.POINTER(CAPS)]
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)  # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    if not h or h == vp(-1).value:
        return out
    invalid = vp(-1).value
    try:
        i = 0
        while i < 512:
            d = IFD()
            d.cbSize = ctypes.sizeof(IFD)
            if not setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(g), i, ctypes.byref(d)):
                break
            i += 1
            need = w.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), None, 0, ctypes.byref(need), None)
            if need.value < 8:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(w.DWORD))[0] = 8 if ctypes.sizeof(vp) == 8 else 6
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), buf, need, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            fh = k32.CreateFileW(path, 0, 3, None, 3, 0, None)  # chi doc thuoc tinh, khong mo quyen doc/ghi
            if not fh or fh == invalid:
                continue
            try:
                pp, caps = vp(), CAPS()
                if hid.HidD_GetPreparsedData(fh, ctypes.byref(pp)):
                    hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                if caps.UsagePage == 1 and caps.Usage in (4, 5):
                    a = ATTR()
                    a.Size = ctypes.sizeof(ATTR)
                    hid.HidD_GetAttributes(fh, ctypes.byref(a))
                    nb = ctypes.create_unicode_buffer(128)
                    hid.HidD_GetProductString(fh, nb, ctypes.sizeof(nb))
                    out.append(("VID_%04X&PID_%04X" % (a.VendorID, a.ProductID), nb.value.strip()))
            finally:
                k32.CloseHandle(fh)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h)
    return out


def detected():
    """([ten X-input], [ten D-input]) cua may nay: tay cam dang cam + tung cam."""
    reg = registry_names()
    xs, ds = [], []

    def put(vidpid, name):
        name = (name or "").strip()
        if not name or '"' in name or any(ord(c) > 255 for c in name):
            return
        (xs if is_xinput(vidpid, name) else ds).append(name)

    for vidpid, hidname in connected_controllers():
        put(vidpid, reg.get(vidpid.upper()) or hidname)  # DirectInput dung OEMName neu co, khong thi ten HID
        if hidname and reg.get(vidpid.upper()) and hidname != reg[vidpid.upper()]:
            put(vidpid, hidname)
    for vidpid, name in reg.items():
        if is_xinput(vidpid, name):  # ten tung cam: chi lay tay cam X-input (thiet bi D-input la cu co the la vo lang...)
            put(vidpid, name)
    return list(dict.fromkeys(xs)), list(dict.fromkeys(ds))


def _aliases(text):
    return {a.lower() for a in re.findall(r'AddAlias\s+"([^"]*)"', text)}


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw, raw.decode("latin-1")


def _insert(path, raw, text, controller, names):
    m = re.search(r'^AddController\s+"' + re.escape(controller) + r'"[^\r\n]*\r?\n', text, re.M) if controller else \
        re.search(r'^AddController[^\r\n]*\r?\n', text, re.M)
    if not m or not names:
        return []
    nl = "\r\n" if "\r\n" in text else "\n"
    text = text[:m.end()] + "".join(f'\tAddAlias "{n}"{nl}' for n in names) + text[m.end():]
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
    return names


def ensure_aliases(xenon=XENON, dinput=DINPUT):
    """Them ten tay cam con thieu. Tra ve {"xinput": [...], "dinput": [...]} cac ten da them."""
    if not (os.path.isfile(xenon) and os.path.isfile(dinput)):
        return {"xinput": [], "dinput": []}
    xraw, xtext = _read(xenon)
    draw, dtext = _read(dinput)
    have = _aliases(xtext) | _aliases(dtext)
    xs, ds = detected()
    add_x, add_d = [], []
    for n in xs + KNOWN_X:
        if n.lower() not in have and '"' not in n:
            add_x.append(n); have.add(n.lower())
    for n in ds + KNOWN_D:
        if n.lower() not in have and '"' not in n:
            add_d.append(n); have.add(n.lower())
    return {"xinput": _insert(xenon, xraw, xtext, None, add_x),
            "dinput": _insert(dinput, draw, dtext, DINPUT_PROFILE, add_d)}


if __name__ == "__main__":
    print("Tay cam dang cam:", connected_controllers())
    print("Phan loai (X-input, D-input):", detected())
    print("Da them:", ensure_aliases())
