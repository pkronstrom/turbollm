"""Hand-rolled multipart/form-data encoding, kept stdlib-only.

Single shared implementation for cli.transcribe and transcribe_split —
one place to fix encoding bugs.
"""
import uuid


def build_multipart(fields: dict, file_field: str, filename: str,
                    content_type: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Encode form fields plus one file part. Returns (body, content_type)."""
    boundary = "----turbollm" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
