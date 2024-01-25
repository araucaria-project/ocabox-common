import unittest

from obcom.data_colection.address import Address


class AddressTest(unittest.TestCase):

    def test_address_create(self):
        d = {'adr': 'aaa.bbb.ccc', 'trash': 343}
        a = Address(**d)
        self.assertTrue(hasattr(a, 'adr'))
        self.assertEqual(a.adr, d['adr'].split('.'))
        self.assertFalse(hasattr(a, 'trash'))

        a = Address(adr='')
        self.assertIsInstance(a, Address)

    def test_address_eq(self):
        """
        Test equal two Address objects.
        :return:
        """
        a1 = Address('aaa.bbb.ccc')
        a2 = Address('aaa.bbb.ccc')
        self.assertEqual(a1, a2)
        a2 = Address('bbb.aaa.ccc')
        self.assertNotEqual(a1, a2)
        a2 = Address('aaa.bbb')
        self.assertNotEqual(a1, a2)

    def test_address_as_address(self):
        s1 = 'aaa.bbb.ccc'
        a1 = Address('ff.ss.aa')
        out = Address.as_address(s1)
        self.assertIsInstance(out, Address)
        out = Address.as_address(a1)
        self.assertIsInstance(out, Address)

    def test_address_str(self):
        s = 'ff.ss.aa'
        a1 = Address(s)
        self.assertIsInstance(a1.__str__(), str)
        self.assertEqual(a1.__str__(), s)

    def test_address_iter(self):
        s = 'ff.ss.aa'
        a1 = Address(s)
        sl = s.split('.')
        for index, item in enumerate(a1):
            self.assertEqual(item, sl[index])

    def test_to_dict(self):
        d_init = {'adr': 'aaa.bbb.ccc'}
        a = Address(**d_init)
        dic = a.to_dict()
        self.assertEqual(d_init, dic)


if __name__ == '__main__':
    unittest.main()
