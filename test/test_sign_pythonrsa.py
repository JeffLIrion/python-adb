import os
import unittest

from adb.sign_pythonrsa import PythonRSASigner


@unittest.skipIf(not os.path.exists('test/adbkey'), "Skipping because ADB key 'test/adbkey' does not exist.")
class TestPythonRSASigner(unittest.TestCase):
    def setUp(self):
        self.signer = PythonRSASigner.FromRSAKeyPath('test/adbkey')

    def test_sign(self):
        """Test that the ``Sign`` method does not raise an exception."""
        self.signer.Sign(b'notadb')
        self.assertTrue(True)
