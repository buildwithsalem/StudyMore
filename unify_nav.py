"""Replace every page's own copy of the top menu with the shared _nav.html.

Run from the repo root:   python unify_nav.py          (shows what it would change)
                          python unify_nav.py --apply  (does it)
Pages without a menu (login, register, verify...) are left alone."""

import pathlib
import re
import sys

APPLY = "--apply" in sys.argv
INCLUDE = '{% include "_nav.html" %}'
HEADER = re.compile(r'<header class="topbar">.*?</header>', re.DOTALL)

for f in sorted(pathlib.Path("src/templates").glob("*.html")):
    if f.name == "_nav.html":
        continue
    text = f.read_text()
    if INCLUDE in text:
        print("already shared:", f.name)
        continue
    match = HEADER.search(text)
    if not match or 'id="site-nav"' not in match.group(0):
        print("no menu (left alone):", f.name)
        continue
    print(("updated:" if APPLY else "would update:"), f.name)
    if APPLY:
        f.write_text(text[: match.start()] + INCLUDE + text[match.end():])