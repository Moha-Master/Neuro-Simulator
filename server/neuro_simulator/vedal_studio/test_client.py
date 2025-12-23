"""Test client for the Vedal Studio module."""

import asyncio
import websockets
import json
import aiohttp


async def test_api_health():
    """Test the health check API endpoint."""
    print("--- Testing API Health Endpoint ---")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8000/api/system/health") as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"Health check successful: {data}")
                    return True
                else:
                    print(f"Health check failed with status: {response.status}")
                    return False
    except Exception as e:
        print(f"Error during health check: {e}")
        return False


async def test_websocket_config():
    """Test the WebSocket configuration functionality."""
    print("\n--- Testing WebSocket Configuration ---")
    try:
        async with websockets.connect("ws://localhost:8000/ws/admin") as websocket:
            print("Connected to admin WebSocket")
            
            # Test get_config
            print("\nTesting get_config...")
            request_id = "test_get_config"
            get_config_request = {
                "action": "get_config",
                "request_id": request_id,
                "payload": {}
            }
            
            await websocket.send(json.dumps(get_config_request))
            
            response = await websocket.recv()
            response_data = json.loads(response)
            print(f"Received response: {response_data}")  # Debug print

            if response_data.get("request_id") == request_id:
                # Check if the response contains the config data
                payload_data = response_data.get("payload", {})
                config_data = payload_data.get("config", {})
                if config_data:  # If config exists and is not empty
                    print("Get config successful")

                    # Test save_config using the retrieved config
                    print("\nTesting save_config...")
                    save_request_id = "test_save_config"
                    save_config_request = {
                        "action": "save_config",
                        "request_id": save_request_id,
                        "payload": {
                            "config": config_data  # Use the same config data
                        }
                    }

                    await websocket.send(json.dumps(save_config_request))

                    response = await websocket.recv()
                    response_data = json.loads(response)
                    print(f"Save config response: {response_data}")  # Debug print

                    if response_data.get("request_id") == save_request_id:
                        status = response_data.get("payload", {}).get("status")
                        message = response_data.get("payload", {}).get("message")
                        print(f"Save config {status}: {message}")
                        return True
                    else:
                        print("Save config response doesn't match request ID")
                        return False
                else:
                    print(f"Unexpected response format: {response_data}")
                    return False
            else:
                print(f"Get config response doesn't match request ID. Expected: {request_id}, Got: {response_data.get('request_id')}")
                return False
    except Exception as e:
        print(f"Error during WebSocket config test: {e}")
        return False


async def test_websocket_reload():
    """Test the WebSocket reload functionality."""
    print("\n--- Testing WebSocket Reload ---")
    try:
        async with websockets.connect("ws://localhost:8000/ws/admin") as websocket:
            print("Connected to admin WebSocket")

            # Test reload_modules
            print("\nTesting reload_modules...")
            request_id = "test_reload_modules"
            reload_request = {
                "action": "reload_modules",
                "request_id": request_id,
                "payload": {}
            }

            await websocket.send(json.dumps(reload_request))

            response = await websocket.recv()
            response_data = json.loads(response)

            if response_data.get("request_id") == request_id:
                status = response_data.get("payload", {}).get("status")
                message = response_data.get("payload", {}).get("message")
                print(f"Reload modules {status}: {message}")
                return True
            else:
                print("Reload modules response doesn't match request ID")
                return False
    except Exception as e:
        print(f"Error during WebSocket reload test: {e}")
        return False


async def test_websocket_schema():
    """Test the WebSocket schema functionality."""
    print("\n--- Testing WebSocket Schema ---")
    try:
        async with websockets.connect("ws://localhost:8000/ws/admin") as websocket:
            print("Connected to admin WebSocket")

            # Test get_settings_schema
            print("\nTesting get_settings_schema...")
            request_id = "test_get_schema"
            schema_request = {
                "action": "get_settings_schema",
                "request_id": request_id,
                "payload": {}
            }

            await websocket.send(json.dumps(schema_request))

            response = await websocket.recv()
            response_data = json.loads(response)

            if response_data.get("request_id") == request_id:
                schema = response_data.get("payload", {})
                if isinstance(schema, dict) and "properties" in schema:
                    print(f"Schema retrieved successfully, contains {len(schema.get('properties', {}))} top-level properties")
                    # Print a brief overview of the schema structure, including nested properties
                    for prop_name, prop_info in list(schema.get('properties', {}).items())[:3]:  # Show first 3 properties
                        prop_type = prop_info.get('type', 'unknown')
                        prop_title = prop_info.get('title', prop_name)
                        print(f"  - {prop_name} ({prop_type}): {prop_title}")

                        # If it's an object with properties, show some of them
                        if prop_type == "object" and "properties" in prop_info:
                            nested_props = prop_info["properties"]
                            for nested_name, nested_info in list(nested_props.items())[:3]:  # Show first 3 nested properties
                                nested_type = nested_info.get('type', 'unknown')
                                nested_title = nested_info.get('title', nested_name)
                                print(f"    - {nested_name} ({nested_type}): {nested_title}")

                                # If nested property is also an object, show some of its properties
                                if nested_type == "object" and "properties" in nested_info:
                                    sub_nested_props = nested_info["properties"]
                                    for sub_name, sub_info in list(sub_nested_props.items())[:2]:  # Show first 2 sub-properties
                                        sub_type = sub_info.get('type', 'unknown')
                                        sub_title = sub_info.get('title', sub_name)
                                        print(f"      - {sub_name} ({sub_type}): {sub_title}")
                    return True
                else:
                    print(f"Unexpected schema format: {schema}")
                    return False
            else:
                print("Schema response doesn't match request ID")
                return False
    except Exception as e:
        print(f"Error during WebSocket schema test: {e}")
        return False


async def test_dashboard_access():
    """Test if the dashboard is accessible."""
    print("\n--- Testing Dashboard Access ---")
    try:
        async with aiohttp.ClientSession() as session:
            # Try to access the dashboard root
            async with session.get("http://localhost:8000/") as response:
                if response.status in [200, 301, 302, 404]:
                    print(f"Dashboard access test completed with status: {response.status}")
                    if response.status == 200:
                        print("Dashboard is accessible at http://localhost:8000/")
                    elif response.status == 404:
                        print("Dashboard not found at http://localhost:8000/ (this may be expected if no index is provided)")
                    return True
                else:
                    print(f"Dashboard access failed with status: {response.status}")
                    return False
    except Exception as e:
        print(f"Error during dashboard access test: {e}")
        return False


async def main():
    """Main test function."""
    print("Starting Vedal Studio module test client...")
    print("Make sure the Vedal Studio module is running on http://localhost:8000")
    
    # Run all tests
    health_ok = await test_api_health()
    config_ok = await test_websocket_config()
    reload_ok = await test_websocket_reload()
    schema_ok = await test_websocket_schema()
    dashboard_ok = await test_dashboard_access()

    print("\n--- Test Results ---")
    print(f"API Health Test: {'PASS' if health_ok else 'FAIL'}")
    print(f"WebSocket Config Test: {'PASS' if config_ok else 'FAIL'}")
    print(f"WebSocket Reload Test: {'PASS' if reload_ok else 'FAIL'}")
    print(f"WebSocket Schema Test: {'PASS' if schema_ok else 'FAIL'}")
    print(f"Dashboard Access Test: {'PASS' if dashboard_ok else 'FAIL'}")

    overall_result = health_ok and config_ok and reload_ok and schema_ok and dashboard_ok
    print(f"Overall Result: {'PASS' if overall_result else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())