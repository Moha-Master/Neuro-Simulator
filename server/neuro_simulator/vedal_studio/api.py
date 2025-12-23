"""API endpoints for the Vedal Studio module."""

import json
import os
from pathlib import Path
import yaml
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import ConfigManager

router = APIRouter()

# Global variables to store app state
app_state = {
    'config_manager': None,
    'active_websockets': set()
}


# API 端点
@router.get("/api/system/health")
async def health_check():
    return {"status": "healthy", "message": "Vedal Studio is running"}


# WebSocket 端点
@router.websocket("/ws/admin")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    app_state['active_websockets'].add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # 处理不同的管理消息
            action = message.get("action")
            payload = message.get("payload", {})
            
            if action == "get_config":
                config = app_state['config_manager'].config
                await websocket.send_text(json.dumps({
                    "type": "response",
                    "request_id": message.get("request_id"),
                    "payload": {
                        "status": "success",
                        "config": config
                    }
                }))
            elif action == "save_config":
                new_config = payload.get("config")
                if new_config:
                    try:
                        # 获取旧配置用于比较
                        old_config = app_state['config_manager'].config

                        app_state['config_manager'].save_config(new_config)

                        # 比较配置变化，只有在配置真正变化时才通知模块重载
                        changed_modules = _get_changed_modules(old_config, new_config)
                        if changed_modules:
                            await notify_modules_config_change(old_config, new_config)
                            response_message = f"Configuration saved and modules notified: {changed_modules}"
                        else:
                            response_message = "Configuration saved (no changes detected)"

                        await websocket.send_text(json.dumps({
                            "type": "response",
                            "request_id": message.get("request_id"),  # 这里是原始的 message 字典参数
                            "payload": {"status": "success", "message": response_message}  # 这里是自定义的响应消息
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({
                            "type": "response",
                            "request_id": message.get("request_id"),  # 这里是原始的 message 字典参数
                            "payload": {"status": "error", "message": str(e)}
                        }))
            elif action == "reload_modules":
                # 通知所有模块重载配置
                await notify_modules_config_change()

                await websocket.send_text(json.dumps({
                    "type": "response",
                    "request_id": message.get("request_id"),
                    "payload": {"status": "success", "message": "Reload modules command sent to all modules"}
                }))
            else:
                await websocket.send_text(json.dumps({
                    "type": "response",
                    "request_id": message.get("request_id"),
                    "payload": {"status": "error", "message": f"Unknown action: {action}"}
                }))
    except WebSocketDisconnect:
        app_state['active_websockets'].remove(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in app_state['active_websockets']:
            app_state['active_websockets'].remove(websocket)


import asyncio
import websockets


async def notify_modules_config_change(old_config=None, new_config=None):
    """通知需要重载配置的模块."""
    if new_config is None:
        # 如果没有提供新旧配置，表示手动触发重载所有模块
        print("Manually reloading all modules...")
        await _reload_all_modules()
        return

    # 比较配置变化，只通知受影响的模块
    changed_modules = _get_changed_modules(old_config, new_config)
    print(f"Configuration changes detected in modules: {changed_modules}")

    for module_name in changed_modules:
        await _notify_module(module_name)


def _get_changed_modules(old_config, new_config):
    """获取配置发生变化的模块列表."""
    changed_modules = []

    # 确保配置是字典类型
    if not isinstance(old_config, dict) or not isinstance(new_config, dict):
        print("Warning: Configuration is not a dictionary, skipping change detection")
        return []

    # 检查 general 配置是否变化（这会影响所有模块）
    if old_config.get('general') != new_config.get('general'):
        # 如果 general 配置变化，通知所有模块
        changed_modules = ['neuro_sama']  # 将来可以扩展更多模块
        return changed_modules

    # 比较各模块的配置
    for module_name in new_config.keys():
        if module_name not in ['general', 'stream']:  # 排除 general 和其他非模块配置
            # 检查特定模块配置是否变化
            if old_config.get(module_name) != new_config.get(module_name):
                changed_modules.append(module_name)

    return changed_modules


async def _notify_module(module_name):
    """通知特定模块重载配置."""
    try:
        # 获取模块配置
        config_dict = app_state['config_manager'].config

        # 确保配置是字典类型
        if not isinstance(config_dict, dict):
            print(f"Error: Configuration is not a dictionary for module {module_name}")
            return

        module_config = config_dict.get(module_name, {})

        # 确保模块配置是字典类型
        if not isinstance(module_config, dict):
            print(f"Error: Module configuration is not a dictionary for module {module_name}")
            return

        server_settings = module_config.get('server_settings', {})

        if not server_settings:
            print(f"Warning: No server settings found for module {module_name}")
            return

        host = server_settings.get('host', '127.0.0.1')
        port = server_settings.get('port', 8001)

        # 构建 WebSocket 管理端点 URL
        ws_url = f"ws://{host}:{port}/ws/admin"

        print(f"Attempting to notify module {module_name} at {ws_url}")

        # 连接模块的管理 WebSocket 端点
        async with websockets.connect(ws_url) as websocket:
            # 发送重载配置请求
            reload_request = {
                "action": "reload_config",
                "request_id": f"reload_{module_name}_{int(time.time())}",
                "payload": {}
            }

            await websocket.send(json.dumps(reload_request))

            # 等待响应（可选，取决于是否需要确认）
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                response_data = json.loads(response)
                print(f"Module {module_name} responded: {response_data}")
            except asyncio.TimeoutError:
                print(f"Warning: No response from module {module_name} within timeout")
            except Exception as e:
                print(f"Warning: Error receiving response from module {module_name}: {e}")

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Warning: Could not connect to module {module_name} at {ws_url}: {e}")
    except Exception as e:
        print(f"Warning: Failed to notify module {module_name}: {e}")


async def _reload_all_modules():
    """重载所有模块."""
    config = app_state['config_manager'].config

    # 获取所有有效的模块（即在配置中有 server_settings 的模块）
    modules_to_reload = []
    for key, value in config.items():
        if key not in ['general'] and isinstance(value, dict) and 'server_settings' in value:
            modules_to_reload.append(key)

    print(f"Reloading all modules: {modules_to_reload}")

    for module_name in modules_to_reload:
        await _notify_module(module_name)


def generate_config_schema(config, title=None):
    """根据当前配置动态生成 schema."""
    if not isinstance(config, dict):
        return {}

    schema = {
        "type": "object",
        "properties": {}
    }

    for key, value in config.items():
        if isinstance(value, dict):
            # 递归处理嵌套字典
            nested_schema = generate_config_schema(value)
            schema["properties"][key] = {
                "type": "object",
                "title": (title + " " if title else "") + key.replace('_', ' ').title() + " Settings",  # 生成标题
                "properties": nested_schema["properties"]
            }
        elif isinstance(value, list):
            # 处理数组
            if value and len(value) > 0:
                first_item = value[0]
                if isinstance(first_item, dict):
                    # 如果数组包含对象，获取第一个对象的结构作为 schema
                    item_schema = generate_config_schema(first_item)
                    schema["properties"][key] = {
                        "type": "array",
                        "title": (title + " " if title else "") + key.replace('_', ' ').title(),  # 生成标题
                        "items": item_schema
                    }
                else:
                    # 简单数组
                    item_type = type(first_item).__name__
                    if item_type == "str":
                        item_type = "string"
                    elif item_type == "int":
                        item_type = "integer"
                    elif item_type == "float":
                        item_type = "number"
                    elif item_type == "bool":
                        item_type = "boolean"

                    schema["properties"][key] = {
                        "type": "array",
                        "title": (title + " " if title else "") + key.replace('_', ' ').title(),  # 生成标题
                        "items": {"type": item_type}
                    }
            else:
                # 空数组，使用通用结构
                schema["properties"][key] = {
                    "type": "array",
                    "title": (title + " " if title else "") + key.replace('_', ' ').title(),  # 生成标题
                }
        else:
            # 处理基本类型
            value_type = type(value).__name__
            if value_type == "str":
                value_type = "string"
            elif value_type == "int":
                value_type = "integer"
            elif value_type == "float":
                value_type = "number"
            elif value_type == "bool":
                value_type = "boolean"

            schema["properties"][key] = {
                "type": value_type,
                "title": (title + " " if title else "") + key.replace('_', ' ').title(),  # 生成标题
                "default": value
            }

    return schema


# 需要导入 time 模块
import time