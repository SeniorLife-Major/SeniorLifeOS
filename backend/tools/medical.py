"""Lists on medical documents (steps 6b/6c): lab test rows, medicines, bill items.

Reading:
  lab_rows        the results table of a lab report. With OCR pieces ("parts", each with a
                  box) every piece is put in the column whose header it sits under, and rows
                  are rebuilt after correcting for a tilted photo, so a value can't drift onto
                  the wrong test. Without pieces, each line is split into name/value/unit/range.
  medicine_rows   lines such as "Tab. Metformin 500 mg 1-0-1 x 30 days".
  item_rows       bill lines that end in an amount, between the table header and the total.
  heading_fields  lab or hospital name taken from the page heading; doctor from a "Dr." line.

Checking (normalize_rows): numbers and amounts are parsed, and a lab value is marked high or
low only against the reference range printed on the same row. Nothing is interpreted.
Every row of every list is reviewed by the owner (Decision #29).
"""

from __future__ import annotations

import re
import statistics
from typing import Any

MEDICAL_TYPES = {"lab_report", "prescription", "medical_bill"}
LIST_FOR_TYPE = {"lab_report": "tests", "prescription": "medicines", "medical_bill": "items"}
LIST_FIELDS = set(LIST_FOR_TYPE.values())
ROW_META = ("source_ref", "confidence", "box")  # kept through normalization, never values

QUALITATIVE = {"positive", "negative", "nil", "absent", "present", "reactive", "non-reactive", "normal", "trace"}
NUMBER = re.compile(r"\d+(?:\.\d+)?")
MONEY = re.compile(r"(?:₹|Rs\.?|INR)?\s*(\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:/-)?", re.IGNORECASE)

# ---------- small parsers ----------


def parse_number(text: str | None) -> float | None:
    text = (text or "").strip()
    return float(text) if NUMBER.fullmatch(text) else None


def parse_money(text: str | None) -> float | None:
    """'₹1,250.50', 'Rs. 1,250/-', '1250.5' -> 1250.5. Badly placed commas are refused."""
    match = MONEY.fullmatch((text or "").strip())
    if not match:
        return None
    number = match.group(1)
    whole = number.split(".")[0]
    if "," in whole and not (re.fullmatch(r"\d{1,2}(,\d{2})*,\d{3}", whole) or re.fullmatch(r"\d{1,3}(,\d{3})+", whole)):
        return None
    return round(float(number.replace(",", "")), 2)


def lab_flag(value: float | None, ref_range: str | None) -> str | None:
    """'high' / 'low' / 'normal' from the printed range only ('12.0 - 15.5', '< 200', '> 40')."""
    if value is None or not ref_range:
        return None
    text = ref_range.strip()
    if match := re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text):
        low, high = float(match[1]), float(match[2])
        return "low" if value < low else "high" if value > high else "normal"
    if match := re.fullmatch(r"<\s*=?\s*(\d+(?:\.\d+)?)", text):
        return "high" if value >= float(match[1]) else "normal"
    if match := re.fullmatch(r">\s*=?\s*(\d+(?:\.\d+)?)", text):
        return "low" if value <= float(match[1]) else "normal"
    return None


def tidy_range(text: str) -> str:
    """Same spacing for every range ('0.6-1.2' -> '0.6 - 1.2', '<200' -> '< 200'); numbers untouched."""
    if match := re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text):
        return f"{match[1]} - {match[2]}"
    if match := re.fullmatch(r"([<>]=?)\s*(\d+(?:\.\d+)?)", text):
        return f"{match[1]} {match[2]}"
    return text


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def normalize_rows(key: str, rows: Any) -> list[dict[str, Any]] | None:
    """Checked copies of list rows. Each row may carry "problems" for the reviewer. None if empty."""
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        meta = {k: row[k] for k in ROW_META if k in row}
        problems = []
        if key == "tests":
            value_text = _clean(row.get("value"))
            value = parse_number(value_text)
            if value is None and value_text.casefold() not in QUALITATIVE:
                problems.append("value is not a number")
            ref_range = tidy_range(_clean(row.get("ref_range"))) or None
            clean = {"name": _clean(row.get("name")), "value": value if value is not None else value_text or None,
                     "unit": _clean(row.get("unit")) or None, "ref_range": ref_range,
                     "flag": lab_flag(value, ref_range)}
            if not clean["name"]:
                problems.append("no test name")
        elif key == "medicines":
            clean = {field: _clean(row.get(field)) or None
                     for field in ("name", "form", "strength", "frequency", "duration")}
            if not clean["name"]:
                problems.append("no medicine name")
        else:  # items
            amount = parse_money(row.get("amount"))
            clean = {"description": _clean(row.get("description")) or None, "amount": amount}
            if amount is None:
                problems.append("amount is not readable")
            if not clean["description"]:
                problems.append("no description")
        if problems:
            clean["problems"] = problems
        out.append({**clean, **meta})
    return out


# ---------- reading rows from pages ----------

LAB_COLUMNS = {
    "name": ("test", "investigation", "parameter", "test name"),
    "value": ("result", "value", "observed value", "results"),
    "unit": ("unit", "units"),
    "ref_range": ("reference range", "ref. range", "reference", "normal range", "bio. ref. interval",
                  "biological reference interval", "ref range", "range"),
}
TABLE_END = re.compile(r"end of report|pathologist|signature|authori[sz]ed|\*\*\*|remarks|interpretation",
                       re.IGNORECASE)


BILL_COLUMNS = {
    "serial": ("s.no", "sno", "s. no", "sr. no", "sr no", "#"),
    "description": ("description", "particulars", "item", "items", "service", "services"),
    "qty": ("qty", "quantity"),
    "rate": ("rate", "price", "unit price", "mrp"),
    "amount": ("amount",),
}


def _column_of(text: str, columns: dict[str, tuple[str, ...]]) -> str | None:
    folded = _clean(text).casefold().rstrip(":")
    for column, names in columns.items():
        if folded in names:
            return column
    return None


def _header_columns(piece: dict[str, Any], columns: dict[str, tuple[str, ...]]) -> list[tuple[str, float]]:
    """Column names in one OCR piece, with each one's left edge.

    OCR sometimes joins neighbouring headers into one piece ("S.No Description"); then each
    name's left edge is estimated from where its words start inside the piece's text.
    """
    whole = _column_of(piece["text"], columns)
    if whole:
        return [(whole, piece["box"][0])]
    text = piece["text"]
    words = list(re.finditer(r"\S+", text))
    found, at = [], 0
    while at < len(words):
        for size in (3, 2, 1):  # longest header name first ("unit price" before "unit")
            if at + size <= len(words):
                column = _column_of(text[words[at].start():words[at + size - 1].end()], columns)
                if column:
                    x0, x1 = piece["box"][0], piece["box"][2]
                    found.append((column, x0 + (x1 - x0) * words[at].start() / max(len(text), 1)))
                    at += size
                    break
        else:
            return []  # a word that is not a header: this piece is not part of the header row
    return found


def _aligned_lines(page: dict[str, Any]) -> list[dict[str, Any]] | None:
    lines, text_lines = page.get("lines"), page.get("text", "").splitlines()
    return lines if lines and len(lines) == len(text_lines) else None


def _ref(page: dict[str, Any], index: int) -> str:
    return f"page:{page['page']}:line:{index + 1}"


def lab_rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _aligned_lines(page)
    if lines and any(line.get("parts") for line in lines):
        rows = _rows_by_column(page, lines, LAB_COLUMNS, key="name", needed={"name", "value"}, end=TABLE_END)
        if rows is not None:
            return rows
    text_lines = page.get("text", "").splitlines()
    header = next((i for i, line in enumerate(text_lines)
                   if re.search(r"\b(test|investigation|parameter)\b", line, re.I)
                   and re.search(r"\b(result|value)s?\b", line, re.I)), None)
    return [] if header is None else _lab_rows_by_text(page, text_lines, header)


def _center(part: dict[str, Any]) -> tuple[float, float]:
    x0, y0, x1, y1 = part["box"]
    return (x0 + x1) / 2, (y0 + y1) / 2


def _rows_by_column(page, lines, column_names, key: str, needed: set[str], end: re.Pattern) -> list[dict[str, Any]] | None:
    """Read a table piece by piece. The header pieces are looked for among all pieces,
    because on a tilted photo OCR's line grouping splits even the header row.
    Returns None when there is no header with the needed columns."""
    pieces = [{**part, "line": index} for index, line in enumerate(lines) for part in line.get("parts") or []]
    header: dict[str, dict[str, Any]] = {}  # column -> the piece holding its name
    lefts: dict[str, float] = {}             # column -> left edge
    for piece in pieces:
        for column, left in _header_columns(piece, column_names):
            if column not in header:
                header[column], lefts[column] = piece, left
    if not needed <= header.keys():
        return None
    # A tilted photo: the header's own slope tells how much each row drifts across the page.
    unique = {id(piece): piece for piece in header.values()}.values()
    centers = [_center(piece) for piece in unique]
    slope = 0.0
    spread = sum((x - statistics.fmean(c[0] for c in centers)) ** 2 for x, _ in centers)
    if len(centers) >= 2 and spread:
        mean_x, mean_y = statistics.fmean(x for x, _ in centers), statistics.fmean(y for _, y in centers)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in centers) / spread
    origin_x = min(x for x, _ in centers)

    def level(piece: dict[str, Any]) -> float:  # height on the page once the tilt is taken out
        x, y = _center(piece)
        return y - slope * (x - origin_x)

    height = statistics.median(p["box"][3] - p["box"][1] for p in pieces) or 1
    top = statistics.fmean(level(piece) for piece in unique) + 0.5 * height
    ends = [level(p) for p in pieces if end.search(p["text"]) and level(p) > top]
    bottom = min(ends) if ends else float("inf")
    headers = [id(piece) for piece in unique]
    pieces = [{**p, "level": level(p), "height": p["box"][3] - p["box"][1]} for p in pieces
              if id(p) not in headers and top < level(p) < bottom]
    columns = list(lefts.items())
    if not pieces:
        return []
    pieces.sort(key=lambda p: p["level"])
    gap = 0.5 * (statistics.median(p["height"] for p in pieces) or 1)
    groups = [[pieces[0]]]
    for piece in pieces[1:]:
        if abs(piece["level"] - groups[-1][-1]["level"]) < gap:
            groups[-1].append(piece)
        else:
            groups.append([piece])
    rows = []
    for group in groups:
        cells: dict[str, list[dict[str, Any]]] = {}
        for piece in sorted(group, key=lambda p: p["box"][0]):
            column = min(columns, key=lambda c: abs(c[1] - piece["box"][0]))[0]
            cells.setdefault(column, []).append(piece)
        if key not in cells:
            continue  # a stray piece (a stamp, a footnote, a serial number) with no name
        name_piece = cells[key][0]
        rows.append({
            **{column: " ".join(p["text"] for p in found) for column, found in cells.items()},
            "source_ref": _ref(page, name_piece["line"]),
            "confidence": round(min(p["confidence"] for p in group), 4),
            "box": [min(p["box"][0] for p in group), min(p["box"][1] for p in group),
                    max(p["box"][2] for p in group), max(p["box"][3] for p in group)],
        })
    return rows


def _lab_rows_by_text(page, text_lines, header) -> list[dict[str, Any]]:
    lines = _aligned_lines(page)
    rows = []
    for index in range(header + 1, len(text_lines)):
        text = text_lines[index]
        if TABLE_END.search(text):
            break
        tokens = text.split()
        at = next((i for i, token in enumerate(tokens) if i and (NUMBER.fullmatch(token)
                                                                  or token.casefold() in QUALITATIVE)), None)
        if at is None:
            continue
        rest = tokens[at + 1:]
        unit = rest[0] if rest and not re.match(r"[<>\d]", rest[0]) else None
        rows.append({
            "name": " ".join(tokens[:at]), "value": tokens[at], "unit": unit,
            "ref_range": " ".join(rest[1:] if unit else rest) or None,
            "source_ref": _ref(page, index),
            "confidence": lines[index]["confidence"] if lines else page.get("quality", 1.0),
            **({"box": lines[index].get("box")} if lines else {}),
        })
    return rows


MEDICINE_LINE = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(?P<form>tab(?:let)?|cap(?:sule)?|syp|syrup|inj(?:ection)?|drops?|oint(?:ment)?|gel|cream)"
    r"\.?\s+(?P<rest>.+)$", re.IGNORECASE)
STRENGTH = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|%)(?=\s|$|[,;)])", re.IGNORECASE)
FREQUENCY = re.compile(r"\b(?:\d\s*-\s*\d\s*-\s*\d|OD|BD|BID|TDS|TID|QID|HS|SOS|once daily|twice daily|"
                       r"thrice daily|at night|after food|before food)\b", re.IGNORECASE)
DURATION = re.compile(r"(?:\bx|\bfor)\s*\d+\s*(?:days?|weeks?|months?)\b", re.IGNORECASE)


def medicine_rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _aligned_lines(page)
    rows = []
    for index, text in enumerate(page.get("text", "").splitlines()):
        match = MEDICINE_LINE.match(text)
        if not match:
            continue
        rest = match["rest"]
        strength, frequency, duration = STRENGTH.search(rest), FREQUENCY.search(rest), DURATION.search(rest)
        cut = min([m.start() for m in (strength, frequency, duration) if m] + [len(rest)])
        rows.append({
            "name": rest[:cut].strip(" ,-"), "form": match["form"],
            "strength": strength.group(0) if strength else None,
            "frequency": frequency.group(0) if frequency else None,
            "duration": duration.group(0) if duration else None,
            "source_ref": _ref(page, index),
            "confidence": lines[index]["confidence"] if lines else page.get("quality", 1.0),
            **({"box": lines[index].get("box")} if lines else {}),
        })
    return rows


ITEM_HEADER = re.compile(r"\b(description|particulars|item|service)s?\b.*\b(amount|rate|price|total)\b", re.I)
TOTAL_LINE = re.compile(r"\b(sub\s*total|grand\s*total|total|net\s*amount|amount\s*payable|net\s*payable|gst|"
                        r"discount|paid|balance|round\s*off|cgst|sgst)\b", re.IGNORECASE)
TRAILING_AMOUNT = re.compile(r"^(?P<text>.*?)\s+(?P<amount>(?:₹|Rs\.?)?\s*\d[\d,]*\.\d{2})$")


def item_rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    lines = _aligned_lines(page)
    if lines and any(line.get("parts") for line in lines):
        # Same column reader as lab tables: a tilted photo can't move an amount to another item.
        rows = _rows_by_column(page, lines, BILL_COLUMNS, key="description", needed={"description", "amount"},
                               end=TOTAL_LINE)
        if rows is not None:
            return [{"description": row["description"], "amount": row.get("amount"),
                     **{k: row[k] for k in ROW_META if k in row}} for row in rows]
    text_lines = page.get("text", "").splitlines()
    header = next((i for i, line in enumerate(text_lines) if ITEM_HEADER.search(line)), None)
    if header is None:
        return []
    rows = []
    for index in range(header + 1, len(text_lines)):
        text = text_lines[index]
        if TOTAL_LINE.search(text):
            break
        match = TRAILING_AMOUNT.match(text.strip())
        if not match:
            continue
        description = re.sub(r"^\d+[.)]?\s+", "", match["text"]).strip()  # drop a serial number
        description = re.sub(r"(\s+\d+(?:\.\d+)?){1,2}$", "", description)  # drop qty / rate columns
        rows.append({
            "description": description, "amount": match["amount"],
            "source_ref": _ref(page, index),
            "confidence": lines[index]["confidence"] if lines else page.get("quality", 1.0),
            **({"box": lines[index].get("box")} if lines else {}),
        })
    return rows


READERS = {"tests": lab_rows, "medicines": medicine_rows, "items": item_rows}

MEDICAL_WORDS = re.compile(
    r"\b(?:mg/dl|g/dl|mmol/l|iu/ml|uiu/ml|haemoglobin|hemoglobin|pathology|laboratory|diagnostics?|"
    r"patient|prescription|rx|tab\.|cap\.|syp\.|diagnosis|pharmacy|hospital|clinic|dr\.|mbbs|"
    r"reference range|discharge)(?=\W|$)", re.IGNORECASE)


def looks_medical(text: str) -> bool:
    """True if the page has words typical of medical papers. Used to keep such pages off the
    cloud model unless the senior agreed (Decision #29); a false 'yes' only means less cloud."""
    return bool(MEDICAL_WORDS.search(text))


def medical_type_from_text(text: str) -> str | None:
    """Lab report, prescription or bill from the page's own words; None if unclear."""
    lines = text.splitlines()
    if any(re.search(r"\b(test|investigation|parameter)\b", l, re.I) and re.search(r"\b(result|value)s?\b", l, re.I)
           for l in lines):
        return "lab_report"
    if sum(bool(MEDICINE_LINE.match(line)) for line in lines) >= 1 and re.search(r"\b(rx|dr\.?)\b", text, re.I):
        return "prescription"
    if any(ITEM_HEADER.search(line) for line in lines) and re.search(r"\btotal\b", text, re.I):
        return "medical_bill"
    return None


def extract_lists(pages: list[dict[str, Any]], document_type: str | None) -> dict[str, dict[str, Any]]:
    """The list field for a medical document type, as one extracted field holding all rows."""
    key = LIST_FOR_TYPE.get(document_type or "")
    if not key:
        return {}
    rows = [row for page in pages for row in READERS[key](page)]
    if not rows:
        return {}
    first = rows[0]["source_ref"]
    page_number, line_number = int(first.split(":")[1]), int(first.split(":")[3])
    return {key: {"value": rows, "page": page_number, "line": line_number, "source_ref": first,
                  "confidence": min(row["confidence"] for row in rows)}}


HEADING_FIELDS = {"lab_report": "lab_name", "medical_bill": "provider"}
DOCTOR = re.compile(r"^\s*(Dr\.?\s+[A-Z][A-Za-z.\s]+?)(?:\s*[,(|]|\s{2,}|$)")


def heading_fields(pages: list[dict[str, Any]], document_type: str | None, found: dict[str, Any]) -> dict[str, Any]:
    """Unlabelled values printed in fixed places: the lab or hospital name in the heading,
    the doctor on a line starting with 'Dr.'. Marked so the owner checks them."""
    extra: dict[str, Any] = {}
    page = pages[0] if pages else None
    if not page:
        return extra
    lines = _aligned_lines(page)
    text_lines = page.get("text", "").splitlines()

    def item(value: str, index: int, layout: str) -> dict[str, Any]:
        line = lines[index] if lines else {}
        return {"value": value, "page": page["page"], "line": index + 1, "source_ref": _ref(page, index),
                "confidence": line.get("confidence", page.get("quality", 1.0)), "box": line.get("box"),
                "layout": layout}

    heading_key = HEADING_FIELDS.get(document_type or "")
    if heading_key and heading_key not in found:
        for index, text in enumerate(text_lines[:3]):
            letters = sum(c.isalpha() for c in text)
            if ":" not in text and letters >= 6 and letters / max(len(text), 1) > 0.6:
                extra[heading_key] = item(text.strip(), index, "heading")
                break
    if document_type == "prescription" and "doctor_name" not in found:
        for index, text in enumerate(text_lines):
            if match := DOCTOR.match(text):
                extra["doctor_name"] = item(match.group(1).strip(), index, "doctor_line")
                break
    return extra
