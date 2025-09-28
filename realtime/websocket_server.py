"""
DEPRECATED: Legacy WebSocket Server for Real-time Stock Price Streaming

⚠️ WARNING: This file contains the legacy standalone WebSocket server implementation.

The WebSocket functionality has been moved to fastapi_websocket.py and is now
integrated directly with the FastAPI application on port 8080. This ensures 
compatibility with reverse proxy setups like Caddy without requiring separate ports.

NEW IMPLEMENTATION: See realtime/fastapi_websocket.py
NEW ENDPOINT: /api/realtime/ws (integrated with main FastAPI app)

This file is kept for reference only.
"""

import asyncio
import json
import logging
from typing import Dict, Set, List, Optional
from datetime import datetime
import websockets
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed, WebSocketException

from .models import StockSubscription, PriceUpdate, WebSocketMessage, ErrorResponse
from .price_fetcher import PriceManager

logger = logging.getLogger(__name__)

class WebSocketConnection:
    """Represents a single WebSocket connection with subscription management."""
    
    def __init__(self, websocket: WebSocketServerProtocol, connection_id: str):
        self.websocket = websocket
        self.connection_id = connection_id
        self.subscribed_symbols: Set[str] = set()
        self.user_id: Optional[str] = None
        self.connected_at = datetime.now()
        self.last_ping = datetime.now()
    
    async def send_message(self, message: dict):
        """Send JSON message to client."""
        try:
            await self.websocket.send(json.dumps(message, default=str))
        except (ConnectionClosed, WebSocketException) as e:
            logger.debug(f"Connection {self.connection_id} closed during send: {e}")
            raise
        except Exception as e:
            logger.error(f"Error sending message to {self.connection_id}: {e}")
            raise
    
    async def send_price_update(self, update: PriceUpdate):
        """Send price update to client."""
        message = {
            "type": "price_update",
            "data": update.dict(),
            "timestamp": datetime.now().isoformat()
        }
        await self.send_message(message)
    
    async def send_error(self, error: str, message: str, symbol: str = None):
        """Send error message to client."""
        error_msg = {
            "type": "error",
            "data": {
                "error": error,
                "message": message,
                "symbol": symbol,
                "timestamp": datetime.now().isoformat()
            }
        }
        await self.send_message(error_msg)
    
    def is_subscribed_to(self, symbol: str) -> bool:
        """Check if connection is subscribed to a symbol."""
        return symbol.upper() in self.subscribed_symbols

class WebSocketManager:
    """Manages WebSocket connections and integrates with PriceManager."""
    
    def __init__(self, price_manager: PriceManager):
        self.price_manager = price_manager
        self.connections: Dict[str, WebSocketConnection] = {}
        self.symbol_subscribers: Dict[str, Set[str]] = {}  # symbol -> connection_ids
        self._connection_counter = 0
    
    def _generate_connection_id(self) -> str:
        """Generate unique connection ID."""
        self._connection_counter += 1
        return f"conn_{self._connection_counter}_{int(datetime.now().timestamp())}"
    
    async def handle_connection(self, websocket: WebSocketServerProtocol):
        """Handle new WebSocket connection."""
        connection_id = self._generate_connection_id()
        connection = WebSocketConnection(websocket, connection_id)
        self.connections[connection_id] = connection
        
        logger.info(f"🔌 New WebSocket connection: {connection_id} (total: {len(self.connections)})")
        
        try:
            # Send welcome message
            await connection.send_message({
                "type": "connected",
                "data": {
                    "connection_id": connection_id,
                    "timestamp": datetime.now().isoformat(),
                    "message": "Connected to real-time stock price feed"
                }
            })
            
            # Handle incoming messages
            async for message in websocket:
                await self._handle_message(connection, message)
                
        except ConnectionClosed:
            logger.info(f"🔌 Connection {connection_id} closed normally")
        except WebSocketException as e:
            logger.warning(f"🔌 WebSocket error for {connection_id}: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error for connection {connection_id}: {e}")
        finally:
            await self._cleanup_connection(connection_id)
    
    async def _handle_message(self, connection: WebSocketConnection, message: str):
        """Handle incoming WebSocket message from client."""
        try:
            data = json.loads(message)
            message_type = data.get('type', '').lower()
            
            if message_type == 'subscribe':
                await self._handle_subscription(connection, data)
            elif message_type == 'unsubscribe':
                await self._handle_unsubscription(connection, data)
            elif message_type == 'ping':
                await self._handle_ping(connection)
            elif message_type == 'get_price':
                await self._handle_get_price(connection, data)
            else:
                await connection.send_error(
                    "invalid_message_type",
                    f"Unknown message type: {message_type}"
                )
                
        except json.JSONDecodeError:
            await connection.send_error(
                "invalid_json",
                "Message must be valid JSON"
            )
        except Exception as e:
            logger.error(f"Error handling message from {connection.connection_id}: {e}")
            await connection.send_error(
                "internal_error",
                "Internal server error"
            )
    
    async def _handle_subscription(self, connection: WebSocketConnection, data: dict):
        """Handle subscription request."""
        try:
            symbols = data.get('symbols', [])
            user_id = data.get('user_id')
            
            if not symbols:
                await connection.send_error(
                    "invalid_subscription",
                    "Must provide symbols array"
                )
                return
            
            # Update connection user_id if provided
            if user_id:
                connection.user_id = user_id
            
            # Subscribe to each symbol
            subscribed = []
            for symbol in symbols:
                symbol = symbol.upper()
                
                # Add to connection subscriptions
                connection.subscribed_symbols.add(symbol)
                
                # Add to global symbol subscribers
                if symbol not in self.symbol_subscribers:
                    self.symbol_subscribers[symbol] = set()
                self.symbol_subscribers[symbol].add(connection.connection_id)
                
                # Subscribe to price manager if first subscriber
                if len(self.symbol_subscribers[symbol]) == 1:
                    self.price_manager.subscribe_symbol(
                        symbol, 
                        lambda update, s=symbol: asyncio.create_task(self._broadcast_price_update(s, update))
                    )
                
                subscribed.append(symbol)
            
            # Send confirmation
            await connection.send_message({
                "type": "subscribed",
                "data": {
                    "symbols": subscribed,
                    "total_subscriptions": len(connection.subscribed_symbols),
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            logger.info(f"📊 Connection {connection.connection_id} subscribed to: {subscribed}")
            
            # Send current prices for subscribed symbols
            await self._send_current_prices(connection, subscribed)
            
        except Exception as e:
            logger.error(f"Error in subscription for {connection.connection_id}: {e}")
            await connection.send_error("subscription_error", str(e))
    
    async def _handle_unsubscription(self, connection: WebSocketConnection, data: dict):
        """Handle unsubscription request."""
        try:
            symbols = data.get('symbols', [])
            
            if not symbols:
                # Unsubscribe from all
                symbols = list(connection.subscribed_symbols)
            
            unsubscribed = []
            for symbol in symbols:
                symbol = symbol.upper()
                
                if symbol in connection.subscribed_symbols:
                    # Remove from connection subscriptions
                    connection.subscribed_symbols.discard(symbol)
                    
                    # Remove from global symbol subscribers
                    if symbol in self.symbol_subscribers:
                        self.symbol_subscribers[symbol].discard(connection.connection_id)
                        
                        # Unsubscribe from price manager if no more subscribers
                        if not self.symbol_subscribers[symbol]:
                            self.price_manager.unsubscribe_symbol(symbol)
                            del self.symbol_subscribers[symbol]
                    
                    unsubscribed.append(symbol)
            
            # Send confirmation
            await connection.send_message({
                "type": "unsubscribed",
                "data": {
                    "symbols": unsubscribed,
                    "remaining_subscriptions": len(connection.subscribed_symbols),
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            logger.info(f"📊 Connection {connection.connection_id} unsubscribed from: {unsubscribed}")
            
        except Exception as e:
            logger.error(f"Error in unsubscription for {connection.connection_id}: {e}")
            await connection.send_error("unsubscription_error", str(e))
    
    async def _handle_ping(self, connection: WebSocketConnection):
        """Handle ping message."""
        connection.last_ping = datetime.now()
        await connection.send_message({
            "type": "pong",
            "data": {
                "timestamp": datetime.now().isoformat()
            }
        })
    
    async def _handle_get_price(self, connection: WebSocketConnection, data: dict):
        """Handle immediate price request."""
        try:
            symbol = data.get('symbol', '').upper()
            
            if not symbol:
                await connection.send_error("invalid_request", "Must provide symbol")
                return
            
            price = await self.price_manager.get_current_price(symbol, force_refresh=True)
            
            if price:
                await connection.send_message({
                    "type": "current_price",
                    "data": {
                        "symbol": symbol,
                        "price": price.dict(),
                        "timestamp": datetime.now().isoformat()
                    }
                })
            else:
                await connection.send_error("price_not_found", f"Could not fetch price for {symbol}", symbol)
                
        except Exception as e:
            logger.error(f"Error fetching price for {connection.connection_id}: {e}")
            await connection.send_error("fetch_error", str(e))
    
    async def _send_current_prices(self, connection: WebSocketConnection, symbols: List[str]):
        """Send current prices for symbols to a connection."""
        try:
            prices = await self.price_manager.get_multiple_prices(symbols)
            
            for symbol, price in prices.items():
                if price:
                    await connection.send_message({
                        "type": "current_price",
                        "data": {
                            "symbol": symbol,
                            "price": price.dict(),
                            "timestamp": datetime.now().isoformat()
                        }
                    })
                else:
                    await connection.send_error(
                        "price_unavailable", 
                        f"Price not available for {symbol}", 
                        symbol
                    )
        except Exception as e:
            logger.error(f"Error sending current prices: {e}")
    
    async def _broadcast_price_update(self, symbol: str, update: PriceUpdate):
        """Broadcast price update to all subscribers of a symbol."""
        if symbol not in self.symbol_subscribers:
            return
        
        # Get all connection IDs subscribed to this symbol
        connection_ids = list(self.symbol_subscribers[symbol])
        
        # Send update to each connection
        for connection_id in connection_ids:
            if connection_id in self.connections:
                connection = self.connections[connection_id]
                try:
                    await connection.send_price_update(update)
                except (ConnectionClosed, WebSocketException):
                    # Connection is closed, will be cleaned up later
                    pass
                except Exception as e:
                    logger.error(f"Error sending price update to {connection_id}: {e}")
    
    async def _cleanup_connection(self, connection_id: str):
        """Clean up connection and unsubscribe from all symbols."""
        if connection_id not in self.connections:
            return
        
        connection = self.connections[connection_id]
        
        # Unsubscribe from all symbols
        for symbol in list(connection.subscribed_symbols):
            if symbol in self.symbol_subscribers:
                self.symbol_subscribers[symbol].discard(connection_id)
                
                # Unsubscribe from price manager if no more subscribers
                if not self.symbol_subscribers[symbol]:
                    self.price_manager.unsubscribe_symbol(symbol)
                    del self.symbol_subscribers[symbol]
        
        # Remove connection
        del self.connections[connection_id]
        
        logger.info(f"🧹 Cleaned up connection {connection_id} (remaining: {len(self.connections)})")
    
    def get_stats(self) -> dict:
        """Get WebSocket server statistics."""
        return {
            "total_connections": len(self.connections),
            "subscribed_symbols": len(self.symbol_subscribers),
            "connections": [
                {
                    "id": conn_id,
                    "subscriptions": len(conn.subscribed_symbols),
                    "user_id": conn.user_id,
                    "connected_at": conn.connected_at.isoformat()
                }
                for conn_id, conn in self.connections.items()
            ]
        }

# NOTE: This file contains the legacy WebSocket server implementation.
# The WebSocket functionality has been moved to fastapi_websocket.py
# and integrated directly with the FastAPI application on the same port (8080).
# This ensures compatibility with reverse proxy setups like Caddy.

# DEPRECATED: create_websocket_server function has been replaced by
# FastAPI-integrated WebSocket endpoints in fastapi_websocket.py