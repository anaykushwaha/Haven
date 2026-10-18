# run_simulator.py
# Haven Household Simulator CLI

# Standalone entry point for inspecting and poking at the household
# simulator without needing Alexa+, MCP, or the planner running. Meant
# for quick local checks while developing the Context Resolver and
# executor

# Usage:
#   python scripts/run_simulator.py status
#   python scripts/run_simulator.py rooms
#   python scripts/run_simulator.py people
#   python scripts/run_simulator.py devices
#   python scripts/run_simulator.py set-presence <person_id> <home|away>
#   python scripts/run_simulator.py set-occupied <room_id> <true|false>

from __future__ import annotations

import sys

from simulator.household.service import HouseholdSimulatorService


def main(argv: list[str]) -> int:
    # Parses the command line and runs the requested simulator action

    if len(argv) < 1:
        _print_usage()
        return 1

    command = argv[0]
    service = HouseholdSimulatorService()

    if command == "status":
        _print_status(service)
    elif command == "rooms":
        _print_rooms(service)
    elif command == "devices":
        _print_devices(service)
    elif command == "people":
        _print_people(service)
    elif command == "set-presence":
        return _set_presence(service, argv[1:])
    elif command == "set-occupied":
        return _set_occupied(service, argv[1:])
    else:
        _print_usage()
        return 1

    return 0


def _print_status(service: HouseholdSimulatorService) -> None:
    household = service.get_household()

    print(f"Household: {household.get('name')} ({household.get('household_id')})")
    print(f"Timezone: {household.get('timezone')}")
    print(f"People home: {len(service.people_home())} / {len(service.get_people())}")


def _print_rooms(service: HouseholdSimulatorService) -> None:
    for room in service.get_rooms():
        occupied = "occupied" if room.get("occupied") else "available"

        print(f"- {room.get('name')} [{room.get('room_id')}]: {occupied}")


def _print_devices(service: HouseholdSimulatorService) -> None:
    for device in service.get_devices():
        online = "online" if device.get("online", True) else "OFFLINE"
        print(f"- {device.get('name')} [{device.get('device_id')}]: {online} {device.get('state')}")


def _print_people(service: HouseholdSimulatorService) -> None:
    for person in service.get_people():
        print(f"- {person.get('name')} [{person.get('person_id')}]: {person.get('presence')}")


def _set_presence(service: HouseholdSimulatorService, args: list[str]) -> int:
    if len(args) != 2:
        print("Usage: set-presence <person_id> <home|away>")
        return 1

    person_id, presence = args
    service.set_presence(person_id, presence)
    print(f"Updated {person_id} presence to {presence}.")

    return 0


def _set_occupied(service: HouseholdSimulatorService, args: list[str]) -> int:
    if len(args) != 2:
        print("Usage: set-occupied <room_id> <true|false>")
        return 1

    room_id, occupied_raw = args
    occupied = occupied_raw.strip().lower() in {"true", "1", "yes"}
    service.set_room_occupied(room_id, occupied)
    print(f"Updated {room_id} occupied to {occupied}.")

    return 0


def _print_usage() -> None:
    print(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
