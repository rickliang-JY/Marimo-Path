"""Weakly-supervised patch classification from labeled ROIs (SPEC-ML Part B.4).

Training data comes from the HE-Scope database: ROIs with a non-empty
``label`` and an existing ``patch_path`` image. Features are extracted per
patch via ``hescope.features.extract_features`` (imported lazily so this
module stays importable even before that module exists). The model is a
``StandardScaler`` + ``LogisticRegression`` pipeline persisted with joblib
plus a JSON sidecar under ``models_dir/<name>/``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from .db import AgentRunRepo, ROIRepo

MODEL_FILENAME = "model.joblib"
META_FILENAME = "meta.json"

#: Env var selecting an optional embedding backend from hescope.embeddings
#: (e.g. "gpfm"). Unset/empty keeps the default handcrafted-feature path.
EMBEDDER_ENV_VAR = "HESCOPE_EMBEDDER"


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
    encoder: str | None = None  # hescope.embeddings registry name, if used
    warning: str | None = None  # e.g. embedder load failure -> fallback note


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_feature_vector(patch_path: str) -> "np.ndarray":
    from hescope import features  # lazy: hescope.features may not exist yet

    with Image.open(patch_path) as im:
        img = im.convert("RGB")
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
    set, patches are embedded in batches via ``hescope.embeddings``; any
    load/inference failure falls back to the handcrafted path and returns a
    human-readable warning (recorded in the model meta). Default (env unset)
    is the handcrafted path with no warning — existing behavior is unchanged.
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
    ``patch_path`` image (rows whose patch file is missing are skipped).

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
    variable names an encoder from ``hescope.embeddings`` (e.g. "gpfm"),
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
    for row in rows:
        label = (row.get("label") or "").strip()
        if not label:
            continue
        patch_path = row.get("patch_path")
        if not patch_path or not Path(patch_path).is_file():
            continue
        patch_paths.append(patch_path)
        labels_list.append(label)

    features_list, encoder_name, embedder_warning = _compute_training_features(
        patch_paths
    )

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
        warning=embedder_warning,
    )
    joblib.dump(pipeline, model_dir / MODEL_FILENAME)
    meta = asdict(info)
    meta["seed"] = seed
    meta["min_per_class"] = min_per_class
    with open(model_dir / META_FILENAME, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    summary = (
        f"trained model {name!r}: {info.n_samples} samples, "
        f"labels={labels}, cv_accuracy="
        + (f"{cv_accuracy:.3f}" if cv_accuracy is not None else "n/a")
    )
    if encoder_name:
        summary += f", encoder={encoder_name!r} (dim={info.feature_dim})"
    if embedder_warning:
        summary += f", warning: {embedder_warning}"
    try:
        AgentRunRepo(engine).record(
            tool="train_model",
            input={"name": name, "labels": labels},
            output_text=summary,
            model=name,
        )
    except Exception:
        pass  # DB hiccup must never fail training

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
    pipeline = joblib.load(model_path)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    return pipeline, meta


def _feature_vector_for_model(meta: dict, img: "Image.Image") -> "np.ndarray":
    """Feature vector matching how the model was trained.

    Metas with an ``encoder`` key (trained with ``HESCOPE_EMBEDDER`` set)
    embed via ``hescope.embeddings``; metas without it (all pre-embedding
    models) use the handcrafted path, keeping old models fully compatible.
    """
    encoder_name = meta.get("encoder")
    if encoder_name:
        from . import embeddings  # lazy: no torch import at module level

        encoder = embeddings.load_encoder(encoder_name)
        return embeddings.embed_tiles(encoder, [img.convert("RGB")])[0]
    from hescope import features  # lazy: hescope.features may not exist yet

    return np.asarray(
        features.extract_features(img.convert("RGB")), dtype=np.float32
    )


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
                with open(meta_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
                if isinstance(meta, dict):
                    out.append(meta)
            except (json.JSONDecodeError, OSError):
                continue
    return out
