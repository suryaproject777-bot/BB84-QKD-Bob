"""Bob BB84 over physical SX126X LoRa; original Wi-Fi script unchanged."""
import argparse
import asyncio
import sys
import time
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ServerClientBB84.prng import Basis
from ServerClientBB84.RBpi.bb84_serverClient_bob import (BobClient, Detector, Photon, NetworkAPIClient)
from ServerClientBB84.RBpi.lora_integration.lora_transport import LoRaTransport, encode_message, decode_message

class LoRaAPIClient(NetworkAPIClient):
    def __init__(self, radio):
        self.radio = radio
        self.tx_bytes = 0
        self.rx_bytes = 0

    async def post(self, endpoint, data=None):
        msg = encode_message({'endpoint': endpoint, 'data': data or {}})
        self.tx_bytes += len(msg) + 4
        await asyncio.to_thread(self.radio.send, msg)
        if endpoint == '/close':
            return {}
        response = await asyncio.to_thread(self.radio.receive)
        self.rx_bytes += len(response) + 4
        return decode_message(response)

async def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', default='/dev/ttyAMA0')
    p.add_argument('--protocol', default='polar', choices=['polar', 'cascade', 'winnow', 'nr_ldpc_std'])
    p.add_argument('--pa-protocol', default='toeplitz')
    p.add_argument('--confirm-bits', type=int, default=64)
    p.add_argument('--eta', type=float, default=0.90)
    p.add_argument('--p-dc', type=float, default=1e-5)
    args = p.parse_args()
    radio = LoRaTransport('bob', port=args.port)
    api = LoRaAPIClient(radio)
    bob = BobClient('BobClient', api, protocol=args.protocol,
                    pa_protocol=args.pa_protocol, confirm_bits=args.confirm_bits,
                    seed=1002, verbose=True)
    detector = Detector('Detector', efficiency=args.eta,
                        dark_count_prob=args.p_dc, parent_bob=bob, seed=1004)
    actors = [detector, bob]
    tasks = [asyncio.create_task(a.start()) for a in actors]
    try:
        print('[BOB] Requesting simulated photon train via LoRa...')
        q_resp = await api.post('/start-quantum')
        num_qubits, mu, T = q_resp['num_qubits'], q_resp['mu'], q_resp['transmittance']
        surviving_map = {pid: Photon(id=pid, bit=bit, basis=Basis(basis_str),
                                     creation_time='', count=cnt)
                         for pid, bit, basis_str, cnt in q_resp['photons']}
        print(f'[BOB] Received {len(surviving_map)} surviving pulses across {num_qubits} clock slots')
        for slot_id in range(num_qubits):
            await detector.send(detector, (slot_id, surviving_map.get(slot_id)))
        await detector.send(detector, ('FLUSH',))
        await bob.detection_done.wait()
        p_sig = 1.0 - np.exp(-mu * T * detector.eta)
        p_dark = 2 * detector.p_dc
        p_click = p_sig + p_dark - (p_sig * p_dark)
        p_multi = 1.0 - (1.0 + mu) * np.exp(-mu)
        q1 = max(0.0, 1.0 - (p_multi / p_click)) if p_click > 0 else 0.0
        results = await bob.run_classical_post_processing(num_qubits, q1=q1)
        await api.post('/close')
        print('\n--- RESULTS ---')
        print(f"Sifted Key Length : {results['sifted_length']}")
        print(f"QBER (Est)        : {results['qber']:.4%}")
        print(f'Single-Photon q1  : {q1:.4f}')
        print(f"Reconciled Length : {results['final_length']}")
        print(f"Key Confirmed     : {results['key_confirmed']}")
        print(f"Total Leakage     : {results['revealed']} bits")
        print(f"PA Key Length     : {results['pa_length']} bits")
        print(f"Corrected Errors  : {results['corrected']}")
        print(f"Channel Uses      : {results['channel_uses']}")
        print(f'Application traffic (compressed): TX={api.tx_bytes/1024:.2f} KB RX={api.rx_bytes/1024:.2f} KB')
        if results['pa_length'] > 0:
            print('Final Shared Secret (first 50 bits): ' + ''.join(map(str, results['final_secret_key'][:50])))
    finally:
        radio.close()
        for a in actors:
            await a.send(a, ('STOP',))
        await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())
