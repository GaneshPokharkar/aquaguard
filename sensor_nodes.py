"""Virtual IoT sensor nodes - one per zone.

Each node has three sensors (flow, pressure, water level) and its own MQTT
connection. It publishes a reading every second, reports battery and Wi-Fi
signal, and uses an MQTT "last will" so the broker marks it offline if it drops.

Realistic behaviour:
  - water demand follows a daily pattern (morning / evening peaks)
  - pressure falls a little as flow rises (pipe friction)
  - the battery drains; a flat battery takes the node offline
  - a weak Wi-Fi signal loses some readings

Test actions (from the dashboard) inject leaks, break sensors or change settings.
"""

import math
import random
import time

from common import CONFIG, SENSOR, STATUS, TEST, VALVE, connect, demand_factor, pressure_for, publish, sim_hour

SENSORS = CONFIG["sensors"]

# A full-size leak: extra flow escaping, plus extra pressure lost at the leak
LEAK_SIZE = {"small": 0.35, "medium": 0.6, "major": 1.0}   # fraction of the full effect
LEAK_FLOW, LEAK_PRESSURE = 0.50, 0.30
SLOW_LEAK_S = 60                                            # a slow leak reaches full size in a minute


class SensorNode:
    def __init__(self, zone, info):
        self.zone = zone
        self.node_id = f"NODE-{zone}"
        self.normal_flow = info["flow"]
        self.normal_pressure = info["pressure"]
        self.start_battery = float(info.get("battery", 100))
        self.base_rssi = info.get("signal", -60)
        self.valve_open = True
        self.seq = 0
        self.reset()
        self.client = connect(
            self.node_id,
            {VALVE.format(zone=zone): self.on_valve},
            will=(STATUS.format(zone=zone), {"zone": zone, "online": False}),
        )
        time.sleep(0.3)  # stagger connections
        self.set_online(True)

    def reset(self):
        self.leak = None                             # None / small / medium / major
        self.leak_started = 0.0
        self.leak_slow = False
        self.level = 80.0
        self.battery = self.start_battery
        self.demand_pct = 100                        # operator setting, % of normal demand
        self.supply_bar = self.normal_pressure       # operator setting, supply pressure
        self.mode = {"flow": "ok", "pressure": "ok"} # per sensor: ok / stuck / bad / off
        self.last = {"flow": None, "pressure": None}
        self.online = True

    def set_online(self, online):
        self.online = online
        publish(self.client, STATUS.format(zone=self.zone), {"zone": self.zone, "online": online}, retain=True)

    def on_valve(self, topic, msg):
        self.valve_open = msg.get("state") == "OPEN"
        if not self.valve_open:
            self.leak = None      # closing the valve isolates the leak

    def leak_strength(self):
        """0..1: how big the leak is right now (a slow leak grows over time)."""
        if not self.leak:
            return 0.0
        size = LEAK_SIZE[self.leak]
        if self.leak_slow:
            size *= min((time.time() - self.leak_started) / SLOW_LEAK_S, 1.0)
        return size

    # ------------------------------------------------------------------
    def read_sensors(self):
        """Return (flow, pressure, level) as the sensors would measure them."""
        noise = SENSORS["noise_pct"] / 100
        demand = self.normal_flow * demand_factor(sim_hour()) * self.demand_pct / 100
        leak = self.leak_strength()

        if not self.valve_open:
            flow, pressure = 0.0, self.supply_bar * 1.05          # no flow, static pressure
        else:
            flow = demand + self.normal_flow * LEAK_FLOW * leak    # water escaping adds to the flow
            pressure = pressure_for(flow, self.normal_flow, self.supply_bar) * (1 - LEAK_PRESSURE * leak)

        # Water level in the zone's storage tank: drains on a leak, refills afterwards
        self.level += 0.05 * math.sin(time.time() / 30)
        if leak:
            self.level -= 0.5 * leak
        elif self.level < 80:
            self.level += 0.1
        self.level = min(max(self.level, 0), 100)

        true_values = {
            "flow": round(max(flow * (1 + random.gauss(0, noise)), 0), 2),
            "pressure": round(max(pressure * (1 + random.gauss(0, noise)), 0), 2),
        }
        # Each sensor can be broken on its own
        measured = {}
        for sensor, value in true_values.items():
            mode = self.mode[sensor]
            if mode == "stuck":           # frozen on its last value
                value = self.last[sensor] if self.last[sensor] is not None else value
            elif mode == "bad":           # impossible value from a broken sensor
                value = -1.0
            elif mode == "off":           # sensor not responding
                value = None
            measured[sensor] = value
            if value is not None:
                self.last[sensor] = value
        return measured["flow"], measured["pressure"], round(self.level, 1)

    def tick(self):
        if not self.online:
            return
        flow, pressure, level = self.read_sensors()
        self.battery = max(self.battery - SENSORS["battery_drain"], 0)
        if self.battery <= 0:                          # flat battery: node goes silent
            print(f"🪫 {self.node_id} battery empty - going offline")
            self.set_online(False)
            return
        self.seq += 1
        rssi = self.base_rssi + random.randint(-3, 3)
        if rssi < SENSORS["weak_signal"] and random.random() < 0.15:
            return                                     # reading lost over a weak Wi-Fi link
        publish(self.client, SENSOR.format(zone=self.zone), {
            "zone": self.zone,
            "node": self.node_id,
            "seq": self.seq,           # gaps = lost readings
            "flow": flow,              # L/min
            "pressure": pressure,      # bar
            "level": level,            # %
            "battery": round(self.battery, 1),
            "rssi": rssi,
            "demand_pct": self.demand_pct,
            "supply_bar": self.supply_bar,
            "sensors": dict(self.mode),
            "leak_injected": bool(self.leak),
            "sim_hour": round(sim_hour(), 2),
            "ts": time.time(),
        })


def main():
    nodes = {z: SensorNode(z, info) for z, info in CONFIG["zones"].items()}

    def on_test(topic, msg):
        action, zone = msg.get("action"), msg.get("zone")
        targets = nodes.values() if zone == "ALL" else [nodes[zone]] if zone in nodes else []
        for node in targets:
            if action == "leak":
                node.leak = msg.get("severity", "medium")
                node.leak_slow = msg.get("slow", False)
                node.leak_started = time.time()
            elif action == "stuck":                       # quick test: both sensors stuck
                node.mode = {"flow": "stuck", "pressure": "stuck"}
            elif action == "bad":                         # quick test: impossible pressure
                node.mode["pressure"] = "bad"
            elif action == "sensor":                      # one sensor: ok / stuck / bad / off
                node.mode[msg["sensor"]] = msg["mode"]
            elif action == "demand":
                node.demand_pct = float(msg["value"])
            elif action == "supply":
                node.supply_bar = float(msg["value"])
            elif action == "battery":                     # replace the battery
                node.battery = 100.0
                node.set_online(True)
            elif action == "offline":
                node.set_online(False)
            elif action == "reset":
                node.reset()
                node.set_online(True)
        print(f"🧪 Test: {action} zone {zone} {msg}")

    control = connect("aquaguard-test-listener", {TEST: on_test})
    print(f"📡 {len(nodes)} sensor nodes publishing every {SENSORS['interval_s']}s")
    try:
        while True:
            for node in nodes.values():
                node.tick()
            time.sleep(SENSORS["interval_s"])
    except KeyboardInterrupt:
        for node in nodes.values():
            node.set_online(False)
        control.loop_stop()


if __name__ == "__main__":
    main()
