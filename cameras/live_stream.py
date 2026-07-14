"""
cameras/live_stream.py
───────────────────────
Live camera capture + real-time MJPEG streaming for SmartDetect.

Architecture (capture and ML are decoupled — same pattern as camera_processor):

  Capture thread (~30 fps):
    read frame → push to analysis queue (drop-oldest)
    → annotate frame from CACHED analysis results
    → encode JPEG → self._latest_frame
  Analysis worker thread (runs at its own pace, ~1-3 Hz on CPU):
    YOLO persons/objects → ByteTrack (persistent track ids)
    → SmartIdentifier once per track (registration gated on track age)
    → InsightFace full-frame, dress colour, height, bag linking
    → sighting logging (deduped) → cached results for the capture thread
  GET /camera/stream/{id} yields MJPEG from self._latest_frame
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
import warnings
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from database.db import SessionLocal
from database.models import Person
from database.queries import log_sighting, check_watchlist_alert

logger = logging.getLogger(__name__)

# supervision provides ByteTrack + PolygonZone (deprecation warning is about
# the v0.30 move to the separate `trackers` package — pinned <0.30 in requirements)
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        import supervision as sv
    _SV_AVAILABLE = True
except Exception as _sv_exc:  # pragma: no cover
    sv = None
    _SV_AVAILABLE = False
    logger.warning("supervision unavailable (%s) — tracking disabled, no new registrations", _sv_exc)


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

# A track must survive this many analysis cycles before a new SDT code
# is registered — prevents one ghost person row per cycle.
_MIN_TRACK_AGE_FOR_REGISTRATION = 3

# Movement-trail length (analysis cycles) drawn behind each tracked person
_TRAIL_LENGTH = 30

# Faces smaller/blurrier than this are drawn but never decide identity —
# far-away faces give noisy embeddings that cross-match strangers
_MIN_FACE_HEIGHT_PX  = 48
_MIN_FACE_DET_SCORE  = 0.60


class LiveStream:
    """
    Manages one camera source with capture and ML analysis on separate threads.

    Parameters
    ----------
    source        : int (webcam index) or str (RTSP / file path)
    location_id   : SmartDetect location ID
    zone_id       : zone label (e.g. "entrance")
    camera_id     : human-readable camera identifier
    target_fps    : capture/stream rate (default 30 — analysis runs independently)
    """

    def __init__(
        self,
        source,
        location_id: str = "LOC-001",
        zone_id:     str  = "main",
        camera_id:   str  = "CAM-001",
        target_fps:  int  = 30,
    ):
        self.source      = source
        self._source     = source  # kept for status API
        self.location_id = location_id
        self.zone_id     = zone_id
        self.camera_id   = camera_id
        self._interval   = 1.0 / max(target_fps, 1)

        self._cap: Optional[cv2.VideoCapture] = None
        self._thread:  Optional[threading.Thread] = None
        self._worker:  Optional[threading.Thread] = None
        self._running  = False
        self._lock     = threading.Lock()
        self._latest_frame: bytes = self._encode_frame(_BLANK)

        # ── Analysis handoff (capture → worker, drop-oldest) ───────────
        self._analysis_queue: queue.Queue = queue.Queue(maxsize=1)
        self._results_lock = threading.Lock()
        # persons: [{bbox, code, method, conf, color_hex, gender, height_label,
        #            face_bbox, face_kps, carrying, tracker_id}], objects: [{bbox, label}]
        self._latest_results: Dict = {"persons": [], "objects": []}

        # ── Tracking state (worker thread only) ────────────────────────
        self._tracker = None
        self._zone    = None            # sv.PolygonZone — built on first frame
        self.zone_count: int = 0        # persons currently in the camera zone
        self._track_codes: Dict[int, Dict] = {}   # tracker_id → {code, method, conf}
        self._track_age:   Dict[int, int]  = {}   # tracker_id → analysis cycles seen
        self._track_trails: Dict[int, deque] = {} # tracker_id → recent bbox centers

        # ── Stats ──────────────────────────────────────────────────────────
        self.persons_today: int = 0
        self.active_tracks: Dict[int, str] = {}   # track_id → SDT code
        self._detections: deque = deque(maxlen=100)

        # ── FPS counter (frame count + time window) ────────────────────
        self._fps_count     = 0
        self._fps_last_time = time.time()
        self._fps_value     = 0.0
        self._analysis_fps  = 0.0   # analysis cycles per second
        self._total_frames  = 0

        # ── Frame-level counters (updated per analysis cycle) ──────────
        self._frame_persons = 0
        self._frame_faces   = 0
        self._frame_bags    = 0

        # ── Face bbox smoothing (temporal averaging, keyed by tracker_id) ──
        self._face_bbox_history: Dict[int, list] = {}
        self._face_kps_history:  Dict[int, list] = {}

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

        if _SV_AVAILABLE:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                # activation 0.25 so conf-0.3 detections start tracks on the
                # first cycle — analysis runs ~1-2 Hz, waiting frames = seconds
                self._tracker = sv.ByteTrack(track_activation_threshold=0.25)

    # ── Public interface ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the camera and start the capture + analysis threads."""
        if isinstance(self.source, int):
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera source: {self.source}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # MJPG codec: USB webcams deliver 30 fps MJPG vs ~8 fps uncompressed YUY2
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc('M', 'J', 'P', 'G'))
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
        self._worker = threading.Thread(target=self._analysis_loop, daemon=True,
                                        name=f"LiveStream-ML-{self.camera_id}")
        self._worker.start()
        logger.info("LiveStream %s started — source=%s", self.camera_id, self.source)

    def stop(self) -> None:
        """Stop both threads and release the camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._worker:
            self._worker.join(timeout=3)
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
            "analysis_fps":          round(self._analysis_fps, 1),
            "persons_detected_today": self.persons_today,
            "active_tracks":         len(self.active_tracks),
            "zone_count":            self.zone_count,
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

    # ─────────────────────────────────────────────────────────────────────────
    # Capture loop — lightweight: read, hand off, annotate from cache, encode
    # ─────────────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            t0 = time.time()
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("LiveStream %s: frame read failed — retrying", self.camera_id)
                time.sleep(0.5)
                continue

            self._total_frames += 1

            # Hand the frame to the analysis worker — only copy when the
            # worker is ready for one (it consumes ~1/s; copying every frame
            # wastes capture-loop time)
            if self._analysis_queue.empty():
                try:
                    self._analysis_queue.put_nowait(frame.copy())
                except queue.Full:
                    pass

            annotated = self._annotate_frame(frame)
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
    # Analysis worker — YOLO + ByteTrack + identify + sightings (own pace)
    # ─────────────────────────────────────────────────────────────────────────

    def _analysis_loop(self) -> None:
        cycle_times: deque = deque(maxlen=10)
        while self._running:
            try:
                frame = self._analysis_queue.get(timeout=1)
            except queue.Empty:
                continue
            t0 = time.time()
            try:
                self._analyze_frame(frame)
            except Exception as exc:
                logger.debug("LiveStream analysis error: %s", exc)
            cycle_times.append(time.time() - t0)
            if cycle_times:
                avg = sum(cycle_times) / len(cycle_times)
                self._analysis_fps = 1.0 / max(avg, 0.001)

    def _analyze_frame(self, frame: np.ndarray) -> None:
        """Heavy ML pipeline. Writes results into self._latest_results."""
        fh, fw = frame.shape[:2]

        if not self._detector:
            return

        detections = self._detector.detect(frame)
        person_dets = [d for d in detections if d["label"] == "person"]
        bag_dets    = [d for d in detections if d["label"] in _BAG_CLASSES]
        bottle_dets = [d for d in detections if d["label"] in _BOTTLE_CLASSES]
        object_dets = bag_dets + bottle_dets

        self._frame_persons = len(person_dets)
        self._frame_bags    = len(bag_dets) + len(bottle_dets)

        # ── ByteTrack: persistent tracker_id per person ────────────────────
        tracked: List[Tuple[Optional[int], List[int]]] = []  # (tracker_id, [x,y,w,h])
        if self._tracker is not None and person_dets:
            xyxy = np.array(
                [[d["bbox"][0], d["bbox"][1],
                  d["bbox"][0] + d["bbox"][2], d["bbox"][1] + d["bbox"][3]]
                 for d in person_dets],
                dtype=np.float32,
            )
            conf = np.array([d["confidence"] for d in person_dets], dtype=np.float32)
            sv_dets = sv.Detections(
                xyxy=xyxy,
                confidence=conf,
                class_id=np.zeros(len(person_dets), dtype=int),
            )
            sv_dets = self._tracker.update_with_detections(sv_dets)
            for i in range(len(sv_dets)):
                x1, y1, x2, y2 = sv_dets.xyxy[i].astype(int)
                tid = int(sv_dets.tracker_id[i]) if sv_dets.tracker_id is not None else None
                tracked.append((tid, [x1, y1, x2 - x1, y2 - y1]))

            # ByteTrack only returns confirmed tracks — also show raw YOLO
            # detections it hasn't confirmed yet (as "Detecting..."), so a
            # second person appears instantly instead of cycles later
            for d in person_dets:
                dx, dy, dw, dh = d["bbox"]
                covered = False
                for _, tb in tracked:
                    tx, ty, tw_, th_ = tb
                    ix = max(0, min(dx + dw, tx + tw_) - max(dx, tx))
                    iy = max(0, min(dy + dh, ty + th_) - max(dy, ty))
                    inter = ix * iy
                    union = dw * dh + tw_ * th_ - inter
                    if union > 0 and inter / union > 0.5:
                        covered = True
                        break
                if not covered:
                    tracked.append((None, [dx, dy, dw, dh]))

            # Zone occupancy (full-frame polygon by default — customise later)
            if self._zone is None and _SV_AVAILABLE:
                self._zone = sv.PolygonZone(
                    polygon=np.array([[0, 0], [fw, 0], [fw, fh], [0, fh]])
                )
            if self._zone is not None:
                try:
                    self._zone.trigger(sv_dets)
                    self.zone_count = int(self._zone.current_count)
                except Exception:
                    self.zone_count = len(tracked)
        else:
            # No tracker: annotate without ids; registration stays gated off
            tracked = [(None, list(d["bbox"])) for d in person_dets]
            self.zone_count = len(tracked)

        # ── Full-frame face detection (resized for CPU speed) ──────────────
        all_faces: list = []
        if self._face_rec:
            try:
                small_frame = cv2.resize(frame, (640, 480))
                self._face_rec.extract_embedding(small_frame)
                raw_faces = getattr(self._face_rec, "_last_faces", []) or []
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
                all_faces = raw_faces
            except Exception as exc:
                logger.debug("Full-frame face detection error: %s", exc)
        self._frame_faces = len(all_faces)

        # ── Per-person: identify (once per track), face match, colour, log ──
        persons_out: List[Dict] = []
        seen_tids: set = set()
        db = SessionLocal()
        try:
            now = time.time()
            stale = [c for c, t in self._seen_cache.items() if now - t > 300]
            for c in stale:
                self._seen_cache.pop(c, None)

            # Codes worn by people visible RIGHT NOW — colour/Re-ID must not
            # hand one of these to a second person in the same scene
            current_tids  = {t for t, _ in tracked if t is not None}
            claimed_codes = {
                self._track_codes[t]["code"]
                for t in current_tids if t in self._track_codes
            }

            for tid, bbox in tracked:
                x, y, w, h = bbox
                x2, y2 = x + w, y + h

                # ── Face match FIRST (center inside upper 60% of person box) —
                # its embedding feeds identify(), so identity always comes from
                # THIS person's face and InsightFace runs once per cycle ──────
                face_bbox, face_kps, gender_str = None, None, ""
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

                # Quality gate: tiny or low-confidence faces give noisy
                # embeddings that cross-match strangers — draw them, but never
                # let them decide identity (match OR register)
                face_emb = None
                if matched_face is not None:
                    try:
                        fb_q = matched_face.bbox
                        face_h = float(fb_q[3] - fb_q[1])
                        det_sc = float(getattr(matched_face, "det_score", 1.0) or 1.0)
                        if face_h >= _MIN_FACE_HEIGHT_PX and det_sc >= _MIN_FACE_DET_SCORE:
                            face_emb = getattr(matched_face, "embedding", None)
                    except Exception:
                        face_emb = getattr(matched_face, "embedding", None)

                # ── Identify: cached per tracker_id, registration age-gated ──
                code, method, conf = "Detecting...", "pending", 0.0
                label              = "Detecting..."
                color_hex_from_id  = None
                if tid is not None:
                    seen_tids.add(tid)
                    self._track_age[tid] = self._track_age.get(tid, 0) + 1
                    cached = self._track_codes.get(tid)
                    if cached:
                        code, method, conf = cached["code"], cached["method"], cached["conf"]
                        label = cached.get("label", code)
                    elif self._identifier:
                        try:
                            result = self._identifier.identify(
                                frame, bbox, db,
                                location_id=self.location_id,
                                zone_id=self.zone_id,
                                allow_new=self._track_age[tid] >= _MIN_TRACK_AGE_FOR_REGISTRATION,
                                face_embedding=face_emb,
                                exclude_codes=claimed_codes,
                                # full-frame scan already found every face —
                                # a per-person InsightFace rerun just burns CPU
                                extract_face_if_missing=False,
                            )
                            code   = result["unique_code"]
                            method = result["method"]
                            conf   = result["confidence"]
                            color_hex_from_id = result.get("color_hex")
                            if code != "Detecting...":
                                # Named enrollment: label shows the person's
                                # name once an operator has set one
                                label = code
                                try:
                                    prow = db.query(Person).filter(Person.unique_code == code).first()
                                    if prow is not None and getattr(prow, "display_name", None):
                                        label = f"{prow.display_name} ({code})"
                                except Exception:
                                    pass
                                self._track_codes[tid] = {"code": code, "method": method,
                                                          "conf": conf, "label": label}
                                self.active_tracks[tid] = code
                                claimed_codes.add(code)
                        except Exception as exc:
                            logger.debug("SmartIdentifier error: %s", exc)

                    # Movement trail (one point per analysis cycle)
                    trail = self._track_trails.setdefault(tid, deque(maxlen=_TRAIL_LENGTH))
                    trail.append((x + w // 2, y2))

                if matched_face is not None and tid is not None:
                    raw_bbox = matched_face.bbox.copy()
                    hist = self._face_bbox_history.setdefault(tid, [])
                    hist.append(raw_bbox)
                    self._face_bbox_history[tid] = hist[-5:]
                    face_bbox = np.mean(self._face_bbox_history[tid], axis=0).astype(int).tolist()

                    kps = getattr(matched_face, "kps", None)
                    if kps is not None and len(kps) >= 5:
                        khist = self._face_kps_history.setdefault(tid, [])
                        khist.append(kps[:5].copy())
                        self._face_kps_history[tid] = khist[-5:]
                        face_kps = np.mean(self._face_kps_history[tid], axis=0).astype(int).tolist()

                    g_val = getattr(matched_face, "gender", None)
                    if g_val is not None:
                        gender_str = "M" if int(g_val) == 1 else "F"

                # ── Dress colour (torso crop, K-means) ──────────────────────
                color_hex = color_hex_from_id or ""
                if not color_hex:
                    try:
                        torso_crop = frame[
                            max(0, y + int(h * 0.30)): min(fh, y + int(h * 0.70)),
                            max(0, x): min(fw, x2),
                        ]
                        if torso_crop.size > 100:
                            dom = self._dominant_color(torso_crop)
                            if dom is not None:
                                r, g_c, b = dom
                                color_hex = f"#{r:02x}{g_c:02x}{b:02x}"
                    except Exception:
                        pass

                # ── Height estimate ─────────────────────────────────────────
                height_ratio = h / max(fh, 1)
                if height_ratio > 0.6:
                    height_label = "~Tall"
                elif height_ratio > 0.35:
                    height_label = "~Medium"
                else:
                    height_label = "~Short"

                # ── Link bags to this person ────────────────────────────────
                carrying = None
                for obj in object_dets:
                    ox, oy, ow, oh = obj["bbox"]
                    overlap_x = max(0, min(x2, ox + ow) - max(x, ox))
                    overlap_y = max(0, min(y2, oy + oh) - max(y, oy))
                    overlaps  = overlap_x > 0 and overlap_y > 0
                    dist = math.sqrt((ox + ow // 2 - (x + w // 2)) ** 2
                                     + (oy + oh // 2 - (y + h // 2)) ** 2)
                    if overlaps or dist < 50:
                        carrying = _OBJECT_LABELS.get(obj["label"], "object")
                        break

                # ── Log sighting (deduped) ──────────────────────────────────
                if code and code != "Detecting...":
                    if now - self._seen_cache.get(code, 0) > 30:
                        self._seen_cache[code] = now
                        self.persons_today += (1 if method == "new_registration" else 0)
                        snap_path = self._save_sighting_snapshot(
                            code, frame[max(0, y):min(fh, y2), max(0, x):min(fw, x2)]
                        )
                        try:
                            log_sighting(
                                unique_code=code,
                                location_id=self.location_id,
                                zone_id=self.zone_id,
                                camera_id=self.camera_id,
                                confidence=conf,
                                db=db,
                                frame_path=snap_path,
                            )
                            check_watchlist_alert(
                                unique_code=code,
                                camera_id=self.camera_id,
                                location_id=self.location_id,
                                db=db,
                            )
                        except Exception as exc:
                            logger.debug("log_sighting error: %s", exc)

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

                persons_out.append({
                    "tracker_id":   tid,
                    "bbox":         [x, y, w, h],
                    "code":         code,
                    "label":        label,
                    "method":       method,
                    "conf":         conf,
                    "color_hex":    color_hex,
                    "gender":       gender_str,
                    "height_label": height_label,
                    "face_bbox":    face_bbox,
                    "face_kps":     face_kps,
                    "carrying":     carrying,
                })
        finally:
            db.close()

        # ── Drop state for tracks that disappeared ──────────────────────────
        for tid in list(self._track_age.keys()):
            if tid not in seen_tids:
                self._track_age.pop(tid, None)
                self._track_trails.pop(tid, None)
                self._face_bbox_history.pop(tid, None)
                self._face_kps_history.pop(tid, None)
                self.active_tracks.pop(tid, None)
                # keep self._track_codes so a returning ByteTrack id keeps its code

        objects_out = [{"bbox": o["bbox"], "label": _OBJECT_LABELS.get(o["label"], o["label"]),
                        "is_bag": o["label"] in _BAG_CLASSES} for o in object_dets]

        with self._results_lock:
            self._latest_results = {"persons": persons_out, "objects": objects_out}

    # ─────────────────────────────────────────────────────────────────────────
    # Annotation — runs in the capture loop using cached analysis results
    # ─────────────────────────────────────────────────────────────────────────

    def _annotate_frame(self, frame: np.ndarray) -> np.ndarray:
        annotated = frame  # draw in place; capture loop owns this frame
        fh, fw = frame.shape[:2]

        with self._results_lock:
            results = self._latest_results
        persons = results["persons"]
        objects = results["objects"]

        # ── Bag / object boxes ────────────────────────────────────────────
        for obj in objects:
            ox, oy, ow, oh = obj["bbox"]
            color = _ORANGE if obj["is_bag"] else _YELLOW
            label = obj["label"]
            cv2.rectangle(annotated, (ox, oy), (ox + ow, oy + oh), color, 2)
            (tw, th), _ = cv2.getTextSize(label, _FONT, 0.45, 1)
            cv2.rectangle(annotated, (ox, oy - th - 6), (ox + tw + 4, oy), color, -1)
            cv2.putText(annotated, label, (ox + 2, oy - 3), _FONT, 0.45, _BLACK, 1)

        # ── Movement trails ───────────────────────────────────────────────
        for p in persons:
            tid = p["tracker_id"]
            trail = self._track_trails.get(tid) if tid is not None else None
            if trail and len(trail) >= 2:
                pts = np.array(trail, dtype=np.int32).reshape(-1, 1, 2)
                cv2.polylines(annotated, [pts], False, _BOX_COLORS.get(p["method"], _GREY), 2)

        # ── Person boxes + labels ─────────────────────────────────────────
        for p in persons:
            x, y, w, h = p["bbox"]
            x2, y2 = x + w, y + h
            code, method, conf = p["code"], p["method"], p["conf"]
            color = _BOX_COLORS.get(method, _GREY if method == "pending" else _WHITE)

            cv2.rectangle(annotated, (x, y), (x2, y2), color, 2)

            # Face box + landmarks
            if p["face_bbox"]:
                fx1, fy1, fx2, fy2 = p["face_bbox"]
                cv2.rectangle(annotated, (fx1, fy1), (fx2, fy2), _CYAN, 2)
                cv2.putText(annotated, "Face", (fx1, fy1 - 8), _FONT, 0.5, _CYAN, 1)
                if p["face_kps"]:
                    landmark_colors = [
                        (255, 0,   0),    # left eye
                        (0,   255, 0),    # right eye
                        (0,   0,   255),  # nose
                        (255, 255, 0),    # left mouth
                        (0,   255, 255),  # right mouth
                    ]
                    for i, kp in enumerate(p["face_kps"][:5]):
                        cv2.circle(annotated, (int(kp[0]), int(kp[1])), 3, landmark_colors[i], -1)

            # Dress colour square + hex
            if p["color_hex"]:
                try:
                    hexv = p["color_hex"].lstrip("#")
                    r, g_c, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
                    sq_size = 14
                    sq_x, sq_y = x + 3, y2 - sq_size - 3
                    cv2.rectangle(annotated, (sq_x, sq_y),
                                  (sq_x + sq_size, sq_y + sq_size), (b, g_c, r), -1)
                    cv2.rectangle(annotated, (sq_x, sq_y),
                                  (sq_x + sq_size, sq_y + sq_size), _WHITE, 1)
                    cv2.putText(annotated, p["color_hex"],
                                (sq_x + sq_size + 3, sq_y + sq_size - 2),
                                _FONT, 0.32, _WHITE, 1)
                except Exception:
                    pass

            # Name/SDT label + gender above box
            id_text = p.get("label") or code
            if p["gender"]:
                id_text += f" {p['gender']}"
            (tw, th_t), _ = cv2.getTextSize(id_text, _FONT, 0.50, 2)
            cv2.rectangle(annotated, (x, y - th_t - 8), (x + tw + 6, y), color, -1)
            cv2.putText(annotated, id_text, (x + 3, y - 4), _FONT, 0.50, _BLACK, 2)

            # Method + confidence below box
            method_short = {
                "face": "Face", "dress_color": "Color",
                "body_structure": "Body", "multi_feature": "Multi",
                "new_registration": "New", "pending": "..."
            }.get(method, method)
            cv2.putText(annotated, f"{method_short} {int(conf * 100)}%",
                        (x, y2 + 14), _FONT, 0.38, color, 1)

            # Height label on right side of box
            cv2.putText(annotated, p["height_label"],
                        (x2 - 55, y2 - 5), _FONT, 0.35, _WHITE, 1)

            if p["carrying"]:
                cv2.putText(annotated, f"carrying {p['carrying']}",
                            (x, y2 + 28), _FONT, 0.35, _ORANGE, 1)

        # ── HUD overlay ───────────────────────────────────────────────────
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
        stats = (f"Persons: {self._frame_persons} | Faces: {self._frame_faces} | "
                 f"Bags: {self._frame_bags} | FPS: {fps:.0f} | ML: {self._analysis_fps:.1f}/s")
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

    _MAX_SNAPSHOTS_PER_PERSON = 20

    def _save_sighting_snapshot(self, code: str, crop: np.ndarray) -> Optional[str]:
        """Save a person crop for this sighting; keep only the newest files."""
        try:
            if crop is None or crop.size == 0:
                return None
            snap_dir = Path("snapshots") / code
            snap_dir.mkdir(parents=True, exist_ok=True)
            fname = snap_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            if not cv2.imwrite(str(fname), crop):
                return None
            # Prune oldest sighting shots (never registered.jpg)
            shots = sorted(p for p in snap_dir.glob("*.jpg") if p.name != "registered.jpg")
            for old in shots[:-self._MAX_SNAPSHOTS_PER_PERSON]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return fname.as_posix()
        except Exception as exc:
            logger.debug("sighting snapshot failed: %s", exc)
            return None

    @staticmethod
    def _encode_frame(frame: np.ndarray) -> bytes:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes()
