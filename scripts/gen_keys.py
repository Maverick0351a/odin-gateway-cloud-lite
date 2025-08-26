import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')

def kid_from_pub(pub_raw: bytes) -> str:
    return f"ed25519-{hashlib.sha256(pub_raw).hexdigest()[:16]}"

priv = Ed25519PrivateKey.generate()
seed = priv.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
)
pub_raw = priv.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)

print("ODIN_GATEWAY_PRIVATE_KEY_B64=" + b64u(seed))
print("ODIN_GATEWAY_KID=" + kid_from_pub(pub_raw))
