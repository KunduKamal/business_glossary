from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

out = Path.home() / ".dbt"
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

(out / "snowflake_key.p8").write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
))

pub = key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
body = "".join(line for line in pub.splitlines() if "-----" not in line)
print(body)