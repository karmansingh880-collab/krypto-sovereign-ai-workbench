"""
Tests for the direct chat/code path, the code runner, picture understanding, LAN mode and the sovereignty proof.
None of them needs Ollama, MongoDB, Qdrant or Docker.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from backend.app import netguard, network, sovereignty
from backend.app.agent import direct, vision_context
from backend.app.agent.state import new_state
from backend.app.router import router
from backend.app.tools import code_runner


class RouterHelperTests(unittest.TestCase):
    def test_specialist_only_in_demo_mode_and_when_installed(self):
        with mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"}), \
                mock.patch.object(router, "installed_models", return_value={"llama3.2:3b", "qwen2.5-coder:1.5b"}):
            self.assertEqual(router.specialist_model("coder"), "qwen2.5-coder:1.5b")
            self.assertIsNone(router.specialist_model("vision"))  # not installed
            self.assertIsNone(router.specialist_model("general"))  # no specialist for this type
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KRYPTO_DEMO_MODEL", None)
            with mock.patch.object(router, "installed_models", return_value={"qwen2.5-coder:1.5b"}):
                self.assertIsNone(router.specialist_model("coder"))  # locked-model mode never substitutes

    def test_specialist_can_be_switched_off(self):
        env = {"KRYPTO_DEMO_MODEL": "llama3.2:3b", "KRYPTO_DEMO_CODER_MODEL": "none"}
        with mock.patch.dict(os.environ, env), mock.patch.object(router, "installed_models", return_value={"qwen2.5-coder:1.5b"}):
            self.assertIsNone(router.specialist_model("coder"))

    def test_route_to_and_describe_route(self):
        with mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"}):
            decision = router.route_to("coder", "the request asks for a program")
            self.assertEqual(decision.model_key, "coder")
            self.assertEqual(decision.designed_tag, "qwen2.5-coder:7b")
            self.assertEqual(decision.model_tag, "llama3.2:3b")
            info = router.describe_route(decision, "qwen2.5-coder:1.5b")
            self.assertEqual((info["task_type"], info["model"], info["substituted"]), ("coder", "qwen2.5-coder:1.5b", True))
            self.assertTrue(router.describe_route(decision)["substituted"])

    def test_locked_mode_reports_no_substitution(self):
        os.environ.pop("KRYPTO_DEMO_MODEL", None)
        decision = router.route_to("general", "x")
        self.assertFalse(router.describe_route(decision)["substituted"])

    def test_installed_models_reads_ollama_objects(self):
        class Item:
            def __init__(self, model):
                self.model = model

        class Listing:
            models = [Item("moondream:latest"), Item("llama3.2:3b")]

        fake = mock.Mock()
        fake.list.return_value = Listing()
        with mock.patch.object(router, "ollama", fake), mock.patch.object(router, "_installed_cache", (0.0, set())):
            names = router.installed_models()
        self.assertTrue({"moondream", "moondream:latest", "llama3.2:3b"} <= names)


class RequestKindTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_code_requests(self):
        for goal in ["simple code for python", "write a python function to check prime", "give me a program for fibonacci series",
                     "code for bubble sort", "create a python class for a bank account", "python program to check palindrome",
                     "write a script that counts words", "Write code to reverse a string", "fix this function so it sorts"]:
            self.assertEqual(direct.kind_of(goal), "code", goal)

    def test_questions_are_chat(self):
        for goal in ["What is the capital of Australia?", "Explain how a python decorator works", "why is the sky blue",
                     "what is a function in mathematics", "Tell me a joke", "How does photosynthesis work?", "summarize the causes of WW1"]:
            self.assertEqual(direct.kind_of(goal), "chat", goal)

    def test_other_languages_are_shown_not_run(self):
        self.assertEqual(direct.kind_of("write a javascript function to reverse a string"), "code-other")
        self.assertEqual(direct.kind_of("create a java class for a stack"), "code-other")
        self.assertEqual(direct.kind_of("write a python script that calls a java program"), "code")

    def test_follow_up_that_edits_earlier_code_is_a_code_request(self):
        earlier = "Question: write a python function to check prime\nAnswer: **Verified:** this code was run in a sandbox and passed."
        for goal in ("now make it work for a list of numbers and print the primes", "also add type hints", "handle negative numbers"):
            self.assertEqual(direct.kind_of(goal, earlier), "code", goal)
            self.assertEqual(direct.kind_of(goal), "chat", goal)  # the same words with no earlier code are a plain question
        for goal in ("Explain how it works", "why does it use a loop?"):
            self.assertEqual(direct.kind_of(goal, earlier), "chat", goal)
        self.assertEqual(direct.kind_of("use javascript instead", earlier), "code-other")
        self.assertEqual(direct.kind_of("And what is its population roughly?", "Question: capital of Australia?\nAnswer: Canberra."), "chat")

    def test_follow_up_context_keeps_the_codes_line_breaks(self):
        from backend.app.routes import tasks

        code_answer = "**Verified:** ok\n```python\ndef f(x):\n    return x\n```"
        text = tasks._conversation_text({"goal": "write f", "steps": [{"step": "Answer the request", "response": code_answer, "error": None}]})
        self.assertIn("def f(x):\n    return x", text)
        plain = tasks._conversation_text({"goal": "q", "steps": [{"step": "Answer the question", "response": "line one\n\nline two", "error": None}]})
        self.assertIn("line one line two", plain)

    def test_applies_only_in_demo_mode_without_files(self):
        self.assertTrue(direct.applies(new_state("hello")))
        self.assertFalse(direct.applies(new_state("hello", ["a.pdf"])))
        self.assertFalse(direct.applies(new_state("write an approval note")))
        os.environ.pop("KRYPTO_DEMO_MODEL", None)
        self.assertFalse(direct.applies(new_state("hello")))


class CodeExtractionTests(unittest.TestCase):
    def test_fenced_block(self):
        self.assertEqual(direct.extract_code("Sure.\n```python\nprint(1)\n```\nDone"), "print(1)")

    def test_unlabelled_and_uppercase_fence(self):
        self.assertEqual(direct.extract_code("```\nx = 1\n```"), "x = 1")
        self.assertEqual(direct.extract_code("```Python\nx = 2\n```"), "x = 2")

    def test_block_cut_off_before_closing_fence(self):
        self.assertEqual(direct.extract_code("Here:\n```python\ndef f():\n    return 1\n"), "def f():\n    return 1")

    def test_raw_code_without_fence(self):
        self.assertIn("def f", direct.extract_code("def f():\n    return 1"))

    def test_prose_has_no_code(self):
        self.assertEqual(direct.extract_code("This program adds two numbers."), "")

    def test_first_block_wins(self):
        self.assertEqual(direct.extract_code("```python\na=1\n```\ntext\n```python\nb=2\n```"), "a=1")

    def test_intro_is_the_sentence_before_the_code(self):
        self.assertEqual(direct._intro_of("This adds numbers.\n```python\nx=1\n```"), "This adds numbers.")
        self.assertEqual(direct._intro_of("```python\nx=1\n```"), "")
        self.assertEqual(direct._intro_of("x" * 400 + "\n```python\nx=1\n```"), "")


class AssertStrippingTests(unittest.TestCase):
    def test_removes_asserts_and_the_passed_banner(self):
        code = "def f(x):\n    return x + 1\nprint(f(1))\nassert f(1) == 3\nprint('All checks passed')\n"
        out = direct.without_asserts(code)
        self.assertNotIn("assert", out)
        self.assertNotIn("passed", out)
        self.assertIn("print(f(1))", out)

    def test_nothing_to_strip_returns_empty(self):
        self.assertEqual(direct.without_asserts("print(1)\n"), "")

    def test_unparseable_code_returns_empty(self):
        self.assertEqual(direct.without_asserts("def f(:\n"), "")

    def test_only_asserts_leaves_a_valid_program(self):
        out = direct.without_asserts("assert 1 == 2\n")
        compile(out, "x", "exec")


class FinalAnswerTests(unittest.TestCase):
    result = {"passed": True, "stdout": "hi\n", "stderr": "", "isolation_detail": "Docker container", "seconds": 1.0}

    def test_verified(self):
        text = direct._final_code_answer("It prints.", "print('hi')", self.result, 1)
        self.assertIn("**Verified:**", text)
        self.assertIn("```python\nprint('hi')\n```", text)
        self.assertIn("**Output**", text)

    def test_verified_after_fixes_says_so(self):
        self.assertIn("after 2 automatic fixes", direct._final_code_answer("", "x=1", self.result, 3))

    def test_checks_removed_is_not_called_verified(self):
        text = direct._final_code_answer("", "x=1", self.result, 3, checks_dropped=True)
        self.assertNotIn("**Verified:**", text)
        self.assertIn("not fully verified", text)

    def test_failure_shows_the_error(self):
        failed = {**self.result, "passed": False, "stdout": "", "stderr": "Traceback...\nNameError: x"}
        text = direct._final_code_answer("", "x", failed, 3)
        self.assertIn("**Not verified:**", text)
        self.assertIn("NameError", text)


class DirectHandleTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"})
        self._env.start()
        self.spec = mock.patch.object(direct, "specialist_model", lambda key: "qwen2.5-coder:1.5b" if key == "coder" else None)
        self.spec.start()

    def tearDown(self):
        self.spec.stop()
        self._env.stop()

    def _run(self, goal, replies, runs):
        prompts = []

        def fake_ask(tag, prompt, max_tokens, stream=True, temperature=0.2, stop=None):
            prompts.append((tag, prompt))
            return next(replies)

        state = new_state(goal)
        with mock.patch.object(direct, "_ask", fake_ask), mock.patch.object(direct, "run_python", lambda code: next(runs)):
            direct.handle(state)
        return state, prompts

    @staticmethod
    def _ran(passed, stdout="", stderr=""):
        return {"passed": passed, "stdout": stdout, "stderr": stderr, "isolation": "docker", "isolation_detail": "Docker", "seconds": 0.5, "container_id": "abc123def456"}

    def test_chat_answer_records_the_route(self):
        state, prompts = self._run("What is 2+2?", iter(["Four."]), iter([]))
        self.assertEqual(state["observations"][-1]["response"], "Four.")
        route = state["observations"][0]["route"]
        self.assertEqual((route["task_type"], route["model"]), ("general", "llama3.2:3b"))
        self.assertEqual(state["current_step"], len(state["plan"]))
        self.assertEqual(prompts[0][0], "llama3.2:3b")

    def test_code_passes_first_time(self):
        replies = iter(["Adds.\n```python\nprint(1)\nassert 1 == 1\nprint('All checks passed')\n```"])
        state, prompts = self._run("simple code for python", replies, iter([self._ran(True, "1\nAll checks passed\n")]))
        final = state["observations"][-1]
        self.assertTrue(final["result"]["verified"])
        self.assertIn("**Verified:**", final["response"])
        self.assertEqual(prompts[0][0], "qwen2.5-coder:1.5b")
        self.assertEqual(state["observations"][0]["route"]["task_type"], "coder")
        self.assertEqual([o["tool_used"] for o in state["observations"] if o["tool_used"]], ["route_task", "run_code_sandbox"])

    def test_failure_is_fed_back_and_fixed(self):
        replies = iter(["Adds.\n```python\nprint(x)\n```", "It was undefined.\n```python\nx = 1\nprint(x)\n```"])
        runs = iter([self._ran(False, "", "NameError: name 'x' is not defined"), self._ran(True, "1\n")])
        state, prompts = self._run("write a python program that prints a number", replies, runs)
        self.assertIn("NameError", prompts[1][1])  # the second prompt carries the real error
        final = state["observations"][-1]
        self.assertTrue(final["result"]["verified"])
        self.assertEqual(final["result"]["attempts"], 2)
        self.assertIn("x = 1", final["response"])

    def test_unchanged_repair_escalates_to_the_larger_model(self):
        code = "```python\nprint(x)\n```"
        replies = iter([f"A.\n{code}", f"Same.\n{code}", "Fixed.\n```python\nx = 2\nprint(x)\n```"])
        runs = iter([self._ran(False, "", "NameError"), self._ran(True, "2\n")])
        state, prompts = self._run("write a python program", replies, runs)
        self.assertEqual([tag for tag, _ in prompts], ["qwen2.5-coder:1.5b", "qwen2.5-coder:1.5b", "llama3.2:3b"])
        self.assertTrue(state["observations"][-1]["result"]["verified"])

    def test_wrong_assert_values_run_without_checks_and_say_so(self):
        bad = "Adds.\n```python\nprint(1 + 1)\nassert 1 + 1 == 3\nprint('All checks passed')\n```"
        replies = iter([bad, bad, bad, bad])
        assertion = self._ran(False, "2\n", "AssertionError")
        runs = iter([assertion, self._ran(True, "2\n")])  # the repairs change nothing, so the next run is the relaxed one
        state, _ = self._run("write a python program that adds", replies, runs)
        final = state["observations"][-1]
        self.assertFalse(final["result"]["verified"])
        self.assertTrue(final["result"]["ran"] and final["result"]["checks_removed"])
        self.assertIn("not fully verified", final["response"])
        self.assertNotIn("assert", final["response"].split("```python", 1)[1].split("```", 1)[0])

    def test_prose_only_reply_is_returned_as_the_answer(self):
        state, _ = self._run("simple code for python", iter(["Programs are fun.", "Still no code."]), iter([]))
        self.assertIn("no code", state["observations"][-1]["response"])  # the model's own words, not an error
        self.assertIsNone(state["observations"][-1]["error"])
        self.assertFalse(any(o["tool_used"] == "run_code_sandbox" for o in state["observations"]))  # nothing to run

    def test_other_language_is_not_run(self):
        with mock.patch.object(direct, "run_python", side_effect=AssertionError("must not run")):
            state, _ = self._run("write a javascript function to add", iter(["```js\nfunction a(){}\n```"]), iter([]))
        self.assertIn("Not run", state["observations"][-1]["response"])

    def test_model_failure_becomes_a_visible_error(self):
        def boom(*args, **kwargs):
            raise RuntimeError("Ollama is down")

        state = new_state("What is 2+2?")
        with mock.patch.object(direct, "_ask", boom):
            direct.handle(state)
        self.assertEqual(state["observations"][-1]["error"], "Ollama is down")


class CodeRunnerTests(unittest.TestCase):
    def test_local_run_passes(self):
        result = code_runner._run_local("print('hello')")
        self.assertTrue(result["passed"])
        self.assertIn("hello", result["stdout"])

    def test_local_run_reports_errors(self):
        result = code_runner._run_local("raise ValueError('nope')")
        self.assertFalse(result["passed"])
        self.assertIn("ValueError", result["stderr"])

    def test_local_run_has_no_network(self):
        code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 443), timeout=2)\n    print('CONNECTED')\nexcept OSError as e:\n    print('blocked', e)\n"
        out = code_runner._run_local(code)
        self.assertIn("blocked", out["stdout"])
        self.assertNotIn("CONNECTED", out["stdout"])

    def test_local_run_stops_a_runaway_program(self):
        with mock.patch.object(code_runner, "LOCAL_TIMEOUT_SECONDS", 2):
            result = code_runner._run_local("while True:\n    pass")
        self.assertFalse(result["passed"])
        self.assertIn("did not finish", result["stderr"])

    def test_local_run_has_no_input(self):
        result = code_runner._run_local("input('x')")
        self.assertFalse(result["passed"])
        self.assertIn("EOFError", result["stderr"])

    def test_local_run_cleans_up_and_hides_secrets(self):
        os.environ["KRYPTO_TEST_SECRET"] = "hunter2"
        try:
            result = code_runner._run_local("import os\nprint(os.environ.get('KRYPTO_TEST_SECRET'))")
        finally:
            del os.environ["KRYPTO_TEST_SECRET"]
        self.assertIn("None", result["stdout"])

    def test_falls_back_when_docker_is_missing(self):
        with mock.patch.object(code_runner, "docker_available", return_value=False):
            result = code_runner.run_python("print(6 * 7)")
        self.assertTrue(result["passed"])
        self.assertEqual(result["isolation"], "process")
        self.assertIn("42", result["stdout"])

    def test_falls_back_when_the_docker_daemon_is_down(self):
        broken = {"passed": False, "stdout": "", "stderr": "error during connect: cannot connect to the docker daemon", "container_id": None}
        with mock.patch.object(code_runner, "docker_available", return_value=True), \
                mock.patch.object(code_runner, "run_code_sandbox", return_value=broken):
            result = code_runner.run_python("print('ok')")
        self.assertEqual(result["isolation"], "process")
        self.assertTrue(result["passed"])

    def test_a_real_failure_in_docker_is_not_retried_locally(self):
        failed = {"passed": False, "stdout": "", "stderr": "NameError: x", "container_id": "abc"}
        with mock.patch.object(code_runner, "docker_available", return_value=True), \
                mock.patch.object(code_runner, "run_code_sandbox", return_value=failed):
            result = code_runner.run_python("print(x)")
        self.assertEqual(result["isolation"], "docker")
        self.assertFalse(result["passed"])

    def test_long_output_is_shortened(self):
        result = code_runner._clip("x" * 10000)
        self.assertLess(len(result), 6100)


class VisionContextTests(unittest.TestCase):
    def test_when_the_words_are_not_enough(self):
        self.assertTrue(vision_context.wants_picture_understanding("what is this?", ""))
        self.assertTrue(vision_context.wants_picture_understanding("describe the chart", "Figure 1 Quarterly Output"))
        self.assertFalse(vision_context.wants_picture_understanding("what is the code?", "A" * 300))
        self.assertFalse(vision_context.wants_picture_understanding("read the total", "Invoice total 4471 due Friday for services rendered"))

    def test_image_facts_without_a_model(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blue.png"
            Image.new("RGB", (300, 200), (10, 20, 200)).save(path)
            facts = vision_context.image_facts(str(path))
        self.assertIn("300 x 200", facts)
        self.assertIn("landscape", facts)
        self.assertIn("blue", facts)

    def test_image_facts_for_a_bad_file_is_empty(self):
        self.assertEqual(vision_context.image_facts("does-not-exist.png"), "")

    def test_describe_without_a_vision_model_is_honest(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"}), \
                mock.patch.object(vision_context, "specialist_model", return_value=None):
            path = Path(folder) / "x.png"
            Image.new("RGB", (50, 50), "white").save(path)
            result = vision_context.describe(str(path), "what is this?")
        self.assertFalse(result["used_model"])
        self.assertIn("No vision model is installed", result["text"])
        self.assertEqual(result["route"]["task_type"], "vision")

    def test_describe_uses_the_vision_model_and_shrinks_the_picture(self):
        seen = {}

        def fake(model, prompt, images, max_tokens=None):
            seen["size"] = Image.open(images[0]).size
            seen["model"], seen["prompt"] = model, prompt
            return "A bar chart with four blue bars."

        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"}), \
                mock.patch.object(vision_context, "specialist_model", return_value="moondream"), \
                mock.patch("backend.app.llm.chat_with_images", fake):
            path = Path(folder) / "big.png"
            Image.new("RGB", (4000, 3000), "white").save(path)
            result = vision_context.describe(str(path), "describe the chart")
        self.assertLessEqual(max(seen["size"]), vision_context.MAX_SIDE)
        self.assertEqual(seen["model"], "moondream")
        self.assertTrue(result["used_model"])
        self.assertIn("bar chart", result["text"])

    def test_plain_description_uses_the_vision_models_words(self):
        obs = [{"tool_used": "describe_image", "step": "[SYSTEM] look at the picture a.png", "result": "A red circle.", "model_tag": "moondream"}]
        reply = vision_context.plain_description_reply(obs, "Describe this picture")
        self.assertIn("A red circle.", reply)
        self.assertIsNone(vision_context.plain_description_reply(obs, "Describe this picture and list the colours"))
        self.assertIsNone(vision_context.plain_description_reply(obs, "What is the total?"))
        self.assertIsNone(vision_context.plain_description_reply([], "Describe this picture"))


class LanGuardTests(unittest.TestCase):
    def test_private_addresses_only_with_lan_allowed(self):
        with mock.patch.dict(os.environ, {"KRYPTO_ALLOW_LAN": ""}):
            self.assertFalse(netguard._is_local_host("192.168.1.5"))
            self.assertTrue(netguard._is_local_host("127.0.0.1"))
        with mock.patch.dict(os.environ, {"KRYPTO_ALLOW_LAN": "1"}):
            for host in ("192.168.1.5", "10.0.0.2", "172.16.4.4", "169.254.10.10", "127.0.0.1", "localhost"):
                self.assertTrue(netguard._is_local_host(host), host)
            for host in ("8.8.8.8", "1.1.1.1", "142.250.1.1", "example.com", "172.32.0.1"):
                self.assertFalse(netguard._is_local_host(host), host)

    def test_loopback_is_not_counted_as_a_lan_connection(self):
        self.assertTrue(netguard._is_loopback("127.0.0.1"))
        self.assertTrue(netguard._is_loopback("localhost"))
        self.assertFalse(netguard._is_loopback("192.168.1.5"))

    def test_self_test_refusals_are_logged_not_counted(self):
        before = netguard.stats()["blocked"]
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(netguard, "AUDIT_LOG", Path(folder) / "a.log"):
            with netguard.self_test_scope():
                netguard._record("1.1.1.1", 443)
            self.assertEqual(netguard.stats()["blocked"], before)
            self.assertIn("self_test_refused", netguard.audit_tail(5)[-1])
            netguard._record("8.8.8.8", 53)  # outside the scope it counts again
        self.assertEqual(netguard.stats()["blocked"], before + 1)
        netguard._blocked_total -= 1  # leave the shared counter as it was

    def test_audit_log_lines_are_json(self):
        import json

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(netguard, "AUDIT_LOG", Path(folder) / "a.log"):
            netguard.audit({"event": "x", "n": 1})
            line = netguard.audit_tail(1)[0]
        self.assertEqual(json.loads(line)["event"], "x")

    def test_audit_tail_of_a_missing_file_is_empty(self):
        with mock.patch.object(netguard, "AUDIT_LOG", Path(tempfile.gettempdir()) / "krypto-no-such-audit.log"):
            self.assertEqual(netguard.audit_tail(3), [])


class LinkInfoTests(unittest.TestCase):
    def _fake(self, names):
        import socket

        class St:
            def __init__(self, up, speed):
                self.isup, self.speed = up, speed

        class Addr:
            def __init__(self, ip):
                self.family, self.address = socket.AF_INET, ip

        stats = {n: St(up, speed) for n, (up, speed, ip) in names.items()}
        addrs = {n: [Addr(ip)] for n, (up, speed, ip) in names.items() if ip}
        return stats, addrs

    def test_ethernet_and_wifi_and_virtual_adapters(self):
        stats, addrs = self._fake({
            "Ethernet": (True, 1000, "192.168.50.2"), "Wi-Fi": (True, 150, "172.22.1.5"),
            "vEthernet (WSL)": (True, 10000, "172.30.0.1"), "Loopback Pseudo-Interface 1": (True, 0, "127.0.0.1"),
            "Local Area Connection* 9": (False, 0, "169.254.1.1"),
        })
        import psutil

        with mock.patch.object(psutil, "net_if_stats", return_value=stats), mock.patch.object(psutil, "net_if_addrs", return_value=addrs), \
                mock.patch.dict(os.environ, {"KRYPTO_PORT": "8010"}):
            info = network.link_info()
        self.assertEqual(info["kind"], "both")
        self.assertEqual([a["name"] for a in info["adapters"]], ["Ethernet", "Wi-Fi"])
        self.assertEqual(info["lan_urls"][0], "http://192.168.50.2:8010/ui/")  # ethernet listed first

    def test_no_cable_no_wifi(self):
        stats, addrs = self._fake({"Ethernet": (False, 0, None)})
        import psutil

        with mock.patch.object(psutil, "net_if_stats", return_value=stats), mock.patch.object(psutil, "net_if_addrs", return_value=addrs):
            info = network.link_info()
        self.assertEqual(info["kind"], "none")
        self.assertEqual(info["lan_urls"], [])


class SovereigntyTests(unittest.TestCase):
    def test_scope_of(self):
        self.assertEqual(sovereignty.scope_of("127.0.0.1"), "this computer")
        self.assertEqual(sovereignty.scope_of("::1"), "this computer")
        self.assertEqual(sovereignty.scope_of("192.168.0.9"), "local network")
        self.assertEqual(sovereignty.scope_of("169.254.4.4"), "local network")
        self.assertEqual(sovereignty.scope_of("8.8.8.8"), "internet")
        self.assertEqual(sovereignty.scope_of("not-an-ip"), "internet")  # unknown is treated as the worst case

    def test_snapshot_shape_and_verdict(self):
        snap = sovereignty.snapshot()
        for key in ("verdict", "headline", "connections_now", "listening", "counts_now", "ever_external", "processes",
                    "cloud_libraries_installed", "audit_log", "how_to_check"):
            self.assertIn(key, snap)
        self.assertIn(snap["verdict"], ("local-only", "lan-only", "helpers", "internet"))

    @staticmethod
    def _row(role, party, remote, scope, direction="outbound", state="ESTABLISHED", process="x.exe"):
        return {"role": role, "party": party, "process": process, "pid": 1, "local": "10.0.0.2:5555", "remote": remote,
                "state": state, "direction": direction, "scope": scope, "kind": "connection"}

    def _snapshot_with(self, rows):
        sovereignty._seen.clear()
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(netguard, "AUDIT_LOG", Path(folder) / "a.log"), \
                mock.patch.object(sovereignty, "_connections", return_value=rows), \
                mock.patch.object(sovereignty, "_our_processes", return_value={}):
            snap = sovereignty.snapshot()
            logged = netguard.audit_tail(5)
        sovereignty._seen.clear()
        return snap, logged

    def test_a_core_component_reaching_the_internet_is_a_failure(self):
        snap, logged = self._snapshot_with([self._row("Ollama model server", "core", "34.1.2.3:443", "internet")])
        self.assertEqual(snap["verdict"], "internet")
        self.assertEqual((snap["ever_external"], snap["ever_external_core"]), (1, 1))
        self.assertTrue(any("os_connection" in line and "Ollama model server" in line for line in logged))

    def test_a_helper_reaching_the_internet_is_named_and_advised(self):
        snap, _ = self._snapshot_with([self._row("Docker Desktop", "helper", "3.223.72.87:443", "internet", process="com.docker.backend.exe")])
        self.assertEqual(snap["verdict"], "helpers")
        self.assertEqual(snap["ever_external_core"], 0)
        self.assertIn("Docker Desktop", snap["headline"])
        self.assertEqual(snap["helper_advice"][0]["role"], "Docker Desktop")
        self.assertIn("seal_network", snap["helper_advice"][0]["advice"])

    def test_a_browser_on_the_local_network_is_not_a_leak(self):
        snap, _ = self._snapshot_with([self._row("Krypto server", "core", "192.168.50.2:53211", "local network", direction="inbound")])
        self.assertEqual(snap["verdict"], "lan-only")
        self.assertEqual(snap["ever_external"], 0)

    def test_an_incoming_connection_from_the_internet_is_not_counted_as_an_outgoing_call(self):
        snap, _ = self._snapshot_with([self._row("Krypto server", "core", "8.8.4.4:5000", "internet", direction="inbound")])
        self.assertEqual(snap["ever_external"], 0)

    def test_only_local_connections_is_the_clean_verdict(self):
        snap, _ = self._snapshot_with([self._row("MongoDB", "core", "127.0.0.1:50000", "this computer")])
        self.assertEqual(snap["verdict"], "local-only")

    def test_an_attempt_that_never_connected_is_marked_as_such(self):
        rows = [self._row("Docker Desktop", "helper", "3.223.72.87:443", "internet", state="SYN_SENT")]
        sovereignty._seen.clear()
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(netguard, "AUDIT_LOG", Path(folder) / "a.log"), \
                mock.patch.object(sovereignty, "_connections", return_value=rows), mock.patch.object(sovereignty, "_our_processes", return_value={}):
            snap = sovereignty.snapshot()
        sovereignty._seen.clear()
        self.assertFalse(snap["ever_seen"][0]["connected"])

    def test_role_grouping(self):
        self.assertEqual(sovereignty._role("ollama app.exe", ""), ("Ollama tray app (auto-updater)", "helper"))
        self.assertEqual(sovereignty._role("ollama.exe", ""), ("Ollama model server", "core"))
        self.assertEqual(sovereignty._role("com.docker.backend.exe", ""), ("Docker Desktop", "helper"))
        self.assertEqual(sovereignty._role("mongod.exe", ""), ("MongoDB", "core"))
        self.assertEqual(sovereignty._role("python.exe", "-m uvicorn backend.app.main:app"), ("Krypto server", "core"))
        self.assertIsNone(sovereignty._role("python.exe", "-m http.server"))
        self.assertIsNone(sovereignty._role("msedge.exe", ""))

    def test_self_test_refuses_to_run_outside_strict_mode(self):
        with mock.patch.object(netguard, "enabled", return_value=False):
            result = sovereignty.self_test()
        self.assertFalse(result["ran"])
        self.assertEqual(result["attempts"], [])

    def test_self_test_reports_refusals_in_strict_mode(self):
        import socket

        def refuse(*args, **kwargs):
            raise OSError(101, "Blocked by Krypto offline mode")

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(netguard, "AUDIT_LOG", Path(folder) / "a.log"), \
                mock.patch.object(netguard, "enabled", return_value=True), \
                mock.patch.object(socket, "create_connection", refuse), mock.patch.object(socket, "getaddrinfo", refuse):
            result = sovereignty.self_test()
        self.assertTrue(result["ran"] and result["all_refused"])
        self.assertEqual(len(result["attempts"]), 3)

    def test_self_test_flags_a_connection_that_got_through(self):
        import socket

        class Open:
            def close(self):
                pass

        with mock.patch.object(netguard, "enabled", return_value=True), \
                mock.patch.object(socket, "create_connection", lambda *a, **k: Open()), \
                mock.patch.object(socket, "getaddrinfo", lambda *a, **k: []):
            result = sovereignty.self_test()
        self.assertFalse(result["all_refused"])

    def test_no_cloud_libraries_in_this_environment(self):
        self.assertEqual(sovereignty.cloud_packages(), [])


class VerbatimTextTests(unittest.TestCase):
    OCR = [{"forced": True, "tool_used": "extract_text", "result": "KRYPTO 4471", "error": None}]

    def test_reads_the_text_exactly(self):
        for goal in ("What text is written in the image? Reply with just the text.", "read the text", "what is written here",
                     "Extract all the text from this scan", "what does it say"):
            reply = vision_context.verbatim_text_reply(self.OCR, goal)
            self.assertIn("> KRYPTO 4471", reply, goal)

    def test_other_questions_are_left_to_the_model(self):
        for goal in ("What is the total?", "summarize the text", "What does it say about the budget?", "Read the budget section",
                     "how many words are written", "x" * 300):
            self.assertIsNone(vision_context.verbatim_text_reply(self.OCR, goal), goal)

    def test_long_or_multiple_documents_are_left_to_the_model(self):
        long_doc = [{"forced": True, "tool_used": "extract_text", "result": "word " * 600, "error": None}]
        self.assertIsNone(vision_context.verbatim_text_reply(long_doc, "read the text"))
        self.assertIsNone(vision_context.verbatim_text_reply(self.OCR * 2, "read the text"))

    def test_the_picture_description_is_not_part_of_the_quoted_text(self):
        doc = [{"forced": True, "tool_used": "extract_text", "result": "KRYPTO 4471\n\n[What the picture shows]\nA sign.", "error": None}]
        reply = vision_context.verbatim_text_reply(doc, "read the text")
        self.assertNotIn("A sign", reply)


class SampleReportTests(unittest.TestCase):
    """The composer's default source is the sample inspection report; unrelated requests must not get it."""

    def setUp(self):
        from backend.app.routes import tasks

        self.irrelevant = tasks.sample_is_irrelevant
        self._env = mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_unrelated_requests_drop_the_sample(self):
        for goal in ("simple code for python", "What is the capital of Australia and why is it not Sydney?", "tell me a joke",
                     "give me a program for fibonacci series", "Explain photosynthesis"):
            self.assertTrue(self.irrelevant(goal), goal)

    def test_report_requests_keep_the_sample(self):
        for goal in ("Read the inspection report, extract wall-thickness readings and generate an approval note.",
                     "summarize the report", "write a python function to compute corrosion rate", "Which pipe needs replacing?",
                     "what does this document say"):
            self.assertFalse(self.irrelevant(goal), goal)

    def test_locked_model_mode_is_unchanged(self):
        os.environ.pop("KRYPTO_DEMO_MODEL", None)
        self.assertFalse(self.irrelevant("simple code for python"))


class UnreadableFileTests(unittest.TestCase):
    def test_friendly_message_names_the_file_and_kind(self):
        from backend.app.agent import graph

        text = graph._friendly_read_error("report.pdf", "C:/x/abc.pdf", RuntimeError("Failed to open file 'C:/x/abc.pdf' as type pdf."))
        self.assertIn("report.pdf", text)
        self.assertIn("PDF", text)
        self.assertIn("damaged", text)

    def test_nothing_readable_only_when_every_file_failed(self):
        from backend.app.agent import graph

        def read(error):
            return {"forced": True, "tool_used": "extract_text", "error": error, "result": None if error else "text"}

        state = new_state("q", ["a.pdf", "b.pdf"])
        state["observations"] = [read("bad"), read("bad")]
        self.assertTrue(graph._nothing_readable(state))
        state["observations"] = [read("bad"), read(None)]
        self.assertFalse(graph._nothing_readable(state))
        self.assertFalse(graph._nothing_readable(new_state("q")))  # no files attached

    def test_run_agent_fails_fast_on_a_damaged_file(self):
        from backend.app.agent import graph

        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"KRYPTO_DEMO_MODEL": "llama3.2:3b"}):
            path = Path(folder) / "broken.pdf"
            path.write_bytes(b"%PDF-1.4\n" + os.urandom(500))
            with mock.patch.object(graph, "_call_ollama", side_effect=AssertionError("the model must not be asked")):
                state = graph.run_agent("Summarize", [str(path)])
        self.assertEqual(state["plan"], ["Read the attached file"])
        self.assertEqual(state["current_step"], 0)
        self.assertIn("Could not read", state["observations"][-1]["error"])


if __name__ == "__main__":
    unittest.main()
