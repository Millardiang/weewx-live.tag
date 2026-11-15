#!/usr/bin/env python3
#
#    Distributed under the terms of the GNU General Public License (GPLv3)
#
"""Apply or revert the livetag changes to the stock WeeWX Seasons skin.

The changes:

  titlebar.inc      The "last update" line becomes a clock that ticks every
                    second (kept in step with the station's clock by each
                    update), an "updated 3s ago" counter, and a status dot
                    (green = live, amber = delayed, red = no data). It also
                    loads the live-update script, so every Seasons page gets it.
  index.html.tmpl   '#set global $current = $getVar("live", $current)' near
                    the top, so the Current Conditions panel goes live.
  skin.conf         SKIN_NAME / SKIN_VERSION become "Seasons Live" / the
                    extension version, shown in "About this station".

Both edits fall back to the stock behaviour if $live is not available, so
the pages still render if the extension is removed. Each edit is wrapped in
'## livetag:begin' / '## livetag:end' markers (Cheetah comments, which never
appear in the output), and the first time a file is changed a copy is kept as
<file>.pre-livetag.

Usage:
    python3 livetag_seasons.py apply  [SEASONS_SKIN_DIR]
    python3 livetag_seasons.py revert [SEASONS_SKIN_DIR]
    python3 livetag_seasons.py status [SEASONS_SKIN_DIR]

SEASONS_SKIN_DIR defaults to ~/weewx-data/skins/Seasons (pip install), then
/etc/weewx/skins/Seasons (deb/rpm), then /home/weewx/skins/Seasons (v4).

This file has no WeeWX dependencies. The extension installer uses it too.
"""

import os
import re
import shutil
import sys

VERSION = '1.0.0'          # keep in step with livetag.py
SKIN_NAME = 'Seasons Live'

BEGIN = '## livetag:begin (added by weewx-livetag; "livetag_seasons.py revert" undoes it)'
END = '## livetag:end'
ORIGINAL = '## livetag:original:'
BACKUP_SUFFIX = '.pre-livetag'

DEFAULT_DIRS = [
    os.path.expanduser('~/weewx-data/skins/Seasons'),
    '/etc/weewx/skins/Seasons',
    '/home/weewx/skins/Seasons',
]

INDEX_BLOCK = '#set global $current = $getVar("live", $current)'

TITLEBAR_LINE_RE = re.compile(r'^([ \t]*)<p class="lastupdate">\$current\.dateTime</p>[ \t]*$',
                              re.MULTILINE)

TITLEBAR_BLOCK = r'''#if $hasVar("live")
    <style>
      #title .lastupdate { line-height: 1.5; display: flex; align-items: center; gap: .45em; flex-wrap: wrap; }
      .live-dot { width: .7em; height: .7em; border-radius: 50%; background: #9e9e9e; flex: none; }
      .live-dot.ok    { background: #2e7d32; }
      .live-dot.slow  { background: #f9a825; }
      .live-dot.stale { background: #c62828; }
      .live-dot.pulse { animation: live-pulse 1.2s ease-out; }
      @keyframes live-pulse { from { box-shadow: 0 0 0 0 rgba(46,125,50,.6); } to { box-shadow: 0 0 0 .6em rgba(46,125,50,0); } }
      .live-ago { opacity: .75; }
    </style>
    <p class="lastupdate">
      <span id="live-dot" class="live-dot" title="Live data"></span>
      <span id="live-time">$live.dateTime.format("%A, %-d %B %Y, %H:%M:%S")</span>
      <span id="live-ago" class="live-ago"></span>
    </p>
    $live.script
    <script>
      (function () {
        var SLOW = 30, STALE = 120;   // seconds: amber after SLOW, red after STALE
        var dot = document.getElementById('live-dot');
        var timeEl = document.getElementById('live-time');
        var agoEl = document.getElementById('live-ago');
        var dateFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
        var timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        var last = null;          // browser time (ms) when the last update arrived
        var stationTs = null;     // station time (s) of the last update
        var shown = null;

        // The clock runs from the station's own time, advanced by the time
        // elapsed in the browser since the last update, so a wrong clock on
        // the viewer's computer doesn't matter. Each update re-syncs it.
        function showClock() {
          if (stationTs === null) { return; }
          var d = new Date(stationTs * 1000 + (Date.now() - last));
          var text = dateFmt.format(d) + ', ' + timeFmt.format(d);
          if (text !== shown) { timeEl.textContent = shown = text; }
        }

        document.addEventListener('weewx-live', function (e) {
          if (e.detail.dateTime) {
            last = Date.now();
            stationTs = e.detail.dateTime;
            showClock();
          }
          dot.classList.remove('pulse'); void dot.offsetWidth; dot.classList.add('pulse');
          status();
        });

        function status() {
          if (last === null) { agoEl.textContent = ''; return; }
          var s = Math.round((Date.now() - last) / 1000);
          agoEl.textContent = 'updated ' + (s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's') + ' ago';
          dot.className = 'live-dot ' + (s > STALE ? 'stale' : s > SLOW ? 'slow' : 'ok') +
                          (dot.classList.contains('pulse') ? ' pulse' : '');
          dot.title = s > STALE ? 'No live data' : s > SLOW ? 'Live data delayed' : 'Live';
        }
        status();
        setInterval(showClock, 250);   // checked 4x a second so the seconds tick on time
        setInterval(status, 1000);
      })();
    </script>
#else
__ORIGINAL__
#end if'''

MARKED_RE = re.compile(r'^[ \t]*## livetag:begin.*?^[ \t]*## livetag:end[ \t]*\n?',
                       re.MULTILINE | re.DOTALL)


class SeasonsError(Exception):
    pass


def find_skin_dir(path=None):
    candidates = [path] if path else DEFAULT_DIRS
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, 'titlebar.inc')):
            return os.path.abspath(d)
    raise SeasonsError("Cannot find the Seasons skin (looked in: %s)" % ', '.join(candidates))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _write(path, text, dry_run):
    if dry_run:
        return
    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    tmp = path + '.livetag-tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _wrap(body, original_line=None):
    lines = [BEGIN]
    if original_line is not None:
        lines.append(ORIGINAL + original_line)
    lines.append(body)
    lines.append(END)
    return '\n'.join(lines) + '\n'


def is_applied(text):
    return BEGIN.split(' (')[0] in text


# ---------------------------------------------------------------- apply ----

def _apply_titlebar(text):
    if is_applied(text):
        return None, 'already applied'
    m = TITLEBAR_LINE_RE.search(text)
    if not m:
        raise SeasonsError('titlebar.inc: cannot find the line '
                           '<p class="lastupdate">$current.dateTime</p> '
                           '(customised skin?). Edit it by hand; see README.')
    original = m.group(0)
    block = _wrap(TITLEBAR_BLOCK.replace('__ORIGINAL__', original), original)
    end = m.end()
    if text[end:end + 1] == '\n':
        end += 1
    return text[:m.start()] + block + text[end:], 'live date/time, counter and status dot added'


def _apply_index(text):
    if is_applied(text):
        return None, 'already applied'
    lines = text.splitlines(True)
    # Insert after the last #errorCatcher / #encoding line in the header;
    # failing that, after the leading '##' comment lines.
    header = [i for i, line in enumerate(lines[:20])
              if line.lstrip().startswith(('#errorCatcher', '#encoding'))]
    if header:
        pos = header[-1] + 1
    else:
        pos = 0
        while pos < len(lines) and lines[pos].lstrip().startswith('##'):
            pos += 1
    lines.insert(pos, _wrap(INDEX_BLOCK))
    return ''.join(lines), 'Current Conditions made live'


SKIN_LINE_RE = re.compile(r'^(SKIN_NAME|SKIN_VERSION)[ \t]*=.*$', re.MULTILINE)


def _apply_skinconf(text):
    if is_applied(text):
        return None, 'already applied'
    values = {'SKIN_NAME': SKIN_NAME, 'SKIN_VERSION': VERSION}
    found = []

    def repl(m):
        found.append(m.group(1))
        return _wrap('%s = %s' % (m.group(1), values[m.group(1)]), m.group(0)).rstrip('\n')

    new = SKIN_LINE_RE.sub(repl, text)
    if not found:
        raise SeasonsError('skin.conf: no SKIN_NAME / SKIN_VERSION lines found')
    return new, 'skin shown as "%s %s"' % (SKIN_NAME, VERSION)


# --------------------------------------------------------------- revert ----

def _revert(text):
    if not is_applied(text):
        return None

    def restore(m):
        block = m.group(0)
        for line in block.splitlines():
            if line.startswith(ORIGINAL):
                return line[len(ORIGINAL):] + '\n'
        return ''

    return MARKED_RE.sub(restore, text)


# --------------------------------------------------------------- legacy ----
# Hand-made edits from before the markers existed: an unmarked
#   #if $hasVar("live") ... #else <stock lastupdate line> #end if
# block in titlebar.inc, and in index.html.tmpl an unmarked '#set global
# $current = ...live...' line and an '#if $hasVar("live") $live.script #end if'.
# Left in place, the old title bar block hides the new one inside its #else.

LEGACY_TITLEBAR_RE = re.compile(
    r'^[ \t]*#if \$hasVar\("live"\)[ \t]*\n'
    r'(?:(?!^[ \t]*## livetag:begin).)*?'
    r'^[ \t]*#else[ \t]*\n'
    r'([ \t]*<p class="lastupdate">\$current\.dateTime</p>)[ \t]*\n'
    r'[ \t]*#end if[ \t]*\n?',
    re.MULTILINE | re.DOTALL)

LEGACY_INDEX_RES = [
    re.compile(r'^[ \t]*#set global \$current = \$(?:live|getVar\("live", \$current\))[ \t]*\n',
               re.MULTILINE),
    re.compile(r'^[ \t]*#if \$hasVar\("live"\)[ \t]*\n[ \t]*\$live\.script[ \t]*\n'
               r'[ \t]*#end if[ \t]*\n', re.MULTILINE),
    re.compile(r'^[ \t]*\$live\.script[ \t]*\n', re.MULTILINE),
]


def _strip_outside_markers(text, func):
    """Apply func only to the parts of text outside livetag markers."""
    out, pos = [], 0
    for m in MARKED_RE.finditer(text):
        out.append(func(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(func(text[pos:]))
    return ''.join(out)


def _clean_legacy(name, text):
    """Return (text, removed?) with old unmarked hand edits removed."""
    if name == 'titlebar.inc':
        def f(t):
            prev = None
            while prev != t:   # a block may have been nested in another one
                prev, t = t, LEGACY_TITLEBAR_RE.sub(lambda m: m.group(1) + '\n', t)
            return t
    elif name == 'index.html.tmpl':
        def f(t):
            for r in LEGACY_INDEX_RES:
                t = r.sub('', t)
            return t
    else:
        return text, False
    new = _strip_outside_markers(text, f)
    return new, new != text


# ------------------------------------------------------------------ API ----

FILES = (('titlebar.inc', _apply_titlebar), ('index.html.tmpl', _apply_index),
         ('skin.conf', _apply_skinconf))


def apply(skin_dir=None, dry_run=False, out=print):
    skin_dir = find_skin_dir(skin_dir)
    changed = False
    for name, func in FILES:
        path = os.path.join(skin_dir, name)
        if not os.path.isfile(path):
            out("  %s: not found, skipped" % name)
            continue
        text = _read(path)
        base = _revert(text)            # strip an earlier livetag block, if any
        base = text if base is None else base
        base, had_legacy = _clean_legacy(name, base)
        new, msg = func(base)
        if new == text:
            new, msg = None, 'already up to date'
        elif had_legacy:
            msg = 'removed an older hand-made live block; ' + msg
        elif base != text:
            msg = 'updated to this version'
        if new is not None:
            _write(path, new, dry_run)
            changed = True
        out("  %s: %s%s" % (name, msg, ' (dry run)' if dry_run and new else ''))
    return changed


def revert(skin_dir=None, dry_run=False, out=print):
    skin_dir = find_skin_dir(skin_dir)
    changed = False
    for name, _ in FILES:
        path = os.path.join(skin_dir, name)
        if not os.path.isfile(path):
            continue
        new = _revert(_read(path))
        if new is None:
            out("  %s: no livetag changes" % name)
            continue
        if not dry_run:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new)
        changed = True
        out("  %s: reverted%s" % (name, ' (dry run)' if dry_run else ''))
    return changed


def status(skin_dir=None, out=print):
    skin_dir = find_skin_dir(skin_dir)
    for name, _ in FILES:
        path = os.path.join(skin_dir, name)
        if os.path.isfile(path):
            out("  %s: %s" % (name, 'applied' if is_applied(_read(path)) else 'stock'))


def main(argv):
    if len(argv) < 2 or argv[1] not in ('apply', 'revert', 'status'):
        print(__doc__)
        return 2
    try:
        skin_dir = find_skin_dir(argv[2] if len(argv) > 2 else None)
        print("Seasons skin: %s" % skin_dir)
        {'apply': apply, 'revert': revert, 'status': status}[argv[1]](skin_dir)
    except (SeasonsError, OSError) as e:
        print("Error: %s" % e)
        return 1
    if argv[1] != 'status':
        print("Done. Changes show up at the next report run.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
