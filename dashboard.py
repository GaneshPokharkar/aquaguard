"""AquaGuard dashboard - one page showing the virtual IoT water sensors live.

    streamlit run dashboard.py
"""

import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import ALERTS, CONFIG, ROOT, TEST, VALVE_SET, connect
from schematic import schematic

st.set_page_config(page_title="AquaGuard", page_icon="💧", layout="wide")

ZONES = CONFIG["zones"]
COLORS = {"A": "#3987e5", "B": "#d95926", "C": "#199e70", "D": "#9085e9"}
HISTORY = 120   # readings kept per zone (2 minutes)


# ----------------------------------------------------------------------
# Live data from MQTT (shared by all browser tabs)
# ----------------------------------------------------------------------

@st.cache_resource
def live_data():
    data = {
        "readings": defaultdict(lambda: deque(maxlen=HISTORY)),
        "online": {},
        "valves": {},
        "alerts": deque(maxlen=30),
        "active": {},           # (zone, type) -> latest alert
        "leaks_seen": 0,        # counts new leak alerts (drives the alarm beep)
        "seq": {},              # zone -> [first seq, last seq, readings received]
        "lock": threading.Lock(),
    }

    def on_reading(topic, r):
        r["ts"] = r.get("ts") or time.time()           # real ESP32 nodes have no clock
        with data["lock"]:
            data["readings"][r["zone"]].append(r)
            s = data["seq"].get(r["zone"])
            if s is None or r["seq"] <= s[1]:          # first reading, or node restarted
                data["seq"][r["zone"]] = [r["seq"], r["seq"], 1]
            else:
                s[1], s[2] = r["seq"], s[2] + 1

    def on_status(topic, s):
        with data["lock"]:
            data["online"][s["zone"]] = s["online"]

    def on_valve(topic, v):
        with data["lock"]:
            data["valves"][v["zone"]] = v

    def on_alert(topic, a):
        with data["lock"]:
            data["alerts"].appendleft(a)
            key = (a["zone"], a["type"])
            if a["status"] == "active":
                data["active"][key] = a
                data["leaks_seen"] += a["type"] == "leak"
            else:
                data["active"].pop(key, None)

    data["client"] = connect(f"aquaguard-dashboard-{int(time.time())}", {
        "aquaguard/sensors/+": on_reading,
        "aquaguard/sensors/+/status": on_status,
        "aquaguard/valves/+": on_valve,
        ALERTS: on_alert,
    })
    return data


data = live_data()


def send(topic, payload):
    data["client"].publish(topic, json.dumps(payload), qos=1)


def zone_status(zone, last):
    """What a zone's sensor node is reporting right now: (label, colour, key)."""
    if not data["online"].get(zone, True) or not last or time.time() - last["ts"] > 5:
        return "⚫ Offline", "gray", "offline"
    if (zone, "fault") in data["active"]:
        return "🟠 Sensor fault", "orange", "fault"
    leak = data["active"].get((zone, "leak"))
    if leak:
        return f"🔴 {leak['severity'].title()} leak", "red", "leak"
    valve = data["valves"].get(zone, {}).get("state", "OPEN")
    if valve != "OPEN":
        return f"🟣 Valve {valve.lower()}", "violet", "closed"
    return "🟢 Normal", "green", "normal"


# ----------------------------------------------------------------------
# Sidebar: test the sensors
# ----------------------------------------------------------------------

def inject_leak(zone, severity, slow):
    send(TEST, {"action": "leak", "zone": zone, "severity": severity, "slow": slow})
    st.toast(f"{'Slow' if slow else 'Burst'} {severity} leak started in Zone {zone}")


with st.sidebar:
    st.header("🧪 Test the sensors")
    zone = st.selectbox("Zone", list(ZONES), format_func=lambda z: f"Zone {z} · {ZONES[z]['name']}")
    severity = st.radio("Leak size", ["small", "medium", "major"], index=2, horizontal=True)
    slow = st.radio("Leak type", ["Burst", "Slow (grows over 1 min)"], horizontal=True) != "Burst"
    if st.button("💥 Inject leak", type="primary", width="stretch"):
        inject_leak(zone, severity, slow)
    st.caption("Break a sensor (should show as a fault, not a leak):")
    if st.button("🧊 Stuck sensor", width="stretch"):
        send(TEST, {"action": "stuck", "zone": zone})
    if st.button("⚠️ Bad reading", width="stretch"):
        send(TEST, {"action": "bad", "zone": zone})
    if st.button("📴 Take node offline", width="stretch"):
        send(TEST, {"action": "offline", "zone": zone})
    if st.button("🔋 Replace battery", width="stretch"):
        send(TEST, {"action": "battery", "zone": zone})
        st.toast(f"Zone {zone} battery replaced")
    st.divider()
    if st.button("🔄 Reset everything", width="stretch"):
        send(TEST, {"action": "reset", "zone": "ALL"})
        for z in ZONES:
            send(VALVE_SET.format(zone=z), {"zone": z, "action": "open"})
    sound = st.toggle("🔔 Alarm sound on new leaks", value=True)

    st.header("💾 Logs")
    for name in ["readings", "alerts"]:
        path = ROOT / "logs" / f"{name}.csv"
        st.download_button(f"⬇️ {name}.csv", path.read_bytes() if path.exists() else b"", f"aquaguard_{name}.csv",
                           "text/csv", width="stretch", disabled=not path.exists())
    st.caption("Saved continuously by data_logger.py in the logs/ folder.")


# ----------------------------------------------------------------------
# Control pop-ups: opened by clicking FT, PT or V in the schematic
# ----------------------------------------------------------------------

MODES = {"ok": "✅ Working", "stuck": "🧊 Stuck", "bad": "⚠️ Bad reading", "off": "📴 Off"}


def latest(zone):
    with data["lock"]:
        rows = data["readings"][zone]
        return rows[-1] if rows else {}


@st.dialog("🌊 Flow sensor (FT)")
def flow_dialog(zone):
    last, info = latest(zone), ZONES[zone]
    mode = last.get("sensors", {}).get("flow", "ok")
    st.markdown(f"**Zone {zone} · {info['name']}** — reading "
                f"**{last['flow'] if last.get('flow') is not None else '—'} L/min** (normal {info['flow']})")
    demand = st.slider("Water demand (% of normal)", 0, 200, int(last.get("demand_pct", 100)), 10,
                       help="How much water the zone uses. More demand = more flow, but it is not a leak.")
    new_mode = st.radio("Sensor state", ["ok", "stuck", "off"], index=["ok", "stuck", "off"].index(mode)
                        if mode in ("ok", "stuck", "off") else 0, format_func=MODES.get, horizontal=True)
    if st.button("Apply", type="primary", width="stretch"):
        send(TEST, {"action": "demand", "zone": zone, "value": demand})
        send(TEST, {"action": "sensor", "zone": zone, "sensor": "flow", "mode": new_mode})
        st.toast(f"Zone {zone} flow sensor: demand {demand}%, {MODES[new_mode]}")
        st.rerun()


@st.dialog("🧭 Pressure sensor (PT)")
def pressure_dialog(zone):
    last, info = latest(zone), ZONES[zone]
    mode = last.get("sensors", {}).get("pressure", "ok")
    st.markdown(f"**Zone {zone} · {info['name']}** — reading "
                f"**{last['pressure'] if last.get('pressure') is not None else '—'} bar** (normal {info['pressure']})")
    supply = st.slider("Supply pressure (bar)", 1.0, 5.0, float(last.get("supply_bar", info["pressure"])), 0.1,
                       help="Pressure delivered to the zone by the pump / main.")
    new_mode = st.radio("Sensor state", list(MODES), index=list(MODES).index(mode) if mode in MODES else 0,
                        format_func=MODES.get, horizontal=True)
    if st.button("Apply", type="primary", width="stretch"):
        send(TEST, {"action": "supply", "zone": zone, "value": supply})
        send(TEST, {"action": "sensor", "zone": zone, "sensor": "pressure", "mode": new_mode})
        st.toast(f"Zone {zone} pressure sensor: {supply:.1f} bar, {MODES[new_mode]}")
        st.rerun()


@st.dialog("🔧 Valve (V)")
def valve_dialog(zone):
    state = data["valves"].get(zone, {}).get("state", "OPEN")
    reason = data["valves"].get(zone, {}).get("reason", "")
    st.markdown(f"**Zone {zone} · {ZONES[zone]['name']}** — valve is **{state}**  \n:gray[{reason}]")
    c1, c2 = st.columns(2)
    if c1.button("🔓 Open", width="stretch", disabled=state == "OPEN"):
        send(VALVE_SET.format(zone=zone), {"zone": zone, "action": "open"})
        st.toast(f"Opening Zone {zone} valve")
        st.rerun()
    if c2.button("🔒 Close", width="stretch", disabled=state == "CLOSED"):
        send(VALVE_SET.format(zone=zone), {"zone": zone, "action": "close"})
        st.toast(f"Closing Zone {zone} valve")
        st.rerun()


@st.dialog("💧 Create a leak")
def leak_dialog(zone):
    st.markdown(f"Make a leak on the **Zone {zone} · {ZONES[zone]['name']}** pipe and watch the sensors react.")
    size = st.radio("Leak size", ["small", "medium", "major"], index=1, horizontal=True)
    kind = st.radio("Leak type", ["Burst", "Slow (grows over 1 min)"], horizontal=True)
    if st.button("💥 Start leak", type="primary", width="stretch"):
        inject_leak(zone, size, kind != "Burst")
        st.rerun()


DIALOGS = {"FT": flow_dialog, "PT": pressure_dialog, "V": valve_dialog, "PIPE": leak_dialog}


def is_manual(last, info):
    """True if an operator changed this zone's settings or sensors."""
    sensors = last.get("sensors", {})
    return (last.get("demand_pct", 100) != 100 or abs(last.get("supply_bar", info["pressure"]) - info["pressure"]) > 0.01
            or any(mode != "ok" for mode in sensors.values()))


# ----------------------------------------------------------------------
# Live page
# ----------------------------------------------------------------------

st.title("💧 AquaGuard")
st.caption("Virtual IoT sensor nodes → MQTT → leak detector → valve controller")


@st.fragment(run_every=1)
def live():
    connected = data["client"].is_connected()
    with data["lock"]:
        readings = {z: list(data["readings"][z]) for z in ZONES}

    if not connected:
        st.error("📡 MQTT broker not connected — retrying…")

    # Pipe & sensor schematic -------------------------------------------
    picture = []
    for z, info in ZONES.items():
        last = readings[z][-1] if readings[z] else {}
        picture.append({
            "id": z, "name": info["name"],
            "flow": last.get("flow"), "pressure": last.get("pressure"), "level": last.get("level", 0),
            "valve": data["valves"].get(z, {}).get("state", "OPEN"),
            "status": zone_status(z, last or None)[2],
            "ft_mode": last.get("sensors", {}).get("flow", "ok"),
            "pt_mode": last.get("sensors", {}).get("pressure", "ok"),
            "battery_low": last.get("battery", 100) < CONFIG["sensors"]["low_battery"],
            "weak_signal": last.get("rssi", 0) < CONFIG["sensors"]["weak_signal"],
            "manual": bool(last) and is_manual(last, info),
        })
    hours = next((r[-1]["sim_hour"] for r in readings.values() if r and "sim_hour" in r[-1]), None)
    clock = "" if hours is None else f"🕒 Simulated time {int(hours):02d}:{int(hours % 1 * 60):02d}"
    click = schematic(picture, clock=clock, beep=data["leaks_seen"] if sound else None)
    if click:
        DIALOGS[click["kind"]](click["zone"])

    # Zone cards ---------------------------------------------------------
    cols = st.columns(len(ZONES))
    for col, (z, info) in zip(cols, ZONES.items()):
        last = readings[z][-1] if readings[z] else None
        status, color, key = zone_status(z, last)
        with col.container(border=True):
            st.markdown(f"**Zone {z} · {info['name']}**  \n:{color}[**{status}**]")
            if last:
                if last["flow"] is None:
                    flow_line = "🌊 Flow :orange[**— no signal**]"
                else:
                    flow_pct = (last["flow"] - info["flow"]) / info["flow"] * 100
                    flow_c = "red" if key == "leak" else "gray"
                    flow_line = f"🌊 Flow **{last['flow']:.0f}** L/min :{flow_c}[({flow_pct:+.0f}%)]"
                if last["pressure"] is None:
                    pres_line = "🧭 Pressure :orange[**— no signal**]"
                else:
                    pres_pct = (last["pressure"] - info["pressure"]) / info["pressure"] * 100
                    pres_c = "red" if key == "leak" else "gray"
                    pres_line = f"🧭 Pressure **{last['pressure']:.2f}** bar :{pres_c}[({pres_pct:+.0f}%)]"
                st.markdown(f"{flow_line}  \n{pres_line}  \n🛢️ Water level **{last['level']:.0f}** %")
                age = time.time() - last["ts"]
                st.caption(f"🔋 {last['battery']:.0f}% · 📶 {last['rssi']} dBm · seen {age:.0f}s ago")
            else:
                st.caption("Waiting for the sensor node…")
            valve = data["valves"].get(z, {}).get("state", "OPEN")
            v1, v2 = st.columns([1, 1], vertical_alignment="center")
            v1.markdown(f"Valve **{valve}**")
            if valve == "OPEN":
                if v2.button("Close", key=f"close_{z}", width="stretch"):
                    send(VALVE_SET.format(zone=z), {"zone": z, "action": "close"})
            elif valve == "CLOSED":
                if v2.button("Open", key=f"open_{z}", width="stretch"):
                    send(VALVE_SET.format(zone=z), {"zone": z, "action": "open"})

    # Charts ---------------------------------------------------------------
    tabs = st.tabs(["🌊 Flow (L/min)", "🧭 Pressure (bar)", "🛢️ Water level (%)"])
    for tab, field, normal_key in zip(tabs, ["flow", "pressure", "level"], ["flow", "pressure", None]):
        fig = go.Figure()
        for z in ZONES:
            rows = readings[z]
            if not rows:
                continue
            times = [datetime.fromtimestamp(r["ts"]) for r in rows]
            fig.add_trace(go.Scatter(x=times, y=[r[field] for r in rows], name=f"Zone {z}",
                                     line=dict(color=COLORS[z], width=2)))
            if normal_key:
                fig.add_trace(go.Scatter(x=[times[0], times[-1]], y=[ZONES[z][normal_key]] * 2, showlegend=False,
                                         line=dict(color=COLORS[z], width=1, dash="dot"), hoverinfo="skip"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), hovermode="x unified",
                          legend=dict(orientation="h", y=1.1), uirevision=field)
        tab.plotly_chart(fig, width="stretch", key=f"chart_{field}")
        if normal_key:
            tab.caption("Dotted lines = typical value for each zone; real demand follows the daily pattern (busy mornings and evenings).")

    # Sensor health + alerts ----------------------------------------------
    left, right = st.columns([3, 2])
    with left:
        st.subheader("📟 Sensor nodes")
        rows = []
        for z in ZONES:
            last = readings[z][-1] if readings[z] else {}
            first, latest_seq, received = data["seq"].get(z, [0, -1, 0])
            sent = latest_seq - first + 1
            battery, rssi = last.get("battery"), last.get("rssi")
            rows.append({
                "Node": f"NODE-{z}",
                "Zone": ZONES[z]["name"],
                "Status": zone_status(z, last or None)[0],
                "Battery": None if battery is None else
                f"{battery:.0f}%" + (" 🪫 low" if battery < CONFIG["sensors"]["low_battery"] else ""),
                "Signal": None if rssi is None else
                f"{rssi} dBm" + (" ⚠️ weak" if rssi < CONFIG["sensors"]["weak_signal"] else ""),
                "Readings lost": f"{sent - received} of {sent}" if sent > 0 else None,
                "Last seen (s)": round(time.time() - last["ts"], 1) if last else None,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with right:
        st.subheader("🚨 Alerts")
        with data["lock"]:
            alerts = list(data["alerts"])[:8]
        if not alerts:
            st.success("No alerts — all sensors normal.")
        for a in alerts:
            when = datetime.fromtimestamp(a["ts"]).strftime("%H:%M:%S")
            kind = {"leak": f"{a['severity']} leak", "fault": "sensor fault", "battery": "battery"}[a["type"]]
            text = f"**{when}** · Zone {a['zone']} · {kind} · **{a['status']}** — {a['message']}"
            if a["status"] == "active":
                st.error(text)
            elif a["status"] == "isolated":
                st.warning(text)
            else:
                st.info(text)


live()
