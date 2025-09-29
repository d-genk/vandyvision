from PIL import Image, ExifTags

path = "test.jpg"
with Image.open(path) as im:
    exif = im.getexif()

    # Map IDs to human-readable tag names
    for tag_id, value in exif.items():
        tag = ExifTags.TAGS.get(tag_id, tag_id)
        print(f"{tag:25}: {value}")
