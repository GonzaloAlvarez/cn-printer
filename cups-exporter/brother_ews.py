"""Brother embedded web server (EWS) scraper for the cups-exporter.

The Brother's own web server is reachable through ipp-usb (the same
http://<ipp-usb host:port>/ the IPP endpoint lives on) and its
"Maintenance Information" page (/general/information.html?kind=item) is
public: page counters, drum/toner life, jam and replacement counters and
the last ten errors. IPP gives none of that (the Brother reports no
printer-impressions-completed), and the CSV export needs the admin login.

Pure stdlib. parse_maintenance() turns the page into a dict; EwsCache
fetches it on a TTL so Prometheus scrapes never hammer the printer.
"""
import html
import re
import time
import urllib.request

PRINTED_FUNCTIONS = {"Total": "total", "Fax": "fax", "Copy": "copy", "Print": "print", "Others": "others"}
SCAN_SOURCES = {"ADF(1-sided)": "adf_simplex", "ADF(2-sided)": "adf_duplex", "Flatbed": "flatbed"}
SCAN_TOTAL = "Scan Page Count"
REPLACE_PARTS = {"Toner": "toner", "Drum Unit": "drum"}
JAM_LOCATIONS = {
    "Total Paper Jams": "total", "Jam Tray 1": "tray1", "Jam Inside": "inside", "Jam Rear": "rear",
    "Jam 2-sided": "duplex", "Total Paper Jams (ADF 1-sided)": "adf_simplex", "Total Paper Jams (ADF 2-sided)": "adf_duplex",
}

_TAG = re.compile(r"<[^>]+>")
_GROUP = re.compile(r'<div class="contentsGroup">(.*?)</div>', re.S)
_TITLE = re.compile(r"<h3>(.*?)</h3>", re.S)
_ITEM = re.compile(r'<dt(?P<sub> class="subhead")?>(?P<label>.*?)</dt>\s*<dd>(?P<value>.*?)</dd>', re.S)
_ERR_ROW = re.compile(r"<tr>\s*<th>(\d+)</th>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", re.S)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _text(fragment):
    """Tags out, entities (&#32;, &nbsp;) decoded, whitespace folded, footnote stars dropped."""
    t = html.unescape(_TAG.sub(" ", fragment)).replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"\*+$", "", t).strip()


def _num(text):
    m = _NUM.search(text or "")
    if not m:
        return None
    return float(m.group()) if "." in m.group() else int(m.group())


def _items(group):
    return [(bool(m.group("sub")), _text(m.group("label")), _text(m.group("value"))) for m in _ITEM.finditer(group)]


def parse_maintenance(page):
    """Return a dict with: model, firmware, page_counter, average_coverage_percent, drum_life_percent,
    toner_life_percent, printed_by_size{}, printed_by_type{}, printed_by_function{}, duplex_by_function{},
    scanned_total, scanned_by_source{}, replacements{}, jams{}, errors[(index, error, page)]."""
    page = re.sub(r"<script.*?</script>", "", page, flags=re.S)
    out = {"printed_by_size": {}, "printed_by_type": {}, "printed_by_function": {}, "duplex_by_function": {},
           "scanned_by_source": {}, "replacements": {}, "jams": {}, "errors": []}
    for group in _GROUP.findall(page):
        tm = _TITLE.search(group)
        title = _text(tm.group(1)) if tm else ""
        items = _items(group)
        labels = [lbl for sub, lbl, _ in items if not sub]
        if title == "Node Information":
            d = {lbl: val for _, lbl, val in items}
            out["model"] = d.get("Model Name", "")
            out["firmware"] = d.get("Main Firmware Version", "")
        elif "Page Counter" in labels:
            for _, lbl, val in items:
                if lbl == "Page Counter":
                    out["page_counter"] = _num(val)
                elif lbl.startswith("Average Coverage"):
                    out["average_coverage_percent"] = _num(val)
        elif title == "Remaining Life":
            for _, lbl, val in items:
                if lbl.startswith("Drum"):
                    out["drum_life_percent"] = _num(val)
                elif lbl.startswith("Toner"):
                    out["toner_life_percent"] = _num(val)
        elif title == "Total Pages Printed":
            if "A4/Letter" in labels:
                out["printed_by_size"] = {lbl: _num(val) for sub, lbl, val in items if not sub}
            elif "Plain/Thin/Recycled" in labels:
                out["printed_by_type"] = {lbl: _num(val) for sub, lbl, val in items if not sub}
            elif "Total" in labels:
                parent = None
                for sub, lbl, val in items:
                    if not sub:
                        parent = PRINTED_FUNCTIONS.get(lbl, lbl.lower())
                        out["printed_by_function"][parent] = _num(val)
                    elif parent and lbl.startswith("2-sided"):
                        out["duplex_by_function"][parent] = _num(val)
        elif title == "Total Pages Scanned":
            for _, lbl, val in items:
                if lbl == SCAN_TOTAL:
                    out["scanned_total"] = _num(val)
                elif lbl in SCAN_SOURCES:
                    out["scanned_by_source"][SCAN_SOURCES[lbl]] = _num(val)
        elif title == "Replace Count":
            for _, lbl, val in items:
                if lbl in REPLACE_PARTS:
                    out["replacements"][REPLACE_PARTS[lbl]] = _num(val)
        elif any(lbl.startswith("Total Paper Jams") for lbl in labels):
            for _, lbl, val in items:
                if lbl in JAM_LOCATIONS:
                    out["jams"][JAM_LOCATIONS[lbl]] = _num(val)
        elif title.startswith("Error History"):
            for idx, err, pg in _ERR_ROW.findall(group):
                err = _text(err)
                if err:
                    out["errors"].append((int(idx), err, _num(_text(pg))))
    if "page_counter" not in out and "total" in out["printed_by_function"]:
        out["page_counter"] = out["printed_by_function"]["total"]
    return out


def fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "cn-printer cups-exporter"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "iso-8859-1"
        return r.read().decode(charset, errors="replace")


class EwsCache:
    """Fetch + parse at most every `interval` seconds; keep the last good snapshot on failure."""

    def __init__(self, url, interval=300, timeout=10):
        self.url, self.interval, self.timeout = url, interval, timeout
        self.data, self.last_success, self.last_attempt, self.last_error = None, 0.0, 0.0, ""

    def snapshot(self):
        now = time.time()
        if self.url and now - self.last_attempt >= self.interval:
            self.last_attempt = now
            try:
                data = parse_maintenance(fetch(self.url, self.timeout))
                if "page_counter" not in data and not data["printed_by_function"]:
                    raise ValueError("maintenance page has no page counters (login page? wrong URL?)")
                self.data, self.last_success, self.last_error = data, now, ""
            except Exception as e:  # noqa: BLE001 - any failure just marks the EWS down
                self.last_error = f"{type(e).__name__}: {e}"
        return self.data, (self.last_success > 0 and self.last_success == self.last_attempt), self.last_success
