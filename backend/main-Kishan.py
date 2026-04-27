"""
backend/main.py
────────────────
FastAPI application for SmartDetect — Universal Camera Detection System.

Endpoints
─────────
POST /auth/login                  — Obtain JWT
GET  /health                      — Public health check
POST /register                    — Register person [operator+]
GET  /person/{code}/trail         — Movement trail [operator+]
POST /sighting                    — Log person sighting [operator+]
GET  /locations                   — List all locations [operator+]
POST /locations                   — Create location [admin]
GET  /objects/recent              — Recent object detections [operator+]
GET  /objects/counts              — Today's bag/vehicle counts [operator+]
GET  /logs                        — Recent log lines [admin]
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db, init_db
from database.models import Location
from database.queries import (
    get_object_counts_today,
    get_person_trail,
    get_recent_objects,
    log_object_sighting,
    log_sighting,
)
from recognition.registration import register_person
from backend.auth import (
    LoginRequest, LoginResponse, TokenData,
    login as _auth_login,
    require_operator, require_admin,
)
from backend.logger import get_structured_logger, read_recent_logs

logger = get_structured_logger(__name__)

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SmartDetect API",
    description="Universal Camera Detection & Person Tracking System",
    version="2.0.0",
    docs_url="/docs", redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    try:
        init_db()
        logger.info("startup", message="SmartDetect database initialised.")
    except Exception as exc:
        logger.error("startup", message=f"DB init failed: {exc}")


# ─── Pydantic models ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    base64_image: str = Field(..., description="Base64-encoded face image")
    zone_id:      str = Field(..., description="Zone within the location (e.g. 'Entrance A')")
    location_id:  str = Field(..., description="Location ID (e.g. 'LOC-001')")
    person_type:  str = Field("unknown", description="visitor | staff | unknown")


class RegisterResponse(BaseModel):
    unique_code:          str
    person_type:          str
    location_name:        str
    is_new_registration:  bool
    message:              str


class SightingRequest(BaseModel):
    unique_code:  str
    location_id:  str
    zone_id:      str
    camera_id:    str
    confidence:   float = Field(..., ge=0.0, le=1.0)
    frame_path:   Optional[str] = None


class SightingResponse(BaseModel):
    success: bool
    message: str


class TrailItem(BaseModel):
    location_name:        str
    location_type:        str
    location_id:          Optional[str]
    zone_id:              Optional[str]
    camera_id:            str
    seen_at:              str
    confidence:           float
    frame_snapshot_path:  Optional[str] = None


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


class ObjectDetectionModel(BaseModel):
    id:          str
    object_type: str
    location_id: Optional[str]
    zone_id:     Optional[str]
    camera_id:   str
    confidence:  float
    detected_at: str
    bbox:        List[int]


class ObjectCountsModel(BaseModel):
    bags:     int
    vehicles: int


# ─── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=LoginResponse, tags=["Auth"])
def login(payload: LoginRequest) -> LoginResponse:
    """Authenticate and receive a JWT Bearer token."""
    result = _auth_login(payload)
    logger.info("auth.login", message=f"Login: user='{payload.username}' role='{result.role}'")
    return result


# ─── Public ───────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


# ─── Person endpoints ─────────────────────────────────────────────────────────

@app.post("/register", response_model=RegisterResponse, tags=["Person"])
def register(
    payload: RegisterRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """Register a person from a base64 face image. Requires operator+."""
    logger.info("register", message=f"Registration by '{token.username}' at loc='{payload.location_id}' zone='{payload.zone_id}'")
    try:
        img_bytes = base64.b64decode(payload.base64_image)
        frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Cannot decode image.")
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid image: {exc}") from exc

    try:
        result = register_person(
            frame,
            zone_id=payload.zone_id,
            location_id=payload.location_id,
            db=db,
            person_type=payload.person_type,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return RegisterResponse(**result)


@app.get("/person/{unique_code}/trail", response_model=List[TrailItem], tags=["Person"])
def person_trail(
    unique_code: str,
    db:          Session   = Depends(get_db),
    token:       TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    """Fetch chronological movement trail. Requires operator+."""
    logger.info("trail.fetch", message=f"Trail for '{unique_code}' by '{token.username}'")
    return get_person_trail(unique_code, db=db)


@app.post("/sighting", response_model=SightingResponse, tags=["Sighting"])
def record_sighting(
    payload: SightingRequest,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_operator),
) -> Dict[str, Any]:
    """Log a camera sighting. Requires operator+."""
    logger.info("sighting",
                message=f"Sighting: code={payload.unique_code} loc={payload.location_id} zone={payload.zone_id}")
    success = log_sighting(
        unique_code=payload.unique_code,
        location_id=payload.location_id,
        zone_id=payload.zone_id,
        camera_id=payload.camera_id,
        confidence=payload.confidence,
        db=db,
        frame_path=payload.frame_path,
    )
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Person '{payload.unique_code}' not found.")
    return SightingResponse(success=True, message="Sighting logged.")


# ─── Location endpoints ───────────────────────────────────────────────────────

@app.get("/locations", response_model=List[LocationModel], tags=["Locations"])
def list_locations(
    db:    Session   = Depends(get_db),
    token: TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    """Return all locations. Requires operator+."""
    locs = db.query(Location).order_by(Location.name).all()
    return [{"id": l.id, "name": l.name, "type": l.type, "address": l.address} for l in locs]


@app.post("/locations", response_model=LocationModel, tags=["Locations"], status_code=status.HTTP_201_CREATED)
def create_location(
    payload: LocationModel,
    db:      Session   = Depends(get_db),
    token:   TokenData = Depends(require_admin),
) -> Dict[str, Any]:
    """Create a new location record. Requires admin."""
    if db.query(Location).filter(Location.id == payload.id).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Location ID already exists.")
    from datetime import datetime  # noqa: PLC0415
    loc = Location(id=payload.id, name=payload.name, type=payload.type, address=payload.address,
                   created_at=datetime.utcnow())
    db.add(loc)
    db.commit()
    db.refresh(loc)
    logger.info("location.create", message=f"Created: id='{loc.id}' name='{loc.name}' by '{token.username}'")
    return {"id": loc.id, "name": loc.name, "type": loc.type, "address": loc.address}


# ─── Object detection endpoints (Change 2) ───────────────────────────────────

@app.get("/objects/recent", response_model=List[ObjectDetectionModel], tags=["Objects"])
def recent_objects(
    location_id: str = Query(..., description="Location ID to filter by"),
    limit:       int = Query(50, ge=1, le=200),
    db:          Session   = Depends(get_db),
    token:       TokenData = Depends(require_operator),
) -> List[Dict[str, Any]]:
    """Return last N object detections at a location. Requires operator+."""
    return get_recent_objects(location_id=location_id, limit=limit, db=db)


@app.get("/objects/counts", response_model=ObjectCountsModel, tags=["Objects"])
def object_counts(
    location_id: str = Query(..., description="Location ID"),
    db:          Session   = Depends(get_db),
    token:       TokenData = Depends(require_operator),
) -> Dict[str, int]:
    """Return today's bag and vehicle counts at a location. Requires operator+."""
    return get_object_counts_today(location_id=location_id, db=db)


# ─── Admin endpoints ──────────────────────────────────────────────────────────

@app.get("/logs", response_model=LogsResponse, tags=["System"])
def get_logs(
    lines:  int        = 100,
    token:  TokenData  = Depends(require_admin),
) -> Dict[str, Any]:
    """Fetch recent system log lines. Requires admin."""
    recent = read_recent_logs(n=lines)
    return {"lines": recent, "count": len(recent)}
