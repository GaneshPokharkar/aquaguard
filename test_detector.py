"""Tests for the detection rules:  python -m pytest"""

from common import pressure_for
from detector import find_fault, learn, leak_severity, leak_signals

NORMAL_FLOW, SUPPLY = 100, 3.0
LEARNED = {"flow": 100.0, "supply": 3.0}


def reading(flow=100, pressure=3.0, level=80):
    return {"flow": flow, "pressure": pressure, "level": level}


def check(flow, pressure):
    return leak_severity(*leak_signals(reading(flow, pressure), NORMAL_FLOW, LEARNED))


# ---------------------------------------------------------------- leaks

def test_normal_reading_is_not_a_leak():
    assert check(101, pressure_for(101, NORMAL_FLOW, SUPPLY)) is None


def test_more_demand_is_not_a_leak():
    # 50 % more demand: pressure drops, but exactly as the pipe physics predicts
    assert check(150, pressure_for(150, NORMAL_FLOW, SUPPLY)) is None


def test_lower_supply_pressure_is_not_a_leak():
    assert check(100, 2.4) is None          # pressure down, but no extra flow


def test_leak_needs_extra_flow_and_extra_pressure_loss():
    # Same extra flow as above, but pressure 20 % below what that flow should give
    assert check(150, pressure_for(150, NORMAL_FLOW, SUPPLY) * 0.8) == "major"


def test_leak_severity():
    def leak(size):
        flow = 100 + 50 * size
        return check(flow, pressure_for(flow, NORMAL_FLOW, SUPPLY) * (1 - 0.3 * size))
    assert leak(0.35) == "small"
    assert leak(0.6) == "medium"
    assert leak(1.0) == "major"


def test_very_high_demand_is_not_a_leak():
    # double demand at the busy morning peak: ~3x normal flow
    assert pressure_for(310, NORMAL_FLOW, SUPPLY) > 0
    assert check(310, pressure_for(310, NORMAL_FLOW, SUPPLY)) is None


def test_learning_follows_demand_but_not_a_leak():
    learned = dict(LEARNED)
    r = reading(150, pressure_for(150, NORMAL_FLOW, SUPPLY))     # demand change: learn it
    learn(learned, r, NORMAL_FLOW, *leak_signals(r, NORMAL_FLOW, learned))
    assert learned["flow"] > 100

    learned = dict(LEARNED)
    r = reading(150, pressure_for(150, NORMAL_FLOW, SUPPLY) * 0.8)  # leak: don't learn it
    learn(learned, r, NORMAL_FLOW, *leak_signals(r, NORMAL_FLOW, learned))
    assert learned == LEARNED


# ---------------------------------------------------------------- a whole simulated day

def simulate_day(leak_from=None, demand=1.0, slow=True, seconds=600):
    """Run the sensor physics + detector for one simulated day (600 readings).

    Returns (reading number, severity) when a leak is confirmed, or None.
    """
    import random
    from common import demand_factor
    random.seed(1)
    learned, in_a_row = None, 0
    for t in range(seconds):
        hour = t / seconds * 24
        leak = 0.0
        if leak_from is not None and t >= leak_from:
            leak = min((t - leak_from) / 60, 1.0) if slow else 1.0
        flow = NORMAL_FLOW * demand_factor(hour) * demand + 50 * leak
        pressure = pressure_for(flow, NORMAL_FLOW, SUPPLY) * (1 - 0.3 * leak)
        r = reading(flow * (1 + random.gauss(0, 0.015)), pressure * (1 + random.gauss(0, 0.015)))
        learned = learned or {"flow": r["flow"], "supply": SUPPLY}
        signals = leak_signals(r, NORMAL_FLOW, learned)
        learn(learned, r, NORMAL_FLOW, *signals)
        severity = leak_severity(*signals)
        in_a_row = in_a_row + 1 if severity else 0
        if in_a_row >= 3:
            return t, severity
    return None


def test_full_day_without_leak_gives_no_false_alarm():
    assert simulate_day() is None


def test_double_demand_all_day_gives_no_false_alarm():
    assert simulate_day(demand=2.0) is None


def test_slow_leak_is_caught_within_a_minute():
    found = simulate_day(leak_from=300)
    assert found is not None and found[0] - 300 <= 60


def test_major_burst_is_major_at_any_time_of_day():
    for start in range(30, 600, 60):
        found = simulate_day(leak_from=start, slow=False)
        assert found is not None and found[1] == "major", (start, found)


# ---------------------------------------------------------------- faults

def test_impossible_reading_is_a_fault():
    assert find_fault(reading(pressure=-1), []) == "impossible sensor reading"
    assert find_fault(reading(level=140), []) == "impossible sensor reading"


def test_stuck_sensor_is_a_fault():
    assert find_fault(reading(), [(100.0, 3.0 + i / 100) for i in range(6)]) == "flow sensor stuck on the same value"
    assert find_fault(reading(), [(100.0 + i, 3.0) for i in range(6)]) == "pressure sensor stuck on the same value"
    assert find_fault(reading(), [(100.0 + i, 3.0 + i / 100) for i in range(6)]) is None


def test_closed_valve_zero_flow_is_not_stuck():
    assert find_fault(reading(flow=0), [(0.0, 3.1 + i / 100) for i in range(6)]) is None


def test_sensor_not_responding():
    assert find_fault(reading(flow=None), []) == "flow sensor not responding"
    assert find_fault(reading(pressure=None), []) == "pressure sensor not responding"
