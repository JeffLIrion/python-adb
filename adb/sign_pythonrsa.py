from pyasn1.codec.der import decoder
from pyasn1.type import univ
import rsa
from rsa import pkcs1

from adb import adb_protocol


class _Accum(object):
    def __init__(self):
        self._buf = b""

    def update(self, msg):
        self._buf += msg

    def digest(self):
        return self._buf


pkcs1.HASH_METHODS["SHA-1-PREHASHED"] = _Accum
pkcs1.HASH_ASN1["SHA-1-PREHASHED"] = pkcs1.HASH_ASN1["SHA-1"]


def _load_rsa_private_key(pem):
    try:
        der = rsa.pem.load_pem(pem, "PRIVATE KEY")
        keyinfo, _ = decoder.decode(der)
        if keyinfo[1][0] != univ.ObjectIdentifier("1.2.840.113549.1.1.1"):
            raise ValueError("Not a DER-encoded OpenSSL private RSA key")
        private_key_der = keyinfo[2].asOctets()
    except IndexError:
        raise ValueError("Not a DER-encoded OpenSSL private RSA key")
    return rsa.PrivateKey.load_pkcs1(private_key_der, format="DER")


class PythonRSASigner(adb_protocol.AuthSigner):
    def __init__(self, pub=None, priv=None):
        self.priv_key = _load_rsa_private_key(priv)
        self.pub_key = pub

    @classmethod
    def FromRSAKeyPath(cls, rsa_key_path):
        with open(rsa_key_path + ".pub") as f:
            pub = f.read()
        with open(rsa_key_path) as f:
            priv = f.read()
        return cls(pub, priv)

    def Sign(self, data):
        return rsa.sign(data, self.priv_key, "SHA-1-PREHASHED")

    def GetPublicKey(self):
        return self.pub_key
