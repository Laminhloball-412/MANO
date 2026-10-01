from __future__ import annotations

import socket
import time

from .models import SliceProfile


class OaiTelnetControl:
    """Control a running OAI UE through the ciUE telnet module.

    The extended commands are supplied by patches/oai-duranta-runtime-pdu.patch.
    No fallback restarts the UE; failure therefore preserves the old PDU session.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9090, timeout: float = 5):
        self.host, self.port, self.timeout = host, port, timeout

    def _command(self, command: str) -> str:
        with socket.create_connection((self.host, self.port), self.timeout) as conn:
            conn.settimeout(self.timeout)
            try:
                conn.recv(65535)
            except socket.timeout:
                pass
            conn.sendall(command.encode() + b"\n")
            time.sleep(0.3)
            chunks = []
            while True:
                try:
                    chunk = conn.recv(65535)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if b"softmodem>" in chunk:
                    break
            output = b"".join(chunks).decode(errors="replace")
            if "ERROR" in output.upper() or "UNKNOWN COMMAND" in output.upper():
                raise RuntimeError(output.strip())
            return output

    def establish(self, profile: SliceProfile, ue_id: int = 0) -> str:
        return self._command(
            f"ciUE add_pdu_session {profile.pdu_session_id},{ue_id},{profile.sst},{profile.sd},{profile.dnn}"
        )

    def release(self, pdu_session_id: int, ue_id: int = 0) -> str:
        return self._command(f"ciUE release_pdu_session {pdu_session_id},{ue_id}")
