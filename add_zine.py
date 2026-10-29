import pathlib

TAG = "<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='zine.css') }}\">"
ANCHORS = [
    "filename='semesters.css') }}\">",
    "filename='style.css') }}\">",
]

for f in sorted(pathlib.Path("src/templates").glob("*.html")):
    text = f.read_text()
    if "zine.css" in text:
        continue
    for anchor in ANCHORS:
        if anchor in text:
            f.write_text(text.replace(anchor, anchor + "\n    " + TAG, 1))
            print("updated:", f.name)
            break
    else:
        print("no stylesheet link, check by hand:", f.name)