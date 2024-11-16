import argparse
import asyncio
import os
import json
import struct
import zstandard as zstd
import zipfile
import tarfile
import io
import hashlib
from asyncio import StreamReader, StreamWriter
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
import logging

# Constants
PROTOCOL_VERSION = "1.1.0"
COMPRESSION_TYPES = ["None", "Zstd", "Zip", "TarGz"]

class FastDPServer:
    def __init__(self, host='0.0.0.0', port=8888, compress_type='None', output_dir="./"):
        self.host = host
        self.port = port
        self.compress_type = compress_type
        self.console = Console()
        self.lock = asyncio.Lock()
        self.active_transfers = {}  # Key: client identifier, Value: received_chunks
        self.filename = "received_file"
        self.output_dir = output_dir

    async def handle_client(self, reader: StreamReader, writer: StreamWriter):
        client_id = writer.get_extra_info('peername')
        self.active_transfers[client_id] = {}
        self.console.log(f"Handling new client: {client_id}")
        try:
            headers = await self.read_http_headers(reader)
            self.console.log(f"Received headers from {client_id}: {headers}")
            if not self.validate_handshake(headers):
                self.console.log(f"Invalid handshake from {client_id}. Closing connection.")
                await self.close_connection(writer)
                return

            await self.send_handshake_response(writer, headers)
            self.console.log(f"Sent handshake response to {client_id}")

            # Receive the header containing file transfer details
            client_header = await self.receive_fastdp_header(reader)
            self.console.log(f"Received FastDP header from {client_id}: {client_header}")

            # Proceed to receive the file without sending an acknowledgment
            await self.receive_file(reader, writer, client_header, client_header['Checksum'], client_id)
        except Exception as e:
            print(f"Error handling client {client_id}: {e}")
        finally:
            await self.close_connection(writer)
            self.active_transfers.pop(client_id, None)

    async def read_http_headers(self, reader: StreamReader):
        headers = {}
        while True:
            line = await reader.readline()
            if line == b'\r\n':
                break
            if b':' in line:
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
        try:
            length_bytes = await reader.readexactly(4)
            length = struct.unpack('!I', length_bytes)[0]
            header_bytes = await reader.readexactly(length)
            header = json.loads(header_bytes.decode())
            return header
        except asyncio.IncompleteReadError:
            raise Exception("Incomplete FastDP header received.")
        except json.JSONDecodeError:
            raise Exception("Invalid JSON format in FastDP header.")

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

    async def receive_file(self, reader: StreamReader, writer: StreamWriter, header: dict, expected_checksum: str, client_id):
        one_chunk = header['OneChunk']
        full_chunk = header['FullChunk']
        full_size = header['FullSize']
        compress_type = header['CompressType']
        file_name = header.get('FileName', 'received_file')
        self.filename = file_name

        received_chunks = {}
        bytes_received = 0
        checksum = hashlib.sha256()

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=self.console
        ) as progress:
            task = progress.add_task("Receiving file...", total=full_size)

            while len(received_chunks) < full_chunk:
                try:
                    header_data = await reader.readexactly(8)
                    chunk_num, chunk_size = struct.unpack('!II', header_data)
                    chunk_data = await reader.readexactly(chunk_size)
                    received_chunks[chunk_num] = chunk_data
                    bytes_received += chunk_size
                    checksum.update(chunk_data)
                    progress.update(task, advance=chunk_size)
                except asyncio.IncompleteReadError:
                    await self.send_error_notification(writer, "Incomplete chunk data.")
                    return
                except struct.error:
                    await self.send_error_notification(writer, "Malformed chunk header.")
                    return

        # Combine chunks and verify checksum
        file_data = b''.join(received_chunks[i] for i in sorted(received_chunks.keys()))
        if checksum.hexdigest() != expected_checksum:
            await self.send_error_notification(writer, "Checksum mismatch.")
            return

        # Decompress if necessary
        if compress_type == "Zstd":
            dctx = zstd.ZstdDecompressor()
            file_data = dctx.decompress(file_data)
        elif compress_type == "Zip":
            with zipfile.ZipFile(io.BytesIO(file_data), 'r') as zip_file:
                file_name_in_archive = zip_file.namelist()[0]
                file_data = zip_file.read(file_name_in_archive)
        elif compress_type == "TarGz":
            with tarfile.open(fileobj=io.BytesIO(file_data), mode='r:gz') as tar_file:
                member = tar_file.getmembers()[0]
                file_data = tar_file.extractfile(member).read()

        # Ensure file_name is not None
        if file_name:
            filename = file_name

        # Save the file
        output_path = os.path.join(self.output_dir, filename)
        with open(output_path, 'wb') as f:
            f.write(file_data)
        progress.console.log(f"File saved to {output_path}")

        # Notify client of successful receipt
        success_message = "File received successfully.".encode()
        writer.write(success_message)
        await writer.drain()

    def variable_path_to_path(self, variable_path):
        return os.path.expanduser(variable_path)

    async def send_error_notification(self, writer: StreamWriter, message: str):
        error_message = f"ERROR: {message}".encode()
        writer.write(error_message)
        await writer.drain()
        await self.close_connection(writer)

    async def close_connection(self, writer: StreamWriter):
        writer.close()
        await writer.wait_closed()

    async def start_server(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f'FastDP Server listening on {self.host}:{self.port}')
        async with server:
            await server.serve_forever()

class FastDPClient:
    def __init__(self, host='127.0.0.1', port=8888, file_path='file_to_send', compress_type='Zstd'):
        self.host = host
        self.port = port
        self.file_path = file_path
        self.compress_type = compress_type
        self.console = Console()
        self.checksum = hashlib.sha256()

    async def perform_handshake(self, reader: StreamReader, writer: StreamWriter):
        handshake_request = (
            f"GET /fastdp-endpoint HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: fastdp\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-FastDP-Key: some-random-key\r\n"
            f"Sec-FastDP-Version: {PROTOCOL_VERSION}\r\n"
            "\r\n"
        )
        writer.write(handshake_request.encode())
        await writer.drain()

        # Read response headers
        headers = {}
        while True:
            line = await reader.readline()
            if line == b'\r\n':
                break
            if b':' in line:
                key, value = line.decode().strip().split(":", 1)
                headers[key.strip()] = value.strip()

        # Validate handshake response
        if headers.get("Sec-FastDP-Version") != PROTOCOL_VERSION:
            raise Exception("Handshake failed: Protocol version mismatch")

    async def connect(self):
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            await self.perform_handshake(reader, writer)
            compressed_data = await self.exchange_fastdp_headers(reader, writer)
            await self.send_file(reader, writer, compressed_data)
        except Exception as e:
            print(f"Connection error: {e}")

    async def exchange_fastdp_headers(self, reader: StreamReader, writer: StreamWriter):
        # Read and compress the file first
        with open(self.file_path, 'rb') as f:
            file_data = f.read()

        # Compress if necessary
        if self.compress_type == "Zstd":
            cctx = zstd.ZstdCompressor()
            file_data = cctx.compress(file_data)
        elif self.compress_type == "Zip":
            with io.BytesIO() as zip_buffer:
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    zip_file.writestr(os.path.basename(self.file_path), file_data)
                file_data = zip_buffer.getvalue()
        elif self.compress_type == "TarGz":
            with io.BytesIO() as tar_buffer:
                with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar_file:
                    tar_info = tarfile.TarInfo(name=os.path.basename(self.file_path))
                    tar_info.size = len(file_data)
                    tar_file.addfile(tar_info, io.BytesIO(file_data))
                file_data = tar_buffer.getvalue()
        else:
            # If not compressed, file_data remains as is
            pass

        # Calculate checksum on compressed data
        self.checksum.update(file_data)
        
        # Now calculate FullSize and FullChunk based on compressed data
        file_size = len(file_data)
        one_chunk = self.determine_chunk_size(file_size)
        full_chunk = (file_size // one_chunk) + 1 if file_size % one_chunk != 0 else file_size // one_chunk

        header = {
            "OneChunk": one_chunk,
            "FullChunk": full_chunk,
            "FullSize": file_size,
            "DPVersion": PROTOCOL_VERSION,
            "CompressType": self.compress_type,
            "FileName": os.path.basename(self.file_path) or self.file_path.split("/")[-1],
            "Checksum": self.calculate_checksum()
        }

        # Send header to server
        await self.send_fastdp_header(writer, header)
        # Proceed to send file
        return file_data  # Return compressed data for sending

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

    def determine_chunk_size(self, full_size):
        if full_size <= 10 * 1024 * 1024:  # 10 MB
            return 1024 * 256  # 256 KB
        elif full_size <= 1 * 1024 * 1024 * 1024:  # 1 GB
            return 1024 * 1024  # 1 MB
        else:
            return 4 * 1024 * 1024  # 4 MB

    def calculate_checksum(self):
        return self.checksum.hexdigest()

    async def send_file(self, reader: StreamReader, writer: StreamWriter, server_header: dict):
        self.console.log("Starting file transmission.")
        one_chunk = server_header['OneChunk']
        compress_type = server_header['CompressType']

        with open(self.file_path, 'rb') as f:
            file_data = f.read()
            self.checksum.update(file_data)

        if compress_type == "Zstd":
            cctx = zstd.ZstdCompressor()
            file_data = cctx.compress(file_data)
        elif compress_type == "Zip":
            with io.BytesIO() as zip_buffer:
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    zip_file.writestr(os.path.basename(self.file_path), file_data)
                file_data = zip_buffer.getvalue()
        elif compress_type == "TarGz":
            with io.BytesIO() as tar_buffer:
                with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar_file:
                    tar_info = tarfile.TarInfo(name=os.path.basename(self.file_path))
                    tar_info.size = len(file_data)
                    tar_file.addfile(tar_info, io.BytesIO(file_data))
                file_data = tar_buffer.getvalue()

        chunks = [file_data[i:i+one_chunk] for i in range(0, len(file_data), one_chunk)]

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=self.console
        ) as progress:
            task = progress.add_task("Sending file...", total=len(file_data))

            for chunk_num, chunk in enumerate(chunks, start=0):
                logging.debug(f"Sending chunk {chunk_num}...")
                chunk_header = struct.pack('!II', chunk_num, len(chunk))
                writer.write(chunk_header + chunk)
                await writer.drain()
                progress.update(task, advance=len(chunk))

        print("File sent successfully.")
        progress.update(task, completed=len(file_data))

    async def send_file(self, reader: StreamReader, writer: StreamWriter, compressed_data: bytes):
        self.console.log("Starting file transmission.")
        one_chunk = self.determine_chunk_size(len(compressed_data))
        chunks = [compressed_data[i:i+one_chunk] for i in range(0, len(compressed_data), one_chunk)]

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=self.console
        ) as progress:
            task = progress.add_task("Sending file...", total=len(compressed_data))

            for chunk_num, chunk in enumerate(chunks):
                chunk_header = struct.pack('!II', chunk_num, len(chunk))
                writer.write(chunk_header + chunk)
                await writer.drain()
                progress.update(task, advance=len(chunk))

        progress.update(task, completed=len(compressed_data))

class ChecksumManager:
    @staticmethod
    def compute_file_checksum(file_path):
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

def main():

    parser = argparse.ArgumentParser(description="FastDP Command Line Tool")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    subparsers = parser.add_subparsers(dest="command")

    server_parser = subparsers.add_parser("server", aliases=["receive"], help="Start FastDP server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host to bind the server")
    server_parser.add_argument("--port", type=int, default=5423, help="Port to bind the server")
    server_parser.add_argument("--compress", choices=COMPRESSION_TYPES, default="Zstd", help="Compression type")
    server_parser.add_argument("--output", help="Output directory for received files", default="./")

    client_parser = subparsers.add_parser("client", aliases=["send"], help="Start FastDP client")
    client_parser.add_argument("--host", default="127.0.0.1", help="Server host to connect to")
    client_parser.add_argument("--port", type=int, default=5423, help="Server port to connect to")
    client_parser.add_argument("--file", required=True, help="File to send")
    client_parser.add_argument("--compress", choices=COMPRESSION_TYPES, default="Zstd", help="Compression type")

    args = parser.parse_args()


    logging.basicConfig(
        level=(logging.DEBUG if args.debug else logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler()]
    )

    if args.command in ["server", "receive"]:
        server = FastDPServer(host=args.host, port=args.port, compress_type=args.compress, output_dir=args.output)
        asyncio.run(server.start_server())
    elif args.command in ["client", "send"]:
        client = FastDPClient(host=args.host, port=args.port, file_path=args.file, compress_type=args.compress)
        asyncio.run(client.connect())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()