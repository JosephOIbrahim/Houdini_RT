#!/usr/bin/env python3
"""Tests for the sidecar turn loop skeleton.

Run from the extension root with either interpreter::

    py -3.12 -m pytest tests/test_turn_loop.py -q
    python -m pytest tests/test_turn_loop.py -q

Standard library only. No HTTP, no Houdini, no model call: every entry point
here is a local fake. Timing tests use short real deadlines (50-250 ms) and a
polling helper, so the suite stays under a second on a quiet machine.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

# The extension root, so ``rt_runtime.sidecar.turn_loop`` resolves whether the
# runner is pytest (conftest.py does this too) or this file directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from rt_runtime.sidecar import turn_loop  # noqa: E402
from rt_runtime.sidecar.turn_loop import (  # noqa: E402
    MAX_PROMPT_CHARS,
    MAX_WAIT_MS,
    STATE_CANCELLED,
    STATE_CANCELLING,
    STATE_COMPLETE,
    STATE_ERROR,
    STATE_RUNNING,
    STATE_TIMEOUT,
    TurnBadRequest,
    TurnBusy,
    TurnDuplicate,
    TurnLoop,
    TurnNotRunning,
    TurnShuttingDown,
    TurnUnknownRequest,
    call_entry_point,
    error_response,
    json_safe_result,
    parse_turn_request,
)


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class FakeResult:
    """Stand-in for ``synapse.cognitive.agent_loop.AgentTurnResult``."""

    def __init__(
        self,
        status: str = "complete",
        iterations: int = 1,
        tool_calls_made: int = 0,
        error: str = "",
        messages: list | None = None,
        usage: list | None = None,
        text: str | None = None,
    ) -> None:
        self.status = status
        self.iterations = iterations
        self.tool_calls_made = tool_calls_made
        self.error = error
        self.messages = messages if messages is not None else []
        self.usage = usage if usage is not None else []
        if text is not None:
            self.text = text


def assistant_text(text: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.005):
    """Return the first truthy value, or fail the test after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within %.2fs" % timeout)


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------


class ParseRequestTests(unittest.TestCase):
    def test_full_body_parses(self) -> None:
        request = parse_turn_request(
            b'{"prompt": "hello", "max_tokens": 32, "request_id": "r-1", "wait_ms": 500}'
        )
        self.assertEqual(request.prompt, "hello")
        self.assertEqual(request.max_tokens, 32)
        self.assertEqual(request.request_id, "r-1")
        self.assertEqual(request.wait_ms, 500)

    def test_defaults(self) -> None:
        request = parse_turn_request({"prompt": "hi"})
        self.assertIsNone(request.max_tokens)
        self.assertIsNone(request.request_id)
        self.assertEqual(request.wait_ms, 0)

    def test_invalid_cases_are_refused(self) -> None:
        cases = [
            b"{",  # not JSON
            b"[]",  # not an object
            b'{"prompt": ""}',
            b'{"prompt": "   "}',
            b'{"prompt": 3}',
            b'{"prompt": "ok", "propmt": "typo"}',  # unknown field
            b'{"prompt": "ok", "max_tokens": 0}',
            b'{"prompt": "ok", "max_tokens": 999999}',
            b'{"prompt": "ok", "max_tokens": true}',
            b'{"prompt": "ok", "max_tokens": 1.5}',
            b'{"prompt": "ok", "request_id": "has space"}',
            b'{"prompt": "ok", "request_id": ""}',
            b'{"prompt": "ok", "wait_ms": -1}',
            b'{"prompt": "ok", "wait_ms": %d}' % (MAX_WAIT_MS + 1),
            b'{"prompt": "ok", "wait_ms": 1.5}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(TurnBadRequest):
                    parse_turn_request(raw)

    def test_prompt_cap(self) -> None:
        parse_turn_request({"prompt": "x" * MAX_PROMPT_CHARS})
        with self.assertRaises(TurnBadRequest):
            parse_turn_request({"prompt": "x" * (MAX_PROMPT_CHARS + 1)})

    def test_body_size_cap(self) -> None:
        with self.assertRaises(TurnBadRequest):
            parse_turn_request(b" " * (turn_loop.MAX_TURN_BODY_BYTES + 1))

    def test_non_string_key_is_a_bad_field_not_a_crash(self) -> None:
        with self.assertRaises(TurnBadRequest):
            parse_turn_request({1: "x", "prompt": "ok"})

    def test_error_names_field_not_value(self) -> None:
        secret = "SECRET-PROMPT-9f3"
        with self.assertRaises(TurnBadRequest) as caught:
            parse_turn_request({"prompt": secret, "max_tokens": 0})
        self.assertNotIn(secret, str(caught.exception))


# ---------------------------------------------------------------------------
# Result projection
# ---------------------------------------------------------------------------


class ResultProjectionTests(unittest.TestCase):
    def test_success_shape(self) -> None:
        result = json_safe_result(
            FakeResult(
                status="complete",
                iterations=2,
                tool_calls_made=1,
                messages=[assistant_text("the answer")],
                usage=[{"input_tokens": 3, "output_tokens": 4}],
            )
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["tool_calls_made"], 1)
        self.assertEqual(result["error"], "")
        self.assertEqual(result["text"], "the answer")
        self.assertFalse(result["text_truncated"])
        self.assertEqual(result["usage"], [{"input_tokens": 3, "output_tokens": 4}])
        for key in ("status", "iterations", "tool_calls_made", "error", "text"):
            self.assertIn(key, result)
        json.dumps(result)

    def test_explicit_text_wins(self) -> None:
        result = json_safe_result(
            FakeResult(text="direct", messages=[assistant_text("from messages")])
        )
        self.assertEqual(result["text"], "direct")

    def test_non_result_is_bad_result(self) -> None:
        for value in (None, "a string", 3):
            with self.subTest(value=value):
                result = json_safe_result(value)
                self.assertEqual(result["status"], "bad_result")
                self.assertTrue(result["error"])

    def test_unknown_status_is_named(self) -> None:
        result = json_safe_result(FakeResult(status="something-new", error="x"))
        self.assertEqual(result["status"], "unknown_status")
        self.assertIn("something-new", result["error"])

    def test_text_and_error_are_truncated(self) -> None:
        huge = "y" * (turn_loop.MAX_RESULT_TEXT_CHARS + 10)
        result = json_safe_result(
            FakeResult(messages=[assistant_text(huge)], error="e" * 5000)
        )
        self.assertTrue(result["text_truncated"])
        self.assertEqual(len(result["text"]), turn_loop.MAX_RESULT_TEXT_CHARS)
        self.assertEqual(len(result["error"]), turn_loop.MAX_ERROR_CHARS)

    def test_usage_is_sanitised(self) -> None:
        result = json_safe_result(
            FakeResult(
                usage=[
                    {"input_tokens": 2, "junk": "drop me"},
                    {"input_tokens": "not an int"},
                    "not a mapping",
                ]
            )
        )
        self.assertEqual(result["usage"], [{"input_tokens": 2}])


# ---------------------------------------------------------------------------
# Entry-point call shapes
# ---------------------------------------------------------------------------


class CallShapeTests(unittest.TestCase):
    def test_keyword_only_shape(self) -> None:
        seen = {}

        def entry(prompt, *, cancel_event=None, max_tokens=None):
            seen.update(
                prompt=prompt, cancel_event=cancel_event, max_tokens=max_tokens
            )
            return FakeResult()

        event = threading.Event()
        call_entry_point(entry, "p", event, 64)
        self.assertEqual(seen["prompt"], "p")
        self.assertIs(seen["cancel_event"], event)
        self.assertEqual(seen["max_tokens"], 64)

    def test_positional_cancel_shape(self) -> None:
        seen = {}

        def entry(prompt, cancel_event):
            seen.update(prompt=prompt, cancel_event=cancel_event)
            return FakeResult()

        event = threading.Event()
        call_entry_point(entry, "p", event)
        self.assertEqual(seen["prompt"], "p")
        self.assertIs(seen["cancel_event"], event)

    def test_var_keyword_shape(self) -> None:
        seen = {}

        def entry(prompt, **kwargs):
            seen.update(kwargs)
            return FakeResult()

        event = threading.Event()
        call_entry_point(entry, "p", event, 16)
        self.assertIs(seen["cancel_event"], event)
        self.assertEqual(seen["max_tokens"], 16)

    def test_prompt_only_shape(self) -> None:
        seen = []

        def entry(prompt):
            seen.append(prompt)
            return FakeResult()

        call_entry_point(entry, "p", threading.Event(), 16)
        self.assertEqual(seen, ["p"])

    def test_unsupported_shapes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            TurnLoop(lambda: None)  # no prompt parameter
        with self.assertRaises(ValueError):
            TurnLoop(lambda prompt, client, dispatcher: None)  # host must bind
        with self.assertRaises(ValueError):
            TurnLoop(lambda prompt, *, client: None)  # required keyword-only
        with self.assertRaises(TypeError):
            TurnLoop("not callable")


# ---------------------------------------------------------------------------
# Admission, success, error mapping
# ---------------------------------------------------------------------------


class TurnLoopTests(unittest.TestCase):
    def test_success_round_trip(self) -> None:
        prompt = "SECRET-PROMPT-9f3"
        loop = TurnLoop(
            lambda prompt, *, cancel_event=None, max_tokens=None: FakeResult(
                status="complete",
                iterations=2,
                tool_calls_made=1,
                messages=[assistant_text("SECRET-ANSWER")],
                usage=[{"input_tokens": 3, "output_tokens": 4}],
            ),
            timeout_s=2.0,
        )
        admitted = loop.start(prompt)
        self.assertIn(admitted["state"], (STATE_RUNNING, STATE_COMPLETE))
        final = loop.wait(admitted["request_id"], 2.0)
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["state"], STATE_COMPLETE)
        self.assertTrue(final["ok"])
        self.assertIsNone(final["error_kind"])
        self.assertEqual(final["result"]["status"], "complete")
        self.assertEqual(final["result"]["text"], "SECRET-ANSWER")
        self.assertEqual(final["result"]["iterations"], 2)
        self.assertEqual(final["result"]["tool_calls_made"], 1)
        json.dumps(final, sort_keys=True)

        # The prompt and the answer never reach the status surface.
        blob = json.dumps(loop.status_fields(), sort_keys=True)
        self.assertNotIn("SECRET", blob)
        self.assertIn("SECRET-ANSWER", json.dumps(final))

    def test_api_error_mapping(self) -> None:
        loop = TurnLoop(
            lambda prompt, *, cancel_event=None: FakeResult(
                status="api_error", error="rate limited"
            ),
            timeout_s=2.0,
        )
        final = loop.wait(loop.start("p")["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_ERROR)
        self.assertEqual(final["error_kind"], "api_error")
        self.assertEqual(final["result"]["status"], "api_error")
        self.assertEqual(final["result"]["error"], "rate limited")
        self.assertFalse(final["ok"])

    def test_unknown_stop_reason_mapping(self) -> None:
        loop = TurnLoop(
            lambda prompt, **kwargs: FakeResult(
                status="unknown_stop_reason",
                error="Unhandled stop_reason: 'max_tokens'",
            ),
            timeout_s=2.0,
        )
        final = loop.wait(loop.start("p")["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_ERROR)
        self.assertEqual(final["error_kind"], "unknown_stop_reason")

    def test_max_iterations_is_a_failure_not_a_completion(self) -> None:
        loop = TurnLoop(
            lambda prompt, **kwargs: FakeResult(status="max_iterations"),
            timeout_s=2.0,
        )
        final = loop.wait(loop.start("p")["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_ERROR)
        self.assertEqual(final["error_kind"], "max_iterations")
        self.assertFalse(final["ok"])

    def test_entry_point_exception_is_contained(self) -> None:
        def explode(prompt, **kwargs):
            raise RuntimeError("payload bug")

        loop = TurnLoop(explode, timeout_s=2.0)
        final = loop.wait(loop.start("p")["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_ERROR)
        self.assertEqual(final["error_kind"], "entry_point_error")
        self.assertIn("payload bug", final["result"]["error"])

    def test_unknown_request_id_polls_to_none(self) -> None:
        loop = TurnLoop(lambda prompt, **kwargs: FakeResult())
        self.assertIsNone(loop.poll("never-seen"))
        self.assertIsNone(loop.wait("never-seen", 0.0))
        with self.assertRaises(TurnUnknownRequest):
            loop.cancel("never-seen")


# ---------------------------------------------------------------------------
# Concurrency and idempotency
# ---------------------------------------------------------------------------


class ConcurrencyTests(unittest.TestCase):
    def test_second_turn_is_rejected_while_one_runs(self) -> None:
        release = threading.Event()

        def blocking(prompt, *, cancel_event=None):
            release.wait(3.0)
            return FakeResult()

        loop = TurnLoop(blocking, timeout_s=5.0)
        first = loop.start("a")
        with self.assertRaises(TurnBusy) as caught:
            loop.start("b")
        self.assertEqual(caught.exception.code, "turn_in_progress")
        self.assertEqual(
            caught.exception.detail["active_request_id"], first["request_id"]
        )
        status, payload = error_response(caught.exception)
        self.assertEqual(status, 429)
        self.assertEqual(payload["error"], "turn_in_progress")

        release.set()
        final = loop.wait(first["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_COMPLETE)

        second = loop.start("c")
        self.assertEqual(loop.wait(second["request_id"], 2.0)["state"], STATE_COMPLETE)

    def test_duplicate_request_id(self) -> None:
        release = threading.Event()

        def blocking(prompt, **kwargs):
            release.wait(3.0)
            return FakeResult()

        loop = TurnLoop(blocking, timeout_s=5.0)
        loop.start("a", request_id="fixed-id")
        with self.assertRaises(TurnDuplicate) as caught:
            loop.start("b", request_id="fixed-id")
        self.assertEqual(caught.exception.code, "duplicate_request_id")

        release.set()
        final = loop.wait("fixed-id", 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_COMPLETE)

        replay = loop.start("something else", request_id="fixed-id")
        self.assertEqual(replay["request_id"], "fixed-id")
        self.assertEqual(replay["state"], STATE_COMPLETE)
        self.assertEqual(loop.status_fields()["turns_total"], 1)
        self.assertEqual(loop.status_fields()["turns_replayed"], 1)


# ---------------------------------------------------------------------------
# Cancellation and timeouts
# ---------------------------------------------------------------------------


class CancellationTests(unittest.TestCase):
    def test_cancel_reaches_the_payload_event(self) -> None:
        started = threading.Event()

        def cooperative(prompt, *, cancel_event=None):
            started.set()
            for _ in range(400):
                if cancel_event is not None and cancel_event.is_set():
                    return FakeResult(status="cancelled", iterations=1)
                time.sleep(0.005)
            return FakeResult(status="complete")

        loop = TurnLoop(cooperative, timeout_s=5.0)
        admitted = loop.start("p")
        self.assertTrue(started.wait(1.0))

        first = loop.cancel(admitted["request_id"])
        self.assertEqual(first["state"], STATE_CANCELLING)
        self.assertTrue(first["cancel_requested"])

        final = loop.wait(admitted["request_id"], 2.0)
        assert final is not None
        self.assertEqual(final["state"], STATE_CANCELLED)
        self.assertFalse(final["ok"])
        self.assertEqual(final["result"]["status"], "cancelled")

        with self.assertRaises(TurnNotRunning) as caught:
            loop.cancel(admitted["request_id"])
        self.assertEqual(caught.exception.code, "turn_not_running")
        self.assertEqual(caught.exception.detail["state"], STATE_CANCELLED)

    def test_soft_timeout_on_a_hung_payload(self) -> None:
        release = threading.Event()

        def hung(prompt, *, cancel_event=None):
            release.wait(3.0)  # deliberately ignores the cancel event
            return FakeResult(status="complete")

        loop = TurnLoop(hung, timeout_s=0.05, timeout_grace_s=0.15)
        first = loop.start("a")
        wait_until(
            lambda: (loop.poll(first["request_id"]) or {}).get("state")
            == STATE_TIMEOUT,
            timeout=1.0,
        )
        polled = loop.poll(first["request_id"])
        assert polled is not None
        self.assertEqual(polled["error_kind"], "timeout")
        self.assertIsNone(polled["result"])
        self.assertFalse(polled["ok"])

        # The slot is held through the grace window, then quarantined.
        with self.assertRaises(TurnBusy):
            loop.start("b")
        time.sleep(0.25)
        second = loop.start("c")

        release.set()
        self.assertEqual(loop.wait(second["request_id"], 2.0)["state"], STATE_COMPLETE)

        # The quarantined worker's late result did not overwrite the timeout.
        late = loop.poll(first["request_id"])
        assert late is not None
        self.assertEqual(late["state"], STATE_TIMEOUT)
        self.assertGreaterEqual(loop.status_fields()["turns_quarantined"], 1)

    def test_cooperative_worker_still_reports_timeout(self) -> None:
        def cooperative(prompt, *, cancel_event=None):
            cancel_event.wait(3.0)
            return FakeResult(status="cancelled", iterations=0)

        loop = TurnLoop(cooperative, timeout_s=0.05, timeout_grace_s=1.0)
        admitted = loop.start("a")
        final = wait_until(
            lambda: (
                (loop.poll(admitted["request_id"]) or {}).get("state")
                == STATE_TIMEOUT
                and loop.poll(admitted["request_id"])
            ),
            timeout=1.0,
        )
        self.assertEqual(final["state"], STATE_TIMEOUT)
        self.assertEqual(final["error_kind"], "timeout")
        # Worker exited: the slot is free even though the grace window is open.
        # The timer publishes "timeout" a moment before the worker observes the
        # event and releases the slot, so poll for the release rather than
        # assuming the thread has already been scheduled on a loaded machine.
        wait_until(
            lambda: loop.status_fields()["turn_state"] == "idle", timeout=0.9
        )
        # ...and it was released by the worker, not by the hard-deadline
        # quarantine (which is what a wedged payload would need).
        self.assertEqual(loop.status_fields()["turns_quarantined"], 0)
        second = loop.start("b")
        loop.cancel(second["request_id"])
        self.assertEqual(loop.wait(second["request_id"], 2.0)["state"], STATE_CANCELLED)

    def test_shutdown_cancels_and_refuses_new_turns(self) -> None:
        def cooperative(prompt, *, cancel_event=None):
            cancel_event.wait(3.0)
            return FakeResult(status="cancelled")

        loop = TurnLoop(cooperative, timeout_s=5.0)
        admitted = loop.start("a")
        self.assertTrue(loop.shutdown(1.0))
        final = loop.poll(admitted["request_id"])
        assert final is not None
        self.assertEqual(final["state"], STATE_CANCELLED)
        with self.assertRaises(TurnShuttingDown):
            loop.start("b")

    def test_shutdown_does_not_wait_for_a_hung_worker(self) -> None:
        release = threading.Event()

        def hung(prompt, **kwargs):
            release.wait(3.0)
            return FakeResult()

        loop = TurnLoop(hung, timeout_s=5.0)
        loop.start("a")
        started = time.monotonic()
        self.assertFalse(loop.shutdown(0.1))
        self.assertLess(time.monotonic() - started, 1.0)
        release.set()

    def test_wait_is_clamped(self) -> None:
        release = threading.Event()

        def blocking(prompt, **kwargs):
            release.wait(3.0)
            return FakeResult()

        loop = TurnLoop(blocking, timeout_s=5.0)
        admitted = loop.start("a")
        started = time.monotonic()
        result = loop.wait(admitted["request_id"], 60.0)
        elapsed = time.monotonic() - started
        assert result is not None
        self.assertEqual(result["state"], STATE_RUNNING)
        self.assertLess(elapsed, 3.0)  # clamped to MAX_WAIT_MS, not 60 s
        release.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
