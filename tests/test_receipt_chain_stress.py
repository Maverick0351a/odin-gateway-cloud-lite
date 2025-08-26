import os
import json
from app import receipts

def test_receipt_chain_stress(tmp_path):
    # Use a temp file for receipts
    os.environ['ODIN_LOCAL_RECEIPTS'] = str(tmp_path / 'stress.log')
    store = receipts.load_receipt_store()
    trace_id = 'trace-stress'
    total = 1200
    prev_hash = None
    for i in range(total):
        r = store.add({
            'trace_id': trace_id,
            'hop': i,
            'ts': '2025-01-01T00:00:00Z',  # constant timestamp fine for this test
            'payload': {'n': i}
        })
        # linkage check immediate
        assert r.get('prev_receipt_hash') == prev_hash
        prev_hash = r.get('receipt_hash')
    chain = store.chain(trace_id)
    assert len(chain) == total
    # verify chain ordering and hash linkage
    for idx, rec in enumerate(chain):
        if idx == 0:
            assert rec.get('prev_receipt_hash') is None
        else:
            assert rec.get('prev_receipt_hash') == chain[idx-1].get('receipt_hash')
