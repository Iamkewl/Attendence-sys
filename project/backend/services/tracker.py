"""Temporal multi-object tracker manager for per-camera face tracks.

Primary backend uses Ultralytics BoT-SORT when available. A lightweight IoU
fallback tracker is used if BoT-SORT cannot be initialized.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

BBox = tuple[int, int, int, int]
QualityFn = Callable[[np.ndarray, BBox, tuple[int, int, int]], tuple[float, float]]


@dataclass
class FrameSample:
    """One quality-scored frame sample retained for track-level fusion."""

    bbox: BBox
    crop_bgr: np.ndarray
    person_bbox: BBox
    person_crop_bgr: np.ndarray
    quality_score: float
    sharpness: float
    captured_at: float


@dataclass
class Track:
    """Temporal track state for one subject candidate."""

    track_id: int
    bbox_history: list[BBox] = field(default_factory=list)
    person_bbox_history: list[BBox] = field(default_factory=list)
    frame_buffer: list[FrameSample] = field(default_factory=list)
    best_embedding: np.ndarray | None = None
    best_person_embedding: np.ndarray | None = None
    identity: int | None = None
    confidence: float = 0.0
    frame_count: int = 0
    created_timestamp: float = field(default_factory=time.time)
    last_seen_timestamp: float = field(default_factory=time.time)
    consistent_matches: int = 0
    missed_frames: int = 0
    status: str = "new"
    force_reid: bool = True
    min_confirmed_quality: float = 0.0

    @property
    def latest_bbox(self) -> BBox | None:
        if not self.bbox_history:
            return None
        return self.bbox_history[-1]

    @property
    def best_frame(self) -> FrameSample | None:
        if not self.frame_buffer:
            return None
        return self.frame_buffer[0]

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_timestamp)

    def add_observation(
        self,
        *,
        bbox: BBox,
        crop_bgr: np.ndarray,
        person_bbox: BBox,
        person_crop_bgr: np.ndarray,
        quality_score: float,
        sharpness: float,
        max_buffer_size: int,
        max_history: int,
        quality_drop_ratio: float,
    ) -> None:
        """Update temporal state with one new detection observation."""
        now = time.time()
        self.last_seen_timestamp = now
        self.frame_count += 1
        self.missed_frames = 0

        self.bbox_history.append(bbox)
        if len(self.bbox_history) > max_history:
            self.bbox_history = self.bbox_history[-max_history:]

        self.person_bbox_history.append(person_bbox)
        if len(self.person_bbox_history) > max_history:
            self.person_bbox_history = self.person_bbox_history[-max_history:]

        self.frame_buffer.append(
            FrameSample(
                bbox=bbox,
                crop_bgr=crop_bgr,
                person_bbox=person_bbox,
                person_crop_bgr=person_crop_bgr,
                quality_score=float(quality_score),
                sharpness=float(sharpness),
                captured_at=now,
            )
        )
        self.frame_buffer.sort(key=lambda item: item.quality_score, reverse=True)
        if len(self.frame_buffer) > max_buffer_size:
            self.frame_buffer = self.frame_buffer[:max_buffer_size]

        if self.status == "new" and self.frame_count > 1:
            self.status = "active"
        elif self.status != "confirmed":
            self.status = "active"

        if self.status == "confirmed" and self.min_confirmed_quality > 0:
            if quality_score < (self.min_confirmed_quality * quality_drop_ratio):
                self.force_reid = True
                self.status = "active"

    def record_identity(
        self,
        *,
        student_id: int,
        confidence: float,
        consistent_required: int,
        embedding: np.ndarray | None = None,
    ) -> None:
        """Update identity confidence and confirmation state from a fresh match."""
        if self.identity is None:
            self.identity = int(student_id)
            self.consistent_matches = 1
        elif int(student_id) == self.identity:
            self.consistent_matches += 1
        else:
            # Identity conflict: reopen track for re-identification.
            self.identity = int(student_id)
            self.consistent_matches = 1
            self.force_reid = True
            self.status = "active"

        self.confidence = float(confidence)
        if embedding is not None:
            self.best_embedding = np.asarray(embedding, dtype=np.float32).copy()

        if self.consistent_matches >= consistent_required:
            self.status = "confirmed"
            self.force_reid = False
            best = self.best_frame
            if best is not None:
                self.min_confirmed_quality = max(
                    float(self.min_confirmed_quality), float(best.quality_score)
                )
        elif self.status != "confirmed":
            self.status = "active"

    def needs_identification(
        self,
        *,
        consistent_required: int,
        quality_drop_ratio: float,
    ) -> bool:
        """Determine whether this track should run embedding + matching."""
        if self.force_reid:
            return True
        if self.identity is None:
            return True
        if self.consistent_matches < consistent_required:
            return True
        if self.status != "confirmed":
            return True

        best = self.best_frame
        if best is None:
            return True

        if self.min_confirmed_quality > 0 and best.quality_score < (
            self.min_confirmed_quality * quality_drop_ratio
        ):
            self.force_reid = True
            self.status = "active"
            return True
        return False


@dataclass
class _CameraTrackerState:
    tracker: object
    tracks: dict[int, Track] = field(default_factory=dict)


class _BoTSortAdapter:
    """Adapter around Ultralytics BoT-SORT that accepts raw xyxy detections."""

    def __init__(self, *, frame_rate: int, track_buffer: int):
        from ultralytics.engine.results import Boxes
        from ultralytics.trackers.bot_sort import BOTSORT
        from ultralytics.utils import IterableSimpleNamespace, YAML
        from ultralytics.utils.checks import check_yaml

        cfg_dict = YAML.load(check_yaml("botsort.yaml"))
        cfg_dict["tracker_type"] = "botsort"
        cfg_dict["track_buffer"] = int(track_buffer)
        cfg_dict["with_reid"] = False
        cfg = IterableSimpleNamespace(**cfg_dict)

        self._boxes_cls = Boxes
        self._tracker = BOTSORT(args=cfg, frame_rate=int(frame_rate))

    def update(self, detections_xyxy: np.ndarray, frame_bgr: np.ndarray) -> np.ndarray:
        boxes = self._boxes_cls(detections_xyxy, frame_bgr.shape[:2])
        return self._tracker.update(boxes, frame_bgr)


class _IoUFallbackTracker:
    """Simple IoU tracker used only when BoT-SORT cannot initialize."""

    def __init__(self, iou_threshold: float = 0.35):
        self._iou_threshold = float(iou_threshold)
        self._next_id = 1
        self._active_boxes: dict[int, np.ndarray] = {}

    @staticmethod
    def _iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
        ax1, ay1, ax2, ay2 = box_a.tolist()
        bx1, by1, bx2, by2 = box_b.tolist()

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        iw = max(0.0, inter_x2 - inter_x1)
        ih = max(0.0, inter_y2 - inter_y1)
        inter = iw * ih

        area_a = max(0.0, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(0.0, (bx2 - bx1) * (by2 - by1))
        denom = max(area_a + area_b - inter, 1e-8)
        return float(inter / denom)

    def update(self, detections_xyxy: np.ndarray, frame_bgr: np.ndarray) -> np.ndarray:
        if detections_xyxy.size == 0:
            return np.empty((0, 8), dtype=np.float32)

        det_boxes = detections_xyxy[:, :4].astype(np.float32)
        unmatched_dets = set(range(det_boxes.shape[0]))
        unmatched_tracks = set(self._active_boxes.keys())
        matches: list[tuple[int, int]] = []

        # Greedy assignment by IoU.
        while unmatched_dets and unmatched_tracks:
            best_pair: tuple[int, int] | None = None
            best_iou = 0.0
            for track_id in list(unmatched_tracks):
                track_box = self._active_boxes[track_id]
                for det_idx in list(unmatched_dets):
                    iou = self._iou(track_box, det_boxes[det_idx])
                    if iou > best_iou:
                        best_iou = iou
                        best_pair = (track_id, det_idx)

            if best_pair is None or best_iou < self._iou_threshold:
                break

            track_id, det_idx = best_pair
            matches.append((track_id, det_idx))
            unmatched_tracks.discard(track_id)
            unmatched_dets.discard(det_idx)

        for track_id, det_idx in matches:
            self._active_boxes[track_id] = det_boxes[det_idx]

        for det_idx in unmatched_dets:
            new_id = self._next_id
            self._next_id += 1
            self._active_boxes[new_id] = det_boxes[det_idx]
            matches.append((new_id, det_idx))

        rows: list[list[float]] = []
        for track_id, det_idx in matches:
            x1, y1, x2, y2 = det_boxes[det_idx].tolist()
            conf = float(detections_xyxy[det_idx][4])
            cls = float(detections_xyxy[det_idx][5])
            rows.append([x1, y1, x2, y2, float(track_id), conf, cls, float(det_idx)])
        return np.asarray(rows, dtype=np.float32)


class TrackerManager:
    """Maintains per-camera trackers and temporal state for face tracks."""

    def __init__(
        self,
        *,
        quality_fn: QualityFn | None = None,
        top_n_frames: int = 5,
        max_lost_frames: int = 20,
        frame_rate: int = 30,
        consistent_match_count: int = 3,
        quality_drop_ratio: float = 0.6,
        tracker_factory: Callable[[], object] | None = None,
    ) -> None:
        self._quality_fn: QualityFn = quality_fn or self._default_quality_fn
        self._top_n_frames = int(max(1, top_n_frames))
        self._max_lost_frames = int(max(1, max_lost_frames))
        self._frame_rate = int(max(1, frame_rate))
        self._consistent_match_count = int(max(1, consistent_match_count))
        self._quality_drop_ratio = float(min(max(quality_drop_ratio, 0.1), 1.0))
        self._tracker_factory = tracker_factory
        self._camera_states: dict[str, _CameraTrackerState] = {}

    def configure(
        self,
        *,
        quality_fn: QualityFn | None = None,
        top_n_frames: int | None = None,
        max_lost_frames: int | None = None,
        frame_rate: int | None = None,
        consistent_match_count: int | None = None,
        quality_drop_ratio: float | None = None,
    ) -> None:
        """Update runtime tracker tuning knobs without replacing live state."""
        if quality_fn is not None:
            self._quality_fn = quality_fn
        if top_n_frames is not None:
            self._top_n_frames = int(max(1, top_n_frames))
        if max_lost_frames is not None:
            self._max_lost_frames = int(max(1, max_lost_frames))
        if frame_rate is not None:
            self._frame_rate = int(max(1, frame_rate))
        if consistent_match_count is not None:
            self._consistent_match_count = int(max(1, consistent_match_count))
        if quality_drop_ratio is not None:
            self._quality_drop_ratio = float(min(max(quality_drop_ratio, 0.1), 1.0))

    @staticmethod
    def _default_quality_fn(
        crop_bgr: np.ndarray,
        bbox: BBox,
        frame_shape: tuple[int, int, int],
    ) -> tuple[float, float]:
        """Fallback quality scoring compatible with pipeline heuristics."""
        x, y, w, h = bbox
        frame_h, frame_w = frame_shape[:2]
        face_area = float(max(w, 1) * max(h, 1))
        frame_area = float(max(frame_w * frame_h, 1))
        area_ratio = face_area / frame_area

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        area_score = min(1.0, area_ratio / max(float(settings.min_face_area_ratio), 1e-8))
        blur_score = min(1.0, sharpness / max(float(settings.min_blur_variance), 1e-6))
        quality = 0.55 * area_score + 0.45 * blur_score
        return float(quality), float(sharpness)

    def _build_tracker(self) -> object:
        if self._tracker_factory is not None:
            return self._tracker_factory()

        try:
            return _BoTSortAdapter(
                frame_rate=self._frame_rate,
                track_buffer=self._max_lost_frames,
            )
        except Exception as exc:
            logger.warning(
                "tracker: BoT-SORT init failed, using IoU fallback tracker: %s",
                exc,
            )
            return _IoUFallbackTracker()

    def _camera_state(self, camera_id: str) -> _CameraTrackerState:
        key = str(camera_id)
        state = self._camera_states.get(key)
        if state is None:
            state = _CameraTrackerState(tracker=self._build_tracker())
            self._camera_states[key] = state
        return state

    @staticmethod
    def _estimate_person_bbox(
        face_bbox: BBox,
        frame_shape: tuple[int, int, int],
    ) -> BBox:
        """Estimate person bbox from face bbox using configurable height heuristic."""
        x, y, w, h = face_bbox
        frame_h, frame_w = frame_shape[:2]

        if not settings.enable_person_box_estimation:
            return face_bbox

        face_h = max(int(h), 1)
        person_h = max(int(round(face_h * float(settings.person_box_height_factor))), face_h)
        person_w = max(int(round(w * 2.5)), int(w))

        center_x = x + (w // 2)
        top_y = max(0, y - int(round(face_h * 0.6)))

        px1 = max(0, center_x - (person_w // 2))
        py1 = max(0, top_y)
        px2 = min(frame_w, px1 + person_w)
        py2 = min(frame_h, py1 + person_h)

        return (
            int(px1),
            int(py1),
            int(max(1, px2 - px1)),
            int(max(1, py2 - py1)),
        )

    def update(
        self,
        camera_id: str,
        detections: list[BBox],
        frame: np.ndarray,
    ) -> list[Track]:
        """Update one camera tracker with current detections and return active tracks."""
        state = self._camera_state(camera_id)

        rows: list[list[float]] = []
        frame_h, frame_w = frame.shape[:2]
        for x, y, w, h in detections:
            x1 = float(max(0, x))
            y1 = float(max(0, y))
            x2 = float(min(frame_w, x + w))
            y2 = float(min(frame_h, y + h))
            if x2 <= x1 or y2 <= y1:
                continue
            rows.append([x1, y1, x2, y2, 1.0, 0.0])

        det_array = (
            np.asarray(rows, dtype=np.float32)
            if rows
            else np.empty((0, 6), dtype=np.float32)
        )
        tracked_rows = state.tracker.update(det_array, frame)

        now = time.time()
        active_ids: set[int] = set()
        for row in tracked_rows:
            if len(row) < 5:
                continue

            x1, y1, x2, y2 = [float(v) for v in row[:4]]
            track_id = int(float(row[4]))
            bbox = (
                int(round(x1)),
                int(round(y1)),
                int(round(max(1.0, x2 - x1))),
                int(round(max(1.0, y2 - y1))),
            )

            bx, by, bw, bh = bbox
            cx1 = max(0, bx)
            cy1 = max(0, by)
            cx2 = min(frame_w, bx + bw)
            cy2 = min(frame_h, by + bh)
            if cx2 <= cx1 or cy2 <= cy1:
                continue

            crop = frame[cy1:cy2, cx1:cx2].copy()
            if crop.size == 0:
                continue

            person_bbox = self._estimate_person_bbox(bbox, frame.shape)
            px, py, pw, ph = person_bbox
            px1 = max(0, px)
            py1 = max(0, py)
            px2 = min(frame_w, px + pw)
            py2 = min(frame_h, py + ph)
            if px2 <= px1 or py2 <= py1:
                person_bbox = bbox
                px1, py1, px2, py2 = cx1, cy1, cx2, cy2
            person_crop = frame[py1:py2, px1:px2].copy()
            if person_crop.size == 0:
                person_crop = crop
                person_bbox = bbox

            quality_score, sharpness = self._quality_fn(crop, bbox, frame.shape)

            track = state.tracks.get(track_id)
            if track is None:
                track = Track(track_id=track_id, created_timestamp=now, last_seen_timestamp=now)
                state.tracks[track_id] = track

            track.add_observation(
                bbox=bbox,
                crop_bgr=crop,
                person_bbox=person_bbox,
                person_crop_bgr=person_crop,
                quality_score=float(quality_score),
                sharpness=float(sharpness),
                max_buffer_size=self._top_n_frames,
                max_history=max(self._top_n_frames * 4, 20),
                quality_drop_ratio=self._quality_drop_ratio,
            )
            active_ids.add(track_id)

        for track_id, track in list(state.tracks.items()):
            if track_id in active_ids:
                continue
            track.missed_frames += 1
            track.status = "lost"
            if track.missed_frames > self._max_lost_frames:
                del state.tracks[track_id]

        return [state.tracks[track_id] for track_id in sorted(active_ids)]

    def get_active_tracks(self, camera_id: str) -> list[Track]:
        """Return currently active (not lost) tracks for one camera."""
        state = self._camera_states.get(str(camera_id))
        if state is None:
            return []
        return [
            track
            for track in state.tracks.values()
            if track.missed_frames == 0
        ]

    def cleanup_stale(self, max_age_seconds: int = 300) -> int:
        """Remove stale tracks across all cameras and return removal count."""
        now = time.time()
        removed = 0
        max_age = float(max(1, max_age_seconds))

        for camera_id in list(self._camera_states.keys()):
            state = self._camera_states[camera_id]
            for track_id, track in list(state.tracks.items()):
                if (now - track.last_seen_timestamp) > max_age:
                    del state.tracks[track_id]
                    removed += 1

            if not state.tracks:
                del self._camera_states[camera_id]

        return removed

    def camera_diagnostics(self, camera_id: str) -> dict:
        """Return one camera diagnostics payload."""
        active_tracks = self.get_active_tracks(camera_id)
        if not active_tracks:
            return {
                "camera_id": str(camera_id),
                "active_tracks": 0,
                "confirmed_tracks": 0,
                "average_track_age_seconds": 0.0,
            }

        avg_age = sum(track.age_seconds for track in active_tracks) / float(len(active_tracks))
        confirmed = sum(
            1
            for track in active_tracks
            if track.status == "confirmed" and track.identity is not None
        )
        return {
            "camera_id": str(camera_id),
            "active_tracks": int(len(active_tracks)),
            "confirmed_tracks": int(confirmed),
            "average_track_age_seconds": round(float(avg_age), 2),
        }

    def collect_diagnostics(self) -> list[dict]:
        """Return diagnostics for all active camera trackers."""
        return [
            self.camera_diagnostics(camera_id)
            for camera_id in sorted(self._camera_states.keys())
        ]

    @property
    def consistent_match_count(self) -> int:
        return self._consistent_match_count

    @property
    def quality_drop_ratio(self) -> float:
        return self._quality_drop_ratio


tracker_manager = TrackerManager(
    top_n_frames=5,
    max_lost_frames=20,
    frame_rate=30,
    consistent_match_count=3,
    quality_drop_ratio=0.6,
)
