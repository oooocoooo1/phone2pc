APP_VERSION = "5.5.2"
PROTOCOL_VERSION = 6

SERVER_PORT = 8765
MAX_MESSAGE_SIZE = 2 * 1024 * 1024  # Headroom for 1 MiB file frames
MAX_CLIPBOARD_SIZE = 256 * 1024  # 256 KiB UTF-8 text
MAX_REMOTE_INPUT_SIZE = 64 * 1024  # 64 KiB UTF-8 text
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GiB

FILE_CHUNK_SIZE = 1024 * 1024  # LocalSend-style large streaming blocks
FILE_ACK_WINDOW = 16 * 1024 * 1024  # WebSocket fallback only
