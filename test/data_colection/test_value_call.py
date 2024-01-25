import time
import unittest
from obcom.data_colection.address import Address
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.tree_user import TreeUser, TreeServiceUser
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse
from obcom.ob_config import OBConfig


class ValueExchangeTest(unittest.TestCase):
    pass


class ValueRequestTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._cfg = OBConfig()

    def tearDown(self) -> None:
        super().tearDown()

    def test_from_dict(self):
        """
        Test create ValueRequest class from dictionary.

        :return:
        """
        d = {'address': 'aaa.bbb.ccc', 'time_of_data': time.time(), 'time_of_data_tolerance': 23, 'trash': 54}
        vr = ValueRequest.from_dict(d)
        self.assertTrue(hasattr(vr, 'address'))
        self.assertTrue(hasattr(vr, 'time_of_data'))
        self.assertTrue(hasattr(vr, 'time_of_data_tolerance'))
        self.assertTrue(hasattr(vr, 'request_timeout'))
        self.assertFalse(hasattr(vr, 'trash'))
        self.assertIsInstance(vr.address, Address)
        self.assertIsInstance(vr.time_of_data, float)

    def test_copy(self):
        """Test correct copied object"""
        vr = ValueRequest(address='sample_address', time_of_data=123456.0, time_of_data_tolerance=5.0,
                          request_timeout=1234567.0, request_type='PUT', request_data={'sample': 55.0},
                          user=TreeServiceUser(name='user_name1'))
        copy_vr = vr.copy()
        # check is copied well
        for k, v in vr.__dict__.items():
            self.assertEqual(v, getattr(copy_vr, k))

        # check is not the same instance
        vr.index = vr.index + 1
        self.assertNotEqual(vr.index, copy_vr.index)
        vr.user.name = vr.user.name + '_changed'
        self.assertNotEqual(vr.user.name, copy_vr.user.name)

        # check id was copied not generate new id
        self.assertEqual(vr.user.id_, copy_vr.user.id_)
        # check id is not the same instance
        vr.user._user_id = b'notthesameid123'
        self.assertNotEqual(vr.user.id_, copy_vr.user.id_)
        # not the same instance of address
        vr.address.adr = vr.address.adr + ['changed']
        self.assertNotEqual(vr.address.adr, copy_vr.address.adr)
        # check data dict was correctly copied
        self.assertDictEqual(vr.request_data, copy_vr.request_data)
        # test request type
        self.assertEqual(vr.request_type, copy_vr.request_type)

    def test_to_dict(self):
        expected_dict = {'address': {'adr': 'aaa.bbb.ccc'},
                         'time_of_data': 1661349399.030824, 'time_of_data_tolerance': 23.0,
                         'request_timeout': 1661349399.030824 - 100, 'request_type': 'GET', 'request_data': {},
                         'user': TreeUser().to_dict(), 'cycle_query': False}
        vr = ValueRequest(address=expected_dict['address'], time_of_data=expected_dict['time_of_data'],
                          time_of_data_tolerance=expected_dict['time_of_data_tolerance'],
                          request_timeout=expected_dict['request_timeout'])
        out_dict = vr.to_dict()
        self.assertNotIsInstance(out_dict['address'], Address, msg='Object Address has not implemented '
                                                                   'method to_dict()')
        self.assertDictEqual(out_dict, expected_dict)

    def test_default_config(self):
        expected_time_of_data_tolerance = self._cfg.config['data_collection']['ValueRequest'][
            'time_of_data_tolerance'].get()
        vr = ValueRequest('aaa.bbb.ccc')
        # request_timeout
        self.assertEqual(expected_time_of_data_tolerance, vr.time_of_data_tolerance)
        self.assertTrue(type(vr.time_of_data_tolerance) == int or float)
        # time_of_data_tolerance
        self.assertTrue(isinstance(vr.time_of_data, float))


class ValueResponseTest(unittest.TestCase):
    def test_from_dict(self):
        """
        Test create ValueResponse class from dictionary.

        :return:
        """
        d = {'address': 'aaa.bbb.ccc', 'value': Value(234, time.time()), 'trash': 54}
        vr = ValueResponse.from_dict(d)
        self.assertTrue(hasattr(vr, 'address'))
        self.assertTrue(hasattr(vr, 'value'))
        self.assertFalse(hasattr(vr, 'trash'))
        self.assertIsInstance(vr.address, Address)
        self.assertIsInstance(vr.value, Value)

        value_dict = {'v': 234, 'ts': 1661349399.030824, 'type': None, 'status': False, 'error': None}
        d = {'address': 'aaa.bbb.ccc', 'value': value_dict, 'trash': 54}
        vr = ValueResponse.from_dict(d)
        self.assertTrue(hasattr(vr, 'address'))
        self.assertTrue(hasattr(vr, 'value'))
        self.assertFalse(hasattr(vr, 'trash'))
        self.assertIsInstance(vr.address, Address)
        self.assertIsInstance(vr.value, Value)
        self.assertTrue(vr.value.v == value_dict['v'])
        self.assertTrue(vr.value.ts == value_dict['ts'])

    def test_to_dict(self):
        value_dict = {'v': 234, 'ts': 1661349399.030824, 'type': None, 'tags': {}}
        expected_dict = {'address': {'adr': 'aaa.bbb.ccc'}, 'value': value_dict, 'status': False,
                         'error': {'code': 230, 'message': 'message error', 'component_name': 'sample_source',
                                   'severity': 'NORMAL'}}
        vr = ValueResponse(expected_dict['address'], Value(**value_dict), expected_dict['status'],
                           expected_dict['error'])
        out_dict = vr.to_dict()
        self.assertNotIsInstance(out_dict['address'], Address, msg='Object Address has not implemented '
                                                                   'method to_dict()')
        self.assertNotIsInstance(out_dict['value'], Value, msg='Object Value has not implemented '
                                                               'method to_dict()')
        self.assertIsInstance(out_dict['value']['ts'], float)
        self.assertDictEqual(out_dict, expected_dict)
        self.assertFalse(out_dict['status'])
        self.assertNotIsInstance(out_dict['error'], ResponseError, msg='Object ResponseError has not implemented '
                                                                       'method to_dict()')
        self.assertDictEqual(out_dict['error'], expected_dict['error'])


if __name__ == '__main__':
    unittest.main()
