from PIL import Image
import xml.etree.ElementTree as ET
from pathlib import Path

def _stars_from_percent(pct: int) -> int:
    # Windows typically uses 1,25,50,75,99. Map to 1..5.
    if pct <= 0: return 0
    if pct >= 99: return 5
    # round to nearest of {1,25,50,75,99} → 1..5
    cuts = [1, 25, 50, 75, 99]
    stars = [1, 2, 3, 4, 5]
    closest = min(range(len(cuts)), key=lambda i: abs(cuts[i] - pct))
    return stars[closest]

def get_windows_rating(path, look_for_sidecar=True):
    """
    Returns {'stars': int|None, 'xmp_rating': str|None, 'ms_rating_percent': str|None, 'source': 'embedded'|'sidecar'|None}
    stars is 0..5 (None if not found).
    """
    def _parse_xmp(xmp_bytes, source_label):
        ns = {
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "xmp": "http://ns.adobe.com/xap/1.0/",
            "mp":  "http://ns.microsoft.com/photo/1.2/",
        }
        root = ET.fromstring(xmp_bytes)

        # 1) Element form: <xmp:Rating>5</xmp:Rating>
        xmp_rating_el = root.find(".//xmp:Rating", ns)
        xmp_rating = xmp_rating_el.text.strip() if (xmp_rating_el is not None and xmp_rating_el.text) else None

        # 2) Attribute form: <rdf:Description xmp:Rating="5" ...>
        if xmp_rating is None:
            # search any element with an attribute in the xmp namespace named 'Rating'
            for el in root.iter():
                for k, v in el.attrib.items():
                    if k.startswith("{http://ns.adobe.com/xap/1.0/}") and k.endswith("Rating") and v.strip():
                        xmp_rating = v.strip()
                        break
                if xmp_rating:
                    break

        # 3) Microsoft Photo: <mp:RatingPercent>99</mp:RatingPercent> (sometimes attribute too)
        mp_rating_pct_el = root.find(".//mp:RatingPercent", ns)
        mp_rating_pct = mp_rating_pct_el.text.strip() if (mp_rating_pct_el is not None and mp_rating_pct_el.text) else None
        if mp_rating_pct is None:
            for el in root.iter():
                for k, v in el.attrib.items():
                    if k.startswith("{http://ns.microsoft.com/photo/1.2/}") and k.endswith("RatingPercent") and v.strip():
                        mp_rating_pct = v.strip()
                        break
                if mp_rating_pct:
                    break

        # Compute stars consistently
        stars = None
        if xmp_rating is not None:
            try:
                stars = max(0, min(5, int(xmp_rating)))
            except ValueError:
                pass
        if stars is None and mp_rating_pct is not None:
            try:
                stars = _stars_from_percent(int(mp_rating_pct))
            except ValueError:
                pass

        return {
            "stars": stars,
            "xmp_rating": xmp_rating,
            "ms_rating_percent": mp_rating_pct,
            "source": source_label,
        }

    # 1) Embedded XMP
    with Image.open(path) as im:
        xmp = im.info.get("XML:com.adobe.xmp")
        if xmp:
            return _parse_xmp(xmp, "embedded")

    # 2) Optional: XMP sidecar next to the file (some workflows use this)
    if look_for_sidecar:
        sidecar = Path(path).with_suffix(".xmp")
        try:
            if sidecar.exists():
                return _parse_xmp(sidecar.read_bytes(), "sidecar")
        except Exception:
            pass

    return {"stars": None, "xmp_rating": None, "ms_rating_percent": None, "source": None}

info = get_windows_rating("test3.jpg")
print(info)  # e.g., {'stars': 2, 'xmp_rating': '2', 'ms_rating_percent': '25', 'source': 'embedded'}

