"""Leak detector: reads the sensor data and raises leak, sensor-fault and battery alerts.

How it decides (thresholds in config.yaml):
  Sensor fault : a sensor is stuck, not responding, or reports an impossible value
  Leak         : 1. flow is above its *learned* normal, and
                 2. pressure is below what that flow should give (pipe physics),
                 for several readings in a row.
                 More demand raises flow but pressure follows the normal curve,
                 so it is not a leak. A leak adds flow AND loses extra pressure.
  Normal       : learned from the data, so it follows the daily pattern and any
                 setting changes. Learning pauses while a reading looks like a leak.
  Severity     : by how much extra water is flowing - small / medium / major
"""

import time
from collections import defaultdict, deque

from common import ALERTS, CONFIG, SENSOR, VALVE, connect, friction_factor, pressure_for, publish

RULES = CONFIG["detection"]


def find_fault(reading, recent):
    """Return a fault message, or None if the sensors look healthy.

    recent: list of the last (flow, pressure) readings for this zone.
    """
    for sensor in ("flow", "pressure"):
        if reading[sensor] is None:
            return f"{sensor} sensor not responding"
    if reading["pressure"] < 0 or reading["pressure"] > RULES["max_pressure"] or not 0 <= reading["level"] <= 100:
        return "impossible sensor reading"
    n = RULES["stuck_readings"]
    if len(recent) >= n:
        for i, sensor in enumerate(("flow", "pressure")):
            values = [r[i] for r in recent[-n:]]
            if len(set(values)) == 1 and values[0] > 0:
                return f"{sensor} sensor stuck on the same value"
    return None


def leak_signals(reading, normal_flow, learned):
    """Return (extra_flow_pct, pressure_deficit_pct) compared with the learned normal.

    extra_flow_pct      : flow above the learned normal, as % of the zone's normal flow
    pressure_deficit_pct: how far pressure is below what this flow should give
    """
    extra_flow = (reading["flow"] - learned["flow"]) / normal_flow * 100
    expected = pressure_for(reading["flow"], normal_flow, learned["supply"])
    deficit = (expected - reading["pressure"]) / expected * 100
    return extra_flow, deficit


def leak_severity(extra_flow, deficit):
    """'small' / 'medium' / 'major' if both signals say leak, else None.

    Size is judged from both signals and the bigger one wins, because the
    extra-flow estimate lags a little while demand is changing during the day.
    """
    if extra_flow < RULES["flow_rise_pct"] or deficit < RULES["pressure_drop_pct"]:
        return None
    if extra_flow >= 40 or deficit >= 25:
        return "major"
    if extra_flow >= 20 or deficit >= 15:
        return "medium"
    return "small"


def learn(learned, reading, normal_flow, extra_flow, deficit):
    """Slowly move the learned normal towards this reading, unless it looks like a leak."""
    if extra_flow >= RULES["flow_rise_pct"] / 2 and deficit >= RULES["pressure_drop_pct"] / 2:
        return  # leak-like: don't learn from it
    rate = RULES["learn_rate"]
    supply_seen = reading["pressure"] / friction_factor(reading["flow"], normal_flow)
    learned["flow"] += rate * (reading["flow"] - learned["flow"])
    learned["supply"] += rate * (supply_seen - learned["supply"])


class Detector:
    def __init__(self):
        self.recent = defaultdict(lambda: deque(maxlen=20))   # last (flow, pressure) per zone
        self.learned = {}                                      # zone -> {"flow", "supply"}
        self.leak_count = defaultdict(int)                     # consecutive leak readings
        self.active = {}                                       # (zone, type) -> alert
        self.valve_open = defaultdict(lambda: True)
        self.client = connect("aquaguard-detector", {
            SENSOR.format(zone="+"): self.on_reading,
            VALVE.format(zone="+"): self.on_valve,
        })

    def alert(self, zone, kind, status, message, severity=None):
        payload = {"zone": zone, "type": kind, "severity": severity, "status": status,
                   "message": message, "ts": time.time()}
        publish(self.client, ALERTS, payload)
        icon = {"active": "🚨", "isolated": "🔒", "resolved": "✅"}[status]
        print(f"{icon} Zone {zone}: {kind} {severity or ''} {status} - {message}")

    def on_valve(self, topic, msg):
        zone = msg["zone"]
        self.valve_open[zone] = msg["state"] == "OPEN"
        leak = self.active.get((zone, "leak"))
        if leak and msg["state"] == "CLOSED":
            del self.active[(zone, "leak")]
            self.alert(zone, "leak", "isolated", "valve closed, leak isolated", leak["severity"])

    def on_reading(self, topic, r):
        zone = r["zone"]
        normal = CONFIG["zones"][zone]
        self.recent[zone].append((r["flow"], r["pressure"]))

        # Battery warning
        low = r["battery"] < CONFIG["sensors"]["low_battery"]
        if low and (zone, "battery") not in self.active:
            self.active[(zone, "battery")] = {}
            self.alert(zone, "battery", "active", f"battery low ({r['battery']:.0f}%) - replace soon")
        elif not low and self.active.pop((zone, "battery"), None) is not None:
            self.alert(zone, "battery", "resolved", "battery replaced")

        # 1. Faulty sensor? (checked first - a broken sensor is not a leak)
        fault = find_fault(r, list(self.recent[zone]))
        if fault:
            self.leak_count[zone] = 0
            if (zone, "fault") not in self.active:
                self.active[(zone, "fault")] = {}
                self.alert(zone, "fault", "active", fault)
            return
        if self.active.pop((zone, "fault"), None) is not None:
            self.alert(zone, "fault", "resolved", "sensor readings back to normal")

        # 2. Leak? Only while water is flowing
        if not self.valve_open[zone] or r["flow"] < 1:
            self.leak_count[zone] = 0
            return
        learned = self.learned.setdefault(zone, {"flow": r["flow"], "supply": normal["pressure"]})
        extra_flow, deficit = leak_signals(r, normal["flow"], learned)
        learn(learned, r, normal["flow"], extra_flow, deficit)

        severity = leak_severity(extra_flow, deficit)
        self.leak_count[zone] = self.leak_count[zone] + 1 if severity else 0
        if self.leak_count[zone] >= RULES["confirm_readings"] and (zone, "leak") not in self.active:
            self.active[(zone, "leak")] = {"severity": severity}
            self.alert(zone, "leak", "active",
                       f"{extra_flow:.0f}% extra flow, pressure {deficit:.0f}% below expected", severity)


def main():
    Detector()
    print("🔍 Leak detector watching all sensor nodes")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
