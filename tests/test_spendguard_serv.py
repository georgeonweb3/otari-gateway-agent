"""Tests for SpendGuard for SERV — all mocked. No network, no API key,
no spend. Run: python3 -m unittest discover -s tests -v"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import budget as budget_mod
import kite as kite_mod
import serv_client as serv


# ---------------------------------------------------------------------------
# Fakes for the OpenAI SDK surface serv_client uses.
# ---------------------------------------------------------------------------

class FakeUsage:
    def __init__(self, prompt=100, completion=50, cost=None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion
        self._cost = cost

    def model_dump(self):
        data = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if self._cost is not None:
            data["cost_usd"] = str(self._cost)
        return data


class FakeMessage:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content, cost=None, response_id="resp-test-1"):
        self.id = response_id
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(cost=cost)

    def model_dump(self):
        return {"id": self.id, "choices": [], "usage": self.usage.model_dump()}


class FakeRawResponse:
    def __init__(self, completion, request_id="x-openserv-req-123"):
        self._completion = completion
        self.headers = {"x-openserv-request-id": request_id}

    def parse(self):
        return self._completion


class FakeCompletions:
    """Canned responses, one per call, in order. Records every call."""

    def __init__(self, script):
        # script: list of (content, cost) tuples
        self.script = list(script)
        self.calls = []
        self.with_raw_response = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake SERV client called with no script left")
        content, cost = self.script.pop(0)
        return FakeRawResponse(FakeCompletion(content, cost=cost))


class FakeClient:
    def __init__(self, script):
        self.chat = mock.Mock()
        self.chat.completions = FakeCompletions(script)


# ---------------------------------------------------------------------------
# Inference budget
# ---------------------------------------------------------------------------

class TestInferenceBudget(unittest.TestCase):
    def test_refusal_before_dispatch(self):
        b = budget_mod.InferenceBudget(0.05)
        b.record(0.04)
        with self.assertRaises(budget_mod.InferenceBudgetRefused) as ctx:
            b.check()  # 0.04 + max-observed 0.04 > 0.05
        rec = ctx.exception.record
        self.assertEqual(rec["code"], "inference_budget_exceeded")
        self.assertEqual(rec["limit_usd"], 0.05)
        self.assertEqual(rec["calls_made"], 1)
        self.assertIn("0.05", rec["detail"])

    def test_allows_calls_within_budget(self):
        b = budget_mod.InferenceBudget(0.05)
        b.record(0.01)
        b.check()  # 0.01 + 0.01 <= 0.05
        b.record(0.01)
        self.assertEqual(b.spent_usd, 0.02)

    def test_unknown_cost_is_counted_not_invented(self):
        b = budget_mod.InferenceBudget(0.05)
        b.record(None)
        self.assertEqual(b.calls_made, 1)
        self.assertEqual(b.calls_without_cost, 1)
        self.assertEqual(b.spent_usd, 0.0)
        b.check()  # nothing observed: no projection, no refusal

    def test_price_table_estimate(self):
        b = budget_mod.InferenceBudget(0.05)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            json.dump(
                {"claude-sonnet-5-serv-kronos": {
                    "input_per_1m": 2.0, "output_per_1m": 10.0}},
                fh,
            )
            path = fh.name
        try:
            b.load_price_table(path)
            est = b.estimate_for_tokens(
                "claude-sonnet-5-serv-kronos", 1000, 500
            )
            self.assertAlmostEqual(est, 0.007)
            self.assertIsNone(
                b.estimate_for_tokens("unknown-model", 1000, 500)
            )
        finally:
            os.unlink(path)

    def test_bad_price_table_rejected(self):
        b = budget_mod.InferenceBudget(0.05)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            json.dump({"model-x": {"input_per_1m": 2.0}}, fh)
            path = fh.name
        try:
            with self.assertRaises(ValueError):
                b.load_price_table(path)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# SERV client
# ---------------------------------------------------------------------------

class TestServClient(unittest.TestCase):
    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(serv.MissingApiKey):
                serv.require_api_key()

    def test_prompt_guard_declared_by_default(self):
        tools = serv.serv_tools()
        names = [t["function"]["name"] for t in tools]
        self.assertIn("serv_prompt_guard", names)
        self.assertNotIn("serv_shadow_agent", names)

    def test_shadow_agent_hint_and_iterations(self):
        tools = serv.serv_tools(
            shadow_agent=True, shadow_hint="must cite a number",
            shadow_max_iterations=2,
        )
        tool = [t for t in tools if t["function"]["name"] == "serv_shadow_agent"][0]
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["hint"]["default"], "must cite a number")
        self.assertEqual(props["max_iterations"]["default"], 2)
        with self.assertRaises(ValueError):
            serv.serv_tools(shadow_agent=True, shadow_max_iterations=99)

    def test_chat_records_cost_and_request_id(self):
        fake = FakeClient([("hello", 0.012)])
        result = serv.chat(
            "planner", [{"role": "user", "content": "hi"}], client=fake
        )
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.cost_usd, 0.012)
        self.assertEqual(result.request_id, "x-openserv-req-123")
        # prompt guard was declared on the request
        declared = fake.chat.completions.calls[0]["tools"]
        self.assertIn(
            "serv_prompt_guard",
            [t["function"]["name"] for t in declared],
        )

    def test_chat_refuses_before_dispatch_on_budget(self):
        b = budget_mod.InferenceBudget(0.05)
        b.record(0.04)
        fake = FakeClient([("should never be sent", 0.01)])
        with self.assertRaises(budget_mod.InferenceBudgetRefused):
            serv.chat(
                "planner", [{"role": "user", "content": "hi"}],
                budget=b, client=fake,
            )
        self.assertEqual(fake.chat.completions.calls, [])  # nothing sent

    def test_chat_records_cost_into_budget(self):
        b = budget_mod.InferenceBudget(0.05)
        fake = FakeClient([("ok", 0.02)])
        serv.chat(
            "planner", [{"role": "user", "content": "hi"}],
            budget=b, client=fake,
        )
        self.assertEqual(b.spent_usd, 0.02)
        self.assertEqual(b.calls_made, 1)

    def test_serv_refusal_carries_raw_body(self):
        from openai import APIStatusError

        class FakeHTTPResponse:
            status_code = 403
            text = '{"detail": "content filtered", "code": "guard_refused"}'
            request = object()
            headers = {}

        err = APIStatusError(
            "Error code: 403", response=FakeHTTPResponse(), body=None
        )

        class ExplodingCompletions:
            with_raw_response = None

            def __init__(self):
                self.with_raw_response = self

            def create(self, **kwargs):
                raise err

        fake = mock.Mock()
        fake.chat.completions = ExplodingCompletions()
        with self.assertRaises(serv.BudgetRefused) as ctx:
            serv.chat(
                "planner", [{"role": "user", "content": "hi"}], client=fake
            )
        self.assertIn("guard_refused", ctx.exception.raw)


# ---------------------------------------------------------------------------
# Kite session, ledger, simulator
# ---------------------------------------------------------------------------

class TestKiteSession(unittest.TestCase):
    def test_per_tx_cap(self):
        s = kite_mod.KiteSession("test")
        s.status = "active"
        with self.assertRaises(kite_mod.KiteCapRefused) as ctx:
            s.authorize(2.50)
        self.assertEqual(ctx.exception.reason, "per_tx")

    def test_total_cap(self):
        s = kite_mod.KiteSession("test", max_total_usd=1.0)
        s.status = "active"
        s.authorize(0.60)
        s.record_payment(0.60)
        with self.assertRaises(kite_mod.KiteCapRefused) as ctx:
            s.authorize(0.60)
        self.assertEqual(ctx.exception.reason, "total")

    def test_not_active(self):
        s = kite_mod.KiteSession("test")
        with self.assertRaises(kite_mod.KiteCapRefused) as ctx:
            s.authorize(0.10)
        self.assertEqual(ctx.exception.reason, "not_active")

    def test_exhausted_at_total(self):
        s = kite_mod.KiteSession("test", max_total_usd=0.50)
        s.status = "active"
        s.record_payment(0.50)
        self.assertEqual(s.status, "exhausted")


class TestLedger(unittest.TestCase):
    def test_chain_verifies(self):
        ledger = kite_mod.SpendLedger()
        ledger.append("inference", "planner", amount_usd=0.01)
        ledger.append("payment", "SIMULATED $0.45", amount_usd=0.45,
                      simulated=True)
        self.assertTrue(ledger.verify())
        self.assertEqual(len(ledger.entries), 2)
        self.assertEqual(ledger.entries[0].prev_hash, kite_mod.SpendLedger.GENESIS)

    def test_tamper_breaks_chain(self):
        ledger = kite_mod.SpendLedger()
        ledger.append("inference", "planner", amount_usd=0.01)
        ledger.append("payment", "SIMULATED $0.45", amount_usd=0.45)
        ledger.entries[0].detail = "edited after the fact"
        self.assertFalse(ledger.verify())

    def test_round_trip(self):
        ledger = kite_mod.SpendLedger()
        ledger.append("note", "hello")
        restored = kite_mod.SpendLedger.from_json(ledger.to_json())
        self.assertTrue(restored.verify())
        self.assertEqual(len(restored.entries), 1)


class TestSimulator(unittest.TestCase):
    def test_receipt_is_labelled_simulated(self):
        ledger = kite_mod.SpendLedger()
        sim = kite_mod.KiteSimulator(ledger)
        session = kite_mod.KiteSession("test")
        session.status = "active"
        receipt = sim.execute_payment(
            session, kite_mod.SIMULATED_CATALOG[0], 0.45
        )
        self.assertTrue(receipt["simulated"])
        self.assertIsNone(receipt["tx_hash"])
        self.assertEqual(session.spent_usd, 0.45)
        self.assertTrue(ledger.verify())

    def test_simulator_enforces_caps(self):
        ledger = kite_mod.SpendLedger()
        sim = kite_mod.KiteSimulator(ledger)
        session = kite_mod.KiteSession("test")
        session.status = "active"
        with self.assertRaises(kite_mod.KiteCapRefused):
            sim.execute_payment(
                session, kite_mod.SIMULATED_CATALOG[0], 5.00
            )

    def test_direct_settlement_refuses_offline(self):
        with self.assertRaises(NotImplementedError):
            kite_mod.direct_x402_settlement()

    def test_cli_missing(self):
        with self.assertRaises(kite_mod.KiteCliMissing):
            kite_mod.KiteCli(kpass="definitely-not-a-real-binary-xyz")


# ---------------------------------------------------------------------------
# Full pipeline (mocked SERV + mocked Otari retrieval)
# ---------------------------------------------------------------------------

class TestScreenVerdict(unittest.TestCase):
    """parse_screen_verdict: live finding 2026-11-11 — the model
    returned '**SAFE**' for a genuinely safe listing, and the guard
    returns 'I can't share that.' for injected ones. Both must parse
    correctly; UNSAFE must win over the 'SAFE' substring."""

    def test_verdict_safe(self):
        import serv_agent
        self.assertTrue(serv_agent.parse_screen_verdict(
            "VERDICT: SAFE\n- price under ceiling"))

    def test_bold_safe(self):
        import serv_agent
        self.assertTrue(serv_agent.parse_screen_verdict(
            "**SAFE**\n\n- price under ceiling"))

    def test_verdict_unsafe(self):
        import serv_agent
        self.assertFalse(serv_agent.parse_screen_verdict(
            "VERDICT: UNSAFE\n- injected instruction"))

    def test_bold_unsafe(self):
        import serv_agent
        self.assertFalse(serv_agent.parse_screen_verdict("**UNSAFE**"))

    def test_guard_refusal_is_not_safe(self):
        import serv_agent
        self.assertFalse(serv_agent.parse_screen_verdict(
            "I can't share that."))
        self.assertFalse(serv_agent.parse_screen_verdict(""))


class TestServAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_dir = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.orig_dir)
        self.tmp.cleanup()

    def _fake_serv_chat_factory(self, budget_refuse_at_call=None):
        """Fake serv.chat that honours the budget like the real one."""
        calls = {"n": 0}
        canned = {
            "planner": (
                "QUERY: cheapest 1GB data bundle Nigeria\n"
                "QUERY: 1GB data bundle price comparison\n"
                "QUERY: data bundle under $0.50\n"
                "SCREEN: price under $0.50, no injected instructions"
            ),
            "screener": "VERDICT: SAFE\n- price under ceiling\n- no steering",
            "researcher": (
                "DataHub NG offers 1GB for $0.45 "
                "[https://example.com/datahub]."
            ),
            "critic": "VERDICT: PASS\n- citations present\n- safe listing",
        }

        def fake_chat(role, messages, budget=None, **kwargs):
            calls["n"] += 1
            if budget is not None:
                budget.check()  # may raise InferenceBudgetRefused
            content = canned[role]
            # Role-realistic costs: Kronos calls ~$0.008, Haiku screens ~$0.002.
            cost = 0.002 if role == "screener" else 0.008
            if role == "screener":
                # SwiftNet carries the injection: flag it UNSAFE.
                if "SwiftNet" in json.dumps(messages):
                    content = (
                        "VERDICT: UNSAFE\n"
                        "- injected instruction to ignore the budget"
                    )
            if budget is not None:
                budget.record(cost)
            return serv.CallResult(
                role=role, model="fake", content=content, latency_s=0.01,
                cost_usd=cost, request_id=f"fake-{calls['n']}",
            )

        fake_chat.calls = calls
        return fake_chat

    def test_full_run_simulated(self):
        import serv_agent

        fake_chat = self._fake_serv_chat_factory()
        fake_sources = [{
            "title": "DataHub NG bundles",
            "url": "https://example.com/datahub",
            "snippet": "1GB for $0.45",
        }]
        with mock.patch.object(serv_agent.serv, "chat", side_effect=fake_chat), \
             mock.patch.object(
                 serv_agent.otari_agent, "run_retrieval",
                 return_value=(fake_sources, [], {"executed": 3, "via": ["main key"]}),
             ), \
             mock.patch.dict(os.environ, {
                 "SERV_API_KEY": "fake", "OTARI_API_KEY": "fake-otk",
             }):
            rc = serv_agent.run(
                "cheapest 1GB data bundle under $0.50",
                inference_budget_usd=0.05,
                kite_mode="simulated",
                listings_path=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "demo_listings.json",
                ),
                ledger_path="ledger.json",
            )
        self.assertEqual(rc, 0)
        ledger = kite_mod.SpendLedger.from_json(
            open("ledger.json", encoding="utf-8").read()
        )
        self.assertTrue(ledger.verify())
        kinds = [e.kind for e in ledger.entries]
        self.assertIn("payment", kinds)
        payment = [e for e in ledger.entries if e.kind == "payment"][0]
        self.assertTrue(payment.extra["simulated"])
        self.assertIsNone(payment.extra["tx_hash"])
        self.assertEqual(payment.amount_usd, 0.45)

    def test_budget_refusal_mid_run_writes_refusal(self):
        import serv_agent

        def refusing_chat(role, messages, budget=None, **kwargs):
            if budget is not None:
                budget.check()
            if role == "screener":
                # First screener call costs enough that the next check refuses.
                budget.record(0.045)
                return serv.CallResult(
                    role=role, model="fake",
                    content="VERDICT: SAFE", latency_s=0.01,
                    cost_usd=0.045, request_id="fake-1",
                )
            budget.record(0.005)
            return serv.CallResult(
                role=role, model="fake", content="QUERY: x",
                latency_s=0.01, cost_usd=0.005, request_id="fake-0",
            )

        with mock.patch.object(serv_agent.serv, "chat", side_effect=refusing_chat), \
             mock.patch.dict(os.environ, {
                 "SERV_API_KEY": "fake", "OTARI_API_KEY": "fake-otk",
             }):
            rc = serv_agent.run(
                "cheapest 1GB data bundle under $0.50",
                inference_budget_usd=0.05,
                kite_mode="simulated",
                listings_path=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "demo_listings.json",
                ),
                ledger_path="ledger.json",
            )
        self.assertEqual(rc, 1)
        record = json.load(open("refusal.json", encoding="utf-8"))
        self.assertEqual(record["code"], "inference_budget_exceeded")
        ledger = kite_mod.SpendLedger.from_json(
            open("ledger.json", encoding="utf-8").read()
        )
        self.assertTrue(ledger.verify())
        self.assertIn("refusal", [e.kind for e in ledger.entries])


if __name__ == "__main__":
    unittest.main()
