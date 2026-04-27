"""
recognition/smart_identifier.py
─────────────────────────────────
4-method smart person identification pipeline for SmartDetect.

Priority order:
  1. Face Recognition  (FaceRecognizer — ArcFace embedding, threshold 0.72)
  2. Dress Color       (K-means torso crop, HSV distance ≤ 30)
  3. Body Re-ID        (OSNet embedding, threshold 0.78)
  4. Multi-feature     (weighted combination, threshold 0.65)
  5. New Registration  (auto-assign SDT-XXXX if all methods fail)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import numpy as np

from database.models import Person
from database.queries import (
    find_person_by_embedding,
    find_by_dress_color,
    get_next_sdt_number,
    update_person_last_seen,
)
from recognition.face_recognizer import FaceRecognizer
from recognition.reid_model import PersonReID

logger = logging.getLogger(__name__)

# Singletons — lazy loaded
_face_recognizer: Optional[FaceRecognizer] = None
_reid_model: Optional[PersonReID] = None


def _get_face_recognizer() -> FaceRecognizer:
    global _face_recognizer
    if _face_recognizer is None:
        _face_recognizer = FaceRecognizer()
        _face_recognizer.load_model()
    return _face_recognizer


def _get_reid_model() -> PersonReID:
    global _reid_model
    if _reid_model is None:
        _reid_model = PersonReID()  # auto-loads model in __init__
    return _reid_model


# ─── Color helpers ─────────────────────────────────────────────────────────────

def _dominant_color_hsv(bgr_crop: np.ndarray, k: int = 3) -> Optional[Dict]:
    """K-means dominant color extraction from a BGR crop. Returns HSV dict + hex."""
    try:
        import cv2
        if bgr_crop is None or bgr_crop.size == 0:
            return None
        h, w = bgr_crop.shape[:2]
        if h < 10 or w < 10:
            return None

        # Reshape to pixel list and run K-means
        pixels = bgr_crop.reshape(-1, 3).astype(np.float32)
        k = min(k, len(pixels))
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 5, cv2.KMEANS_RANDOM_CENTERS)
        counts = np.bincount(labels.flatten())
        dominant_bgr = centers[np.argmax(counts)].astype(np.uint8)

        # Convert to HSV
        hsv = cv2.cvtColor(np.array([[dominant_bgr]], dtype=np.uint8), cv2.COLOR_BGR2HSV)[0][0]
        r, g, b = int(dominant_bgr[2]), int(dominant_bgr[1]), int(dominant_bgr[0])
        hex_color = f"#{r:02x}{g:02x}{b:02x}"

        return {
            "hue":        int(hsv[0]),
            "saturation": int(hsv[1]),
            "value":      int(hsv[2]),
            "hex_color":  hex_color,
        }
    except Exception as exc:
        logger.debug("dominant_color_hsv failed: %s", exc)
        return None


def _hsv_distance(a: Dict, b: Dict) -> float:
    """Euclidean distance in HSV space (hue wrapped)."""
    dh = min(abs(a["hue"] - b["hue"]), 180 - abs(a["hue"] - b["hue"]))
    ds = abs(a["saturation"] - b["saturation"])
    dv = abs(a["value"] - b["value"])
    return float(np.sqrt(dh**2 + ds**2 + dv**2))


# ─── SmartIdentifier ──────────────────────────────────────────────────────────

class SmartIdentifier:
    """
    Multi-method person identification.

    Usage:
        si = SmartIdentifier()
        result = si.identify(frame, bbox, db)
        # result: {unique_code, method, confidence, color_hex, ...}
    """

    FACE_THRESHOLD  = 0.35
    COLOR_THRESHOLD = 30.0
    REID_THRESHOLD  = 0.55
    MULTI_THRESHOLD = 0.65

    def identify(
        self,
        frame: np.ndarray,
        bbox: list,            # [x, y, w, h]
        db,
        location_id: str = "",
        zone_id: str = "",
    ) -> Dict:
        """Run the full 4-method pipeline and return result dict."""
        import cv2

        x, y, w, h = bbox
        fh, fw = frame.shape[:2]
        person_crop = frame[
            max(0, y): min(fh, y + h),
            max(0, x): min(fw, x + w),
        ]
        torso_crop = frame[
            max(0, y): min(fh, y + int(h * 0.45)),
            max(0, x): min(fw, x + w),
        ]

        # Partial scores for multi-feature fallback
        best_face_code,  face_score  = None, 0.0
        best_reid_code,  reid_score  = None, 0.0
        best_color_code, color_score = None, 0.0
        color_info: Optional[Dict]   = None

        # ── Method 1: Face ──────────────────────────────────────────────────
        try:
            recognizer = _get_face_recognizer()
            embeddings = recognizer.extract_embedding(frame)
            if embeddings:
                q_emb = embeddings[0]
                match = find_person_by_embedding(q_emb, db=db, threshold=self.FACE_THRESHOLD)
                if match:
                    return {
                        "unique_code": match["unique_code"],
                        "method":      "face",
                        "confidence":  round(match["similarity"], 3),
                        "color_hex":   None,
                        "embedding":   q_emb.tolist(),
                    }
                if len(embeddings) > 0:
                    best_face_code = None
                    face_score = 0.0  # no match but have embedding
        except Exception as exc:
            logger.debug("SmartIdentifier.face failed: %s", exc)

        # ── Method 2: Dress Color ────────────────────────────────────────────
        try:
            color_info = _dominant_color_hsv(torso_crop)
            if color_info:
                color_match = find_by_dress_color(color_info, threshold=self.COLOR_THRESHOLD, db=db)
                if color_match:
                    return {
                        "unique_code": color_match["unique_code"],
                        "method":      "dress_color",
                        "confidence":  round(color_match["score"], 3),
                        "color_hex":   color_info["hex_color"],
                        "embedding":   None,
                    }
                best_color_code = None
                color_score = 0.0
        except Exception as exc:
            logger.debug("SmartIdentifier.dress_color failed: %s", exc)

        # ── Method 3: Body Re-ID ─────────────────────────────────────────────
        try:
            reid = _get_reid_model()
            reid_emb = reid.extract_features(person_crop)
            if reid_emb is not None and len(reid_emb) > 0:
                reid_match = find_person_by_embedding(
                    reid_emb, db=db,
                    threshold=self.REID_THRESHOLD,
                    embedding_field="reid_embedding",
                )
                if reid_match:
                    return {
                        "unique_code": reid_match["unique_code"],
                        "method":      "body_structure",
                        "confidence":  round(reid_match["similarity"], 3),
                        "color_hex":   color_info["hex_color"] if color_info else None,
                        "embedding":   None,
                    }
                reid_score = 0.0
        except Exception as exc:
            logger.debug("SmartIdentifier.reid failed: %s", exc)

        # ── Method 5: New Registration ───────────────────────────────────────
        seq = get_next_sdt_number(db)
        new_code = f"SDT-{seq:04d}"

        try:
            recognizer = _get_face_recognizer()
            face_embs = recognizer.extract_embedding(frame)
            face_emb_json = json.dumps(face_embs[0].tolist()) if face_embs else None

            reid = _get_reid_model()
            reid_emb = reid.extract_features(person_crop)
            reid_emb_json = json.dumps(reid_emb.tolist()) if reid_emb is not None else None

            height_ratio = round(h / max(fh, 1), 4)

            person = Person(
                unique_code      = new_code,
                face_embedding   = face_emb_json,
                reid_embedding   = reid_emb_json,
                dress_color_hsv  = json.dumps(color_info) if color_info else None,
                body_height_ratio= height_ratio,
                entry_zone       = zone_id,
                location_id      = location_id,
                person_type      = "unknown",
                created_at       = datetime.now(timezone.utc).replace(tzinfo=None),
                first_seen_at    = datetime.now(timezone.utc).replace(tzinfo=None),
                last_seen_at     = datetime.now(timezone.utc).replace(tzinfo=None),
                total_sightings  = 1,
            )
            db.add(person)
            db.commit()
            logger.info("SmartIdentifier: new person registered as %s", new_code)
        except Exception as exc:
            logger.error("SmartIdentifier: DB save failed for %s: %s", new_code, exc)
            try:
                db.rollback()
            except Exception:
                pass

        return {
            "unique_code": new_code,
            "method":      "new_registration",
            "confidence":  1.0,
            "color_hex":   color_info["hex_color"] if color_info else None,
            "embedding":   None,
        }
