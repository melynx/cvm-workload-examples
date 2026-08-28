import io
import json
import unittest
import urllib.parse

import server


class FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name.lower()] = value

    def end_headers(self):
        pass


class ServerTests(unittest.TestCase):
    def test_reported_payload_uses_last_structured_log(self):
        logs = "\n".join(
            [
                "plain text",
                'prefix {"payload_bytes": 10}',
                'prefix {"kind":"ready","payload_bytes":536870912}',
            ]
        )
        self.assertEqual(server.parse_reported_payload_bytes(logs), 536870912)

    def test_state_passes_through_dynamic_portal_environment(self):
        responses = {
            "/portal-external-api/status": (
                200,
                {
                    "workload_ref": "baby-container-tester:v0.2.0",
                    "workload_id": "0xworkload",
                    "platform": {
                        "cloud_type": "azure",
                        "tee_type": "sev-snp",
                        "machine_type": "Standard_DC4as_v5",
                        "attestation_mode": "hardware",
                    },
                    "chain": {
                        "registration": "required",
                        "tee_backend": "auto",
                        "status": "verified",
                        "chain_id": 31337,
                        "session_id": "0xsession",
                    },
                    "prover": {"backend": "sp1", "execution": "remote"},
                },
            ),
            "/baby-container/image/list": (
                200,
                {"images": [{"image_id": "sha256:image"}]},
            ),
            "/baby-container/list": (
                200,
                {
                    "images": [{"image_id": "sha256:image"}],
                    "instances": [
                        {
                            "instance_id": "test-1",
                            "container_id": "container",
                            "status": "running",
                        }
                    ],
                },
            ),
            "/baby-container/logs?instance_id=test-1&max_bytes=1048576": (
                200,
                {"logs": 'prefix {"payload_bytes":1073741824}'},
            ),
        }
        original = server.portal_request
        server.portal_request = lambda method, path, **kwargs: responses[path]
        try:
            handler = FakeHandler()
            server.Handler.send_state(
                handler, urllib.parse.urlparse("/api/state")
            )
        finally:
            server.portal_request = original

        self.assertEqual(handler.status, 200)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(payload["portal"]["platform"]["tee_type"], "sev-snp")
        self.assertEqual(payload["portal"]["chain"]["tee_backend"], "auto")
        self.assertEqual(payload["portal"]["prover"]["backend"], "sp1")
        self.assertEqual(payload["baby_reported_payload_bytes"], 1073741824)


if __name__ == "__main__":
    unittest.main()
