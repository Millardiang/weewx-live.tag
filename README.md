# weewx-livetag

Adds a `$live` tag to WeeWX templates. It works like `$current`, but it
reads the latest LOOP packet instead of the last archive record. The value
also keeps updating in the browser between report runs.

```
<p>Temperature: $live.outTemp</p>          <!-- updates every few seconds -->
<p>Wind: $live.windSpeed from $live.windDir.compass</p>
$live.script                                <!-- once per page -->
```

Tested on WeeWX 5.5.2. It needs `weewx.reportengine.build_skin_dict`, so it
should also run on WeeWX 4.6 or later, but that has not been tested.

## How it works

1. **`LiveService`** binds to `NEW_LOOP_PACKET`. It merges each packet into
   an in-memory "latest values" record. Partial packets are merged, transient
   observations such as `rain` are not carried forward, and values not
   refreshed within `stale_age` are dropped.
2. A background thread writes a small `live.json` into the `HTML_ROOT` of
   every report that uses `$live`, at most every `min_interval` seconds. Each
   file uses that report's own units, formats and labels, so it matches the
   rest of the page. The loop thread never touches the disk.
3. **`LiveTags`** is a search list extension that provides `$live`. When the
   report renders, each tag outputs a `<span>` that already holds the current
   value, so the page is correct even without JavaScript.
4. **`$live.script`** writes a small inline script. It polls `live.json` and
   updates every live span on the page. Polling pauses while the tab is
   hidden, and it fires a `weewx-live` event you can hook.

## Manual installation

The paths depend on how WeeWX was installed:

| Install type     | `bin/user` directory          | skins directory           | config                       |
|------------------|-------------------------------|---------------------------|------------------------------|
| pip (WeeWX 5)    | `~/weewx-data/bin/user/`      | `~/weewx-data/skins/`     | `~/weewx-data/weewx.conf`    |
| deb / rpm        | `/etc/weewx/bin/user/`        | `/etc/weewx/skins/`       | `/etc/weewx/weewx.conf`      |
| setup.py (v4)    | `/home/weewx/bin/user/`       | `/home/weewx/skins/`      | `/home/weewx/weewx.conf`     |

### 1. Copy the files

```
cp bin/user/livetag.py          <bin/user directory>/
cp bin/user/livetag_seasons.py  <bin/user directory>/   # Seasons patcher (step 6)
cp -r skins/LiveDemo            <skins directory>/      # optional demo skin
```

On deb/rpm installs, make sure the files are readable by the `weewx` user.

### 2. Register the service

In `weewx.conf`, add `user.livetag.LiveService` to the **end** of
`report_services`. Putting it after `StdWXCalculate` means that derived
values such as `dewpoint`, `windchill` and `appTemp` are already in the
packet.

```
[Engine]
    [[Services]]
        ...
        report_services = weewx.engine.StdPrint, weewx.engine.StdReport, user.livetag.LiveService
```

### 3. Add the `[Live]` section (optional; these are the defaults)

```
[Live]
    enable = true
    filename = live.json        # written to each live report's HTML_ROOT
    min_interval = 2.5          # minimum seconds between JSON writes
    stale_age = 300             # drop values not seen for this long (0 = never)
    transient = rain, hail, lightning_strike_count
    include = ""                # only publish these observations (empty = all)
    exclude = ""                # never publish these observations
    poll_interval = 5           # browser polling interval, seconds
    stale_after = 120           # browser flags the page stale after this long
    # reports = SeasonsReport   # explicit list of reports (see below)
```

**Where `live.json` goes.** By default it's written to the `HTML_ROOT` of
the stock **Seasons** report, i.e. next to the Seasons pages
(`~/weewx-data/public_html/live.json` on a pip install).

In more detail: JSON is written for every enabled report whose
`search_list_extensions` includes `user.livetag.LiveTags`. If no report
lists it, the service falls back to `SeasonsReport`. Set `reports` to choose
the reports explicitly instead. If two live reports share the same
`HTML_ROOT`, the files are named `live-<ReportName>.json` so their units
cannot clash. `$live.script` picks the right file automatically.

### 4. Enable `$live` in a report

Seasons needs this so its templates can use `$live` (the JSON is written for
Seasons either way):

```
[StdReport]
    [[SeasonsReport]]
        ...
        [[[CheetahGenerator]]]
            search_list_extensions = user.livetag.LiveTags
```

**Demo skin (optional).** It produces a single page, `livedemo.html`.
Because it isn't called `index.html`, it can't overwrite the Seasons front
page. Add this under `[StdReport]`:

```
    [[LiveDemo]]
        skin = LiveDemo
        HTML_ROOT = public_html/live
        enable = true
```

The page is then at `…/live/livedemo.html`. Keeping its own `HTML_ROOT`
also gives it its own `live.json`, so it doesn't change the Seasons file's
name (see "Where `live.json` goes").

**Other skins.** Add the same `search_list_extensions` line to that report,
either in its `skin.conf` or in `weewx.conf`. If the skin already has
`search_list_extensions`, add this one to the list (comma-separated) rather
than replacing it. Then use `$live` in its templates (see the tag reference).

### 5. Restart WeeWX

```
sudo systemctl restart weewx
```

The log should show a line like
`livetag: version 1.0.0, report 'SeasonsReport' -> /…/public_html/live.json`.
Within a few seconds, `live.json` should appear and start updating. Live
values reach the pages after the next report run.

### 6. Make Seasons live (optional)

```
python3 ~/weewx-data/bin/user/livetag_seasons.py apply
```

This makes the changes described in [Live Seasons](#live-seasons) below.
Give the skin folder as an extra argument if it isn't in a standard place,
or if you run the script with `sudo` (then `~` is root's home folder):
`python3 /etc/weewx/bin/user/livetag_seasons.py apply /etc/weewx/skins/Seasons`.
No restart is needed. The changes show up at the next report run.

## Installing with weectl

`install.py` is included, so `weectl extension install` works too:

```
weectl extension install weewx-livetag-1.0.0.zip
```

It does steps 1–5 for Seasons. If Seasons already lists other search list
extensions, it adds `user.livetag.LiveTags` to that list rather than
replacing it. It also adds the LiveDemo report, but with `enable = false`.

It then **asks whether to apply the live Seasons changes** (step 6). To
choose without being asked, add `--seasons` or `--no-seasons`:

```
weectl extension install weewx-livetag-1.0.0.zip -y --seasons
```

If there's no terminal to ask (e.g. a script), it doesn't apply them, and it
prints the command to run later. `--dry-run` shows what would change
without touching anything. Restart WeeWX afterwards.

## Making `$current` live in a template

You can also make an existing skin live without rewriting its templates. Add
this one line near the top of a template, after any `#errorCatcher` or
`#encoding` lines:

```
#set global $current = $live
```

Every `$current.xxx` in that template **and the files it `#include`s** then
renders as a live span. This also covers `$current.xxx.ordinal_compass` and
lookups such as `$getVar('current.' + $x)`. Other templates in the same
report are not touched, so RSS, NOAA and text outputs stay plain.

What changes and what doesn't:

* `$current.outTemp`, `$current.dateTime` and other bare tags: live, from the LOOP data.
* Observations the LOOP data doesn't have (archive-only types such as `ET`,
  or transient ones such as `rain`) fall back to the archive record. This
  matches what `$current` normally means, e.g. the interval rain total.
* `$current(timestamp=..., max_delta=..., data_binding=...)`: called with
  arguments, it hands over to WeeWX's own `$current` unchanged.
* `$latest`, `$day`, `$trend` and other tags are not affected.

## Live Seasons

`livetag_seasons.py` (or the installer, if you answer yes) makes two edits
to the stock Seasons skin:

* **`titlebar.inc`**: the "last update" line becomes a live clock that ticks
  every second (e.g. *Sunday, 15 November 2026, 10:14:03*), plus an
  "updated 3s ago" counter and a status dot. The clock runs from the
  station's own time and re-syncs with every update, so a wrong clock on the
  viewer's computer doesn't affect it. If data stops, the clock keeps running
  while the counter and dot show the data is stale. The dot is green and pulses on each update. It
  turns amber after 30 s without new data, red after 2 minutes, and stays
  grey if JavaScript isn't running. The title bar also loads the
  live-update script, so every Seasons page gets the live title.
* **`index.html.tmpl`**: one line near the top,
  `#set global $current = $getVar("live", $current)`, so the front page's
  Current Conditions panel updates live.

`rss.xml`, the NOAA reports and the other templates are not changed.

```
python3 livetag_seasons.py apply  [SKIN_DIR]   # make the changes
python3 livetag_seasons.py revert [SKIN_DIR]   # put both files back exactly as they were
python3 livetag_seasons.py status [SKIN_DIR]   # show 'applied' or 'stock' for each file
```

Details:

* Running `apply` twice is safe. On a skin patched by an earlier copy of
  the script, `apply` replaces the old block with the current one. Each edit sits between
  `## livetag:begin` / `## livetag:end` markers (Cheetah comments, which
  never appear in the output). The first time a file is changed, a copy of
  the original is kept as `<file>.pre-livetag`.
* The edits fall back to the stock behaviour if `$live` isn't available, so
  Seasons still renders if the extension is removed or misconfigured.
* If `titlebar.inc` has been customised and the stock
  `<p class="lastupdate">$current.dateTime</p>` line isn't there, the script
  skips that file and says so. You can then make the edit by hand with
  `seasons/titlebar-live-block.inc`.
* A WeeWX upgrade may replace the stock Seasons files. If that happens, run
  `apply` again. Copying Seasons to a new skin name avoids it altogether.
* To change the amber/red limits, edit `SLOW` and `STALE` near the top of the
  script block in `titlebar.inc`. The time is shown in the viewer's time
  zone, using en-GB formatting.

### The template code

The package's `seasons/` folder holds the Seasons template code as ordinary
files:

| File                               | What it is                                                          |
|------------------------------------|---------------------------------------------------------------------|
| `seasons/titlebar.inc`             | Complete `titlebar.inc` with the live title bar (from WeeWX 5.5.2)  |
| `seasons/index.html.tmpl`          | Complete `index.html.tmpl` with the `$current` line (WeeWX 5.5.2)   |
| `seasons/titlebar-live-block.inc`  | Just the live title bar block, for pasting into a customised skin   |

These files are produced by `livetag_seasons.py apply`, so they match what
the script writes. If your Seasons skin is the stock 5.5.2 one, you can copy
the two complete files over the ones in `skins/Seasons/` instead of running
the script. `status` and `revert` recognise them either way. For another
WeeWX version, use the script or the block file, so your version's other
template changes are kept.

### By hand

The two edits, if you prefer to do them yourself:

1. In `index.html.tmpl`, under `#errorCatcher Echo`:
   `#set global $current = $getVar("live", $current)`
2. In `titlebar.inc`, replace `<p class="lastupdate">$current.dateTime</p>`
   with the contents of `seasons/titlebar-live-block.inc`.

Use the fail-safe `$getVar("live", $current)` form shown here. With a plain
`#set global $current = $live`, the whole template fails with
`cannot find 'live'` if the report doesn't load `user.livetag.LiveTags`.

Don't use this in a template that puts `$current` inside HTML attributes or
JavaScript strings (e.g. `title="$current.outTemp"`). The span markup would
break them. Use `$current.outTemp.static` there, or leave that template alone.

## Tag reference

| Tag                               | Output                                                  |
|-----------------------------------|---------------------------------------------------------|
| `$live.outTemp`                   | Live span with value and label: `21.3°C`                |
| `$live.outTemp.value`             | Live span without label: `21.3`                         |
| `$live.windDir.compass`           | Live span with ordinal direction: `NNW`                 |
| `$live.dateTime`                  | Live span with the packet time (`current` time format)  |
| `$live.outTemp.label`             | Static unit label: `°C`                                 |
| `$live.outTemp.static`            | Static formatted value, no span                         |
| `$live.outTemp.raw`               | Raw number in the report's units                        |
| `$live.outTemp.format("%.2f")`    | Static, custom format                                   |
| `$live.outTemp.degree_F`          | Static, converted to another unit                       |
| `$live['outTemp']`                | Same as `$live.outTemp`                                 |
| `$live.script`                    | Inline `<script>` that keeps the spans updated          |
| `$live.script(interval=10, url='../live.json')` | Override poll interval or JSON path       |
| `$live.available`                 | `True` if LOOP data was in memory when the page rendered |
| `$live.source`                    | `loop`, `archive` (fallback) or `none`                  |
| `$live.url`                       | This report's JSON file name                            |
| `$live.windDir.ordinal_compass`   | Live span, same as `.compass` (for `$current` skins)     |
| `$live(max_delta=600)`            | Function form: hands over to stock `$current`           |

Any observation in the LOOP packet works, including extension types. An
unknown name renders as WeeWX's usual `?'name'?` marker.

**Templates in subdirectories.** `$live.script` defaults to a relative URL
(`live.json`). For a template that renders into a subfolder of `HTML_ROOT`,
pass the path: `$live.script(url='../live.json')`.

**Offline renders.** When a report runs outside `weewxd`
(e.g. `weectl report run`), there is no LOOP data in memory. The tags then
fall back to the latest archive record, and `$live.source` is `archive`.
Individual observations that are missing from the LOOP data also fall back to
the archive record.

### Writing your own spans

```html
<span data-live="outTemp" data-live-field="value"></span>
```

`data-live-field` can be `formatted` (default), `value`, `raw`, `label`,
`unit` or `ordinal`.

### Hooking updates (gauges, charts)

```js
document.addEventListener('weewx-live', function (e) {
  var t = e.detail.observations.outTemp;   // {raw, value, formatted, label, unit, group}
  myGauge.set(t.raw);
});
// window.weewxLive.data holds the latest payload; window.weewxLive.refresh() polls now.
```

### CSS hooks

* `.weewx-live`: every live span
* `.weewx-live-changed`: added for 1.5 s when a value changes
* `html.weewx-live-stale`: set when no new data has arrived for `stale_after`

## JSON format

```json
{
  "version": "1.0.0",
  "report": "SeasonsReport",
  "generated": 1794658041,
  "dateTime": 1794658041,
  "observations": {
    "outTemp": {"raw": 0.37, "unit": "degree_C", "group": "group_temperature",
                "formatted": "0.4°C", "value": "0.4", "label": "°C"},
    "windDir": {"raw": 360.0, "unit": "degree_compass", "group": "group_direction",
                "formatted": "360°", "value": "360", "label": "°", "ordinal": "N"}
  }
}
```

## Notes and caveats

* **Remote web server.** The JSON is written locally. If your pages are
  served from another host, the stock FTP/rsync upload only runs with the
  reports, so `live.json` would only refresh once per archive interval. You
  have three options. You can serve `HTML_ROOT` from the WeeWX machine. You
  can run a small sync loop, e.g. `inotifywait` + `rsync` on `live.json`. Or
  you can switch the transport to MQTT over websockets (a natural next
  version of this extension).
* **Caching.** The script adds a cache-busting query string, but a proxy or
  CDN may still cache JSON. With nginx, for example:
  `location ~ live.*\.json$ { add_header Cache-Control "no-store"; }`
* **Rain.** LOOP `rain` is the amount in that packet only, so it is
  transient by default and isn't useful live. Use `$live.rainRate` for live
  rain, and `$day.rain.sum` (static) for totals.
* **Archive vs. live.** Values derived only at archive time (e.g. some
  `xtypes`) won't appear in `$live`. They can still be used through
  `$current`.
* **Load.** Each write is a few KB. With `min_interval = 2.5`, that is
  about 1 write every 2.5 s per live report. Raise it on SD-card systems if
  you are worried about wear, or point `HTML_ROOT` at a tmpfs.

## Uninstall

If you applied the Seasons changes, revert them first, before the script is
removed: `python3 <bin/user directory>/livetag_seasons.py revert`. Leaving
them in place is harmless, because Seasons falls back to its stock title and
Current Conditions once `$live` is gone.

With weectl: `weectl extension uninstall livetag`, then remove
`user.livetag.LiveTags` from `[[SeasonsReport]]` if it's still there. By
hand:

Remove `user.livetag.LiveService` from `report_services`, remove
`user.livetag.LiveTags` from any `search_list_extensions`, delete the
`[Live]` and `[[LiveDemo]]` sections, delete `bin/user/livetag.py`,
`bin/user/livetag_seasons.py` and `skins/LiveDemo`, then restart WeeWX.

## License

GPLv3, the same as WeeWX.

## Troubleshooting

**`cannot find 'live'`**: the report isn't loading `user.livetag.LiveTags`.
Check that `search_list_extensions = user.livetag.LiveTags` is in a
`[[[CheetahGenerator]]]` section *inside* that report's `[[...]]` section
(or in the skin's `skin.conf`), then restart WeeWX. At startup the log should
show `livetag: version …, report 'SeasonsReport' -> …/live.json`. If it
says `no report lists user.livetag.LiveTags; defaulting to 'SeasonsReport'`,
the JSON is still being written, but the `search_list_extensions` setting
isn't where WeeWX expects it, so templates can't use `$live`.
