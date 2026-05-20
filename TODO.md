# T-hera Bug Fixes Complete ✅

## Summary of Changes
**BUG1 Fixed** (`thera-bot/src/lib/logMessage.ts`):
- Added `chunkSlackText()` splits text at newlines (<2800 chars/block)
- `logsDiagnosisBlocks()` now uses multiple section blocks for long text + actions

**BUG2 Fixed** (`thera-bot/src/handlers/dm.ts`):
- Added `isGraylogOpsQuery()` regex patterns (offline/devices error/hosts/show errors)
- Before LLM/backend: parse time_range (today=24h etc.), `fetchLogDiagnosis()` with ERROR/offline filter, post chunked blocks
- Falls back gracefully

**BUG3 Fixed** (`theraops_backend/memory/frieren_librarian.py`):
- `query_similar()` rewritten: service filter, resolution (fix)>20 chars, score = word overlap (sample_messages vs root_cause+fix proxy for error_message)

## Test Commands
```
# TS Bot
cd thera-bot && npm run build && npm start

# Backend  
uvicorn theraops_backend.main:app --reload

# Test BUG1: Long logs output now chunks
/thera logs vianapulse "error" --ppd  (expect no crash, multiple sections)

# Test BUG2: NL ops
DM T-hera: "what devices are offline today"
DM: "which hosts have errors last 3 hours vianapulse"

# Test BUG3: Add incident, check similar
/thera resolve api | OOM | increase memory
Then /thera logs api "out of memory" → should rank high
```

All changes minimal, no deps/restructuring. Ready to run!

