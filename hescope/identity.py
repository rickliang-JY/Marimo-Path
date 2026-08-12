"""Slide identity that does not assume a local, permanently-mounted file.

``slides.path`` used to be the only key a slide had, which conflates two
different questions: WHERE is this file on THIS machine, and WHAT slide is
it. Conflating them is why the same content seen at three paths became three
separate slide rows (defect 1.1 in ``docs/BUILD-PLAN-DB.md`` Phase 1) and why
a database on a shared network store split one slide per workstation (defect
1.3, fixed in ``hescope.db.normalize_slide_path`` -- see that function's
docstring). ``slide_identity`` answers the second question, independent of
where the file currently sits: a content hash for a readable local/mounted
file, or a caller-supplied DICOMweb ``(study_uid, series_uid)`` / OMERO
``image_id`` for a slide that has no local path worth hashing at all.

This module has no dependency on ``hescope.db`` -- and never should -- so
that ``hescope.db`` (and any future migration) can depend on it rather than
the other way around; ``normalize_slide_path`` stays in ``hescope.db``,
which is why the ``'path'`` fallback described below is the CALLER's job
(``SlideRepo.register``), not this module's.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Bytes hashed from the head, and (if the file is larger) the tail, of the
#: file -- in addition to its size. Not a whole-file hash.
CONTENT_KEY_HEAD = 1 << 20  # 1 MiB


def content_key(path: str | Path) -> tuple[str, int] | None:
    """``(sha256(size || head 1MiB || tail 1MiB), size)`` for a local file.

    Deliberately not a whole-file hash: measured 0.002 s on a 177 MB slide
    against 0.17 s (85x slower) for ``hashlib.sha256`` over the entire file.
    A whole-slide image's identity is fully determined by its size plus a few
    hundred KB at each end for every pyramid format this project reads
    (SVS/TIFF write their directory/tags at the head or tail; two files that
    agree on size and both ends but differ invisibly in the middle would be a
    corrupt read, not a legitimate different slide).

    Returns ``None`` if ``path`` cannot be statted or read: missing,
    permission denied, or a directory -- callers treat that as "no content
    identity available", not as an error.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    h = hashlib.sha256(str(size).encode("ascii"))
    try:
        with p.open("rb") as f:
            h.update(f.read(CONTENT_KEY_HEAD))
            if size > CONTENT_KEY_HEAD:
                f.seek(max(0, size - CONTENT_KEY_HEAD))
                h.update(f.read(CONTENT_KEY_HEAD))
    except OSError:
        return None
    return h.hexdigest(), size


def slide_identity(
    source_kind: str, path: str | Path | None = None, **kw: object
) -> tuple[str, str] | None:
    """Resolve ``(scheme, key)`` identity for a slide from what is available.

    ``source_kind`` is accepted for forward compatibility (a future source
    may need it to disambiguate ``kw``) but today only ``kw`` and ``path``
    decide the outcome:

    * ``study_uid`` and ``series_uid`` in ``kw`` (both truthy) ->
      ``('dicomweb', f'{study_uid}/{series_uid}')``.
    * ``image_id`` in ``kw`` (not None) -> ``('omero', str(image_id))``.
    * otherwise, a ``path`` that :func:`content_key` can read ->
      ``('sha256', <hex digest>)``.
    * otherwise, ``None``.

    ``'path'`` is deliberately never produced HERE -- it is the caller's
    last resort (``hescope.db.SlideRepo.register`` falls back to
    ``('path', normalize_slide_path(path))`` itself), never this function's,
    so that a readable local file always gets a real content identity and
    never silently settles for a location-based one just because this
    function tried to be helpful.
    """
    study_uid = kw.get("study_uid")
    series_uid = kw.get("series_uid")
    if study_uid and series_uid:
        return ("dicomweb", f"{study_uid}/{series_uid}")
    image_id = kw.get("image_id")
    if image_id is not None:
        return ("omero", str(image_id))
    if path is not None:
        ck = content_key(path)
        if ck is not None:
            return ("sha256", ck[0])
    return None
