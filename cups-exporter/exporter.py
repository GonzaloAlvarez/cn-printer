#!/usr/bin/env python3
"""cn-printer cups-exporter — Prometheus metrics for a CUPS server and, when
PRINTER_IPP_URI is set, for the printer device itself (ipp-usb endpoint):
IPP attributes plus the Brother's public "Maintenance Information" web page
(brother_ews.py), which carries the lifetime counters IPP lacks.

Env: CUPS_SERVER (host:port, default 127.0.0.1:631), EXPORTER_PORT (9329),
PRINTER_IPP_URI (optional, e.g. ipp://172.30.6.1:60000/ipp/print),
PRINTER_EWS_URL (default: http://<PRINTER_IPP_URI host:port>; empty disables),
PRINTER_EWS_PATH (/general/information.html?kind=item),
PRINTER_EWS_INTERVAL (seconds between page fetches, 300), PRINTER_EWS_TIMEOUT (10).

Metrics (gauges unless noted):
  cups_up                                   CUPS answered the scrape
  cups_printers                             number of queues
  cups_printer_state{printer}               3 idle, 4 processing, 5 stopped
  cups_printer_accepting{printer}           1/0
  cups_printer_shared{printer}              1/0
  cups_printer_state_reason{printer,reason} 1 per active reason (media-empty-error, toner-low-warning …)
  cups_jobs{printer,state}                  not-completed jobs by state (pending|held|processing|stopped)
  cups_jobs_completed_total                 jobs in the completed history (counter; resets with MaxJobs)
  cups_job_oldest_pending_age_seconds{printer}
  printer_marker_level_percent{printer,marker,type,color}
  printer_impressions_total{printer}        printer-impressions-completed when the device reports it (counter)
  printer_device_state{printer}             device's own printer-state (via PRINTER_IPP_URI)
  printer_device_state_reason{printer,reason}
  cups_scrape_duration_seconds
Brother maintenance page (counters are lifetime totals, so `increase()` works):
  brother_ews_up                            the maintenance page was fetched and parsed on the last attempt
  brother_ews_last_success_timestamp_seconds
  brother_info{model,firmware}              1
  brother_pages_printed_total               "Total Pages Printed › Total" (== Page Counter)
  brother_pages_printed_detail_total{breakdown=size|type|function,item}
  brother_pages_printed_duplex_total{function}
  brother_pages_scanned_total               "Scan Page Count"
  brother_pages_scanned_detail_total{source=adf_simplex|adf_duplex|flatbed}
  brother_drum_life_percent / brother_toner_life_percent / brother_average_coverage_percent
  brother_replacements_total{part=toner|drum}
  brother_paper_jams_total{location=total|tray1|inside|rear|duplex|adf_simplex|adf_duplex}
  brother_error_history{index,error}        page count at which the error happened (index 1 = most recent)
"""
import os
import time
import urllib.parse

import cups
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily

import brother_ews

CUPS_SERVER = os.environ.get("CUPS_SERVER", "127.0.0.1:631")
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "9329"))
PRINTER_IPP_URI = os.environ.get("PRINTER_IPP_URI", "")


def _default_ews_url():
    if not PRINTER_IPP_URI:
        return ""
    u = urllib.parse.urlparse(PRINTER_IPP_URI)
    return f"http://{u.hostname}:{u.port or 631}" if u.hostname else ""


PRINTER_EWS_URL = os.environ.get("PRINTER_EWS_URL", _default_ews_url()).rstrip("/")
PRINTER_EWS_PATH = os.environ.get("PRINTER_EWS_PATH", "/general/information.html?kind=item")
PRINTER_EWS_INTERVAL = int(os.environ.get("PRINTER_EWS_INTERVAL", "300"))
PRINTER_EWS_TIMEOUT = int(os.environ.get("PRINTER_EWS_TIMEOUT", "10"))
EWS = brother_ews.EwsCache(PRINTER_EWS_URL + PRINTER_EWS_PATH if PRINTER_EWS_URL else "", PRINTER_EWS_INTERVAL, PRINTER_EWS_TIMEOUT)

JOB_STATES = {3: "pending", 4: "held", 5: "processing", 6: "stopped", 7: "canceled", 8: "aborted", 9: "completed"}
MARKER_ATTRS = ["marker-levels", "marker-names", "marker-types", "marker-colors"]


def _split_server(server):
    host, _, port = server.rpartition(":")
    if not host:
        return server, 631
    return host, int(port)


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _lbl(seq, i, fallback):
    """pycups renders some device-side attribute tags as "(unknown IPP value tag …)";
    keep only clean strings as label values."""
    v = seq[i] if i < len(seq) else None
    if isinstance(v, str) and v and not v.startswith("("):
        return v
    return fallback


def _markers(metric, label, attrs):
    levels = _as_list(attrs.get("marker-levels"))
    names = _as_list(attrs.get("marker-names"))
    types = _as_list(attrs.get("marker-types"))
    colors = _as_list(attrs.get("marker-colors"))
    for i, lvl in enumerate(levels):
        if isinstance(lvl, int) and lvl >= 0:
            metric.add_metric([label, _lbl(names, i, f"marker{i}"), _lbl(types, i, ""), _lbl(colors, i, "")], lvl)


def _brother_metrics():
    """Metric families for the Brother maintenance page snapshot (always yields brother_ews_up when configured)."""
    if not EWS.url:
        return []
    data, up_now, last_ok = EWS.snapshot()
    ews_up = GaugeMetricFamily("brother_ews_up", "1 if the Brother maintenance page was fetched and parsed on the last attempt")
    ews_up.add_metric([], 1 if up_now else 0)
    last = GaugeMetricFamily("brother_ews_last_success_timestamp_seconds", "Unix time of the last successful maintenance-page parse")
    last.add_metric([], last_ok)
    fams = [ews_up, last]
    if not data:
        return fams
    info = GaugeMetricFamily("brother_info", "Brother model and main firmware from the maintenance page", labels=["model", "firmware"])
    info.add_metric([data.get("model", ""), data.get("firmware", "")], 1)
    printed = CounterMetricFamily("brother_pages_printed", "Lifetime pages printed (Total Pages Printed › Total)")
    detail = CounterMetricFamily("brother_pages_printed_detail", "Lifetime pages printed by paper size, paper type or function", labels=["breakdown", "item"])
    duplex = CounterMetricFamily("brother_pages_printed_duplex", "Lifetime 2-sided pages by function", labels=["function"])
    scanned = CounterMetricFamily("brother_pages_scanned", "Lifetime pages scanned (Scan Page Count)")
    scan_detail = CounterMetricFamily("brother_pages_scanned_detail", "Lifetime pages scanned by source", labels=["source"])
    drum = GaugeMetricFamily("brother_drum_life_percent", "Drum unit remaining life")
    toner = GaugeMetricFamily("brother_toner_life_percent", "Toner remaining life (maintenance page)")
    coverage = GaugeMetricFamily("brother_average_coverage_percent", "Average toner coverage per page")
    repl = CounterMetricFamily("brother_replacements", "Consumable replacement count", labels=["part"])
    jams = CounterMetricFamily("brother_paper_jams", "Lifetime paper jams by location", labels=["location"])
    errors = GaugeMetricFamily("brother_error_history", "Last errors (value = page counter when it happened; index 1 = most recent)", labels=["index", "error"])
    total = data["printed_by_function"].get("total", data.get("page_counter"))
    if total is not None:
        printed.add_metric([], total)
    for key, label in (("printed_by_size", "size"), ("printed_by_type", "type"), ("printed_by_function", "function")):
        for item, v in data[key].items():
            if v is not None:
                detail.add_metric([label, item], v)
    for fn, v in data["duplex_by_function"].items():
        if v is not None:
            duplex.add_metric([fn], v)
    if data.get("scanned_total") is not None:
        scanned.add_metric([], data["scanned_total"])
    for src, v in data["scanned_by_source"].items():
        if v is not None:
            scan_detail.add_metric([src], v)
    if data.get("drum_life_percent") is not None:
        drum.add_metric([], data["drum_life_percent"])
    if data.get("toner_life_percent") is not None:
        toner.add_metric([], data["toner_life_percent"])
    if data.get("average_coverage_percent") is not None:
        coverage.add_metric([], data["average_coverage_percent"])
    for part, v in data["replacements"].items():
        if v is not None:
            repl.add_metric([part], v)
    for loc, v in data["jams"].items():
        if v is not None:
            jams.add_metric([loc], v)
    for idx, err, pg in data["errors"]:
        errors.add_metric([str(idx), err], pg if pg is not None else 0)
    return fams + [info, printed, detail, duplex, scanned, scan_detail, drum, toner, coverage, repl, jams, errors]


class CupsCollector:
    def collect(self):
        t0 = time.time()
        up = GaugeMetricFamily("cups_up", "1 if CUPS answered the scrape")
        n_printers = GaugeMetricFamily("cups_printers", "Number of CUPS queues")
        state = GaugeMetricFamily("cups_printer_state", "CUPS printer-state (3 idle, 4 processing, 5 stopped)", labels=["printer"])
        accepting = GaugeMetricFamily("cups_printer_accepting", "printer-is-accepting-jobs", labels=["printer"])
        shared = GaugeMetricFamily("cups_printer_shared", "printer-is-shared", labels=["printer"])
        reason = GaugeMetricFamily("cups_printer_state_reason", "Active CUPS printer-state-reasons", labels=["printer", "reason"])
        jobs = GaugeMetricFamily("cups_jobs", "Not-completed jobs by state", labels=["printer", "state"])
        completed = CounterMetricFamily("cups_jobs_completed", "Jobs in the completed history (resets with MaxJobs)")
        oldest = GaugeMetricFamily("cups_job_oldest_pending_age_seconds", "Age of the oldest pending job", labels=["printer"])
        marker = GaugeMetricFamily("printer_marker_level_percent", "Marker (toner/drum) level", labels=["printer", "marker", "type", "color"])
        impressions = CounterMetricFamily("printer_impressions", "printer-impressions-completed reported by the device", labels=["printer"])
        dev_state = GaugeMetricFamily("printer_device_state", "Device printer-state via PRINTER_IPP_URI", labels=["printer"])
        dev_reason = GaugeMetricFamily("printer_device_state_reason", "Device printer-state-reasons via PRINTER_IPP_URI", labels=["printer", "reason"])
        dur = GaugeMetricFamily("cups_scrape_duration_seconds", "Scrape duration")

        host, port = _split_server(CUPS_SERVER)
        try:
            cups.setServer(host)
            cups.setPort(port)
            conn = cups.Connection()
            printers = conn.getPrinters()
            up.add_metric([], 1)
            n_printers.add_metric([], len(printers))
            counts = {}
            pending_oldest = {}
            now = time.time()
            active = conn.getJobs(which_jobs="not-completed", requested_attributes=["job-state", "job-printer-uri", "time-at-creation"])
            for _job_id, j in active.items():
                pname = j.get("job-printer-uri", "").rsplit("/", 1)[-1]
                st = JOB_STATES.get(j.get("job-state"), "unknown")
                counts[(pname, st)] = counts.get((pname, st), 0) + 1
                if st == "pending":
                    age = now - j.get("time-at-creation", now)
                    pending_oldest[pname] = max(pending_oldest.get(pname, 0), age)
            for name, p in printers.items():
                # getPrinters() omits printer-is-accepting-jobs; ask for it explicitly
                try:
                    extra = conn.getPrinterAttributes(name, requested_attributes=["printer-is-accepting-jobs", "printer-is-shared"])
                except Exception:
                    extra = {}
                state.add_metric([name], p.get("printer-state", 0))
                accepting.add_metric([name], 1 if extra.get("printer-is-accepting-jobs", p.get("printer-is-accepting-jobs")) else 0)
                shared.add_metric([name], 1 if extra.get("printer-is-shared", p.get("printer-is-shared")) else 0)
                for r in _as_list(p.get("printer-state-reasons")):
                    if r and r != "none":
                        reason.add_metric([name, r], 1)
                for st in ("pending", "held", "processing", "stopped"):
                    jobs.add_metric([name, st], counts.get((name, st), 0))
                oldest.add_metric([name], pending_oldest.get(name, 0))
                try:
                    attrs = conn.getPrinterAttributes(name, requested_attributes=MARKER_ATTRS + ["printer-impressions-completed"])
                    _markers(marker, name, attrs)
                    if "printer-impressions-completed" in attrs:
                        impressions.add_metric([name], attrs["printer-impressions-completed"])
                except Exception:
                    pass
            try:
                completed.add_metric([], len(conn.getJobs(which_jobs="completed", requested_attributes=["job-id"])))
            except Exception:
                pass
        except Exception:
            up.add_metric([], 0)

        if PRINTER_IPP_URI:
            try:
                u = urllib.parse.urlparse(PRINTER_IPP_URI)
                cups.setServer(u.hostname)
                cups.setPort(u.port or 631)
                dev = cups.Connection()
                a = dev.getPrinterAttributes(uri=PRINTER_IPP_URI, requested_attributes=["printer-state", "printer-state-reasons", "printer-impressions-completed"] + MARKER_ATTRS)
                dev_state.add_metric(["device"], a.get("printer-state", 0))
                for r in _as_list(a.get("printer-state-reasons")):
                    if r and r != "none":
                        dev_reason.add_metric(["device", r], 1)
                if "printer-impressions-completed" in a:
                    impressions.add_metric(["device"], a["printer-impressions-completed"])
                _markers(marker, "device", a)
            except Exception:
                dev_state.add_metric(["device"], 0)

        brother = _brother_metrics()

        dur.add_metric([], time.time() - t0)
        for m in (up, n_printers, state, accepting, shared, reason, jobs, completed, oldest, marker, impressions, dev_state, dev_reason, *brother, dur):
            yield m


if __name__ == "__main__":
    REGISTRY.register(CupsCollector())
    start_http_server(EXPORTER_PORT)
    print(f"cups-exporter listening on :{EXPORTER_PORT}, CUPS_SERVER={CUPS_SERVER}, PRINTER_IPP_URI={PRINTER_IPP_URI or '-'}, PRINTER_EWS_URL={PRINTER_EWS_URL or '-'} (every {PRINTER_EWS_INTERVAL}s)", flush=True)
    while True:
        time.sleep(3600)
