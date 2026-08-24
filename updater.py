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
    print("Checking  :", MANIFEST_URL)
    print()

    try:
        req = urllib.request.Request(MANIFEST_URL, headers={"User-Agent": "Bitget-V15-Updater"})
        with urllib.request.urlopen(req, timeout=20) as r:
            manifest = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("[ERROR] Cannot read GitHub update manifest:", e)
        input("\nPress Enter to close...")
        return 1

    remote = str(manifest.get("version", "0.0.0"))
    current = local_version()

    print("Latest    :", remote)
    if ver_tuple(remote) <= ver_tuple(current):
        print("\nAlready up to date.")
        input("\nPress Enter to close...")
        return 0

    backup_root = ROOT / "backups" / remote
    backup_root.mkdir(parents=True, exist_ok=True)

    files = manifest.get("files", [])
    if not files:
        print("\n[ERROR] New version exists but manifest has no files.")
        input("\nPress Enter to close...")
        return 2

    updated = 0
    print("\nNew version found. Creating backup and updating files...\n")

    for item in files:
        rel = item["path"].replace("\\", "/")
        name = Path(rel).name

        if name in PROTECTED_NAMES or rel in PROTECTED_NAMES:
            print("[SKIP PROTECTED]", rel)
            continue

        url = item.get("url") or f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}"
        expected = item.get("sha256", "").lower()

        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(delete=False, dir=str(dst.parent)) as tf:
            tmp = Path(tf.name)

        try:
            print("[DOWNLOAD]", rel)
            download(url, tmp)

            if expected and sha256(tmp).lower() != expected:
                raise RuntimeError("SHA256 mismatch")

            if dst.exists():
                b = backup_root / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, b)

            os.replace(tmp, dst)
            updated += 1
            print("[UPDATED ]", rel)
        except Exception as e:
            print("[FAILED  ]", rel, "-", e)
            if tmp.exists():
                tmp.unlink()
            print("\nUpdate stopped to avoid a partial silent update.")
            input("\nPress Enter to close...")
            return 3

    (ROOT / "VERSION.txt").write_text(remote + "\n", encoding="utf-8")

    print("\n" + "=" * 60)
    print(" UPDATE COMPLETE")
    print("=" * 60)
    print("Version:", current, "->", remote)
    print("Files updated:", updated)
    print("Backup:", backup_root)
    print("\nProtected memory/config files were not replaced.")
    input("\nPress Enter to close...")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
