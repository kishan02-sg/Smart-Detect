"""
recognition/reid_model.py
──────────────────────────
Person Re-Identification module using torchreid's OSNet.
Falls back to a lightweight numpy-based stub when torchreid is unavailable
(useful for development / environments where torchreid can't be installed).
"""

from __future__ import annotations

import logging
from typing import List

import cv2
import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    _HAS_TORCH = True
    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    _HAS_TORCH = False
    _DEVICE = "cpu"


# ─── Lightweight stub used when torchreid is not available ───────────────────

class _StubExtractor:
    """
    Fallback feature extractor that uses colour histograms as person features.
    Not as accurate as OSNet, but keeps the rest of the pipeline working.
    """

    def __call__(self, images: list) -> "list[np.ndarray]":
        feats = []
        for img in images:
            feat = self._color_histogram(img)
            feats.append(feat)
        return feats

    @staticmethod
    def _color_histogram(img: np.ndarray, bins: int = 64) -> np.ndarray:
        """Return a normalised RGB colour histogram as a feature vector."""
        try:
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        except Exception:
            img_rgb = img
        hist = []
        for ch in range(3):
            h, _ = np.histogram(img_rgb[:, :, ch], bins=bins, range=(0, 256))
            hist.append(h.astype(np.float32))
        vec = np.concatenate(hist)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec  # shape: (192,) when bins=64


class PersonReID:
    """
    Wraps torchreid's osnet_x1_0 model for person Re-Identification.
    Automatically falls back to a colour-histogram stub if torchreid is not
    installed.

    Example
    -------
    >>> reid = PersonReID()
    >>> features = reid.extract_features(person_crop)
    >>> best_idx = reid.match(features, gallery_features)
    """

    MODEL_NAME = "osnet_x1_0"
    INPUT_SIZE = (256, 128)  # height, width — standard Re-ID input

    def __init__(self) -> None:
        self._extractor = None
        self._device = _DEVICE
        self._stub_mode = False
        self._load_model()

    # ─────────────────────────────────────────────────────────────────────────
    # Initialisation
    # ─────────────────────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """
        Load OSNet-x1.0 pretrained on Market-1501.
        Falls back to colour-histogram stub if torchreid is unavailable.
        """
        try:
            import torchreid  # noqa: PLC0415

            self._extractor = torchreid.utils.FeatureExtractor(
                model_name=self.MODEL_NAME,
                model_path="",          # empty → download pretrained weights
                device=self._device,
            )
            logger.info("PersonReID: loaded %s on %s", self.MODEL_NAME, self._device)
        except ImportError:
            logger.warning(
                "torchreid not installed — using colour-histogram stub for Re-ID. "
                "Install with: pip install torchreid  (requires torch)"
            )
            self._extractor = _StubExtractor()
            self._stub_mode = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load torchreid model (%s) — using colour-histogram stub.", exc
            )
            self._extractor = _StubExtractor()
            self._stub_mode = True

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def extract_features(self, person_crop_image: np.ndarray) -> np.ndarray:
        """
        Extract an appearance feature vector from a person crop.

        Returns a numpy float32 array (512-dim for torchreid, 192-dim for stub).
        Returns a zero vector on failure.
        """
        if person_crop_image is None or person_crop_image.size == 0:
            logger.warning("extract_features: received empty image")
            return np.zeros(512, dtype=np.float32)

        try:
            resized = cv2.resize(
                person_crop_image,
                (self.INPUT_SIZE[1], self.INPUT_SIZE[0]),
            )
            features = self._extractor([resized])

            feat = features[0]
            # torchreid returns tensors; stub returns ndarrays
            if hasattr(feat, "cpu"):
                feat = feat.cpu().numpy()
            return np.array(feat, dtype=np.float32).flatten()
        except Exception as exc:  # noqa: BLE001
            logger.error("extract_features failed: %s", exc)
            return np.zeros(512, dtype=np.float32)

    def match(
        self,
        query_features: np.ndarray,
        gallery_features: List[np.ndarray],
        threshold: float = 0.7,
    ) -> int:
        """
        Find the best match for a query feature vector in a gallery.

        Returns the index of the best matching entry, or -1 if no match
        is found above ``threshold``.
        """
        if not gallery_features:
            return -1

        query_norm = query_features / (np.linalg.norm(query_features) + 1e-8)
        best_idx = -1
        best_sim = -1.0

        for idx, gallery_feat in enumerate(gallery_features):
            if gallery_feat is None:
                continue
            g_norm = gallery_feat / (np.linalg.norm(gallery_feat) + 1e-8)
            sim = float(np.dot(query_norm, g_norm))
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        return best_idx if best_sim >= threshold else -1
