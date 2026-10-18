# seed_data.py
# Haven Simulator Data Seeder

# Resets the simulator's JSON demo data files back to known-good
# defaults. Useful after run_simulator.py has been used to mutate
# presence/occupancy/device state during manual testing, or whenever
# a demo run needs to start from a clean, predictable household state

# Usage:
#   python scripts/seed_data.py

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

HOUSEHOLD_DEFAULT = {
    "household_id": "household-001",
    "name": "The Rivera Household",
    "timezone": "America/New_York",
    "preferences": {
        "media": {
            "preferred_genres": ["sci-fi", "comedy"],
            "avoid_genres": ["horror"],
        },
        "movie_night": {
            "typical_day": "friday",
            "typical_start_time": "19:30",
        },
    },
}

ROOMS_DEFAULT = [
    {
        "room_id": "room-living-room",
        "name": "Living Room",
        "occupied": False,
        "current_activity": None,
        "device_ids": [
            "device-tv-living-room",
            "device-lights-living-room",
            "device-thermostat-living-room"
        ]
    },
    {
        "room_id": "room-kitchen",
        "name": "Kitchen",
        "occupied": False,
        "current_activity": None,
        "device_ids": [
            "device-lights-kitchen",
            "device-lock-back-door"
        ]
    },
    {
        "room_id": "room-bedroom-1",
        "name": "Primary Bedroom",
        "occupied": False,
        "current_activity": None,
        "device_ids": [
            "device-lights-bedroom-1"
        ]
    },
    {
        "room_id": "room-entry",
        "name": "Front Entry",
        "occupied": False,
        "current_activity": None,
        "device_ids": [
            "device-lock-front-door",
            "device-security-system"
        ]
    }
]

PEOPLE_DEFAULT = [
    {
        "person_id": "person-alex",
        "name": "Alex",
        "presence": "home",
        "preferences": {"preferred_genres": ["sci-fi", "drama"]},
    },
    {
        "person_id": "person-sam",
        "name": "Sam",
        "presence": "home",
        "preferences": {
            "preferred_genres": ["comedy"],
            "avoid_genres": ["horror"],
        },
    },
    {
        "person_id": "person-jamie",
        "name": "Jamie",
        "presence": "away",
        "preferences": {"preferred_genres": ["animation"]},
    },
]

DEVICES_DEFAULT = [
    {
        "device_id": "device-tv-living-room",
        "name": "Living Room TV",
        "device_type": "tv",
        "room_id": "room-living-room",
        "online": True,
        "state": {
            "power": "off",
            "current_media_id": None
        }
    },
    {
        "device_id": "device-lights-living-room",
        "name": "Living Room Lights",
        "device_type": "light",
        "room_id": "room-living-room",
        "online": True,
        "state": {
            "power": "on",
            "brightness": 80
        }
    },
    {
        "device_id": "device-lights-kitchen",
        "name": "Kitchen Lights",
        "device_type": "light",
        "room_id": "room-kitchen",
        "online": True,
        "state": {
            "power": "on",
            "brightness": 100
        }
    },
    {
        "device_id": "device-lights-bedroom-1",
        "name": "Primary Bedroom Lights",
        "device_type": "light",
        "room_id": "room-bedroom-1",
        "online": True,
        "state": {
            "power": "off",
            "brightness": 0
        }
    },
    {
        "device_id": "device-thermostat-living-room",
        "name": "Living Room Thermostat",
        "device_type": "thermostat",
        "room_id": "room-living-room",
        "online": True,
        "state": {
            "mode": "heat",
            "away": False,
            "target_celsius": 21.0,
            "home_target_celsius": 21.0
        }
    },
    {
        "device_id": "device-lock-front-door",
        "name": "Front Door Lock",
        "device_type": "lock",
        "room_id": "room-entry",
        "online": True,
        "state": {
            "locked": False
        }
    },
    {
        "device_id": "device-lock-back-door",
        "name": "Back Door Lock",
        "device_type": "lock",
        "room_id": "room-kitchen",
        "online": True,
        "state": {
            "locked": False
        }
    },
    {
        "device_id": "device-security-system",
        "name": "Home Security System",
        "device_type": "security_system",
        "room_id": "room-entry",
        "online": True,
        "state": {
            "armed": False,
            "mode": "disarmed"
        }
    }
]

MEDIA_DEFAULT = [
    {
        "media_id": "media-interstellar",
        "title": "Interstellar",
        "genres": ["sci-fi", "drama"],
        "duration_minutes": 169,
        "rating": "PG-13",
    },
    {
        "media_id": "media-the-martian",
        "title": "The Martian",
        "genres": ["sci-fi", "comedy"],
        "duration_minutes": 144,
        "rating": "PG-13",
    },
    {
        "media_id": "media-paddington-2",
        "title": "Paddington 2",
        "genres": ["comedy", "animation"],
        "duration_minutes": 103,
        "rating": "PG",
    },
    {
        "media_id": "media-arrival",
        "title": "Arrival",
        "genres": ["sci-fi", "drama"],
        "duration_minutes": 116,
        "rating": "PG-13",
    },
    {
        "media_id": "media-hunt-for-wilderpeople",
        "title": "Hunt for the Wilderpeople",
        "genres": ["comedy"],
        "duration_minutes": 101,
        "rating": "PG-13",
    },
]

TARGETS = [
    (REPO_ROOT / "simulator" / "household" / "data" / "household.json", HOUSEHOLD_DEFAULT),
    (REPO_ROOT / "simulator" / "household" / "data" / "rooms.json", ROOMS_DEFAULT),
    (REPO_ROOT / "simulator" / "household" / "data" / "people.json", PEOPLE_DEFAULT),
    (REPO_ROOT / "simulator" / "security" / "data" / "devices.json", DEVICES_DEFAULT),
    (REPO_ROOT / "simulator" / "media" / "data" / "media.json", MEDIA_DEFAULT),
]


def main() -> int:
    # Writes every default fixture back to disk, creating parent
    # directories if they don't already exist

    for path, default_value in TARGETS:
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as handle:
            json.dump(default_value, handle, indent=2)
            handle.write("\n")

        print(f"Reset {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
