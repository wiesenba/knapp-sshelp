"""Offline regression tests. All SSH calls are mocks; all writes use temp homes."""
from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(ROOT))
import app
from catalog import Catalog, environment, safe_text
from state import Store
from themes import NAMES, compact, fzf_colors, pointer

SAMPLE = """Host *
    ForwardX11 no
## EX00001 - Demo - Dev
Host Demo-Dev
    HostName 192.0.2.10
    User demo
## EX00001 - Demo - Live
Host Demo-Live
    HostName 192.0.2.11
    User demo
## EX00002 - Lab - Sbx
Host Lab-Sbx
    HostName 192.0.2.12
Host !Demo-Live *.example.invalid
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / ".ssh").mkdir()
        self.config = self.home / ".ssh/config"
        self.config.write_text(SAMPLE)
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
            "SSHELP_CONFIG": str(self.config), "NO_COLOR": "1",
        })
        self.env.start()
        self.ctx = app.Context()

    def tearDown(self):
        self.ctx.store.close()
        self.env.stop()
        self.temp.cleanup()

    def prepare_add_host(self):
        self.config.write_text(SAMPLE.split("## EX00002", 1)[0])
        self.ctx.catalog = Catalog(self.config, self.home)


class CatalogTests(Fixture):
    def test_literal_aliases_and_no_patterns(self):
        self.assertEqual(len(self.ctx.catalog.hosts), 3)
        self.assertIsNone(self.ctx.catalog.lookup("*"))
        self.assertIsNone(self.ctx.catalog.lookup("!Demo-Live"))

    def test_project_and_environment(self):
        host = self.ctx.catalog.lookup("demo-dev")
        self.assertEqual((host.project, host.project_name, host.environment), ("EX00001", "Demo", "dev"))

    def test_multiple_aliases(self):
        self.config.write_text("Host Demo-Dev Demo-Live\n HostName 192.0.2.10\n User demo\n")
        catalog = Catalog(self.config, self.home)
        for alias in ("demo-dev", "demo-live"):
            self.assertEqual(catalog.lookup(alias).declared["hostname"], "192.0.2.10")

    def test_filters(self):
        self.assertEqual([h.alias for h in self.ctx.catalog.filtered("live", "EX00001")], ["Demo-Live"])
        self.assertEqual(len(self.ctx.catalog.filtered(project="demo")), 2)
        self.assertEqual([h.alias for h in self.ctx.catalog.filtered(query="demo live")], ["Demo-Live"])

    def test_includes_relative_to_ssh_directory(self):
        inc = self.home / ".ssh/conf.d"
        inc.mkdir()
        included = inc / "extra.conf"
        included.write_text('## EX00002 - Lab - Sbx\nHost Lab-Sbx\n HostName 192.0.2.15\n')
        self.config.write_text('Include "conf.d/*.conf"\n' + SAMPLE.split("## EX00002", 1)[0])
        catalog = Catalog(self.config, self.home)
        self.assertEqual(len(catalog.hosts), 3)
        self.assertEqual(catalog.lookup("Lab-Sbx").source, str(included))
        self.assertEqual(catalog.lookup("Lab-Sbx").declared["hostname"], "192.0.2.15")

    def test_include_cycle_is_bounded(self):
        self.config.write_text("Include config\n" + SAMPLE)
        cat = Catalog(self.config, self.home)
        self.assertEqual(len(cat.hosts), 3)
        self.assertTrue(cat.warnings)

    def test_equal_sign_directives(self):
        self.config.write_text("Host = Demo-Dev\nHostName=192.0.2.1\n")
        self.assertEqual(Catalog(self.config, self.home).lookup("Demo-Dev").declared["hostname"], "192.0.2.1")

    def test_control_characters_are_not_emitted(self):
        self.assertNotIn("\x1b", safe_text("demo\x1b[31m\tname\n"))

    def test_bad_alias_cannot_become_shell_input(self):
        self.config.write_text("Host 'bad;touch' Demo-Dev\n")
        self.assertEqual(list(Catalog(self.config, self.home).hosts), ["demo-dev"])

    def test_environment_suffix_variants(self):
        self.assertEqual(environment("Demo-Live-SV2"), "live")
        self.assertEqual(environment("Demo-Dev2"), "dev")
        self.assertEqual(environment("Lab", "EX00002 - Lab - Sbx"), "sbx")


class StateTests(Fixture):
    def test_favourite_toggle_is_case_insensitive(self):
        self.assertTrue(self.ctx.store.toggle("Demo-Dev"))
        self.assertFalse(self.ctx.store.toggle("demo-dev"))

    def test_recent_and_open_count(self):
        self.ctx.store.opened("Demo-Live")
        self.ctx.store.opened("Demo-Live")
        self.assertEqual(self.ctx.store.metadata()["demo-live"]["opens"], 2)

    def test_state_is_private(self):
        self.assertEqual(stat.S_IMODE(self.ctx.store.path.stat().st_mode), 0o600)

    def test_concurrent_connections(self):
        directory = self.ctx.directory
        def write(_):
            store = Store(directory)
            try:
                store.opened("demo-dev")
            finally:
                store.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(12)))
        self.assertEqual(self.ctx.store.metadata()["demo-dev"]["opens"], 12)

    def test_favourite_filter(self):
        self.ctx.store.toggle("Demo-Live")
        self.assertEqual([h.alias for h in app.selected_hosts(self.ctx, {"view": "favourites"})], ["Demo-Live"])

    def test_recents_are_most_recent_first(self):
        with patch("state.time.time", side_effect=[100.0, 101.0]):
            self.ctx.store.opened("Demo-Dev")
            self.ctx.store.opened("Demo-Live")
        self.assertEqual([h.alias for h in app.selected_hosts(self.ctx, {"view": "recent"})], ["Demo-Live", "Demo-Dev"])

    def test_database_symlink_is_refused(self):
        directory = self.home / "linked"
        directory.mkdir()
        (directory / "state.sqlite3").symlink_to(self.config)
        with self.assertRaises(ValueError):
            Store(directory)


class InterfaceTests(Fixture):
    def test_minimal_is_default_theme(self):
        self.assertEqual(self.ctx.theme, "minimal")

    def test_network_is_off_for_new_state(self):
        self.assertFalse(self.ctx.network)

    def test_network_on_preference_survives_new_context(self):
        p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--network", "on", "--list"], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        other = app.Context()
        try:
            self.assertTrue(other.network)
        finally:
            other.store.close()

    def test_theme_and_network_preferences_survive_new_context(self):
        p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--theme", "midnight", "--no-network", "--list"], capture_output=True)
        self.assertEqual(p.returncode, 0)
        other = app.Context()
        try:
            self.assertEqual(other.theme, "midnight")
            self.assertFalse(other.network)
        finally:
            other.store.close()

    def test_ember_theme_is_available_and_persistent(self):
        p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--theme", "ember", "--list"], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        other = app.Context()
        try:
            self.assertEqual(other.theme, "ember")
            self.assertTrue(compact(other.theme))
            self.assertIn("sshelp / ember", app.rows(other, {}))
        finally:
            other.store.close()

    def test_all_themes_use_default_background_everywhere(self):
        for theme in NAMES:
            for colored in (True, False):
                style = fzf_colors(theme, colored)
                for token in ("bg:-1", "bg+:-1", "preview-bg:-1", "gutter:-1"):
                    self.assertIn(token, style)
                self.assertNotRegex(style, r"(?:^|,)(?:bg|bg\+|preview-bg|gutter):#")

    def test_focus_is_bold_underlined_with_wide_pointer(self):
        for theme in NAMES:
            for colored in (True, False):
                style = fzf_colors(theme, colored)
                current = [token for token in style.split(',') if token.startswith('fg+:')][-1]
                self.assertIn(':bold:underline', current)
        self.assertEqual(len(pointer(True)), 2)
        self.assertEqual(pointer(False), ">>")

    def test_alias_text_does_not_mask_fzf_focus_style(self):
        self.ctx.color = True
        for theme in NAMES:
            self.ctx.theme = theme
            output = app.rows(self.ctx, {})
            self.assertIn(f'{"Demo-Dev":32} ', output)
            self.assertNotRegex(output, r'\x1b\[(?!0m)[0-9;]*mDemo-Dev')

    def test_project_picker_uses_high_visibility_pointer(self):
        self.ctx.theme = 'ember'
        self.ctx.color = True
        with patch.object(app.subprocess, 'run', return_value=subprocess.CompletedProcess([], 130, '')) as run:
            app.choose_project(self.ctx)
        self.assertIn('--pointer=▌▸', run.call_args.args[0])
        self.assertIn('--color=' + fzf_colors('ember', True), run.call_args.args[0])

    def test_network_off_live_preview_never_starts_probe(self):
        self.ctx.network = False
        with patch.object(app, "effective", return_value={"hostname": ["127.0.0.1"], "port": ["22"]}), patch.object(app, "Probe") as probe, contextlib.redirect_stdout(io.StringIO()) as out:
            app.preview(self.ctx, "Demo-Dev", live=True)
        probe.assert_not_called()
        self.assertIn("Network off", out.getvalue())

    def test_details_do_not_probe_even_with_network_enabled(self):
        self.ctx.network = True
        with patch.object(app, "effective", return_value={}), patch.object(app, "Probe") as probe, contextlib.redirect_stdout(io.StringIO()):
            app.preview(self.ctx, "Demo-Dev")
        probe.assert_not_called()

    def test_cancel_during_debounce_sends_no_packets(self):
        self.ctx.network = True
        with patch.object(app, "effective", return_value={"hostname": ["127.0.0.1"], "port": ["22"]}), patch.object(app, "Probe") as probe, patch.object(app.threading.Event, "wait", side_effect=app.PreviewCancelled), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(app.PreviewCancelled):
                app.preview(self.ctx, "Demo-Dev", live=True)
        probe.assert_not_called()

    def test_row_identifiers_are_separate_from_display(self):
        output = app.rows(self.ctx, {})
        self.assertEqual(len(output.splitlines()), 7)
        self.assertEqual(output.splitlines()[4].split("\t")[0], "Demo-Dev")
        self.assertNotIn("\x1b", output)

    def test_paint_preserves_alignment(self):
        self.assertEqual(app.paint("  STAR   ", "cyan", False), "  STAR   ")

    def test_session_environment_cycle_and_reset(self):
        session = self.home / "view.json"
        session.write_text(json.dumps({"env": "all", "view": "all", "project": "Demo"}))
        app.session_action(self.ctx, session, "env")
        self.assertEqual(app.session_read(session)["env"], "live")
        app.session_action(self.ctx, session, "all")
        self.assertEqual(app.session_read(session), {"env": "all", "view": "all", "project": ""})

    def test_session_star(self):
        session = self.home / "view.json"
        session.write_text('{}')
        app.session_action(self.ctx, session, "star", "Demo-Dev")
        self.assertTrue(self.ctx.store.metadata()["demo-dev"]["favourite"])

    def test_preview_does_not_probe_network(self):
        result = subprocess.CompletedProcess([], 0, "hostname 192.0.2.10\nuser demo\nport 22\n", "")
        with patch.object(app.subprocess, "run", return_value=result) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            app.preview(self.ctx, "Demo-Dev")
        self.assertEqual(run.call_args.args[0][:2], ["ssh", "-G"])
        self.assertIn("192.0.2.10", output.getvalue())

    def test_project_switcher_returns_project_id(self):
        result = subprocess.CompletedProcess([], 0, "EX00001\tEX00001 Demo\n", "")
        with patch.object(app.subprocess, "run", return_value=result):
            self.assertEqual(app.choose_project(self.ctx), "EX00001")

    def test_project_switcher_cancel(self):
        result = subprocess.CompletedProcess([], 130, "", "")
        with patch.object(app.subprocess, "run", return_value=result):
            self.assertIsNone(app.choose_project(self.ctx))

    def test_narrow_terminal_hides_preview(self):
        session = self.home / "view.json"
        session.write_text('{}')
        result = subprocess.CompletedProcess([], 0, "demo\nenter\nDemo-Dev\trow\n", "")
        with patch.object(app.shutil, "which", return_value="/usr/bin/fzf"), patch.object(app.shutil, "get_terminal_size", return_value=(80, 24)), patch.object(app.subprocess, "run", return_value=result) as run:
            self.assertEqual(app.choose(self.ctx, session, "demo"), ("demo", "enter", "Demo-Dev"))
        self.assertIn('--preview-window=down,40%,border-top,wrap,hidden', run.call_args.args[0])

    def test_inherited_fzf_key_overrides_are_not_used(self):
        session = self.home / "view.json"
        session.write_text('{}')
        result = subprocess.CompletedProcess([], 130, "", "")
        with patch.dict(os.environ, {"FZF_DEFAULT_OPTS": "--bind enter:abort", "FZF_DEFAULT_OPTS_FILE": "/unused"}), patch.object(app.shutil, "which", return_value="/usr/bin/fzf"), patch.object(app.subprocess, "run", return_value=result) as run:
            app.choose(self.ctx, session, "")
        self.assertEqual(run.call_args.kwargs['env']['FZF_DEFAULT_OPTS'], '')
        self.assertNotIn('FZF_DEFAULT_OPTS_FILE', run.call_args.kwargs['env'])

    def test_plain_zero_padded_selection(self):
        with patch("builtins.input", return_value="02"), patch.object(app, "run_connection", return_value=0) as connect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.plain_menu(self.ctx, {}, ""), 0)
        self.assertEqual(connect.call_args.args[1], "Demo-Live")

    def test_plain_invalid_selection(self):
        with patch("builtins.input", return_value="999999999999"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                app.plain_menu(self.ctx, {}, "")

    def test_plain_cancel(self):
        with patch("builtins.input", return_value="q"), patch.object(app, "run_connection") as connect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.plain_menu(self.ctx, {}, ""), 0)
        connect.assert_not_called()

    def test_connection_passes_direct_ssh_argv_and_inherits_environment(self):
        arguments = ["printf", "a; printf ignored"]
        socket_path = str(self.home / "demo.sock")
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": socket_path}), patch.object(app.subprocess, "call", return_value=255) as call, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.run_connection(self.ctx, "Demo-Live", arguments), 255)
            self.assertEqual(os.environ["SSH_AUTH_SOCK"], socket_path)
        call.assert_called_once_with(["ssh", "-F", str(self.config), "Demo-Live", *arguments])
        self.assertEqual(call.call_args.kwargs, {})

    def test_diagnostic_does_not_add_recents(self):
        with patch.object(app.subprocess, "call", return_value=0) as call, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.run_connection(self.ctx, "Demo-Dev", [], True)
        call.assert_called_once_with([
            "timeout", "--foreground", "--kill-after=2", "25", "ssh", "-F", str(self.config),
            "-vv", "-n", "-T", "-x", "-a", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "Demo-Dev", "true",
        ])
        self.assertEqual(self.ctx.store.metadata(), {})

    def test_invalid_target_is_refused(self):
        with self.assertRaises(ValueError):
            app.run_connection(self.ctx, "bad;touch", [])

    def test_json_cli(self):
        p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--list", "--json", "--env", "live"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([h["alias"] for h in json.loads(p.stdout)], ["Demo-Live"])

    def test_help_has_no_state_side_effects(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "unused")}):
            p = subprocess.run([sys.executable, str(ROOT / "app.py"), "-h"], capture_output=True)
        self.assertEqual(p.returncode, 0)
        self.assertFalse((self.home / "unused").exists())

    def test_portable_version_has_no_state_side_effects(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "unused")}):
            p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--version"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "sshelp 2.2.0")
        self.assertFalse((self.home / "unused").exists())

    def test_missing_config_suggests_setup(self):
        with patch.dict(os.environ, {"SSHELP_CONFIG": str(self.home / "missing-config")}):
            p = subprocess.run([sys.executable, str(ROOT / "app.py"), "--list"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("sshelp --setup", p.stderr)

    def test_add_host_confirmation_defaults_to_no(self):
        self.prepare_add_host()
        original = self.config.read_bytes()
        answers = ["Lab-Sbx", "192.0.2.99", "demo", "22", "EX00002", "Lab", ""]
        with patch("builtins.input", side_effect=answers), patch.object(app.sys.stdin, "isatty", return_value=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.add_host(self.ctx), 0)
        self.assertEqual(self.config.read_bytes(), original)

    def test_add_host_backup_and_minimal_entry(self):
        self.prepare_add_host()
        original = self.config.read_bytes()
        answers = ["Lab-Sbx", "192.0.2.99", "demo", "22", "EX00002", "Lab", "yes"]
        with patch("builtins.input", side_effect=answers), patch.object(app.sys.stdin, "isatty", return_value=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.add_host(self.ctx), 0)
        self.assertEqual(next((self.ctx.directory / "config-backups").iterdir()).read_bytes(), original)
        appended = self.config.read_bytes()[len(original):]
        self.assertIn(b"Host Lab-Sbx", appended)
        self.assertNotIn(b"RemoteForward", appended)
        self.assertNotIn(b"ForwardAgent", appended)

    def test_add_host_rejects_newline_injection(self):
        self.prepare_add_host()
        answers = ["Lab-Sbx", "192.0.2.99\nProxyCommand unsafe", "demo", "22", "", ""]
        with patch("builtins.input", side_effect=answers), patch.object(app.sys.stdin, "isatty", return_value=True), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                app.add_host(self.ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)