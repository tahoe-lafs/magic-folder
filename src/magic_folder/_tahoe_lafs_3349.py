# Copyright (C) Least Authority TFA GmbH

"""
Hotfix for https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3349
"""

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa


def _hotfix_create_signing_keypair(key_size):
    """
    Create a new RSA signing (private) keypair from scratch. Can be used with
    `sign_data` function.

    :param int key_size: length of key in bits

    :returns: 2-tuple of (private_key, public_key)
    """
    priv_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )
    return priv_key, priv_key.public_key()


def hotfix():
    """
    Apply the issue fix.
    """
    import allmydata.crypto.rsa as rsa
    try:
        # Attempt to pick a key size neither too large nor too small.  We
        # don't want cryptography to reject it for security policy reasons nor
        # do we want to burn 30 CPU seconds generating an unused key.
        rsa.create_signing_keypair(2048)
    except ValueError:
        # This could mean the key size was too small or the public exponent
        # supplied was unacceptable.  Regarding key size, though, a
        # python-cryptography developer said:
        #
        #   It's unlikely cryptography will ever reject a 2048 bit RSA key.
        #
        rsa.create_signing_keypair = _hotfix_create_signing_keypair
