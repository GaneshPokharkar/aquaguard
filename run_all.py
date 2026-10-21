"""Start everything with one command:  python run_all.py

Uses an MQTT broker already running on localhost:1883 (e.g. Mosquitto, set up
with the username/password from config.yaml). If there isn't one, it starts the
bundled Python broker (amqtt) with password login. Press Ctrl+C to stop.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

from common import CONFIG

ROOT = Path(__file__).parent
PY = sys.executable


def broker_running():
    try:
        socket.create_connection(("localhost", 1883), timeout=1).close()
        return True
    except OSError:
        return False


def write_password_file():
    """Create the broker's user file from the login in config.yaml."""
    from pwdlib.hashers.argon2 import Argon2Hasher
    user, password = CONFIG["broker"]["username"], CONFIG["broker"]["password"]
    (ROOT / "broker_passwd").write_text(f"{user}:{Argon2Hasher().hash(password)}\n")


def main():
    procs = []
    if broker_running():
        print("✅ Using the MQTT broker on localhost:1883")
    else:
        print("ℹ️  No broker found - starting the built-in one (amqtt, password login)")
        write_password_file()
        amqtt = Path(PY).with_name("amqtt.exe" if sys.platform == "win32" else "amqtt")
        procs.append(subprocess.Popen([str(amqtt), "-c", str(ROOT / "broker.yaml")], cwd=ROOT,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        while not broker_running():
            time.sleep(0.5)

    for script in ["valves.py", "detector.py", "data_logger.py", "sensor_nodes.py"]:
        procs.append(subprocess.Popen([PY, script], cwd=ROOT))
        time.sleep(1)
    procs.append(subprocess.Popen([PY, "-m", "streamlit", "run", "dashboard.py"], cwd=ROOT))

    print("\n🌐 Dashboard: http://localhost:8501   (Ctrl+C to stop)\n")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in reversed(procs):
            p.terminate()
        print("🛑 AquaGuard stopped")


if __name__ == "__main__":
    main()
