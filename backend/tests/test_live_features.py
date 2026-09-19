"""
Unit tests for live progress, offline guard, network status, answer checks, exports and the
model helper. They need no running services (Ollama and the database are stubbed where needed).

Run from the repo root:
    .venv\\Scripts\\python -m unittest backend.tests.test_live_features -v
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from docx import Document

from backend.app import live, llm, network, quality
from backend.app.agent import doc_context as dc
from backend.app.outputs.export import ExportError, answer_of, export_answer
from backend.app.routes import tasks as task_routes
from backend.app.routes import ws as ws_routes
from backend.app.tools import ocr


class LiveRunTests(unittest.TestCase):
    def setUp(self):
        self.run_ = live.hub.create("unit")
        self.token = live.bind(self.run_)

    def tearDown(self):
        live.unbind(self.token)
        live.hub.forget("unit")

    def test_stage_lifecycle_and_durations(self):
        with live.stage("a", "Reading"):
            time.sleep(0.02)
        stage = self.run_.snapshot()["stages"][0]
        self.assertEqual((stage["status"], stage["title"]), ("done", "Reading"))
        self.assertGreaterEqual(stage["duration"], 0.0)

    def test_failing_stage_is_marked_and_the_error_still_propagates(self):
        with self.assertRaises(ValueError):
            with live.stage("x", "Doomed"):
                raise ValueError("boom")
        stage = self.run_.snapshot()["stages"][0]
        self.assertEqual((stage["status"], stage["error"]), ("error", "boom"))

    def test_plan_shows_upcoming_steps_and_finish_marks_unused_ones_skipped(self):
        live.plan([("a", "A"), ("b", "B"), ("c", "C")])
        live.start("a")
        live.done("a")
        self.run_.finish("success")
        states = {s["id"]: s["status"] for s in self.run_.snapshot()["stages"]}
        self.assertEqual(states, {"a": "done", "b": "skipped", "c": "skipped"})
        self.assertEqual([t["id"] for t in self.run_.timeline()], ["a"])  # skipped steps are not stored

    def test_finish_closes_steps_still_running(self):
        live.start("a", "A")
        self.run_.finish("failed", "stopped")
        self.assertEqual(self.run_.snapshot()["stages"][0]["status"], "error")
        self.assertEqual(self.run_.snapshot()["message"], "stopped")

    def test_progress_and_detail_updates(self):
        live.start("a", "Reading sections")
        live.update("a", "Section 3 of 6", 3, 6)
        stage = self.run_.snapshot()["stages"][0]
        self.assertEqual((stage["detail"], stage["current"], stage["total"]), ("Section 3 of 6", 3, 6))

    def test_note_changes_the_detail_of_the_running_step_only(self):
        live.start("a", "A")
        live.done("a")
        live.start("b", "B")
        live.note("about 60 s")
        by = {s["id"]: s["detail"] for s in self.run_.snapshot()["stages"]}
        self.assertEqual((by["a"], by["b"]), ("", "about 60 s"))

    def test_text_streaming_and_reset(self):
        live.token("Hel")
        live.token("lo")
        self.assertEqual(self.run_.snapshot()["text"], "Hello")
        live.reset_text()
        self.assertEqual(self.run_.snapshot()["text"], "")

    def test_extras_are_kept_for_saving_with_the_task(self):
        live.set_extra("sources", [{"text": "x"}])
        self.assertEqual(self.run_.extras["sources"], [{"text": "x"}])

    def test_everything_is_a_no_op_when_nobody_watches(self):
        live.unbind(self.token)
        try:
            live.start("q", "Q")
            live.token("x")
            live.note("y")
            with live.stage("z", "Z"):
                pass
            self.assertFalse(live.watching())
        finally:
            self.token = live.bind(self.run_)

    def test_file_labels_and_conversation_context(self):
        live.set_file_labels({"C:/x/abc123.pdf": "My report.pdf"})
        self.assertEqual(live.file_label("C:/x/abc123.pdf"), "My report.pdf")
        self.assertEqual(live.file_label("C:/x/other.pdf"), "other.pdf")
        live.set_conversation("Question: q\nAnswer: a")
        self.assertIn("Answer: a", live.conversation())

    def test_context_reaches_worker_threads(self):
        async def go():
            await asyncio.to_thread(lambda: live.token("from a thread"))
        asyncio.run(go())
        self.assertEqual(self.run_.snapshot()["text"], "from a thread")


class LiveDeliveryTests(unittest.TestCase):
    def test_events_reach_a_subscriber_in_order_from_a_worker_thread(self):
        async def go():
            run = live.hub.create("delivery")
            sub = run.subscribe(asyncio.get_running_loop())

            def worker():
                token = live.bind(run)
                for i in range(3):
                    live.start(f"s{i}", f"Step {i}")
                    live.token(f"t{i}")
                    live.done(f"s{i}")
                live.unbind(token)

            await asyncio.to_thread(worker)
            run.finish("success")
            await asyncio.sleep(0.05)
            events = []
            while not sub.queue.empty():
                events.append(sub.queue.get_nowait())
            live.hub.forget("delivery")
            return events

        events = asyncio.run(go())
        started = [e["stage"]["id"] for e in events if e["type"] == "stage" and e["stage"]["status"] == "running"]
        self.assertEqual(started, ["s0", "s1", "s2"])
        self.assertEqual(events[-1]["type"], "status")
        self.assertIn("snapshot", [e["type"] for e in events])

    def test_a_flooded_subscriber_is_flagged_for_a_fresh_snapshot(self):
        async def go():
            run = live.hub.create("flood")
            sub = run.subscribe(asyncio.get_running_loop())
            for _ in range(live.QUEUE_LIMIT + 20):
                run.token("x")
            await asyncio.sleep(0.2)
            live.hub.forget("flood")
            return sub.overflowed

        self.assertTrue(asyncio.run(go()))

    def test_unsubscribe_stops_delivery(self):
        async def go():
            run = live.hub.create("unsub")
            sub = run.subscribe(asyncio.get_running_loop())
            run.unsubscribe(sub)
            run.token("x")
            await asyncio.sleep(0.05)
            live.hub.forget("unsub")
            return sub.queue.empty()

        self.assertTrue(asyncio.run(go()))

    def test_saved_snapshot_describes_a_finished_run_from_its_timeline(self):
        from datetime import datetime, timedelta, timezone

        start = datetime(2026, 9, 20, tzinfo=timezone.utc)
        snap = ws_routes._saved_snapshot({
            "_id": "abc", "status": "success", "final_message": "ok", "created_at": start,
            "finished_at": start + timedelta(seconds=12),
            "timeline": [{"id": "read:0", "title": "Reading", "status": "done", "detail": "x", "duration": 1.0, "error": None}],
        })
        self.assertEqual((snap["status"], snap["elapsed"], len(snap["stages"])), ("success", 12.0, 1))


class AnswerCheckTests(unittest.TestCase):
    SOURCE = ("The budget is 4,200,000 rupees. Pump P-77 read 8.4. The code is ORCHID-7731-DELTA. "
              "\"To install MySQL Server\" is the aim.")

    def test_real_facts_verify(self):
        answer = "Budget is 4,200,000 rupees; P-77 read 8.4; code ORCHID-7731-DELTA"
        self.assertEqual(quality.unverified_facts(answer, self.SOURCE), [])

    def test_invented_facts_are_reported(self):
        missing = quality.unverified_facts("Budget 5,300,000, pump P-99 read 9.1, code ZEBRA-1234-ECHO", self.SOURCE)
        self.assertTrue({"5,300,000", "P-99", "9.1", "ZEBRA-1234-ECHO"} <= set(missing))

    def test_ignores_list_numbers_facts_from_the_question_and_number_formatting(self):
        self.assertEqual(quality.unverified_facts("Here are 5 points:\n1. a\n2. b", self.SOURCE), [])
        self.assertEqual(quality.unverified_facts("Yes, 12345 is right", self.SOURCE, "is 12345 right?"), [])
        self.assertEqual(quality.unverified_facts("4200000", self.SOURCE), [])

    def test_quotes_must_match_the_document(self):
        self.assertEqual(quality.unverified_facts('The aim: "To install MySQL Server"', self.SOURCE), [])
        self.assertNotEqual(quality.unverified_facts('The aim: "To remove MySQL Server entirely"', self.SOURCE), [])

    def test_empty_inputs_and_the_report_size_limit(self):
        self.assertEqual(quality.unverified_facts("", self.SOURCE), [])
        self.assertEqual(quality.unverified_facts("12345", ""), [])
        many = " ".join(str(1000 + i) for i in range(30))
        self.assertLessEqual(len(quality.unverified_facts(many, "nothing here")), quality.MAX_REPORTED)


class OfflineGuardTests(unittest.TestCase):
    """The guard patches sockets process-wide, so it is exercised in a separate process."""

    def _run(self, code: str) -> str:
        script = "import os\nos.environ['KRYPTO_OFFLINE']='1'\n" + textwrap.dedent(code)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                             env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
        return out.stdout.strip()

    def test_internet_lan_and_dns_are_blocked_but_localhost_is_not(self):
        result = self._run("""
            import socket
            from backend.app import netguard
            netguard.install_if_requested()
            def blocked(fn):
                try: fn(); return False
                except OSError: return True
            listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(16); port = listener.getsockname()[1]
            print(blocked(lambda: socket.create_connection(("8.8.8.8", 53), timeout=2)),
                  blocked(lambda: socket.getaddrinfo("example.com", 80)),
                  blocked(lambda: socket.create_connection(("192.168.1.1", 80), timeout=2)),
                  blocked(lambda: socket.create_connection(("127.0.0.1", port), timeout=2)),
                  blocked(lambda: socket.create_connection(("localhost", port), timeout=2)),
                  netguard.stats()["blocked"])
        """)
        self.assertEqual(result, "True True True False False 3")

    def test_nothing_is_blocked_unless_the_mode_is_on(self):
        code = textwrap.dedent("""
            import socket
            from backend.app import netguard
            netguard.install_if_requested()
            print(netguard.enabled())
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                             env={k: v for k, v in __import__("os").environ.items() if k != "KRYPTO_OFFLINE"}
                             | {"PYTHONPATH": str(Path(__file__).resolve().parents[2])})
        self.assertEqual(out.stdout.strip(), "False")


class NetworkStatusTests(unittest.TestCase):
    def test_a_dead_target_reports_offline_quickly(self):
        started = time.time()
        self.assertIsNone(network.probe_internet(0.3, [("10.255.255.1", 9)]))
        self.assertLess(time.time() - started, 2)

    def test_a_reachable_local_listener_reports_a_latency(self):
        import socket

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            self.assertIsNotNone(network.probe_internet(1.0, [("127.0.0.1", port)]))
        finally:
            listener.close()

    def test_status_has_every_field_the_page_needs(self):
        status = network.status()
        for key in ("online", "latency_ms", "strict", "blocked_attempts", "recent_blocked", "wifi", "message", "checked_at"):
            self.assertIn(key, status)

    def test_status_says_offline_when_nothing_is_reachable(self):
        with mock.patch.object(network, "probe_internet", return_value=None):
            status = network.status()
        self.assertFalse(status["online"])
        self.assertIn("offline", status["message"].lower())

    def test_monitor_only_announces_real_changes(self):
        async def go():
            monitor = network.NetworkMonitor()
            queue = monitor.subscribe()
            with mock.patch.object(network, "status", return_value={"online": True, "strict": False, "blocked_attempts": 0, "wifi": None}):
                await monitor.refresh()
                await monitor.refresh()          # unchanged: no second message
            with mock.patch.object(network, "status", return_value={"online": False, "strict": False, "blocked_attempts": 0, "wifi": None}):
                await monitor.refresh()          # changed
            return queue.qsize()

        self.assertEqual(asyncio.run(go()), 2)


class ExportTests(unittest.TestCase):
    STEPS = [
        {"step": "[SYSTEM] read attached file a.pdf", "tool_used": "extract_text", "response": "doc text", "error": None},
        {"step": "Which port is used?", "tool_used": None, "response": "- The port is 3306.\n- The host is localhost.", "error": None},
    ]

    def test_answer_of_takes_the_last_real_answer(self):
        self.assertIn("3306", answer_of(self.STEPS))
        self.assertEqual(answer_of([{"step": "[SYSTEM] x", "response": "y", "error": None}]), "")

    def test_word_and_pdf_contain_the_question_and_answer(self):
        with tempfile.TemporaryDirectory():
            docx = export_answer(self.STEPS, "Which port is used?", None, "docx", [])
            pdf = export_answer(self.STEPS, "Which port is used?", None, "pdf", [])
            text = "\n".join(p.text for p in Document(docx).paragraphs)
            pdf_text = "".join(page.get_text() for page in pymupdf.open(pdf))
        self.assertTrue("Which port is used?" in text and "3306" in text)
        self.assertIn("3306", pdf_text)

    def test_image_is_a_chart_when_there_are_numbers_else_a_card(self):
        from PIL import Image

        card = export_answer(self.STEPS, "Which port is used?", None, "image", [])
        self.assertGreater(Image.open(card).width, 300)
        steps = [{"step": "Sales?", "tool_used": None, "response": "TITLE: Sales\nTYPE: bar\nQ1: 10\nQ2: 20", "error": None}]
        chart = export_answer(steps, "Sales?", None, "image", [])
        self.assertTrue(Path(chart).name.startswith("sales"))

    def test_refusals_have_clear_messages(self):
        with self.assertRaises(ExportError):
            export_answer([], "q", None, "docx", [])                      # nothing to export
        with self.assertRaises(ExportError):
            export_answer(self.STEPS, "q", None, "exe", [])               # unknown format
        with self.assertRaises(ExportError):
            export_answer([{"step": "q", "response": "ok", "error": None}], "q", None, "image", [])  # too short

    def test_corrosion_run_reuses_its_word_note(self):
        assessment = {"table": [{"location": "P-1"}], "attention": [], "required_thickness": 6}
        self.assertEqual(export_answer([], "q", assessment, "docx", ["/x/approval_note_1.docx"]), "/x/approval_note_1.docx")
        with self.assertRaises(ExportError):
            export_answer([], "q", assessment, "docx", [])


class ModelHelperTests(unittest.TestCase):
    def test_reading_speed_is_learned_from_real_timings_only(self):
        before = llm.read_speed()
        llm._learn_speed({"prompt_eval_count": 50, "prompt_eval_duration": 1_000_000_000})     # too short: ignored
        self.assertEqual(llm.read_speed(), before)
        llm._learn_speed({"prompt_eval_count": 1000, "prompt_eval_duration": 20_000_000_000})  # 50 tok/s
        self.assertGreater(llm.read_speed(), before)
        llm._read_speed = 28.0

    def test_estimate_scales_with_prompt_length(self):
        self.assertLess(llm.estimate_seconds("x" * 1000), llm.estimate_seconds("x" * 10000))

    def test_plain_call_returns_the_text_and_keeps_the_model_loaded(self):
        with mock.patch.object(llm, "ollama") as fake:
            fake.chat.return_value = {"message": {"content": "hello"}}
            self.assertEqual(llm.chat("m", "p", max_tokens=50), "hello")
            kwargs = fake.chat.call_args.kwargs
        self.assertEqual(kwargs["keep_alive"], llm.KEEP_ALIVE)
        self.assertEqual(kwargs["options"], {"num_predict": 50})

    def test_streaming_shows_tokens_live_and_returns_the_full_text(self):
        run = live.hub.create("stream")
        token = live.bind(run)
        try:
            chunks = [{"message": {"content": "Hel"}}, {"message": {"content": "lo"}}, {"message": {"content": ""}, "done": True}]
            with mock.patch.object(llm, "ollama") as fake:
                fake.chat.return_value = iter(chunks)
                live.start("answer", "Writing")
                text = llm.chat("m", "some prompt", stream_to_ui=True)
            self.assertEqual((text, run.snapshot()["text"]), ("Hello", "Hello"))
            self.assertEqual(run.snapshot()["stages"][0]["detail"], "Writing the answer")
        finally:
            live.unbind(token)
            live.hub.forget("stream")

    def test_no_streaming_when_nobody_is_watching(self):
        with mock.patch.object(llm, "ollama") as fake:
            fake.chat.return_value = {"message": {"content": "plain"}}
            self.assertEqual(llm.chat("m", "p", stream_to_ui=True), "plain")
            self.assertNotIn("stream", fake.chat.call_args.kwargs)


class DocContextExtraTests(unittest.TestCase):
    def test_compact_text_removes_waste_but_keeps_the_words(self):
        raw = "Port:\u00a03306 \n\n\n  Host  localhost\uf0b7 end"
        compact = dc.compact_text(raw)
        self.assertLess(len(compact), len(raw))
        for word in ("Port:", "3306", "localhost", "end"):
            self.assertIn(word, compact)
        self.assertNotIn("\uf0b7", compact)
        self.assertNotIn("\n\n", compact)

    def test_whole_document_needs_no_sources_but_passages_are_described(self):
        text, sources = dc.select_context_details("short text", "anything")
        self.assertEqual((text, sources), ("short text", []))
        long_text = "\n".join(f"paragraph {i} " + "y" * 200 for i in range(200))
        context, sources = dc.select_context_details(long_text, "give an overview")  # summary: evenly spread, no model
        self.assertTrue(sources and all(0 <= s["start_percent"] < 100 and s["text"] for s in sources))
        self.assertLess(len(context), len(long_text))

    def test_chunk_embeddings_are_remembered_between_questions(self):
        long_text = "\n".join(f"paragraph {i} about topic {i} " + "z" * 300 for i in range(80))
        calls = []

        def fake_embed(model, input):
            calls.append(len(input))
            return mock.Mock(embeddings=[[float(i % 7 + 1), 1.0, 0.5] for i in range(len(input))])

        dc._embed_cache.clear()
        with mock.patch.object(dc.ollama, "embed", side_effect=fake_embed):
            dc.select_context_details(long_text, "what about topic 5?")
            first = sum(calls)
            calls.clear()
            dc.select_context_details(long_text, "what about topic 5?")
            second = sum(calls)
        self.assertGreater(first, 1)
        self.assertEqual(second, 1)  # only the question itself is embedded again

    def test_progress_is_reported_for_each_section(self):
        text = "\n".join(f"line {i} " + "q" * 900 for i in range(120))
        seen = []
        dc.long_document_notes(text, lambda prompt, n: "- ok", progress=lambda n, of: seen.append((n, of)))
        self.assertEqual(seen[0][0], 1)
        self.assertEqual(seen[-1], (dc.MAX_SECTIONS, dc.MAX_SECTIONS))


class OcrCacheTests(unittest.TestCase):
    def test_same_content_same_key_different_content_different_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = Path(tmp) / "a.bin", Path(tmp) / "b.bin", Path(tmp) / "c.bin"
            a.write_bytes(b"same content")
            b.write_bytes(b"same content")
            c.write_bytes(b"other content")
            self.assertEqual(ocr._content_key(str(a)), ocr._content_key(str(b)))
            self.assertNotEqual(ocr._content_key(str(a)), ocr._content_key(str(c)))

    def test_results_are_remembered_and_empty_ones_are_not(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ocr, "CACHE_DIR", Path(tmp)):
            ocr._cache_put("k1", "recognized text")
            self.assertEqual(ocr._cache_get("k1"), "recognized text")
            ocr._cache_put("k2", "   ")
            self.assertIsNone(ocr._cache_get("k2"))
            self.assertIsNone(ocr._cache_get("missing"))

    def test_old_entries_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ocr, "CACHE_DIR", Path(tmp)), \
                mock.patch.object(ocr, "_CACHE_MAX_FILES", 3):
            for i in range(6):
                ocr._cache_put(f"k{i}", f"text {i}")
                time.sleep(0.01)
            self.assertEqual(len(list(Path(tmp).glob("*.txt"))), 3)
            self.assertEqual(ocr._cache_get("k5"), "text 5")

    def test_a_cached_image_is_not_read_again(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ocr, "CACHE_DIR", Path(tmp)):
            image = Path(tmp) / "x.png"
            from PIL import Image

            Image.new("RGB", (40, 40), "white").save(image)
            with mock.patch.object(ocr, "_ocr_image_file", return_value="text on image") as engine:
                self.assertEqual(ocr.extract_text(str(image)), "text on image")
                self.assertEqual(ocr.extract_text(str(image)), "text on image")
            self.assertEqual(engine.call_count, 1)


class TaskHelperTests(unittest.TestCase):
    def test_conversation_text_for_a_normal_run_and_a_corrosion_run(self):
        normal = task_routes._conversation_text({"goal": "Which port?", "steps": ExportTests.STEPS})
        self.assertIn("Question: Which port?", normal)
        self.assertIn("3306", normal)
        corrosion = task_routes._conversation_text({
            "goal": "note", "assessment": {"table": [1, 2, 3, 4], "attention": ["P-103"], "required_thickness": 6.0}, "steps": [],
        })
        self.assertIn("P-103", corrosion)
        self.assertIn("4 locations", corrosion)

    def test_serialize_carries_the_new_fields(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        doc = {"_id": "1", "goal": "g", "status": "success", "created_at": now, "finished_at": now,
               "timeline": [{"id": "a"}], "extras": {"unverified": ["x"]}, "parent_id": "0"}
        detail = task_routes._serialize(doc)
        self.assertEqual((detail["timeline"], detail["extras"], detail["parent_id"]), ([{"id": "a"}], {"unverified": ["x"]}, "0"))
        self.assertNotIn("timeline", task_routes._serialize(doc, with_steps=False))


class RefusalGuardTests(unittest.TestCase):
    def setUp(self):
        from backend.app.agent import graph
        self.looks = graph._looks_like_refusal

    def test_short_refusals_are_detected(self):
        for text in ["I can't fulfill this request.", "I cannot help with that.", "Sorry, I am unable to do that.",
                     "I’m not able to read images.", "As an AI I do not read images."]:
            self.assertTrue(self.looks(text), text)

    def test_honest_answers_are_not_refusals(self):
        for text in ["KRYPTO 4471", "I can't find that in the document.", "The document does not mention a phone number.",
                     "", "The port is 3306. " * 20, "Sorry to say, the text contains no such figure."]:
            self.assertFalse(self.looks(text), text)

    def test_document_answer_retries_once_after_a_refusal(self):
        from backend.app.agent import graph
        replies = iter(["I can't fulfill this request.", "KRYPTO 4471"])
        calls = []

        def fake(model, prompt, *, max_tokens=None, stream=False):
            calls.append(prompt)
            return next(replies)

        state = {"observations": [{"forced": True, "tool_used": "extract_text", "result": "KRYPTO 4471"}]}
        with mock.patch.object(graph, "_call_ollama", fake):
            answer = graph._answer_from_document(state, "Which brand name is shown in the image?", "m")
        self.assertEqual(answer, "KRYPTO 4471")
        self.assertEqual(len(calls), 2)
        self.assertIn("ALREADY been extracted", calls[1])

    def test_no_retry_when_the_first_answer_is_fine(self):
        from backend.app.agent import graph
        calls = []

        def fake(model, prompt, *, max_tokens=None, stream=False):
            calls.append(prompt)
            return "KRYPTO 4471"

        state = {"observations": [{"forced": True, "tool_used": "extract_text", "result": "KRYPTO 4471"}]}
        with mock.patch.object(graph, "_call_ollama", fake):
            graph._answer_from_document(state, "Which brand is this?", "m")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
