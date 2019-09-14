import os
import unittest

from adb.sign_pycryptodome import PycryptodomeAuthSigner


@unittest.skipIf(not os.path.exists('test/adbkey'), "Skipping because ADB key 'test/adbkey' does not exist.")
class TestPycryptodomeAuthSigner(unittest.TestCase):
    def setUp(self):
        self.signer = PycryptodomeAuthSigner('test/adbkey')

    def test_sign(self):
        """Test that the ``Sign`` method does not raise an exception."""
        self.signer.Sign(b'notadb')
        self.assertTrue(True)
