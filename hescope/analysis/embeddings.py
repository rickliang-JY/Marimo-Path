"""Pathology foundation-model encoder factory (docs/STRATEGY.md §2f).

A thin registry + lazy loader for tile-level encoders:

* ``EncoderSpec`` / ``ENCODERS`` — static metadata (name, HF repo, embedding
  dim, license, commercial-use and gating flags, loader description). This is
  importable with zero heavy deps: importing this module never touches torch,
  timm, huggingface_hub or the network.
* ``default_encoder_name()`` — license red line: only encoders that are both
  ``commercial_ok`` and non-gated are eligible (GPFM preferred).
* ``load_encoder(name)`` — lazily imports torch/torchvision/timm and loads
  weights only when called. Any failure raises ``RuntimeError`` with explicit
  remediation guidance (dependency install, HF gating/token, license notes).
* ``embed_tiles(encoder, pil_images, batch_size=32)`` — batched no_grad
  inference, CPU/GPU auto.

Model choices follow r2-foundation-models.md / docs/STRATEGY.md §2f: GPFM (MIT) is the
platform default; UNI2-h is registered for academic comparison only
(CC-BY-NC-ND-4.0, never a default); H-optimus-0 (Apache-2.0) is the
commercially-safe alternative; ResNet18(ImageNet) is the local fallback with
no license concerns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

# ImageNet normalization shared by the ResNet18 fallback (mirrors
# ``hescope.analysis.features.extract_embedding``).
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class EncoderSpec:
    """Static metadata for one registered encoder."""

    name: str
    hf_id: str | None  # Hugging Face repo id; None for purely local encoders
    embedding_dim: int
    license: str  # SPDX-style license id of the weights
    commercial_ok: bool  # license permits commercial use / platform distribution
    gated: bool  # HF repo requires access request / auth token
    loader: str  # human-readable description of how the weights are loaded


ENCODERS: dict[str, EncoderSpec] = {
    # GPFM (HKUST, MIT) — platform default. HF repo id per
    # research/r2-foundation-models.md and docs/STRATEGY.md §2f (majiabo/GPFM).
    "gpfm": EncoderSpec(
        name="gpfm",
        hf_id="majiabo/GPFM",
        embedding_dim=1024,
        license="MIT",
        commercial_ok=True,
        gated=False,
        loader="timm.create_model('hf-hub:majiabo/GPFM', pretrained=True)",
    ),
    # UNI2-h (Mahmood lab) — academic comparison only; CC-BY-NC-ND and gated,
    # so it must NEVER become a default (license red line, docs/STRATEGY.md §2f).
    "uni2h": EncoderSpec(
        name="uni2h",
        hf_id="mahmoodlab/UNI2-h",
        embedding_dim=1536,
        license="CC-BY-NC-ND-4.0",
        commercial_ok=False,
        gated=True,
        loader=(
            "timm.create_model('hf-hub:mahmoodlab/UNI2-h', pretrained=True, "
            "init_values=1e-5, dynamic_img_size=True)"
        ),
    ),
    # H-optimus-0 (Bioptimus, Apache-2.0) — commercially safe alternative.
    "hoptimus0": EncoderSpec(
        name="hoptimus0",
        hf_id="bioptimus/H-optimus-0",
        embedding_dim=1536,
        license="Apache-2.0",
        commercial_ok=True,
        gated=False,
        loader=(
            "timm.create_model('hf-hub:bioptimus/H-optimus-0', pretrained=True, "
            "init_values=1e-5, dynamic_img_size=True)"
        ),
    ),
    # Local fallback: ResNet18(ImageNet) avgpool embedding — same construction
    # as ``hescope.analysis.features.extract_embedding`` (torchvision weights, fc
    # replaced by Identity). No HF repo, no license concerns.
    "resnet18": EncoderSpec(
        name="resnet18",
        hf_id=None,
        embedding_dim=512,
        license="BSD-3-Clause",
        commercial_ok=True,
        gated=False,
        loader="torchvision.models.resnet18(weights=IMAGENET1K_V1), fc=Identity",
    ),
}

_PREFERRED_DEFAULT = "gpfm"


def list_encoders() -> list[dict]:
    """All registered encoder metadata as plain dicts (JSON-serializable).

    Intended for the agent ``analysis_capabilities`` report.
    """
    return [asdict(spec) for spec in ENCODERS.values()]


def default_encoder_name() -> str:
    """Default encoder under the license red line.

    Only encoders that are both ``commercial_ok`` and non-gated are eligible;
    GPFM is preferred when present. Raises ``RuntimeError`` if the registry
    ever ends up with no license-safe encoder.
    """
    eligible = [
        spec for spec in ENCODERS.values() if spec.commercial_ok and not spec.gated
    ]
    for spec in eligible:
        if spec.name == _PREFERRED_DEFAULT:
            return spec.name
    if eligible:
        return eligible[0].name
    raise RuntimeError(  # pragma: no cover - registry misconfiguration guard
        "no license-safe encoder registered (need commercial_ok and non-gated)"
    )


class Encoder:
    """A loaded encoder: spec + torch model + preprocessing, ready to embed.

    ``preprocess`` maps a PIL image to a CHW float tensor; ``embed_batch``
    runs a list of PIL images through the model under ``no_grad`` and returns
    an ``(n, embedding_dim)`` float32 array.
    """

    def __init__(self, spec: EncoderSpec, model, preprocess, torch, device) -> None:
        self.spec = spec
        self.model = model
        self.preprocess = preprocess
        self._torch = torch
        self._device = device

    @property
    def embedding_dim(self) -> int:
        return self.spec.embedding_dim

    def embed_batch(self, pil_images) -> np.ndarray:
        torch = self._torch
        tensors = [self.preprocess(im.convert("RGB")) for im in pil_images]
        batch = torch.stack(tensors).to(self._device)
        with torch.no_grad():
            out = self.model(batch)
        return out.detach().cpu().numpy().astype(np.float32)


def _torch_device(torch):
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _load_resnet18(spec: EncoderSpec) -> Encoder:
    """ResNet18(ImageNet) fallback — same weights/construction as
    ``hescope.analysis.features.extract_embedding`` (fc -> Identity, 224px, ImageNet
    normalization), exposed here in batchable form."""
    try:
        import torch
        import torchvision
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(
            "encoder 'resnet18' requires torch and torchvision; install them "
            "with: pip install torch torchvision"
        ) from exc
    try:
        weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
        model = torchvision.models.resnet18(weights=weights)
    except Exception as exc:
        raise RuntimeError(
            "failed to load ResNet18 ImageNet weights (first use downloads "
            f"them from the torchvision model hub; check network/proxy): {exc}"
        ) from exc
    model.fc = torch.nn.Identity()
    model.eval()
    device = _torch_device(torch)
    model.to(device)

    mean = np.asarray(_IMAGENET_MEAN, dtype=np.float32)
    std = np.asarray(_IMAGENET_STD, dtype=np.float32)

    def preprocess(img):
        from PIL import Image as _PILImage

        rgb = img.convert("RGB").resize((224, 224), _PILImage.BILINEAR)
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        arr = (arr - mean) / std
        return torch.from_numpy(arr.transpose(2, 0, 1))

    return Encoder(spec, model, preprocess, torch, device)


def _load_timm_encoder(spec: EncoderSpec) -> Encoder:
    """Generic timm ``hf-hub:`` loader for the ViT foundation models."""
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(
            f"encoder {spec.name!r} requires torch; install it with: "
            "pip install torch"
        ) from exc
    try:
        import timm
    except ImportError as exc:
        raise RuntimeError(
            f"encoder {spec.name!r} ({spec.hf_id}) requires timm and "
            "huggingface_hub; install them with: "
            "pip install timm huggingface_hub"
        ) from exc
    try:
        model = timm.create_model(
            f"hf-hub:{spec.hf_id}",
            pretrained=True,
            init_values=1e-5,
            dynamic_img_size=True,
        )
        data_cfg = timm.data.resolve_model_data_config(model)
        transform = timm.data.create_transform(**data_cfg, is_training=False)
    except Exception as exc:
        hints = [
            f"failed to load encoder {spec.name!r} from Hugging Face "
            f"({spec.hf_id}): {exc}"
        ]
        if spec.gated:
            hints.append(
                f"this model is GATED: request access at "
                f"https://huggingface.co/{spec.hf_id} and authenticate with "
                "`huggingface-cli login` or the HF_TOKEN environment variable"
            )
        else:
            hints.append(
                "check network/proxy access to huggingface.co; the first "
                "load downloads the weights"
            )
        hints.append(f"weight license: {spec.license}")
        raise RuntimeError("; ".join(hints)) from exc
    model.eval()
    device = _torch_device(torch)
    model.to(device)

    def preprocess(img):
        return transform(img.convert("RGB"))

    return Encoder(spec, model, preprocess, torch, device)


def load_encoder(name: str) -> Encoder:
    """Load encoder ``name`` (lazy: heavy imports/weight loads happen here).

    Raises ``RuntimeError`` with explicit remediation guidance on any failure
    (missing deps -> pip install hint; gated repo -> HF access/token hint;
    download failure -> network hint). Unknown names raise ``ValueError``
    listing the registry.
    """
    spec = ENCODERS.get(name)
    if spec is None:
        raise ValueError(
            f"unknown encoder {name!r}; available: {sorted(ENCODERS)} "
            "(see hescope.analysis.embeddings.list_encoders())"
        )
    if name == "resnet18":
        return _load_resnet18(spec)
    return _load_timm_encoder(spec)


def embed_tiles(encoder, pil_images, batch_size: int = 32) -> np.ndarray:
    """Batch-embed PIL tiles; returns ``(n, embedding_dim)`` float32.

    ``encoder`` is anything with an ``embed_batch(list[PIL.Image])`` method
    (a loaded ``Encoder`` or a test stub). Batching keeps memory bounded;
    CPU/GPU placement is decided by the encoder at load time.
    """
    images = list(pil_images)
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if not images:
        dim = getattr(encoder, "embedding_dim", 0)
        return np.zeros((0, int(dim)), dtype=np.float32)
    chunks = [
        encoder.embed_batch(images[i : i + batch_size])
        for i in range(0, len(images), batch_size)
    ]
    return np.concatenate(chunks, axis=0).astype(np.float32)
