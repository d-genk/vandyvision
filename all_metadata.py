from PIL import Image, ExifTags, IptcImagePlugin
import xml.etree.ElementTree as ET

TAGS = ExifTags.TAGS
GPSTAGS = ExifTags.GPSTAGS

def _decode_xp(val):
    if isinstance(val, list):
        val = bytes(val)
    if isinstance(val, bytes):
        return val.decode("utf-16le", errors="ignore").rstrip("\x00")
    return None

def _parse_gps(gps_ifd):
    out = {}
    for k, v in gps_ifd.items():
        out[GPSTAGS.get(k, k)] = v
    return out

def dump_all_metadata(path):
    with Image.open(path) as im:
        print("=== BASIC IMAGE INFO ===")
        print("format:", im.format)
        print("mode:", im.mode)
        print("size:", im.size)  # (w, h)
        if "dpi" in im.info:
            print("dpi:", im.info["dpi"])
        if "jfif" in im.info:
            print("jfif:", im.info["jfif"], "version:", im.info.get("jfif_version"), "unit:", im.info.get("jfif_unit"), "density:", im.info.get("jfif_density"))
        if "icc_profile" in im.info:
            print("icc_profile: bytes:", len(im.info["icc_profile"]))

        print("\n=== EXIF (including GPS & XP* fields) ===")
        exif = im.getexif()
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)

            # Expand GPS sub-IFD
            if tag == "GPSInfo" and isinstance(value, dict):
                gps = _parse_gps(value)
                print(f"{tag}:")
                for gk, gv in gps.items():
                    print(f"  - {gk}: {gv}")
                continue

            # Decode Windows XP* UTF-16LE fields
            if tag in ("XPTitle", "XPComment", "XPAuthor", "XPKeywords", "XPSubject") or tag_id in (40091,40092,40093,40094,40095):
                decoded = _decode_xp(value)
                print(f"{tag}: {decoded}")
                continue

            print(f"{tag}: {value}")

        # Some tools embed the whole EXIF blob; handy to know it exists
        if "exif" in im.info:
            print("\n(exif blob present, bytes:", len(im.info["exif"]), ")")

        print("\n=== IPTC (APP13) ===")
        try:
            iptc = IptcImagePlugin.getiptcinfo(im)
            if iptc:
                for k, v in iptc.items():
                    # k is a tuple like (record, dataset), e.g., (2, 25) for Keywords
                    if isinstance(v, bytes):
                        try:
                            v = v.decode("utf-8")
                        except Exception:
                            pass
                    print(f"{k}: {v}")
            else:
                print("(none)")
        except Exception as e:
            print("(error reading IPTC:", e, ")")

        print("\n=== XMP (dc:subject, rating, etc.) ===")
        xmp = im.info.get("XML:com.adobe.xmp")
        if xmp:
            print("xmp bytes:", len(xmp))
            try:
                root = ET.fromstring(xmp)
                ns = {
                    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                    "dc":  "http://purl.org/dc/elements/1.1/",
                    "xmp": "http://ns.adobe.com/xap/1.0/",
                }
                subjects = [li.text for li in root.findall(".//dc:subject//rdf:li", ns) if li.text]
                rating = root.find(".//xmp:Rating", ns)
                if subjects:
                    print("dc:subject:", subjects)
                if rating is not None and rating.text:
                    print("xmp:Rating:", rating.text)

                # --- Microsoft Photo extensions ---
                ns["mp"] = "http://ns.microsoft.com/photo/1.2/"
                mp_rating = root.find(".//mp:Rating", ns)
                mp_rating_pct = root.find(".//mp:RatingPercent", ns)

                if mp_rating is not None and mp_rating.text:
                    print("MicrosoftPhoto:Rating:", mp_rating.text)
                if mp_rating_pct is not None and mp_rating_pct.text:
                    print("MicrosoftPhoto:RatingPercent:", mp_rating_pct.text)
            except Exception as e:
                print("(XMP present but parsing failed:", e, ")")
        else:
            print("(none)")

        print("\n=== JPEG COMMENTS (COM) ===")
        comment = im.info.get("comment")
        if comment:
            if isinstance(comment, bytes):
                try:
                    comment = comment.decode("utf-8")
                except Exception:
                    pass
            print(comment)
        else:
            print("(none)")

# usage
dump_all_metadata("test2.jpg")
