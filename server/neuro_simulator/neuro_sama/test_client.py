"""Test client for the Neuro Sama module."""

import asyncio
import websockets
import json


async def test_websocket():
    """Test the WebSocket connection to the Neuro Sama module."""
    chat_uri = "ws://localhost:8001/ws/chat"
    admin_uri = "ws://localhost:8001/ws/admin"

    # Connect to chat WebSocket
    async with websockets.connect(chat_uri) as chat_websocket:
        print("Connected to Neuro Sama module (chat endpoint)")

        # Test messages with live stream context
        test_messages = [
            {"content": "LIVE CONTEXT: Neuro is currently live streaming on Twitch. Chat user 'test_user' says: Hello, Neuro Sama!", "module": "stream_system"},
            {"content": "LIVE CONTEXT: Neuro is currently live streaming on Twitch. Chat user 'another_user' says: How are you today?", "module": "stream_system"},
            {"content": "LIVE CONTEXT: Neuro is currently live streaming on Twitch. Chat user 'curious_user' says: Tell me about yourself.", "module": "stream_system", "audio": False},  # Test audio disabled
            {"content": "PRIVATE DM CONTEXT: User 'vedal987' sends a private message: Hey Neuro, how are you doing?", "module": "dm_system", "audio": False}  # Test private DM scenario
        ]

        for msg in test_messages:
            print(f"\nSending: {msg}")

            # Send message
            await chat_websocket.send(json.dumps(msg))

            # Wait for responses until we receive an 'info' message or timeout
            responses_received = 0
            while True:
                try:
                    response = await chat_websocket.recv()
                    response_data = json.loads(response)

                    # Process speak responses to truncate audio_base64 in display
                    if response_data.get("type") == "speak":
                        response_copy = response_data.copy()
                        payload_copy = response_copy.get("payload", {}).copy()
                        if "audio_base64" in payload_copy:
                            original_audio = payload_copy["audio_base64"]
                            if len(original_audio) > 20:
                                payload_copy["audio_base64"] = original_audio[:20] + "..."
                            response_copy["payload"] = payload_copy
                        print(f"Received: {response_copy}")
                    else:
                        print(f"Received: {response_data}")

                    # Handle different response types based on the new output format
                    response_type = response_data.get("type")

                    if response_type == "completion":
                        print("Received completion response, moving to next message...")
                        break
                    elif response_type == "speak":
                        # Handle speak output
                        payload = response_data.get("payload", {})
                        text = payload.get("text", "")
                        audio_base64 = payload.get("audio_base64", "")
                        duration = payload.get("duration", 0)
                        audio_preview = audio_base64[:16] if audio_base64 else "No audio"
                        print(f"Received speak output - Text: '{text[:50]}...', Audio: {audio_preview}..., Duration: {duration}s")
                        responses_received += 1
                    elif response_type == "info":
                        print(f"Received info response: {response_data.get('payload', {}).get('message', '')}")
                    elif response_type == "error":
                        print(f"Received error response: {response_data.get('payload', {}).get('message', '')}")
                        if "currently processing" in response_data.get("payload", {}).get("message", ""):
                            print("Module is processing, waiting for responses...")
                            continue
                        # Don't break here, as we might still get a completion message
                    else:
                        # Handle other types of responses
                        print(f"Received {response_type} response: {response_data}")
                        responses_received += 1

                except websockets.exceptions.ConnectionClosed:
                    print("Connection closed by server")
                    return
                except json.JSONDecodeError:
                    print(f"Received non-JSON response: {response}")
                    continue

            print(f"Processed {responses_received} responses for message: {msg['content']}")

        # Now test the admin WebSocket for configuration reload
        # Keep the chat connection open and create a new admin connection
        print("\n--- Testing Admin WebSocket ---")
        async with websockets.connect(admin_uri) as admin_websocket:
            print("Connected to Neuro Sama module (admin endpoint)")

            # Send a config reload request
            request_id = "test_reload_1"
            reload_request = {
                "action": "reload_config",
                "request_id": request_id,
                "payload": {}
            }

            print(f"Sending config reload request: {reload_request}")
            await admin_websocket.send(json.dumps(reload_request))

            # Wait for response
            try:
                response = await admin_websocket.recv()
                response_data = json.loads(response)
                print(f"Received admin response: {response_data}")

                if response_data.get("request_id") == request_id:
                    status = response_data.get("payload", {}).get("status")
                    message = response_data.get("payload", {}).get("message")
                    print(f"Config reload {status}: {message}")

            except websockets.exceptions.ConnectionClosed:
                print("Admin WebSocket connection closed")
            except json.JSONDecodeError:
                print("Received non-JSON response from admin endpoint")

    print("All tests completed, connections closed.")


def main():
    """Main entry point for the test client."""
    print("Starting Neuro Sama module test client...")
    print("Make sure the Neuro Sama module is running on ws://localhost:8001/ws")
    asyncio.run(test_websocket())


if __name__ == "__main__":
    main()
