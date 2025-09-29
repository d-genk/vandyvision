from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# -----------------------------
# Data structures
# -----------------------------

@dataclass(frozen=True)
class RatingRecord:
    path: Path
    stars: Optional[int]                # 0..5 or None if unrated
    xmp_rating: Optional[int]           # raw XMP-xmp:Rating (0..5) if present
    ms_rating: Optional[int]            # raw MicrosoftPhoto:Rating (0..5) if present
    ms_rating_percent: Optional[int]    # raw MicrosoftPhoto:RatingPercent (0..99/100) if present


@dataclass(frozen=True)
class FilterResult:
    selected: List[Path]                # ordered list of files that meet criteria (+/- unrated per flag)
    cutoff_stars: Optional[int]         # percentile cutoff (None if no rated files)
    rated: List[RatingRecord]
    unrated: List[RatingRecord]
    stats: Dict[str, int]               # counts summary


# -----------------------------
# File collection
# -----------------------------

def collect_files(
    folder: Path | str,
    recursive: bool = True,
    extensions: Optional[Sequence[str]] = None,
) -> List[Path]:
    """
    Return a list of files under `folder`.
    `extensions` are matched case-insensitively without the leading dot (e.g., ["jpg","jpeg"]).
    """
    root = Path(folder)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    it: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in it if p.is_file()]

    if extensions:
        extset = {e.lower().lstrip(".") for e in extensions}
        files = [p for p in files if p.suffix.lower().lstrip(".") in extset]

    return files


# -----------------------------
# ExifTool integration
# -----------------------------

def _stars_from_percent(pct: Optional[int]) -> Optional[int]:
    """Map MicrosoftPhoto RatingPercent (0..99/100) to 0..5 stars."""
    if pct is None:
        return None
    try:
        pct = int(pct)
    except Exception:
        return None
    if pct <= 0:
        return 0
    if pct >= 99:     # Windows commonly uses {1,25,50,75,99}
        return 5
    cuts  = [1, 25, 50, 75, 99]
    stars = [1,  2,  3,  4,  5]
    idx = min(range(len(cuts)), key=lambda i: abs(cuts[i] - pct))
    return stars[idx]


def _normalize_stars(exiftool_row: Dict[str, object]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """
    Given one JSON dict from exiftool, extract rating as 0..5 stars.
    Returns (stars, xmp_rating, ms_rating, ms_rating_percent)
    """
    # ExifTool keys with -s -s -s:
    #   "Rating"                          (XMP-xmp:Rating)
    #   "MicrosoftPhoto:Rating"
    #   "MicrosoftPhoto:RatingPercent"
    xmp_rating = exiftool_row.get("Rating")
    ms_rating = exiftool_row.get("MicrosoftPhoto:Rating")
    ms_percent = exiftool_row.get("MicrosoftPhoto:RatingPercent")

    def _as_int(val) -> Optional[int]:
        if val is None:
            return None
        try:
            return int(val)
        except Exception:
            return None

    xr = _as_int(xmp_rating)
    mr = _as_int(ms_rating)
    mp = _as_int(ms_percent)

    stars: Optional[int] = None
    for v in (xr, mr):
        if v is not None:
            stars = max(0, min(5, v))
            break
    if stars is None:
        stars = _stars_from_percent(mp)

    return stars, xr, mr, mp


def read_ratings_with_exiftool(files: Sequence[Path]) -> List[RatingRecord]:
    """
    Batch-reads ratings using ExifTool.
    Requires `exiftool` available in PATH.
    """
    if not files:
        return []

    if shutil.which("exiftool") is None:
        raise RuntimeError("exiftool not found in PATH")

    cmd = [
        "exiftool", "-j", "-n", "-s", "-s", "-s",
        "-XMP-xmp:Rating",
        "-XMP-MicrosoftPhoto:Rating",
        "-XMP-MicrosoftPhoto:RatingPercent",
        *map(str, files),
    ]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    rows = json.loads(out)

    records: List[RatingRecord] = []
    for row in rows:
        src = row.get("SourceFile")
        if not src:  # defensive: should always be present
            continue
        stars, xr, mr, mp = _normalize_stars(row)
        records.append(
            RatingRecord(
                path=Path(src),
                stars=stars,
                xmp_rating=xr,
                ms_rating=mr,
                ms_rating_percent=mp,
            )
        )
    return records


# -----------------------------
# Percentile + filtering
# -----------------------------

def percentile_nearest_rank(values: Sequence[int], q: float) -> int:
    """
    Nearest-rank percentile (inclusive). q in [0,1].
    For n values, returns the value at rank ceil(q * n) (1-indexed).
    """
    if not values:
        raise ValueError("percentile_nearest_rank requires a non-empty sequence")
    q = min(1.0, max(0.0, float(q)))
    vals = sorted(values)
    n = len(vals)
    rank = max(1, math.ceil(q * n))
    return vals[rank - 1]


def filter_images_by_rating(
    folder: Path | str,
    percentile: float = 0.5,
    include_unrated_in_result: bool = True,
    recursive: bool = True,
    extensions: Optional[Sequence[str]] = None,
) -> FilterResult:
    """
    - Scans `folder` for images.
    - Reads ratings with ExifTool.
    - Computes the percentile cutoff on *rated* images only.
    - Returns all images with rating >= cutoff, plus (optionally) unrated images.

    Parameters
    ----------
    folder : path-like
    percentile : float in [0,1]
        e.g., 0.5 for median, 0.8 for top 20%.
    include_unrated_in_result : bool
        If True, add unrated files to the result list.
    recursive : bool
        Recurse subfolders.
    extensions : sequence[str] or None
        e.g., ["jpg", "jpeg", "png"]; default uses a broad photo set.

    Returns
    -------
    FilterResult
    """
    default_exts = ["jpg", "jpeg", "tif", "tiff", "png", "heic", "cr2", "nef", "arw", "dng"]
    exts = extensions if extensions else default_exts

    files = collect_files(folder, recursive=recursive, extensions=exts)
    if not files:
        return FilterResult(selected=[], cutoff_stars=None, rated=[], unrated=[], stats={"total_scanned": 0, "rated": 0, "unrated": 0, "selected": 0})

    records = read_ratings_with_exiftool(files)
    # Build fast lookup path->record; ExifTool may skip unreadables, keep originals too
    by_path = {r.path: r for r in records}
    # Ensure we also capture any files that ExifTool skipped (as unrated)
    for f in files:
        if f not in by_path:
            by_path[f] = RatingRecord(path=f, stars=None, xmp_rating=None, ms_rating=None, ms_rating_percent=None)

    rated = [r for r in by_path.values() if r.stars is not None]
    unrated = [r for r in by_path.values() if r.stars is None]

    cutoff: Optional[int] = None
    if rated:
        cutoff = percentile_nearest_rank([r.stars for r in rated if r.stars is not None], percentile)

    selected: List[Path] = []
    if cutoff is not None:
        selected.extend([r.path for r in rated if (r.stars is not None and r.stars >= cutoff)])

    if include_unrated_in_result:
        selected.extend([r.path for r in unrated])

    # Stable order by path string
    selected = sorted(set(selected), key=lambda p: str(p).lower())

    stats = {
        "total_scanned": len(files),
        "rated": len(rated),
        "unrated": len(unrated),
        "selected": len(selected),
    }
    return FilterResult(selected=selected, cutoff_stars=cutoff, rated=sorted(rated, key=lambda r: str(r.path).lower()), unrated=sorted(unrated, key=lambda r: str(r.path).lower()), stats=stats)

# 1) Median selection, include unrated
'''res = filter_images_by_rating(r"C:\photos\ingest", percentile=0.5, include_unrated_in_result=True)
print("Cutoff stars:", res.cutoff_stars)
print("Selected count:", len(res.selected))
for p in res.selected[:5]:
    print(" -", p)'''

# 2) 80th percentile, exclude unrated, only JPG/JPEG, non-recursive
'''res80 = filter_images_by_rating(
    folder=Path("data"),
    percentile=0.2,
    include_unrated_in_result=False,
    recursive=False,
    extensions=["jpg", "jpeg"],
)
print(res80.stats)
for p in res80.selected:
    print(" -", p)'''
