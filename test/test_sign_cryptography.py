from mock import patch
import os
import sys
import unittest

from adb.adb_keygen import keygen
from adb.sign_cryptography import CryptographySigner

from adb_keygen_stub import open_priv_pub


#@unittest.skipIf(not os.path.exists('test/adbkey'), "Skipping because ADB key 'test/adbkey' does not exist.")
class TestCryptographySigner(unittest.TestCase):

    '''@unittest.skipIf(sys.version_info[0] == 2, "This exception is only raised in Python3")
    def test_sign_cryptography_fails_python3(self):
        with patch('adb.sign_cryptography.open', open_priv_pub), patch('adb.adb_keygen.open', open_priv_pub):
            keygen('test/adbkey')
            with self.assertRaises(Exception):
                signer = CryptographySigner('test/adbkey')'''

    '''@unittest.skipIf(sys.version_info[0] == 3, "sign_cryptography is broken in Python3")
    def test_sign(self):
        """Test that the ``Sign`` method does not raise an exception."""
        with patch('adb.sign_cryptography.open', open_priv_pub), patch('adb.adb_keygen.open', open_priv_pub):
            keygen('test/adbkey')
            self.signer = CryptographySigner('test/adbkey')

        self.signer.Sign(b'notadb')
        self.assertTrue(True)'''

    def test_get_public_key(self):
        """Test that the ``GetPublicKey`` method works correctly."""
        with patch('adb.sign_cryptography.open', open_priv_pub), patch('adb.adb_keygen.open', open_priv_pub):
            keygen('test/adbkey')
            self.signer = CryptographySigner('test/adbkey')

        with patch('{}.open'.format(__name__), open_priv_pub):
            with open('test/adbkey.pub') as f:
                pub = f.read()

            self.assertEqual(pub, self.signer.GetPublicKey())
