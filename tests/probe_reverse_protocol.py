"""Inspect the local emulator's reverse setup handshake on an unused test port."""
import socket
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adb_tunnel import CNXN, OPEN, OKAY, CLSE, WRTE, send_packet, read_packet

with socket.socket() as reserve:
    reserve.bind(('127.0.0.1',0))
    port = reserve.getsockname()[1]
with socket.create_connection(('127.0.0.1',21503),timeout=3) as device:
    device.settimeout(3)
    send_packet(device,CNXN,0x01000000,4096,b'host::\0')
    print(read_packet(device)[:3])
    send_packet(device,OPEN,1,0,f'reverse:forward:tcp:{port};tcp:{port}\0'.encode())
    for _ in range(3):
        packet=read_packet(device)
        print(packet)
        if packet[0]==WRTE:
            send_packet(device,OKAY,1,packet[1])
        elif packet[0]==CLSE:
            send_packet(device,CLSE,1,packet[1])
