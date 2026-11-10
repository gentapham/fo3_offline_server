"""Dung lai file client tu DB cua server va (tuy chon) cai vao game - GM Tool > Database > "Ap dung vao game".

    python -I apply.py            # chi dung: build/data_0.big, build/Label.big
    python -I apply.py --install  # dung roi chep vao game (chi khi fifazf.exe khong chay), sao luu ban dang dung

Thu tu (nhu quy trinh tay truoc day, xem memory fo3-season-cards-pipeline):
 1. season.py client : orig/data_0.big + orig/Label.big + Server/gamedata/fifadb.sqlite (the, ten, sua chi so cua
    GM Tool) -> build/ (dong 3D: the moi, mua huyen thoai, mua rieng + mat muon tu card_edits.json; icon mua)
 2. extras.py        : AC Milan (47) + doi PRIME / INFINITY PRIME vao fifa_ng_db cua build/data_0.big
 3. --install        : backup/<gio>/ giu 2 ban gan nhat; chep vao data/data_0.big, data/ui/components/Label.big
Ban va cho ban be: sau khi cai, push bang tools/make_update.py nhu thuong le.
"""
import datetime, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BUILD, BACKUP = os.path.join(HERE, "build"), os.path.join(HERE, "backup")
TARGETS = {"data_0.big": os.path.join(GAME, "data", "data_0.big"),
           "Label.big": os.path.join(GAME, "data", "ui", "components", "Label.big")}
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


def main():
    install = "--install" in sys.argv
    if install and game_running():  # kiem tra truoc ca khi dung (dung mat ~1 phut)
        raise SystemExit("Game đang chạy: thoát game rồi bấm Áp dụng lại.")
    run(os.path.join(HERE, "season.py"), "client")
    run(os.path.join(HERE, "extras.py"), os.path.join(BUILD, "data_0.big"), "--write")
    for name, dst in TARGETS.items():
        src = os.path.join(BUILD, name)
        assert os.path.isfile(src), src
        if name == "data_0.big":
            assert os.path.getsize(src) == os.path.getsize(os.path.join(HERE, "orig", name)), "data_0.big đổi kích thước"
    print("Đã dựng xong build/data_0.big, build/Label.big.", flush=True)
    if not install:
        return
    if game_running():
        raise SystemExit("Game đang chạy: thoát game rồi bấm Áp dụng lại.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUP, stamp)
    os.makedirs(bdir, exist_ok=True)
    for name, dst in TARGETS.items():
        if os.path.isfile(dst):
            shutil.copy2(dst, os.path.join(bdir, name))
    for name, dst in TARGETS.items():
        tmp = dst + ".new"
        shutil.copyfile(os.path.join(BUILD, name), tmp)
        os.replace(tmp, dst)
    old = sorted(d for d in os.listdir(BACKUP) if os.path.isdir(os.path.join(BACKUP, d)))
    for d in old[:-KEEP]:
        shutil.rmtree(os.path.join(BACKUP, d), ignore_errors=True)
    print(f"Đã cài vào game (bản cũ sao lưu ở tools/clientbuild/backup/{stamp}). Vào game để xem; "
          "muốn bạn bè có bản này thì push cập nhật như thường lệ.", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
