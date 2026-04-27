"""
scripts/demo_setup.py
──────────────────────
SmartDetect demo data setup (Change 3 — Universal Person Tracking).

Seeds:
  - 3 locations (mall, campus, airport)
  - 10 persons across those locations with SDT-XXXX codes
  - Realistic sightings across multiple days
  - 5 bag + 3 vehicle object detections per location per day

Run:
    python scripts/demo_setup.py
    python scripts/demo_setup.py --days 3
    python scripts/demo_setup.py --wipe   # clears existing demo data
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.db import SessionLocal, init_db
from database.models import Location, ObjectSighting, Person, Sighting

# ─── Demo data ────────────────────────────────────────────────────────────────

DEMO_LOCATIONS = [
    {"id": "LOC-001", "name": "Phoenix Mall",          "type": "mall",    "address": "123 Mall Rd"},
    {"id": "LOC-002", "name": "City Campus",           "type": "campus",  "address": "456 University Ave"},
    {"id": "LOC-003", "name": "International Airport", "type": "airport", "address": "789 Airport Blvd"},
]

# 10 demo personas spread across the 3 locations
DEMO_PERSONAS = [
    {"name": "Alice",   "type": "visitor", "location": "LOC-001", "zones": ["Entrance", "Food Court", "West Wing"]},
    {"name": "Bob",     "type": "visitor", "location": "LOC-001", "zones": ["Entrance", "East Wing", "Parking"]},
    {"name": "Charlie", "type": "staff",   "location": "LOC-001", "zones": ["Staff Room", "Control Room"]},
    {"name": "Diana",   "type": "visitor", "location": "LOC-002", "zones": ["Main Gate", "Library", "Cafeteria"]},
    {"name": "Eve",     "type": "staff",   "location": "LOC-002", "zones": ["Admin Block", "Lab A", "Library"]},
    {"name": "Frank",   "type": "visitor", "location": "LOC-002", "zones": ["Main Gate", "Sports Hall"]},
    {"name": "Grace",   "type": "visitor", "location": "LOC-003", "zones": ["Check-in", "Gate A", "Departure"]},
    {"name": "Henry",   "type": "visitor", "location": "LOC-003", "zones": ["Arrivals", "Gate B", "Exit"]},
    {"name": "Isabelle","type": "staff",   "location": "LOC-003", "zones": ["Security", "Gate A", "Gate B"]},
    {"name": "Jack",    "type": "unknown", "location": "LOC-001", "zones": ["Entrance", "East Wing"]},
]

OBJECT_TYPES = {
    "bags":     ["backpack", "handbag", "suitcase"],
    "vehicles": ["car", "motorcycle", "truck"],
}


def _make_embedding(seed: int) -> list:
    rng  = np.random.default_rng(seed=seed * 997 + 1)
    vec  = rng.standard_normal(512).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-8
    return vec.tolist()


def _random_ts(base: datetime, hour_start: int, hour_end: int) -> datetime:
    offset = random.randint(hour_start * 3600, hour_end * 3600 - 60)
    return base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=offset)


def setup_demo(days: int = 1, wipe: bool = False) -> None:
    init_db()
    db = SessionLocal()

    try:
        # Wipe
        if wipe:
            print("  Wiping SDT-* demo persons…")
            demo = db.query(Person).filter(Person.unique_code.like("SDT-%")).all()
            for p in demo:
                db.delete(p)
            db.commit()
            db.query(ObjectSighting).delete()
            db.commit()
            print(f"  Removed {len(demo)} demo persons and all object sightings.\n")

        # ── Seed locations ────────────────────────────────────────────────
        print("  Ensuring locations exist…")
        for loc_data in DEMO_LOCATIONS:
            if not db.query(Location).filter(Location.id == loc_data["id"]).first():
                db.add(Location(**loc_data, created_at=datetime.utcnow()))
        db.commit()
        print(f"  ✓ {len(DEMO_LOCATIONS)} locations ready\n")

        # ── Register demo persons ─────────────────────────────────────────
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        registered = []

        print(f"  Registering {len(DEMO_PERSONAS)} demo persons (SDT-0001 … SDT-0010)…")
        for idx, persona in enumerate(DEMO_PERSONAS):
            sdt_num = idx + 1
            code = f"SDT-{sdt_num:04d}"

            existing = db.query(Person).filter(Person.unique_code == code).first()
            if existing:
                print(f"    ↩  {code} — {persona['name']} (exists)")
                registered.append((code, persona))
                continue

            person = Person(
                id=str(uuid.uuid4()),
                unique_code=code,
                face_embedding=json.dumps(_make_embedding(idx)),
                created_at=today - timedelta(days=days),
                entry_zone=persona["zones"][0],
                location_id=persona["location"],
                person_type=persona["type"],
            )
            db.add(person)
            registered.append((code, persona))
            print(f"    ✓  {code} — {persona['name']} ({persona['type']}) @ {persona['location']}")

        db.commit()

        # ── Simulate movements ────────────────────────────────────────────
        print(f"\n  Simulating {days} day(s) of sightings…")
        total_sightings = 0

        for day_offset in range(days):
            base_date = today - timedelta(days=day_offset)

            for code, persona in registered:
                person = db.query(Person).filter(Person.unique_code == code).first()
                if not person:
                    continue

                for zone in persona["zones"]:
                    h_start = random.randint(7, 17)
                    ts = _random_ts(base_date, h_start, h_start + 2)
                    ts += timedelta(seconds=random.randint(-120, 120))

                    sighting = Sighting(
                        id=str(uuid.uuid4()),
                        person_id=person.id,
                        location_id=persona["location"],
                        zone_id=zone,
                        camera_id=f"CAM-{persona['location']}-{persona['zones'].index(zone)+1:02d}",
                        seen_at=ts,
                        confidence=round(random.uniform(0.72, 0.98), 3),
                    )
                    db.add(sighting)
                    total_sightings += 1

            # ── Simulate object detections ─────────────────────────────────
            obj_count = 0
            for loc in DEMO_LOCATIONS:
                # 5 bags
                for _ in range(5):
                    ts = _random_ts(base_date, 8, 20)
                    db.add(ObjectSighting(
                        id=str(uuid.uuid4()),
                        location_id=loc["id"],
                        zone_id=random.choice(["Entrance", "Main Hall", "Parking"]),
                        camera_id=f"CAM-{loc['id']}-01",
                        object_type=random.choice(OBJECT_TYPES["bags"]),
                        confidence=round(random.uniform(0.65, 0.97), 3),
                        detected_at=ts,
                        bbox_x=random.randint(50, 400), bbox_y=random.randint(50, 300),
                        bbox_w=random.randint(60, 200), bbox_h=random.randint(60, 200),
                    ))
                    obj_count += 1
                # 3 vehicles
                for _ in range(3):
                    ts = _random_ts(base_date, 6, 22)
                    db.add(ObjectSighting(
                        id=str(uuid.uuid4()),
                        location_id=loc["id"],
                        zone_id="Parking",
                        camera_id=f"CAM-{loc['id']}-02",
                        object_type=random.choice(OBJECT_TYPES["vehicles"]),
                        confidence=round(random.uniform(0.70, 0.99), 3),
                        detected_at=ts,
                        bbox_x=random.randint(10, 300), bbox_y=random.randint(50, 250),
                        bbox_w=random.randint(150, 400), bbox_h=random.randint(100, 300),
                    ))
                    obj_count += 1

            db.commit()
            print(f"    Day -{day_offset}: {total_sightings} person sightings, {obj_count} object detections")

        print(f"\n  ✓ SmartDetect demo setup complete!")
        print(f"    Locations : {len(DEMO_LOCATIONS)}")
        print(f"    Persons   : {len(DEMO_PERSONAS)}")
        print(f"    Days      : {days}")
        print(f"\n  Open http://localhost:5173 and search:")
        for code, persona in registered[:5]:
            print(f"    {code}  ({persona['name']}, {persona['type']})")
        print(f"    … and {max(0, len(registered)-5)} more (SDT-0001 → SDT-0010)")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartDetect Demo Setup")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--wipe", action="store_true")
    args = parser.parse_args()

    print("\n════════════════════════════════════")
    print("  SmartDetect — Demo Data Setup")
    print("════════════════════════════════════\n")
    setup_demo(days=args.days, wipe=args.wipe)
    print("\nALL CHANGES COMPLETE — SmartDetect is ready\n")


if __name__ == "__main__":
    main()
