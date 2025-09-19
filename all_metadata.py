from PIL import Image, ExifTags, IptcImagePlugin
import xml.etree.ElementTree as ET

# Known namespaces (you can add more; unknown ones get auto-prefixed as ns1, ns2, ...)
KNOWN_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "dc":  "http://purl.org/dc/elements/1.1/",
    "tiff":"http://ns.adobe.com/tiff/1.0/",
    "exif":"http://ns.adobe.com/exif/1.0/",
    "photoshop":"http://ns.adobe.com/photoshop/1.0/",
    "xmpMM":"http://ns.adobe.com/xap/1.0/mm/",
    "stEvt":"http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "lr":  "http://ns.adobe.com/lightroom/1.0/",
    "mp":  "http://ns.microsoft.com/photo/1.2/",
}

def _build_prefix_maps(root):
    """Map namespace URI -> prefix, using KNOWN_NS first and creating ns1, ns2... for unknown URIs."""
    uri_to_prefix = {uri: pfx for pfx, uri in KNOWN_NS.items()}
    used = set(uri_to_prefix.values())
    counter = 1

    def ensure(tag):
        nonlocal counter
        if tag.startswith("{"):
            uri, _ = tag[1:].split("}", 1)
            if uri not in uri_to_prefix:
                # generate a short prefix
                while True:
                    cand = f"ns{counter}"
                    counter += 1
                    if cand not in used:
                        used.add(cand)
                        uri_to_prefix[uri] = cand
                        break

    for elem in root.iter():
        ensure(elem.tag)
        for k in elem.attrib.keys():
            ensure(k)
    return uri_to_prefix

def _qname(tag, uri_to_prefix):
    """Return 'prefix:local' for a Clark-notation tag like '{uri}local'."""
    if not tag.startswith("{"):
        return tag
    uri, local = tag[1:].split("}", 1)
    pfx = uri_to_prefix.get(uri, None)
    return f"{pfx}:{local}" if pfx else local

def _is_rdf(tag, uri_to_prefix, local):
    if not tag.startswith("{"): 
        return False
    uri, loc = tag[1:].split("}", 1)
    return (uri_to_prefix.get(uri) == "rdf") and (loc == local)

def _textify(val):
    return val.strip() if isinstance(val, str) else val

def dump_xmp_generic(xmp_bytes):
    """Pretty-print all XMP tags/values, arrays, and useful attributes."""
    root = ET.fromstring(xmp_bytes)
    uri_to_prefix = _build_prefix_maps(root)

    print("=== XMP (generic dump) ===")

    def walk(elem, path):
        # Handle RDF containers specially
        if _is_rdf(elem.tag, uri_to_prefix, "Bag") or _is_rdf(elem.tag, uri_to_prefix, "Seq"):
            for i, li in enumerate(elem.findall("./{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")):
                walk(li, f"{path}[{i}]")
            return
        if _is_rdf(elem.tag, uri_to_prefix, "Alt"):
            for i, li in enumerate(elem.findall("./{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")):
                lang = li.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
                key = f"{path}[{i}{',lang='+lang if lang else ''}]"
                text = _textify(li.text or "")
                print(f"{key} = {text}")
            return

        # Normal element
        name = _qname(elem.tag, uri_to_prefix)
        cur = f"{path}/{name}" if path else name

        # If element has rdf:resource, print that (often used for references)
        res = elem.attrib.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource")
        if res:
            print(f"{cur} @rdf:resource = {_textify(res)}")

        # Print other non-xmlns attributes
        for k, v in elem.attrib.items():
            if k.startswith("{http://www.w3.org/2000/xmlns/}"):
                continue
            if k == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource":
                continue
            print(f"{cur} @{_qname(k, uri_to_prefix)} = {_textify(v)}")

        children = list(elem)
        text = _textify(elem.text or "")

        if children:
            # Container or struct: recurse
            for ch in children:
                walk(ch, cur)
        else:
            # Leaf value
            if text != "":
                print(f"{cur} = {text}")

    # Start from rdf:RDF (common) if present, else from root
    rdf = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF")
    start = rdf if rdf is not None else root
    walk(start, "")

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
        
        xmp = im.info.get("XML:com.adobe.xmp")
        if xmp:
            print("xmp bytes:", len(xmp))
            try:
                dump_xmp_generic(xmp)
            except Exception as e:
                print("(XMP present but parsing failed:", e, ")")
        else:
            print("=== XMP ===")
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
dump_all_metadata("test3.jpg")
