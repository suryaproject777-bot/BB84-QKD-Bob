"""Alice BB84 over physical SX126X LoRa; original Wi-Fi script unchanged."""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ServerClientBB84.RBpi.bb84_serverClient_alice import AliceServer, ChannelCollector, QuantumChannel
from ServerClientBB84.RBpi.lora_integration.lora_transport import LoRaTransport, encode_message, decode_message

async def main():
    p = argparse.ArgumentParser()
    p.add_argument('--qubits', type=int, default=20000)
    p.add_argument('--port', default='/dev/ttyAMA0')
    p.add_argument('--mu', type=float, default=0.1)
    p.add_argument('--length-km', type=float, default=10.0)
    p.add_argument('--attenuation-db', type=float, default=0.2)
    p.add_argument('--optical-error', type=float, default=0.01)
    args = p.parse_args()
    collector = ChannelCollector('Collector')
    channel = QuantumChannel('Fiber', args.length_km, args.attenuation_db,
                             args.optical_error, next_actor=collector, seed=1003)
    alice = AliceServer('AliceServer', channel, collector, num_qubits=args.qubits,
                        mu=args.mu, verbose=True, seed=1001)
    actors = [collector, channel, alice]
    tasks = [asyncio.create_task(a.start()) for a in actors]
    radio = LoRaTransport('alice', port=args.port)
    print('[ALICE] BB84 simulation over physical LoRa; waiting for Bob...')
    try:
        while True:
            req = decode_message(await asyncio.to_thread(radio.receive))
            endpoint = req.get('endpoint')
            if endpoint == '/close':
                print('[ALICE] Bob closed the session')
                break
            resp = await alice.handle_api_request(endpoint, req.get('data', {}))
            await asyncio.to_thread(radio.send, encode_message(resp))
    finally:
        radio.close()
        for a in actors:
            await a.send(a, ('STOP',))
        await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
