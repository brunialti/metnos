"""Face detection + face embedding service (in-process, lazy-loaded).

Backend: InsightFace `buffalo_l` (open-source).
    - RetinaFace `det_10g.onnx` → bounding box + 5 landmarks per volto
    - ArcFace `w600k_r50.onnx` → embedding 512 dim per volto allineato

Path di default: `<install_root>/models/face/`. Override via env:
`METNOS_FACE_MODEL_DIR`.

Pipeline:
    1. Caricamento immagine (PIL → ndarray RGB).
    2. Resize a 640x640 (lettera-box, mantiene aspetto).
    3. RetinaFace inference: 9 output tensors (confidence/bbox/landmark a
       3 strides 8/16/32, 2 anchor per cella).
    4. Decoding score map + NMS → lista bbox.
    5. Per ogni volto: align con i 5 landmark (similarity transform a
       templato 112x112), passaggio in ArcFace → embedding 512.
    6. L2-normalize → cosine = dot product.

API:
    detect_faces(path | ndarray) -> list[dict]
        ogni dict: {bbox: (x,y,w,h), score, landmarks: list[(x,y)],
                    embedding: ndarray (512,)}.
    match(query_embedding, candidates) -> list[(idx, score)]
        score in [-1, 1], 1 = identico, soglia tipica >= 0.4 per match.

NB: i nomi degli executor che useranno questo modulo sono in discussione e
non vengono fissati qui. Il backend espone API stabili indipendenti dal
naming finale.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "FaceEngine",
    "get_face_engine",
]


def _default_model_dir() -> Path:
    env = os.environ.get("METNOS_FACE_MODEL_DIR")
    if env:
        return Path(env)
    # ADR 0148 rename-resilient: derive from PATH_ROOT.
    import config as _C  # local import to avoid cyclic at module load
    return _C.PATH_ROOT / "models" / "face"


# Template volto allineato (5 landmark standard ArcFace, 112x112)
# Coordinate (x, y) per: occhio sx, occhio dx, naso, bocca sx, bocca dx.
_ARC_TEMPLATE = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


# ── Engine ──────────────────────────────────────────────────────────


class FaceEngine:
    """Face detection (RetinaFace) + face embedding (ArcFace)."""

    name = "face_buffalo_l"

    def __init__(self, model_dir: Optional[Union[str, Path]] = None):
        self._model_dir = Path(model_dir) if model_dir else _default_model_dir()
        self._det_session = None
        self._emb_session = None
        self._available: Optional[bool] = None
        self._load_lock = threading.Lock()
        # RetinaFace config
        self._det_size: int = 640
        self._det_strides: tuple[int, ...] = (8, 16, 32)
        self._det_anchors_per_cell: int = 2
        self._det_confidence_threshold: float = 0.5
        self._nms_iou_threshold: float = 0.4

    @property
    def available(self) -> bool:
        if self._available is None:
            det_ok = (self._model_dir / "det_10g.onnx").exists()
            emb_ok = (self._model_dir / "w600k_r50.onnx").exists()
            self._available = det_ok and emb_ok
            if not self._available:
                logger.warning(
                    "FaceEngine: modello non completo in %s (det=%s emb=%s)",
                    self._model_dir, det_ok, emb_ok,
                )
        return self._available

    @property
    def dimension(self) -> int:
        """Embedding dim (512 per ArcFace w600k_r50)."""
        return 512

    def _load(self) -> None:
        if self._det_session is not None:
            return
        with self._load_lock:
            if self._det_session is not None:
                return
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 2

            det_path = self._model_dir / "det_10g.onnx"
            emb_path = self._model_dir / "w600k_r50.onnx"
            for p in (det_path, emb_path):
                if not p.exists():
                    import config as _C  # ADR 0148 rename-resilient
                    raise FileNotFoundError(
                        f"FaceEngine: file mancante {p}. Esegui "
                        f"{_C.PATH_ROOT / 'install' / 'download_models.sh'} face",
                    )
            self._det_session = ort.InferenceSession(
                str(det_path), sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._emb_session = ort.InferenceSession(
                str(emb_path), sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._det_input_name = self._det_session.get_inputs()[0].name
            self._emb_input_name = self._emb_session.get_inputs()[0].name
            self._emb_output_name = self._emb_session.get_outputs()[0].name
            logger.info("FaceEngine: loaded (det=%s emb=%s)", det_path.name, emb_path.name)

    # ── API: detection + embedding ──────────────────────────────────

    def detect_faces(
        self,
        image: Union[str, Path, np.ndarray],
        *,
        max_faces: int = 0,  # 0 = nessun limite (cap-aware via 0-as-placeholder)
    ) -> list[dict]:
        """Detect + embed di tutti i volti in una singola immagine.

        Ritorna lista di dict: `{bbox, score, landmarks, embedding}`.
        Lista vuota se nessun volto. `bbox` in coordinate immagine
        originale (x, y, w, h).
        """
        self._load()
        img_rgb = self._load_image(image)
        if img_rgb is None:
            return []
        h0, w0 = img_rgb.shape[:2]

        # Letter-box resize a det_size
        det_input, scale, pad_x, pad_y = self._letterbox(img_rgb, self._det_size)

        # RetinaFace forward
        boxes, scores, landmarks = self._det_forward(det_input)
        if len(boxes) == 0:
            return []

        # Scaling indietro alle coordinate originali
        boxes[:, 0::2] = (boxes[:, 0::2] - pad_x) / scale
        boxes[:, 1::2] = (boxes[:, 1::2] - pad_y) / scale
        landmarks[:, :, 0] = (landmarks[:, :, 0] - pad_x) / scale
        landmarks[:, :, 1] = (landmarks[:, :, 1] - pad_y) / scale

        # NMS
        keep = self._nms(boxes, scores, self._nms_iou_threshold)
        boxes = boxes[keep]
        scores = scores[keep]
        landmarks = landmarks[keep]

        if max_faces > 0 and len(boxes) > max_faces:
            order = np.argsort(-scores)[:max_faces]
            boxes = boxes[order]
            scores = scores[order]
            landmarks = landmarks[order]

        results: list[dict] = []
        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]
            x1 = max(0, int(x1)); y1 = max(0, int(y1))
            x2 = min(w0, int(x2)); y2 = min(h0, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            aligned = self._align_face(img_rgb, landmarks[i])
            emb = self._embed_aligned(aligned)
            results.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "score": float(scores[i]),
                "landmarks": [(float(lm[0]), float(lm[1])) for lm in landmarks[i]],
                "embedding": emb,
            })
        return results

    def match(
        self,
        query_embedding: np.ndarray,
        candidates: np.ndarray,
        *,
        threshold: float = 0.4,
    ) -> list[tuple[int, float]]:
        """Cosine similarity tra query e candidates. Assume L2-norm.

        Ritorna lista `(idx, score)` filtrati per soglia, ordinati desc.
        """
        if candidates.ndim == 1:
            candidates = candidates.reshape(1, -1)
        if candidates.size == 0:
            return []
        scores = candidates @ query_embedding
        results = [(i, float(s)) for i, s in enumerate(scores) if s >= threshold]
        results.sort(key=lambda t: -t[1])
        return results

    # ── Helpers privati: detection decoding ─────────────────────────

    def _det_forward(
        self, det_input: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """RetinaFace forward + decoding. Ritorna (boxes, scores, landmarks).

        boxes: (N, 4) in x1,y1,x2,y2.
        scores: (N,).
        landmarks: (N, 5, 2).
        """
        outputs = self._det_session.run(None, {self._det_input_name: det_input})
        # outputs e' lista di 9 tensor; ordine in det_10g:
        # [score_s8, score_s16, score_s32, bbox_s8, bbox_s16, bbox_s32,
        #  kps_s8, kps_s16, kps_s32]
        scores_list = outputs[0:3]
        bboxes_list = outputs[3:6]
        kps_list = outputs[6:9]

        all_boxes: list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_kps: list[np.ndarray] = []

        for i, stride in enumerate(self._det_strides):
            scores = scores_list[i].reshape(-1)
            bbox_preds = bboxes_list[i].reshape(-1, 4) * stride
            kps_preds = kps_list[i].reshape(-1, 10) * stride

            mask = scores >= self._det_confidence_threshold
            if not np.any(mask):
                continue

            # Anchor centers
            n_cells = self._det_size // stride
            anchor_centers = self._make_anchor_centers(
                n_cells, stride, self._det_anchors_per_cell,
            )
            # decode bbox
            x1 = anchor_centers[:, 0] - bbox_preds[:, 0]
            y1 = anchor_centers[:, 1] - bbox_preds[:, 1]
            x2 = anchor_centers[:, 0] + bbox_preds[:, 2]
            y2 = anchor_centers[:, 1] + bbox_preds[:, 3]
            boxes = np.stack([x1, y1, x2, y2], axis=1)

            # decode landmarks (5 punti, dx/dy per ognuno)
            lm = kps_preds.reshape(-1, 5, 2)
            lm[..., 0] += anchor_centers[:, np.newaxis, 0]
            lm[..., 1] += anchor_centers[:, np.newaxis, 1]

            all_boxes.append(boxes[mask])
            all_scores.append(scores[mask])
            all_kps.append(lm[mask])

        if not all_boxes:
            return (np.zeros((0, 4), dtype=np.float32),
                    np.zeros((0,), dtype=np.float32),
                    np.zeros((0, 5, 2), dtype=np.float32))
        return (np.concatenate(all_boxes), np.concatenate(all_scores),
                np.concatenate(all_kps))

    @staticmethod
    def _make_anchor_centers(
        n_cells: int, stride: int, anchors_per_cell: int,
    ) -> np.ndarray:
        """Centro di ogni anchor su griglia n_cells x n_cells."""
        # InsightFace usa np.tile per creare 2 anchor per cella nello stesso
        # punto (col diverso aspect ratio nei layer FPN).
        ax = np.arange(n_cells, dtype=np.float32) * stride
        gy, gx = np.meshgrid(ax, ax, indexing="ij")
        centers = np.stack([gx, gy], axis=2).reshape(-1, 2)
        if anchors_per_cell > 1:
            centers = np.repeat(centers, anchors_per_cell, axis=0)
        return centers

    @staticmethod
    def _nms(
        boxes: np.ndarray, scores: np.ndarray, iou_thr: float,
    ) -> np.ndarray:
        """Soft-NMS classico. Ritorna indici da tenere."""
        if len(boxes) == 0:
            return np.array([], dtype=np.int64)
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou = inter / np.maximum(1e-9, areas[i] + areas[order[1:]] - inter)
            order = order[1:][iou <= iou_thr]
        return np.array(keep, dtype=np.int64)

    # ── Helpers privati: image I/O e align ──────────────────────────

    @staticmethod
    def _load_image(image: Union[str, Path, np.ndarray]) -> Optional[np.ndarray]:
        if isinstance(image, np.ndarray):
            if image.ndim != 3 or image.shape[2] != 3:
                return None
            return image.astype(np.uint8) if image.dtype != np.uint8 else image
        from PIL import Image
        try:
            img = Image.open(str(image)).convert("RGB")
        except Exception as e:
            logger.warning("FaceEngine: impossibile aprire %s: %s", image, e)
            return None
        return np.asarray(img, dtype=np.uint8)

    @staticmethod
    def _letterbox(
        img: np.ndarray, target: int,
    ) -> tuple[np.ndarray, float, float, float]:
        """Resize mantiene aspetto + padding. Ritorna (NCHW, scale, pad_x, pad_y)."""
        from PIL import Image
        h, w = img.shape[:2]
        scale = target / max(h, w)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        resized = np.asarray(
            Image.fromarray(img).resize((new_w, new_h), Image.BILINEAR),
            dtype=np.float32,
        )
        pad_x = (target - new_w) / 2.0
        pad_y = (target - new_h) / 2.0
        out = np.zeros((target, target, 3), dtype=np.float32)
        ox, oy = int(pad_x), int(pad_y)
        out[oy:oy + new_h, ox:ox + new_w, :] = resized
        # InsightFace normalization: (img - 127.5) / 128.0
        out = (out - 127.5) / 128.0
        # HWC → NCHW
        out = np.transpose(out, (2, 0, 1))[np.newaxis, :, :, :].astype(np.float32)
        return out, scale, pad_x, pad_y

    @staticmethod
    def _align_face(img: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        """Allinea il volto a 112x112 con similarity transform sui 5 punti."""
        # Stima T tale che landmarks @ T ≈ template (least squares similarity)
        # Algoritmo classico: Umeyama similarity transform.
        src = landmarks.astype(np.float32)
        dst = _ARC_TEMPLATE
        T = _umeyama_similarity(src, dst)
        # Applica T (3x3) a img → 112x112
        # Per evitare la dipendenza da OpenCV: implementazione pure-numpy
        # con sampling bilineare.
        return _warp_affine(img, T, (112, 112))

    def _embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        """ArcFace embedding di un'immagine 112x112 RGB allineata."""
        # ArcFace input: (1, 3, 112, 112), normalize (img - 127.5) / 127.5
        x = aligned.astype(np.float32)
        x = (x - 127.5) / 127.5
        x = np.transpose(x, (2, 0, 1))[np.newaxis, :, :, :].astype(np.float32)
        out = self._emb_session.run(
            [self._emb_output_name], {self._emb_input_name: x},
        )[0]
        emb = out[0].astype(np.float32)
        # L2-normalize
        norm = np.linalg.norm(emb) + 1e-9
        return emb / norm

    def health(self) -> dict:
        return {
            "available": self.available,
            "loaded": self._det_session is not None,
            "model_dir": str(self._model_dir),
            "dimension": self.dimension,
            "engine": "face_buffalo_l",
        }


# ── Helpers low-level (similarity transform + warp affine) ─────────


def _umeyama_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Similarity transform (rotazione + scala + traslazione) src→dst.

    Ritorna matrice 3x3 affine.
    """
    n = src.shape[0]
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean
    H = src_demean.T @ dst_demean / n
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(U @ Vt))
    R = U @ np.diag([1.0, d]) @ Vt
    var = (src_demean ** 2).sum() / n
    scale = (S[0] + d * S[1]) / max(var, 1e-9)
    T = np.eye(3, dtype=np.float32)
    T[:2, :2] = (scale * R).T
    T[:2, 2] = dst_mean - scale * (R.T @ src_mean)
    return T


def _warp_affine(
    img: np.ndarray, T: np.ndarray, out_size: tuple[int, int],
) -> np.ndarray:
    """Warp affine bilineare pure-numpy. T mappa src→dst (3x3 affine).

    Per ottenere il dst usiamo l'inverso: ogni pixel dst (x', y') →
    src (x, y) via T^-1.
    """
    out_w, out_h = out_size
    Tinv = np.linalg.inv(T)
    # Coords output
    ys, xs = np.mgrid[0:out_h, 0:out_w]
    ones = np.ones_like(xs, dtype=np.float32)
    coords = np.stack([xs.astype(np.float32), ys.astype(np.float32), ones],
                      axis=0).reshape(3, -1)
    src_coords = Tinv @ coords  # (3, N)
    sx = src_coords[0]
    sy = src_coords[1]

    h, w = img.shape[:2]
    # Bilinear sampling
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    x1 = x0 + 1
    y1 = y0 + 1
    x0c = np.clip(x0, 0, w - 1)
    x1c = np.clip(x1, 0, w - 1)
    y0c = np.clip(y0, 0, h - 1)
    y1c = np.clip(y1, 0, h - 1)
    wa = (x1 - sx) * (y1 - sy)
    wb = (sx - x0) * (y1 - sy)
    wc = (x1 - sx) * (sy - y0)
    wd = (sx - x0) * (sy - y0)

    img_f = img.astype(np.float32)
    Ia = img_f[y0c, x0c]
    Ib = img_f[y0c, x1c]
    Ic = img_f[y1c, x0c]
    Id = img_f[y1c, x1c]
    out = (wa[:, None] * Ia + wb[:, None] * Ib +
           wc[:, None] * Ic + wd[:, None] * Id)
    return out.reshape(out_h, out_w, 3).clip(0, 255).astype(np.uint8)


# ── Singleton ───────────────────────────────────────────────────────

_instance: Optional[FaceEngine] = None
_instance_lock = threading.Lock()


def get_face_engine(
    model_dir: Optional[Union[str, Path]] = None,
) -> FaceEngine:
    """Singleton `FaceEngine`. Prima call fissa il model_dir."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = FaceEngine(model_dir)
    return _instance
