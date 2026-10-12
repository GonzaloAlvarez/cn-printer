#!/usr/bin/env python3
"""Generate the two kaiser Grafana dashboards for printer.lan.

Outputs (committed into cn-observability's provisioning dir):
  <cn-observability>/config/grafana/provisioning/dashboards/printer-overview.json
      host overview (same panel set as kaiser/neptune/cerberus) + Pi power/thermal
  <cn-observability>/config/grafana/provisioning/dashboards/cups.json
      CUPS print server: queue, jobs, printer state reasons, toner, pages (Loki)

Re-run after editing; keep uids stable (printer-overview, cups). Metric sources:
node-exporter-lan (host=printer), amun-printer's rpi.prom textfile, cn-printer's
cups-exporter (job=printer-cups), blackbox-http/https probes, promtail-lan (Loki).

    ./build-dashboards.py [path/to/cn-observability]   (default ../../cn-observability)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
H = 'host="printer"'
Q = 'printer="Brother_MFC-L2750DW"'


def q(expr, legend=None, ref="A", instant=False, ds=PROM):
    t = {"datasource": ds, "expr": expr, "refId": ref}
    if legend:
        t["legendFormat"] = legend
    if instant:
        t["instant"] = True
    return t


def row(pid, title, y):
    return {"id": pid, "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def stat(pid, title, targets, x, y, w, h, unit=None, thresholds=None, decimals=None, mappings=None, color_mode="value", graph="none"):
    steps = thresholds or [{"color": "green", "value": None}]
    fc = {"unit": unit} if unit else {}
    if decimals is not None:
        fc["decimals"] = decimals
    fc["thresholds"] = {"mode": "absolute", "steps": steps}
    if mappings:
        fc["mappings"] = mappings
    return {"id": pid, "type": "stat", "title": title, "datasource": PROM, "targets": targets,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": graph, "textMode": "auto", "orientation": "auto"}}


def ts(pid, title, targets, x, y, w, h, unit=None, min_=None, max_=None, stack=False, thresholds=None, ds=PROM):
    fc = {"custom": {"lineWidth": 1, "fillOpacity": 10, "showPoints": "never"}}
    if stack:
        fc["custom"]["stacking"] = {"mode": "normal", "group": "A"}
    if unit:
        fc["unit"] = unit
    if min_ is not None:
        fc["min"] = min_
    if max_ is not None:
        fc["max"] = max_
    if thresholds:
        fc["thresholds"] = {"mode": "absolute", "steps": thresholds}
        fc["custom"]["thresholdsStyle"] = {"mode": "line"}
    return {"id": pid, "type": "timeseries", "title": title, "datasource": ds, "targets": targets,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def table(pid, title, targets, x, y, w, h):
    return {"id": pid, "type": "table", "title": title, "datasource": PROM, "targets": targets,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {}, "overrides": []},
            "transformations": [{"id": "labelsToFields", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True, "job": True, "instance": True, "host": True, "stack": True, "site": True}}}],
            "options": {"showHeader": True}}


def logs(pid, title, expr, x, y, w, h):
    return {"id": pid, "type": "logs", "title": title, "datasource": LOKI,
            "targets": [{"datasource": LOKI, "expr": expr, "refId": "A"}],
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                        "dedupStrategy": "none", "enableLogDetails": True, "prettifyLogMessage": False}}


def dashboard(uid, title, tags, panels, time_from="now-3h"):
    return {"annotations": {"list": []}, "editable": True, "graphTooltip": 1, "links": [],
            "panels": panels, "refresh": "30s", "schemaVersion": 39, "tags": tags,
            "templating": {"list": []}, "time": {"from": time_from, "to": "now"},
            "timezone": "browser", "title": title, "uid": uid, "version": 1}


UPDOWN = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
YESNO = [{"type": "value", "options": {"0": {"text": "no", "color": "green"}, "1": {"text": "YES", "color": "red"}}}]
RED = [{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]
GREEN_ON_1 = [{"color": "red", "value": None}, {"color": "green", "value": 1}]


# ── Printer host overview (kaiser/neptune/cerberus panel set + Pi power/thermal) ──
def printer_overview():
    p = []
    y = 0
    p.append(row(1, "At a glance", y)); y += 1
    p.append(stat(2, "CPU usage", [q(f'(1 - avg(rate(node_cpu_seconds_total{{{H},mode="idle"}}[5m]))) * 100', "cpu %")], 0, y, 4, 5, unit="percent", decimals=0, thresholds=RED, color_mode="background", graph="area"))
    p.append(stat(3, "Memory used", [q(f'(1 - node_memory_MemAvailable_bytes{{{H}}} / node_memory_MemTotal_bytes{{{H}}}) * 100', "mem %")], 4, y, 4, 5, unit="percent", decimals=0, thresholds=RED, color_mode="background", graph="area"))
    p.append(stat(4, "Disk used (/ on the SSD)", [q(f'(1 - node_filesystem_avail_bytes{{{H},mountpoint="/"}} / node_filesystem_size_bytes{{{H},mountpoint="/"}}) * 100', "disk %")], 8, y, 4, 5, unit="percent", decimals=0, thresholds=RED, color_mode="background", graph="area"))
    p.append(stat(5, "SoC temperature", [q(f'rpi_soc_temp_celsius{{{H}}} or node_hwmon_temp_celsius{{{H}}}', "°C")], 12, y, 4, 5, unit="celsius", decimals=0,
                  thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 80}], color_mode="background", graph="area"))
    p.append(stat(6, "Under-voltage NOW", [q(f'rpi_undervoltage_now{{{H}}}')], 16, y, 4, 5, mappings=YESNO, color_mode="background",
                  thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]))
    p.append(stat(7, "Under-voltage since boot", [q(f'rpi_undervoltage_occurred{{{H}}}')], 20, y, 4, 5, mappings=YESNO, color_mode="background",
                  thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}]))
    y += 5
    p.append(stat(8, "Uptime", [q(f'time() - node_boot_time_seconds{{{H}}}')], 0, y, 4, 4, unit="s", decimals=0))
    p.append(stat(9, "Throttled now", [q(f'rpi_throttled_now{{{H}}}')], 4, y, 4, 4, mappings=YESNO, color_mode="background", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]))
    p.append(stat(10, "Core voltage", [q(f'rpi_core_volts{{{H}}}')], 8, y, 4, 4, unit="volt", decimals=3))
    p.append(stat(11, "Resilience boot unit (cn-printer)", [q('resilience_boot_unit_active{stack="cn-printer"}')], 12, y, 4, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(12, "Reconcile repairs (24h)", [q('sum(increase(resilience_reconcile_repairs_total{stack="cn-printer"}[24h]))')], 16, y, 4, 4, decimals=0,
                  thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]))
    p.append(stat(13, "Load (1m)", [q(f'node_load1{{{H}}}')], 20, y, 4, 4, decimals=2))
    y += 4
    p.append(row(20, "Power and thermal (the reboot investigation)", y)); y += 1
    p.append(ts(21, "SoC temperature", [q(f'rpi_soc_temp_celsius{{{H}}}', "SoC °C")], 0, y, 12, 7, unit="celsius", min_=20,
                thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 80}]))
    p.append(ts(22, "Under-voltage / throttling flags (1 = active)", [
        q(f'rpi_undervoltage_now{{{H}}}', "under-voltage now"),
        q(f'rpi_throttled_now{{{H}}}', "throttled now", ref="B"),
        q(f'rpi_frequency_capped_now{{{H}}}', "freq capped now", ref="C"),
        q(f'rpi_undervoltage_occurred{{{H}}}', "under-voltage since boot", ref="D")], 12, y, 12, 7, min_=0, max_=1))
    y += 7
    p.append(row(30, "Trends", y)); y += 1
    p.append(ts(31, "CPU usage by mode", [q(f'avg by (mode) (rate(node_cpu_seconds_total{{{H}}}[5m])) * 100', "{{mode}}")], 0, y, 12, 8, unit="percent", min_=0, stack=True))
    p.append(ts(32, "Memory", [q(f'node_memory_MemTotal_bytes{{{H}}}', "total"),
                               q(f'node_memory_MemAvailable_bytes{{{H}}}', "available", ref="B"),
                               q(f'node_memory_MemTotal_bytes{{{H}}} - node_memory_MemAvailable_bytes{{{H}}}', "used", ref="C")], 12, y, 12, 8, unit="bytes", min_=0))
    y += 8
    p.append(ts(33, "Network throughput (wlan0 and friends)", [
        q(f'rate(node_network_receive_bytes_total{{{H},device!~"lo|docker.*|br-.*|veth.*|tailscale.*"}}[5m]) * 8', "rx {{device}}"),
        q(f'rate(node_network_transmit_bytes_total{{{H},device!~"lo|docker.*|br-.*|veth.*|tailscale.*"}}[5m]) * 8', "tx {{device}}", ref="B")], 0, y, 12, 8, unit="bps", min_=0))
    p.append(ts(34, "Disk I/O (USB SSD)", [q(f'rate(node_disk_read_bytes_total{{{H},device!~"loop.*|dm-.*|mmcblk.*"}}[5m])', "read {{device}}"),
                                           q(f'rate(node_disk_written_bytes_total{{{H},device!~"loop.*|dm-.*|mmcblk.*"}}[5m])', "write {{device}}", ref="B")], 12, y, 12, 8, unit="Bps", min_=0))
    y += 8
    p.append(ts(35, "Filesystem usage", [
        q(f'(1 - node_filesystem_avail_bytes{{{H},mountpoint="/"}} / node_filesystem_size_bytes{{{H},mountpoint="/"}}) * 100', "/"),
        q(f'(1 - node_filesystem_avail_bytes{{{H},mountpoint="/boot/firmware"}} / node_filesystem_size_bytes{{{H},mountpoint="/boot/firmware"}}) * 100', "/boot/firmware", ref="B")], 0, y, 12, 7, unit="percent", min_=0, max_=100,
        thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 90}]))
    p.append(ts(36, "Load average", [q(f'node_load1{{{H}}}', "1m"), q(f'node_load5{{{H}}}', "5m", ref="B"), q(f'node_load15{{{H}}}', "15m", ref="C")], 12, y, 12, 7, min_=0))
    y += 7
    p.append(row(40, "Logs", y)); y += 1
    p.append(logs(41, "Recent errors / warnings (journal + containers)", f'{{{H}}} |~ "(?i)error|warning|fatal|critical|undervoltage"', 0, y, 24, 10))
    return dashboard("printer-overview", "Printer host overview", ["printer", "homelab", "node-exporter", "raspberry-pi"], p)


# ── CUPS print server ──
def cups_dashboard():
    p = []
    y = 0
    p.append(row(1, "At a glance", y)); y += 1
    p.append(stat(2, "cupsd", [q(f'cups_up{{{H}}}')], 0, y, 3, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(3, "Queue state", [q(f'cups_printer_state{{{H},{Q}}}')], 3, y, 3, 4, color_mode="background",
                  mappings=[{"type": "value", "options": {"3": {"text": "idle", "color": "green"}, "4": {"text": "printing", "color": "blue"}, "5": {"text": "STOPPED", "color": "red"}}}],
                  thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 3}, {"color": "blue", "value": 4}, {"color": "red", "value": 5}]))
    p.append(stat(4, "Accepting jobs", [q(f'cups_printer_accepting{{{H},{Q}}}')], 6, y, 3, 4, color_mode="background", thresholds=GREEN_ON_1,
                  mappings=[{"type": "value", "options": {"0": {"text": "NO", "color": "red"}, "1": {"text": "yes", "color": "green"}}}]))
    p.append(stat(5, "Pending", [q(f'sum(cups_jobs{{{H},state="pending"}})')], 9, y, 3, 4, decimals=0,
                  thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}], color_mode="background"))
    p.append(stat(6, "Printing", [q(f'sum(cups_jobs{{{H},state="processing"}})')], 12, y, 3, 4, decimals=0))
    p.append(stat(7, "Oldest pending job", [q(f'max(cups_job_oldest_pending_age_seconds{{{H}}})')], 15, y, 3, 4, unit="s", decimals=0,
                  thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 300}, {"color": "red", "value": 900}], color_mode="background"))
    p.append(stat(8, "Toner / drum", [q(f'printer_marker_level_percent{{{H},printer="device"}}', "{{marker}}"), q(f'printer_marker_level_percent{{{H},{Q}}}', "{{marker}}", ref="B")], 18, y, 6, 4, unit="percent", decimals=0,
                  thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 10}, {"color": "green", "value": 25}], color_mode="value"))
    y += 4
    p.append(stat(9, "CUPS web/IPP :631", [q('probe_success{instance="http://printer.lan:631/"}')], 0, y, 4, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(10, "Scan UI scan.print.lan", [q('probe_success{instance="https://scan.print.lan/api/v1/system"}')], 4, y, 4, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(11, "swis :5520", [q('probe_success{instance="http://printer.lan:5520/app/"}')], 8, y, 4, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(12, "cups-exporter scrape", [q('up{job="printer-cups"}')], 12, y, 4, 4, mappings=UPDOWN, color_mode="background", thresholds=GREEN_ON_1))
    p.append(stat(13, "Device state (ipp-usb)", [q(f'printer_device_state{{{H},printer="device"}}')], 16, y, 4, 4, color_mode="background",
                  mappings=[{"type": "value", "options": {"0": {"text": "unreachable", "color": "red"}, "3": {"text": "idle", "color": "green"}, "4": {"text": "printing", "color": "blue"}, "5": {"text": "stopped", "color": "red"}}}],
                  thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 3}]))
    p.append(stat(14, "Jobs completed (24h)", [q(f'increase(cups_jobs_completed_total{{{H}}}[24h])')], 20, y, 4, 4, decimals=0))
    y += 4
    p.append(row(20, "Jobs", y)); y += 1
    p.append(ts(21, "Jobs in the queue by state", [q(f'sum by (state) (cups_jobs{{{H}}})', "{{state}}")], 0, y, 12, 7, min_=0, stack=True))
    p.append(ts(22, "Jobs completed per hour", [q(f'increase(cups_jobs_completed_total{{{H}}}[1h])', "completed")], 12, y, 12, 7, min_=0))
    y += 7
    p.append(table(23, "Printer state reasons (CUPS + device)", [q(f'cups_printer_state_reason{{{H}}} == 1', instant=True), q(f'printer_device_state_reason{{{H}}} == 1', ref="B", instant=True)], 0, y, 12, 7))
    p.append(ts(24, "Toner / drum level", [q(f'printer_marker_level_percent{{{H}}}', "{{printer}} {{marker}}")], 12, y, 12, 7, unit="percent", min_=0, max_=100,
                thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 10}]))
    y += 7
    p.append(row(30, "Pages (CUPS page_log via Loki)", y)); y += 1
    p.append(ts(31, "Pages printed per day", [q('sum(count_over_time({job="cups-page-log"} [1d]))', "pages", ds=LOKI)], 0, y, 12, 7, min_=0, ds=LOKI))
    p.append(ts(32, "Pages per user (per day)", [q('sum by (user) (count_over_time({job="cups-page-log"} | pattern "<printer> <user> <job> <_>" [1d]))', "{{user}}", ds=LOKI)], 12, y, 12, 7, min_=0, stack=True, ds=LOKI))
    y += 7
    p.append(logs(33, "page_log", '{job="cups-page-log"}', 0, y, 12, 9))
    p.append(logs(34, "cupsd error_log (warnings and errors)", '{job="cups-error-log"} |~ "^[EW] "', 12, y, 12, 9))
    y += 9
    p.append(row(40, "Containers", y)); y += 1
    p.append(logs(41, "cn-printer containers", '{stack="cn-printer", service=~"cups|scanservjs|swis|cups-exporter|ts-printer"}', 0, y, 24, 9))
    return dashboard("cups", "CUPS print server (cn-printer)", ["printer", "cn-printer", "cups", "homelab"], p, time_from="now-24h")


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n")
    print("wrote", path, f"({len(obj['panels'])} panels)")


if __name__ == "__main__":
    obs = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent.parent / "cn-observability"
    out = obs / "config/grafana/provisioning/dashboards"
    if not out.is_dir():
        sys.exit(f"cn-observability provisioning dir not found: {out}")
    write(out / "printer-overview.json", printer_overview())
    write(out / "cups.json", cups_dashboard())
