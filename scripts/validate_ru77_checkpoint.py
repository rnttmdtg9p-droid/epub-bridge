"""Validate rank 77 initial checkpoints; never grants literary or release approval."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

def digest(value):
    return hashlib.sha256(value).hexdigest()

def validate(root):
    packed = base64.b64decode((root / 'inputs/ru77-source.json.gz.b64').read_bytes())
    assert digest(packed) == 'c837474d251da4690cf402ff2fe7380b656d91ce02e49d22381e17edf296aeaa', 'SOURCE_FREEZE'
    source = json.loads(gzip.decompress(packed))
    folder = root / 'results/ru77-claude'
    assert [u['unit'] for u in source] == list(range(1, 49)), 'SOURCE_SEQUENCE'
    assert sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name.isdigit()) == [f'{i:03}' for i in range(1, 49)], 'UNIT_INVENTORY'
    seen, receipts, ledger = [], [], []
    for unit in source:
        p = folder / f"{unit['unit']:03}" / 'chapter.translated.json'
        raw = p.read_bytes()
        chapter = json.loads(raw)
        assert chapter['unit'] == unit['unit'], 'UNIT_SEQUENCE'
        originals = [dict(id=unit['heading_id'], sha256=digest(unit['heading'].encode()))] + unit['rows']
        targets = [chapter['heading']] + chapter['rows']
        assert len(originals) == len(targets), 'ROW_COUNT'
        for src, row in zip(originals, targets):
            assert src['id'] == row['source_id'], 'ROW_SEQUENCE'
            assert src['sha256'] == row['source_sha256'], 'SOURCE_HASH'
            assert row['target'].strip() and digest(row['target'].encode()) == row['target_sha256'], 'TARGET_HASH'
            if 'text' in src:
                assert digest(src['text'].encode()) == src['sha256'], 'FROZEN_ROW_HASH'
                assert row['target'].count('[[I]]') == row['target'].count('[[/I]]') == src.get('emphasis_spans', 0), 'EMPHASIS'
                if src.get('source_lines'):
                    assert len(row['target'].splitlines()) == len(src['source_lines']), 'DISPLAY_LINES'
            seen.append(row['source_id'])
        assert len(chapter['provider_receipts']) == chapter['translation_batches'], 'RECEIPTS'
        receipts.extend(x['response_id'] for x in chapter['provider_receipts'])
        ledger.append(dict(unit=unit['unit'], rows=len(chapter['rows']), sha256=digest(raw)))
    assert len(seen) == 1964 and len(set(seen)) == 1964, 'EXACT_ONCE'
    assert len(receipts) == len(set(receipts)), 'DUPLICATE_RECEIPT'
    return dict(state='PASS_INITIAL_CHECKPOINT_ALIGNMENT_ONLY', units=48, content_rows=1916, headings=48, provider_receipts=len(receipts), chapters=ledger, release_approval_inferred=False)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.root), indent=2))
