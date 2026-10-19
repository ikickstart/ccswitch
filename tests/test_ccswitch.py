"""ccswitch tests. They never touch the real Keychain: a fake `security` and a fake
`claude` sit first on PATH, HOME is a temp dir."""
import json, os, pathlib, subprocess, sys, tempfile, time, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ccswitch"
FAKEBIN = ROOT / "tests" / "fakebin"
LIVE = "Claude Code-credentials"


def oauth(email, dead=False, expired=False):
    now = int(time.time() * 1000)
    return {"claudeAiOauth": {
        "accessToken": f"at-{email}", "refreshToken": "" if dead else f"rt-{email}",
        "expiresAt": now + 3600_000,
        "refreshTokenExpiresAt": now - 1000 if expired else now + 86400_000,
        "scopes": ["user:inference"], "subscriptionType": "team", "rateLimitTier": "default"},
        "organizationUuid": f"org-{email}"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = pathlib.Path(self.tmp.name)
        self.home = home
        self.kc = home / "keychain.json"
        self.env = {**os.environ, "HOME": str(home), "FAKE_KEYCHAIN": str(self.kc),
                    "CCSWITCH_HOME": str(home / "ccswitch"), "PATH": f"{FAKEBIN}:{os.environ['PATH']}"}
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        for k in [k for k in self.env if k.startswith("CMUX_") or k.startswith("CCSWITCH_")]:
            self.env.pop(k)
        self.env["CCSWITCH_HOME"] = str(home / "ccswitch")
        self.keychain({LIVE: {"tester": json.dumps(oauth("a@x.com"))}})
        self.config({"oauthAccount": {"emailAddress": "a@x.com", "organizationName": "A org"},
                     "numStartups": 3, "mcpServers": {"m": {}},
                     "cachedUsageUtilization": {"fetchedAtMs": 1, "utilization": {
                         "five_hour": {"utilization": 90, "resets_at": "2999-01-01T00:00:00+00:00"},
                         "seven_day": {"utilization": 10, "resets_at": "2999-01-02T00:00:00+00:00"}}}})

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def keychain(self, d=None):
        if d is not None:
            self.kc.write_text(json.dumps(d))
        return json.loads(self.kc.read_text())

    def live(self):
        return json.loads(next(iter(self.keychain()[LIVE].values())))

    def config(self, d=None):
        p = self.home / ".claude.json"
        if d is not None:
            p.write_text(json.dumps(d))
        return json.loads(p.read_text())

    def index(self):
        return json.loads((self.home / "ccswitch" / "slots.json").read_text())

    def cc(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env, capture_output=True,
                              text=True, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

    def login(self, email):
        self.assertEqual(self.cc("login", email).returncode, 0)


class Save(Base):
    def test_save_files_the_live_login_under_its_email_with_the_usage_snapshot(self):
        p = self.cc("save")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("a@x.com", self.keychain()["ccswitch"])
        slot = json.loads(self.keychain()["ccswitch"]["a@x.com"])
        self.assertEqual(slot["credentials"], oauth("a@x.com") | {"claudeAiOauth": slot["credentials"]["claudeAiOauth"]})
        self.assertEqual(slot["oauthAccount"]["emailAddress"], "a@x.com")
        idx = self.index()["slots"]["a@x.com"]
        self.assertEqual(idx["usage"]["five_hour"]["utilization"], 90)
        self.assertEqual(idx["plan"], "team")
        self.assertNotIn("rt-", json.dumps(self.index()), "the index must never hold a token")

    def test_save_refuses_a_dead_login_and_prints_the_fallback(self):
        self.keychain({LIVE: {"tester": json.dumps(oauth("a@x.com", dead=True))}})
        p = self.cc("save")
        self.assertEqual(p.returncode, 2)
        self.assertIn("claude auth login --email a@x.com && ccswitch save", p.stderr)
        self.assertNotIn("ccswitch", self.keychain())

    def test_save_refuses_a_live_item_of_unknown_shape(self):
        self.keychain({LIVE: {"tester": json.dumps({"somethingElse": {}})}})
        p = self.cc("save")
        self.assertEqual(p.returncode, 2)
        self.assertIn("shape", p.stderr)
        self.assertNotIn("ccswitch", self.keychain())


class Switch(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cc("save").returncode, 0)   # slot a
        self.login("b@x.com")                               # now live as b
        self.assertEqual(self.cc("save").returncode, 0)   # slot b

    def test_switch_moves_only_the_oauth_block_and_relabels_the_config(self):
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")
        p = self.cc("a", "-y")
        self.assertEqual(p.returncode, 0, p.stderr)
        live = self.live()
        self.assertEqual(live["claudeAiOauth"]["refreshToken"], "rt-a@x.com")
        self.assertEqual(live["organizationUuid"], "org-a@x.com")
        cfg = self.config()
        self.assertEqual(cfg["oauthAccount"]["emailAddress"], "a@x.com")
        self.assertEqual(cfg["cachedUsageUtilization"]["utilization"]["five_hour"]["utilization"], 90)
        self.assertEqual(cfg["numStartups"], 3, "other keys of .claude.json must survive")
        self.assertEqual(cfg["mcpServers"], {"m": {}})
        self.assertIn("now on a@x.com", p.stderr)

    def test_switch_saves_the_outgoing_login_first_so_a_rotated_token_is_not_lost(self):
        # a session rotated b's refresh token after the slot was saved
        kc = self.keychain()
        doc = json.loads(kc[LIVE]["tester"]); doc["claudeAiOauth"]["refreshToken"] = "rt-b@x.com-rotated"
        kc[LIVE]["tester"] = json.dumps(doc); self.keychain(kc)
        self.assertEqual(self.cc("a", "-y").returncode, 0)
        slot_b = json.loads(self.keychain()["ccswitch"]["b@x.com"])
        self.assertEqual(slot_b["credentials"]["claudeAiOauth"]["refreshToken"], "rt-b@x.com-rotated")
        # and the round trip restores it
        self.assertEqual(self.cc("b", "-y").returncode, 0)
        self.assertEqual(self.live()["claudeAiOauth"]["refreshToken"], "rt-b@x.com-rotated")

    def test_switch_to_the_current_account_is_a_no_op(self):
        p = self.cc("b@x.com", "-y")
        self.assertEqual(p.returncode, 0)
        self.assertIn("already on b@x.com", p.stderr)

    def test_a_dead_slot_is_refused_with_the_fallback_and_the_live_login_is_untouched(self):
        kc = self.keychain()
        slot = json.loads(kc["ccswitch"]["a@x.com"]); slot["credentials"]["claudeAiOauth"]["refreshToken"] = ""
        kc["ccswitch"]["a@x.com"] = json.dumps(slot); self.keychain(kc)
        p = self.cc("a", "-y")
        self.assertEqual(p.returncode, 2)
        self.assertIn("claude auth login --email a@x.com && ccswitch save", p.stderr)
        self.assertEqual(self.live()["claudeAiOauth"]["refreshToken"].startswith("rt-b@x.com"), True)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")

    def test_an_expired_refresh_token_counts_as_dead(self):
        kc = self.keychain()
        kc["ccswitch"]["a@x.com"] = json.dumps(json.loads(kc["ccswitch"]["a@x.com"]) | {"credentials": oauth("a@x.com", expired=True)})
        self.keychain(kc)
        p = self.cc("a", "-y")
        self.assertEqual(p.returncode, 2)
        self.assertIn("expired", p.stderr)

    def test_a_prefix_resolves_when_unique_and_is_refused_when_ambiguous(self):
        self.assertEqual(self.cc("a@", "-y").returncode, 0)
        self.login("ab@x.com"); self.assertEqual(self.cc("save").returncode, 0)
        p = self.cc("a", "-y")
        self.assertEqual(p.returncode, 2)
        self.assertIn("ambiguous", p.stderr)
        self.assertEqual(self.cc("nobody", "-y").returncode, 2)

    def test_without_a_tty_the_switch_needs_dash_y(self):
        p = self.cc("a")
        self.assertEqual(p.returncode, 2)
        self.assertIn("-y", p.stderr)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")

    def test_probe_reports_a_refused_token(self):
        kc = self.keychain()
        slot = json.loads(kc["ccswitch"]["a@x.com"]); slot["credentials"]["claudeAiOauth"]["refreshToken"] = "rt-DEAD"
        kc["ccswitch"]["a@x.com"] = json.dumps(slot); self.keychain(kc)
        p = self.cc("a", "-y", "--probe")
        self.assertEqual(p.returncode, 2)
        self.assertIn("probe failed", p.stderr)
        self.assertEqual(self.cc("b", "-y", "--probe").returncode, 0)

    def test_list_marks_the_current_account_and_never_prints_a_token(self):
        p = self.cc("list")
        self.assertEqual(p.returncode, 0)
        self.assertIn("* b@x.com", p.stdout)
        self.assertIn("  a@x.com", p.stdout)
        self.assertNotIn("rt-", p.stdout + p.stderr)

    def test_remove_forgets_the_slot_only(self):
        self.assertEqual(self.cc("remove", "a", "-y").returncode, 0)
        self.assertNotIn("a@x.com", self.keychain()["ccswitch"])
        self.assertNotIn("a@x.com", self.index()["slots"])
        self.assertIn(LIVE, self.keychain())

    def test_doctor_is_green_then_flags_a_dead_slot(self):
        self.assertEqual(self.cc("doctor").returncode, 0)
        kc = self.keychain()
        slot = json.loads(kc["ccswitch"]["a@x.com"]); slot["credentials"]["claudeAiOauth"]["refreshToken"] = ""
        kc["ccswitch"]["a@x.com"] = json.dumps(slot); self.keychain(kc)
        p = self.cc("doctor")
        self.assertEqual(p.returncode, 1)
        self.assertIn("x  slot a@x.com", p.stdout.replace("x slot", "x  slot"))


class Inputs(Base):
    def test_an_email_that_could_break_the_security_command_line_is_refused(self):
        for bad in ('x"@y.com', 'a b@y.com', 'a@y.com; rm -rf /', "a'@y.com", "not-an-email"):
            p = self.cc("save", "--as", bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertNotIn("ccswitch", self.keychain(), bad)
        self.assertEqual(self.cc("login", 'x"@y.com').returncode, 2)

    def test_a_hand_edited_index_entry_that_is_not_an_email_never_reaches_the_keychain(self):
        self.assertEqual(self.cc("save").returncode, 0)
        idx_path = self.home / "ccswitch" / "slots.json"
        idx = json.loads(idx_path.read_text())
        idx["slots"]['evil"; -s x'] = {"saved_at": "2026-01-01T00:00:00+00:00"}
        idx_path.write_text(json.dumps(idx))
        p = self.cc("evil", "-y")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no slot matches", p.stderr)


class ConfigDir(Base):
    def test_claude_config_dir_moves_the_json_inside_it(self):
        cdir = self.home / "alt"
        cdir.mkdir()
        (cdir / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "alt@x.com"}}))
        self.env["CLAUDE_CONFIG_DIR"] = str(cdir)
        self.assertEqual(self.cc("save", "--as", "alt@x.com").returncode, 0)
        self.assertEqual(json.loads(self.keychain()["ccswitch"]["alt@x.com"])["oauthAccount"]["emailAddress"], "alt@x.com")


if __name__ == "__main__":
    unittest.main()


class Tee(Base):
    def feed(self, **over):
        d = {"session_id": "sess-1", "transcript_path": str(self.home / "t.jsonl"), "cwd": "/x",
             "model": {"id": "claude-fable-5-1"}, "context_window": {"used_percentage": 42.5},
             "rate_limits": {"five_hour": {"used_percentage": 61, "resets_at": 4102444800},
                             "seven_day": {"used_percentage": 20, "resets_at": 4102444800}}}
        d.update(over)
        return json.dumps(d)

    def test_tee_records_the_feed_and_passes_the_original_command_through(self):
        (self.home / "ccswitch").mkdir(parents=True, exist_ok=True)
        (self.home / "ccswitch" / "statusline.json").write_text(json.dumps({"command": "sed 's/^/HUD:/' | head -c 20"}))
        env = {**self.env, "CMUX_SURFACE_ID": "SURF-1", "CMUX_WORKSPACE_ID": "WS-1"}
        p = subprocess.run([sys.executable, str(SCRIPT), "statusline-tee"], env=env, input=self.feed(), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("HUD:{"), p.stdout)
        rec = json.loads((self.home / "ccswitch" / "sessions" / "sess-1.json").read_text())
        self.assertEqual(rec["context_pct"], 42.5)
        self.assertEqual(rec["five_hour"]["used_percentage"], 61)
        self.assertEqual(rec["cmux"], {"surface": "SURF-1", "workspace": "WS-1"})
        self.assertEqual(rec["cwd"], "/x")

    def test_tee_survives_garbage_and_still_runs_the_original(self):
        (self.home / "ccswitch").mkdir(parents=True, exist_ok=True)
        (self.home / "ccswitch" / "statusline.json").write_text(json.dumps({"command": "echo HUD"}))
        p = subprocess.run([sys.executable, str(SCRIPT), "statusline-tee"], env=self.env, input="not json", capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "HUD")


class Watch(Base):
    """Three slots a, b, c; b is live. The feed says b is at 95%."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.cc("save").returncode, 0)               # a (its usage snapshot: 5h 90%)
        self.login("c@x.com"); self.assertEqual(self.cc("save").returncode, 0)   # c (5h 7%)
        self.login("b@x.com"); self.assertEqual(self.cc("save").returncode, 0)   # b, live
        self.cmux_log = self.home / "cmux.log"; self.cmux_log.write_text("")
        self.transcripts = self.home / "transcripts"; self.transcripts.mkdir()
        self.env.update({"FAKE_CMUX_LOG": str(self.cmux_log), "FAKE_CMUX_TRANSCRIPTS": str(self.transcripts),
                         "CCSWITCH_NO_NOTIFY": "1"})
        (self.home / ".claude").mkdir(exist_ok=True)

    def session(self, sid, surface, five_hour=95, ctx=30, age=0):
        t = self.transcripts / f"{surface}.jsonl"
        t.touch()
        d = {"session_id": sid, "ts": int(time.time()) - age, "pid": 1, "cwd": "/x", "transcript_path": str(t),
             "context_pct": ctx, "five_hour": {"used_percentage": five_hour, "resets_at": 4102444800},
             "seven_day": {"used_percentage": 10, "resets_at": 4102444800},
             "cmux": {"surface": surface, "workspace": "WS"}}
        sd = self.home / "ccswitch" / "sessions"; sd.mkdir(parents=True, exist_ok=True)
        (sd / f"{sid}.json").write_text(json.dumps(d))

    def cmux_calls(self):
        return [json.loads(l) for l in self.cmux_log.read_text().splitlines()]

    def state(self):
        return json.loads((self.home / "ccswitch" / "watch-state.json").read_text())

    def test_below_the_threshold_nothing_happens(self):
        self.session("s1", "S1", five_hour=50)
        p = self.cc("watch", "--once", "--switch-at", "90", "-v")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")
        self.assertEqual(self.cmux_calls(), [])

    def test_over_the_threshold_every_session_is_compacted_then_the_account_with_most_headroom_is_loaded(self):
        self.session("s1", "S1", five_hour=95); self.session("s2", "S2", five_hour=95)
        p = self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "native,cmux", "--compact-timeout", "5")
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.cmux_calls()
        sends = [c for c in calls if c[0] == "send"]
        enters = [c for c in calls if c[0] == "send-key"]
        self.assertEqual(sorted(c[c.index("--surface") + 1] for c in sends), ["S1", "S2"])
        self.assertEqual(len(enters), 2)
        self.assertTrue(all(c[-1] == "/compact" for c in sends))
        self.assertIn("compact_boundary", (self.transcripts / "S1.jsonl").read_text())
        # c had 7% in its snapshot, a had 90%: c wins
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "c@x.com")
        self.assertEqual(self.live()["claudeAiOauth"]["refreshToken"].startswith("rt-c@x.com"), True)
        st = self.state()
        self.assertGreater(st["last_switch_at"], 0)
        self.assertGreater(st["ignore_before"], st["last_switch_at"])
        self.assertIn("switched b@x.com -> c@x.com", p.stderr)
        # the outgoing account's usage was written to its slot from the feed
        self.assertEqual(self.index()["slots"]["b@x.com"]["usage"]["five_hour"]["utilization"], 95)

    def test_within_the_cooldown_no_second_switch(self):
        self.session("s1", "S1", five_hour=95)
        self.assertEqual(self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux").returncode, 0)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "c@x.com")
        self.session("s1", "S1", five_hour=95)       # fresh sample, still says 95
        self.assertEqual(self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux").returncode, 0)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "c@x.com")

    def test_when_every_slot_is_over_the_threshold_nothing_is_switched(self):
        idx_path = self.home / "ccswitch" / "slots.json"
        idx = json.loads(idx_path.read_text())
        for e in idx["slots"]:
            idx["slots"][e]["usage"] = {"five_hour": {"utilization": 99, "resets_at": "2999-01-01T00:00:00+00:00"}}
        idx_path.write_text(json.dumps(idx))
        self.session("s1", "S1", five_hour=95)
        p = self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux")
        self.assertEqual(p.returncode, 0)
        self.assertIn("candidates: none", p.stderr)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")
        self.assertEqual(self.cmux_calls(), [], "no compaction when there is nothing to switch to")

    def test_a_slot_whose_window_has_reset_counts_as_empty(self):
        idx_path = self.home / "ccswitch" / "slots.json"
        idx = json.loads(idx_path.read_text())
        idx["slots"]["a@x.com"]["usage"] = {"five_hour": {"utilization": 99, "resets_at": "2000-01-01T00:00:00+00:00"}}
        idx["slots"]["c@x.com"]["usage"] = {"five_hour": {"utilization": 40, "resets_at": "2999-01-01T00:00:00+00:00"}}
        idx_path.write_text(json.dumps(idx))
        self.session("s1", "S1", five_hour=95)
        self.assertEqual(self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux").returncode, 0)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "a@x.com")

    def test_a_dead_candidate_is_skipped_for_the_next_one(self):
        idx_path = self.home / "ccswitch" / "slots.json"
        idx = json.loads(idx_path.read_text())
        idx["slots"]["a@x.com"]["usage"] = {"five_hour": {"utilization": 50, "resets_at": "2999-01-01T00:00:00+00:00"}}
        idx_path.write_text(json.dumps(idx))
        kc = self.keychain()
        slot = json.loads(kc["ccswitch"]["c@x.com"]); slot["credentials"]["claudeAiOauth"]["refreshToken"] = ""
        kc["ccswitch"]["c@x.com"] = json.dumps(slot); self.keychain(kc)
        self.session("s1", "S1", five_hour=95)
        p = self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "a@x.com")
        self.assertIn("trying the next candidate", p.stderr)

    def test_cmux_refusal_is_logged_and_the_switch_still_happens(self):
        self.session("s1", "S1", five_hour=95)
        self.env["FAKE_CMUX_REFUSE"] = "1"
        p = self.cc("watch", "--once", "--switch-at", "90", "--compact-via", "cmux", "--compact-timeout", "1")
        self.assertEqual(p.returncode, 0)
        self.assertIn("cmux refused", p.stderr)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "c@x.com")

    def test_context_threshold_compacts_a_full_session_once_per_cooldown(self):
        self.session("s1", "S1", five_hour=10, ctx=85); self.session("s2", "S2", five_hour=10, ctx=40)
        self.assertEqual(self.cc("watch", "--once", "--compact-at", "80", "--compact-via", "cmux").returncode, 0)
        sends = [c for c in self.cmux_calls() if c[0] == "send"]
        self.assertEqual([c[c.index("--surface") + 1] for c in sends], ["S1"])
        self.session("s1", "S1", five_hour=10, ctx=85)
        self.assertEqual(self.cc("watch", "--once", "--compact-at", "80", "--compact-via", "cmux").returncode, 0)
        self.assertEqual(len([c for c in self.cmux_calls() if c[0] == "send"]), 1, "cooldown")
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")

    def test_a_custom_compaction_command_gets_the_session_json(self):
        out = self.home / "custom.txt"
        self.session("s1", "S1", five_hour=10, ctx=85)
        p = self.cc("watch", "--once", "--compact-at", "80", "--compact-via", f"cmd:cat > {out}")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(out.read_text())["session_id"], "s1")

    def test_native_threshold_lands_in_the_settings_env_and_is_removed_on_uninstall(self):
        sp = self.home / ".claude" / "settings.json"
        sp.write_text(json.dumps({"statusLine": {"type": "command", "command": "node hud.mjs", "refreshInterval": 10}, "model": "x"}))
        p = self.cc("daemon", "install", "--switch-at", "85", "--compact-at", "80", "--no-launchd")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(sp.read_text())
        self.assertEqual(d["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "80")
        self.assertEqual(d["model"], "x")
        self.assertIn("statusline-tee", d["statusLine"]["command"])
        self.assertEqual(json.loads((self.home / "ccswitch" / "statusline.json").read_text())["command"], "node hud.mjs")
        plist = (self.home / "Library" / "LaunchAgents" / "com.ccswitch.watch.plist").read_text()
        self.assertIn("<string>--switch-at</string>", plist)
        self.assertIn("<string>85</string>", plist)
        self.assertIn("<string>--compact-at</string>", plist)
        p = self.cc("daemon", "uninstall", "--no-launchd")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(sp.read_text())
        self.assertNotIn("env", d)
        self.assertEqual(d["statusLine"]["command"], "node hud.mjs")
        self.assertFalse((self.home / "Library" / "LaunchAgents" / "com.ccswitch.watch.plist").exists())

    def test_dry_run_changes_nothing(self):
        self.session("s1", "S1", five_hour=95)
        p = self.cc("watch", "--once", "--switch-at", "90", "--compact-at", "20", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("would switch b@x.com -> c@x.com", p.stderr)
        self.assertEqual(self.config()["oauthAccount"]["emailAddress"], "b@x.com")
        self.assertEqual(self.cmux_calls(), [])
