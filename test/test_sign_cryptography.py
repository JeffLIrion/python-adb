from mock import patch
import os
import unittest

from adb.adb_keygen import keygen
from adb.sign_cryptography import CryptographySigner

from adb_keygen_stub import open_priv_pub


class TestCryptographySigner(unittest.TestCase):
    def setUp(self):
        with patch('adb.sign_cryptography.open', open_priv_pub), patch('adb.adb_keygen.open', open_priv_pub):
            keygen('test/adbkey')
            self.signer = CryptographySigner('test/adbkey')

    def test_sign(self):
        """Test that the ``Sign`` method does not raise an exception."""
        with self.assertRaises(ValueError):
            self.signer.Sign(b'notadb')
        self.assertTrue(True)

    def test_get_public_key(self):
        """Test that the ``GetPublicKey`` method works correctly."""
        with patch('{}.open'.format(__name__), open_priv_pub):
            with open('test/adbkey.pub') as f:
                pub = f.read()

            self.assertEqual(pub, self.signer.GetPublicKey())
