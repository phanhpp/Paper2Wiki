# Graph Format

## graph.json

Tracks all nodes (papers, concepts, entities) and edges between them.
Upsert on every ingest — never duplicate nodes.

```json
{
  "nodes": [
    {"id": "attention_is_all_you_need", "type": "paper",   "title": "Attention Is All You Need"},
    {"id": "attention_mechanism",       "type": "concept", "title": "Attention Mechanism"},
    {"id": "ashish_vaswani",            "type": "entity",  "title": "Ashish Vaswani"}
  ],
  "edges": [
    {"from": "attention_is_all_you_need", "to": "attention_mechanism",  "type": "introduces",  "confidence": "EXTRACTED"},
    {"from": "attention_is_all_you_need", "to": "ashish_vaswani",       "type": "authored_by", "confidence": "EXTRACTED"},
    {"from": "attention_mechanism",       "to": "multi_head_attention",  "type": "related_to",  "confidence": "INFERRED"}
  ]
}
```

## Update Procedure (during ingest)

1. `read_file /wiki/graph/graph.json`
2. Add new nodes — check `id` doesn't already exist before adding
3. Add new edges — check `from+to+type` combo doesn't already exist
4. Tag each edge: `EXTRACTED` (stated in paper) or `INFERRED` (your reasoning)
5. `write_file` the updated JSON back
