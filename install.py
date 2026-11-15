# Installer for the livetag extension, for use with:
#     weectl extension install weewx-livetag-<version>.zip
# Manual installation is described in README.md.

import os
import sys

import weeutil.weeutil
from weecfg.extension import ExtensionInstaller

# Folder this install.py lives in (the unpacked package), so the Seasons
# patcher can be imported from bin/user without being installed first.
HERE = os.path.dirname(os.path.abspath(__file__))

SLE_NAME = 'user.livetag.LiveTags'
DEFAULT_REPORT = 'SeasonsReport'


def loader():
    return LiveTagInstaller()


class LiveTagInstaller(ExtensionInstaller):
    def __init__(self):
        super().__init__(
            version='1.0.0',
            name='livetag',
            description='Adds a $live tag with values from LOOP packets that '
                        'update in the browser between report runs.',
            author='',
            report_services='user.livetag.LiveService',
            config={
                'Live': {
                    'enable': 'true',
                    'filename': 'live.json',
                    'min_interval': '2.5',
                    'poll_interval': '5',
                },
                'StdReport': {
                    'LiveDemo': {
                        'skin': 'LiveDemo',
                        'HTML_ROOT': 'public_html/live',
                        'enable': 'false',
                    },
                },
            },
            files=[
                ('bin/user', ['bin/user/livetag.py', 'bin/user/livetag_seasons.py']),
                ('skins/LiveDemo', ['skins/LiveDemo/skin.conf',
                                    'skins/LiveDemo/livedemo.html.tmpl']),
            ],
        )

    seasons_choice = None   # None = ask; True/False from --seasons/--no-seasons

    def process_args(self, args):
        # weectl extension install weewx-livetag-1.0.0.zip --seasons | --no-seasons
        for a in args or []:
            if a == '--seasons':
                self.seasons_choice = True
            elif a == '--no-seasons':
                self.seasons_choice = False

    def configure(self, engine):
        changed = self._add_search_list(engine)
        self._offer_seasons(engine)
        return changed

    def _offer_seasons(self, engine):
        """Ask whether to apply the live Seasons template changes."""
        out = engine.printer.out
        std = engine.config_dict.get('StdReport', {})
        if DEFAULT_REPORT not in std.sections:
            out("Seasons changes not offered: there is no [[%s]] in [StdReport]."
                % DEFAULT_REPORT)
            return
        skin = std[DEFAULT_REPORT].get('skin', 'Seasons')
        skin_dir = os.path.join(engine.root_dict.get('SKIN_DIR', ''), skin)

        sys.path.insert(0, os.path.join(HERE, 'bin', 'user'))
        try:
            import livetag_seasons
        finally:
            sys.path.pop(0)

        try:
            skin_dir = livetag_seasons.find_skin_dir(skin_dir)
        except livetag_seasons.SeasonsError as e:
            out("Seasons changes not applied: %s" % e)
            return

        choice = self.seasons_choice
        if choice is None:
            if not sys.stdin.isatty():
                out("Seasons changes not applied (no terminal to ask). To apply them "
                    "later, run: python3 %s apply %s"
                    % (os.path.join(engine.root_dict['USER_DIR'], 'livetag_seasons.py'),
                       skin_dir))
                return
            print()
            print("The Seasons skin can be made live:")
            print("  - title bar: live date and time, 'updated Ns ago', green status dot")
            print("  - Current Conditions: updates every few seconds")
            print("The originals are kept as *.pre-livetag, and the change can be undone")
            print("with 'livetag_seasons.py revert'. Skin: %s" % skin_dir)
            choice = weeutil.weeutil.y_or_n("Apply the live Seasons changes (y/n)? ") == 'y'

        if not choice:
            out("Seasons changes not applied. To apply them later, run: python3 %s apply %s"
                % (os.path.join(engine.root_dict['USER_DIR'], 'livetag_seasons.py'), skin_dir))
            return
        out("Applying live Seasons changes%s:" % (' (dry run)' if engine.dry_run else ''))
        try:
            livetag_seasons.apply(skin_dir, dry_run=engine.dry_run, out=out)
        except (livetag_seasons.SeasonsError, OSError) as e:
            out("Seasons changes not applied: %s" % e)

    def _add_search_list(self, engine):
        """Make $live available to Seasons, so live.json is written next to the
        Seasons pages. Appends to any existing search_list_extensions rather
        than replacing it (a plain config merge would not do that)."""
        std = engine.config_dict.get('StdReport')
        if std is None or DEFAULT_REPORT not in std.sections:
            engine.printer.out("No [[%s]] report found; add %s to the report you "
                               "want live by hand." % (DEFAULT_REPORT, SLE_NAME), level=2)
            return False
        report = std[DEFAULT_REPORT]
        if 'CheetahGenerator' not in report:
            report['CheetahGenerator'] = {}
        gen = report['CheetahGenerator']
        current = gen.get('search_list_extensions', [])
        if isinstance(current, str):
            current = [x.strip() for x in current.split(',')]
        current = [x for x in current if x]
        if SLE_NAME in current:
            return False
        gen['search_list_extensions'] = current + [SLE_NAME]
        engine.printer.out("Added %s to [[%s]]" % (SLE_NAME, DEFAULT_REPORT), level=2)
        return True
