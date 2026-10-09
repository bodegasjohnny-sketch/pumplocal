"""Ask: no needless 'can't answer' disclaimers (seen in Johnny's test: a correct Gemma answer followed by
'Hindi masasagot ng datos ang tanong nang eksakto.')."""
import unittest
from unittest import mock

import ai
import core

CONTEXT = "Diesel: 174.216 L, ₱10,000.00 (6 sales). Total sales ₱16,350.00."


class StripCantAnswerTests(unittest.TestCase):
    def test_trailing_tagalog_disclaimer_removed_when_answer_uses_data(self):
        a = "Ang benta ng diesel ngayon ay ₱10,000.00. Hindi masasagot ng datos ang tanong nang eksakto."
        self.assertEqual(ai.strip_cant_answer(a, CONTEXT), "Ang benta ng diesel ngayon ay ₱10,000.00.")

    def test_trailing_english_disclaimer_removed(self):
        a = "Diesel sales today are ₱10,000.00 (174.216 L). However, the data cannot answer this exactly."
        self.assertEqual(ai.strip_cant_answer(a, CONTEXT), "Diesel sales today are ₱10,000.00 (174.216 L).")

    def test_real_cant_answer_is_kept(self):
        a = "Walang datos tungkol sa kahapon. Hindi masasagot ng datos ang tanong."
        self.assertEqual(ai.strip_cant_answer(a, CONTEXT), a)

    def test_single_sentence_is_never_emptied(self):
        a = "Hindi masasagot ng datos ang tanong nang eksakto, pero ₱10,000.00 ang diesel."
        self.assertEqual(ai.strip_cant_answer(a, CONTEXT), a)


class AskPromptTests(unittest.TestCase):
    def setUp(self):
        self.mock_ai = core.MOCK_AI
        core.MOCK_AI = False

    def tearDown(self):
        core.MOCK_AI = self.mock_ai

    def test_ask_strips_disclaimer_and_prompt_has_rules(self):
        seen = {}

        def fake_chat(messages, json_mode=False, num_predict=200):
            seen["prompt"] = messages[-1]["content"]
            return "Ang benta ng diesel ngayon ay ₱10,000.00. Hindi masasagot ng datos ang tanong nang eksakto."

        with mock.patch.object(ai, "chat", fake_chat), mock.patch.object(ai, "summary_context", lambda s: CONTEXT):
            r = ai.ask("Bakit hindi tugma ang diesel ngayon?", {})  # free-form: goes to the model
        self.assertEqual((r["answer"], r["source"], r["lang"]), ("Ang benta ng diesel ngayon ay ₱10,000.00.", "ai", "tl"))
        self.assertEqual(r["unverified_numbers"], [])
        p = seen["prompt"]
        self.assertIn("3-4 short sentences", p)  # a why-question (bakit): neutral causes allowed
        self.assertIn("Do not add disclaimers", p)
        self.assertIn("Only if the number needed is truly missing", p)


if __name__ == "__main__":
    unittest.main()
