import unittest

from app.main import is_local_network_host


class LocalNetworkHostTests(unittest.TestCase):
    def test_allows_private_network_and_loopback_addresses(self):
        for hostname in ("192.168.0.10", "10.0.0.4", "127.0.0.1", "::1", "localhost"):
            with self.subTest(hostname=hostname):
                self.assertTrue(is_local_network_host(hostname))

    def test_rejects_public_addresses_and_hostnames(self):
        for hostname in ("8.8.8.8", "example.com", None):
            with self.subTest(hostname=hostname):
                self.assertFalse(is_local_network_host(hostname))


if __name__ == "__main__":
    unittest.main()
