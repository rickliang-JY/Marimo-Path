"""Prototype of DATABASE-DESIGN.md steps 2-4, on a COPY of the live database.

Proves three things the current schema cannot do:
  1. one slide identified by content, with many file locations;
  2. a slide joined to its case/sample (the join that has 0 rows today);
  3. measurements aggregated in SQL, filtered by the resolution they were
     taken at.

Writes only to scratchpad/proto.db. The live data/hescope.db is never opened
for writing.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sqlite3

SRC = pathlib.Path("data/hescope.db")
DST = pathlib.Path("scratchpad/proto.db")


def content_key(path: pathlib.Path) -> tuple[str, int] | None:
    """sha256(size || head 1MiB || tail 1MiB) — 0.002 s on a 177 MB slide."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > (1 << 20):
            f.seek(max(0, size - (1 << 20)))
            h.update(f.read(1 << 20))
    return h.hexdigest(), size


def full_md5(path: pathlib.Path) -> str | None:
    try:
        h = hashlib.md5()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main() -> None:
    DST.unlink(missing_ok=True)
    shutil.copy2(SRC, DST)
    db = sqlite3.connect(DST)
    db.execute("PRAGMA foreign_keys=ON")

    # ---- step 2: content identity + many-paths-one-slide -------------------
    db.executescript("""
        CREATE TABLE slides_v2 (
          id INTEGER PRIMARY KEY, content_key TEXT NOT NULL UNIQUE,
          file_size INTEGER NOT NULL, md5sum TEXT, sample_id INTEGER,
          name TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
          mpp REAL);
        CREATE TABLE slide_files (
          id INTEGER PRIMARY KEY,
          slide_id INTEGER NOT NULL REFERENCES slides_v2(id) ON DELETE CASCADE,
          path TEXT NOT NULL UNIQUE, source_kind TEXT NOT NULL,
          missing_since TEXT);
    """)

    old_to_new: dict[int, int] = {}
    key_to_new: dict[str, int] = {}
    missing = 0
    for sid, kind, name, path, w, h, mpp in db.execute(
        "SELECT id,source_kind,name,path,width,height,mpp FROM slides"
    ).fetchall():
        p = pathlib.Path(path)
        ck = content_key(p)
        if ck is None:                      # the 18 dead paths
            missing += 1
            ck = (f"missing:{path}", 0)     # keeps the row addressable
        key, size = ck
        if key not in key_to_new:
            cur = db.execute(
                "INSERT INTO slides_v2(content_key,file_size,name,width,height,mpp)"
                " VALUES (?,?,?,?,?,?)", (key, size, name, w, h, mpp))
            key_to_new[key] = cur.lastrowid
        new_id = key_to_new[key]
        old_to_new[sid] = new_id
        db.execute(
            "INSERT OR IGNORE INTO slide_files(slide_id,path,source_kind,missing_since)"
            " VALUES (?,?,?,?)",
            (new_id, path, kind, None if size else "2026-08-10"))

    print(f"slides   {len(old_to_new):>3} rows  ->  {len(key_to_new)} distinct slides"
          f"  ({missing} paths no longer resolve)")

    # ---- step 3: the join that has 0 rows today ---------------------------
    db.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY, source TEXT NOT NULL,
          external_id TEXT, name TEXT, primary_site TEXT,
          UNIQUE(source, external_id));
        CREATE TABLE cases (id INTEGER PRIMARY KEY,
          project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          source TEXT NOT NULL, external_id TEXT, UNIQUE(source, external_id));
        CREATE TABLE samples (id INTEGER PRIMARY KEY,
          case_id INTEGER REFERENCES cases(id) ON DELETE CASCADE,
          source TEXT NOT NULL, external_id TEXT, submitter_id TEXT,
          sample_type TEXT, UNIQUE(source, external_id));
    """)
    db.execute("INSERT INTO projects(source,external_id,name,primary_site)"
               " SELECT 'tcga',project_id,name,primary_site FROM tcga_projects")
    db.execute("INSERT INTO cases(project_id,source,external_id)"
               " SELECT p.id,'tcga',c.submitter_id FROM tcga_cases c"
               " LEFT JOIN projects p ON p.external_id=c.project_id AND p.source='tcga'")
    db.execute("INSERT INTO samples(case_id,source,external_id,submitter_id,sample_type)"
               " SELECT c.id,'tcga',s.sample_id,s.submitter_id,s.sample_type"
               " FROM tcga_samples s LEFT JOIN cases c"
               " ON c.external_id=s.case_submitter_id AND c.source='tcga'")

    # backfill slides.sample_id via the md5 that was already in the database
    linked = 0
    for path, new_id in db.execute(
        "SELECT f.path, f.slide_id FROM slide_files f").fetchall():
        p = pathlib.Path(path)
        if not p.exists() or p.suffix.lower() not in (".svs", ".tif", ".tiff", ".ndpi"):
            continue
        md5 = full_md5(p)
        row = db.execute(
            "SELECT s.id FROM tcga_files tf JOIN samples s"
            " ON s.external_id = tf.sample_id AND s.source='tcga'"
            " WHERE tf.md5sum = ?", (md5,)).fetchone()
        if row:
            db.execute("UPDATE slides_v2 SET sample_id=?, md5sum=? WHERE id=?",
                       (row[0], md5, new_id))
            linked += 1
    print(f"slides linked to a sample via md5sum: {linked}"
          f"   (live database today: 0)")

    # ---- step 4: measurements as rows -------------------------------------
    db.executescript("""
        CREATE TABLE measurements (id INTEGER PRIMARY KEY,
          annotation_id INTEGER NOT NULL REFERENCES rois(id) ON DELETE CASCADE,
          name TEXT NOT NULL, value REAL, unit TEXT, method TEXT NOT NULL,
          mpp_effective REAL, params_json TEXT NOT NULL DEFAULT '{}');
    """)
    n = 0
    for rid, sj, bbox_json, sl in db.execute(
        "SELECT id,stats_json,bbox_json,slide_id FROM rois").fetchall():
        if not sj:
            continue
        try:
            st = json.loads(sj)
            bx = json.loads(bbox_json)
        except (TypeError, ValueError):
            continue
        mpp = db.execute("SELECT mpp FROM slides WHERE id=?", (sl,)).fetchone()[0]
        # THE comparability key: level-0 mpp scaled by the extraction downsample
        eff = None
        if mpp and st.get("width_px"):
            eff = float(mpp) * max(1.0, (bx[2] - bx[0]) / float(st["width_px"]))
        flat = {"tissue_fraction": (st.get("tissue_fraction"), "")}
        he = st.get("he_deconvolution") or {}
        flat["hematoxylin_mean"] = (he.get("hematoxylin_mean"), "")
        flat["eosin_mean"] = (he.get("eosin_mean"), "")
        for name, (val, unit) in flat.items():
            if val is None:
                continue
            db.execute(
                "INSERT INTO measurements(annotation_id,name,value,unit,method,mpp_effective)"
                " VALUES (?,?,?,?,?,?)", (rid, name, float(val), unit, "roi_stats@v1", eff))
            n += 1
    print(f"measurements extracted from stats_json blobs: {n} rows")
    db.commit()

    # ---- the three questions that are unanswerable today ------------------
    print("\n--- Q1  one slide, every location it has been seen at ---")
    for r in db.execute("""
        SELECT s.id, s.name, count(f.id) files,
               sum(f.missing_since IS NOT NULL) gone
          FROM slides_v2 s JOIN slide_files f ON f.slide_id=s.id
         GROUP BY s.id HAVING files>1 ORDER BY files DESC LIMIT 3"""):
        print(f"    slide {r[0]:<3} {r[1][:34]:<34} {r[2]} paths, {r[3]} missing")

    print("\n--- Q2  which case/sample is this slide from? ---")
    for r in db.execute("""
        SELECT s.name, sm.submitter_id, sm.sample_type, c.external_id, p.external_id
          FROM slides_v2 s JOIN samples sm ON sm.id=s.sample_id
          JOIN cases c ON c.id=sm.case_id LEFT JOIN projects p ON p.id=c.project_id"""):
        print(f"    {r[0][:30]:<30} sample={r[1]} ({r[2]})  case={r[3]}  project={r[4]}")

    print("\n--- Q3  comparable measurements only, aggregated in SQL ---")
    for r in db.execute("""
        SELECT m.name, count(*) n, round(avg(m.value),4) mean,
               round(min(m.mpp_effective),3) mpp_lo, round(max(m.mpp_effective),3) mpp_hi
          FROM measurements m JOIN rois a ON a.id=m.annotation_id
         WHERE a.slide_id=2 GROUP BY m.name"""):
        print(f"    {r[0]:<18} n={r[1]}  mean={r[2]}   mpp {r[3]} .. {r[4]}")
    print("    ^ the mpp spread is now VISIBLE, and filterable in a WHERE clause")
    db.close()


if __name__ == "__main__":
    main()
