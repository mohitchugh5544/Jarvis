"""
HTTP Server & Mobile Bridge for Jarvis (Phase 4).

Exposes the JarvisAgent over HTTP for the iPhone Apple Shortcut and other devices.
Runs on 0.0.0.0:8000 with CORS and optional token authentication.

Run:
    python server.py
    (or: uvicorn server:app --host 0.0.0.0 --port 8000)
"""
from contextlib import asynccontextmanager
import socket
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from agent import JarvisAgent
import scheduler


# Single shared agent instance for the session
agent = JarvisAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: start background reminder scheduler
    scheduler.start()
    yield


app = FastAPI(
    title="Jarvis Assistant API",
    description="HTTP endpoint for Jarvis personal assistant (iPhone Shortcut bridge & external tools).",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local network and web front-ends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_auth_token(
    x_jarvis_token: Optional[str] = Header(None, alias="X-Jarvis-Token"),
    authorization: Optional[str] = Header(None),
):
    """Verifies shared secret authentication token if JARVIS_AUTH_TOKEN is set in .env."""
    token = config.JARVIS_AUTH_TOKEN
    if not token:
        return True

    provided = x_jarvis_token
    if not provided and authorization:
        if authorization.startswith("Bearer "):
            provided = authorization[7:].strip()
        else:
            provided = authorization.strip()

    if provided != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing X-Jarvis-Token header.",
        )
    return True


class CommandRequest(BaseModel):
    text: str


class CommandResponse(BaseModel):
    reply: str


@app.post("/command", response_model=CommandResponse, dependencies=[Depends(verify_auth_token)])
def execute_command(req: CommandRequest):
    """Executes a natural language command through the Jarvis agent and returns the spoken reply."""
    user_text = req.text.strip()
    if not user_text:
        return CommandResponse(reply="I didn't hear a command.")
    
    reply = agent.run(user_text)
    return CommandResponse(reply=reply)


@app.get("/health")
def health_check():
    """Health check endpoint to verify server is reachable."""
    return {
        "status": "ok",
        "auth_required": bool(config.JARVIS_AUTH_TOKEN),
    }


def get_network_ips():
    """Detects local LAN and Tailscale IP addresses for quick shortcut configuration."""
    ips = []
    # Local LAN IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        if lan_ip and lan_ip != "127.0.0.1":
            ips.append(("Local Wi-Fi", lan_ip))
    except Exception:
        pass

    # Tailscale IP check
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip.startswith("100."):  # Standard Tailscale CGNAT subnet
                ips.append(("Tailscale", ip))
    except Exception:
        pass

    return ips


def main():
    ips = get_network_ips()
    print("=" * 65)
    print(" JARVIS HTTP SERVER & IPHONE BRIDGE (Phase 4)")
    print("=" * 65)
    print(f" • Local API Docs   : http://localhost:{config.JARVIS_SERVER_PORT}/docs")
    for net_type, ip in ips:
        print(f" • {net_type:<17}: http://{ip}:{config.JARVIS_SERVER_PORT}/command")
    if config.JARVIS_AUTH_TOKEN:
        print(f" • Security         : Token authentication ENABLED (X-Jarvis-Token)")
    else:
        print(f" • Security         : No token set (Open to local network)")
    print("=" * 65 + "\n")

    uvicorn.run(
        app,
        host=config.JARVIS_SERVER_HOST,
        port=config.JARVIS_SERVER_PORT,
    )


if __name__ == "__main__":
    main()
