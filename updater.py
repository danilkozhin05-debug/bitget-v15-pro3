import json, hashlib, os, shutil, tempfile, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "danilkozhin05-debug/bitget-v15-pro3"
BRANCH = "main"
MANIFEST_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/updates/manifest.json"

# Never overwrite private/user-generated state during an application update.
PROTECTED_NAMES = {
    ".env",
    "config.json",
    "memory_state.json",
    "adaptive_stats.json",
    "candle_stats.json",
    "historical_models.json",
    "trades.csv",
    "paper_trades.csv",
    "signals.csv",
    "analysis_history.csv",
    "historical_candles.csv",
}

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def local_version():
    p = ROOT / "VERSION.txt"
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"

def ver_tuple(v):
    parts = []
    for x in str(v).lstrip("vV").split("."):
        try:
            parts.append(int(x))
        except Exception:
            parts.append(0)
    return tuple((parts + [0,0,0])[:3])

def download(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": "Bitget-V15-Updater"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)

def main():
    print("=" * 60)
    print(" BITGET V15 PRO - GITHUB AUTO UPDATE")
    print("=" * 60)
    print()
    print("Repository:", REPO)
    print("Installed :", local_version())
    print()

    # The updater intentionally reads the version and bot directly from GitHub.
    # This means future updates only require replacing bot.py in the repository.
    remote_version_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION.txt"
    remote_bot_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/bot.py"

    try:
        req = urllib.request.Request(remote_version_url, headers={"User-Agent":"Bitget-V15-Updater"})
        with urllib.request.urlopen(req, timeout=20) as r:
            remote = r.read().decode("utf-8").strip()
        req = urllib.request.Request(remote_bot_url, headers={"User-Agent":"Bitget-V15-Updater"})
        with urllib.request.urlopen(req, timeout=30) as r:
            remote_bot = r.read()
    except Exception as e:
        print("[ERROR] Cannot read update from GitHub:", e)
        input("\nPress Enter to close...")
        return 1

    current = local_version()
    local_bot = ROOT / "bot.py"
    local_hash = sha256(local_bot).lower() if local_bot.exists() else ""
    remote_hash = hashlib.sha256(remote_bot).hexdigest().lower()

    print("Latest    :", remote)
    print("Bot hash  :", remote_hash[:16])

    if remote_hash == local_hash:
        if ver_tuple(remote) > ver_tuple(current):
            (ROOT / "VERSION.txt").write_text(remote + "\n", encoding="utf-8")
        print("\nAlready up to date.")
        input("\nPress Enter to close...")
        return 0

    backup_root = ROOT / "backups" / remote
    backup_root.mkdir(parents=True, exist_ok=True)
    print("\nNew bot version found. Creating backup and updating bot.py...\n")

    backup = backup_root / "bot.py"
    if local_bot.exists():
        shutil.copy2(local_bot, backup)

    with tempfile.NamedTemporaryFile(delete=False, dir=str(ROOT), suffix=".tmp") as tf:
        tmp = Path(tf.name)
    try:
        tmp.write_bytes(remote_bot)
        if sha256(tmp).lower() != remote_hash:
            raise RuntimeError("SHA256 mismatch")
        os.replace(tmp, local_bot)
    except Exception as e:
        if tmp.exists(): tmp.unlink()
        if backup.exists(): shutil.copy2(backup, local_bot)
        print("[FAILED]", e)
        input("\nPress Enter to close...")
        return 2

    (ROOT / "VERSION.txt").write_text(remote + "\n", encoding="utf-8")
    print("=" * 60)
    print(" UPDATE COMPLETE")
    print("=" * 60)
    print("Version:", current, "->", remote)
    print("Updated : bot.py")
    print("Backup  :", backup)
    print("Protected memory/config files were not replaced.")
    input("\nPress Enter to close...")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
