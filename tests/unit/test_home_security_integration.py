"""Unit tests for the home and security integrations and their simulators."""

from __future__ import annotations

import json

import pytest

from haven.integrations.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
    UnsupportedDeviceError,
)
from haven.integrations.home import AWAY_CELSIUS, HomeService
from haven.integrations.security import SecurityService
from simulator.household.service import HouseholdSimulatorService
from simulator.security.service import SecuritySimulatorService
from tests.fixtures.plans import (
    BACK_DOOR,
    BEDROOM_LIGHTS,
    FRONT_DOOR,
    KITCHEN_LIGHTS,
    LIVING_ROOM_LIGHTS,
    SECURITY_SYSTEM,
    THERMOSTAT,
    TV,
)


@pytest.fixture
def home(household_sim) -> HomeService:
    return HomeService(household_sim)


@pytest.fixture
def security(household_sim) -> SecurityService:
    return SecurityService(household_sim.security)


# --------------------------------------------------------------------------- #
# HomeService
# --------------------------------------------------------------------------- #


def test_home_lists_every_device_or_one_kind(home):
    assert len(home.list_devices()) == 8
    assert {d.device_id for d in home.list_devices("light")} == {
        LIVING_ROOM_LIGHTS,
        KITCHEN_LIGHTS,
        BEDROOM_LIGHTS,
    }
    assert home.list_devices("toaster") == []


def test_home_reports_a_device(home):
    status = home.get_device(THERMOSTAT)

    assert status.name == "Living Room Thermostat"
    assert status.device_type == "thermostat"
    assert status.room_id == "room-living-room"
    assert status.online is True


def test_unknown_device_is_not_found(home):
    with pytest.raises(DeviceNotFoundError, match="device-nope"):
        home.get_device("device-nope")
    with pytest.raises(DeviceNotFoundError):
        home.turn_off_light("device-nope")


def test_light_commands(home):
    assert home.turn_off_light(LIVING_ROOM_LIGHTS).state == {"power": "off", "brightness": 0}
    assert home.turn_on_light(LIVING_ROOM_LIGHTS).state == {"power": "on", "brightness": 100}
    assert home.turn_on_light(LIVING_ROOM_LIGHTS, brightness=35).state["brightness"] == 35
    assert home.set_light_brightness(LIVING_ROOM_LIGHTS, 20).state == {"power": "on", "brightness": 20}
    assert home.set_light_brightness(LIVING_ROOM_LIGHTS, 0).state["power"] == "off"


def test_turning_a_light_on_keeps_its_current_level(home):
    home.set_light_brightness(KITCHEN_LIGHTS, 40)

    assert home.turn_on_light(KITCHEN_LIGHTS).state["brightness"] == 40


@pytest.mark.parametrize("brightness", [-1, 101, 50.5, "high", True])
def test_brightness_must_be_a_percentage(home, brightness):
    with pytest.raises(ValueError, match="brightness"):
        home.set_light_brightness(LIVING_ROOM_LIGHTS, brightness)


def test_light_commands_only_work_on_lights(home):
    with pytest.raises(UnsupportedDeviceError) as raised:
        home.turn_off_light(THERMOSTAT)

    assert raised.value.expected == "light" and raised.value.actual == "thermostat"


def test_an_offline_device_refuses_commands(home, household_sim):
    household_sim.set_device_state(KITCHEN_LIGHTS, online=False)

    with pytest.raises(DeviceUnavailableError):
        home.turn_off_light(KITCHEN_LIGHTS)
    assert home.get_device(KITCHEN_LIGHTS).online is False  # but it can still be read
    assert household_sim.get_device_state(KITCHEN_LIGHTS)["state"]["power"] == "on"


def test_thermostat_away_and_restore(home):
    away = home.set_thermostat_away(THERMOSTAT)
    assert away.state["mode"] == "away"
    assert away.state["away"] is True
    assert away.state["target_celsius"] == AWAY_CELSIUS
    assert away.state["home_target_celsius"] == 21.0

    restored = home.restore_thermostat(THERMOSTAT)
    assert restored.state["mode"] == "heat"
    assert restored.state["away"] is False
    assert restored.state["target_celsius"] == 21.0


def test_going_away_twice_keeps_the_home_temperature(home):
    home.set_thermostat(THERMOSTAT, 23)
    home.set_thermostat_away(THERMOSTAT)

    again = home.set_thermostat_away(THERMOSTAT, away_celsius=14)

    assert again.state["target_celsius"] == 14.0
    assert again.state["home_target_celsius"] == 23.0


def test_room_occupancy_is_a_simple_fact(home, household_sim):
    household_sim.set_room_occupied("room-kitchen", True)

    assert home.room_occupied("room-kitchen") is True
    assert home.room_occupied("room-living-room") is False
    assert home.room_occupied("room-nope") is None


# --------------------------------------------------------------------------- #
# SecurityService
# --------------------------------------------------------------------------- #


def test_locking_and_unlocking(security):
    assert security.get_lock_status(FRONT_DOOR).locked is False
    assert security.lock_door(FRONT_DOOR).locked is True
    assert security.lock_door(FRONT_DOOR).locked is True  # idempotent
    assert security.unlock_door(FRONT_DOOR).locked is False


def test_arming_and_disarming(security):
    armed = security.arm_system(SECURITY_SYSTEM)
    assert armed.armed is True and armed.mode == "away"

    home_mode = security.arm_system(SECURITY_SYSTEM, mode="home")
    assert home_mode.mode == "home"

    disarmed = security.disarm_system(SECURITY_SYSTEM)
    assert disarmed.armed is False and disarmed.mode == "disarmed"


def test_unknown_arm_mode_is_rejected(security):
    with pytest.raises(ValueError, match="mode"):
        security.arm_system(SECURITY_SYSTEM, mode="panic")


def test_security_commands_validate_the_device(security, household_sim):
    with pytest.raises(DeviceNotFoundError):
        security.lock_door("device-nope")
    with pytest.raises(UnsupportedDeviceError):
        security.lock_door(LIVING_ROOM_LIGHTS)
    with pytest.raises(UnsupportedDeviceError):
        security.arm_system(FRONT_DOOR)
    household_sim.set_device_state(FRONT_DOOR, online=False)
    with pytest.raises(DeviceUnavailableError):
        security.lock_door(FRONT_DOOR)
    assert security.get_lock_status(FRONT_DOOR).online is False


def test_security_status_summarises_the_household(security):
    status = security.get_security_status()
    assert status.armed is False
    assert status.all_locked is False
    assert sorted(status.unlocked_device_ids) == [BACK_DOOR, FRONT_DOOR]

    security.lock_door(FRONT_DOOR)
    security.lock_door(BACK_DOOR)
    security.arm_system(SECURITY_SYSTEM)

    status = security.get_security_status()
    assert status.armed is True
    assert status.all_locked is True
    assert status.unlocked_device_ids == []
    assert len(status.locks) == 2 and len(status.alarms) == 1


def test_security_status_with_no_locks_is_not_all_locked():
    class NoDevices:
        def get_devices(self):
            return []

    status = SecurityService(NoDevices()).get_security_status()

    assert status.all_locked is False
    assert status.armed is False


# --------------------------------------------------------------------------- #
# Simulators
# --------------------------------------------------------------------------- #


def test_household_and_security_share_one_device_store(household_sim):
    household_sim.set_device_state(FRONT_DOOR, state={"locked": True})

    assert household_sim.security.get_device_state(FRONT_DOOR)["state"]["locked"] is True

    household_sim.security.set_device_state(BACK_DOOR, state={"locked": True})

    assert household_sim.get_device_state(BACK_DOOR)["state"]["locked"] is True


def test_device_records_are_copies(household_sim):
    record = household_sim.get_device_state(LIVING_ROOM_LIGHTS)
    record["state"]["power"] = "tampered"
    for device in household_sim.get_devices():
        device["state"]["power"] = "tampered"

    assert household_sim.get_device_state(LIVING_ROOM_LIGHTS)["state"]["power"] == "on"


def test_reload_discards_in_memory_changes(household_sim):
    household_sim.set_device_state(FRONT_DOOR, state={"locked": True})
    household_sim.set_device_state(KITCHEN_LIGHTS, online=False)

    household_sim.reload()

    assert household_sim.get_device_state(FRONT_DOOR)["state"]["locked"] is False
    assert household_sim.get_device_state(KITCHEN_LIGHTS)["online"] is True


def test_unknown_devices_are_rejected_by_the_store(household_sim):
    assert household_sim.get_device_state("device-nope") is None
    with pytest.raises(KeyError):
        household_sim.set_device_state("device-nope", state={"x": 1})


def test_security_simulator_summary():
    sim = SecuritySimulatorService()
    sim.set_device_state(FRONT_DOOR, state={"locked": True})

    summary = sim.get_security_state()

    assert summary["armed"] is False
    assert summary["all_locked"] is False
    assert {lock["device_id"]: lock["locked"] for lock in summary["locks"]} == {
        FRONT_DOOR: True,
        BACK_DOOR: False,
    }
    assert [d["device_id"] for d in sim.get_devices_by_type("security_system")] == [SECURITY_SYSTEM]


def test_security_simulator_reads_a_custom_file(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"device_id": "d1", "name": "D1", "device_type": "lock"}]))

    sim = SecuritySimulatorService(path)

    assert [d["device_id"] for d in sim.get_devices()] == ["d1"]
    assert sim.get_security_state()["all_locked"] is False


def test_security_simulator_needs_its_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SecuritySimulatorService(tmp_path / "missing.json")


def test_household_snapshot_includes_every_device(household_sim):
    snapshot = household_sim.get_snapshot()

    assert {d.device_id for d in snapshot.devices} >= {THERMOSTAT, BACK_DOOR, TV}
    assert snapshot.get_device(THERMOSTAT).device_type.value == "thermostat"


def test_demo_rooms_list_the_new_devices(household_sim):
    rooms = {r["room_id"]: r["device_ids"] for r in household_sim.get_rooms()}
    known = {d["device_id"] for d in household_sim.get_devices()}

    assert THERMOSTAT in rooms["room-living-room"]
    assert BACK_DOOR in rooms["room-kitchen"]
    assert all(device_id in known for ids in rooms.values() for device_id in ids)


def test_a_fresh_simulator_per_test_has_no_leftover_state():
    assert HouseholdSimulatorService().get_device_state(FRONT_DOOR)["state"]["locked"] is False
