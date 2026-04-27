"""
cameras/live_stream.py
───────────────────────
Live camera capture + real-time MJPEG streaming for SmartDetect.

Architecture:
  LiveStream.start()  →  background thread reads frames at ~10 fps
    → ObjectDetector (YOLO persons + bags/objects)
    → FaceRecognizer per person crop (face bbox, landmarks, gender)
    → SmartIdentifier per person (SDT code assignment)
    → Dress colour, height estimate, bag linking
    → Annotated frame with coloured boxes + HUD overlay
    → Encode as JPEG and store in self._latest_frame
  GET /camera/stream/{id} yields MJPEG from self._latest_frame
"""

from __future__ import annotations

import io
import json
import logging
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from database.db import SessionLocal
from database.queries import log_sighting

logger = logging.getLogger(__name__)


# ─── BGR Colours ──────────────────────────────────────────────────────────────
_GREEN   = (80,  200, 80)    # face-identified person
_BLUE    = (200, 130, 50)    # body/Re-ID identified person
_WHITE   = (230, 230, 230)   # new registration
_CYAN    = (255, 220, 0)     # face bounding box
_ORANGE  = (0,   160, 255)   # bag / carried object
_YELLOW  = (0,   230, 255)   # bottle
_RED     = (60,  60,  220)   # legend border
_BLACK   = (15,  15,  15)
_GREY    = (160, 160, 160)
_FONT    = cv2.FONT_HERSHEY_SIMPLEX

# Method → box colour mapping
_BOX_COLORS = {
    "face":             _GREEN,
    "dress_color":      _BLUE,
    "body_structure":   _BLUE,
    "multi_feature":    _BLUE,
    "new_registration": _WHITE,
}

# Blank 480×640 placeholder frame (shown while camera is starting)
_BLANK = np.zeros((480, 640, 3), dtype=np.uint8)

# Object classes we track
_BAG_CLASSES    = {"backpack", "handbag", "suitcase"}
_BOTTLE_CLASSES = {"bottle"}
_OBJECT_LABELS  = {
    "backpack": "Bag",
    "handbag":  "Handbag",
    "suitcase": "Suitcase",
    "bottle":   "Bottle",
}


class LiveStream:
    """
    Manages one camera source.  Starts a background thread that:
      1. Reads frames from cv2.VideoCapture
      2. Runs YOLO person + object detection
      3. Runs FaceRecognizer on each person crop (face bbox + landmarks + gender)
      4. Runs SmartIdentifier on each person (SDT code registration)
      5. Extracts dress colour, height estimate, bag linking
      6. Annotates the frame with coloured boxes + HUD overlay
      7. Stores the latest JPEG in memory for MJPEG streaming

    Parameters
    ----------
    source        : int (webcam index) or str (RTSP / file path)
    location_id   : SmartDetect location ID
    zone_id       : zone label (e.g. "entrance")
    camera_id     : human-readable camera identifier
    target_fps    : capture/process rate (default 10)
    """

    def __init__(
        self,
        source,
        location_id: str = "LOC-001",
        zone_id:     str  = "main",
        camera_id:   str  = "CAM-001",
        target_fps:  int  = 10,
    ):
        self.source      = source
        self._source     = source  # kept for status API
        self.location_id = location_id
        self.zone_id     = zone_id
        self.camera_id   = camera_id
        self._interval   = 1.0 / max(target_fps, 1)

        self._cap: Optional[cv2.VideoCapture] = None
        self._thread:  Optional[threading.Thread] = None
        self._running  = False
        self._lock     = threading.Lock()
        self._latest_frame: bytes = self._encode_frame(_BLANK)

        # ── Stats ──────────────────────────────────────────────────────────
        self.persons_today: int = 0
        self.active_tracks: Dict[int, str] = {}   # track_id → SDT code
        self._detections: deque = deque(maxlen=100)

        # ── FPS counter (frame count + time window) ────────────────────
        self._fps_count     = 0
        self._fps_last_time = time.time()
        self._fps_value     = 0.0
        self._total_frames  = 0   # never resets — used for frame skipping

        # ── Frame-level counters (reset each frame) ────────────────────
        self._frame_persons = 0
        self._frame_faces   = 0
        self._frame_bags    = 0

        # ── Cached face results for frame skipping ─────────────────────
        self._cached_faces: Dict = {}

        # ── Face bbox smoothing (temporal averaging) ──────────────────
        # person_key → list of last 5 face bboxes for smooth drawing
        self._face_bbox_history: Dict[tuple, list] = {}
        self._face_kps_history: Dict[tuple, list] = {}
        self._last_all_faces: list = []  # cached full-frame face results

        # ── Dedup cache  ───────────────────────────────────────────────────
        # unique_code → last log timestamp; skip re-log within 30s
        self._seen_cache: Dict[str, float] = {}

        # ── ML components (lazy-loaded to avoid startup crash) ─────────────
        self._detector   = None
        self._identifier = None
        self._face_rec   = None

        try:
            from recognition.object_detector import ObjectDetector
            from recognition.smart_identifier import SmartIdentifier
            from recognition.face_recognizer import FaceRecognizer
            self._detector   = ObjectDetector()
            self._identifier = SmartIdentifier()
            self._face_rec   = FaceRecognizer()
        except Exception as exc:
            logger.warning("ML models unavailable (%s) — stream will show raw frames", exc)


    # ── Public interface ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the camera and start the processing thread."""
        if isinstance(self.source, int):
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera source: {self.source}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        if self._detector is not None:
            try:
                self._detector.load_model()
            except Exception as exc:
                logger.warning("YOLO load failed (%s) — streaming raw frames", exc)
                self._detector = None
        if self._face_rec is not None:
            try:
                self._face_rec.load_model()
            except Exception as exc:
                logger.warning("FaceRecognizer load failed (%s)", exc)
                self._face_rec = None
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"LiveStream-{self.camera_id}")
        self._thread.start()
        logger.info("LiveStream %s started — source=%s", self.camera_id, self.source)

    def stop(self) -> None:
        """Stop the processing thread and release the camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap:
            self._cap.release()
        logger.info("LiveStream %s stopped", self.camera_id)

    def is_connected(self) -> bool:
        return self._running and self._cap is not None and self._cap.isOpened()

    def get_status(self) -> Dict:
        return {
            "connected":             self.is_connected(),
            "camera_id":             self.camera_id,
            "fps":                   round(self._fps_value, 1),
            "persons_detected_today": self.persons_today,
            "active_tracks":         len(self.active_tracks),
        }

    def get_mjpeg_frame(self) -> bytes:
        with self._lock:
            return self._latest_frame

    def get_recent_detections(self, limit: int = 20) -> list:
        return list(self._detections)[-limit:]

    def get_live_persons(self) -> list:
        """Return persons seen in the last 30 seconds."""
        now = time.time()
        return [
            {"unique_code": code, "last_seen": ts}
            for code, ts in self._seen_cache.items()
            if now - ts < 30
        ]

    # ── Background loop ──────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            t0 = time.time()
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("LiveStream %s: frame read failed — retrying", self.camera_id)
                time.sleep(0.5)
                continue

            annotated = self._process_frame(frame)
            jpg       = self._encode_frame(annotated)
            with self._lock:
                self._latest_frame = jpg

            # ── FPS calculation ───────────────────────────────────────────
            self._fps_count += 1
            now = time.time()
            elapsed_since_reset = now - self._fps_last_time
            if elapsed_since_reset >= 1.0:
                self._fps_value = self._fps_count / elapsed_since_reset
                self._fps_count = 0
                self._fps_last_time = now

            elapsed = now - t0
            sleep_for = max(0, self._interval - elapsed)
            time.sleep(sleep_for)

    # ─────────────────────────────────────────────────────────────────────────
    # Frame Processing Pipeline
    # ─────────────────────────────────────────────────────────────────────────

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Full per-frame pipeline: detect → identify → annotate → HUD."""
        annotated = frame.copy()
        fh, fw = frame.shape[:2]

        # Reset per-frame counters
        self._frame_persons = 0
        self._frame_faces   = 0
        self._frame_bags    = 0
        self._total_frames += 1
        RUN_FACE_EVERY = 5  # InsightFace every 5th frame for performance

        # ML models not available — stream raw frames with basic HUD
        if not self._detector:
            self._draw_hud(annotated, fh, fw)
            return annotated

        # ── 1. YOLO detection on full frame ───────────────────────────────
        try:
            detections = self._detector.detect(frame)
        except Exception as exc:
            logger.debug("LiveStream detect error: %s", exc)
            self._draw_hud(annotated, fh, fw)
            return annotated

        person_dets = [d for d in detections if d["label"] == "person"]
        bag_dets    = [d for d in detections if d["label"] in _BAG_CLASSES]
        bottle_dets = [d for d in detections if d["label"] in _BOTTLE_CLASSES]
        object_dets = bag_dets + bottle_dets

        self._frame_persons = len(person_dets)
        self._frame_bags    = len(bag_dets) + len(bottle_dets)

        # ── 2. Run InsightFace on FULL FRAME (every 5th frame) ────────────
        # Resize to 640x480 for faster CPU inference, then scale back.
        run_face_this_frame = (self._total_frames % RUN_FACE_EVERY == 0)
        if self._face_rec and run_face_this_frame:
            try:
                small_frame = cv2.resize(frame, (640, 480))
                _embs = self._face_rec.extract_embedding(small_frame)
                raw_faces = getattr(self._face_rec, "_last_faces", []) or []

                # Scale face coords back to original frame size
                h_scale = fh / 480
                w_scale = fw / 640
                for face_obj in raw_faces:
                    face_obj.bbox[0] *= w_scale
                    face_obj.bbox[1] *= h_scale
                    face_obj.bbox[2] *= w_scale
                    face_obj.bbox[3] *= h_scale
                    kps = getattr(face_obj, "kps", None)
                    if kps is not None:
                        for kp in kps:
                            kp[0] *= w_scale
                            kp[1] *= h_scale

                self._last_all_faces = raw_faces
            except Exception as exc:
                logger.debug("Full-frame face detection error: %s", exc)

        all_faces = self._last_all_faces
        self._frame_faces = len(all_faces)

        # ── 3. Draw bag/object boxes ──────────────────────────────────
        for obj in object_dets:
            ox, oy, ow, oh = obj["bbox"]
            is_bag = obj["label"] in _BAG_CLASSES
            color  = _ORANGE if is_bag else _YELLOW
            label  = _OBJECT_LABELS.get(obj["label"], obj["label"])
            cv2.rectangle(annotated, (ox, oy), (ox + ow, oy + oh), color, 2)
            (tw, th), _ = cv2.getTextSize(label, _FONT, 0.45, 1)
            cv2.rectangle(annotated, (ox, oy - th - 6), (ox + tw + 4, oy), color, -1)
            cv2.putText(annotated, label, (ox + 2, oy - 3), _FONT, 0.45, _BLACK, 1)

        # ── 4. Process each person detection ──────────────────────────
        db = SessionLocal()
        try:
            now = time.time()
            # Clear stale cache entries
            stale = [c for c, t in self._seen_cache.items() if now - t > 300]
            for c in stale:
                self._seen_cache.pop(c, None)

            for det in person_dets:
                bbox = det["bbox"]   # [x, y, w, h]
                x, y, w, h = bbox
                x2, y2 = x + w, y + h

                # ── 4a. SmartIdentifier (face/ReID/color/new) ─────────────
                code   = "Detecting..."
                method = "unknown"
                conf   = 0.0
                color  = _GREY

                if self._identifier:
                    try:
                        result = self._identifier.identify(
                            frame, bbox, db,
                            location_id=self.location_id,
                            zone_id=self.zone_id,
                        )
                        code   = result["unique_code"]
                        method = result["method"]
                        conf   = result["confidence"]
                        color  = _BOX_COLORS.get(method, _WHITE)
                    except Exception as exc:
                        logger.debug("SmartIdentifier error: %s", exc)

                # ── 4b. Person bounding box ───────────────────────────
                cv2.rectangle(annotated, (x, y), (x2, y2), color, 2)

                # ── 4c. Match faces from full-frame detection to this person ─
                # Check if any face center falls inside this person bbox.
                # Use temporal smoothing for stable drawing.
                gender_str = ""
                person_key = (x, y, w, h)

                matched_face = None
                for face_obj in all_faces:
                    try:
                        fb = face_obj.bbox.astype(int)
                        fc_x = (fb[0] + fb[2]) / 2
                        fc_y = (fb[1] + fb[3]) / 2
                        head_bottom = y + (y2 - y) * 0.6
                        if x <= fc_x <= x2 and y <= fc_y <= head_bottom:
                            matched_face = face_obj
                            break
                    except Exception:
                        continue

                if matched_face is not None:
                    raw_bbox = matched_face.bbox.copy()

                    # Temporal smoothing: average last 5 bboxes
                    if person_key not in self._face_bbox_history:
                        self._face_bbox_history[person_key] = []
                    self._face_bbox_history[person_key].append(raw_bbox)
                    self._face_bbox_history[person_key] = self._face_bbox_history[person_key][-5:]
                    avg_bbox = np.mean(self._face_bbox_history[person_key], axis=0).astype(int)
                    fx1, fy1, fx2, fy2 = avg_bbox

                    # Draw CYAN face box (full-frame coords — no offset needed)
                    cv2.rectangle(annotated, (fx1, fy1), (fx2, fy2), _CYAN, 2)
                    cv2.putText(annotated, "Face", (fx1, fy1 - 8),
                                _FONT, 0.5, _CYAN, 1)

                    # Landmarks with smoothing
                    kps = getattr(matched_face, "kps", None)
                    if kps is not None and len(kps) >= 5:
                        if person_key not in self._face_kps_history:
                            self._face_kps_history[person_key] = []
                        self._face_kps_history[person_key].append(kps[:5].copy())
                        self._face_kps_history[person_key] = self._face_kps_history[person_key][-5:]
                        avg_kps = np.mean(self._face_kps_history[person_key], axis=0).astype(int)

                        landmark_colors = [
                            (255, 0,   0),    # left eye
                            (0,   255, 0),    # right eye
                            (0,   0,   255),  # nose
                            (255, 255, 0),    # left mouth
                            (0,   255, 255),  # right mouth
                        ]
                        for i, kp in enumerate(avg_kps):
                            cv2.circle(annotated, (int(kp[0]), int(kp[1])),
                                       3, landmark_colors[i], -1)

                    # Gender
                    g_val = getattr(matched_face, "gender", None)
                    if g_val is not None:
                        gender_str = "M" if int(g_val) == 1 else "F"

                # ── 3d. Dress colour (torso crop, K-means) ────────────────
                color_hex = ""
                try:
                    torso_top  = y + int(h * 0.30)
                    torso_bot  = y + int(h * 0.70)
                    torso_crop = frame[
                        max(0, torso_top): min(fh, torso_bot),
                        max(0, x): min(fw, x2),
                    ]
                    if torso_crop.size > 100:
                        dom = self._dominant_color(torso_crop)
                        if dom is not None:
                            r, g_c, b = dom
                            color_hex = f"#{r:02x}{g_c:02x}{b:02x}"
                            # Draw small coloured square bottom-left of person box
                            sq_size = 14
                            sq_x, sq_y = x + 3, y2 - sq_size - 3
                            cv2.rectangle(annotated,
                                          (sq_x, sq_y),
                                          (sq_x + sq_size, sq_y + sq_size),
                                          (int(b), int(g_c), int(r)), -1)
                            cv2.rectangle(annotated,
                                          (sq_x, sq_y),
                                          (sq_x + sq_size, sq_y + sq_size),
                                          _WHITE, 1)
                            # Hex label
                            cv2.putText(annotated, color_hex,
                                        (sq_x + sq_size + 3, sq_y + sq_size - 2),
                                        _FONT, 0.32, _WHITE, 1)
                except Exception:
                    pass

                # ── 3e. Height estimate ───────────────────────────────────
                height_ratio = h / max(fh, 1)
                if height_ratio > 0.6:
                    height_label = "~Tall"
                elif height_ratio > 0.35:
                    height_label = "~Medium"
                else:
                    height_label = "~Short"

                # ── 3f. SDT label + gender + height above box ─────────────
                id_text = code
                if gender_str:
                    id_text += f" {gender_str}"
                (tw, th_t), _ = cv2.getTextSize(id_text, _FONT, 0.50, 2)
                cv2.rectangle(annotated, (x, y - th_t - 8), (x + tw + 6, y), color, -1)
                cv2.putText(annotated, id_text, (x + 3, y - 4), _FONT, 0.50, _BLACK, 2)

                # Method + confidence below box
                method_short = {
                    "face": "Face", "dress_color": "Color",
                    "body_structure": "Body", "multi_feature": "Multi",
                    "new_registration": "New"
                }.get(method, method)
                cv2.putText(annotated, f"{method_short} {int(conf*100)}%",
                            (x, y2 + 14), _FONT, 0.38, color, 1)

                # Height label on right side of box
                cv2.putText(annotated, height_label,
                            (x2 - 55, y2 - 5), _FONT, 0.35, _WHITE, 1)

                # ── 3g. Link bags to nearest person ───────────────────────
                for obj in object_dets:
                    ox, oy, ow, oh = obj["bbox"]
                    obj_cx = ox + ow // 2
                    obj_cy = oy + oh // 2
                    per_cx = x + w // 2
                    per_cy = y + h // 2

                    # Check overlap or proximity < 50px
                    overlap_x = max(0, min(x2, ox + ow) - max(x, ox))
                    overlap_y = max(0, min(y2, oy + oh) - max(y, oy))
                    overlaps  = overlap_x > 0 and overlap_y > 0
                    dist = math.sqrt((obj_cx - per_cx)**2 + (obj_cy - per_cy)**2)

                    if overlaps or dist < 50:
                        bag_label = _OBJECT_LABELS.get(obj["label"], "object")
                        cv2.putText(annotated, f"carrying {bag_label}",
                                    (x, y2 + 28), _FONT, 0.35, _ORANGE, 1)
                        break  # only show once per person

                # ── 3h. Log sighting (deduped) ────────────────────────────
                if code and code != "Detecting...":
                    if now - self._seen_cache.get(code, 0) > 30:
                        self._seen_cache[code] = now
                        self.persons_today += (1 if method == "new_registration" else 0)
                        try:
                            log_sighting(
                                unique_code=code,
                                location_id=self.location_id,
                                zone_id=self.zone_id,
                                camera_id=self.camera_id,
                                confidence=conf,
                                db=db,
                            )
                        except Exception as exc:
                            logger.debug("log_sighting error: %s", exc)

                    # Store in recent detections deque
                    self._detections.append({
                        "unique_code": code,
                        "method":      method,
                        "confidence":  conf,
                        "color_hex":   color_hex or None,
                        "gender":      gender_str or None,
                        "height":      height_label,
                        "detected_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        "zone_id":     self.zone_id,
                        "camera_id":   self.camera_id,
                    })
        finally:
            db.close()

        # ── 4. HUD overlay ────────────────────────────────────────────────
        self._draw_hud(annotated, fh, fw)

        return annotated

    # ─────────────────────────────────────────────────────────────────────────
    # HUD Overlay
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_hud(self, annotated: np.ndarray, fh: int, fw: int) -> None:
        """Draw on-screen stats overlay."""
        fps = self._fps_value

        # ── Top-left: camera info ─────────────────────────────────────────
        hud_top = f"SmartDetect | {self.camera_id} | LIVE"
        (hw, hh), _ = cv2.getTextSize(hud_top, _FONT, 0.55, 2)
        cv2.rectangle(annotated, (0, 0), (hw + 14, hh + 12), (0, 0, 0), -1)
        cv2.putText(annotated, hud_top, (7, hh + 5), _FONT, 0.55, _WHITE, 1)

        # ── Bottom-left: stats ────────────────────────────────────────────
        stats = f"Persons: {self._frame_persons} | Faces: {self._frame_faces} | Bags: {self._frame_bags} | FPS: {fps:.0f}"
        (sw, sh), _ = cv2.getTextSize(stats, _FONT, 0.45, 1)
        by = fh - 10
        cv2.rectangle(annotated, (0, by - sh - 8), (sw + 14, fh), (0, 0, 0), -1)
        cv2.putText(annotated, stats, (7, by - 2), _FONT, 0.45, _WHITE, 1)

        # ── Bottom-right: timestamp ───────────────────────────────────────
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        (tsw, tsh), _ = cv2.getTextSize(ts, _FONT, 0.42, 1)
        cv2.rectangle(annotated, (fw - tsw - 14, fh - tsh - 12), (fw, fh), (0, 0, 0), -1)
        cv2.putText(annotated, ts, (fw - tsw - 7, fh - 6), _FONT, 0.42, _GREY, 1)

        # ── Colour legend (top-right) ─────────────────────────────────────
        legend_items = [
            (_GREEN,  "Face ID"),
            (_BLUE,   "Body ID"),
            (_WHITE,  "New"),
            (_ORANGE, "Bag/Obj"),
        ]
        lx = fw - 100
        ly = 8
        for lcolor, ltext in legend_items:
            cv2.rectangle(annotated, (lx, ly), (lx + 10, ly + 10), lcolor, -1)
            cv2.putText(annotated, ltext, (lx + 14, ly + 9), _FONT, 0.33, _WHITE, 1)
            ly += 16

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _dominant_color(bgr_crop: np.ndarray, k: int = 3) -> Optional[Tuple[int, int, int]]:
        """Extract dominant colour from a BGR crop using K-means. Returns (R,G,B)."""
        try:
            pixels = bgr_crop.reshape(-1, 3).astype(np.float32)
            k = min(k, len(pixels))
            if k < 1:
                return None
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
            _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
            counts = np.bincount(labels.flatten())
            dominant_bgr = centers[np.argmax(counts)].astype(np.uint8)
            return (int(dominant_bgr[2]), int(dominant_bgr[1]), int(dominant_bgr[0]))  # RGB
        except Exception:
            return None

    @staticmethod
    def _encode_frame(frame: np.ndarray) -> bytes:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes()

