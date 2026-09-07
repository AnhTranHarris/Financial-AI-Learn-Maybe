import unittest


class M199ImportContractTests(unittest.TestCase):
    def test_m198_contract_is_importable(self):
        import dusty.multi_desk_certification as module
        self.assertIsNotNone(module)


if __name__ == "__main__":
    unittest.main()
