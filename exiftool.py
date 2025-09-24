import json, subprocess, shutil

def read_star_rating(path: str):
    """
    Returns dict with normalized 0..5 'stars' plus raw fields if present.
    Requires exiftool in PATH.
    """
    if shutil.which("exiftool") is None:
        raise RuntimeError("exiftool not found in PATH")

    # -j = JSON, -n = numeric, -s -s -s = short tag names only
    # Ask for the common variants explicitly, but also grab all XMP to be safe.
    cmd = [
        "exiftool", "-j", "-n", "-s", "-s", "-s",
        "-XMP-xmp:Rating",           # e.g., 2
        "-XMP-MicrosoftPhoto:Rating",# sometimes present
        "-XMP-MicrosoftPhoto:RatingPercent", # e.g., 25/50/75/99
        path
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))[0]

    xmp_rating = data.get("Rating")
    mp_rating   = data.get("MicrosoftPhoto:Rating")
    mp_percent  = data.get("MicrosoftPhoto:RatingPercent")

    def stars_from_percent(pct):
        if pct is None: return None
        try:
            pct = int(pct)
        except Exception:
            return None
        if pct <= 0:  return 0
        if pct >= 99: return 5
        cuts = [1, 25, 50, 75, 99]; stars = [1,2,3,4,5]
        return stars[min(range(len(cuts)), key=lambda i: abs(cuts[i]-pct))]

    stars = None
    for v in (xmp_rating, mp_rating):
        if isinstance(v, (int, str)) and str(v).isdigit():
            stars = max(0, min(5, int(v))); break
    if stars is None:
        stars = stars_from_percent(mp_percent)

    return {
        "stars": stars,                       # 0..5 or None
        "xmp_rating": xmp_rating,             # raw
        "ms_rating": mp_rating,               # raw
        "ms_rating_percent": mp_percent,      # raw
    }

# Example
print(read_star_rating("test3.jpg"))
