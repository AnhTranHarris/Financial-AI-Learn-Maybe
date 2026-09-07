import unittest

from dusty.m199_six_desk_graduation import SixDeskGraduation


class M199AuthoritySmokeTests(unittest.TestCase):
    def test_authority_defaults_false(self):
        fields = SixDeskGraduation.__dataclass_fields__
        for name in (
            "broker_write_authority",
            "live_write_authority",
            "promotion_authority",
            "risk_override_authority",
            "guardian_override_authority",
        ):
            self.assertIn(name, fields)
            self.assertFalse(fields[name].default)


if __name__ == "__main__":
    unittest.main()
