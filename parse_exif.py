from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

# =========================
# Public entry point
# =========================

def read_all_metadata(
    image_path: Union[str, Path],
    *,
    numeric: bool = True,
    prefer_exiftool: bool = True,
    keep_sourcefile_key: bool = False,
) -> Dict[str, Any]:
    """
    Return a maximal key→value mapping of metadata for a single image.

    Priority: ExifTool (if present) → rich JSON of all groups and structs.
    Fallback: Pillow-based scraper for EXIF, IPTC, XMP, and common JPEG info.

    Parameters
    ----------
    image_path : str | Path
        Path to the image.
    numeric : bool
        If True, ask exiftool for numeric output where applicable (-n).
        If False, exiftool returns formatted strings (e.g., "1/125").
    prefer_exiftool : bool
        If False, skip exiftool and force the Pillow fallback.
    keep_sourcefile_key : bool
        If True, keep ExifTool's 'SourceFile' key.

    Returns
    -------
    Dict[str, Any]
        Keys are namespaced like 'EXIF:DateTimeOriginal', 'XMP-xmp:Rating',
        'IPTC:Keywords', 'File:FileType', 'JFIF:Density', etc.
        Values are JSON-serializable types (str/int/float/bool/list/dict) when possible.
    """
    path = Path(image_path)

    # Preferred path: ExifTool
    if prefer_exiftool and shutil.which("exiftool") is not None:
        return _read_with_exiftool(path, numeric=numeric, keep_sourcefile_key=keep_sourcefile_key)

    # Fallback path: Pillow/xmletree best-effort
    return _read_with_pillow(path)


# =========================
# ExifTool-backed extraction
# =========================

def _read_with_exiftool(path: Path, *, numeric: bool, keep_sourcefile_key: bool) -> Dict[str, Any]:
    """
    Use ExifTool to fetch a maximal JSON dump and normalize to a flat dict
    with group-prefixed keys (e.g., 'EXIF:DateTimeOriginal').
    """
    # Build the exiftool command:
    # -j           JSON
    # -G:1         include 1st-level group names in the keys (EXIF, XMP-xmp, IPTC, File, Composite, etc.)
    # -a           allow duplicate tags → arrays in JSON
    # -u           include unknown tags
    # -struct      expand structured tags to nested JSON
    # -api largefilesupport=1   robustness for large files/containers
    cmd = [
        "exiftool",
        "-j",
        "-G:1",
        "-a",
        "-u",
        "-struct",
        "-api", "largefilesupport=1",
        str(path),
    ]
    if numeric:
        cmd.insert(1, "-n")

    # Important: suppress warnings on stdout so JSON stays clean
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    blobs = json.loads(out)
    if not blobs:
        return {}

    row = dict(blobs[0])  # should be exactly one object for one input file
    if not keep_sourcefile_key:
        row.pop("SourceFile", None)

    # ExifTool already returns a “maximal” set. Keys look like:
    #   EXIF:DateTimeOriginal
    #   XMP-xmp:Rating
    #   XMP-dc:subject (list/struct)
    #   IPTC:Keywords (list)
    #   File:FileType
    #   Composite:GPSLatitude
    #   MakerNotes:...
    # We just pass this through.
    return row


# =========================
# Pillow-based fallback
# =========================

def _read_with_pillow(path: Path) -> Dict[str, Any]:
    """
    Best-effort, no-ExifTool fallback using Pillow and xml.etree to gather:
    - EXIF (GPS + Windows XP* decoding)
    - IPTC (APP13)
    - XMP (flattened)
    - Basic image info (format, size, DPI, ICC, JFIF, comments)
    Returns a namespaced flat dict.
    """
    result: Dict[str, Any] = {}
    try:
        from PIL import Image, ExifTags, IptcImagePlugin
        import xml.etree.ElementTree as ET

        TAGS = ExifTags.TAGS
        GPSTAGS = ExifTags.GPSTAGS

        def add(key: str, val: Any):
            if val is None:
                return
            result[key] = val

        def _decode_xp(val):
            # Windows XP fields (UTF-16LE), sometimes list of ints
            if isinstance(val, list):
                val = bytes(val)
            if isinstance(val, (bytes, bytearray)):
                try:
                    return bytes(val).decode("utf-16le", errors="ignore").rstrip("\x00")
                except Exception:
                    return None
            return val

        def _flatten_gps(gps_ifd: dict):
            flat = {}
            for k, v in gps_ifd.items():
                name = GPSTAGS.get(k, str(k))
                flat[name] = v
            return flat

        def _flatten_xmp(xmp_bytes: bytes):
            """
            Produce key paths like:
              XMP:dc.subject[0] = "People"
              XMP:xmp.Rating = 5
              XMP:lr.hierarchicalSubject[1] = "People|Family|Clara"
            """
            try:
                root = ET.fromstring(xmp_bytes)
            except Exception:
                return {}

            # Build ns mapping from the XML (fallback prefixes like ns1 for unknown)
            nsmap: Dict[str, str] = {}
            used = set()
            def ensure_prefix(uri: str) -> str:
                # Common namespaces first
                commons = {
                    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
                    "http://www.w3.org/XML/1998/namespace": "xml",
                    "http://purl.org/dc/elements/1.1/": "dc",
                    "http://ns.adobe.com/xap/1.0/": "xmp",
                    "http://ns.adobe.com/lightroom/1.0/": "lr",
                    "http://ns.microsoft.com/photo/1.2/": "mp",
                    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
                    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
                    "http://ns.adobe.com/exif/1.0/": "exif",
                    "http://ns.adobe.com/tiff/1.0/": "tiff",
                }
                if uri in nsmap:
                    return nsmap[uri]
                if uri in commons:
                    p = commons[uri]
                else:
                    # generate ns1, ns2, ...
                    i = 1
                    p = f"ns{i}"
                    while p in used:
                        i += 1
                        p = f"ns{i}"
                nsmap[uri] = p
                used.add(p)
                return p

            def qname(tag: str) -> str:
                if not tag.startswith("{"):
                    return tag
                uri, local = tag[1:].split("}", 1)
                pfx = ensure_prefix(uri)
                return f"{pfx}.{local}"

            kv: Dict[str, Any] = {}

            RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
            XML_NS = "http://www.w3.org/XML/1998/namespace"

            def walk(elem, base: str):
                # Handle RDF containers
                if elem.tag == f"{{{RDF_NS}}}Bag" or elem.tag == f"{{{RDF_NS}}}Seq":
                    for i, li in enumerate(elem.findall(f"./{{{RDF_NS}}}li")):
                        walk(li, f"{base}[{i}]")
                    return
                if elem.tag == f"{{{RDF_NS}}}Alt":
                    for i, li in enumerate(elem.findall(f"./{{{RDF_NS}}}li")):
                        lang = li.attrib.get(f"{{{XML_NS}}}lang")
                        key = f"{base}[{i}{',lang='+lang if lang else ''}]"
                        val = (li.text or "").strip()
                        if val != "":
                            kv[f"XMP:{key}"] = val
                    return

                name = qname(elem.tag)
                cur = f"{base}.{name}" if base else name

                # rdf:resource attributes
                res = elem.attrib.get(f"{{{RDF_NS}}}resource")
                if res:
                    kv[f"XMP:{cur}@rdf.resource"] = res

                # other attributes (non-xmlns)
                for k, v in elem.attrib.items():
                    if k.startswith("{http://www.w3.org/2000/xmlns/}"):
                        continue
                    if k == f"{{{RDF_NS}}}resource":
                        continue
                    kv[f"XMP:{cur}@{qname(k)}"] = v

                children = list(elem)
                text = (elem.text or "").strip()

                if children:
                    for ch in children:
                        walk(ch, cur)
                else:
                    if text != "":
                        kv[f"XMP:{cur}"] = text

            # Start from rdf:RDF if present
            rdf = root.find(f".//{{{RDF_NS}}}RDF")
            start = rdf if rdf is not None else root
            walk(start, "")
            return kv

        with Image.open(path) as im:
            # Basic facts
            add("Image:Format", im.format)
            add("Image:Mode", im.mode)
            add("Image:Size", list(im.size))  # (w,h) → list for JSON-friendliness
            if "dpi" in im.info:
                add("JFIF:DPI", im.info["dpi"])
            if "jfif" in im.info:
                add("JFIF:Identifier", im.info["jfif"])
                add("JFIF:Version", im.info.get("jfif_version"))
                add("JFIF:Unit", im.info.get("jfif_unit"))
                add("JFIF:Density", im.info.get("jfif_density"))
            if "icc_profile" in im.info:
                add("ICC:ProfileLength", len(im.info["icc_profile"]))
            if "comment" in im.info:
                c = im.info.get("comment")
                if isinstance(c, (bytes, bytearray)):
                    try:
                        c = c.decode("utf-8")
                    except Exception:
                        pass
                add("JPEG:Comment", c)

            # EXIF
            try:
                exif = im.getexif()
                if exif:
                    # Flatten EXIF tags with namespaced keys
                    for tag_id, value in exif.items():
                        name = TAGS.get(tag_id, str(tag_id))
                        key = f"EXIF:{name}"
                        if name == "GPSInfo" and isinstance(value, dict):
                            gps = _flatten_gps(value)
                            for gk, gv in gps.items():
                                add(f"EXIF:GPS.{gk}", gv)
                        elif name.startswith("XP"):  # XPTitle, XPAuthor, XPKeywords, etc.
                            add(key, _decode_xp(value))
                        else:
                            add(key, value)
            except Exception:
                pass

            # IPTC (APP13)
            try:
                iptc = IptcImagePlugin.getiptcinfo(im)
                if iptc:
                    for (rec, ds), v in iptc.items():
                        k = f"IPTC:{rec},{ds}"
                        if isinstance(v, (bytes, bytearray)):
                            try:
                                v = bytes(v).decode("utf-8")
                            except Exception:
                                v = bytes(v)
                        add(k, v)
            except Exception:
                pass

            # XMP
            xmp = im.info.get("XML:com.adobe.xmp")
            if xmp:
                for k, v in _flatten_xmp(xmp).items():
                    add(k, v)

    except Exception:
        # If anything goes wrong, return what we have so far
        pass

    return result

Meta = Dict[str, Any]

def _first_present(meta: Meta, keys: Iterable[str]) -> Any:
    """Return the first meta[k] that exists and is not None."""
    for k in keys:
        if k in meta and meta[k] is not None:
            return meta[k]
    return None

def _extract_nested(meta: Meta, parent_keys: Iterable[str], child_key: str) -> Any:
    """
    Get a nested value where meta[parent][child_key], handling:
      - parent as dict,
      - parent as list[dict],
      - parent as list[str] (return the list),
      - parent as None/missing (return None).
    If parent is list[dict], returns a list of child_key values (skipping missing).
    """
    parent = _first_present(meta, parent_keys)
    if parent is None:
        return None

    # If the parent is already a list of strings, just return as-is
    if isinstance(parent, list) and all(isinstance(x, str) for x in parent):
        return parent

    # If parent is a dict → return the child directly (if present)
    if isinstance(parent, dict):
        return parent.get(child_key, None)

    # If parent is a list of dicts → collect the child values
    if isinstance(parent, list) and all(isinstance(x, dict) for x in parent):
        vals = [x.get(child_key) for x in parent if child_key in x and x.get(child_key) is not None]
        # Return a single value if only one found, else list (keeps info without being too noisy)
        if not vals:
            return None
        return vals[0] if len(vals) == 1 else vals

    # Fallback: unknown structure; return as-is so caller can inspect
    return parent

def condense_metadata(meta: Meta) -> Dict[str, Any]:
    """
    Build:
      {
        "title": meta["ImageDescription"],
        "begin": meta["CreateDate"],
        "people_agent_header_1": meta["Artist"],
        "people_agent_header_2": meta["XMP-iptcExt:PersonInImage"],
        "subject_1_term": meta["XMP-plus:ImageSupplier"]["ImageSupplierName"],
        "n_odd": meta["XMP-iptcCore:Location"],
        "n_abstract": meta["XMP-photoshop:Headline"],
      }

    This function is tolerant of namespaced EXIF keys (e.g., "EXIF:ImageDescription")
    and typical XMP struct/list shapes produced by ExifTool.
    """
    out: Dict[str, Any] = {}

    # title ← ImageDescription (prefer namespaced EXIF, then bare)
    out["title"] = _first_present(meta, ("XMP-dc:Description", "IFD0:ImageDescription", "Description", "ImageDescription"))
    if out["title"] is not None:
        while out["title"][-1] == " ":
            out["title"] = out["title"][:len(out["title"]) - 1]
        out["title"] = out["title"].replace("\n", " ")
        while out["title"].find("  ") != -1:
            out["title"] = out["title"].replace("  ", " ")

    # begin ← CreateDate (often EXIF:CreateDate; sometimes also XMP-xmp:CreateDate)
    out["begin"] = _first_present(meta, ("EXIF:CreateDate", "CreateDate", "XMP-xmp:CreateDate"))

    # people_agent_header_1 ← Artist
    out["people_agent_header_1"] = _first_present(meta, ("IPTC:By-line", "IFD0:Artist", "Creator", "Artist"))

    # people_agent_header_2 ← XMP-iptcExt:PersonInImage (usually list of strings)
    out["people_agent_header_2"] = _first_present(meta, ("XMP-iptcExt:PersonInImage",))    
    if isinstance(out["people_agent_header_2"], list):        
        temp = out["people_agent_header_2"][0]
        if len(out["people_agent_header_2"]) > 1:
            for person in out["people_agent_header_2"][1:]:
                temp += " " + person
        out["people_agent_header_2"] = temp            

    # subject_1_term ← XMP-plus:ImageSupplier → ImageSupplierName (struct or list of structs)
    out["subject_1_term"] = _extract_nested(
        meta,
        parent_keys=("XMP-plus:ImageSupplier",),
        child_key="ImageSupplierName",
    )

    # n_odd ← XMP-iptcCore:Location (string or struct in some toolchains)
    # If it’s a struct, try common fields like (City, ProvinceState, CountryName)
    loc = _first_present(meta, ("XMP-iptcCore:Location",))
    if isinstance(loc, dict):
        # try to concatenate common subfields if present; otherwise keep the dict
        pieces = [loc.get(k) for k in ("Sublocation", "City", "ProvinceState", "CountryName") if loc.get(k)]
        out["n_odd"] = ", ".join(pieces) if pieces else loc
    else:
        out["n_odd"] = loc

    # n_abstract ← XMP-photoshop:Headline
    out["n_abstract"] = _first_present(meta, ("XMP-photoshop:Headline",))

    return out

def _non_null(x: Any) -> bool:
    """True if x should count as 'present' (not None/empty)."""
    if x is None:
        return False
    if isinstance(x, str):
        return x.strip() != ""
    if isinstance(x, (list, tuple, set, dict)):
        return len(x) > 0
    return True  # numbers, bools, etc.

def augment_condensed_metadata(
    image_path: str | Path,
    condensed: Dict[str, Any],
    hierarchy: str,    
    mb_base: int = 1024,   # use 1024 for MiB-ish 'MB' (common in dev tools). Set to 1000 for SI MB.
    round_digits: int = 3, # decimals to round file size
) -> Dict[str, Any]:
    """
    Augment a condensed metadata record with hierarchy, file size, and standard fields.

    Parameters
    ----------
    image_path : str | Path
        Path to the image file on disk (used to compute file size).
    condensed : dict
        The condensed metadata record to augment (will not be mutated; a copy is returned).
    hierarchy : str
        The hierarchical relationship to record (stored under key 'hierarchy').
    mb_base : int
        1024 → bytes per 'MB' (binary), 1000 → SI MB. Default 1024.
    round_digits : int
        Decimal places to round file size.

    Returns
    -------
    dict
        A new dict with added fields.
    """
    out = dict(condensed)  # copy so we don't mutate caller's dict

    # 1) hierarchy
    out["hierarchy"] = hierarchy

    # 2) file size (MB) and extent_type
    p = Path(image_path)
    try:
        size_bytes = p.stat().st_size
        mb = size_bytes / (mb_base ** 2)
        out["number"] = round(mb, round_digits)
        out["extent_type"] = "MB"
    except FileNotFoundError:
        # If path is missing, skip size fields (or set to None)
        out["number"] = None
        out["extent_type"] = "MB"

    # 3) standard fields (with the specified conditionals)
    out["restrictions_flag"] = "Yes"
    out["processing_note"] = (
        "This picture was held in VandyVision, the digital asset management system called PhotoShelter."
    )
    out["portion"] = "1"
    out["type_2"] = "Item"
    out["p_acqinfo"] = "Yes"
    out["n_acqinfo"] = "PhotoShelter https://www.photoshelter.com"

    # Conditionally add date-related labels if begin is present
    if _non_null(out.get("begin")):
        out["dates_label"] = "creation"
        out["date_type"] = "single"

    # People roles/relators based on presence of headers
    if _non_null(out.get("people_agent_header_1")):
        out["people_agent_role_1"] = "creator"
        out["people_agent_relator_1"] = "photographer"

    if _non_null(out.get("people_agent_header_2")):
        out["people_agent_role_2"] = "subject"
        out["people_agent_relator_2"] = "associated name"

    # p_odd / l_odd based on n_odd presence
    if _non_null(out.get("n_odd")):
        out["p_odd"] = "Yes"
        out["l_odd"] = "location"

    # subject_1 qualifiers if subject_1_term present
    if _non_null(out.get("subject_1_term")):
        out["subject_1_type"] = "topical"
        out["subject_1_source"] = "local sources"

    return out

'''meta = read_all_metadata("photos/test3.jpg", numeric=True)
for field in meta:
    print(f"{field}: {meta[field]}")
output = condense_metadata(meta)
# print(output)
augmented = augment_condensed_metadata("photos/test3.jpg", output, hierarchy=2)
print(augmented)'''
