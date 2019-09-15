import adb_protocol

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric import utils


class CryptographySigner(adb_protocol.AuthSigner):
    def __init__(self, rsa_key_path):
        with open(rsa_key_path + ".pub") as rsa_pub_file:
            self.public_key = rsa_pub_file.read()

        with open(rsa_key_path) as rsa_prv_file:
            self.rsa_key = serialization.load_pem_private_key(
                rsa_prv_file.read(), None, default_backend()
            )

    def Sign(self, data):
        return self.rsa_key.sign(
            data, padding.PKCS1v15(), utils.Prehashed(hashes.SHA1())
        )

    def GetPublicKey(self):
        return self.public_key
