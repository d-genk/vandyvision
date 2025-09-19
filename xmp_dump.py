from PIL import Image

with Image.open("test3.jpg") as im:
    xmp = im.info.get("XML:com.adobe.xmp")
    if xmp:
        print(xmp.decode("utf-8", errors="ignore"))
    else:
        print("No embedded XMP found")
