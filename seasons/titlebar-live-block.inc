## livetag: live title bar block for the Seasons skin.
## Use this if your titlebar.inc is customised: replace the line
##     <p class="lastupdate">$current.dateTime</p>
## with everything below. Needs user.livetag.LiveTags in the report's
## search_list_extensions; without it, the #else branch shows the stock line.
#if $hasVar("live")
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
    <p class="lastupdate">$current.dateTime</p>
#end if
