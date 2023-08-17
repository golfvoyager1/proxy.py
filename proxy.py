#!/usr/bin/env python3

import asyncio
import logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] [%(name)s] [%(asctime)s] %(message)s")
import ipaddress
import socket

chunk_size = 8192
ip_block_list = ["127.0.0.0/8"]
ip_block_list = [ipaddress.ip_network(x) for x in ip_block_list]
def is_blocked_ip(addr):
    addr = ipaddress.ip_address(addr)
    for nw in ip_block_list:
        if addr in nw:
            return True
    return False

def parse_http(http):
    first_line = http.split(b"\r\n", 1)[0]
    method,hostport,_ = first_line.split(maxsplit=2)
    host,port = hostport.split(b":", 1)
    return method, host.decode(), int(port)

async def resolve_host(host, ipv4):
    af = socket.AF_INET if ipv4 else 0
    info = await asyncio.get_running_loop().getaddrinfo(host, 0, family=af)
    return info[0][4][0]

async def accepted_resp(writer):
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    return await writer.drain()

async def rejected_resp(writer):
    writer.write(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
    return await writer.drain()

async def try_close(writer):
    if writer.is_closing():
        return
    try:
        writer.close()
        await writer.wait_closed()
    except Exception as e:
        logging.debug(e, exc_info=True)

def try_shutdown(writer):
    if writer.is_closing():
        return
    try:
        writer.write_eof()
    except Exception as e:
        logging.debug(e, exc_info=True)

async def handle_conn(reader, writer, ipv4):
    client = ":".join(map(str, writer.get_extra_info('peername')))
    logging.debug(f"New connection from {client}")
    try:
        http = bytearray()
        try:
            while not http.endswith(b"\r\n\r\n"):
                http += await reader.read(1024)
        except Exception as e:
            logging.debug(f"Error while reading request from {client}: {repr(e)}")
            return try_close(writer)
        http = bytes(http)
        try:
            method,host,port = parse_http(http)
        except Exception:
            logging.debug(f"Bad request from {client}, {http}")
            return await rejected_resp(writer)
        try:
            host_addr = await resolve_host(host, ipv4)
        except socket.gaierror:
            logging.warning(f"Failed to resolve '{host}'")
            return await rejected_resp(writer)
        if method.lower() != b"connect":
            logging.debug(f"Unsupported {method} request from {client}")
            return await rejected_resp(writer)
        if is_blocked_ip(host_addr):
            logging.debug(f"Access denied from {client} to {host_addr}:{port}({host})")
            return await rejected_resp(writer)
        logging.info(f"Accepted connection from {client}")
        await accepted_resp(writer)
        try:
            rreader, rwriter = await asyncio.open_connection(host_addr, port)
        except Exception:
            logging.warning(f"Failed to connect to {host_addr}:{port}")
            return await rejected_resp(writer)
        try:
            await asyncio.gather(
                pipe(reader, rwriter), pipe(rreader, writer))
        finally:
            await try_close(rwriter)
    finally:
        logging.debug(f"Closing connection from {client}")
        await try_close(writer)

async def pipe(reader, writer):
    try:
        while (buf := await reader.read(chunk_size)):
            writer.write(buf)
            await writer.drain()
        try_shutdown(writer)
    except ConnectionResetError:
        await try_close(writer)

async def start(addr, port, ipv4):
    server = await asyncio.start_server(lambda r,w: handle_conn(r,w,ipv4),
        addr, port)
    addrs = ', '.join(":".join(map(str, sock.getsockname())) for sock in server.sockets)
    logging.info(f"Serving on {addrs}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HTTP proxy server")
    parser.add_argument("address", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", default=8080)
    parser.add_argument("--ipv4", action="store_true", help="IPv4 only")
    parser.add_argument("--log", choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()
    if args.log:
        logging.getLogger().setLevel(getattr(logging, args.log))
    try:
        asyncio.run(start(args.address, args.port, args.ipv4))
    except KeyboardInterrupt:
        logging.info("Shutting down")
