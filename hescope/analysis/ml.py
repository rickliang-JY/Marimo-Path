"""Weakly-supervised patch classification from labeled ROIs (SPEC-ML Part B.4).

Training data comes from the HE-Scope database: ROIs with a non-empty
``label`` and an existing ``patch_path`` image. Features are extracted per
patch via ``hescope.analysis.features.extract_features`` (imported lazily so this
module stays importable even before that module exists). The model is a
``StandardScaler`` + ``LogisticRegression`` pipeline persisted with joblib
plus a JSON sidecar under ``models_dir/<name>/``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from ..store.db import AgentRunRepo, ROIRepo

_LOG = logging.getLogger(__name__)

MODEL_FILENAME = "model.joblib"
META_FILENAME = "meta.json"

#: Env var selecting an optional embedding backend from hescope.analysis.embeddings
#: (e.g. "gpfm"). Unset/empty keeps the default handcrafted-feature path.
EMBEDDER_ENV_VAR = "HESCOPE_EMBEDDER"

#: Square raster (px) every HANDCRAFTED feature vector is computed at, at
#: BOTH training and inference time.
#:
#: ``features.extract_features`` is raster-dependent by construction:
#: nuclei_count, nuclei_mean_area_px and blur_score are pixel-geometry
#: quantities. On IDENTICAL pixels resampled to 1024/512/256/128 px,
#: nuclei_mean_area_px measured 4973.7 / 1263.2 / 325.5 / 80.7 -- a 62x
#: spread -- and a patch a model classified as 'sparse' with P(dense)=0.0165
#: at the training raster scored P(dense)=0.5559 as a 256 px tile, i.e. the
#: class flipped on resampling alone. Training patches come from
#: ``rois.extract_patch`` (capped at 1024 px) while heatmap tiles are
#: ``tile`` px wide, so the two rasters NEVER agree by accident on a real
#: WSI -- the model is asked to score a distribution it was never fit on.
#: Normalizing both ends to one raster is what makes them comparable; the
#: value is recorded in the model meta as ``feature_raster`` so a model can
#: state what it was fit at. 256 px is the size features.extract_features
#: documents its "<0.5 s" per-tile budget against.
FEATURE_RASTER = 256


@dataclass
class ModelInfo:
    name: str
    labels: list[str]
    feature_dim: int
    cv_accuracy: float | None
    n_samples: int
    path: str
    created_at: str
    # Embedding-backend metadata (schema backwards compatible: older metas
    # lack these keys and are treated as handcrafted-feature models).
    encoder: str | None = None  # hescope.analysis.embeddings registry name, if used
    warning: str | None = None  # e.g. embedder load failure -> fallback note
    # Raster the handcrafted features were computed at; None for encoder
    # models (the encoder does its own resize) and for pre-R06-1 metas.
    feature_raster: int | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_on_oserror(fn, *, attempts: int = 40, delay: float = 0.005):
    """Call ``fn``, retrying briefly while the OS says "busy".

    The other half of R07-10, and the one that is easy to miss: ``os.replace``
    stops a reader seeing a HALF-WRITTEN model, but on Windows it does not
    make the swap invisible -- a reader that tries to OPEN the destination
    inside the replace window gets ``PermissionError: Permission denied``.
    Measured here, 200 atomic replaces against a tight reader loop produced 69
    such errors in 7121 reads, every one an open failure rather than a corrupt
    parse. ``list_models`` swallows ``OSError``, so without this the model
    would still VANISH from the dropdown and from
    ``get_analysis_capabilities()`` -- silently, which is the outcome that
    matters most here.

    Only ``OSError`` is retried: a malformed ``meta.json`` raises
    ``JSONDecodeError`` and must still be reported as malformed.
    """
    import time

    for attempt in range(attempts):
        try:
            return fn()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _replace_atomically(src: Path, dst: Path, *, attempts: int = 100) -> None:
    """``os.replace`` with a Windows retry, falling back to an in-place copy.

    ``os.replace`` is what makes a re-train invisible to a concurrent reader:
    it swaps the whole file in one step, so ``list_models``/``load_model``
    always see one complete generation or the other (R07-10). On Windows,
    though, it raises ``PermissionError`` if anything currently has the
    DESTINATION open -- which is exactly the reader this is protecting -- so
    without the retry the fix would turn a rare corrupt read into a failed
    training run. Reader handles are held for microseconds, so a short backoff
    clears every collision measured here.

    The final fallback is the old in-place overwrite: a model written
    non-atomically is strictly better than a training run that is lost.
    """
    import time

    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # Windows: destination is open elsewhere
            if attempt == attempts - 1:
                break
            time.sleep(0.005)
    dst.write_bytes(src.read_bytes())
    try:
        os.unlink(src)
    except OSError:
        pass


def _to_feature_raster(img: "Image.Image", raster: int | None) -> "Image.Image":
    """Resample to the square raster handcrafted features are defined at.

    ``raster=None`` (a pre-``feature_raster`` model meta) means "leave the
    image alone": those models were fit on whatever raster their patches
    happened to have, so resizing here would introduce the mismatch this
    guards against rather than remove it.
    """
    if not raster or img.size == (int(raster), int(raster)):
        return img
    return img.resize((int(raster), int(raster)), Image.BILINEAR)


def _load_feature_vector(
    patch_path: str, raster: int | None = FEATURE_RASTER
) -> "np.ndarray":
    from . import features  # lazy: torch/skimage stay out of import time

    with Image.open(patch_path) as im:
        img = _to_feature_raster(im.convert("RGB"), raster)
        vec = np.asarray(features.extract_features(img), dtype=np.float32)
    return vec


def _requested_embedder() -> str | None:
    """``HESCOPE_EMBEDDER`` value, or None when unset/empty (default path)."""
    return os.environ.get(EMBEDDER_ENV_VAR, "").strip() or None


def _compute_training_features(
    patch_paths: list[str],
) -> tuple[list[np.ndarray], str | None, str | None]:
    """Feature vectors for training patches.

    Returns ``(features, encoder_name, warning)``. With ``HESCOPE_EMBEDDER``
    set, patches are embedded in batches via ``hescope.analysis.embeddings``; any
    load/inference failure falls back to the handcrafted path and returns a
    human-readable warning (recorded in the model meta). Default (env unset)
    is the handcrafted path with no warning — existing behavior is unchanged.

    The handcrafted path normalizes every patch to :data:`FEATURE_RASTER`;
    the encoder path does not, because ``Encoder.preprocess`` already
    resizes to the backbone's own input size.
    """
    embedder = _requested_embedder()
    if embedder:
        try:
            from . import embeddings  # lazy: no torch import at module level

            encoder = embeddings.load_encoder(embedder)
            images: list[Image.Image] = []
            for p in patch_paths:
                with Image.open(p) as im:
                    images.append(im.convert("RGB"))
            mat = embeddings.embed_tiles(encoder, images)
            return [row for row in mat], embedder, None
        except Exception as exc:
            warning = (
                f"{EMBEDDER_ENV_VAR}={embedder!r} could not be used ({exc}); "
                "fell back to handcrafted features"
            )
            return [_load_feature_vector(p) for p in patch_paths], None, warning
    return [_load_feature_vector(p) for p in patch_paths], None, None


def train_from_annotations(
    engine,
    *,
    name: str = "default",
    models_dir: str | Path = "data/models",
    min_per_class: int = 2,
    seed: int = 42,
) -> ModelInfo:
    """Train a patch classifier from labeled ROIs in the database.

    Pulls all ROIs via ``ROIRepo(engine)`` (``search(label=None)`` applies no
    label filter), keeps rows with a non-empty label, and loads each row's
    ``patch_path`` image (rows whose patch file is missing are skipped —
    how many, and whether a whole label was lost that way, is recorded in
    ``ModelInfo.warning``).

    Handcrafted feature vectors are computed at :data:`FEATURE_RASTER`,
    recorded as ``feature_raster`` in the meta, and inference resamples to
    the same raster — see :func:`_feature_vector_for_model`.

    Raises ``ValueError`` with a clear message when fewer than 2 distinct
    labels are present or any label has fewer than ``min_per_class`` usable
    samples. Fits StandardScaler + LogisticRegression(max_iter=1000,
    random_state=seed); ``cv_accuracy`` is the mean ``cross_val_score`` with
    ``cv = min(3, min_class_count)`` — when the smallest class has fewer
    than 2 samples (CV impossible) the model is trained without CV and
    ``cv_accuracy`` is None.

    Persists ``model.joblib`` + ``meta.json`` under ``models_dir/<name>/``
    and records an ``agent_runs`` row (tool="train_model"); any database
    hiccup while recording is swallowed so it never fails training.

    Optional embedding backend: when the ``HESCOPE_EMBEDDER`` environment
    variable names an encoder from ``hescope.analysis.embeddings`` (e.g. "gpfm"),
    patch embeddings replace the handcrafted features and the meta records
    the encoder name/dim. If the encoder fails to load, training falls back
    to the handcrafted path and the meta carries a ``warning`` note. With
    the env var unset the behavior is exactly the handcrafted path.
    """
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    rows = ROIRepo(engine).search(label=None)  # label=None -> no filter

    patch_paths: list[str] = []
    labels_list: list[str] = []
    skipped: dict[str, int] = {}  # label -> labelled rows with no usable patch
    for row in rows:
        label = (row.get("label") or "").strip()
        if not label:
            continue
        patch_path = row.get("patch_path")
        if not patch_path or not Path(patch_path).is_file():
            # agent_out/patches/ is scratch output while the `rois` rows are
            # the durable record, so the two drift apart in normal use (a
            # cleanup, a moved repo, a tmp reaper), and a row labelled in the
            # Annotations panel never had a patch at all. A whole class can
            # vanish from the model this way; count it so the meta (and the
            # UI above it) can say so instead of leaving the user to spot a
            # label missing from the list.
            skipped[label] = skipped.get(label, 0) + 1
            continue
        patch_paths.append(patch_path)
        labels_list.append(label)

    features_list, encoder_name, embedder_warning = _compute_training_features(
        patch_paths
    )
    feature_raster = None if encoder_name else FEATURE_RASTER

    labels = sorted(set(labels_list))
    if len(labels) < 2:
        raise ValueError(
            "training requires at least 2 distinct labels on ROIs with "
            f"existing patch images; found {len(labels)} "
            f"({labels or 'none'}). Label ROIs in the Annotations panel first."
        )
    counts = {lab: labels_list.count(lab) for lab in labels}
    too_small = {lab: n for lab, n in counts.items() if n < min_per_class}
    if too_small:
        raise ValueError(
            f"each label needs at least {min_per_class} samples with existing "
            f"patch images; too few for: "
            + ", ".join(f"{lab!r} ({n})" for lab, n in sorted(too_small.items()))
        )

    X = np.stack(features_list).astype(np.float32)
    y = np.array(labels_list)
    min_class_count = min(counts.values())

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=seed)),
        ]
    )

    cv_accuracy: float | None = None
    cv = min(3, min_class_count)
    if cv >= 2:
        try:
            scores = cross_val_score(pipeline, X, y, cv=cv)
            cv_accuracy = float(np.mean(scores))
        except Exception:
            cv_accuracy = None  # tiny/degenerate classes: train without CV

    pipeline.fit(X, y)

    notes: list[str] = []
    if embedder_warning:
        notes.append(embedder_warning)
    if skipped:
        dropped = sorted(set(skipped) - set(labels))
        # "no usable patch image" covers both causes: a row that never had a
        # patch_path (labelled but never sent to the code agent) and one whose
        # file is gone. Both are equally invisible in the trained model.
        note = (
            f"{sum(skipped.values())} labelled ROI(s) skipped (no usable "
            "patch image): "
            + ", ".join(f"{lab!r} ({n})" for lab, n in sorted(skipped.items()))
        )
        if dropped:
            note += (
                "; no usable patch left for "
                + ", ".join(repr(lab) for lab in dropped)
                + " -- that label is NOT in this model"
            )
        notes.append(note)
    warning = "; ".join(notes) or None

    model_dir = Path(models_dir) / name
    model_dir.mkdir(parents=True, exist_ok=True)
    created_at = _utcnow_iso()
    info = ModelInfo(
        name=name,
        labels=labels,
        feature_dim=int(X.shape[1]),
        cv_accuracy=cv_accuracy,
        n_samples=int(X.shape[0]),
        path=str(model_dir),
        created_at=created_at,
        encoder=encoder_name,
        warning=warning,
        feature_raster=feature_raster,
    )
    meta = asdict(info)
    meta["seed"] = seed
    meta["min_per_class"] = min_per_class
    # Written to temp names and os.replace()d in, because re-training under an
    # EXISTING name is the documented workflow and readers run outside this
    # thread: list_models() (the model dropdown and get_analysis_capabilities())
    # and load_model() (the heatmap's model_prob path). Overwriting in place
    # left a window in which meta.json was empty or truncated, so list_models
    # -- which swallows a partial read -- silently reported the model as GONE,
    # and load_model raised EOFError/JSONDecodeError. Measured on one model
    # re-trained 60x while two readers polled: 39 vanishings in 535 list calls
    # and 27 failures in 36 loads (R07-10). os.replace is atomic within a
    # directory, so a reader now sees one complete generation or the other.
    _tmp_model = model_dir / (MODEL_FILENAME + ".tmp")
    _tmp_meta = model_dir / (META_FILENAME + ".tmp")
    joblib.dump(pipeline, _tmp_model)
    with open(_tmp_meta, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    # meta.json last: it is what list_models keys off, so the model file is
    # already complete by the time the directory starts advertising it.
    _replace_atomically(_tmp_model, model_dir / MODEL_FILENAME)
    _replace_atomically(_tmp_meta, model_dir / META_FILENAME)

    summary = (
        f"trained model {name!r}: {info.n_samples} samples, "
        f"labels={labels}, cv_accuracy="
        + (f"{cv_accuracy:.3f}" if cv_accuracy is not None else "n/a")
    )
    if encoder_name:
        summary += f", encoder={encoder_name!r} (dim={info.feature_dim})"
    if warning:
        summary += f", warning: {warning}"
    try:
        AgentRunRepo(engine).record(
            tool="train_model",
            input={"name": name, "labels": labels},
            output_text=summary,
            model=name,
        )
    except Exception as exc:
        # DB hiccup must never fail training (the model files are already
        # durably written above) -- but a lost agent_runs row is a lost
        # provenance record (the harness's "evidence" for this training
        # run), so the degradation must be observable, not silent.
        _LOG.warning(
            "could not record agent_runs provenance for train_model %r: "
            "%s: %s", name, type(exc).__name__, exc,
        )

    return info


def load_model(name: str, models_dir: str | Path = "data/models"):
    """Load a persisted model; returns ``(pipeline, meta dict)``."""
    import joblib

    model_dir = Path(models_dir) / name
    model_path = model_dir / MODEL_FILENAME
    meta_path = model_dir / META_FILENAME
    if not model_path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(
            f"model {name!r} not found under {Path(models_dir)} "
            f"(expected {MODEL_FILENAME} and {META_FILENAME} in {model_dir})"
        )
    # Retried: a concurrent re-train under the same name swaps both files, and
    # on Windows an open inside that window fails outright (R07-10).
    pipeline = _retry_on_oserror(lambda: joblib.load(model_path))
    meta = _retry_on_oserror(
        lambda: json.loads(meta_path.read_text(encoding="utf-8"))
    )
    return pipeline, meta


def _feature_vector_for_model(meta: dict, img: "Image.Image") -> "np.ndarray":
    """Feature vector matching how the model was trained.

    Metas with an ``encoder`` key (trained with ``HESCOPE_EMBEDDER`` set)
    embed via ``hescope.analysis.embeddings``; metas without it (all pre-embedding
    models) use the handcrafted path, keeping old models fully compatible.

    The handcrafted path resamples to the meta's ``feature_raster`` first.
    Heatmap tiles are ``tile`` px while training patches were capped at
    1024 px, and the features are raster-dependent, so scoring a tile at its
    own raster feeds the classifier a distribution it was never fit on
    (R06-1). Metas without the key predate the normalization and are scored
    exactly as before.
    """
    encoder_name = meta.get("encoder")
    if encoder_name:
        from . import embeddings  # lazy: no torch import at module level

        encoder = embeddings.load_encoder(encoder_name)
        return embeddings.embed_tiles(encoder, [img.convert("RGB")])[0]
    from . import features  # lazy: torch/skimage stay out of import time

    tile = _to_feature_raster(img.convert("RGB"), meta.get("feature_raster"))
    return np.asarray(features.extract_features(tile), dtype=np.float32)


def predict_patch(model, meta: dict, img: "Image.Image") -> dict:
    """``{label: probability}`` for a patch image, sorted by prob desc."""
    vec = _feature_vector_for_model(meta, img).reshape(1, -1)
    proba = model.predict_proba(vec)[0]
    classes = [str(c) for c in model.classes_]
    return dict(sorted(zip(classes, (float(p) for p in proba)), key=lambda kv: -kv[1]))


def make_prob_metric(model, meta: dict, label: str):
    """Return ``metric_fn(pil_tile) -> float`` = P(label), for heatmap use."""
    classes = [str(c) for c in model.classes_]
    if label not in classes:
        raise ValueError(
            f"label {label!r} not in model classes {classes}"
        )
    idx = classes.index(label)

    def metric(pil_tile: "Image.Image") -> float:
        probs = predict_patch(model, meta, pil_tile)
        return float(min(1.0, max(0.0, probs[label])))

    metric.__name__ = f"prob_{label}"
    return metric


def list_models(models_dir: str | Path = "data/models") -> list[dict]:
    """List persisted models (meta dicts) under ``models_dir``, sorted by name."""
    root = Path(models_dir)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        meta_path = child / META_FILENAME
        if child.is_dir() and meta_path.is_file():
            try:
                # Retried on OSError only: a re-train swapping meta.json under
                # us must not read as "this model does not exist" (R07-10),
                # while a genuinely malformed meta still falls through to the
                # skip below.
                meta = _retry_on_oserror(
                    lambda: json.loads(meta_path.read_text(encoding="utf-8"))
                )
                if isinstance(meta, dict):
                    out.append(meta)
            except (json.JSONDecodeError, OSError):
                continue
    return out
