"""Shared helpers: config, MQTT topics and a small MQTT client wrapper."""

import json
import math
import time
import sys
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

ROOT = Path(__file__).parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())

# MQTT topics
SENSOR = "aquaguard/sensors/{zone}"          # telemetry from a sensor node
STATUS = "aquaguard/sensors/{zone}/status"   # online / offline (retained, LWT)
ALERTS = "aquaguard/alerts"                  # leak and fault alerts
VALVE = "aquaguard/valves/{zone}"            # valve state (retained)
VALVE_SET = "aquaguard/valves/{zone}/set"    # open / close command
TEST = "aquaguard/test"                      # test actions from the dashboard


def friction_factor(flow, normal_flow):
    """Pipe physics: more flow means more friction, so less pressure.

    1.0 at normal flow, higher at low flow, falls towards (but never below) zero at very high flow.
    """
    k = CONFIG["physics"]["friction"]
    return (1 + k) / (1 + k * (flow / normal_flow) ** 2)


def pressure_for(flow, normal_flow, supply):
    """Pressure expected at the sensor for this flow and supply pressure."""
    return supply * friction_factor(flow, normal_flow)


def demand_factor(hour):
    """Daily water use pattern: low at night, peaks in the morning and evening (average ~1)."""
    morning = math.exp(-((hour - 7.5) ** 2) / 3)
    evening = math.exp(-((hour - 19.5) ** 2) / 4)
    return 0.55 + 0.75 * morning + 0.6 * evening + 0.25 * (6 <= hour <= 22)


def sim_hour(now=None):
    """Simulated time of day (0-24). One day lasts `day_minutes` real minutes."""
    day_s = CONFIG["sensors"]["day_minutes"] * 60
    return ((now or time.time()) % day_s) / day_s * 24


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def connect(client_id, handlers=None, will=None):
    """Connect to the broker and route JSON messages to handlers.

    handlers: {topic_filter: function(topic, payload_dict)}
    will:     (topic, payload_dict) published by the broker if we drop off
    """
    handlers = handlers or {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if will:
        client.will_set(will[0], json.dumps(will[1]), qos=1, retain=True)

    def on_connect(c, userdata, flags, reason_code, properties):
        for topic in handlers:
            c.subscribe(topic, qos=1)

    def on_message(c, userdata, msg):
        if not msg.payload:
            return
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        for topic, handler in handlers.items():
            # Check the topic ourselves: some brokers deliver extra retained messages
            if mqtt.topic_matches_sub(topic, msg.topic):
                handler(msg.topic, payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(CONFIG["broker"]["username"], CONFIG["broker"]["password"])
    client.reconnect_delay_set(1, 10)
    client.connect_async(CONFIG["broker"]["host"], CONFIG["broker"]["port"], 30)
    client.loop_start()
    return client


def publish(client, topic, payload, retain=False):
    client.publish(topic, json.dumps(payload), qos=1, retain=retain)
