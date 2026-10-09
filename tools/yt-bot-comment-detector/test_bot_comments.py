import os
import unittest
from datetime import datetime, timedelta, timezone

import bot_comments as bc

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)


def c(cid, author, text, minutes_ago, likes=0):
    return bc.Comment(cid, author, text, NOW - timedelta(minutes=minutes_ago), likes)


class SampleFileTest(unittest.TestCase):
    def setUp(self):
        comments, context, now = bc.load_file(os.path.join(HERE, "sample_comments.json"))
        self.terms = bc.detect(comments, context, now)
        self.by_id = {x.id: x for x in comments}

    def test_campaign_comments_flagged(self):
        for cid in ("b1", "b2", "b3", "b4", "b5"):
            self.assertEqual(self.by_id[cid].label, "likely bot", cid)

    def test_organic_comments_ok(self):
        for cid in ("o1", "o2", "o3", "o4", "o5", "o6", "o7"):
            self.assertEqual(self.by_id[cid].label, "ok", (cid, self.by_id[cid].reasons))

    def test_only_promoted_name_detected(self):
        self.assertEqual(list(self.terms), ["Claudixum"])


class SignalTest(unittest.TestCase):
    def test_auto_handle(self):
        self.assertTrue(bc.is_auto_handle("@Sameer-v2p1g"))
        self.assertTrue(bc.is_auto_handle("@BipulChakma-sq5ce"))
        self.assertFalse(bc.is_auto_handle("@Jemarson2"))
        self.assertFalse(bc.is_auto_handle("@mary-jane"))

    def test_spread_out_mentions_are_not_a_campaign(self):
        comments = [c(str(i), f"@user{i}", "Kubernetes made this so much easier", i * 300) for i in range(5)]
        self.assertEqual(bc.detect(comments, now=NOW), {})

    def test_template_comments(self):
        comments = [
            c("1", "@a", "This video helped me so much, I made $500 this week!", 5),
            c("2", "@b", "This video helped me so much, I made $900 this week!!", 7),
            c("3", "@d", "What microphone do you use?", 60),
        ]
        bc.detect(comments, now=NOW)
        self.assertIn("near-duplicate of another account's comment", comments[0].reasons)
        self.assertNotIn("near-duplicate of another account's comment", comments[2].reasons)

    def test_manual_term_and_new_account(self):
        x = c("1", "@someone", "check out zorbix today", 10)
        x.account_created_at = NOW - timedelta(days=3)
        terms = bc.detect([x, c("2", "@other", "nice", 100)], now=NOW, manual_terms=["zorbix"])
        self.assertIn("zorbix", terms)
        self.assertIn("account is 3 days old", x.reasons)

    def test_extract_video_id(self):
        for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1", "https://youtu.be/dQw4w9WgXcQ",
                    "https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"):
            self.assertEqual(bc.extract_video_id(url), "dQw4w9WgXcQ")


if __name__ == "__main__":
    unittest.main()
