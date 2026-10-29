#!/usr/bin/env bash
set -euo pipefail

DEVICE_IP=192.168.2.119
PORT=2000
DURATION=120
OUTPUT="/tmp/viewpro_control_tcp2000_$(date +%Y%m%d_%H%M%S).pcap"
if [[ $# -ge 1 ]]; then DEVICE_IP=$1; fi
if [[ $# -ge 2 ]]; then PORT=$2; fi
if [[ $# -ge 3 ]]; then DURATION=$3; fi
if [[ $# -ge 4 ]]; then OUTPUT=$4; fi

case "$PORT" in ''|*[!0-9]*) echo "PORT must be numeric" >&2; exit 2;; esac
case "$DURATION" in ''|*[!0-9]*) echo "DURATION must be numeric seconds" >&2; exit 2;; esac
if ! command -v tcpdump >/dev/null 2>&1; then
  echo "tcpdump is required on the Ubuntu control host" >&2
  exit 127
fi
if ! command -v ip >/dev/null 2>&1; then
  echo "iproute2 is required to verify the route" >&2
  exit 127
fi

echo "Route to device:"
ip route get "$DEVICE_IP"
mkdir -p "$(dirname "$OUTPUT")"
echo "Capturing $DEVICE_IP TCP/$PORT for $DURATION seconds to $OUTPUT"
echo "Operate one ViewLink control at a time; do not use the platform buttons."
set +e
sudo timeout --signal=INT "$DURATION" tcpdump -i any -nn -s 0 -U -w "$OUTPUT" "host $DEVICE_IP and tcp port $PORT"
status=$?
set -e
if [[ $status -ne 0 && $status -ne 124 && $status -ne 130 ]]; then
  echo "tcpdump failed with exit code $status" >&2
  exit "$status"
fi
if [[ ! -s "$OUTPUT" ]]; then
  echo "No capture file or no matching packets captured: $OUTPUT" >&2
  exit 1
fi
echo "Capture complete: $OUTPUT"
echo "Packet summary:"
sudo tcpdump -nn -r "$OUTPUT" "host $DEVICE_IP and tcp port $PORT" 2>/dev/null | tail -n 12
