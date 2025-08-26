from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .utils import b64u, b64u_decode


class GatewaySigner:
    """Ed25519 signer using a 32-byte seed (base64url, no padding)."""
    def __init__(self, priv_seed_b64u: str, kid: str):
        self.kid = kid
        seed = b64u_decode(priv_seed_b64u)
        if len(seed) != 32:
            raise ValueError("ODIN_GATEWAY_PRIVATE_KEY_B64 must be a 32-byte base64url seed")
        self._priv = Ed25519PrivateKey.from_private_bytes(seed)
        self._pub = self._priv.public_key()

    def sign(self, message: bytes) -> str:
        sig = self._priv.sign(message)
        return b64u(sig)

    def public_jwk(self) -> dict[str, Any]:
        pub_raw = self._pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {"kty":"OKP","crv":"Ed25519","x": b64u(pub_raw), "kid": self.kid, "status":"active"}

def load_signer_from_env() -> GatewaySigner | None:
    priv = os.getenv("ODIN_GATEWAY_PRIVATE_KEY_B64")
    kid = os.getenv("ODIN_GATEWAY_KID", "gw-default")
    if not priv:
        return None
    return GatewaySigner(priv, kid)

def merge_jwks(active: dict[str, Any] | None, additional_json: str | None) -> dict[str, Any]:
    keys = []
    if active:
        keys.append(active)
    if additional_json:
        try:
            extra = json.loads(additional_json)
            for k in (extra.get("keys") or []):
                if isinstance(k, dict):
                    keys.append(k)
        except Exception:
            pass
    return {"keys": keys}
