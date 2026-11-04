import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    logs = Path(sys.executable).parent / "UserData"
    logs.mkdir(exist_ok=True)
    sys.stdout = sys.stderr = (logs / "audioseyx.log").open("a", encoding="utf-8", buffering=1)

if __name__ == "__main__":
    try:
        from audioseyx.app import main
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(sys, "frozen", False) and not any(flag in sys.argv for flag in ["--self-test", "--no-browser"]):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Could not start audioseyX. See UserData/audioseyx.log.", "audioseyX", 16)
        raise SystemExit(1)
