import time
import unittest
from obcom.data_colection.value import Value


class ValueTest(unittest.TestCase):

    def test_value_eq(self):
        """
        Test equal two Address objects.
        :return:
        """
        now = time.time()
        a1 = Value(15, now)
        later = time.time()
        a2 = Value(15, later)
        self.assertEqual(a1, a2)
        later = time.time()
        a2 = Value(16, later)
        self.assertNotEqual(a1, a2)

    def test_value_gt(self):
        now = time.time()
        a1 = Value(15, now)
        later = time.time()
        a2 = Value(14, later)
        self.assertGreater(a1, a2)
        later = time.time()
        a2 = Value(16, later)
        self.assertGreater(a2, a1)

    def test_value_lt(self):
        now = time.time()
        a1 = Value(15, now)
        later = time.time()
        a2 = Value(16, later)
        self.assertLess(a1, a2)
        later = time.time()
        a2 = Value(14, later)
        self.assertLess(a2, a1)

    def test_date_expired(self):

        now = 1661348770.4527519
        v_date = now - 120  # two minutes older than now
        v1 = Value(23, v_date)
        self.assertTrue(v1.is_expired(now))
        delta = 180
        self.assertFalse(v1.is_expired(now, delta))
        past = now - 1200
        self.assertFalse(v1.is_expired(past))

    def test_to_dict(self):
        value_dict = {'v': 234, 'ts': 1661348770.4527519, 'type': None, 'tags': {}}
        v = Value(**value_dict)
        self.assertIsInstance(v.ts, float)
        dic = v.to_dict()
        self.assertEqual(value_dict, dic)
        self.assertIsInstance(dic['ts'], float)


if __name__ == '__main__':
    unittest.main()
