import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import mock_agent
import node
import protocol


BASE_IMAGE = "0x" + "aa" * 32 + "/automata-linux:v1"
WORKLOAD = "0x" + "bb" * 32 + "/peer-attestation-demo:v1"


def success_response(session):
    return {
        "verified": True,
        "session_id": session["session_id"],
        "session_key_fingerprint": session["session_pubkey"]["fingerprint"],
        "session_public_key": {
            "type_id": session["session_pubkey"]["type_id"],
            "key": session["session_pubkey"]["key"],
        },
        "workload_id": "0x" + "11" * 32,
        "base_image_id": "0x" + "22" * 32,
        "binding_mode": "chain",
        "binding_chain_id": 560048,
        "binding_registry": "0x" + "33" * 20,
        "attestation_mode": "hardware",
        "sources": {"inputs": {}},
        "checks": [{"name": "Session binding", "valid": True, "detail": None}],
    }


class VerifierHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = {}
    request_body = None

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).request_body = json.loads(self.rfile.read(length))
        body = json.dumps(type(self).response_body).encode()
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class VerifierdHttpTests(unittest.TestCase):
    def setUp(self):
        VerifierHandler.response_status = 200
        VerifierHandler.response_body = success_response(mock_agent._sign_message("0x04"))
        VerifierHandler.request_body = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), VerifierHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_request_contains_dynamic_portal_and_expected_references(self):
        result = protocol.verify_peer_session(
            {"host": "beta.example", "port": 2024},
            BASE_IMAGE,
            WORKLOAD,
            f"http://127.0.0.1:{self.server.server_port}",
        )
        self.assertTrue(result["verified"])
        self.assertEqual(
            VerifierHandler.request_body,
            {
                "portal": {"host": "beta.example", "port": 2024},
                "base_image": BASE_IMAGE,
                "workload": WORKLOAD,
            },
        )

    def test_structured_failure_is_not_hidden(self):
        VerifierHandler.response_status = 422
        VerifierHandler.response_body = {
            "checks": [],
            "errors": ["peer quote did not match the measurement policy"],
        }
        with self.assertRaisesRegex(
            protocol.VerifierdError,
            "peer quote did not match the measurement policy",
        ):
            protocol.verify_peer_session(
                {"host": "beta.example", "port": 2024},
                BASE_IMAGE,
                WORKLOAD,
                f"http://127.0.0.1:{self.server.server_port}",
            )


class HandshakeBindingTests(unittest.TestCase):
    def setUp(self):
        node.STATE = node.NodeState()
        node.STATE.peer_portal = {"host": "beta.example", "port": 2024}

    def test_signature_uses_verifierd_key_not_claimed_key(self):
        ephemeral = "0x04"
        signed = mock_agent._sign_message(ephemeral)
        verified = success_response(signed)
        claimed = dict(signed)
        claimed["session_pubkey"] = {
            "type_id": 3,
            "key": "0x04" + "00" * 64,
            "fingerprint": "0x" + "00" * 32,
        }
        frame = {
            "ephemeral_public_key": ephemeral,
            "signature": signed["signature"],
            "session_info": protocol.parse_session_info(claimed),
        }

        with mock.patch.object(protocol, "verify_peer_session", return_value=verified):
            _, peer_info, result = node._verify_peer_handshake(frame)

        self.assertEqual(
            peer_info["session_pubkey"]["key"],
            verified["session_public_key"]["key"],
        )
        self.assertTrue(result["verified"])

    def test_claimed_session_id_must_match_verified_session(self):
        ephemeral = "0x04"
        signed = mock_agent._sign_message(ephemeral)
        verified = success_response(signed)
        verified["session_id"] = "0x" + "ff" * 32
        frame = {
            "ephemeral_public_key": ephemeral,
            "signature": signed["signature"],
            "session_info": protocol.parse_session_info(signed),
        }

        with mock.patch.object(protocol, "verify_peer_session", return_value=verified):
            with self.assertRaisesRegex(ValueError, "session_id does not match"):
                node._verify_peer_handshake(frame)


if __name__ == "__main__":
    unittest.main()
