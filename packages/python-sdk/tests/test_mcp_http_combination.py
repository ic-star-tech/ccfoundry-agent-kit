"""Combination-only evidence for pending kit PRs 17, 18 and 19."""
from fastapi.testclient import TestClient

from ccfoundry_agent_kit import create_agent_app
from mcp_fixture_server import http_peer
from test_mcp_bootstrap_example import example, setup_example


def test_authenticated_json_and_sse_chat_each_call_tool_once(example, tmp_path, monkeypatch):
    with http_peer() as peer:
        bootstrap = setup_example(example, peer, tmp_path, monkeypatch)
        app = create_agent_app(manifest=example.MANIFEST, chat_handler=example.handle_chat, agent_space_dir=tmp_path, foundry_bootstrap=bootstrap)
        with TestClient(app) as client:
            denied = client.post('/api/chat', json={'message':'Search the synthetic source','stream':False})
            assert denied.status_code in (401, 403)
            assert peer.requests == []
            result = client.post('/api/chat', json={'message':'Search the synthetic source','stream':False}, headers={'Authorization':'Bearer fixture-agent-secret'})
            assert result.status_code == 200 and result.headers['content-type'].startswith('application/json')
            assert result.json()['metadata']['mcp_calls'][0]['status'] == 'success'
            assert peer.calls.count('tools/call') == 1
            streamed = client.post('/api/chat', json={'message':'Search the synthetic source','stream':True}, headers={'Authorization':'Bearer fixture-agent-secret'})
            assert streamed.status_code == 200 and streamed.headers['content-type'].startswith('text/event-stream')
            assert '"mcp_calls"' in streamed.text and 'fixture-agent-secret' not in streamed.text
            assert peer.calls.count('tools/call') == 2
