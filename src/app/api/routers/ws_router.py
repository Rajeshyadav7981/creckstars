import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.services.websocket_service import ws_manager

router = APIRouter()


@router.websocket("/ws/match/{match_id}")
async def match_websocket(websocket: WebSocket, match_id: int):
    connected = await ws_manager.connect(websocket, match_id)
    if not connected:
        return
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, match_id)
    except Exception:
        ws_manager.disconnect(websocket, match_id)
