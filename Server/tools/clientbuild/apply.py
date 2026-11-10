"""Dung lai file client tu DB cua server va (tuy chon) cai vao game - GM Tool > Database > "Ap dung vao game".

    python -I apply.py            # chi dung: build/data_0.big, build/Label.big
    python -I apply.py --install  # dung roi chep vao game (chi khi fifazf.exe khong chay), sao luu ban dang dung

Thu tu (nhu quy trinh tay truoc day, xem memory fo3-season-cards-pipeline):
 1. season.py client : orig/data_0.big + orig/Label.big + Server/gamedata/fifadb.sqlite (the, ten, sua chi so cua
    GM Tool) -> build/ (dong 3D: the moi, mua huyen thoai, mua rieng + mat muon tu card_edits.json; icon mua)
 2. extras.py        : AC Milan (47) + doi PRIME / INFINITY PRIME vao fifa_ng_db cua build/data_0.big
 3. --install        : backup/<gio>/ giu 2 ban gan nhat; chep vao data/data_0.big, data/ui/components/Label.big
    (+ data/ui/localization/eng.big khi co ten CLB moi; + data/data_7.big khi co mat 3D tu anh, het mat -> tra ban goc)
Ban va cho ban be: sau khi cai, push bang tools/make_update.py nhu thuong le.
"""
import datetime, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BUILD, BACKUP = os.environ.get("FO3_BUILD") or os.path.join(HERE, "build"), os.path.join(HERE, "backup")  # FO3_BUILD: chi de test (nhu season.py)
TARGETS = {"data_0.big": os.path.join(GAME, "data", "data_0.big"),
           "Label.big": os.path.join(GAME, "data", "ui", "components", "Label.big"),
           # ten CLB moi cua su nghiep CLB (season.py build_teamnames); khong co club_history.json -> khong dung
           "eng.big": os.path.join(GAME, "data", "ui", "localization", "eng.big"),
           # mat 3D tu anh (season.py build_photo_heads); khong co mat -> khong dung, tra ban goc (xem sources)
           "data_7.big": os.path.join(GAME, "data", "data_7.big")}
OPTIONAL = ("eng.big", "data_7.big")
KEEP = 2


def py(*args):
    """Lenh python: server chay trong launcher .exe (Python dong goi, che do --run) hoac python thuong."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run", *args]
    return [sys.executable, "-I", *args]


def run(*args):
    print(">", " ".join(os.path.basename(a) for a in args), flush=True)
    r = subprocess.run(py(*args), cwd=HERE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        raise SystemExit(f"Lỗi ở bước {os.path.basename(args[0])} (mã {r.returncode}). Game chưa bị thay đổi.")


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq fifazf.exe", "/NH"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10).stdout
    except Exception:
        return False
    return "fifazf.exe" in out.lower()


def same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def sources(targets):
    """{ten: file nguon} cua cac file se chep. data_7.big: build/ khi co mat 3D tu anh; khong co mat ma
    game dang dung ban khac ban goc (vua xoa mat cuoi cung) -> chep lai orig/data_7.big."""
    src = {n: os.path.join(BUILD, n) for n in targets}
    orig7, game7 = os.path.join(HERE, "orig", "data_7.big"), TARGETS["data_7.big"]
    if "data_7.big" not in targets and os.path.isfile(orig7) and os.path.isfile(game7) and not same_file(orig7, game7):
        src["data_7.big"] = orig7
    return src


def main():
    install = "--install" in sys.argv
    if install and game_running():  # kiem tra truoc ca khi dung (dung mat ~1 phut)
        raise SystemExit("Game đang chạy: thoát game rồi bấm Áp dụng lại.")
    run(os.path.join(HERE, "season.py"), "client")
    run(os.path.join(HERE, "extras.py"), os.path.join(BUILD, "data_0.big"), "--write")
    targets = {n: d for n, d in TARGETS.items() if n not in OPTIONAL or os.path.isfile(os.path.join(BUILD, n))}
    for name, dst in targets.items():
        src = os.path.join(BUILD, name)
        assert os.path.isfile(src), src
        if name == "data_0.big":
            assert os.path.getsize(src) == os.path.getsize(os.path.join(HERE, "orig", name)), "data_0.big đổi kích thước"
    print("Đã dựng xong build/data_0.big, build/Label.big" + (", build/data_7.big (mặt 3D từ ảnh)" if "data_7.big" in targets else "") + ".", flush=True)
    if not install:
        return
    if game_running():
        raise SystemExit("Game đang chạy: thoát game rồi bấm Áp dụng lại.")
    src = sources(targets)
    targets = {n: TARGETS[n] for n in src}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUP, stamp)
    os.makedirs(bdir, exist_ok=True)
    for name, dst in targets.items():
        if os.path.isfile(dst):
            shutil.copy2(dst, os.path.join(bdir, name))
    for name, dst in targets.items():
        tmp = dst + ".new"
        shutil.copyfile(src[name], tmp)  # copyfile: ngay sua moi -> make_update gui ca ban goc tra lai cho ban be
        if os.path.isfile(dst):
            os.chmod(dst, 0o666)  # eng.big cua game de chi doc (team_names.py)
        os.replace(tmp, dst)
    old = sorted(d for d in os.listdir(BACKUP) if os.path.isdir(os.path.join(BACKUP, d)))
    for d in old[:-KEEP]:
        shutil.rmtree(os.path.join(BACKUP, d), ignore_errors=True)
    print(f"Đã cài vào game (bản cũ sao lưu ở tools/clientbuild/backup/{stamp}). Vào game để xem; "
          "muốn bạn bè có bản này thì push cập nhật như thường lệ.", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
