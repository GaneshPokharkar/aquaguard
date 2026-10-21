"""Data logger: saves every sensor reading and alert to CSV files in logs/.

    logs/readings.csv   one row per sensor reading
    logs/alerts.csv     one row per alert
"""

import csv
import time
from datetime import datetime

from common import ALERTS, ROOT, SENSOR, connect

LOGS = ROOT / "logs"
READING_FIELDS = ["time", "zone", "flow", "pressure", "level", "battery", "rssi", "seq"]
ALERT_FIELDS = ["time", "zone", "type", "severity", "status", "message"]


def append(path, fields, row):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)


def stamp(ts):
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")   # ESP32 sends ts=0


def main():
    LOGS.mkdir(exist_ok=True)
    connect("aquaguard-logger", {
        SENSOR.format(zone="+"): lambda t, r: append(LOGS / "readings.csv", READING_FIELDS, {**r, "time": stamp(r.get("ts"))}),
        ALERTS: lambda t, a: append(LOGS / "alerts.csv", ALERT_FIELDS, {**a, "time": stamp(a["ts"])}),
    })
    print(f"💾 Logging readings and alerts to {LOGS}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
