"""Match literal dates and narrowly scoped Japanese date lists.

Only punctuation-connected days inherit the immediately preceding month/year.
An unrelated number, paragraph or campaign heading ends inheritance.
"""
import re
from datetime import date

from common import normalize_text

DATE = re.compile(
    r"(?<![\d/.-])(?:(?P<y>20\d{2})(?:年|[/.-]))?"
    r"(?P<m>1[0-2]|0?[1-9])(?:月|[/\.])(?P<d>3[01]|[12]\d|0?[1-9])(?:日|(?!\d))"
    r"|(?<!\d)(?P<iy>20\d{2})-(?P<im>\d{2})-(?P<id>\d{2})(?!\d)"
)
NEXT_DAY = re.compile(r"(?:\([月火水木金土日祝・]+\))?(?:、|,|・|および|及び|と|〜|~|～|－|–|—)+(?P<d>3[01]|[12]\d|0?[1-9])日")


def mentioned_dates(text):
    text = normalize_text(text)
    found = set()
    for match in DATE.finditer(text):
        year = match['y'] or match['iy']
        month = int(match['m'] or match['im'])
        day = int(match['d'] or match['id'])
        year = int(year) if year else None
        try:
            date(year or 2000, month, day)
        except ValueError:
            continue
        found.add((year, month, day))
        pos = match.end()
        while tail := NEXT_DAY.match(text, pos):
            day = int(tail['d'])
            try:
                date(year or 2000, month, day)
            except ValueError:
                break
            found.add((year, month, day))
            pos = tail.end()
    return found


def date_present(text, dt):
    return any(m == dt.month and d == dt.day and (y is None or y == dt.year)
               for y, m, d in mentioned_dates(text))
