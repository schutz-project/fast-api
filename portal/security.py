import base64
import hashlib
import hmac
import json
import secrets
import time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

password_hasher = PasswordHasher()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))

def token():
    return secrets.token_urlsafe(32)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def verify_password(encoded, password):
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False

def sign(payload, secret, purpose):
    data = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(secret.encode(), (purpose + ':' + data).encode(), hashlib.sha256).hexdigest()
    return data + '.' + sig

def unsign(value, secret, purpose):
    try:
        if len(value) > 2048:
            return None
        data, sig = value.split('.')
        expected = hmac.new(secret.encode(), (purpose + ':' + data).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
        if not isinstance(payload, dict) or not isinstance(payload.get('exp'), (int, float)):
            return None
        if not time.time() < payload['exp'] <= time.time() + 600:
            return None
        return payload
    except (ValueError, TypeError, UnicodeError):
        return None

def unknown_account(username, secret):
    return 'hmac:' + hmac.new(secret.encode(), ('account:' + username).encode(), hashlib.sha256).hexdigest()
