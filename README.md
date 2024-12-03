
## My Pipelines Ranking

### 1. UltraDP
Most Fast in My Project.
**PEEK SPEED:** 2.46GB / 3sec

### 2. FastDP | NextDP(NextDrop)
**PEEK SPEED:** 3GB / 5sec


## FastDrop Protocol (fastdp) Specification

### Overview

**Protocol Name:** FastDrop Protocol (`fastdp`)  
**Version:** 1.1.0  
**Goal:** The fastest protocol in the world

### Table of Contents

1. [Protocol Handshake](#protocol-handshake)
2. [FastDP Header](#fastdp-header)
3. [Data Transmission Rules](#data-transmission-rules)
4. [Compression Types](#compression-types)
5. [Connection Management](#connection-management)
6. [Example Workflow](#example-workflow)

---

### Protocol Handshake

FastDP initiates communication by upgrading an existing HTTP connection. The handshake involves specific HTTP headers to negotiate the transition from HTTP to FastDP.

**Client Request:**

```http
GET /fastdp-endpoint HTTP/1.1
Host: example.com
Upgrade: fastdp
Connection: Upgrade
Sec-FastDP-Key: <unique-key>
Sec-FastDP-Version: 1.0.0
```

**Server Response:**

```http
HTTP/1.1 101 Switching Protocols
Upgrade: fastdp
Connection: Upgrade
Sec-FastDP-Key: <unique-key>
Sec-FastDP-Version: 1.0.0
```

*Upon successful handshake, both client and server switch to FastDP for further communication.*

---

### FastDP Header

After the handshake, both parties exchange FastDP Headers to establish the parameters for data transmission.

**FastDP Header Fields:**

1. **OneChunk** (`int`):  
   The size of each data chunk.  
   - **Calculation:**  
     - **Small Files:** Smaller chunk sizes to optimize speed.
     - **Large Files:** Larger chunk sizes to maximize throughput.

2. **FullChunk** (`int`):  
   Total number of chunks, calculated as:  
   `FullChunk = Total File Size / OneChunk`

3. **FullSize** (`int`):  
   Total size of the file in bytes.

4. **DPVersion** (`str`):  
   Protocol version (e.g., `"1.0.0"`).

5. **CompressType** (`str`):  
   Compression algorithm used.  
   - **Options:** `["None", "Zstd", "Zip", "TarGz"]`
   
6. **FileName** (`str`):  
   Name of the file being transmitted.

**Example FastDP Header (JSON Format):**

```json
{
  "OneChunk": 1048576,
  "FullChunk": 100,
  "FullSize": 104857600,
  "DPVersion": "1.0.0",
  "CompressType": "Zstd",
  "FileName": "example.txt"
}
```

---

### Data Transmission Rules

FastDP employs specific rules to ensure efficient and reliable data transmission:

1. **Synchronization and Reconnection:**
   - **Frequency:** Every 0.5 GB of data transmitted.
   - **Procedure:**  
     - Notify the peer to reconnect.
     - Disconnect the current connection.
     - Re-establish the connection to manage memory usage and prevent timeouts.

2. **Chunk Management:**
   - **Sending:**  
     - Utilize multithreading to send chunks concurrently.
     - Assign and share a unique chunk number before sending each chunk.
   - **Receiving:**  
     - Receive chunks in any order due to multithreading.
     - Store chunks with their corresponding numbers.
     - After all chunks are received, reorder them sequentially (`1, 2, 3, ..., n`) to reconstruct the original file.

3. **Prioritizing Speed with Safety:**
   - **Transport Layer:**  
     - Use TCP to ensure reliable data transfer.
   - **Optimization:**  
     - Maximize throughput by optimizing chunk sizes and managing concurrent transmissions.

---

### Compression Types

FastDP supports various compression algorithms to optimize data transfer:

1. **None:**  
   No compression applied.

2. **Zstd:**  
   High-speed compression algorithm providing a good balance between speed and compression ratio.

3. **Zip:**  
   Widely-used compression format with moderate speed and compression.

4. **TarGz:**  
   Combines TAR packaging with Gzip compression for efficient file bundling and compression.

*The choice of compression affects both transmission speed and data size. Clients and servers should agree on the compression type during the FastDP Header exchange.*

---

### Connection Management

Efficient connection management is crucial for maintaining high-speed data transfers:

1. **Initial Connection:**
   - Establish an HTTP connection and perform the FastDP handshake.

2. **Data Transfer:**
   - Exchange FastDP Headers.
   - Begin data transmission following FastDP rules.

3. **Periodic Reconnection:**
   - After transmitting 0.5 GB of data:
     - Notify the peer to prepare for reconnection.
     - Gracefully disconnect the current connection.
     - Re-establish the FastDP connection to continue data transfer.

4. **Error Handling:**
   - Implement retry mechanisms for failed chunk transmissions.
   - Ensure data integrity through checksum verification or similar methods.

---

### Example Workflow

1. **Handshake:**
   - Client sends an HTTP request with `Upgrade: fastdp`.
   - Server responds with `101 Switching Protocols`.

2. **Header Exchange:**
   - Both parties exchange FastDP Headers detailing chunk sizes, total size, protocol version, and compression type.

3. **Data Transmission:**
   - Client and server begin sending and receiving data chunks concurrently.
   - Each chunk is prefixed with its chunk number.
   - Chunks are received in any order and stored accordingly.

4. **Reconnection Cycle:**
   - After 0.5 GB of data, both parties disconnect and reconnect to continue the transfer seamlessly.

5. **Completion:**
   - Once all chunks are received, they are reordered to reconstruct the original file.
   - Verification ensures data integrity.

---

## FastDrop Protocol (fastdp) Python Implementation

Below is a Python implementation of the FastDrop Protocol (`fastdp`). The implementation includes both the server and client components, enabling file transfer with the specified protocol features.

### Dependencies

- Python 3.7+
- `zstandard` library for Zstd compression (optional)
- `asyncio` for asynchronous networking
- `struct` and `json` for data handling

Install dependencies using:

```bash
pip install zstandard
```

### Server Implementation (`fastdp_server.py`)

```python
import asyncio
import json
import struct
import zstandard as zstd

from asyncio import StreamReader, StreamWriter

# Constants
PROTOCOL_VERSION = "1.0.0"
COMPRESSION_TYPES = ["None", "Zstd", "Zip", "TarGz"]
CHUNK_THRESHOLD = 500 * 1024 * 1024  # 0.5 GB

class FastDPServer:
    def __init__(self, host='0.0.0.0', port=8888, compress_type='None'):
        self.host = host
        self.port = port
        self.compress_type = compress_type
        self.clients = []

    async def handle_client(self, reader: StreamReader, writer: StreamWriter):
        try:
            # Perform HTTP FastDP handshake
            headers = await self.read_http_headers(reader)
            if not self.validate_handshake(headers):
                writer.close()
                await writer.wait_closed()
                return

            # Send HTTP 101 Switching Protocols response
            await self.send_handshake_response(writer, headers)

            # Exchange FastDP Headers
            fastdp_header = await self.receive_fastdp_header(reader)
            # Prepare FastDP Header for response
            response_header = {
                "OneChunk": self.determine_chunk_size(fastdp_header['FullSize']),
                "FullChunk": (fastdp_header['FullSize'] // fastdp_header.get('OneChunk', 1048576)) + 1,
                "FullSize": fastdp_header['FullSize'],
                "DPVersion": PROTOCOL_VERSION,
                "CompressType": self.compress_type
            }
            await self.send_fastdp_header(writer, response_header)

            # Start receiving file chunks
            await self.receive_file(reader, writer, response_header)
        except Exception as e:
            print(f"Error handling client: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def read_http_headers(self, reader: StreamReader):
        headers = {}
        while True:
            line = await reader.readline()
            if line == b'\r\n':
                break
            key, value = line.decode().strip().split(":", 1)
            headers[key.strip()] = value.strip()
        return headers

    def validate_handshake(self, headers):
        return (
            headers.get("Upgrade", "").lower() == "fastdp" and
            "Sec-FastDP-Key" in headers and
            "Sec-FastDP-Version" in headers and
            headers["Sec-FastDP-Version"] == PROTOCOL_VERSION
        )

    async def send_handshake_response(self, writer: StreamWriter, headers):
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: fastdp\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-FastDP-Key: {headers['Sec-FastDP-Key']}\r\n"
            f"Sec-FastDP-Version: {PROTOCOL_VERSION}\r\n"
            "\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

    async def receive_fastdp_header(self, reader: StreamReader):
        # Assume the header is sent as a JSON string with length prefix
        length_bytes = await reader.readexactly(4)
        length = struct.unpack('!I', length_bytes)[0]
        header_bytes = await reader.readexactly(length)
        header = json.loads(header_bytes.decode())
        return header

    async def send_fastdp_header(self, writer: StreamWriter, header: dict):
        header_json = json.dumps(header).encode()
        writer.write(struct.pack('!I', len(header_json)) + header_json)
        await writer.drain()

    def determine_chunk_size(self, full_size):
        if full_size <= 10 * 1024 * 1024:  # 10 MB
            return 1024 * 256  # 256 KB
        elif full_size <= 1 * 1024 * 1024 * 1024:  # 1 GB
            return 1024 * 1024  # 1 MB
        else:
            return 4 * 1024 * 1024  # 4 MB

    async def receive_file(self, reader: StreamReader, writer: StreamWriter, header: dict):
        one_chunk = header['OneChunk']
        full_chunk = header['FullChunk']
        full_size = header['FullSize']
        compress_type = header['CompressType']

        received_chunks = {}
        bytes_received = 0

        while len(received_chunks) < full_chunk:
            # Read chunk header: 4 bytes chunk number + 4 bytes chunk size
            chunk_header = await reader.readexactly(8)
            chunk_num, chunk_size = struct.unpack('!II', chunk_header)
            # Read chunk data
            chunk_data = await reader.readexactly(chunk_size)
            received_chunks[chunk_num] = chunk_data
            bytes_received += chunk_size

            # Handle periodic reconnection
            if bytes_received >= CHUNK_THRESHOLD:
                # Notify client to reconnect
                await self.notify_reconnect(writer)
                bytes_received = 0

        # Reassemble file
        file_data = b''.join(received_chunks[i] for i in sorted(received_chunks))
        # Decompress if needed
        if compress_type == "Zstd":
            dctx = zstd.ZstdDecompressor()
            file_data = dctx.decompress(file_data)
        elif compress_type == "None":
            pass
        else:
            # Implement other compression types as needed
            pass

        # Save the received file
        with open('received_file', 'wb') as f:
            f.write(file_data)
        print("File received and saved as 'received_file'.")

    async def notify_reconnect(self, writer: StreamWriter):
        # Simple notification protocol: send a special message
        notification = b'RECONNECT'
        writer.write(notification)
        await writer.drain()
        # Close the connection to force reconnection
        writer.close()
        await writer.wait_closed()

    async def start_server(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f'FastDP Server listening on {self.host}:{self.port}')
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    server = FastDPServer(host='0.0.0.0', port=8888, compress_type='Zstd')
    asyncio.run(server.start_server())
```

### Client Implementation (`fastdp_client.py`)

```python
import asyncio
import json
import struct
import zstandard as zstd
import os

from asyncio import StreamReader, StreamWriter

# Constants
PROTOCOL_VERSION = "1.0.0"
COMPRESSION_TYPES = ["None", "Zstd", "Zip", "TarGz"]
CHUNK_THRESHOLD = 500 * 1024 * 1024  # 0.5 GB

class FastDPClient:
    def __init__(self, host='127.0.0.1', port=8888, file_path='file_to_send', compress_type='Zstd'):
        self.host = host
        self.port = port
        self.file_path = file_path
        self.compress_type = compress_type

    async def connect(self):
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                await self.perform_handshake(reader, writer)
                await self.exchange_fastdp_headers(reader, writer)
                await self.send_file(reader, writer)
            except Exception as e:
                print(f"Connection error: {e}")
            await asyncio.sleep(1)  # Wait before reconnecting

    async def perform_handshake(self, reader: StreamReader, writer: StreamWriter):
        # Send HTTP FastDP handshake request
        handshake = (
            f"GET /fastdp-endpoint HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: fastdp\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-FastDP-Key: unique-key-123\r\n"
            f"Sec-FastDP-Version: {PROTOCOL_VERSION}\r\n"
            f"\r\n"
        )
        writer.write(handshake.encode())
        await writer.drain()

        # Read HTTP response
        response = await self.read_http_response(reader)
        if not self.validate_handshake_response(response):
            raise Exception("Failed to switch to FastDP protocol.")

    async def read_http_response(self, reader: StreamReader):
        response = b''
        while True:
            line = await reader.readline()
            response += line
            if line == b'\r\n':
                break
        return response.decode()

    def validate_handshake_response(self, response):
        return "101 Switching Protocols" in response and "Upgrade: fastdp" in response

    async def exchange_fastdp_headers(self, reader: StreamReader, writer: StreamWriter):
        # Prepare FastDP Header
        file_size = os.path.getsize(self.file_path)
        one_chunk = self.determine_chunk_size(file_size)
        full_chunk = (file_size // one_chunk) + 1

        header = {
            "OneChunk": one_chunk,
            "FullChunk": full_chunk,
            "FullSize": file_size,
            "DPVersion": PROTOCOL_VERSION,
            "CompressType": self.compress_type
        }

        # Send FastDP Header
        await self.send_fastdp_header(writer, header)

        # Receive FastDP Header from server
        server_header = await self.receive_fastdp_header(reader)
        # Optionally handle server's header if needed

    def determine_chunk_size(self, full_size):
        if full_size <= 10 * 1024 * 1024:  # 10 MB
            return 1024 * 256  # 256 KB
        elif full_size <= 1 * 1024 * 1024 * 1024:  # 1 GB
            return 1024 * 1024  # 1 MB
        else:
            return 4 * 1024 * 1024  # 4 MB

    async def send_fastdp_header(self, writer: StreamWriter, header: dict):
        header_json = json.dumps(header).encode()
        writer.write(struct.pack('!I', len(header_json)) + header_json)
        await writer.drain()

    async def receive_fastdp_header(self, reader: StreamReader):
        length_bytes = await reader.readexactly(4)
        length = struct.unpack('!I', length_bytes)[0]
        header_bytes = await reader.readexactly(length)
        header = json.loads(header_bytes.decode())
        return header

    async def send_file(self, reader: StreamReader, writer: StreamWriter):
        file_size = os.path.getsize(self.file_path)
        one_chunk = (await self.exchange_fastdp_headers(reader, writer))['OneChunk']
        compress_type = self.compress_type

        # Read and compress the file
        with open(self.file_path, 'rb') as f:
            file_data = f.read()

        if compress_type == "Zstd":
            cctx = zstd.ZstdCompressor()
            file_data = cctx.compress(file_data)
        elif compress_type == "None":
            pass
        else:
            # Implement other compression types as needed
            pass

        # Split into chunks
        chunks = [file_data[i:i+one_chunk] for i in range(0, len(file_data), one_chunk)]
        total_chunks = len(chunks)
        bytes_sent = 0

        for chunk_num, chunk in enumerate(chunks, start=1):
            # Send chunk header: 4 bytes chunk number + 4 bytes chunk size
            chunk_header = struct.pack('!II', chunk_num, len(chunk))
            writer.write(chunk_header + chunk)
            await writer.drain()
            bytes_sent += len(chunk)

            # Handle periodic reconnection
            if bytes_sent >= CHUNK_THRESHOLD:
                print("Reached 0.5 GB, reconnecting...")
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(1)  # Wait before reconnecting
                await self.connect()
                return  # Exit current send_file to restart

        print("File sent successfully.")

    async def run(self):
        await self.connect()

if __name__ == "__main__":
    client = FastDPClient(host='127.0.0.1', port=8888, file_path='file_to_send', compress_type='Zstd')
    asyncio.run(client.run())
```

### Usage Instructions

1. **Prepare the Server:**
   - Save the server code to a file named `fastdp_server.py`.
   - Ensure the server has access to sufficient resources and the desired compression libraries.
   - Run the server:

     ```bash
     python fastdp_server.py
     ```

2. **Prepare the Client:**
   - Save the client code to a file named `fastdp_client.py`.
   - Place the file you wish to send in the same directory and name it `file_to_send` (or modify the `file_path` parameter accordingly).
   - Run the client:

     ```bash
     python fastdp_client.py
     ```

3. **File Transfer:**
   - The client will connect to the server, perform the FastDP handshake, exchange headers, and begin transmitting the file in chunks.
   - Upon completion, the server saves the received file as `received_file`.

### Notes and Considerations

- **Compression Support:**
  - The provided implementation includes support for `None` and `Zstd` compression types.
  - Additional compression types like `Zip` and `TarGz` can be implemented as needed.

- **Error Handling:**
  - The current implementation includes basic error handling. For production use, consider enhancing exception management, retries, and data integrity checks.

- **Performance Optimization:**
  - For enhanced performance, especially with large files, consider implementing asynchronous chunk sending and receiving with concurrency controls.

- **Security:**
  - The current protocol does not include authentication or encryption. For secure data transmission, integrate TLS and authentication mechanisms.

- **Scalability:**
  - The server is designed for simplicity. For handling multiple clients concurrently, consider integrating more robust server architectures or leveraging existing frameworks.

- **Extensibility:**
  - The protocol is extensible. Future versions can incorporate features like bidirectional communication, streaming data, or advanced compression algorithms.
