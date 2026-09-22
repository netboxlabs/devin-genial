"""The opt-in IPv4 routing must filter without ever returning nothing."""

import socket
import unittest
from unittest.mock import patch

from estates.turbobulk import _apply_force_ipv4, _ipv4_first

V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.13.194", 443))
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::6812:dc2", 443, 0, 0))


class IPv4First(unittest.TestCase):
    def test_prefers_ipv4_and_drops_ipv6(self):
        self.assertEqual(_ipv4_first([V6, V4]), [V4])

    def test_falls_back_to_input_when_no_ipv4_exists(self):
        self.assertEqual(_ipv4_first([V6]), [V6])

    def test_apply_is_env_gated_and_idempotent(self):
        original = socket.getaddrinfo
        try:
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("GENIAL_FORCE_IPV4", None)
                _apply_force_ipv4()
                self.assertIs(socket.getaddrinfo, original)
            with patch.dict("os.environ", {"GENIAL_FORCE_IPV4": "1"}):
                _apply_force_ipv4()
                wrapped = socket.getaddrinfo
                self.assertIsNot(wrapped, original)
                _apply_force_ipv4()
                self.assertIs(socket.getaddrinfo, wrapped)
        finally:
            socket.getaddrinfo = original


if __name__ == "__main__":
    unittest.main()
