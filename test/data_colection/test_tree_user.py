import unittest

from obcom.data_colection.tree_user import BaseTreeUser, TreeUser


class BaseTreeUserTest(unittest.TestCase):

    def setUp(self):
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()

    def test_user_equal(self):
        btu = BaseTreeUser(name='Some name')
        btu.set_id(b'12345')
        other = BaseTreeUser(name='Some other name')
        other.set_id(b'12345')
        self.assertTrue(btu == other)


class TreeUserTest(unittest.TestCase):

    def test_user_equal_mixed_child_class(self):
        btu = BaseTreeUser(name='Some abstract name')
        btu.set_id(b'12345')
        other = TreeUser(name='Some normal name')
        other.socket_id = b'12345'
        self.assertTrue(btu == other)
        self.assertTrue(other._user_id == b'')
        other2 = TreeUser(name='Some normal name 2')
        other2.socket_id = b'123456'
        self.assertFalse(btu == other2)
        self.assertTrue(other2._user_id == b'')


class TreeServiceUserTest(unittest.TestCase):
    pass


if __name__ == '__main__':
    unittest.main()
