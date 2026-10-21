"""Valve controller: virtual motorised valves, one per zone.

Closes a zone's valve automatically when a leak alert arrives, and accepts
manual open/close commands from the dashboard.
"""

import threading
import time

from common import ALERTS, CONFIG, VALVE, VALVE_SET, connect, publish

TRAVEL_S = 1.5   # time for the motor to open or close the valve


class Valves:
    def __init__(self):
        self.state = {}
        self.client = connect("aquaguard-valves", {
            ALERTS: self.on_alert,
            VALVE_SET.format(zone="+"): self.on_command,
        })
        time.sleep(1)
        for zone in CONFIG["zones"]:
            self.set_state(zone, "OPEN", "startup")

    def set_state(self, zone, state, reason):
        self.state[zone] = state
        publish(self.client, VALVE.format(zone=zone),
                {"zone": zone, "state": state, "reason": reason, "ts": time.time()}, retain=True)
        print(f"🔧 Zone {zone} valve {state} ({reason})")

    def move(self, zone, target, reason):
        if self.state.get(zone) == target:
            return    # already there
        def run():
            self.set_state(zone, "CLOSING" if target == "CLOSED" else "OPENING", reason)
            time.sleep(TRAVEL_S)
            self.set_state(zone, target, reason)
        threading.Thread(target=run, daemon=True).start()

    def on_alert(self, topic, alert):
        if alert["type"] == "leak" and alert["status"] == "active":
            self.move(alert["zone"], "CLOSED", f"auto: {alert['severity']} leak")

    def on_command(self, topic, cmd):
        target = "CLOSED" if cmd.get("action") == "close" else "OPEN"
        self.move(cmd["zone"], target, "manual")


def main():
    Valves()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
