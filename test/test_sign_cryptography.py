import os
import sys
import unittest

from adb.sign_cryptography import CryptographySigner


@unittest.skipIf(not os.path.exists('test/adbkey'), "Skipping because ADB key 'test/adbkey' does not exist.")
class TestCryptographySigner(unittest.TestCase):
    @unittest.skipIf(sys.version_info[0] == 2, "This exception is only raised in Python3")
    def test_sign_cryptography_fails_python3(self):
        with self.assertRaises(Exception):
            signer = CryptographySigner('test/adbkey')

    #@unittest.skipIf(sys.version_info[0] == 3, "sign_cryptography is broken in Python3")
    #def test_sign(self):
    #    """Test that the ``Sign`` method does not raise an exception."""
    #    self.signer = CryptographySigner('test/adbkey')
    #    self.signer.Sign(b'notadb')
    #    self.assertTrue(True)
