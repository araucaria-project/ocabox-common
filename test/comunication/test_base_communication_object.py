import time
import unittest

from obcom.comunication.base_communication_object import BaseCommunicationObject
from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.message_serializer import MessageSerializer
from obcom.comunication.multipart_structure import MultipartStructure


class BaseCommunicationObjectTest(unittest.TestCase):
    class SampleBaseCommunicationObject(BaseCommunicationObject):
        DEFAULT_NAME = 'DefaultClientTest'
        TYPE = 'client'

    def test__get_time_to_expire(self):
        bco = self.SampleBaseCommunicationObject(name='SampleTestClient')
        now = time.time()
        delta = 1
        # fresh message
        ms = MultipartStructure(
            [b'', MessageSerializer.pack_b(now), b'0', MessageSerializer.pack_b(now + delta), b'\xc2', b'',
             b'sample_data'])
        time_to_expire = bco._get_time_to_expire(ms=ms)
        self.assertTrue(0 < time_to_expire < delta)
        # expire message
        ms = MultipartStructure(
            [b'', MessageSerializer.pack_b(now), b'0', MessageSerializer.pack_b(now - delta), b'\xc2', b'', b'sample_data'])
        with self.assertRaises(CommunicationTimeoutError):
            bco._get_time_to_expire(ms=ms)
        # message hasn't timeout value
        ms = MultipartStructure(
            [b'', MessageSerializer.pack_b(now), b'0', b'', b'\xc2', b'', b'sample_data'])
        with self.assertRaises(CommunicationTimeoutError):
            bco._get_time_to_expire(ms=ms)
        # message hasn't timeout value and try to get default value
        time_to_expire = bco._get_time_to_expire(ms=ms, use_default=True)
        self.assertTrue(time_to_expire == bco.default_timeout)

    def test_get_cfg(self):
        """Test method get_cfg()"""
        bco = self.SampleBaseCommunicationObject(name='SampleTestClient')
        self.assertEqual(bco.get_cfg("protocol"), "tcp")
        self.assertIsNone(bco.get_cfg("not_exist"))
        # test get default config
        self.assertEqual(bco.get_cfg("time_of_data_tolerance"), 0.5)

    def test_get_cfg_deep(self):
        """Test method get_cfg_deep()"""
        bco = self.SampleBaseCommunicationObject(name='SampleTestClient')
        # one lvl deep
        self.assertEqual(bco.get_cfg_deep(["protocol"]), "tcp")
        self.assertIsNone(bco.get_cfg_deep(["not_exist"]))
        # many lvl deep
        self.assertEqual(bco.get_cfg_deep(["app", "base_fits_dir_test_default"]), "test_dir22")
        self.assertIsNone(bco.get_cfg_deep(["app", "not_exist"]))
        self.assertIsNone(bco.get_cfg_deep(["not_exist_lvl_1", "not_exist"]))
        # test get default config
        self.assertEqual(bco.get_cfg_deep(["time_of_data_tolerance"]), 0.5)


if __name__ == '__main__':
    unittest.main()
