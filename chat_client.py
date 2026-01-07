"""LAN Chat Client for VideoLingo"""
import socket
import threading
import json
import time
from typing import Callable, Optional
from dataclasses import dataclass
import uuid


@dataclass
class ReceivedMessage:
    """Represents a received chat message"""
    sender: str
    original_text: str
    original_language: str
    translated_text: str = ""
    timestamp: float = 0.0
    message_id: str = ""
    target_language: Optional[str] = None


class ChatClient:
    """LAN Chat Client that connects to server and handles messages"""
    
    def __init__(self, server_host='localhost', server_port=8888):
        self.server_host = server_host
        self.server_port = server_port
        self.socket = None
        self.running = False
        self.receive_thread = None
        self.message_callback: Optional[Callable] = None
        self.history_callback: Optional[Callable] = None
        self.user_name = f"User_{uuid.uuid4().hex[:8]}"
        self.preferred_language = "en"
        self.translation_service = TranslationService()
        
    def set_user_name(self, name: str):
        """Set the user's display name"""
        self.user_name = name
    
    def set_preferred_language(self, language: str):
        """Set the user's preferred language for translations"""
        self.preferred_language = language
    
    def set_message_callback(self, callback: Callable):
        """Set callback for new messages"""
        self.message_callback = callback
    
    def set_history_callback(self, callback: Callable):
        """Set callback for message history"""
        self.history_callback = callback
    
    def connect(self) -> bool:
        """Connect to the chat server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.server_host, self.server_port))
            self.running = True
            
            # Start receiving thread
            self.receive_thread = threading.Thread(target=self._receive_messages, daemon=True)
            self.receive_thread.start()
            
            # Request message history
            self._request_history()
            
            print(f"Connected to chat server as {self.user_name}")
            return True
            
        except Exception as e:
            print(f"Failed to connect to chat server: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the chat server"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
    
    def send_message(self, text: str, language: str):
        """Send a chat message"""
        if not self.socket or not self.running:
            return False
        
        try:
            message_data = {
                'type': 'chat_message',
                'sender': self.user_name,
                'text': text,
                'language': language,
                'timestamp': time.time()
            }
            
            self.socket.sendall(
                json.dumps(message_data).encode('utf-8') + b'\n'
            )
            return True
            
        except Exception as e:
            print(f"Failed to send message: {e}")
            return False
    
    def _receive_messages(self):
        """Receive messages from the server"""
        buffer = ""
        while self.running and self.socket:
            try:
                data = self.socket.recv(4096).decode('utf-8')
                if not data:
                    break
                
                buffer += data
                lines = buffer.split('\n')
                
                # Keep the last incomplete line in buffer
                buffer = lines[-1]
                
                for line in lines[:-1]:
                    if line.strip():
                        self._process_received_message(line)
                        
            except Exception as e:
                if self.running:
                    print(f"Error receiving message: {e}")
                break
        
        # Connection lost
        if self.running:
            print("Disconnected from chat server")
            self.running = False
    
    def _process_received_message(self, message_json: str):
        """Process a received message from server"""
        print(111111111111)
        try:
            message_data = json.loads(message_json)
            message_type = message_data.get('type')
            
            if message_type == 'chat_message':
                target_lang = self.preferred_language or message_data.get('language', 'en')
                # Create received message object
                received_msg = ReceivedMessage(
                    sender=message_data.get('sender', 'Unknown'),
                    original_text=message_data['text'],
                    original_language=message_data.get('language', 'en'),
                    timestamp=message_data.get('timestamp', time.time()),
                    message_id=message_data.get('message_id', str(uuid.uuid4())),
                    target_language=target_lang
                )
                received_msg.translated_text = self.translation_service.translate_text(
                    received_msg.original_text,
                    received_msg.original_language,
                    target_lang
                )
                
                # Call message callback if set
                if self.message_callback:
                    self.message_callback(received_msg)
                    
            elif message_type == 'message_history':
                # Process message history
                if self.history_callback:
                    messages = []
                    for msg_data in message_data.get('messages', []):
                        target_lang = self.preferred_language or msg_data.get('language', 'en')
                        msg = ReceivedMessage(
                            sender=msg_data.get('sender', 'Unknown'),
                            original_text=msg_data['text'],
                            original_language=msg_data.get('language', 'en'),
                            timestamp=msg_data.get('timestamp', time.time()),
                            message_id=msg_data.get('message_id', str(uuid.uuid4())),
                            target_language=target_lang
                        )
                        msg.translated_text = self.translation_service.translate_text(
                            msg.original_text,
                            msg.original_language,
                            target_lang
                        )
                        messages.append(msg)
                    self.history_callback(messages)
                    
        except json.JSONDecodeError:
            print(f"Invalid JSON received: {message_json}")
        except Exception as e:
            print(f"Error processing message: {e}")
    
    def _request_history(self):
        """Request message history from server"""
        if not self.socket or not self.running:
            return
        
        try:
            request_data = {
                'type': 'get_history',
                'sender': self.user_name
            }
            
            self.socket.sendall(
                json.dumps(request_data).encode('utf-8') + b'\n'
            )
        except Exception as e:
            print(f"Failed to request history: {e}")


class TranslationService:
    """Service for translating chat messages using the configured LLM API"""
    
    def __init__(self):
        self.available_languages = {
            'en': 'English',
            'zh': '简体中文',
            'ja': '日本語',
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'ru': 'Russian',
            'it': 'Italian',
        }
    
    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text using the configured LLM API
        Returns the translated text or original text if translation fails
        """
        normalized_text = text.strip()
        if not normalized_text:
            return ""
        if source_lang == target_lang:
            return text
        
        try:
            from core.utils import ask_gpt

            source_name = self.get_language_name(source_lang)
            target_name = self.get_language_name(target_lang)
            prompt = (
                "You are a professional translator. "
                f"Translate the following text from {source_name} ({source_lang}) "
                f"to {target_name} ({target_lang}). "
                "The output MUST be written only in the target language/script; "
                "do not use Chinese or any other language unless the target is Chinese. "
                "Preserve the meaning and keep line breaks the same as the input. "
                "Return only the translated text without additional commentary.\n\n"
                "<text>\n"
                f"{text}\n"
                "</text>"
            )

            translated_text = ask_gpt(prompt, resp_type=None, log_title="chat_translate")
            return translated_text.strip()
            
        except Exception as e:
            print(f"Translation failed: {e}")
            # Fallback: return original text
            return text
    
    def get_language_name(self, lang_code: str) -> str:
        """Get display name for language code"""
        return self.available_languages.get(lang_code, lang_code)


if __name__ == "__main__":
    # Test the chat client
    client = ChatClient()
    
    def on_message_received(message: ReceivedMessage):
        print(f"[{message.sender}] {message.original_text}")
    
    def on_history_received(messages: list):
        print(f"Received {len(messages)} historical messages")
    
    client.set_message_callback(on_message_received)
    client.set_history_callback(on_history_received)
    
    if client.connect():
        print("Connected! Type messages (type 'quit' to exit):")
        
        while True:
            message = input("> ")
            if message.lower() == 'quit':
                break
            client.send_message(message, "en")
        
        client.disconnect()
