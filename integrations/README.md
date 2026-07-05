# Memdio Sound-Tag Bridge

This bridge turns sound-tag events from a `ced.cpp` style detector into Memdio memories through the local REST API.

## Phase 1: Prerequisites

You need these pieces in your own environment:

- Python 3.11+ for `memdio` and the bridge process
- A working microphone input
- `ced.cpp` or another sound-tagging pipeline that can emit `{tag, confidence, ts}` events
- LocalAI if you plan to run the larger voice/tool loop around the detector

## Phase 2: Build the External Pieces

Clone and build your detector stack separately. A typical flow is:

```bash
git clone <your ced.cpp fork>
cd ced.cpp
make
```

If LocalAI is part of your setup, bring it up and confirm the sound-tagger can emit one JSON event per line, for example:

```json
{"tag":"rooster","confidence":0.92,"ts":"2026-07-05T10:00:00Z"}
```

## Phase 3: Set Up Memdio

From this repository:

```bash
pip install -e ".[search,bridge]"
memdio serve
memdio create-key alice
```

The API runs on `http://localhost:8000` by default. Export the key before you run the bridge:

```bash
export MEMDIO_API_KEY=memdio_xxxx
```

## Phase 4: Run the Bridge

Mock mode for local validation:

```bash
python -m integrations.bridge --source mock
```

Real detector mode over stdin JSON-lines:

```bash
your-detector-command | python -m integrations.bridge --source stdin-json
```

Useful configuration:

```bash
export MEMDIO_CONF_THRESHOLD=0.7
export MEMDIO_DEBOUNCE_SECONDS=5
export MEMDIO_API_URL=http://localhost:8000/memories
python -m integrations.bridge --source stdin-json --threshold 0.8 --debounce 10
```

What the bridge stores:

- `content`: `Detected sound: rooster (conf 0.92) at 2026-07-05T10:00:00+00:00`
- `tags`: `rooster`

## Phase 5: Validate and Register the Tool

Target latencies:

- Sound tagging: under 60 ms per event
- Memdio store request: under 800 ms end-to-end
- Memdio retrieval: under 100 ms for direct lookups

Basic verification:

```bash
curl http://localhost:8000/memories?query=rooster \
  -H "Authorization: Bearer $MEMDIO_API_KEY"
```

If you are wiring this into a LocalAI tool loop, register the bridge as the process that receives detector output and writes durable memories to Memdio. The clean integration point is the detector's event stream, not the Memdio internals.

## Requires Your Environment

These parts were not runnable in this workspace and must be provided externally:

- `ced.cpp` build and model assets
- LocalAI service configuration
- Live microphone/audio device access
- Any websocket relay or detector-specific transport beyond stdin JSON-lines
