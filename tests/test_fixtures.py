import copy
import unittest

from jsonschema import ValidationError

from tools.validate import make_validator, read_fixtures, validate_fixtures, validate_message


class FixtureChecks(unittest.TestCase):
    def setUp(self):
        self.fixtures = copy.deepcopy(read_fixtures())
        self.validator = make_validator()

    def check_invalid(self):
        with self.assertRaises((ValueError, ValidationError)):
            validate_fixtures(self.fixtures, self.validator)

    def test_worked_exchange(self):
        self.assertEqual(validate_fixtures(self.fixtures, self.validator), 12)

    def test_stale_compare_and_set(self):
        self.fixtures["exchange.json"][2]["body"]["expected_route_revision"] = 0
        self.check_invalid()

    def test_unknown_channel(self):
        self.fixtures["snapshot.json"]["body"]["routes"][0]["source_id"] = "wrong-input"
        self.check_invalid()

    def test_missing_owner(self):
        self.fixtures["exchange.json"][1]["body"]["route"]["owner_session"] = None
        self.check_invalid()

    def test_epoch_change(self):
        self.fixtures["exchange.json"][3]["epoch"] = "old-epoch"
        self.check_invalid()

    def test_unrelated_response(self):
        self.fixtures["exchange.json"][3]["request_id"] = "other-request"
        self.check_invalid()

    def test_invalid_restore(self):
        self.fixtures["exchange.json"][-1]["body"]["route"]["enabled"] = True
        self.check_invalid()

    def test_nonfinite_gain(self):
        self.fixtures["exchange.json"][2]["body"]["gain_db"] = float("nan")
        self.check_invalid()

    def test_gain_outside_device_range(self):
        self.fixtures["exchange.json"][2]["body"]["gain_db"] = 7
        self.check_invalid()

    def test_wrong_applied_gain(self):
        self.fixtures["exchange.json"][3]["body"]["route"]["gain_db"] = -11.5
        self.check_invalid()

    def test_gain_tie_rounds_down(self):
        self.fixtures["exchange.json"][2]["body"]["gain_db"] = -11.75
        validate_fixtures(self.fixtures, self.validator)

    def test_renew_does_not_change_audio(self):
        self.fixtures["exchange.json"][5]["body"]["route"]["enabled"] = False
        self.check_invalid()

    def test_wrong_pan_topology(self):
        self.fixtures["snapshot.json"]["body"]["routes"][0]["pan_law"] = "stereo_balance"
        self.check_invalid()

    def test_indeterminate_needs_affected_resources(self):
        message = {
            "version": "0.1", "request_id": "set-1", "op": "set_route",
            "epoch": "epoch-demo", "revision": 2, "status": "indeterminate",
            "body": {"code": "backend_failure", "message": "Readback unavailable"},
        }
        with self.assertRaises(ValueError):
            validate_message(message, self.validator)
        message["body"]["affected_resources"] = ["route-vocal-cue"]
        validate_message(message, self.validator)

    def test_unknown_field(self):
        self.fixtures["exchange.json"][2]["body"]["phantom"] = True
        self.check_invalid()


if __name__ == "__main__":
    unittest.main()
