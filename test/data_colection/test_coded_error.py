"""Severity forwarding contract for coded errors.

Severity drives client retry/stop behaviour (``ErrorPolicy``): NORMAL retries
under SERVICE, CRITICAL stops the subscription. A constructor that silently
drops ``severity`` demotes every error to NORMAL — connectors believe they
raised CRITICAL (e.g. 3002 "method not implemented") while clients retry
forever against an address that can never succeed.

Regression for the ``TreeStructureError.__init__`` bug (severity accepted but
never forwarded to ``super()``), found 2026-06-20, fixed in 1.2.2.
"""
import unittest

from obcom.data_colection.address import AddressError
from obcom.data_colection.coded_error import BaseCodedError, TreeOtherError, TreeStructureError
from obcom.data_colection.value import TreeValueError


class SeverityForwardingTest(unittest.TestCase):

    CASES = [
        (TreeStructureError, {'code': 3002}),
        (TreeOtherError, {'code': 4005}),
        (TreeValueError, {'code': 2002}),
        (AddressError, {'code': 1004, 'address': 'a.b.c'}),
    ]

    def test_explicit_severity_is_kept(self):
        for cls, kwargs in self.CASES:
            for severity in (BaseCodedError.SEVERITY_TEMPORARY,
                             BaseCodedError.SEVERITY_NORMAL,
                             BaseCodedError.SEVERITY_CRITICAL):
                with self.subTest(cls=cls.__name__, severity=severity):
                    err = cls(message='boom', severity=severity, **kwargs)
                    self.assertEqual(err.severity, severity)

    def test_default_severity_is_normal(self):
        for cls, kwargs in self.CASES:
            with self.subTest(cls=cls.__name__):
                err = cls(message='boom', **kwargs)
                self.assertEqual(err.severity, BaseCodedError.SEVERITY_NORMAL)

    def test_extra_kwargs_survive(self):
        err = TreeOtherError(code=4009, message='device said no',
                             severity=BaseCodedError.SEVERITY_NORMAL, device_errno=1035)
        self.assertEqual(err.kwargs.get('device_errno'), 1035)


if __name__ == '__main__':
    unittest.main()
