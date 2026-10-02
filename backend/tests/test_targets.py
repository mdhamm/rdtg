import pytest

from rdtg.harness import RigError, make_rig, open_target, release_target

from .conftest import DEMO


@pytest.fixture(autouse=True)
def _release():
    yield
    release_target()


def test_target_lookup(demo):
    cfg = demo.config
    assert cfg.target().name == "Simulated Hardware"            # default_target
    assert cfg.target("hardware").name == "Hardware"            # case-insensitive
    assert [t.kind for t in cfg.targets] == ["simulated", "partial", "hardware"]
    with pytest.raises(KeyError):
        cfg.target("nope")


def test_unavailable_targets_report_why(demo):
    with pytest.raises(RigError, match="DDC BusTools/1553 driver not found"):
        open_target(demo, "Partially Simulated Hardware")
    with pytest.raises(RigError, match="NI-DAQmx driver not found"):
        open_target(demo, "Hardware")


def test_simulated_hardware_target_round_trip(demo):
    rig = make_rig(DEMO)                    # default target: launches the ECU-7 program
    rig.reset()
    rig.set("FUEL_PUMP_OK", True)
    t0 = rig.set("IGNITION_SW", "ON")
    rig.expect("ENGINE_RUNNING", "==", True, within_ms=500, since=t0)
    # same target + params -> adapters reused
    assert make_rig(DEMO).backend.adapters is rig.backend.adapters


def test_target_params_reach_start_hook(demo):
    rig = make_rig(DEMO, params={"bugs": "no_fuel_pump_check"})
    rig.reset()
    t0 = rig.set("IGNITION_SW", "ON")       # fuel pump NOT ok, but the bug ignores it
    rig.expect("ENGINE_RUNNING", "==", True, within_ms=500, since=t0)
