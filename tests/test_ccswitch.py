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
