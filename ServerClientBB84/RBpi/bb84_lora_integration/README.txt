BB84 simulation over Waveshare SX126X LoRa HAT (experimental)

Copy these three files into:
~/Documents/qkderrsim/ServerClientBB84/RBpi/lora_integration/

Keep the original bb84_serverClient_alice.py and bb84_serverClient_bob.py
in their existing RBpi directory. Do not copy or overwrite sx126x.py.

On both Raspberry Pis:
cd ~/Documents/qkderrsim
source .venv/bin/activate
python -c 'import serial, RPi.GPIO, numpy; print("Dependencies OK")'

Start Alice first:
python -m ServerClientBB84.RBpi.lora_integration.bb84_lora_alice --qubits 20000

Then start Bob:
python -m ServerClientBB84.RBpi.lora_integration.bb84_lora_bob --protocol polar

Stop any other program using /dev/ttyAMA0 before starting these scripts.
The radio settings are the same as the existing PQC demo: 915 MHz,
22 dBm, 2400 bps, UART /dev/ttyAMA0, 220-byte application chunks.

This is a classical BB84 simulator using LoRa for data transport. The
simulated photon descriptions contain bits and bases and are not secret.
Do not use its output as real secure QKD key material.

Caveats: Not hardware-tested; depends on SX126X RSSI-enabled framing.
The receiver waits 5.5 seconds after each message to ACK late retransmits.
Large messages and repeated parity exchanges can take a long time.
A noisy UART byte stream may require further resynchronization handling.
