from PIL import Image, IptcImagePlugin

def _decode_xp(val):
    # EXIF XP* fields are UTF-16LE bytes (sometimes returned as a list of ints)
    if isinstance(val, list):
        val = bytes(val)
    if isinstance(val, bytes):
        text = val.decode("utf-16le", errors="ignore").rstrip("\x00")
        # Windows typically uses semicolons as separators
        parts = [p.strip() for p in text.split(";")]
        return [p for p in parts if p]
    return []

def jpg_keywords(path):
    kws = set()
    with Image.open(path) as im:
        # 1) EXIF XPKeywords (Windows)
        try:
            exif = im.getexif()
            xp = exif.get(40094)  # 40094 = XPKeywords
            if xp:
                kws.update(_decode_xp(xp))
        except Exception:
            pass

        # 2) IPTC (APP13) – tag (2, 25) = "Keywords"
        try:
            iptc = IptcImagePlugin.getiptcinfo(im)
            if iptc and (2, 25) in iptc:
                val = iptc[(2, 25)]
                # Can be a single bytes/str or a list/tuple of them
                if not isinstance(val, (list, tuple)):
                    val = [val]
                for v in val:
                    if isinstance(v, bytes):
                        v = v.decode("utf-8", errors="ignore")
                    if v:
                        kws.add(v.strip())
        except Exception:
            pass

        # 3) XMP packet (usually under dc:subject; Lightroom also uses lr:hierarchicalSubject)
        try:
            xmp = im.info.get("XML:com.adobe.xmp")
            if xmp:
                import xml.etree.ElementTree as ET
                ns = {
                    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                    "dc":  "http://purl.org/dc/elements/1.1/",
                    "lr":  "http://ns.adobe.com/lightroom/1.0/",
                }
                root = ET.fromstring(xmp)

                # dc:subject/rdf:Bag/rdf:li
                for li in root.findall(".//dc:subject//rdf:li", ns):
                    if li.text:
                        kws.add(li.text.strip())

                # lr:hierarchicalSubject (split levels like "People|Family|Clara")
                for li in root.findall(".//lr:hierarchicalSubject//rdf:li", ns):
                    if li.text:
                        for part in (p.strip() for p in li.text.split("|")):
                            if part:
                                kws.add(part)
        except Exception:
            pass

    return sorted(kws)

print(jpg_keywords("test.jpg"))