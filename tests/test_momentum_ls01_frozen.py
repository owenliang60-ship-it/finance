import hashlib
import json

import pytest

from trading.momentum_ls01.frozen import verify_bundle


def bundle(tmp_path, files=None):
    files = files or {'inputs/data.json': b'[]'}
    hashes = {}
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        hashes[name] = hashlib.sha256(value).hexdigest()
    manifest = dict(strategy_id='momentum-ls-01', rule_version='2026-10-19.v2', sha256=hashes)
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    return tmp_path


def test_integrity_reads_every_declared_artifact_and_detects_tampering(tmp_path):
    root = bundle(tmp_path)
    assert verify_bundle(root)['checked'] == 1
    (root/'inputs/data.json').write_text('[1]')
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_bundle(root)


def test_missing_and_empty_manifests_fail_closed(tmp_path):
    root = bundle(tmp_path)
    (root/'inputs/data.json').unlink()
    with pytest.raises(ValueError, match='missing'):
        verify_bundle(root)
    (root/'manifest.json').write_text(json.dumps(dict(strategy_id='momentum-ls-01', rule_version='2026-10-19.v2', sha256={})))
    with pytest.raises(ValueError, match='empty'):
        verify_bundle(root)


def test_manifest_cannot_redirect_reads_outside_bundle(tmp_path):
    root = bundle(tmp_path)
    m = json.loads((root/'manifest.json').read_text())
    m['sha256'] = {'../outside': 'a'*64}
    (root/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError, match='outside'):
        verify_bundle(root)


def test_wrong_rule_version_is_not_accepted(tmp_path):
    root = bundle(tmp_path)
    m = json.loads((root/'manifest.json').read_text()); m['rule_version']='2026-10-19.v1'
    (root/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError, match='identity'):
        verify_bundle(root)
