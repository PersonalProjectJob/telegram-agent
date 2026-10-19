import os
import stat
import tempfile
import unittest
from itertools import product
from pathlib import Path

from nailblog.state import POST_STATES, TRANSITIONS, InvalidTransition, State, StateError

SLOT = "2026-10-17T06:00+07:00"
ALLOWED = {("writing", "held"), ("writing", "review"), ("held", "writing"), ("review", "writing"),
           ("review", "scheduled"), ("review", "held"), ("scheduled", "before_publish"), ("scheduled", "held"), ("before_publish", "published"),
           ("before_publish", "uncertain"), ("uncertain", "published"), ("uncertain", "held")}


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "state" / "nailblog.sqlite3")

    def test_tables_match_the_spec(self):
        with self.state.connection() as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"control", "topics", "posts", "images", "runs", "usage", "notices"} <= names)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            columns = lambda table: {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            self.assertEqual(columns("control"), {"id", "paused", "image_mode", "last_result"})
            self.assertEqual(columns("topics"), {"id", "week", "title", "category", "slot_at", "score", "sources_json", "state"})
            self.assertEqual(columns("posts"), {"id", "topic_id", "slot_at", "state", "draft_json", "fingerprint",
                                                "admin_id", "reason", "details"})
            self.assertEqual(columns("images"), {"id", "post_id", "position", "prompt", "round", "path", "sha256", "dhash",
                                                 "scores_json", "chosen", "uploaded_url"})
            self.assertEqual(columns("runs"), {"id", "post_id", "mode", "seed", "planned_at", "started_at", "ended_at",
                                               "n_generated", "n_pass_first", "rounds", "n_fallback", "usd", "met_deadline"})
            self.assertEqual(columns("usage"), {"at", "provider", "model", "units", "usd"})
            self.assertTrue({"key", "sent_at", "attempts"} <= columns("notices"))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_directory_and_database_modes(self):
        path = Path(self.state.path)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_loose_directory_mode_is_refused(self):
        path = Path(self.state.path)
        path.parent.chmod(0o755)
        with self.assertRaisesRegex(StateError, "0700"):
            State(path)

    def test_symlinked_database_is_refused(self):
        link = Path(self.directory.name) / "link.sqlite3"
        try:
            link.symlink_to(self.state.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(StateError, "symlink"):
            State(link)

    def test_control_defaults_and_updates(self):
        self.assertEqual(self.state.control(), {"id": 1, "paused": 0, "image_mode": "fixed", "last_result": "not started"})
        self.state.set_paused(True)
        self.state.set_image_mode("adaptive")
        control = self.state.control()
        self.assertEqual((control["paused"], control["image_mode"]), (1, "adaptive"))
        with self.assertRaises(StateError):
            self.state.set_image_mode("whenever")
        with self.assertRaises(StateError):
            State(Path(self.directory.name) / "other.sqlite3", image_mode="whenever")

    def test_initial_image_mode_is_kept_across_reopen(self):
        path = Path(self.directory.name) / "mode.sqlite3"
        State(path, image_mode="random")
        self.assertEqual(State(path, image_mode="fixed").control()["image_mode"], "random")

    def test_graph_matches_spec_edges_exactly(self):
        edges = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
        self.assertEqual(edges, ALLOWED)

    def test_every_pair_of_states_allowed_iff_edge(self):
        for old, new in product(POST_STATES, POST_STATES):
            with self.subTest(old=old, new=new):
                post = self.state.add_post(SLOT, state=old)
                if (old, new) in ALLOWED:
                    self.state.transition(post, new, reason="why", evidence="readback ok")
                    self.assertEqual(self.state.get_post(post)["state"], new)
                else:
                    with self.assertRaises(InvalidTransition):
                        self.state.transition(post, new)
                    self.assertEqual(self.state.get_post(post)["state"], old)

    def test_full_happy_path_records_reason_and_details(self):
        post = self.state.add_post(SLOT)
        for step in ("review", "scheduled", "before_publish"):
            self.state.transition(post, step)
        self.state.transition(post, "published", reason="readback ok", details={"photos": 4},
                              evidence={"readback": "Published", "status": "Published"})
        row = self.state.get_post(post)
        self.assertEqual((row["state"], row["reason"], row["details"]),
                         ("published", "readback ok", '{"evidence": {"readback": "Published", "status": "Published"}, "photos": 4}'))

    def test_unknown_post_and_unknown_state_raise(self):
        with self.assertRaises(InvalidTransition):
            self.state.transition(999, "review")
        post = self.state.add_post(SLOT)
        with self.assertRaises(InvalidTransition):
            self.state.transition(post, "nonsense")
        with self.assertRaises(StateError):
            self.state.add_post(SLOT, state="nonsense")

    def test_move_allowed_only_before_publishing_starts(self):
        for state in POST_STATES:
            post = self.state.add_post(SLOT, state=state)
            if state in ("writing", "held", "review", "scheduled"):
                self.state.move_post(post, "2026-10-18T19:00+07:00")
                self.assertEqual(self.state.get_post(post)["slot_at"], "2026-10-18T19:00+07:00")
            else:
                with self.assertRaises(InvalidTransition):
                    self.state.move_post(post, "2026-10-18T19:00+07:00")
        with self.assertRaises(InvalidTransition):
            self.state.move_post(999, SLOT)

    def test_topics_lifecycle(self):
        topic = self.state.add_topic("2026-W43", "Chủ đề", "nail-job", sources=["https://a"])
        self.assertEqual(self.state.get_topic(topic)["state"], "proposed")
        self.assertTrue(self.state.set_topic_state(topic, "approved", only_from=("proposed",)))
        self.assertFalse(self.state.set_topic_state(topic, "approved", only_from=("proposed",)))
        self.assertTrue(self.state.edit_topic(topic, "Mới"))
        self.assertTrue(self.state.set_topic_state(topic, "dropped"))
        self.assertFalse(self.state.edit_topic(topic, "Nữa"))
        self.assertEqual(self.state.list_topics(), [])
        self.assertEqual(self.state.status()["topics"], {"dropped": 1})

    def test_list_posts_filters_by_state_and_slot(self):
        first = self.state.add_post("2026-10-17T06:00+07:00")
        second = self.state.add_post("2026-10-17T19:00+07:00", state="review")
        self.assertEqual([r["id"] for r in self.state.list_posts()], [first, second])
        self.assertEqual([r["id"] for r in self.state.list_posts(("review",))], [second])
        self.assertEqual([r["id"] for r in self.state.list_posts(slot_from="2026-10-17T12:00+07:00")], [second])
        self.assertEqual([r["id"] for r in self.state.list_posts(slot_to="2026-10-17T12:00+07:00")], [first])

    def test_notice_dedup_and_backoff(self):
        self.assertTrue(self.state.notice_due("k", 100))
        self.state.notice_failed("k", 100)
        self.assertFalse(self.state.notice_due("k", 200))
        self.assertTrue(self.state.notice_due("k", 401))
        self.state.notice_sent("k", 401)
        self.assertFalse(self.state.notice_due("k", 10 ** 9))
        self.state.notice_failed("k", 500)  # A late failure never reopens a delivered notice.
        self.assertFalse(self.state.notice_due("k", 10 ** 9))
        with self.state.connection() as db:
            self.assertEqual(db.execute("SELECT attempts FROM notices WHERE key='k'").fetchone()[0], 2)

    def test_usage_rows_sum_by_range_and_prune(self):
        self.state.add_usage(10, "deepseek", "m", 100, 0.5)
        self.state.add_usage(20, "gemini", "i", 2, 0.25)
        self.assertEqual(self.state.usd_between(0, 15), 0.5)
        self.assertEqual(self.state.usd_between(0, 100), 0.75)
        self.assertEqual(self.state.usage_between(15, 100), [(20.0, "gemini", "i", 2.0, 0.25)])
        self.assertEqual(self.state.prune_usage(15), 1)
        self.assertEqual(self.state.usd_between(0, 100), 0.25)

    def test_data_survives_reopen(self):
        post = self.state.add_post(SLOT, state="review")
        self.assertEqual(State(self.state.path).get_post(post)["state"], "review")


if __name__ == "__main__":
    unittest.main()
