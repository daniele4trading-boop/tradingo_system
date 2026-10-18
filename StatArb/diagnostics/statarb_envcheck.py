import os, sys, platform, socket, getpass, json
from urllib.request import urlopen

def line(): print("-" * 70)

print("STATARB ENV CHECK")
line()

# CHECK 2 - Sistema operativo
print("[2] OS")
print("  platform.system()  :", platform.system())
print("  platform.release() :", platform.release())
print("  platform.version() :", platform.version())
print("  platform.platform():", platform.platform())
print("  os.name            :", os.name)        # 'nt' = Windows | 'posix' = Linux/Unix
print("  sys.platform       :", sys.platform)   # 'win32' | 'linux'
line()

# CHECK 3 - Identita' macchina
print("[3] MACCHINA")
print("  hostname :", socket.gethostname())
print("  utente   :", getpass.getuser())
print("  cwd      :", os.getcwd())
line()

# CHECK 4 - Filesystem Windows
print("[4] FILESYSTEM")
print("  esiste C:\\           :", os.path.exists("C:\\"))
for p in [r"C:\MT5_XM\terminal64.exe",
          r"C:\MT5_VTMarkets\terminal64.exe",
          r"C:\MT5_Vantage\terminal64.exe"]:
    print(f"  esiste {p:38s}:", os.path.exists(p))
line()

# CHECK 5 - WSL? (Windows nativo vs Linux-dentro-Windows)
print("[5] WSL DETECTION")
wsl_env = os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP")
print("  WSL env var:", wsl_env)
procver = ""
try:
    if os.path.exists("/proc/version"):
        with open("/proc/version") as f:
            procver = f.read().strip()
except Exception as e:
    procver = f"(n/d: {e})"
print("  /proc/version:", procver[:120])
is_wsl = bool(wsl_env) or ("microsoft" in procver.lower())
print("  -> sembra WSL:", is_wsl)
line()

# CHECK 6 - MetaTrader5 (la capacita' che conta davvero)
print("[6] METATRADER5")
try:
    import MetaTrader5 as mt5
    print("  import MetaTrader5: OK")
    print("  mt5.__version__   :", getattr(mt5, "__version__", "n/d"))
    try:
        ok = mt5.initialize()
        print("  mt5.initialize()  :", ok, "| version:", mt5.version() if ok else None)
        mt5.shutdown()
    except Exception as e:
        print("  mt5.initialize()  : errore ->", e)
except Exception as e:
    print("  import MetaTrader5: FALLITO ->", repr(e))
line()

# CHECK 7 - IP PUBBLICO (prova decisiva: deve essere 144.91.76.28)
print("[7] IP PUBBLICO (deve essere 144.91.76.28 per la VPS Contabo)")
ip = None
for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
    try:
        ip = urlopen(url, timeout=8).read().decode().strip()
        print(f"  {url} -> {ip}")
        break
    except Exception as e:
        print(f"  {url} -> errore: {e}")
print("  IP rilevato        :", ip)
print("  E' la VPS Contabo? :", ip == "144.91.76.28")
line()

# Riepilogo macchina
print("[SUMMARY]")
print(json.dumps({
    "os": platform.system(),
    "is_windows_native": (os.name == "nt") and (not is_wsl),
    "is_wsl": is_wsl,
    "public_ip": ip,
    "is_contabo_vps": ip == "144.91.76.28",
    "C_drive": os.path.exists("C:\\"),
}, indent=2))
