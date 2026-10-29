#!/usr/bin/env python3
"""Forward SAN60 Windows replay frames into the UAV_USV_Platform backend."""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def sample_items(values, limit):
    """Evenly thin large spatial arrays while keeping their first/last samples."""
    if len(values) <= limit:
        return values
    if limit <= 1:
        return [values[-1]]
    last_index = len(values) - 1
    return [values[round(index * last_index / (limit - 1))] for index in range(limit)]


def read_json(url, timeout):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, token, payload, timeout):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Platform-Token": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def make_frame(state, vehicle_id, stream_id, source):
    latest = state.get("latest") or {}
    frequencies = latest.get("frequencies") or []
    powers = latest.get("powers") or []
    if not frequencies or len(frequencies) != len(powers):
        raise ValueError("latest frequencies/powers are missing or have different lengths")

    frequencies_hz = [float(value) * 1_000_000 for value in frequencies]
    gnss = state.get("gnss") or {}
    track = (state.get("spatial") or {}).get("track") or []
    position = track[-1].get("p") if track else None
    local_position = None
    if isinstance(position, list) and len(position) >= 3:
        local_position = {
            "east_m": position[0],
            "north_m": position[1],
            "up_m": position[2],
        }

    sequence = int(latest.get("sequence", 0))
    captured_at = float(latest.get("time", time.time()))
    data = {
        "vehicle_id": vehicle_id,
        "sensor_id": "san60",
        "stream_id": stream_id,
        "type": "spectrum",
        "captured_at": captured_at,
        "start_hz": frequencies_hz[0],
        "stop_hz": frequencies_hz[-1],
        "bin_hz": (frequencies_hz[1] - frequencies_hz[0]) if len(frequencies_hz) > 1 else None,
        "temperature_c": latest.get("temperature_c"),
        "peak_hz": float(latest["peak_mhz"]) * 1_000_000 if latest.get("peak_mhz") is not None else None,
        "peak_dbm": latest.get("peak_dbm"),
        "powers_dbm": powers,
        "sequence": sequence,
        "gnss": {
            "valid": bool(gnss.get("valid", False)),
            "captured_at": gnss.get("captured_at"),
            "latitude": gnss.get("latitude"),
            "longitude": gnss.get("longitude"),
            "altitude_m": gnss.get("altitude_m"),
            "satellites_used": gnss.get("satellites_used"),
        },
    }
    if local_position is not None:
        data["local_position_m"] = local_position
    spatial = state.get("spatial") or {}
    data["spatial"] = {
        "track": sample_items(spatial.get("track") or [], 600),
        "cells": sample_items(spatial.get("cells") or [], 1800),
        "field": sample_items(spatial.get("field") or [], 1800),
        "hotspot": spatial.get("hotspot"),
        "source": spatial.get("source"),
        "reason": spatial.get("reason"),
        "span": spatial.get("span") or [0, 0, 0],
        "voxel_size": spatial.get("voxel_size"),
    }
    return {
        "message_type": "spectrum_frame",
        "timestamp": captured_at,
        "sequence": sequence,
        "source": source,
        "data": data,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default="http://127.0.0.1:8775/api/state")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8081/api/integration/san60/spectrum")
    parser.add_argument("--token", default="uav-usv-local-agent")
    parser.add_argument("--vehicle-id", default="uav_01")
    parser.add_argument("--stream-id", default="offline_san60")
    parser.add_argument("--source", default="SAN60 Windows offline replay")
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0 or args.timeout <= 0:
        parser.error("--interval and --timeout must be positive")

    last_sequence = None
    while True:
        try:
            state = read_json(args.source_url, args.timeout)
            frame = make_frame(state, args.vehicle_id, args.stream_id, args.source)
            sequence = frame["data"]["sequence"]
            if sequence != last_sequence or args.once:
                result = post_json(args.backend_url, args.token, frame, args.timeout)
                last_sequence = sequence
                print(
                    "sent sequence={} points={} gnss={} accepted={}".format(
                        sequence,
                        len(frame["data"]["powers_dbm"]),
                        frame["data"]["gnss"]["valid"],
                        result.get("data", {}).get("accepted", result.get("accepted", True)),
                    ),
                    flush=True,
                )
            if args.once:
                return
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as error:
            print("bridge error: {}".format(error), flush=True)
            if args.once:
                raise SystemExit(1) from error
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
