#
#    Copyright (c) 2026
#
#    Distributed under the terms of the GNU General Public License (GPLv3)
#
"""livetag - live (LOOP packet) values for WeeWX reports.

Vanilla WeeWX only refreshes its web pages once per archive interval. This
extension adds a ``$live`` tag that behaves like ``$current`` but is backed by
the most recent LOOP data, and keeps updating in the browser between report
runs.

It has two halves:

``LiveService``  (a WeeWX service)
    Binds to NEW_LOOP_PACKET and merges every packet into an in-memory "latest
    values" record (partial packets are merged, transient observations such as
    ``rain`` are not carried forward, and values that have not been refreshed
    for ``stale_age`` seconds are dropped). A background thread then writes a
    small JSON file into the HTML_ROOT of every report that uses ``$live``.
    Values in the JSON are converted and formatted with *that report's* units,
    formats and labels, so they match the rest of the page. File writing never
    happens on the main loop thread.

``LiveTags``  (a Cheetah search list extension)
    Provides ``$live`` to templates:

        $live.outTemp               live span: "21.3°C"
        $live.outTemp.value         live span, no label: "21.3"
        $live.windDir.compass       live span, ordinal direction: "NNW"
        $live.outTemp.label         static unit label: "°C"
        $live.outTemp.static        plain (non-live) formatted value
        $live.outTemp.raw           raw number (as for any ValueHelper)
        $live.outTemp.format("%.2f")   static, custom format
        $live.dateTime              live span with the packet time
        $live.script                <script> that polls the JSON and updates
                                    every live span. Include once per page.
        $live.script(interval=10, url='../live.json')
        $live.available             True if LOOP data was available at render
        $live.source                'loop', 'archive' or 'none'
        $live.url                   JSON file name for this report

    To make $current live in one template (e.g. Seasons' index.html.tmpl),
    put this near the top of that template:

        #set global $current = $live

    $current.xxx then renders live spans (including .ordinal_compass), falling
    back to the archive record for observations not in the LOOP data, and
    $current(timestamp=..., max_delta=..., data_binding=...) still behaves
    exactly like the stock tag. Other templates (RSS, NOAA...) are unaffected.

    Live spans look like:

        <span class="weewx-live" data-live="outTemp" data-live-field="formatted">21.3°C</span>

    so you can also write them by hand, or hook the ``weewx-live`` DOM event
    (``event.detail`` is the JSON payload) to drive gauges or charts.

Configuration (weewx.conf, all optional):

    [Live]
        enable = true
        filename = live.json        # JSON written to each report's HTML_ROOT
        min_interval = 2.5          # minimum seconds between JSON writes
        stale_age = 300             # drop values not seen for this long (0 = never)
        transient = rain, hail, lightning_strike_count
        include = ""                # only these observations (empty = all)
        exclude = ""                # never these observations
        poll_interval = 5           # browser polling interval, seconds
        stale_after = 120           # browser flags the page stale after this long
        # reports = SeasonsReport   # explicit list; default is every enabled
        #                           # report whose search_list_extensions
        #                           # includes user.livetag.LiveTags, or
        #                           # SeasonsReport if there are none
"""

import json
import logging
import math
import os
import threading
import time
from html import escape

import weewx
import weewx.reportengine
import weewx.tags
import weewx.units
from weeutil.weeutil import option_as_list, to_bool, to_float
from weewx.cheetahgenerator import SearchList
from weewx.engine import StdService
from weewx.units import ValueHelper, ValueTuple

VERSION = '1.0.0'
SLE_NAME = 'user.livetag.LiveTags'
DEFAULT_REPORT = 'SeasonsReport'

log = logging.getLogger(__name__)

DEFAULTS = {
    'enable': True,
    'filename': 'live.json',
    'min_interval': 2.5,
    'stale_age': 300.0,
    'transient': ['rain', 'hail', 'lightning_strike_count'],
    'include': [],
    'exclude': [],
    'poll_interval': 5.0,
    'stale_after': 120.0,
    'reports': [],
}


# ============================================================================
#                         Configuration helpers
# ============================================================================

def _as_list(val, default):
    if val is None:
        return list(default)
    return [v.strip() for v in option_as_list(val) if v and v.strip()]


def live_settings(config_dict):
    """Return the [Live] settings, typed and with defaults filled in."""
    cfg = config_dict.get('Live', {}) if config_dict else {}
    return {
        'enable': to_bool(cfg.get('enable', DEFAULTS['enable'])),
        'filename': cfg.get('filename', DEFAULTS['filename']),
        'min_interval': max(0.5, to_float(cfg.get('min_interval', DEFAULTS['min_interval']))),
        'stale_age': to_float(cfg.get('stale_age', DEFAULTS['stale_age'])),
        'transient': _as_list(cfg.get('transient'), DEFAULTS['transient']),
        'include': _as_list(cfg.get('include'), DEFAULTS['include']),
        'exclude': _as_list(cfg.get('exclude'), DEFAULTS['exclude']),
        'poll_interval': max(1.0, to_float(cfg.get('poll_interval', DEFAULTS['poll_interval']))),
        'stale_after': to_float(cfg.get('stale_after', DEFAULTS['stale_after'])),
        'reports': _as_list(cfg.get('reports'), DEFAULTS['reports']),
    }


def find_targets(config_dict, settings):
    """Work out which reports want live data, and where their JSON goes.

    Returns a list of dicts with keys: report, skin_dict, html_root, filename, path.
    The SearchList and the service both call this, so they always agree on the
    file name for a report.
    """
    std = config_dict.get('StdReport', {})
    explicit = settings['reports']
    targets = []
    for report in std.sections:
        if report == 'Defaults':
            continue
        if explicit and report not in explicit:
            continue
        try:
            skin_dict = weewx.reportengine.build_skin_dict(config_dict, report)
        except Exception as e:
            log.warning("livetag: cannot build skin dictionary for report '%s': %s", report, e)
            continue
        if not to_bool(skin_dict.get('enable', True)):
            continue
        if not explicit:
            gen = skin_dict.get('CheetahGenerator', {})
            sle = option_as_list(gen.get('search_list_extensions', [])) or []
            if SLE_NAME not in [s.strip() for s in sle]:
                continue
        html_root = os.path.join(config_dict.get('WEEWX_ROOT', ''),
                                 skin_dict.get('HTML_ROOT', 'public_html'))
        targets.append({'report': report,
                        'skin_dict': skin_dict,
                        'html_root': os.path.normpath(html_root)})

    # Nothing configured: default to the stock Seasons report, so live.json
    # lands next to the Seasons pages (normally HTML_ROOT = public_html).
    if not targets and not explicit and DEFAULT_REPORT in std.sections:
        try:
            skin_dict = weewx.reportengine.build_skin_dict(config_dict, DEFAULT_REPORT)
            if to_bool(skin_dict.get('enable', True)):
                html_root = os.path.join(config_dict.get('WEEWX_ROOT', ''),
                                         skin_dict.get('HTML_ROOT', 'public_html'))
                targets.append({'report': DEFAULT_REPORT,
                                'skin_dict': skin_dict,
                                'html_root': os.path.normpath(html_root)})
                log.info("livetag: no report lists %s; defaulting to '%s'",
                         SLE_NAME, DEFAULT_REPORT)
        except Exception as e:
            log.warning("livetag: cannot use default report '%s': %s", DEFAULT_REPORT, e)

    # If two reports share an HTML_ROOT, give each its own file so their units
    # and formats cannot fight each other.
    by_root = {}
    for t in targets:
        by_root.setdefault(t['html_root'], []).append(t)
    base, ext = os.path.splitext(settings['filename'])
    for root, group in by_root.items():
        for t in group:
            if len(group) == 1:
                t['filename'] = settings['filename']
            else:
                t['filename'] = '%s-%s%s' % (base, t['report'], ext or '.json')
            t['path'] = os.path.join(root, t['filename'])
    return targets


# ============================================================================
#                      Shared in-memory "latest values"
# ============================================================================

class _LiveState(object):
    """Thread-safe store of the most recent value of every LOOP observation.

    Written by the engine thread (LiveService), read by the writer thread and
    by the report thread (LiveTags), which all live in the same process.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}          # obs_type -> (value, time_seen)
        self.us_units = None
        self.packets = 0

    def update(self, packet, transient, now):
        with self.lock:
            us = packet.get('usUnits')
            if us is None:
                return
            if self.us_units is not None and us != self.us_units:
                # Unit system changed under us: start afresh.
                self.values.clear()
            self.us_units = us
            for obs in transient:
                self.values.pop(obs, None)
            for obs, val in packet.items():
                if obs == 'usUnits' or val is None:
                    continue
                self.values[obs] = (val, now)
            self.packets += 1

    def snapshot(self, stale_age, now=None):
        """Return a record dict of non-stale values, or None if no data yet."""
        now = time.time() if now is None else now
        with self.lock:
            if self.us_units is None or not self.values:
                return None
            rec = {obs: val for obs, (val, seen) in self.values.items()
                   if stale_age <= 0 or now - seen <= stale_age}
            rec['usUnits'] = self.us_units
            return rec


STATE = _LiveState()


# ============================================================================
#                         Formatting to JSON
# ============================================================================

def _jsonable(val):
    if isinstance(val, float):
        if not math.isfinite(val):
            return None
        return round(val, 6)
    if isinstance(val, (int, str, bool)) or val is None:
        return val
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def format_record(record, formatter, converter, include=None, exclude=None):
    """Convert/format every known observation in a record for the browser."""
    out = {}
    for obs in record:
        if obs == 'usUnits':
            continue
        if include and obs not in include:
            continue
        if exclude and obs in exclude:
            continue
        try:
            vt = weewx.units.as_value_tuple(record, obs)
            if vt[2] is None:
                # Unknown unit group: we cannot convert or format it reliably.
                continue
            vh = ValueHelper(vt, 'current', formatter, converter)
            cvt = vh.value_t
            entry = {
                'raw': _jsonable(cvt[0]),
                'unit': cvt[1],
                'group': cvt[2],
                'formatted': vh.format(),
                'value': vh.format(add_label=False),
                'label': formatter.get_label_string(cvt[1]) if cvt[1] else '',
            }
            if cvt[2] == 'group_direction':
                entry['ordinal'] = vh.ordinal_compass()
            out[obs] = entry
        except Exception as e:
            log.debug("livetag: skipping '%s': %s", obs, e)
    return out


def build_payload(record, target, settings):
    return {
        'version': VERSION,
        'report': target['report'],
        'generated': int(time.time()),
        'dateTime': record.get('dateTime'),
        'observations': format_record(record,
                                      target['formatter'],
                                      target['converter'],
                                      settings['include'],
                                      settings['exclude']),
    }


def write_atomic(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


# ============================================================================
#                              The service
# ============================================================================

class LiveService(StdService):
    """Captures LOOP packets and publishes them as JSON for each live report."""

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.settings = live_settings(config_dict)

        if not self.settings['enable']:
            log.info("livetag: disabled by [Live] enable = false")
            return

        self.targets = find_targets(config_dict, self.settings)
        for t in self.targets:
            t['formatter'] = weewx.units.Formatter.fromSkinDict(t['skin_dict'])
            t['converter'] = weewx.units.Converter.fromSkinDict(t['skin_dict'])
            t['errors'] = 0

        if self.targets:
            for t in self.targets:
                log.info("livetag: version %s, report '%s' -> %s",
                         VERSION, t['report'], t['path'])
        else:
            log.warning("livetag: no report uses %s and there is no enabled SeasonsReport (add it to a report's "
                        "search_list_extensions, or set [Live] reports). "
                        "No JSON will be written.", SLE_NAME)

        self._thread = threading.Thread(target=self._run, name='LiveTagWriter')
        self._thread.daemon = True
        self._thread.start()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        # Cheap: merge into memory and poke the writer thread. Never block here.
        STATE.update(event.packet, self.settings['transient'], time.time())
        self._wake.set()

    def _run(self):
        last = 0.0
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                break
            delay = self.settings['min_interval'] - (time.time() - last)
            if delay > 0 and self._stop.wait(delay):
                break
            self._wake.clear()
            last = time.time()
            self._write_all()

    def _write_all(self):
        record = STATE.snapshot(self.settings['stale_age'])
        if not record:
            return
        for t in self.targets:
            try:
                write_atomic(t['path'], build_payload(record, t, self.settings))
                if t['errors']:
                    log.info("livetag: writing %s again", t['path'])
                t['errors'] = 0
            except Exception as e:
                # Log the first failure and then every 100th, to avoid flooding.
                if t['errors'] % 100 == 0:
                    log.error("livetag: cannot write %s: %s", t['path'], e)
                t['errors'] += 1

    def shutDown(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(5.0)
            self._thread = None


# ============================================================================
#                          The search list ($live)
# ============================================================================

_LIVE_JS = r"""(function (cfg) {
  if (window.weewxLive) { return; }
  var api = window.weewxLive = { config: cfg, data: null, refresh: poll };
  var lastDateTime = null, lastChange = Date.now(), timer = null, first = true;

  function apply(data) {
    var obs = data.observations || {};
    var els = document.querySelectorAll('[data-live]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], o = obs[el.getAttribute('data-live')];
      if (!o) { continue; }
      var v = o[el.getAttribute('data-live-field') || 'formatted'];
      if (v === undefined || v === null) { continue; }
      v = String(v);
      if (el.getAttribute('data-live-text') !== v) {
        el.setAttribute('data-live-text', v);
        el.innerHTML = v;
        if (!first) { flash(el); }
      }
    }
    first = false;
  }

  function flash(el) {
    el.classList.remove('weewx-live-changed');
    void el.offsetWidth;
    el.classList.add('weewx-live-changed');
    setTimeout(function () { el.classList.remove('weewx-live-changed'); }, 1500);
  }

  function checkStale() {
    var stale = cfg.staleAfter > 0 && (Date.now() - lastChange) > cfg.staleAfter;
    document.documentElement.classList.toggle('weewx-live-stale', stale);
  }

  function poll() {
    var url = cfg.url + (cfg.url.indexOf('?') < 0 ? '?' : '&') + '_=' + Date.now();
    fetch(url, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(function (data) {
        api.data = data;
        if (data.dateTime !== lastDateTime) {
          lastDateTime = data.dateTime;
          lastChange = Date.now();
          apply(data);
          document.dispatchEvent(new CustomEvent('weewx-live', { detail: data }));
        }
        checkStale();
      })
      .catch(function () { checkStale(); });
  }

  function start() { if (!timer) { poll(); timer = setInterval(poll, cfg.interval); } }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); } else { start(); }
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})(__CONFIG__);"""


class LiveValue(ValueHelper):
    """A ValueHelper that renders as a self-updating <span>."""

    def __init__(self, obs_type, value_t, formatter, converter):
        super().__init__(value_t, 'current', formatter, converter)
        self.obs_type = obs_type

    def _span(self, field, text):
        return ('<span class="weewx-live" data-live="%s" data-live-field="%s">%s</span>'
                % (escape(self.obs_type, quote=True), field, text))

    def _compass_text(self):
        try:
            return ValueHelper.ordinal_compass(self)
        except Exception:
            return self.formatter.ordinate_names[-1]

    def __str__(self):
        return self._span('formatted', self.format())

    def ordinal_compass(self):
        """Live span holding the ordinal compass direction. Overridden so that
        skins written for $current (e.g. Seasons) go live unchanged."""
        return self._span('ordinal', self._compass_text())

    @property
    def value(self):
        """Live span holding the value without a unit label."""
        return self._span('value', self.format(add_label=False))

    @property
    def compass(self):
        """Live span holding the ordinal compass direction (e.g. NNW)."""
        return self._span('ordinal', self._compass_text())

    @property
    def label(self):
        """Static unit label, e.g. '°C'."""
        unit = self.value_t[1]
        return self.formatter.get_label_string(unit) if unit else ''

    @property
    def static(self):
        """A plain, non-live ValueHelper for the same value."""
        return ValueHelper(self.value_t, 'current', self.formatter)


class LiveBinder(object):
    """The object bound to $live.

    It can also stand in for $current in a template:

        #set global $current = $live

    Used bare ($current.outTemp) it gives live values. Called with arguments
    ($current(timestamp=..., max_delta=..., data_binding=...)) it hands over to
    WeeWX's own $current, so existing templates keep working.
    """

    _RESERVED = ('available', 'source', 'url', 'script')

    def __init__(self, loop_record, archive_record_fn, formatter, converter,
                 filename, settings, record_binder):
        self._loop = loop_record
        self._archive_fn = archive_record_fn
        self._archive = None
        self._archive_loaded = False
        self._formatter = formatter
        self._converter = converter
        self._filename = filename
        self._settings = settings
        self._record_binder = record_binder

    def __call__(self, timestamp=None, max_delta=None, data_binding=None):
        if timestamp is None and max_delta is None and data_binding is None:
            return self
        return self._record_binder.current(timestamp=timestamp, max_delta=max_delta,
                                           data_binding=data_binding)

    def _archive_record(self):
        if not self._archive_loaded:
            self._archive_loaded = True
            try:
                self._archive = self._archive_fn()
            except Exception as e:
                log.debug("livetag: no archive record for fallback: %s", e)
        return self._archive

    @property
    def available(self):
        return self._loop is not None

    @property
    def source(self):
        if self._loop is not None:
            return 'loop'
        return 'archive' if self._archive_record() else 'none'

    @property
    def url(self):
        return self._filename

    def script(self, interval=None, url=None, stale_after=None):
        cfg = {
            'url': url or self._filename,
            'interval': int(1000 * float(interval or self._settings['poll_interval'])),
            'staleAfter': int(1000 * float(self._settings['stale_after']
                                           if stale_after is None else stale_after)),
        }
        js = _LIVE_JS.replace('__CONFIG__', json.dumps(cfg).replace('</', '<\\/'))
        return '<script>\n%s\n</script>' % js

    def __getitem__(self, obs_type):
        # Cheetah's compiled NameMapper treats anything with __getitem__ as a
        # mapping and looks up $live.xxx as live['xxx'] first, so the real
        # attributes must be routed back here.
        if obs_type in LiveBinder._RESERVED:
            return getattr(self, obs_type)
        return self._make(obs_type)

    def __getattr__(self, obs_type):
        # Don't let Cheetah or copy/pickle machinery probe us into fake obs types.
        if obs_type.startswith('_') or obs_type in ('has_key', 'keys', 'items'):
            raise AttributeError(obs_type)
        return self._make(obs_type)

    def _make(self, obs_type):
        # Prefer the LOOP value; otherwise use the archive record, which also
        # covers archive-only types (e.g. ET) and transient ones such as
        # 'rain', where the archive interval total is what $current means.
        for rec in (self._loop, self._archive_record()):
            if rec and obs_type in rec and 'usUnits' in rec:
                vt = weewx.units.as_value_tuple(rec, obs_type)
                return LiveValue(obs_type, vt, self._formatter, self._converter)
        rec = self._loop or self._archive_record()
        us = rec['usUnits'] if rec else weewx.US
        unit, group = weewx.units.getStandardUnitType(us, obs_type)
        if group is None:
            # Probably a typo: render the usual WeeWX "?'xyz'?" marker.
            return ValueHelper(weewx.units.UnknownObsType(obs_type),
                               formatter=self._formatter)
        return LiveValue(obs_type, ValueTuple(None, unit, group),
                         self._formatter, self._converter)


class LiveTags(SearchList):
    """Search list extension providing $live."""

    def __init__(self, generator):
        super().__init__(generator)
        self.settings = live_settings(generator.config_dict)
        report = generator.skin_dict.get('REPORT_NAME')
        self.filename = self.settings['filename']
        try:
            for t in find_targets(generator.config_dict, self.settings):
                if t['report'] == report:
                    self.filename = t['filename']
                    break
        except Exception as e:
            log.debug("livetag: could not resolve JSON filename for '%s': %s", report, e)

    def get_extension_list(self, timespan, db_lookup):
        gen = self.generator
        gen_record = getattr(gen, 'record', None)

        def archive_record():
            # The record that triggered this report run, else the newest in the DB.
            if gen_record:
                return gen_record
            return db_lookup().getRecord(timespan.stop)

        record_binder = weewx.tags.RecordBinder(db_lookup, timespan.stop,
                                                gen.formatter, gen.converter,
                                                record=gen_record)
        return [{'live': LiveBinder(STATE.snapshot(self.settings['stale_age']),
                                    archive_record,
                                    gen.formatter,
                                    gen.converter,
                                    self.filename,
                                    self.settings,
                                    record_binder)}]
