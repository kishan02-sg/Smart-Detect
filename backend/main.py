"""
backend/main.py
────────────────
FastAPI application for SmartDetect — Universal Camera Detection System.

Endpoints
─────────
POST /auth/login              — Obtain a JWT token
GET  /health                  — Health check (public)
POST /register                — Register a person [operator+]
GET  /person/{code}/trail     — Movement trail [operator+]
POST /sighting                — Log a sighting [operator+]
GET  /locations               — List locations [operator+]
POST /locations               — Create location [admin]
GET  /persons                 — List all persons [operator+]
GET  /persons/live            — Currently visible persons [operator+]
GET  /cameras                 — List cameras grouped by location [operator+]
POST /cameras                 — Create a camera [operator+]
DELETE /cameras/{id}          — Delete a camera [operator+]
POST /camera/start            — Start a camera stream [operator+]
POST /camera/stop             — Stop a specific camera stream [operator+]
POST /camera/stop-all         — Stop all camera streams [operator+]
GET  /camera/status           — Multi-camera status [operator+]
GET  /camera/stream/{id}      — MJPEG live stream [public]
GET  /camera/detections/recent— Recent detections [operator+]
GET  /logs                    — Recent log lines [admin]
POST /search/by-photo         — Photo search [public]
GET  /analytics/count/live    — Live count [public]
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db, init_db
from database.models import Location, Person, Camera as CameraModel
from database.queries import (
    get_person_trail,
    get_recent_detections,
    log_sighting,
)
from recognition.registration import register_person

from backend.auth import (
    LoginRequest, LoginResponse, TokenData,
    login as _auth_login,
    require_operator, require_admin,
)
from backend.logger import get_structured_logger, read_recent_logs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = get_structured_logger(__name__)

# ─── App Factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="SmartDetect API",
    description="Universal Camera Detection & Person Tracking System.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Global camera state ─────────────────────────────────────────────────────
# camera_id → LiveStream instance  (max 4 simultaneous)
active_streams: Dict[str, Any] = {}
MAX_STREAMS = 4


@app.on_event("startup")
def startup_event() -> None:
    try:
        init_db()
        logger.info("startup", message="Database initialised successfully.")
    except Exception as exc:
        logger.error("startup", message=f"Database initialisation failed: {exc}")
        return

    # ── Auto-start default webcam ────────────────────────────────────────────
    import os
    if os.getenv("SMARTDETECT_NO_AUTOSTART", "0") != "1":
        try:
            from cameras.live_stream import LiveStream
            from database.db import SessionLocal
            from datetime import datetime

            db = SessionLocal()
            try:
                # Ensure a default camera record exists
                cam = db.query(CameraModel).filter(CameraModel.id == "CAM-001").first()
                if not cam:
                    cam = CameraModel(
                        id="CAM-001",
                        location_id="LOC-001",
                        zone_id="main",
                        label="Default Webcam",
                        source="0",
                        is_active=False,
                        created_at=datetime.utcnow(),
                    )
                    db.add(cam)
                    db.commit()
                    logger.info("startup", message="Created default camera CAM-001")

                # Start LiveStream on webcam 0
                stream = LiveStream(
                    source=0,
                    location_id=cam.location_id,
                    zone_id=cam.zone_id,
                    camera_id="CAM-001",
                )
                stream.start()
                active_streams["CAM-001"] = stream

                # Mark as active in DB
                cam.is_active = True
                db.commit()
                logger.info("startup", message="Auto-started webcam CAM-001 (source=0)")
            finally:
                db.close()
        except Exception as exc:
            logger.warning("startup", message=f"Webcam auto-start skipped: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    base64_image: str  = Field(..., description="Base64-encoded face image")
    zone_id:      str  = Field(..., description="Zone where registration occurs")
    location_id:  str  = Field(..., description="Location ID (e.g. LOC-001)")
    person_type:  str  = Field("unknown", description="visitor | staff | unknown")


class RegisterResponse(BaseModel):
    unique_code:         str
    person_type:         str
    location_name:       str
    is_new_registration: bool
    message:             str


class SightingRequest(BaseModel):
    unique_code:         str
    location_id:         str
    zone_id:             str
    camera_id:           str
    confidence:          float = Field(..., ge=0.0, le=1.0)
    frame_snapshot_path: Optional[str] = None


class SightingResponse(BaseModel):
    success: bool
    message: str


class TrailItem(BaseModel):
    location_name:       str
    location_type:       str
    location_id:         Optional[str]
    zone_id:             Optional[str]
    camera_id:           str
    seen_at:             str
    confidence:          float
    frame_snapshot_path: Optional[str] = None


class LocationModel(BaseModel):
    id:      str
    name:    str
    type:    str = "other"
    address: Optional[str] = None


class HealthResponse(BaseModel):
    status: str


class LogsResponse(BaseModel):
    lines: List[str]
    count: int


class CameraStartRequest(BaseModel):
    camera_id: str = Field(..., description="Camera ID to start (e.g. CAM-001)")


class CameraStopRequest(BaseModel):
    camera_id: str = "CAM-001"


class CameraCreateRequest(BaseModel):
    location_id: str  = Field(..., description="Location ID")
    zone_id:     str  = Field("main", description="Zone label")
    label:       str  = Field("Camera", description="Human-readable camera name")
    source:      str  = Field("0", description="Webcam index or RTSP URL")


# ─────────────────────────────────────────────────────────────────────────────
# Auth Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=LoginResponse, tags=["Auth"])
def login(payload: LoginRequest) -> LoginResponse:
    result = _auth_login(payload)
    logger.info("auth.login", message=f"Login by user='{payload.username}' role='{result.role}'")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Person Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/register", response_model=RegisterResponse, tags=["Person"])
def register(
    payload: RegisterRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    logger.info("register", message=f"Registration by '{token.username}' loc='{payload.location_id}'")
    try:
        img_bytes = base64.b64decode(payload.base64_image)
        frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Cannot decode image.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid image: {exc}") from exc

    try:
        result = register_person(
            frame,
            zone_id=payload.zone_id,
            location_id=payload.location_id,
            db=db,
            person_type=payload.person_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return RegisterResponse(**result)


@app.get("/person/{unique_code}/trail", response_model=List[TrailItem], tags=["Person"])
def person_trail(
    unique_code: str,
    db:          Session   = Depends(get_db),
    token:       TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    return get_person_trail(unique_code, db=db)


@app.post("/sighting", response_model=SightingResponse, tags=["Sighting"])
def record_sighting(
    payload: SightingRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    success = log_sighting(
        unique_code=payload.unique_code,
        location_id=payload.location_id,
        zone_id=payload.zone_id,
        camera_id=payload.camera_id,
        confidence=payload.confidence,
        db=db,
        frame_path=payload.frame_snapshot_path,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Person '{payload.unique_code}' not found.")
    return SightingResponse(success=True, message="Sighting logged.")


@app.get("/persons", tags=["Person"])
def list_persons(
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    try:
        persons = db.query(Person).order_by(Person.created_at.desc()).all()
        return [
            {
                "unique_code":     p.unique_code,
                "person_type":     p.person_type,
                "location_id":     p.location_id,
                "total_sightings": getattr(p, "total_sightings", None) or 0,
                "first_seen_at":   p.first_seen_at.isoformat() if getattr(p, "first_seen_at", None) else None,
                "last_seen_at":    p.last_seen_at.isoformat()  if getattr(p, "last_seen_at",  None) else None,
                "created_at":      p.created_at.isoformat() if p.created_at else None,
            }
            for p in persons
        ]
    except Exception as exc:
        logger.error("persons.list", message=f"Error: {exc}")
        return []


@app.get("/persons/live", tags=["Person"])
def live_persons() -> List[Dict[str, Any]]:
    all_live: List[Dict] = []
    for stream in active_streams.values():
        try:
            for entry in stream.get_live_persons():
                recent = {d["unique_code"]: d for d in stream.get_recent_detections(100)}
                det = recent.get(entry["unique_code"], {})
                all_live.append({
                    "unique_code":     entry["unique_code"],
                    "method":          det.get("method", "unknown"),
                    "confidence":      det.get("confidence", 0.0),
                    "color_hex":       det.get("color_hex"),
                    "zone":            stream.zone_id,
                    "camera_id":       stream.camera_id,
                    "total_sightings": 0,
                })
        except Exception:
            pass
    return all_live


# ─────────────────────────────────────────────────────────────────────────────
# Location Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/locations", response_model=List[LocationModel], tags=["Locations"])
def list_locations(
    db:    Session   = Depends(get_db),
    token: TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    locs = db.query(Location).order_by(Location.name).all()
    return [{"id": l.id, "name": l.name, "type": l.type, "address": l.address} for l in locs]


@app.post("/locations", response_model=LocationModel, tags=["Locations"],
          status_code=status.HTTP_201_CREATED)
def create_location(
    payload: LocationModel,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_admin),
) -> Dict[str, Any]:
    if db.query(Location).filter(Location.id == payload.id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Location ID already exists.")
    from datetime import datetime
    loc = Location(id=payload.id, name=payload.name, type=payload.type,
                   address=payload.address, created_at=datetime.utcnow())
    db.add(loc); db.commit(); db.refresh(loc)
    return {"id": loc.id, "name": loc.name, "type": loc.type, "address": loc.address}


# ─────────────────────────────────────────────────────────────────────────────
# Camera CRUD Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/cameras", tags=["Camera"])
def list_cameras(
    db:    Session   = Depends(get_db),
    token: TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    """Return all cameras grouped by location."""
    locations = db.query(Location).order_by(Location.name).all()
    result = []
    for loc in locations:
        cams = db.query(CameraModel).filter(CameraModel.location_id == loc.id).all()
        # merge with live is_active from active_streams
        cam_list = []
        for c in cams:
            is_live = c.id in active_streams
            cam_list.append({
                "id":        c.id,
                "zone_id":   c.zone_id,
                "label":     c.label,
                "source":    c.source,
                "is_active": is_live or c.is_active,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
        result.append({
            "location_id":   loc.id,
            "location_name": loc.name,
            "location_type": loc.type,
            "cameras":       cam_list,
        })
    return result


@app.post("/cameras", tags=["Camera"], status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreateRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """Create a new camera record."""
    loc = db.query(Location).filter(Location.id == payload.location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail=f"Location '{payload.location_id}' not found.")

    # Auto-generate camera ID
    existing = db.query(CameraModel).count()
    cam_id = f"CAM-{existing + 1:03d}"
    while db.query(CameraModel).filter(CameraModel.id == cam_id).first():
        existing += 1
        cam_id = f"CAM-{existing + 1:03d}"

    from datetime import datetime
    cam = CameraModel(
        id=cam_id,
        location_id=payload.location_id,
        zone_id=payload.zone_id,
        label=payload.label,
        source=payload.source,
        is_active=False,
        created_at=datetime.utcnow(),
    )
    db.add(cam); db.commit(); db.refresh(cam)
    logger.info("camera.create", message=f"Created {cam_id} at {payload.location_id}/{payload.zone_id}")
    return {
        "id":          cam.id,
        "location_id": cam.location_id,
        "zone_id":     cam.zone_id,
        "label":       cam.label,
        "source":      cam.source,
        "is_active":   cam.is_active,
    }


@app.delete("/cameras/{camera_id}", tags=["Camera"])
def delete_camera(
    camera_id: str,
    db:        Session   = Depends(get_db),
    token:     TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    cam = db.query(CameraModel).filter(CameraModel.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found.")
    # Stop stream if running
    if camera_id in active_streams:
        try:
            active_streams[camera_id].stop()
        except Exception:
            pass
        active_streams.pop(camera_id, None)
    db.delete(cam); db.commit()
    logger.info("camera.delete", message=f"Deleted {camera_id}")
    return {"status": "deleted", "camera_id": camera_id}


# ─────────────────────────────────────────────────────────────────────────────
# Camera Stream Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/camera/start", tags=["Camera"])
def camera_start(
    payload: CameraStartRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """
    Start a live camera stream.
    Looks up the Camera record by camera_id to get source, location_id, zone_id.
    """
    from cameras.live_stream import LiveStream

    camera_id = payload.camera_id

    # Already running?
    if camera_id in active_streams:
        return {"status": "already_running", "camera_id": camera_id}

    if len(active_streams) >= MAX_STREAMS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Max {MAX_STREAMS} simultaneous streams already active.",
        )

    # Look up Camera record
    cam = db.query(CameraModel).filter(CameraModel.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{camera_id}' not found. Create it first via POST /cameras.",
        )

    # Resolve source type
    source: Union[int, str] = cam.source
    try:
        source = int(source)
    except (ValueError, TypeError):
        pass  # keep as string (RTSP URL)

    try:
        stream = LiveStream(
            source=source,
            location_id=cam.location_id,
            zone_id=cam.zone_id,
            camera_id=camera_id,
        )
        stream.start()
        active_streams[camera_id] = stream

        # Mark active in DB
        cam.is_active = True
        db.commit()

        logger.info("camera.start", message=f"Stream started: {camera_id} source={source}")
        return {
            "status":      "started",
            "camera_id":   camera_id,
            "location_id": cam.location_id,
            "zone_id":     cam.zone_id,
            "label":       cam.label,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@app.post("/camera/stop", tags=["Camera"])
def camera_stop(
    payload: CameraStopRequest,
    db:      Session = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """Stop a specific camera stream and mark is_active=False in DB."""
    camera_id = payload.camera_id
    if camera_id in active_streams:
        try:
            active_streams[camera_id].stop()
        except Exception:
            pass
        active_streams.pop(camera_id, None)

    # Update DB
    cam = db.query(CameraModel).filter(CameraModel.id == camera_id).first()
    if cam:
        cam.is_active = False
        db.commit()

    logger.info("camera.stop", message=f"Stream stopped: {camera_id}")
    return {"status": "stopped", "camera_id": camera_id}


@app.post("/camera/stop-all", tags=["Camera"])
def camera_stop_all(
    db:    Session   = Depends(get_db),
    token: TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """Stop ALL running camera streams."""
    count = 0
    for cid in list(active_streams.keys()):
        try:
            active_streams[cid].stop()
        except Exception:
            pass
        active_streams.pop(cid, None)
        count += 1

    # Update all is_active in DB
    db.query(CameraModel).update({"is_active": False})
    db.commit()

    logger.info("camera.stop_all", message=f"Stopped {count} stream(s).")
    return {"status": "all_stopped", "count": count}


@app.get("/camera/status", tags=["Camera"])
def camera_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return status of all cameras (active and inactive)."""
    all_cams = db.query(CameraModel).all()
    total = len(all_cams)

    cameras_list = []
    for c in all_cams:
        stream     = active_streams.get(c.id)
        is_running = stream is not None
        fps        = 0.0
        persons    = 0
        if stream:
            try:
                st      = stream.get_status()
                fps     = st.get("fps", 0.0)
                persons = st.get("persons_detected_today", 0)
            except Exception:
                pass

        loc = db.query(Location).filter(Location.id == c.location_id).first()
        cameras_list.append({
            "camera_id":             c.id,
            "label":                 c.label,
            "location_id":           c.location_id,
            "location_name":         loc.name if loc else c.location_id,
            "zone_id":               c.zone_id,
            "source":                c.source,
            "is_active":             is_running,
            "fps":                   fps,
            "persons_detected_today": persons,
        })

    active_count = len(active_streams)
    return {
        "total_cameras":  total,
        "active_cameras": active_count,
        "connected":      active_count > 0,
        "cameras":        cameras_list,
    }


@app.get("/camera/detections/recent", tags=["Camera"])
def camera_detections_recent(
    limit: int = Query(20, ge=1, le=100),
    db:    Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Recent detections — in-memory first, then DB fallback."""
    if active_streams:
        stream = next(iter(active_streams.values()))
        try:
            recents = stream.get_recent_detections(limit)
            if recents:
                return recents
        except Exception:
            pass

    try:
        from database.models import Sighting
        rows = (
            db.query(Sighting)
            .order_by(Sighting.seen_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "unique_code": r.unique_code or getattr(r, "person_id", ""),
                "zone_id":     r.zone_id,
                "camera_id":   r.camera_id,
                "confidence":  r.confidence,
                "seen_at":     r.seen_at.isoformat() if r.seen_at else None,
                "detected_at": r.seen_at.isoformat() if r.seen_at else None,
                "method":      "face",
                "color_hex":   None,
            }
            for r in rows
        ]
    except Exception:
        return []


@app.get("/camera/stream/{camera_id}", tags=["Camera"])
def camera_stream(camera_id: str) -> StreamingResponse:
    """Stream MJPEG video from an active camera. Use as <img src=...>."""
    import time

    if camera_id not in active_streams and active_streams:
        camera_id = next(iter(active_streams))

    def generate():
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "No Signal", (220, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 2)
        _, blank_jpg   = cv2.imencode(".jpg", blank)
        blank_bytes    = blank_jpg.tobytes()

        while True:
            stream = active_streams.get(camera_id)
            frame_bytes = blank_bytes
            if stream:
                try:
                    frame_bytes = stream.get_mjpeg_frame()
                except Exception:
                    pass

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )
            time.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin-only Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/logs", response_model=LogsResponse, tags=["System"])
def get_logs(
    lines: int       = 100,
    token: TokenData = Depends(require_admin),
) -> Dict[str, Any]:
    recent = read_recent_logs(n=lines)
    return {"lines": recent, "count": len(recent)}


# ─────────────────────────────────────────────────────────────────────────────
# Photo Search Route
# ─────────────────────────────────────────────────────────────────────────────

class PhotoSearchRequest(BaseModel):
    base64_image: str  = Field(..., description="Base64-encoded photo")
    scope:        str  = Field("live_and_history", description="live_and_history | live_only")
    check_only:   bool = Field(False, description="If true, only verify face detected")


@app.post("/search/by-photo", tags=["Search"])
def search_by_photo(
    payload: PhotoSearchRequest,
    db:      Session = Depends(get_db),
) -> Dict[str, Any]:
    import base64 as _b64
    from datetime import datetime, timezone, timedelta
    from recognition.face_recognizer import FaceRecognizer
    from database.queries import find_person_by_embedding, get_person_trail

    try:
        img_bytes = _b64.b64decode(payload.base64_image)
        frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Cannot decode image")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    recognizer = FaceRecognizer()
    recognizer.load_model()
    embeddings   = recognizer.extract_embedding(frame)
    face_detected = bool(embeddings)

    if payload.check_only:
        return {"face_detected": face_detected}

    if not face_detected:
        return {
            "matched": False,
            "face_detected": False,
            "cameras_searched": len(active_streams) or 1,
            "message": "No face found in photo.",
        }

    query_emb = embeddings[0]
    match = find_person_by_embedding(query_emb, db=db, threshold=0.65)
    cameras_searched = max(len(active_streams), 1)

    if not match:
        return {
            "matched": False, "face_detected": True,
            "cameras_searched": cameras_searched,
            "message": "Person not found in any camera",
        }

    unique_code = match["unique_code"]
    confidence  = match["similarity"]
    trail = get_person_trail(unique_code, db=db)

    now_ts = datetime.now(timezone.utc)
    if payload.scope == "live_only":
        cutoff_iso = (now_ts.replace(tzinfo=None) - timedelta(minutes=5)).isoformat()
        trail = [t for t in trail if t.get("seen_at", "") >= cutoff_iso]

    live_matches    = []
    history_matches = []
    live_codes      = set()

    for cam_id, stream in active_streams.items():
        recent = {d["unique_code"]: d for d in stream.get_recent_detections(100)}
        if unique_code in recent:
            det = recent[unique_code]
            live_matches.append({
                "camera_id":   cam_id,
                "camera_name": f"Camera {cam_id}",
                "zone_id":     stream.zone_id,
                "confidence":  det.get("confidence", confidence),
                "detected_at": det.get("detected_at"),
            })
            live_codes.add(cam_id)

    for t in trail[-20:]:
        cam_id = t.get("camera_id", "")
        history_matches.append({
            "camera_id":   cam_id or t.get("location_id", "—"),
            "camera_name": f"Camera {cam_id}" if cam_id else t.get("location_name", "—"),
            "zone_id":     t.get("zone_id"),
            "confidence":  t.get("confidence", confidence),
            "detected_at": t.get("seen_at"),
        })

    return {
        "matched":          True,
        "face_detected":    True,
        "unique_code":      unique_code,
        "confidence":       round(confidence, 4),
        "cameras_searched": cameras_searched,
        "is_live_now":      len(live_matches) > 0,
        "live_matches":     live_matches,
        "history_matches":  history_matches[:10],
        "timeline":         trail[-20:],
        "first_seen":       trail[0]["seen_at"] if trail else None,
        "last_seen":        trail[-1]["seen_at"] if trail else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/analytics/count/live", tags=["Analytics"])
def analytics_live_count(db: Session = Depends(get_db)) -> Dict[str, Any]:
    from database.models import Sighting
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    try:
        count = db.query(Sighting).filter(Sighting.seen_at >= today).count()
    except Exception:
        count = 0
    return {"total": count}
