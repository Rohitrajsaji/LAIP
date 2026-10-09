"""Create local runtime secrets once; never overwrite an existing stack identity."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / ".local"
if root.is_symlink():
    raise RuntimeError("Runtime secret directory may not be a symlink")
root.mkdir(mode=0o700, exist_ok=True)
os.chmod(root, 0o700)


def create(name: str, value: str) -> str:
    path = root / name
    try:
        with path.open("x") as stream:
            os.chmod(path, 0o444)
            stream.write(value + "\n")
    except FileExistsError:
        if path.is_symlink():
            raise RuntimeError("Runtime secret may not be a symlink")
    # Parent directory is owner-only; read-only files let non-root containers
    # read Compose's bind-mounted secrets without exposing host traversal.
    os.chmod(path, 0o444)
    return path.read_text().strip()


password = create("database_password", secrets.token_hex(24))
create("service_token", secrets.token_hex(32))
create("database_url", f"postgresql://laip:{password}@database:5432/laip")
runtime_password = create("runtime_password", secrets.token_hex(32))
create(
    "runtime_database_url",
    f"postgresql://laip_runtime:{runtime_password}@database:5432/laip",
)
print("Local runtime secrets initialized; existing values preserved.")
